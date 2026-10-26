#!/usr/bin/env python3
"""Shim: type-facing ``lidar_tiles`` → active lidar provider tiles module.

Prefer ``scripts/lidar_provider.tile_client()`` from glue code. This shim
keeps a stable import / CLI name that does not spell a vendor id.
Historical ``defra_lidar`` remains for older docs and provider-local tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

from _provider_shim import ensure_import_paths

ensure_import_paths()


def _load():
    from lidar_provider import load_lidar_module

    return load_lidar_module("tiles")


if __name__ == "__main__":
    tool = Path(__file__).resolve().parent.parent
    if str(tool) not in sys.path:
        sys.path.insert(0, str(tool))
    import script_env

    script_env.prepend_world_activation()
    _mod = _load()
    main_fn = getattr(_mod, "main", None)
    if main_fn is None:
        raise SystemExit(
            "lidar_tiles is a library module; use: python3 -m lidar_tiles … "
            "via the provider CLI, or call lidar_provider.tile_client()"
        )
    raise SystemExit(main_fn())
else:
    _mod = _load()
    sys.modules[__name__] = _mod
