#!/usr/bin/env python3
"""Project-level ``vector_map`` download → cache → reference contract.

Same story as other evidence types (ADR 001 / ``reference/TYPES.txt``):

1. **Download** the regional source once into the active world's ``downloads/``
   (gitignored cache; e.g. a Geofabrik PBF dump).
2. **Extract** place-scoped GeoJSON clips into ``reference/vector_map/<place>/``
   (type-named tree; shared across every cell in the project).
3. **Read** via :func:`vector_map_clip_root` / the footprints index — never by
   pointing ``osm_root=`` at another world's tree as the primary path.

Legacy on-disk folder name (``legacy_reference_paths.VECTOR_MAP_LEGACY_DIRNAME``)
is still resolved when it holds clips and ``vector_map/`` is empty. Manifest rows
with ``kind=map`` are an optional ledger (same ``ref_manifest`` as aerial); the
authoritative clip tree for footprints is always under this world's
``reference/vector_map``.
"""
from __future__ import annotations

from pathlib import Path

from legacy_reference_paths import VECTOR_MAP_LEGACY_DIRNAME  # noqa: E402
from script_env import ROOT  # noqa: E402

# Stable evidence type id (``reference/TYPES.txt``).
TYPE = 'vector_map'

# On-disk layout under the active world root.
REFERENCE_DIRNAME = 'vector_map'
DOWNLOADS_DIRNAME = 'downloads'


def _world_root(world_root: Path | None = None) -> Path:
    """Resolve the world checkout root.

    @param world_root: override (default: active ``ROOT``)
    @returns: absolute world root
    """
    return Path(world_root) if world_root is not None else Path(ROOT)


def downloads_dir(world_root: Path | None = None) -> Path:
    """Directory for cached regional downloads (PBF, etc.) for this world.

    @param world_root: world root override (default: active ``ROOT``)
    @returns: ``<world>/downloads`` (may not exist yet)
    """
    return _world_root(world_root) / DOWNLOADS_DIRNAME


def reference_dir(world_root: Path | None = None) -> Path:
    """Preferred type-named clip tree for this world (may be empty / missing).

    @param world_root: world root override (default: active ``ROOT``)
    @returns: ``<world>/reference/vector_map``
    """
    return _world_root(world_root) / 'reference' / REFERENCE_DIRNAME


def legacy_reference_dir(world_root: Path | None = None) -> Path:
    """Legacy clip-tree path still accepted when populated.

    @param world_root: world root override (default: active ``ROOT``)
    @returns: ``<world>/reference/<legacy>``
    """
    return _world_root(world_root) / 'reference' / VECTOR_MAP_LEGACY_DIRNAME


def default_pbf_path(
    filename: str,
    world_root: Path | None = None,
) -> Path:
    """Default cache path for a regional PBF dump under ``downloads/``.

    @param filename: basename only (e.g. ``hampshire-latest.pbf``)
    @param world_root: world root override (default: active ``ROOT``)
    @returns: absolute path under this world's downloads dir
    @raises ValueError: when ``filename`` is empty or contains path separators
    """
    name = (filename or '').strip()
    if not name or '/' in name or '\\' in name or name in ('.', '..'):
        raise ValueError(f'pbf filename must be a basename, got {filename!r}')
    return downloads_dir(world_root) / name


def place_clip_dir(
    place_slug: str,
    world_root: Path | None = None,
) -> Path:
    """Directory for one place's clips under ``reference/vector_map``.

    Extractors (world provider ``osm_extract``) should write here — not into
    another world's checkout and not via a live cross-world symlink.

    @param place_slug: place folder name (already slugified)
    @param world_root: world root override (default: active ``ROOT``)
    @returns: ``<world>/reference/vector_map/<place_slug>``
    @raises ValueError: when ``place_slug`` is empty or looks like a path
    """
    slug = (place_slug or '').strip().strip('/')
    if not slug or '/' in slug or '\\' in slug or slug in ('.', '..'):
        raise ValueError(f'place_slug must be a single folder name, got {place_slug!r}')
    return reference_dir(world_root) / slug


def _has_clips(path: Path) -> bool:
    """True when ``path`` looks like a populated clip tree (``*/*.geojson``)."""
    return path.is_dir() and any(path.glob('*/*.geojson'))


def clip_count(path: Path) -> int:
    """Count GeoJSON clips under a clip-tree root.

    @param path: clip-tree directory
    @returns: number of ``*/*.geojson`` files (0 if missing)
    """
    if not path.is_dir():
        return 0
    return sum(1 for _ in path.glob('*/*.geojson'))


def has_clips(world_root: Path | None = None) -> bool:
    """True when this world has at least one vector_map (or legacy) GeoJSON clip.

    @param world_root: world root override (default: active ``ROOT``)
    @returns: whether a populated clip tree exists
    """
    root = _world_root(world_root)
    preferred = reference_dir(root)
    legacy = legacy_reference_dir(root)
    return _has_clips(preferred) or _has_clips(legacy)


def vector_map_clip_root(world_root: Path | None = None) -> Path:
    """Directory of vector-map GeoJSON clips for a world checkout.

    Prefers the type-named folder ``reference/vector_map`` when it actually holds
    clips (or is the only tree present). Falls back to the legacy on-disk name so
    an empty ``vector_map/`` created by a first extract cannot hide a full legacy
    tree.

    @param world_root: world root override (default: active ``ROOT``)
    @returns: absolute clip-tree path
    @raises FileNotFoundError: when neither folder exists
    """
    root = _world_root(world_root)
    preferred = reference_dir(root)
    legacy = legacy_reference_dir(root)
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
        f'(or legacy {legacy}). Download the regional PBF into '
        f'{downloads_dir(root)}/, then extract clips into {preferred}/ '
        f'(same-world reference only — do not point osm_root= at another world).'
    )


def project_status(world_root: Path | None = None) -> dict:
    """Project-level vector_map readiness (once per world, shared across cells).

    @param world_root: world root override (default: active ``ROOT``)
    @returns: dict with ``ready``, ``path``, ``clip_count``, ``downloads``,
        ``reference``, ``legacy_fallback``
    """
    root = _world_root(world_root)
    preferred = reference_dir(root)
    legacy = legacy_reference_dir(root)
    try:
        path = vector_map_clip_root(root)
        ready = _has_clips(path)
        count = clip_count(path)
        legacy_fallback = path.resolve() == legacy.resolve()
    except FileNotFoundError:
        path = preferred
        ready = False
        count = 0
        legacy_fallback = False
    return {
        'ready': ready,
        'path': path,
        'clip_count': count,
        'downloads': downloads_dir(root),
        'reference': preferred,
        'legacy_fallback': legacy_fallback,
    }


def is_under_world(path: Path, world_root: Path | None = None) -> bool:
    """True when ``path`` resolves inside the given world checkout.

    Used to refuse treating another world's ``reference/vector_map`` as the
    primary clip root (tests may still pass an alternate tree under a temp dir).

    @param path: candidate clip root
    @param world_root: world root override (default: active ``ROOT``)
    @returns: whether ``path`` is under the world root
    """
    root = _world_root(world_root).resolve()
    try:
        Path(path).resolve().relative_to(root)
        return True
    except ValueError:
        return False
