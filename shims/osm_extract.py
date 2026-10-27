#!/usr/bin/env python3
"""Shim — canonical module: active world ``providers/geofabrik-hampshire/osm_extract.py``.

Also installs street-furniture extract jobs from
``lib/sources/street_furniture.EXTRACT_JOBS`` into the world module's
``LAYER_PRESETS`` so ``--layers furniture`` reaches the vector-map cache.
"""
from __future__ import annotations

import sys
from pathlib import Path

import _provider_shim as _ps

_ps.ensure_import_paths()

from street_furniture import install_furniture_layer_preset  # noqa: E402
from world_provider import load_world_provider  # noqa: E402

_PACKAGE = "providers/geofabrik-hampshire"
_FILE = "osm_extract.py"


def _load_with_furniture():
    """Load world osm_extract and install the furniture LAYER_PRESETS entry.

    @returns: loaded world provider module
    """
    mod = load_world_provider(_PACKAGE, _FILE, "osm_extract")
    install_furniture_layer_preset(mod.LAYER_PRESETS)
    return mod


if __name__ == "__main__":
    tool = Path(__file__).resolve().parent.parent
    if str(tool) not in sys.path:
        sys.path.insert(0, str(tool))
    import script_env

    script_env.prepend_world_activation()
    _mod = _load_with_furniture()
    raise SystemExit(_mod.main())
else:
    _mod = _load_with_furniture()
    globals().update({k: v for k, v in vars(_mod).items() if not k.startswith("__")})
