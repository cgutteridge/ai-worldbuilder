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
``run`` claims, exports SITE_OWNER and SITE_LOCK_FILE for <cmd>, runs it and always releases (command
failure, Ctrl-C and SIGTERM included); it exits with the command's status. A live feature
apply should use ``scripts/site_apply.py`` instead: it writes a command log, prints one
APPLY_EXIT line, and can recover without claiming first. Example:
  python3 scripts/site_lock.py run me "baseline" -- python3 shims/lidar_ghost.py apply
With ``--budget-min N`` it prints one OVER-BUDGET line at N minutes, kills the
process group at N times ``--kill-factor`` (default 1.5) and exits 124, and
appends a verdict to site/run-budget.jsonl. Without ``--budget-min`` nothing
about the wait, the status or the lock changes, and no verdict is written.
Owner names are the whole identity: two sessions that both use one name share a lock, so one
session's release (or claim) removes or replaces the other's. Give a long sequence its own name.
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


def _lock_path():
    """Return the lock file this process should read and refresh.

    A lock runner sets ``SITE_LOCK_FILE`` to the file it claimed. The child uses
    that same file. If this process has selected a different world, refuse
    instead of reporting a missing lock.

    @returns: lock file path
    @raises SystemExit: ``SITE_LOCK_FILE`` is not this world's lock file
    """
    override = os.environ.get('SITE_LOCK_FILE')
    if not override:
        return LOCK
    chosen = Path(override)
    try:
        own = Path(LOCK).resolve()
    except RuntimeError:
        return chosen
    if own != chosen.resolve():
        raise SystemExit(
            f'World lock file is {chosen} but this command resolved {own}. '
            'Pass the same --world the lock runner used.'
        )
    return chosen


def read():
    """Return the lock dict, or None if unlocked.

    @returns: lock dict or None
    @raises SystemExit: ``SITE_LOCK_FILE`` does not match this world's lock file
    """
    path = Path(_lock_path())
    return json.loads(path.read_text()) if path.exists() else None


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
    path = _lock_path()
    lk = read()
    if not owner:
        raise SystemExit('Set SITE_OWNER=<owner> (claim first: scripts/site_lock.py claim <owner> "<task>")')
    if not fresh(lk) or lk['owner'] != owner:
        detail = lk if lk is not None else f'None (no file at {path})'
        raise SystemExit(f'World lock not held by {owner}: {detail}')
    lk['heartbeat'] = time.time()
    lk['heartbeat_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
    write_json(path, lk)


def hold(owner, task):
    """Make sure ``owner`` holds a fresh lock, re-claiming one that lapsed (#458).

    A long sequence (undo then rebuild) calls this between steps. A lock that is still ours has
    its heartbeat refreshed; a missing or stale lock is claimed again; a fresh lock held by another
    owner is never taken and raises as ``claim`` does.

    @param owner: lock owner id
    @param task: what the lock is for (used only when the lock is claimed again)
    @returns: ``'held'`` when the lock was still ours, ``'reclaimed'`` when it had lapsed
    @raises SystemExit: another owner holds a fresh lock
    """
    lk = read()
    if fresh(lk) and lk['owner'] == owner:
        check(owner)
        return 'held'
    claim(owner, task)
    return 'reclaimed'


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


def _stop_group(process) -> None:
    """Stop ``process`` and its session, including descendants that outlive it.

    @param process: started ``Popen`` in its own session, or None
    """
    if process is None:
        return
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


def _child_env(owner):
    """Environment for a locked child: owner name and the claimed lock file.

    @param owner: lock owner id
    @returns: environment mapping
    """
    return {
        **os.environ,
        'SITE_OWNER': owner,
        'SITE_LOCK_FILE': str(Path(LOCK).resolve()),
    }


def _open_log(log_path, log_append):
    """Open a command log after the lock claim has succeeded.

    @param log_path: file to create, or None to inherit the parent's streams
    @param log_append: append when True, otherwise replace
    @returns: open text file, or None
    """
    if log_path is None:
        return None
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open('a' if log_append else 'w', encoding='utf-8', errors='replace')


def run(owner, task, cmd, *, budget_min=None, kill_factor=DEFAULT_KILL_FACTOR, cell=None,
        log_path=None, log_append=False):
    """Claim the lock, run ``cmd`` with SITE_OWNER=owner, and always release.

    Release happens on success, failure, Ctrl-C and SIGTERM. A refused claim runs nothing and
    releases nothing (the lock is not ours). The child receives ``SITE_LOCK_FILE`` so it
    refreshes the file this process claimed. With ``log_path``, the child's output goes
    to that file, opened only after the claim succeeds. With ``budget_min``, one OVER-BUDGET
    line is printed when that many minutes have elapsed, the process group is killed at
    ``budget_min * kill_factor`` the same way every exit already stops it, and the status is 124.
    That run appends elapsed time and a verdict (``ok``, ``over-budget`` or ``killed``) for the
    morning report. Without ``budget_min`` the wait and the status are unchanged and nothing
    is recorded.

    @param owner: lock owner id
    @param task: what the lock is for
    @param cmd: argv list for the command
    @param budget_min: minutes before the over-budget flag; None leaves the run unbudgeted
    @param kill_factor: kill at budget_min times this value (at least 1)
    @param cell: optional ``tx,tz`` stored on the budget record
    @param log_path: command log opened after a successful claim; None inherits streams
    @param log_append: append to ``log_path`` instead of replacing it
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
    log_handle = None
    verdict = None
    elapsed = None
    status = None
    try:
        # Own a process group so termination cannot leave a builder running after
        # the lock was released. Layer commands inherit this group.
        log_handle = _open_log(log_path, log_append)
        popen = {'env': _child_env(owner), 'start_new_session': True}
        if log_handle is not None:
            popen['stdout'] = log_handle
            popen['stderr'] = subprocess.STDOUT
        process = subprocess.Popen(cmd, **popen)
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
            _stop_group(process)
        finally:
            try:
                if log_handle is not None:
                    log_handle.close()
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


def run_command(cmd, *, env=None, log_path=None, log_append=False) -> int:
    """Run ``cmd`` in its own session and always stop that session.

    Does not claim or release the world lock. Recover uses this so a fresh
    lock, including one held by the same owner, is left untouched.

    @param cmd: argv list
    @param env: environment for the child (default: this process's environment)
    @param log_path: command log opened for the child; None inherits streams
    @param log_append: append to ``log_path`` instead of replacing it
    @returns: the command's exit status, or 130 on Ctrl-C
    @raises SystemExit: empty command, or 128+signal when SIGTERM hits this process
    """
    if not cmd:
        raise SystemExit('run: no command given')

    def _term(signum, _frame):
        raise SystemExit(128 + signum)

    old = signal.signal(signal.SIGTERM, _term)
    process = None
    log_handle = None
    try:
        try:
            log_handle = _open_log(log_path, log_append)
            popen = {'env': os.environ if env is None else env, 'start_new_session': True}
            if log_handle is not None:
                popen['stdout'] = log_handle
                popen['stderr'] = subprocess.STDOUT
            process = subprocess.Popen(cmd, **popen)
            rc = process.wait()
            return 128 - rc if rc < 0 else rc
        except KeyboardInterrupt:
            return 130
        finally:
            try:
                _stop_group(process)
            finally:
                if log_handle is not None:
                    log_handle.close()
    finally:
        signal.signal(signal.SIGTERM, old)


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
