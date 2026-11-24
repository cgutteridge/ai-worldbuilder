#!/usr/bin/env python3
"""Read roof volumes for one map way and write a feature_build spec proposal (#459).

Writes the read and the proposal under ``--out`` only; never a world spec, never blocks.
Implementation: ``lib/builds/roof_read.py``.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__":
    tool = Path(__file__).resolve().parent.parent
    if str(tool) not in sys.path:
        sys.path.insert(0, str(tool))
    import script_env

    script_env.ensure_import_paths()
    script_env.prepend_world_activation()
    from roof_read import main

    raise SystemExit(main())
