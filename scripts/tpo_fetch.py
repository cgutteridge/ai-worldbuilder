#!/usr/bin/env python3
"""Deprecated path — use ``python3 shims/tpo_fetch.py`` (world provider shim)."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

_TOOL = Path(__file__).resolve().parent.parent
_SHIMS = _TOOL / "shims"
sys.path.insert(0, str(_SHIMS))
runpy.run_path(str(_SHIMS / "tpo_fetch.py"), run_name="__main__")
