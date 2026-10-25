#!/usr/bin/env python3
"""Run stdlib unit tests for scripts in this repository (no Minecraft required).

  export AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/southampton-build
  python3 scripts/run_tests.py
  python3 scripts/run_tests.py --only test_model_bakeoff
  python3 scripts/run_tests.py --only test_run_tests --only test_ship_branch

``--only`` takes a unittest module stem (``test_foo`` for ``scripts/test_foo.py``),
not a path. Repeat the flag (or pass it multiple times) to select several
modules. With no ``--only`` flags the runner discovers the full ``test_*.py``
suite under ``scripts/`` as before. An unknown stem fails with a clear error.

Full discover (no ``--only``) takes a fail-fast advisory flock on
``<git-common-dir>/ai-worldbuilder-full-suite.lock`` (shared across worktrees).
A second overlapping full run exits ``2`` immediately (no wait); real test
failures and unset world still exit ``1``. ``--only`` never takes the lock.
When a parent already holds the lock (``--parent-holds-lock`` or env
``AI_WORLDBUILDER_FULL_SUITE_LOCK_HELD=1``), the child skips acquire so
``ship_branch`` can re-enter under the same lock.

Quiet by default: on success prints one summary line; on failure prints each
failing test id with a capped traceback, then a one-line summary. Exits 0 when
all tests pass, 1 otherwise (or 2 when locked out of a full run). Use before
handoff or in CI.
Requires ``AI_WORLDBUILDER_WORLD_ROOT`` (no silent default world).
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import importlib.util
import io
import os
import subprocess
import sys
import unittest
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import TextIO

import pre_commit_checks as pcc

WORLD_ENV = 'AI_WORLDBUILDER_WORLD_ROOT'
PARENT_HOLDS_LOCK_ENV = 'AI_WORLDBUILDER_FULL_SUITE_LOCK_HELD'
FULL_SUITE_LOCK_NAME = 'ai-worldbuilder-full-suite.lock'
EXIT_LOCKED = 2
DEFAULT_TRACEBACK_LINES = pcc.DEFAULT_TRACEBACK_LINES


class UnknownTestModule(ValueError):
    """Raised when ``--only`` names a module stem with no matching file."""


class FullSuiteLocked(RuntimeError):
    """Raised when another process already holds the full-suite flock."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the test runner.

    @param argv: argument vector without the program name (default: ``sys.argv[1:]``)
    @returns: namespace with ``only`` and ``parent_holds_lock``
    """
    parser = argparse.ArgumentParser(
        description=(
            'Run stdlib unit tests under scripts/. '
            'Optional --only selects module stems (test_foo).'
        )
    )
    parser.add_argument(
        '--only',
        action='append',
        metavar='MODULE',
        dest='only',
        default=None,
        help=(
            'unittest module stem under scripts/ (e.g. test_model_bakeoff); '
            'repeatable. Default: discover all test_*.py modules.'
        ),
    )
    parser.add_argument(
        '--parent-holds-lock',
        action='store_true',
        dest='parent_holds_lock',
        default=False,
        help=(
            'Skip full-suite flock acquire; parent already holds '
            f'{FULL_SUITE_LOCK_NAME} (also set via {PARENT_HOLDS_LOCK_ENV}=1).'
        ),
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def parent_already_holds_lock(
    args: argparse.Namespace,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Whether the caller says a parent already holds the full-suite lock.

    @param args: parsed CLI namespace
    @param env: environment mapping (default: ``os.environ``)
    @returns: True when ``--parent-holds-lock`` or the env var is truthy
    """
    if getattr(args, 'parent_holds_lock', False):
        return True
    environ = os.environ if env is None else env
    raw = environ.get(PARENT_HOLDS_LOCK_ENV, '').strip().lower()
    return raw in {'1', 'true', 'yes', 'on'}


