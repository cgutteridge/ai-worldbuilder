#!/usr/bin/env python3
"""Shim — canonical module: active world ``providers/hampshire-hub/aerial_mosaic.py``."""
from __future__ import annotations

from world_provider import load_world_provider, run_world_provider

_PACKAGE = "providers/hampshire-hub"
_FILE = "aerial_mosaic.py"

_mod = load_world_provider(_PACKAGE, _FILE, "aerial_mosaic")
globals().update({k: v for k, v in vars(_mod).items() if not k.startswith("__")})

if __name__ == "__main__":
    run_world_provider(_PACKAGE, _FILE)
