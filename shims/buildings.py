#!/usr/bin/env python3
"""Shim — resolve the catalogue ``building_register`` role in the active world."""
from __future__ import annotations

import sys
from pathlib import Path

import _provider_shim as _ps

_ps.ensure_import_paths()

from world_provider import load_world_provider, run_world_provider  # noqa: E402

_ROLE = "building_register"
_FILE = "buildings.py"

if __name__ == "__main__":
    tool = Path(__file__).resolve().parent.parent
    if str(tool) not in sys.path:
        sys.path.insert(0, str(tool))
    import script_env

    script_env.prepend_world_activation()
    run_world_provider(_ROLE, _FILE)
else:
    _mod = load_world_provider(_ROLE, _FILE, "buildings")
    globals().update({k: v for k, v in vars(_mod).items() if not k.startswith("__")})
