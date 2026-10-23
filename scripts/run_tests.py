#!/usr/bin/env python3
"""Run stdlib unit tests for scripts in this repository (no Minecraft required).

  export AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/southampton-build
  python3 scripts/run_tests.py

Exits 0 when all tests pass, 1 otherwise. Use before handoff or in CI.
Requires ``AI_WORLDBUILDER_WORLD_ROOT`` (no silent default world).
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

WORLD_ENV = 'AI_WORLDBUILDER_WORLD_ROOT'


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
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
