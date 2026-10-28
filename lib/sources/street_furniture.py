#!/usr/bin/env python3
"""Street furniture kinds for the decor pass: map points, recipes, designer specs.

Mapped OSM point tags (bench, bin, bike rack, lamp, bollard, sign) become one
furniture kind each. The decor layer places a small block recipe on paving at
that column. When the map has no points, a designer writes
``reference/decor-furniture.json`` (same idea as trees-extra-crowns.json).

Extract presets (GDAL OSM layer + WHERE) live here so world ``osm_extract`` and
tests share one vocabulary for fetching points into ``reference/vector_map``.
"""
from __future__ import annotations

import json
from pathlib import Path

from script_env import ROOT  # noqa: E402

# Stable kind ids used in recipes, specs, and dry-run reports.
KINDS = (
    'bench',
    'waste_basket',
    'bicycle_parking',
    'street_lamp',
    'bollard',
    'sign',
)

# Block stacks relative to the live surface: (dy_above_surface, block).
# dy=1 is the first air cell above the top solid.
RECIPES: dict[str, list[tuple[int, str]]] = {
    'bench': [(1, 'spruce_stairs')],
    'waste_basket': [(1, 'cauldron')],
    'bicycle_parking': [(1, 'iron_bars')],
    'street_lamp': [(1, 'oak_fence'), (2, 'oak_fence'), (3, 'lantern')],
    'bollard': [(1, 'stone_brick_wall')],
    'sign': [(1, 'oak_sign')],
}

# Real Minecraft wall blocks used elsewhere in this repo (decor / garden / block ref).
# Recipe ``*_wall`` names must be one of these — ``smooth_stone_wall`` is not a block.
REAL_WALL_BLOCKS = frozenset({
    'brick_wall',
    'stone_brick_wall',
    'cobblestone_wall',
})

# GDAL OSM extract jobs: (gdal_layer, SQL WHERE, default model tags).
# Installed into world osm_extract ``LAYER_PRESETS['furniture']`` by the tool
# shim (``shims/osm_extract.py``) via :func:`install_furniture_layer_preset`.
EXTRACT_JOBS: list[tuple[str, str, list[str]]] = [
    (
        'points',
        (
            "amenity IN ('bench','waste_basket','bicycle_parking') "
            "OR highway = 'street_lamp' "
            "OR barrier = 'bollard' "
            "OR traffic_sign IS NOT NULL "
            "OR information IS NOT NULL"
        ),
        ['context'],
    ),
]


def install_furniture_layer_preset(
    layer_presets: dict[str, list[tuple[str, str, list[str]]]],
) -> None:
    """Wire :data:`EXTRACT_JOBS` into an osm_extract ``LAYER_PRESETS`` dict.

    Overwrites any existing ``furniture`` entry so the tool vocabulary is the
    single source of truth for fetching street-furniture points into
    ``reference/vector_map``.

    @param layer_presets: mutable world (or test) ``LAYER_PRESETS`` mapping
    """
    layer_presets['furniture'] = list(EXTRACT_JOBS)

DESIGNER_SPEC = ROOT / 'reference' / 'decor-furniture.json'

# Live surface kinds that may host street furniture (plaza / paved walk).
# Surfaces furniture must not stand on (car lanes); lawn, gravel, paths and paving are fine.
ROAD_KINDS = frozenset({'tarmac', 'mark', 'kerb'})


def classify(tags: dict) -> str | None:
    """Map OSM tags to a furniture kind, or None when not street furniture.

    @param tags: flattened feature properties (including other_tags merges)
    @returns: one of :data:`KINDS`, or None
    """
    amenity = tags.get('amenity')
    if amenity == 'bench':
        return 'bench'
    if amenity == 'waste_basket':
        return 'waste_basket'
    if amenity == 'bicycle_parking':
        return 'bicycle_parking'
    if tags.get('highway') == 'street_lamp':
        return 'street_lamp'
    if tags.get('barrier') == 'bollard':
        return 'bollard'
    if tags.get('traffic_sign') or tags.get('information') or tags.get('tourism') == 'information':
        return 'sign'
    return None


def recipe(kind: str) -> list[tuple[int, str]]:
    """Block stack for one furniture kind.

    @param kind: furniture kind id
    @returns: list of (dy_above_surface, block id without ``minecraft:``)
    @raises KeyError: unknown kind
    """
    return list(RECIPES[kind])


BARRIER_KINDS = frozenset({'hedge', 'brick_wall', 'iron_bars', 'spruce_fence'})


