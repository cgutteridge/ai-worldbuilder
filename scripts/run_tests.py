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

Quiet by default: on success prints one summary line; on failure prints each
failing test id with a capped traceback, then a one-line summary. Exits 0 when
all tests pass, 1 otherwise. Use before handoff or in CI.
Requires ``AI_WORLDBUILDER_WORLD_ROOT`` (no silent default world).
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import os
import sys
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

import pre_commit_checks as pcc

WORLD_ENV = 'AI_WORLDBUILDER_WORLD_ROOT'
DEFAULT_TRACEBACK_LINES = pcc.DEFAULT_TRACEBACK_LINES


class UnknownTestModule(ValueError):
    """Raised when ``--only`` names a module stem with no matching file."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the test runner.

    @param argv: argument vector without the program name (default: ``sys.argv[1:]``)
    @returns: namespace with ``only`` (list of stems, or None for full discover)
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
    return parser.parse_args(list(argv) if argv is not None else None)


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


def main(argv: Sequence[str] | None = None) -> int:
    """Discover and run ``test_*.py`` modules under ``scripts/``.

    With ``--only``, runs only the named module stems. Without flags, discovers
    the full suite.

    @param argv: optional CLI args (without program name)
    @returns: process exit code (0 success, 1 if any test failed or env unset)
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
    try:
        suite = load_suite(scripts, only=args.only)
    except UnknownTestModule as exc:
        print(f'run_tests: {exc}', file=sys.stderr)
        return 1
    # Swallow runner chatter and incidental test prints (stdout + stderr);
    # report_result writes the quiet summary to the real stderr afterwards.
    sink = io.StringIO()
    real_stderr = sys.stderr
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        result = unittest.TextTestRunner(stream=sink, verbosity=0).run(suite)
    return report_result(result, stream=real_stderr)


if __name__ == '__main__':
    sys.exit(main())
