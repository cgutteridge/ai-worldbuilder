#!/usr/bin/env python3
"""Locate the active world's vector-map GeoJSON clip tree.

Prefers the type-named folder ``reference/vector_map`` when it actually holds
clips (or is the only tree present). Falls back to the legacy on-disk name via
``legacy_reference_paths`` so an empty ``vector_map/`` created by a first
extract cannot hide a full legacy tree.
"""
from __future__ import annotations

from pathlib import Path

from legacy_reference_paths import VECTOR_MAP_LEGACY_DIRNAME  # noqa: E402
from script_env import ROOT  # noqa: E402


def _has_clips(path: Path) -> bool:
    """True when ``path`` looks like a populated clip tree (``*/*.geojson``)."""
    return path.is_dir() and any(path.glob('*/*.geojson'))


def vector_map_clip_root(world_root: Path | None = None) -> Path:
    """Directory of vector-map GeoJSON clips for a world checkout.

    @param world_root: world root override (default: active ``ROOT``)
    @returns: absolute clip-tree path
    @raises FileNotFoundError: when neither folder exists
    """
    root = Path(world_root) if world_root is not None else Path(ROOT)
    preferred = root / 'reference' / 'vector_map'
    legacy = root / 'reference' / VECTOR_MAP_LEGACY_DIRNAME
    pref_ok = preferred.is_dir()
    leg_ok = legacy.is_dir()
    if pref_ok and _has_clips(preferred):
        return preferred
    if leg_ok and _has_clips(legacy):
        return legacy
    if pref_ok:
        return preferred
    if leg_ok:
        return legacy
    raise FileNotFoundError(
        f'vector-map clip tree missing; expected {preferred} '
        f'(or legacy {legacy})'
    )
