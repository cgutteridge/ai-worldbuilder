#!/usr/bin/env python3
"""Deprecated path — use ``python3 shims/buildings.py`` (world provider shim)."""
from __future__ import annotations

import runpy
from pathlib import Path

runpy.run_path(
    str(Path(__file__).resolve().parent.parent / "shims" / "buildings.py"),
    run_name="__main__",
)
