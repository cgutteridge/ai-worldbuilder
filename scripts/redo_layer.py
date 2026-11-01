#!/usr/bin/env python3
"""Redo selected cell layers, with checked viewing and one lock (--apply)."""
from script_env import ensure_import_paths
ensure_import_paths()
from layer_redo import main

if __name__ == '__main__':
    raise SystemExit(main())
