#!/usr/bin/env python3
"""Shim: type-facing ``lidar_extremes`` → active lidar provider extremes module.

Resolved through ``lib/sources/lidar_provider.py`` by catalogue role. This shim
keeps a stable import / CLI name that does not spell a vendor id.
"""
from __future__ import annotations

import sys
from pathlib import Path

from _provider_shim import ensure_import_paths

ensure_import_paths()


def _load():
    from lidar_provider import load_lidar_module

    return load_lidar_module("extremes")


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
            "lidar_extremes is a library module; import it, do not run as a CLI"
        )
    raise SystemExit(main_fn())
else:
    _mod = _load()
    sys.modules[__name__] = _mod
