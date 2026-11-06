#!/usr/bin/env python3
"""Pre-commit guardrails for the worldbuilder tool repo.

Mechanical checks only:

- On ``task/*`` worker branches, refuse staged edits to the system tracker
  (``tasks/index.json``, ``tasks/done.json``).
- When staged paths are administrative-only (tracker files, ``docs/``, markdown,
  the root docs files; the same allowlist as ``ship_branch.is_docs_only_path``),
  skip provider-name audit and the unit suite, but validate that any staged
  tracker file is well-formed JSON (#82, #241). Anything else is code and keeps
  the full checks.
- Provider-name audit (#28): no new line-level hits outside allowed homes.
- Wave lock helpers for the parallel-task-wave orchestrator (one open wave).

Invoked by ``.githooks/pre-commit``. Install with
``python3 scripts/install_git_hooks.py``.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

_TOOL_ROOT = Path(__file__).resolve().parent.parent
if str(_TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOL_ROOT))

import python_runtime  # noqa: E402

python_runtime.ensure_python_runtime()

TRACKER_PATHS = frozenset({'tasks/index.json', 'tasks/done.json'})
WAVE_LOCK_NAME = '.wave-lock'
DEFAULT_TRACEBACK_LINES = 40


class WaveLockError(RuntimeError):
    """Raised when a wave lock cannot be acquired or is held by another orchestrator."""


def format_check_summary(
    name: str,
    *,
    ok: bool,
    failed: int = 0,
    total: int = 0,
) -> str:
    """Return a one-line status summary for a pre-commit check.

    @param name: check label (e.g. ``unit tests``, ``provider-name audit``)
    @param ok: whether the check passed
    @param failed: number of failing cases (used when ``ok`` is False and total > 0)
    @param total: total cases run (used when ``ok`` is False)
    @returns: single-line summary such as ``unit tests: FAILED 3/412``
    """
    if ok:
        return f'{name}: OK'
    if total > 0:
        return f'{name}: FAILED {failed}/{total}'
    return f'{name}: FAILED'


def format_failure_excerpt(
    test_id: str,
    traceback: str,
    *,
    max_lines: int = DEFAULT_TRACEBACK_LINES,
    label: str = 'FAIL',
) -> str:
    """Format one failing test id with a capped traceback.

    @param test_id: unittest id (``module.Class.method``)
    @param traceback: full failure/error traceback text
    @param max_lines: keep only the last N traceback lines
    @param label: ``FAIL`` or ``ERROR``
    @returns: multi-line block starting with ``FAIL <id>`` / ``ERROR <id>``
    """
    tb_lines = traceback.rstrip('\n').splitlines()
    if len(tb_lines) > max_lines:
        tb_lines = tb_lines[-max_lines:]
    body = '\n'.join(tb_lines)
    if body:
        return f'{label} {test_id}\n{body}'
    return f'{label} {test_id}'


def format_failure_report(
    failures: Sequence[tuple[str, str]],
    *,
    max_lines: int = DEFAULT_TRACEBACK_LINES,
    label: str = 'FAIL',
) -> str:
    """Format several failing test excerpts separated by blank lines.

    @param failures: sequence of ``(test_id, traceback)`` pairs
    @param max_lines: traceback line cap per failure
    @param label: ``FAIL`` or ``ERROR``
    @returns: combined report text (empty when ``failures`` is empty)
    """
    blocks = [
        format_failure_excerpt(test_id, tb, max_lines=max_lines, label=label)
        for test_id, tb in failures
    ]
    return '\n\n'.join(blocks)


def is_worker_branch(branch: str) -> bool:
    """Return True when ``branch`` is a parallel-wave worker branch.

    @param branch: current git branch name
    @returns: whether tracker edits are forbidden on this branch
    """
    return branch.startswith('task/')


def forbidden_tracker_paths(branch_names: Iterable[str], paths: Iterable[str]) -> list[str]:
    """Return staged tracker paths that a worker checkout must not commit.

    Any matching ``task/…`` name among ``branch_names`` counts (covers detached
    HEAD when a worker branch still points at the commit).

    @param branch_names: candidate branch names for this checkout
    @param paths: staged file paths relative to the repo root
    @returns: sorted list of forbidden paths present in ``paths``
    """
    if not any(is_worker_branch(name) for name in branch_names):
        return []
    hit = {p for p in paths if p in TRACKER_PATHS}
    return sorted(hit)


def is_tracker_only_paths(paths: Iterable[str]) -> bool:
    """Return True when staged paths are non-empty and only the system tracker.

    Allowlist is exactly :data:`TRACKER_PATHS` (``tasks/index.json``,
    ``tasks/done.json``). Empty staging or any other path is not tracker-only.

    @param paths: staged file paths relative to the repo root
    @returns: whether the commit is an archive-style tracker-only change
    """
    staged = list(paths)
    if not staged:
        return False
    return all(p in TRACKER_PATHS for p in staged)


# Markdown that audits or tests inspect (provider-name and stale-term audits on
# rules/, skill stub check on skills): never on the fast path.
CHECKED_MARKDOWN_PREFIXES = ('rules/', '.agents/', '.claude/')


def _is_checked_markdown(path: str) -> bool:
    normalised = path.replace('\\', '/')
    return normalised.startswith(CHECKED_MARKDOWN_PREFIXES)


def is_administrative_only_paths(paths: Iterable[str]) -> bool:
    """Return True when staged paths are non-empty and all tracker or docs.

    Uses ``ship_branch.is_docs_only_path`` (``tasks/``, ``docs/``, any ``.md``,
    a few root docs files) minus markdown under ``rules/``, ``.agents/`` and
    ``.claude/``, which the provider-name audit and skill-stub tests cover. Empty staging is not administrative.

    @param paths: staged file paths relative to the repo root
    @returns: whether the commit may skip the provider audit and unit suite
    """
    import ship_branch

    staged = list(paths)
    if not staged:
        return False
    return all(
        ship_branch.is_docs_only_path(p) and not _is_checked_markdown(p)
        for p in staged
    )


def invalid_tracker_files(repo_root: Path, paths: Iterable[str]) -> list[str]:
    """Return staged tracker files whose staged content is not valid JSON.

    @param repo_root: repository root
    @param paths: staged file paths relative to the repo root
    @returns: sorted tracker paths that fail to parse
    """
    bad: list[str] = []
    for path in sorted(set(paths) & TRACKER_PATHS):
        try:
            text = _git(repo_root, 'show', f':{path}')
            json.loads(text)
        except (subprocess.CalledProcessError, ValueError):
            bad.append(path)
    return bad


def acquire_wave_lock(
    repo_root: Path,
    *,
    wave_id: str,
    holder: str,
) -> Path:
    """Create or refresh ``.wave-lock`` for this orchestrator.

    Same ``holder`` may re-acquire (crash recovery) and refreshes ``wave_id``.
    A different holder fails.

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
    payload = {
        'wave_id': wave_id,
        'holder': holder,
        'acquired_at': datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    return path


def release_wave_lock(repo_root: Path, *, holder: str) -> None:
    """Remove ``.wave-lock`` when ``holder`` owns it.

    @param repo_root: repository root
    @param holder: orchestrator identity that must match the lock
    @raises WaveLockError: when the lock is missing or held by someone else
    """
    path = repo_root / WAVE_LOCK_NAME
    if not path.is_file():
        raise WaveLockError('no .wave-lock to release')
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('holder') != holder:
        raise WaveLockError(
            f"wave lock held by {data.get('holder')!r}; "
            f"refusing release by {holder!r}"
        )
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


def checkout_branch_names(repo_root: Path) -> list[str]:
    """Branch names that apply to this checkout for tracker ownership.

    Attached HEAD uses the current branch alone. Points-at-HEAD tips are
    included only when HEAD is detached, so a ``task/…`` worktree cannot
    bypass the tracker rule by detaching, while ``main`` (or any non-worker
    branch) is not blocked merely because fresh ``task/…`` branches share
    the same commit tip.

    @param repo_root: repository root
    @returns: unique branch names, current first when set
    """
    current = current_branch(repo_root)
    if current:
        return [current]
    names: list[str] = []
    try:
        pointed = _git(
            repo_root,
            'branch',
            '--points-at',
            'HEAD',
            '--format=%(refname:short)',
        )
    except subprocess.CalledProcessError:
        pointed = ''
    for name in pointed.splitlines():
        if name and name not in names:
            names.append(name)
    return names


def run_provider_name_audit(repo_root: Path) -> int:
    """Fail when new provider-id wording appears outside allowed homes (#28).

    Captures the audit CLI's own chatter and prints a one-line summary instead.

    @param repo_root: repository root
    @returns: process exit code from ``scripts/check_provider_names.py``
    """
    script = repo_root / 'scripts' / 'check_provider_names.py'
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=repo_root / 'scripts',
        capture_output=True,
        text=True,
    )
    ok = result.returncode == 0
    if not ok:
        detail = (result.stderr or result.stdout or '').rstrip()
        if detail:
            print(detail, file=sys.stderr)
    print(format_check_summary('provider-name audit', ok=ok), file=sys.stderr)
    return result.returncode


