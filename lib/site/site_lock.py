#!/usr/bin/env python3
"""Single-builder lock for the active world.

Only the thread holding a fresh lock may change the live world. apply()
checks it (env SITE_OWNER must match) and refreshes the heartbeat. A lock
with no heartbeat for STALE_MIN minutes may be taken over.

  python3 scripts/site_lock.py status
  python3 scripts/site_lock.py claim <owner> "<queue item / task>"
  python3 scripts/site_lock.py release <owner>
  python3 scripts/site_lock.py run <owner> "<task>" -- <cmd> [args...]
  python3 scripts/site_lock.py run <owner> "<task>" --budget-min N [--kill-factor F] [--cell tx,tz] -- <cmd>
``run`` claims, exports SITE_OWNER for <cmd>, runs it and always releases (command
failure, Ctrl-C and SIGTERM included); it exits with the command's status. Example:
  python3 scripts/site_lock.py run me "baseline" -- python3 shims/lidar_ghost.py apply
With ``--budget-min N`` it prints one OVER-BUDGET line at N minutes, kills the
process group at N times ``--kill-factor`` (default 1.5) and exits 124, and
appends a verdict to site/run-budget.jsonl. Without ``--budget-min`` nothing
about the wait, the status or the lock changes, and no verdict is written.
By hand instead:  SITE_OWNER=<owner> python3 scripts/<build>.py --apply
"""
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from atomic_json import write_json
from script_env import ROOT  # noqa: E402

LOCK = ROOT / 'site' / 'LOCK.json'
RUN_BUDGET = ROOT / 'site' / 'run-budget.jsonl'
STALE_MIN = 90
BUDGET_EXIT = 124
DEFAULT_KILL_FACTOR = 1.5
_CELL = re.compile(r'-?\d+,-?\d+')


def read():
    """Return the lock dict, or None if unlocked.

    @returns: lock dict or None
    """
    return json.loads(LOCK.read_text()) if LOCK.exists() else None


def fresh(lk):
    """True if ``lk`` has a heartbeat newer than STALE_MIN minutes.

    @param lk: lock dict or None
    @returns: whether the lock is still held
    """
    return lk and time.time() - lk['heartbeat'] < STALE_MIN * 60


def check(owner=None):
    """Raise unless ``owner`` (default env SITE_OWNER) holds a fresh lock; refresh heartbeat.

    @param owner: lock owner id (defaults to SITE_OWNER)
    """
    owner = owner or os.environ.get('SITE_OWNER')
    lk = read()
    if not owner:
        raise SystemExit('Set SITE_OWNER=<owner> (claim first: scripts/site_lock.py claim <owner> "<task>")')
    if not fresh(lk) or lk['owner'] != owner:
        raise SystemExit(f'World lock not held by {owner}: {lk}')
    lk['heartbeat'] = time.time()
    lk['heartbeat_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
    write_json(LOCK, lk)


def claim(owner, task):
    """Write the lock for ``owner``; refuses while another owner's lock is fresh.

    @param owner: lock owner id
    @param task: what the lock is for
    """
    lk = read()
    if fresh(lk) and lk['owner'] != owner:
        raise SystemExit(f'Held by {lk["owner"]} since {lk["claimed_at"]} for: {lk["task"]}')
    now = time.time()
    write_json(LOCK, {
        'owner': owner, 'task': task, 'claimed': now, 'heartbeat': now,
        'claimed_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'heartbeat_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    })


def release(owner):
    """Remove the lock; refuses while another owner's lock is fresh.

    @param owner: lock owner id
    """
    lk = read()
    if lk and lk['owner'] != owner and fresh(lk):
        raise SystemExit(f'Not yours: {lk}')
    LOCK.unlink(missing_ok=True)


def over_budget_line(owner, task, elapsed):
    """One flag line when a budgeted run reaches its budget.

    @param owner: lock owner id
    @param task: what the lock is for
    @param elapsed: seconds since the command started
    @returns: a single line carrying owner, task and elapsed seconds
    """
    safe_task = ' '.join(str(task).split())
    return f'OVER-BUDGET owner={owner} task={safe_task} elapsed={elapsed:.1f}s'


def append_budget_record(record):
    """Append one budget verdict that ``site_status.py morning`` can read.

    @param record: mapping written as one JSON object per line
    """
    RUN_BUDGET.parent.mkdir(parents=True, exist_ok=True)
    with RUN_BUDGET.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, sort_keys=True) + '\n')


def wait_budget(process, owner, task, budget_min, kill_factor):
    """Poll ``process`` until it exits, or until the kill deadline.

    Prints one OVER-BUDGET line once the budget is reached, including when the
    command has just exited past that point. The caller still stops the process
    group; this function does not signal it.

    @param process: started ``Popen`` in its own session
    @param owner: lock owner id
    @param task: what the lock is for
    @param budget_min: minutes before the over-budget flag
    @param kill_factor: kill once elapsed reaches budget_min times this
    @returns: ``(status, verdict, elapsed_seconds)``. Verdict is ``ok``,
        ``over-budget`` or ``killed``. A kill returns status 124.
    """
    start = time.monotonic()
    budget_s = float(budget_min) * 60.0
    kill_s = budget_s * float(kill_factor)
    flagged = False
    while True:
        rc = process.poll()
        elapsed = time.monotonic() - start
        if not flagged and elapsed >= budget_s:
            flagged = True
            print(over_budget_line(owner, task, elapsed), flush=True)
        if rc is not None:
            status = 128 - rc if rc < 0 else rc
            return status, ('over-budget' if flagged else 'ok'), elapsed
        if elapsed >= kill_s:
            return BUDGET_EXIT, 'killed', elapsed
        wake = kill_s if flagged else budget_s
        time.sleep(min(0.05, max(0.0, wake - elapsed)))


