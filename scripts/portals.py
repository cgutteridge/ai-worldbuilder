#!/usr/bin/env python3
"""Shim — canonical module: active world ``providers/uos-portals/portals.py``."""
from __future__ import annotations

from world_provider import load_world_provider, run_world_provider

_PACKAGE = "providers/uos-portals"
_FILE = "portals.py"

_mod = load_world_provider(_PACKAGE, _FILE, "portals")
globals().update({k: v for k, v in vars(_mod).items() if not k.startswith("__")})

if __name__ == "__main__":
    run_world_provider(_PACKAGE, _FILE)
