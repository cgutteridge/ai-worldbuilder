#!/usr/bin/env python3
"""Deprecated path — use ``python3 shims/tpo_fetch.py`` (world provider shim)."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

_TARGET = Path(__file__).resolve().parent.parent / "shims" / "tpo_fetch.py"
runpy.run_path(str(_TARGET), run_name="__main__")
