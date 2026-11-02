#!/usr/bin/env python3
"""Re-run a feature's recorded current builder without redesign (dry run by default)."""
from __future__ import annotations

import script_env  # noqa: F401 — shared library import paths
from feature_reapply import main


if __name__ == '__main__':
    raise SystemExit(main())
