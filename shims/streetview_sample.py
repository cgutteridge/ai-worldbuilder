#!/usr/bin/env python3
"""Shim: ``streetview_sample`` → ``providers/google-streetview/get/streetview_sample.py``.

Canonical implementation lives under the system provider package. This file
keeps the historical import name and CLI entrypoint working.
"""
from __future__ import annotations

import sys

from _provider_shim import load_provider

_mod = load_provider("google-streetview", "get", "streetview_sample")
# Importers and patches see the implementation module, not this wrapper.
sys.modules[__name__] = _mod

if __name__ == "__main__":
    main_fn = getattr(_mod, "main", None)
    if main_fn is None:
        raise SystemExit(
            "streetview_sample is a library module; import it, do not run as a CLI"
        )
    raise SystemExit(main_fn())
