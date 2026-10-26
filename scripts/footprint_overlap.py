#!/usr/bin/env python3
"""CLI wrapper — implementation in ``lib/builds/footprint_overlap.py``."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path


def main() -> None:
    """Activate ``--world`` then run the library module as ``__main__``."""
    tool = Path(__file__).resolve().parent.parent
    if str(tool) not in sys.path:
        sys.path.insert(0, str(tool))
    import script_env

    script_env.prepend_world_activation()
    runpy.run_path(
        str(tool / "lib" / "builds" / "footprint_overlap.py"),
        run_name="__main__",
    )


if __name__ == "__main__":
    main()