def git_common_dir(repo_root: Path) -> Path:
    """Absolute git common directory for ``repo_root`` (shared across worktrees).

    @param repo_root: repository root or linked worktree root
    @returns: absolute path to the common ``.git`` directory
    """
    result = subprocess.run(
        ['git', 'rev-parse', '--git-common-dir'],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    raw = result.stdout.strip()
    path = Path(raw)
    if not path.is_absolute():
        path = (repo_root / path).resolve()
    return path


def full_suite_lock_path(repo_root: Path) -> Path:
    """Path of the advisory full-suite lock file under the git common dir.

    @param repo_root: repository root or linked worktree root
    @returns: ``<git-common-dir>/ai-worldbuilder-full-suite.lock``
    """
    return git_common_dir(repo_root) / FULL_SUITE_LOCK_NAME


@contextlib.contextmanager
def full_suite_lock(
    repo_root: Path,
    *,
    parent_holds: bool = False,
) -> Iterator[int | None]:
    """Hold the fail-fast advisory flock for a default full discover run.

    Does nothing when ``parent_holds`` is True. Otherwise opens
    :func:`full_suite_lock_path` and takes ``LOCK_EX|LOCK_NB``; raises
    :class:`FullSuiteLocked` immediately if another holder exists.

    @param repo_root: repository root or linked worktree root
    @param parent_holds: skip acquire (parent already holds the flock)
    @yields: lock file descriptor, or None when skipped
    @raises FullSuiteLocked: when the lock is busy (fail-fast, no wait)
    """
    if parent_holds:
        yield None
        return
    path = full_suite_lock_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FullSuiteLocked(
                f'full suite already running (lock {path}); '
                'fail-fast, try again when free'
            ) from exc
        try:
            yield fd
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def load_suite(
    scripts_dir: Path,
    *,
    only: Sequence[str] | None = None,
) -> unittest.TestSuite:
    """Build a test suite from ``scripts_dir``.

    @param scripts_dir: directory containing ``test_*.py`` modules
    @param only: module stems to load; None discovers all ``test_*.py`` files
    @returns: loaded suite
    @raises UnknownTestModule: when a named stem has no ``<stem>.py`` file
    """
    loader = unittest.TestLoader()
    if not only:
        return loader.discover(str(scripts_dir), pattern='test_*.py')
    suite = unittest.TestSuite()
    scripts_text = str(scripts_dir.resolve())
    if scripts_text not in sys.path:
        sys.path.insert(0, scripts_text)
    for name in only:
        stem = name.strip()
        if not stem or '/' in stem or '\\' in stem or stem.endswith('.py'):
            raise UnknownTestModule(
                f'unknown test module {name!r}: expected a stem like '
                f'test_foo (not a path)'
            )
        path = (scripts_dir / f'{stem}.py').resolve()
        if not path.is_file():
            raise UnknownTestModule(
                f'unknown test module {stem!r}: no file {path.name} under '
                f'{scripts_dir}'
            )
        # Load from the concrete file so stems resolve under a custom
        # scripts_dir (tests) and stale sys.modules entries cannot win.
        sys.modules.pop(stem, None)
        spec = importlib.util.spec_from_file_location(stem, path)
        if spec is None or spec.loader is None:
            raise UnknownTestModule(
                f'unknown test module {stem!r}: could not load {path}'
            )
        module = importlib.util.module_from_spec(spec)
        sys.modules[stem] = module
        spec.loader.exec_module(module)
        suite.addTests(loader.loadTestsFromModule(module))
    return suite


def report_result(
    result: unittest.TestResult,
    *,
    stream: TextIO | None = None,
    max_traceback_lines: int = DEFAULT_TRACEBACK_LINES,
) -> int:
    """Print quiet failure excerpts and a one-line unit-test summary.

    @param result: completed ``unittest`` result
    @param stream: output stream (default ``sys.stderr``)
    @param max_traceback_lines: traceback line cap per failure/error
    @returns: 0 when successful, 1 otherwise
    """
    out = sys.stderr if stream is None else stream
    failures = [(t.id(), tb) for t, tb in result.failures]
    errors = [(t.id(), tb) for t, tb in result.errors]
    blocks: list[str] = []
    if failures:
        blocks.append(
            pcc.format_failure_report(
                failures, max_lines=max_traceback_lines, label='FAIL'
            )
        )
    if errors:
        blocks.append(
            pcc.format_failure_report(
                errors, max_lines=max_traceback_lines, label='ERROR'
            )
        )
    if blocks:
        print('\n\n'.join(blocks), file=out)
    failed = len(failures) + len(errors)
    total = result.testsRun
    ok = result.wasSuccessful()
    print(
        pcc.format_check_summary(
            'unit tests', ok=ok, failed=failed, total=total
        ),
        file=out,
    )
    return 0 if ok else 1


def _run_suite(suite: unittest.TestSuite) -> int:
    """Execute ``suite`` with quiet capture and report to real stderr.

    @param suite: loaded unittest suite
    @returns: 0 on success, 1 on failure
    """
    sink = io.StringIO()
    real_stderr = sys.stderr
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        result = unittest.TextTestRunner(stream=sink, verbosity=0).run(suite)
    return report_result(result, stream=real_stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """Discover and run ``test_*.py`` modules under ``scripts/``.

    With ``--only``, runs only the named module stems (no full-suite lock).
    Without flags, discovers the full suite under the advisory flock.

    @param argv: optional CLI args (without program name)
    @returns: 0 success, 1 test failure / unset world / unknown module,
        2 when locked out of a full run
    """
    if not os.environ.get(WORLD_ENV, '').strip():
        print(
            f'{WORLD_ENV} is unset. Set it before running tests '
            f'(e.g. export {WORLD_ENV}=$PWD/worlds/southampton-build).',
            file=sys.stderr,
        )
        return 1
    args = parse_args(argv)
    scripts = Path(__file__).resolve().parent
    root = scripts.parent
    shims = root / 'shims'
    for path in (shims, scripts):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)

    def _load_and_run() -> int:
        try:
            suite = load_suite(scripts, only=args.only)
        except UnknownTestModule as exc:
            print(f'run_tests: {exc}', file=sys.stderr)
            return 1
        return _run_suite(suite)

    if args.only:
        return _load_and_run()

    parent_holds = parent_already_holds_lock(args)
    try:
        with full_suite_lock(root, parent_holds=parent_holds):
            return _load_and_run()
    except FullSuiteLocked as exc:
        print(f'run_tests: {exc}', file=sys.stderr)
        return EXIT_LOCKED


if __name__ == '__main__':
    sys.exit(main())
