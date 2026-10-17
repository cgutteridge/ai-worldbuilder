#!/usr/bin/env python3
"""Shim — canonical module: active world ``providers/southampton-tpo/tpo_fetch.py``."""
from __future__ import annotations

from world_provider import load_world_provider, run_world_provider

_PACKAGE = "providers/southampton-tpo"
_FILE = "tpo_fetch.py"

_mod = load_world_provider(_PACKAGE, _FILE, "tpo_fetch")
globals().update({k: v for k, v in vars(_mod).items() if not k.startswith("__")})

if __name__ == "__main__":
    run_world_provider(_PACKAGE, _FILE)
