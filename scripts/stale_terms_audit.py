#!/usr/bin/env python3
"""CLI wrapper — implementation in ``lib/audit/stale_terms_audit.py``."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path


def main() -> None:
    """Run the library module as ``__main__``."""
    tool = Path(__file__).resolve().parent.parent
    if str(tool) not in sys.path:
        sys.path.insert(0, str(tool))
    import script_env  # noqa: F401 — bootstrap lib/* onto path

    runpy.run_path(
        str(tool / "lib" / "audit" / "stale_terms_audit.py"),
        run_name="__main__",
    )


if __name__ == "__main__":
    main()
