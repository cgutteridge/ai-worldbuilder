#!/usr/bin/env python3
"""Thin shim — feature-selection checks live at ``bakeoff/feature_selection_check.py``.

Importing via ``scripts/`` on ``sys.path`` loads the real module. CLI:

  python3 bakeoff/feature_selection_check.py …
  python3 scripts/feature_selection_check.py …
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_TARGET = (
    Path(__file__).resolve().parent.parent
    / 'bakeoff'
    / 'feature_selection_check.py'
)


def _load_real():
    """Load ``bakeoff/feature_selection_check.py`` under its canonical name.

    @returns: the loaded module
    """
    name = 'feature_selection_check'
    existing = sys.modules.get(name)
    if (
        existing is not None
        and getattr(existing, '__file__', None)
        and Path(existing.__file__).resolve() == _TARGET.resolve()
    ):
        return existing
    spec = importlib.util.spec_from_file_location(name, _TARGET)
    if spec is None or spec.loader is None:
        raise ImportError(f'cannot load feature-selection CLI from {_TARGET}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.modules[__name__] = module
    spec.loader.exec_module(module)
    return module


_REAL = _load_real()

if __name__ == '__main__':
    raise SystemExit(_REAL.main())
