#!/usr/bin/env python3
"""Thin shim — bake-off CLI lives at ``bakeoff/model_bakeoff.py``.

Importing ``model_bakeoff`` via ``scripts/`` on ``sys.path`` loads the real
module (so the empty shim cannot shadow it). CLI:

  python3 bakeoff/model_bakeoff.py …
  python3 scripts/model_bakeoff.py …
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_TARGET = Path(__file__).resolve().parent.parent / 'bakeoff' / 'model_bakeoff.py'


def _load_real():
    """Load ``bakeoff/model_bakeoff.py`` and register it as ``model_bakeoff``.

    @returns: the loaded module
    """
    existing = sys.modules.get('model_bakeoff')
    if (
        existing is not None
        and getattr(existing, '__file__', None)
        and Path(existing.__file__).resolve() == _TARGET.resolve()
    ):
        return existing
    spec = importlib.util.spec_from_file_location('model_bakeoff', _TARGET)
    if spec is None or spec.loader is None:
        raise ImportError(f'cannot load bake-off CLI from {_TARGET}')
    module = importlib.util.module_from_spec(spec)
    sys.modules['model_bakeoff'] = module
    # When this file was imported as model_bakeoff, replace the shim entry too.
    sys.modules[__name__] = module
    spec.loader.exec_module(module)
    return module


_REAL = _load_real()

if __name__ == '__main__':
    raise SystemExit(_REAL.main())
