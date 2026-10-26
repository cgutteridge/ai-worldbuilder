#!/usr/bin/env python3
"""Shim — canonical module: active world ``providers/geofabrik-hampshire/osm_extract.py``."""
from __future__ import annotations

import _provider_shim as _ps

_ps.ensure_import_paths()

from world_provider import load_world_provider, run_world_provider  # noqa: E402

_PACKAGE = "providers/geofabrik-hampshire"
_FILE = "osm_extract.py"

_mod = load_world_provider(_PACKAGE, _FILE, "osm_extract")
globals().update({k: v for k, v in vars(_mod).items() if not k.startswith("__")})

if __name__ == "__main__":
    run_world_provider(_PACKAGE, _FILE)
