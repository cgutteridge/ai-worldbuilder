#!/usr/bin/env python3
"""Shim: type-facing ``lidar_world`` → active lidar provider world module.

Prefer ``scripts/lidar_provider.composite_world()`` from glue code. This shim
keeps a stable import / CLI name that does not spell a vendor id.
"""
from __future__ import annotations

import sys

from _provider_shim import ensure_import_paths

ensure_import_paths()
from lidar_provider import load_lidar_module  # noqa: E402

_mod = load_lidar_module("world")
sys.modules[__name__] = _mod

if __name__ == "__main__":
    main_fn = getattr(_mod, "main", None)
    if main_fn is None:
        raise SystemExit(
            "lidar_world is a library module; import it, do not run as a CLI"
        )
    raise SystemExit(main_fn())
