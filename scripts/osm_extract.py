#!/usr/bin/env python3
"""Deprecated path — use ``python3 shims/osm_extract.py`` (world provider shim)."""
from __future__ import annotations

import runpy
from pathlib import Path

runpy.run_path(
    str(Path(__file__).resolve().parent.parent / "shims" / "osm_extract.py"),
    run_name="__main__",
)