def run_unit_tests(repo_root: Path) -> int:
    """Run the repo's stdlib unit test suite (quiet reporter).

    @param repo_root: repository root
    @returns: process exit code from ``scripts/run_tests.py``
    """
    script = repo_root / 'scripts' / 'run_tests.py'
    return subprocess.call([sys.executable, str(script)], cwd=repo_root)


def run_pre_commit(repo_root: Path, *, skip_tests: bool = False) -> int:
    """Run all pre-commit checks; print failures to stderr.

    Tracker ownership always runs. Provider-name audit and the unit suite are
    skipped when ``skip_tests`` is True, or when staged paths are administrative-only
    (:func:`is_administrative_only_paths`: tracker, docs, markdown) so archive and
    docs commits on ``main`` need no ``--skip-tests``; staged tracker files must
    still parse as JSON. Otherwise the disposable-world unit suite always runs
    (no env gate).

    @param repo_root: repository root
    @param skip_tests: when True, skip provider audit and unit suite (ownership only)
    @returns: 0 on success, 1 on failure
    """
    branches = checkout_branch_names(repo_root)
    paths = staged_paths(repo_root)
    forbidden = forbidden_tracker_paths(branches, paths)
    if forbidden:
        print(
            'pre-commit: worker branch must not edit the system tracker:\n  '
            + '\n  '.join(forbidden)
            + '\nOrchestrator archives tasks after merge (parallel-task-wave).',
            file=sys.stderr,
        )
        return 1
    if skip_tests:
        return 0
    if is_administrative_only_paths(paths):
        bad = invalid_tracker_files(repo_root, paths)
        if bad:
            print(
                'pre-commit: staged tracker file is not valid JSON:\n  '
                + '\n  '.join(bad),
                file=sys.stderr,
            )
            return 1
        return 0
    code = run_provider_name_audit(repo_root)
    if code != 0:
        return code
    return run_unit_tests(repo_root)


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
        help='only enforce tracker ownership (no provider audit or unit suite)',
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
    rel.add_argument('--holder', required=True)
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
        try:
            release_wave_lock(root, holder=args.holder)
        except WaveLockError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    return 2


if __name__ == '__main__':
    sys.exit(main())
