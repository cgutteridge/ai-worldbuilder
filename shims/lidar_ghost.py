#!/usr/bin/env python3
"""CLI entry point for the shared lidar ghost planner."""
from _provider_shim import ensure_import_paths

ensure_import_paths()

from lidar_ghost import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())
