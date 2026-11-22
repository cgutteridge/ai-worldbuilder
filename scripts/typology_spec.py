#!/usr/bin/env python3
"""Write typology specs from footprints, lidar and the district profile.

Dry run unless ``--write``. Writes spec JSON only and never places blocks.
Implementation: ``lib/builds/typology_spec.py``.
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
    from typology_spec import main

    raise SystemExit(main())
