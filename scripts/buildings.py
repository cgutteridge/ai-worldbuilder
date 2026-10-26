#!/usr/bin/env python3
"""Deprecated path — use ``python3 shims/buildings.py`` (world provider shim)."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path


def main() -> None:
    """Run the canonical shim under ``shims/``."""
    tool = Path(__file__).resolve().parent.parent
    shims = tool / "shims"
    sys.path.insert(0, str(shims))
    runpy.run_path(str(shims / "buildings.py"), run_name="__main__")


if __name__ == "__main__":
    main()
