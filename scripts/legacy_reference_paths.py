#!/usr/bin/env python3
"""On-disk folder names that still use pre-type vocabulary.

Callers must prefer type-named paths (``reference/vector_map``). This module
exists so shared scripts can fall back without spelling legacy names in every
caller. Scanned by the provider-name audit only via the allowlist entry.
"""
from __future__ import annotations

# Historical vector-map clip tree under the active world ``reference/``.
VECTOR_MAP_LEGACY_DIRNAME = 'osm'