def _load_designer_entry(cell: tuple[int, int], path: Path | None = None) -> dict:
    """Raw designer JSON entry for one cell, or ``{}`` when missing/invalid."""
    spec = Path(path) if path is not None else DESIGNER_SPEC
    if not spec.is_file():
        return {}
    try:
        data = json.loads(spec.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    entry = data.get(f'{cell[0]},{cell[1]}')
    return entry if isinstance(entry, dict) else {}


def designer_items(cell: tuple[int, int], path: Path | None = None) -> list[dict]:
    """Designer-proposed furniture and barrier strips for one cell.

    File shape: ``{"tx,tz": {"evidence": "...", "items": [
      {"x","z","kind","note"?},
      {"kind":"hedge","cells":[[x,z],...],"note"?,"allow_against_build"?}
    ]}}``.
    Missing file or missing cell key → empty list.

    @param cell: (tx, tz)
    @param path: override spec path (tests)
    @returns: item dicts (furniture with ``x``/``z``/``kind``, or barriers with ``cells``)
    """
    entry = _load_designer_entry(cell, path)
    if not entry:
        return []
    evidence = str(entry['evidence']) if entry.get('evidence') else ''
    out: list[dict] = []
    for raw in entry.get('items') or []:
        if not isinstance(raw, dict):
            continue
        kind = raw.get('kind')
        if kind in RECIPES:
            try:
                x, z = int(raw['x']), int(raw['z'])
            except (KeyError, TypeError, ValueError):
                continue
            item = {'x': x, 'z': z, 'kind': kind, 'source': 'designer'}
            if raw.get('note'):
                item['note'] = str(raw['note'])
            if evidence:
                item['evidence'] = evidence
            out.append(item)
            continue
        if kind in BARRIER_KINDS and raw.get('cells'):
            cells = []
            for c in raw['cells']:
                try:
                    cells.append([int(c[0]), int(c[1])])
                except (TypeError, ValueError, IndexError):
                    continue
            if not cells:
                continue
            item = {
                'kind': kind,
                'cells': cells,
                'source': 'designer',
                'allow_against_build': bool(raw.get('allow_against_build', kind == 'hedge')),
            }
            if raw.get('note'):
                item['note'] = str(raw['note'])
            if evidence:
                item['evidence'] = evidence
            out.append(item)
    return out


def designer_observations(cell: tuple[int, int], path: Path | None = None) -> list[dict]:
    """Photo/aerial observations that classify lidar objects or record uncertainty.

    File shape under the cell key: ``"observations": [{"kind", "note"?,
    "cell": [x,z]?, "source": "photo_read"|"aerial"?, "evidence_ids": [...]?,
    "unresolved_reason"?}]``. Coordinate-bearing permanent furniture labels can
    authorize a detected lidar object's placement; source defaults to photo_read.
    Missing coordinates or unresolved_reason keep observations unresolved.

    @param cell: (tx, tz)
    @param path: override spec path (tests)
    @returns: observation dicts for the decor plan
    """
    entry = _load_designer_entry(cell, path)
    out: list[dict] = []
    for raw in entry.get('observations') or []:
        if not isinstance(raw, dict) or not raw.get('kind'):
            continue
        obs = {'kind': str(raw['kind'])}
        if raw.get('cell') is not None:
            try:
                obs['cell'] = [int(raw['cell'][0]), int(raw['cell'][1])]
            except (TypeError, ValueError, IndexError):
                continue
        if raw.get('source'):
            obs['source'] = str(raw['source'])
        if raw.get('evidence_ids'):
            obs['evidence_ids'] = list(raw['evidence_ids'])
        if raw.get('note'):
            obs['note'] = str(raw['note'])
        if raw.get('unresolved_reason'):
            obs['unresolved_reason'] = str(raw['unresolved_reason'])
        elif entry.get('evidence'):
            obs['note'] = obs.get('note') or str(entry['evidence'])
        out.append(obs)
    return out


def map_items(furniture: list[tuple[int, int, str]]) -> list[dict]:
    """Normalise OSMGround furniture triples to the same item dicts as the designer.

    @param furniture: ``(x, z, kind)`` from the vector-map cache
    @returns: item dicts with ``source='map'``
    """
    out: list[dict] = []
    for x, z, kind in furniture:
        if kind not in RECIPES:
            continue
        out.append({'x': int(x), 'z': int(z), 'kind': kind, 'source': 'map'})
    return out


def merge_items(mapped: list[dict], designed: list[dict]) -> list[dict]:
    """Combine map and designer items; a mapped point at the same column wins.

    @param mapped: items from the map cache
    @param designed: items from the designer spec
    @returns: sorted unique items (by x, z, kind)
    """
    by_col: dict[tuple[int, int], dict] = {}
    for item in designed + mapped:  # map last so it overwrites designer
        by_col[(item['x'], item['z'])] = item
    return sorted(by_col.values(), key=lambda i: (i['x'], i['z'], i['kind']))
