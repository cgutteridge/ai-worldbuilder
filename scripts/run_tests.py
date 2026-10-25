#!/usr/bin/env python3
"""Run stdlib unit tests for scripts in this repository (no Minecraft required).

  export AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/southampton-build
  python3 scripts/run_tests.py

Quiet by default: on success prints one summary line; on failure prints each
failing test id with a capped traceback, then a one-line summary. Exits 0 when
all tests pass, 1 otherwise. Use before handoff or in CI.
Requires ``AI_WORLDBUILDER_WORLD_ROOT`` (no silent default world).
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from typing import TextIO

import pre_commit_checks as pcc

WORLD_ENV = 'AI_WORLDBUILDER_WORLD_ROOT'
DEFAULT_TRACEBACK_LINES = pcc.DEFAULT_TRACEBACK_LINES


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


def main() -> int:
    """Discover and run all ``test_*.py`` modules under ``scripts/``.

    @returns: process exit code (0 success, 1 if any test failed or env unset)
    """
    if not os.environ.get(WORLD_ENV, '').strip():
        print(
            f'{WORLD_ENV} is unset. Set it before running tests '
            f'(e.g. export {WORLD_ENV}=$PWD/worlds/southampton-build).',
            file=sys.stderr,
        )
        return 1
    scripts = Path(__file__).resolve().parent
    root = scripts.parent
    shims = root / 'shims'
    for path in (shims, scripts):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)
    suite = unittest.TestLoader().discover(str(scripts), pattern='test_*.py')
    # Swallow runner chatter and incidental test prints (stdout + stderr);
    # report_result writes the quiet summary to the real stderr afterwards.
    sink = io.StringIO()
    real_stderr = sys.stderr
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        result = unittest.TextTestRunner(stream=sink, verbosity=0).run(suite)
    return report_result(result, stream=real_stderr)


if __name__ == '__main__':
    sys.exit(main())
