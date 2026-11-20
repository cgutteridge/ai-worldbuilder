#!/usr/bin/env python3
"""Register a verified legacy feature builder (dry run by default)."""
from __future__ import annotations

import script_env  # noqa: F401 — shared library import paths
from builder_registration import main


if __name__ == '__main__':
    raise SystemExit(main())
