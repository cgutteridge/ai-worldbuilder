#!/usr/bin/env python3
"""Shim: ``commons_fetch`` → ``providers/commons/get/commons_fetch.py``.

Canonical implementation lives under the system provider package. This file
keeps the historical import name and CLI entrypoint working.
"""
from __future__ import annotations

import sys
from pathlib import Path

from _provider_shim import load_provider

if __name__ == "__main__":
    tool = Path(__file__).resolve().parent.parent
    if str(tool) not in sys.path:
        sys.path.insert(0, str(tool))
    import script_env

    script_env.prepend_world_activation()
    _mod = load_provider("commons", "get", "commons_fetch")
    main_fn = getattr(_mod, "main", None)
    if main_fn is None:
        raise SystemExit(
            "commons_fetch is a library module; import it, do not run as a CLI"
        )
    raise SystemExit(main_fn())
else:
    _mod = load_provider("commons", "get", "commons_fetch")
    sys.modules[__name__] = _mod
