#!/usr/bin/env python3
"""Single-builder lock for Southampton Refine.

Only the thread holding a fresh lock may change the live world. apply()
checks it (env SITE_OWNER must match) and refreshes the heartbeat. A lock
with no heartbeat for STALE_MIN minutes may be taken over.

  python3 scripts/site_lock.py status
  python3 scripts/site_lock.py claim <owner> "<queue item / task>"
  python3 scripts/site_lock.py release <owner>
  python3 scripts/site_lock.py run <owner> "<task>" -- <cmd> [args...]
``run`` claims, exports SITE_OWNER for <cmd>, runs it and always releases (command
failure, Ctrl-C and SIGTERM included); it exits with the command's status. Example:
  python3 scripts/site_lock.py run me "baseline" -- python3 scripts/lidar_ghost.py apply
By hand instead:  SITE_OWNER=<owner> python3 scripts/<build>.py --apply
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from atomic_json import write_json
from script_env import ROOT  # noqa: E402

LOCK = ROOT / 'site' / 'LOCK.json'
STALE_MIN = 90


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


def run(owner, task, cmd):
    """Claim the lock, run ``cmd`` with SITE_OWNER=owner, and always release.

    Release happens on success, failure, Ctrl-C and SIGTERM. A refused claim runs nothing and
    releases nothing (the lock is not ours).

    @param owner: lock owner id
    @param task: what the lock is for
    @param cmd: argv list for the command
    @returns: the command's exit status (128+signal if killed by a signal)
    """
    if not cmd:
        raise SystemExit('run: no command given (site_lock.py run <owner> "<task>" -- <cmd>)')
    claim(owner, task)

    def _term(signum, _frame):
        raise SystemExit(128 + signum)

    old = signal.signal(signal.SIGTERM, _term)
    process = None
    completed = False
    try:
        # Own a process group so termination cannot leave a builder running after
        # the lock was released. Layer commands inherit this group.
        process = subprocess.Popen(cmd, env={**os.environ, 'SITE_OWNER': owner}, start_new_session=True)
        rc = process.wait()
        completed = True
        return 128 - rc if rc < 0 else rc
    except KeyboardInterrupt:
        return 130
    finally:
        if process is not None and not completed:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            # The worker may have exited before its child CLI. Stop every member
            # of the group before making the world lock available again.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        signal.signal(signal.SIGTERM, old)
        release(owner)


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
        if len(sys.argv) < 6 or sys.argv[4] != '--':
            print(__doc__, file=sys.stderr)
            return 2
        return run(sys.argv[2], sys.argv[3], sys.argv[5:])
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main() or 0)
