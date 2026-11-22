#!/usr/bin/env python3
"""Run stdlib unit tests for this repository (no Minecraft required).

  python3 scripts/run_tests.py
  python3 scripts/run_tests.py --only test_run_tests --only test_ship_branch
  python3 scripts/run_tests.py --bakeoff
  python3 scripts/run_tests.py --bakeoff --only test_model_bakeoff

``--only`` takes a unittest module stem (``test_foo`` for ``scripts/test_foo.py``
or ``lib/<category>/test_foo.py``), not a path. Repeat the flag (or pass it
multiple times) to select several modules. With no ``--only`` flags the runner
discovers the full ``test_*.py`` suite under ``scripts/`` and each
``lib/<category>/``. The closed bake-off under ``bakeoff/`` is left out of that
default suite; ``--bakeoff`` opts in (alone, or with ``--only`` for a stem such
as ``test_model_bakeoff``). An unknown stem fails with a clear error.

Full discover (no ``--only``) takes a fail-fast advisory flock on
``<git-common-dir>/ai-worldbuilder-full-suite.lock`` (shared across worktrees).
A second overlapping full run exits ``2`` immediately (no wait); real test
failures still exit ``1``. ``--only`` never takes the lock.
When a parent already holds the lock (``--parent-holds-lock`` or env
``AI_WORLDBUILDER_FULL_SUITE_LOCK_HELD=1``), the child skips acquire so
``ship_branch`` can re-enter under the same lock.

Quiet by default: on success prints one summary line; on failure prints each
failing test id with a capped traceback, then a one-line summary. Exits 0 when
all tests pass, 1 otherwise (or 2 when locked out of a full run). Use before
handoff or in CI.
Always uses disposable test-world configuration, ignoring any selected world.
No Minecraft save, world checkout, evidence cache or extracted assets are required.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import importlib.util
import io
import os
import sys
import unittest
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import TextIO

from shared_git import git_output
import pre_commit_checks as pcc
from unit_test_world import unit_test_world

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
    @returns: namespace with ``only``, ``bakeoff`` and ``parent_holds_lock``
    """
    parser = argparse.ArgumentParser(
        description=(
            'Run stdlib unit tests under scripts/ and lib/*/. '
            'Optional --only selects module stems (test_foo). '
            '--bakeoff also collects the closed bake-off tests.'
        )
    )
    parser.add_argument(
        '--only',
        action='append',
        metavar='MODULE',
        dest='only',
        default=None,
        help=(
            'unittest module stem under scripts/ or lib/*/ '
            '(and bakeoff/ when --bakeoff is set); repeatable. '
            'Default: discover test_*.py modules, excluding bakeoff/.'
        ),
    )
    parser.add_argument(
        '--bakeoff',
        action='store_true',
        dest='bakeoff',
        default=False,
        help=(
            'Opt in to bakeoff/test_*.py. The closed bake-off is omitted '
            'from the default suite.'
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
    raw = git_output(repo_root, 'rev-parse', '--git-common-dir')
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


def _ensure_on_path(directory: Path) -> None:
    """Prepend ``directory`` to ``sys.path`` when missing.

    @param directory: directory that should be importable
    """
    text = str(directory.resolve())
    if text not in sys.path:
        sys.path.insert(0, text)


def load_suite(
    scripts_dir: Path,
    *,
    only: Sequence[str] | None = None,
    extra_dirs: Sequence[Path] | None = None,
) -> unittest.TestSuite:
    """Build a test suite from ``scripts_dir`` and optional extra roots.

    @param scripts_dir: primary directory containing ``test_*.py`` modules
    @param only: module stems to load; None discovers all ``test_*.py`` files
    @param extra_dirs: additional roots searched with ``scripts_dir`` for
        discover and ``--only`` (``lib/<category>/``, and ``bakeoff/`` only
        when the caller opted in)
    @returns: loaded suite
    @raises UnknownTestModule: when a named stem has no ``<stem>.py`` file
    """
    extras = [d for d in (extra_dirs or ()) if d.is_dir()]
    dirs = [*extras, scripts_dir] if extras else [scripts_dir]
    for directory in dirs:
        if directory.is_dir():
            _ensure_on_path(directory)
    loader = unittest.TestLoader()
    if not only:
        suite = unittest.TestSuite()
        for directory in dirs:
            if directory.is_dir():
                suite.addTests(
                    loader.discover(str(directory), pattern='test_*.py')
                )
        # discover() may insert start_dir at sys.path[0]; put extras first again.
        for directory in reversed(extras):
            _ensure_on_path(directory)
        return suite
    suite = unittest.TestSuite()
    dir_labels = ', '.join(str(d) for d in dirs)
    for name in only:
        stem = name.strip()
        if not stem or '/' in stem or '\\' in stem or stem.endswith('.py'):
            raise UnknownTestModule(
                f'unknown test module {name!r}: expected a stem like '
                f'test_foo (not a path)'
            )
        path: Path | None = None
        for directory in dirs:
            candidate = (directory / f'{stem}.py').resolve()
            if candidate.is_file():
                path = candidate
                break
        if path is None:
            raise UnknownTestModule(
                f'unknown test module {stem!r}: no file {stem}.py under '
                f'{dir_labels}'
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


def lib_test_dirs(repo_root: Path) -> list[Path]:
    """Category directories under ``lib/`` that may hold ``test_*.py`` modules.

    @param repo_root: tool repository root
    @returns: sorted existing ``lib/<category>/`` directories
    """
    lib_root = repo_root / 'lib'
    if not lib_root.is_dir():
        return []
    return sorted(
        p for p in lib_root.iterdir() if p.is_dir() and not p.name.startswith('.')
    )


def discover_extra_dirs(
    repo_root: Path,
    *,
    include_bakeoff: bool = False,
) -> list[Path]:
    """Roots searched besides ``scripts/`` when building the suite.

    ``lib/<category>/`` is always included. ``bakeoff/`` is the closed
    experiment and is included only when ``include_bakeoff`` is true
    (CLI ``--bakeoff``).

    @param repo_root: tool repository root
    @param include_bakeoff: opt in to ``bakeoff/test_*.py``
    @returns: extra discover roots (existing directories only)
    """
    extras = lib_test_dirs(repo_root)
    if include_bakeoff:
        bakeoff = repo_root / 'bakeoff'
        if bakeoff.is_dir():
            extras.append(bakeoff)
    return extras


def main(argv: Sequence[str] | None = None) -> int:
    """Discover and run ``test_*.py`` under ``scripts/`` and ``lib/*/``.

    With ``--only``, runs only the named module stems (no full-suite lock).
    Without flags, discovers the default suite under the advisory flock.
    ``bakeoff/`` is collected only when ``--bakeoff`` is set.

    @param argv: optional CLI args (without program name)
    @returns: 0 success, 1 test failure / unknown module,
        2 when locked out of a full run
    """
    args = parse_args(argv)
    scripts = Path(__file__).resolve().parent
    root = scripts.parent
    shims = root / 'shims'
    lib_dirs = lib_test_dirs(root)
    # Put lib/* ahead of scripts/ so bare imports hit libraries, not CLI wrappers.
    # Do NOT import script_env / site_kit here: unit_test_world() selects the
    # disposable checkout first, then the suite imports world-aware modules.
    for path in (scripts, shims, *reversed(lib_dirs)):
        if path.is_dir():
            _ensure_on_path(path)
    for path in reversed(lib_dirs):
        _ensure_on_path(path)

    def _load_and_run() -> int:
        with unit_test_world():
            extras = discover_extra_dirs(
                root, include_bakeoff=bool(args.bakeoff)
            )
            try:
                suite = load_suite(
                    scripts,
                    only=args.only,
                    extra_dirs=tuple(extras),
                )
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
