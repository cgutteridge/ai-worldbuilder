#!/usr/bin/env python3
"""Read-only audit of ground surfaces standing over air (implementation: ``lib/layers/ground_float_audit.py``)."""
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
    from ground_float_audit import main

    raise SystemExit(main())
