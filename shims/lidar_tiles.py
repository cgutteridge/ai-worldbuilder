#!/usr/bin/env python3
"""Shim: type-facing ``lidar_tiles`` → active lidar provider tiles module.

Prefer ``scripts/lidar_provider.tile_client()`` from glue code. This shim
keeps a stable import / CLI name that does not spell a vendor id.
Historical ``defra_lidar`` remains for older docs and provider-local tests.
"""
from __future__ import annotations

import sys

from _provider_shim import ensure_import_paths

ensure_import_paths()
from lidar_provider import load_lidar_module  # noqa: E402

_mod = load_lidar_module("tiles")
sys.modules[__name__] = _mod

if __name__ == "__main__":
    main_fn = getattr(_mod, "main", None)
    if main_fn is None:
        raise SystemExit(
            "lidar_tiles is a library module; use: python3 -m lidar_tiles … "
            "via the provider CLI, or call lidar_provider.tile_client()"
        )
    raise SystemExit(main_fn())
