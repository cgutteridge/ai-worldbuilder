#!/usr/bin/env python3
"""List landed feature plans with a stale or unrecorded builder/spec (read-only)."""
from __future__ import annotations

import script_env  # noqa: F401 — shared library import paths
from builder_audit import main


if __name__ == '__main__':
    raise SystemExit(main())
