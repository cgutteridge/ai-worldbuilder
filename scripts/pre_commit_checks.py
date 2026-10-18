#!/usr/bin/env python3
"""Pre-commit guardrails for the worldbuilder tool repo.

Mechanical checks only:

- On ``task/*`` worker branches, refuse staged edits to the system tracker
  (``tasks/index.json``, ``tasks/done.json``).
- Wave lock helpers for the parallel-task-wave orchestrator (one open wave).

Invoked by ``.githooks/pre-commit``. Install with
``python3 scripts/install_git_hooks.py``.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

TRACKER_PATHS = frozenset({'tasks/index.json', 'tasks/done.json'})
WAVE_LOCK_NAME = '.wave-lock'


class WaveLockError(RuntimeError):
    """Raised when a wave lock cannot be acquired or is held by another orchestrator."""


def is_worker_branch(branch: str) -> bool:
    """Return True when ``branch`` is a parallel-wave worker branch.

    @param branch: current git branch name
    @returns: whether tracker edits are forbidden on this branch
    """
    return branch.startswith('task/')


def forbidden_tracker_paths(branch: str, paths: Iterable[str]) -> list[str]:
    """Return staged tracker paths that a worker branch must not commit.

    @param branch: current git branch name
    @param paths: staged file paths relative to the repo root
    @returns: sorted list of forbidden paths present in ``paths``
    """
    if not is_worker_branch(branch):
        return []
    hit = {p for p in paths if p in TRACKER_PATHS}
    return sorted(hit)


def acquire_wave_lock(
    repo_root: Path,
    *,
    wave_id: str,
    holder: str,
) -> Path:
    """Create or refresh ``.wave-lock`` for this orchestrator.

    Same ``holder`` may re-acquire (crash recovery). A different holder fails.

    @param repo_root: repository root
    @param wave_id: short id for this wave
    @param holder: orchestrator identity (chat id, hostname+pid, etc.)
    @returns: path to the lock file
    @raises WaveLockError: when another holder already owns the lock
    """
    path = repo_root / WAVE_LOCK_NAME
    if path.is_file():
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('holder') != holder:
            raise WaveLockError(
                f"wave lock held by {data.get('holder')!r} "
                f"(wave_id={data.get('wave_id')!r}); refuse second orchestrator"
            )
        return path
    payload = {
        'wave_id': wave_id,
        'holder': holder,
        'acquired_at': datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    return path


def release_wave_lock(repo_root: Path) -> None:
    """Remove ``.wave-lock`` if present.

    @param repo_root: repository root
    """
    path = repo_root / WAVE_LOCK_NAME
    if path.is_file():
        path.unlink()


def _git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ['git', *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def staged_paths(repo_root: Path) -> list[str]:
    """List paths staged for commit.

    @param repo_root: repository root
    @returns: relative path strings
    """
    out = _git(repo_root, 'diff', '--cached', '--name-only', '--diff-filter=ACMR')
    if not out:
        return []
    return [line for line in out.splitlines() if line]


def current_branch(repo_root: Path) -> str:
    """Return the current branch name (empty when detached).

    @param repo_root: repository root
    @returns: branch name or empty string
    """
    try:
        return _git(repo_root, 'symbolic-ref', '--short', 'HEAD')
    except subprocess.CalledProcessError:
        return ''


def run_unit_tests(repo_root: Path) -> int:
    """Run the repo's stdlib unit test suite.

    @param repo_root: repository root
    @returns: process exit code from ``scripts/run_tests.py``
    """
    script = repo_root / 'scripts' / 'run_tests.py'
    return subprocess.call([sys.executable, str(script)], cwd=repo_root)


def run_pre_commit(repo_root: Path, *, skip_tests: bool = False) -> int:
    """Run all pre-commit checks; print failures to stderr.

    @param repo_root: repository root
    @param skip_tests: when True, skip the unit suite (ownership only)
    @returns: 0 on success, 1 on failure
    """
    branch = current_branch(repo_root)
    paths = staged_paths(repo_root)
    bad = forbidden_tracker_paths(branch, paths)
    if bad:
        print(
            'pre-commit: worker branch must not edit the system tracker:\n  '
            + '\n  '.join(bad)
            + '\nOrchestrator archives tasks after merge (parallel-task-wave).',
            file=sys.stderr,
        )
        return 1
    if skip_tests:
        return 0
    code = run_unit_tests(repo_root)
    if code != 0:
        print('pre-commit: unit tests failed', file=sys.stderr)
    return code


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry for the git hook and wave-lock helpers.

    @param argv: argument list (defaults to ``sys.argv[1:]``)
    @returns: process exit code
    """
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='cmd', required=True)

    check = sub.add_parser('check', help='run pre-commit checks')
    check.add_argument(
        '--skip-tests',
        action='store_true',
        help='only enforce tracker ownership (no unit suite)',
    )
    check.add_argument(
        '--repo-root',
        type=Path,
        default=None,
        help='repository root (default: parent of scripts/)',
    )

    acq = sub.add_parser('wave-lock-acquire', help='acquire .wave-lock')
    acq.add_argument('--wave-id', required=True)
    acq.add_argument('--holder', required=True)
    acq.add_argument('--repo-root', type=Path, default=None)

    rel = sub.add_parser('wave-lock-release', help='release .wave-lock')
    rel.add_argument('--repo-root', type=Path, default=None)

    args = parser.parse_args(argv)
    root = args.repo_root
    if root is None:
        root = Path(__file__).resolve().parent.parent
    root = root.resolve()

    if args.cmd == 'check':
        return run_pre_commit(root, skip_tests=args.skip_tests)
    if args.cmd == 'wave-lock-acquire':
        try:
            path = acquire_wave_lock(root, wave_id=args.wave_id, holder=args.holder)
        except WaveLockError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(path)
        return 0
    if args.cmd == 'wave-lock-release':
        release_wave_lock(root)
        return 0
    return 2


if __name__ == '__main__':
    sys.exit(main())
