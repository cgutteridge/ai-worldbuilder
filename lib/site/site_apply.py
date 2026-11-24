#!/usr/bin/env python3
"""One command for a live feature apply.

Claims the world lock, runs the apply with its output in a command log, always
releases the lock, and prints one result line last. Recover does not claim:
a fresh lock already held, including by this same owner, makes
``site_recover.py`` treat the run as still alive and do nothing. A chain stops
when recover fails, so the apply does not start on an unfinished journal entry.

  python3 scripts/site_apply.py --world <world> run --owner <owner> --task "<what>" -- <cmd>
  python3 scripts/site_apply.py --world <world> recover -- <cmd>
  python3 scripts/site_apply.py --world <world> chain --owner <owner> --task "<what>" -- <recover> -- <apply>

Owner names are the whole identity. Two sessions that share one name share the
lock; pass a name no other session is using. If another owner holds a fresh
lock, this command prints who and does not start the apply.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import site_lock


def result_line(exit_code: int, lock: str, report: Path | None, note: str | None = None) -> str:
    """One trailing result line for a background waiter.

    @param exit_code: status of the command that ran, or 2 when the lock was busy
    @param lock: ``released``, ``busy``, or ``not claimed``
    @param report: command log path, or None when this step wrote nothing
    @param note: optional clause in parentheses
    @returns: ``APPLY_EXIT=<n>, lock <state>, <path>``
    """
    target = 'none' if report is None else str(report)
    text = f'APPLY_EXIT={exit_code}, lock {lock}, {target}'
    if note:
        text += f' ({note})'
    return text


def default_log_path() -> Path:
    """Command log under the active world's verification directory.

    @returns: ``verification/site/site-apply-<stamp>-<pid>.log``
    """
    from script_env import ROOT
    stamp = time.strftime('%Y%m%d%H%M%S')
    return Path(ROOT) / 'verification' / 'site' / f'site-apply-{stamp}-{os.getpid()}.log'


def _held_by_other(owner: str) -> str | None:
    """Who holds a fresh lock, when it is not ``owner``.

    @param owner: lock owner this command would claim as
    @returns: the refusal line, or None when this owner may claim
    """
    lk = site_lock.read()
    if site_lock.fresh(lk) and lk['owner'] != owner:
        return f'Held by {lk["owner"]} since {lk["claimed_at"]} for: {lk["task"]}'
    return None


def apply_command(owner: str, task: str, cmd: list[str], log_path: Path, *, log_append: bool = False) -> int:
    """Claim, run ``cmd``, always release, and print the result line last.

    A fresh lock held by another owner runs nothing and writes no command log.

    @param owner: lock owner id (unique to this session)
    @param task: what the lock is for
    @param cmd: argv list for the apply
    @param log_path: command log, created only after the claim succeeds
    @param log_append: append to an existing log (a chain after recover)
    @returns: the command status, 130 on Ctrl-C, or 2 when the lock is busy
    """
    holder = _held_by_other(owner)
    if holder is not None:
        print(holder, flush=True)
        print(result_line(2, 'busy', None), flush=True)
        return 2
    status = 1
    report = 'skip'
    try:
        status = site_lock.run(owner, task, cmd, log_path=log_path, log_append=log_append)
        report = 'released'
        return status
    except SystemExit as exc:
        if str(exc).startswith('Held by '):
            print(exc, flush=True)
            status = 2
            report = 'busy'
            return 2
        # SIGTERM during the locked run becomes SystemExit(128 + signal) after
        # the lock is released. That still needs the end marker.
        if isinstance(exc.code, int):
            status = exc.code
            report = 'released'
            return status
        raise
    except Exception:
        report = 'released'
        raise
    finally:
        if report == 'released':
            print(result_line(status, 'released', Path(log_path)), flush=True)
        elif report == 'busy':
            print(result_line(status, 'busy', None), flush=True)


def recover_command(cmd: list[str], log_path: Path) -> int:
    """Run ``cmd`` without claiming the world lock, then print the result line.

    @param cmd: argv list (typically ``site_recover.py recover <cell> --apply``)
    @param log_path: command log for this recover
    @returns: the command status, or 130 on Ctrl-C
    """
    try:
        status = site_lock.run_command(cmd, log_path=log_path)
    except SystemExit as exc:
        if not isinstance(exc.code, int):
            raise
        print(result_line(exc.code, 'not claimed', Path(log_path)), flush=True)
        return exc.code
    print(result_line(status, 'not claimed', Path(log_path)), flush=True)
    return status


def chain_commands(owner: str, task: str, recover_cmd: list[str], apply_cmd: list[str], log_path: Path) -> int:
    """Recover without claiming, then apply only if recover exited 0.

    @param owner: lock owner id for the apply step
    @param task: what the apply lock is for
    @param recover_cmd: argv list run with no claim
    @param apply_cmd: argv list run only after recover exits 0
    @param log_path: command log; the apply appends when recover succeeds
    @returns: recover's status when that step fails, otherwise the apply status
    """
    try:
        status = site_lock.run_command(recover_cmd, log_path=log_path)
    except SystemExit as exc:
        if not isinstance(exc.code, int):
            raise
        print(result_line(exc.code, 'not claimed', Path(log_path)), flush=True)
        return exc.code
    if status != 0:
        print(result_line(
            status, 'not claimed', Path(log_path), 'recover failed; apply not started',
        ), flush=True)
        return status
    return apply_command(owner, task, apply_cmd, log_path, log_append=True)


def _take_option(opts: list[str], flag: str) -> str | None:
    """Return the value of ``flag`` and remove the pair from ``opts``.

    @param opts: option list, mutated when the flag is present
    @param flag: option name, including leading dashes
    @returns: the value, or None when the flag is absent
    @raises SystemExit: the flag is present without a value
    """
    if flag not in opts:
        return None
    index = opts.index(flag)
    if index + 1 >= len(opts):
        raise SystemExit(f'{flag} needs a value')
    value = opts[index + 1]
    del opts[index:index + 2]
    return value


def main(argv: list[str] | None = None) -> int:
    """CLI: ``run``, ``recover``, or ``chain``.

    ``--world`` is activated by the ``scripts/`` wrapper before this runs.

    @param argv: arguments after the program name (default ``sys.argv[1:]``)
    @returns: the apply, recover, or chain status; 2 on a bad invocation
    """
    args = list(sys.argv[1:] if argv is None else argv)
    usage = (
        'usage: site_apply.py run --owner O --task T [--log PATH] -- <cmd>\n'
        '       site_apply.py recover [--log PATH] -- <cmd>\n'
        '       site_apply.py chain --owner O --task T [--log PATH] -- <recover> -- <apply>'
    )
    if not args or args[0] not in ('run', 'recover', 'chain'):
        print(usage, file=sys.stderr)
        return 2
    mode = args.pop(0)
    if '--' not in args:
        print(usage, file=sys.stderr)
        return 2
    dash = args.index('--')
    opts, command = args[:dash], args[dash + 1:]
    if not command:
        print(usage, file=sys.stderr)
        return 2
    try:
        owner = _take_option(opts, '--owner')
        task = _take_option(opts, '--task')
        log_text = _take_option(opts, '--log')
        if opts:
            raise SystemExit(f'unknown option {opts[0]}')
        if mode == 'recover' and (owner is not None or task is not None):
            raise SystemExit('recover does not take --owner or --task; it must not claim the lock')
        if mode in ('run', 'chain') and (not owner or not task):
            raise SystemExit(f'{mode} needs --owner and --task')
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2
    log_path = Path(log_text) if log_text is not None else default_log_path()
    if mode == 'recover':
        return recover_command(command, log_path)
    if mode == 'run':
        return apply_command(owner, task, command, log_path)
    if '--' not in command:
        print(usage, file=sys.stderr)
        return 2
    split = command.index('--')
    recover_cmd, apply_cmd = command[:split], command[split + 1:]
    if not recover_cmd or not apply_cmd:
        print(usage, file=sys.stderr)
        return 2
    return chain_commands(owner, task, recover_cmd, apply_cmd, log_path)


if __name__ == '__main__':
    raise SystemExit(main() or 0)
