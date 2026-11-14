#!/usr/bin/env python3
"""CLI entry point for the shared lidar ghost planner."""
import importlib
import sys

from _provider_shim import ensure_import_paths

ensure_import_paths()

if __name__ == '__main__':
    raise SystemExit(importlib.import_module('lidar_ghost').main())
else:
    # Paths now prefer the library. Remove this in-progress shim before importing
    # its shared module so direct imports with only shims/ on sys.path also work.
    del sys.modules[__name__]
    sys.modules[__name__] = importlib.import_module(__name__)