def run(owner, task, cmd, *, budget_min=None, kill_factor=DEFAULT_KILL_FACTOR, cell=None):
    """Claim the lock, run ``cmd`` with SITE_OWNER=owner, and always release.

    Release happens on success, failure, Ctrl-C and SIGTERM. A refused claim runs nothing and
    releases nothing (the lock is not ours). With ``budget_min``, one OVER-BUDGET line is printed
    when that many minutes have elapsed, the process group is killed at ``budget_min * kill_factor``
    the same way every exit already stops it, and the status is 124. That run appends elapsed time
    and a verdict (``ok``, ``over-budget`` or ``killed``) for the morning report. Without
    ``budget_min`` the wait and the status are unchanged and nothing is recorded.

    @param owner: lock owner id
    @param task: what the lock is for
    @param cmd: argv list for the command
    @param budget_min: minutes before the over-budget flag; None leaves the run unbudgeted
    @param kill_factor: kill at budget_min times this value (at least 1)
    @param cell: optional ``tx,tz`` stored on the budget record
    @returns: the command's exit status, 124 when the budget kills it, or 128+signal
    """
    if not cmd:
        raise SystemExit('run: no command given (site_lock.py run <owner> "<task>" -- <cmd>)')
    if budget_min is not None and float(budget_min) <= 0:
        raise SystemExit('run: --budget-min must be greater than 0')
    if float(kill_factor) < 1:
        raise SystemExit('run: --kill-factor must be at least 1')
    claim(owner, task)

    def _term(signum, _frame):
        raise SystemExit(128 + signum)

    old = signal.signal(signal.SIGTERM, _term)
    process = None
    verdict = None
    elapsed = None
    status = None
    try:
        # Own a process group so termination cannot leave a builder running after
        # the lock was released. Layer commands inherit this group.
        process = subprocess.Popen(cmd, env={**os.environ, 'SITE_OWNER': owner}, start_new_session=True)
        if budget_min is None:
            rc = process.wait()
            status = 128 - rc if rc < 0 else rc
        else:
            status, verdict, elapsed = wait_budget(
                process, owner, task, float(budget_min), float(kill_factor))
        return status
    except KeyboardInterrupt:
        return 130
    finally:
        try:
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
                # Waiting for the worker alone is insufficient: it may already have
                # exited while its child CLI is still stopping. Give the entire group
                # a bounded grace period, including on a normal worker exit.
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    process.poll()  # Reap the worker so it cannot keep the group alive.
                    try:
                        os.killpg(process.pid, 0)
                    except (ProcessLookupError, PermissionError):
                        break
                    time.sleep(.01)
                # Stop any remaining descendants before making the lock available.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                process.wait()
        finally:
            try:
                signal.signal(signal.SIGTERM, old)
            finally:
                try:
                    if verdict is not None:
                        record = {
                            'budget_min': float(budget_min),
                            'elapsed_sec': round(elapsed, 3),
                            'exit': status,
                            'owner': owner,
                            'task': task,
                            'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
                            'verdict': verdict,
                        }
                        if cell is not None:
                            record['cell'] = cell
                        append_budget_record(record)
                finally:
                    release(owner)


def parse_run_argv(argv):
    """Parse ``run <owner> <task> [flags] -- <cmd>``.

    @param argv: full ``sys.argv``
    @returns: ``(owner, task, cmd, budget_min, kill_factor, cell)``
    @raises ValueError: when the invocation does not match that shape
    """
    usage = ('usage: site_lock.py run <owner> "<task>" '
             '[--budget-min N] [--kill-factor F] [--cell tx,tz] -- <cmd>')
    if len(argv) < 6 or '--' not in argv[4:]:
        raise ValueError(usage)
    dash = argv.index('--', 4)
    command = argv[dash + 1:]
    if not command:
        raise ValueError(usage)
    budget_min = None
    kill_factor = DEFAULT_KILL_FACTOR
    cell = None
    opts = argv[4:dash]
    index = 0
    while index < len(opts):
        flag = opts[index]
        if flag not in ('--budget-min', '--kill-factor', '--cell'):
            raise ValueError(f'run: unknown option {flag}')
        if index + 1 >= len(opts):
            raise ValueError(f'run: {flag} needs a value')
        value = opts[index + 1]
        index += 2
        if flag == '--cell':
            if not _CELL.fullmatch(value):
                raise ValueError('run: --cell must look like tx,tz')
            cell = value
            continue
        try:
            number = float(value)
        except ValueError:
            raise ValueError(f'run: {flag} needs a number') from None
        if flag == '--budget-min':
            if number <= 0:
                raise ValueError('run: --budget-min must be greater than 0')
            budget_min = number
        elif number < 1:
            raise ValueError('run: --kill-factor must be at least 1')
        else:
            kill_factor = number
    return argv[2], argv[3], command, budget_min, kill_factor, cell


def main():
    """CLI: status | claim | release | run."""
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    lk = read()
    if cmd == 'status':
        print(json.dumps(lk, indent=1) if lk else 'unlocked', '(fresh)' if fresh(lk) else '(stale/none)')
    elif cmd == 'claim':
        claim(sys.argv[2], ' '.join(sys.argv[3:]))
        print('claimed by', sys.argv[2])
    elif cmd == 'release':
        release(sys.argv[2])
        print('released')
    elif cmd == 'run':
        try:
            owner, task, command, budget_min, kill_factor, cell = parse_run_argv(sys.argv)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        return run(owner, task, command, budget_min=budget_min, kill_factor=kill_factor, cell=cell)
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main() or 0)
