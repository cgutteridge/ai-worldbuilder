#!/usr/bin/env python3
"""WGS84 ↔ OSGB36 via world_frame / pyproj (compatibility re-exports).

Prefer ``scripts/world_frame.py`` for new code. Kept so existing imports continue.
"""
from __future__ import annotations

from world_frame import (  # noqa: F401
    osgb_batch_to_wgs84,
    osgb_to_wgs84,
    transform_epsg,
    wgs84_batch_to_osgb,
    wgs84_to_osgb,
)

__all__ = [
    'wgs84_to_osgb',
    'osgb_to_wgs84',
    'wgs84_batch_to_osgb',
    'osgb_batch_to_wgs84',
    'transform_epsg',
]
