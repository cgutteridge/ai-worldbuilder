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
    'bollard': [(1, 'smooth_stone_wall')],
    'sign': [(1, 'oak_sign')],
}

# GDAL OSM extract jobs: (gdal_layer, SQL WHERE, default model tags).
# World osm_extract LAYER_PRESETS['furniture'] should match this list.
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

DESIGNER_SPEC = ROOT / 'reference' / 'decor-furniture.json'

# Live surface kinds that may host street furniture (plaza / paved walk).
PAVING_KINDS = frozenset({'paving', 'brick'})


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


def designer_items(cell: tuple[int, int], path: Path | None = None) -> list[dict]:
    """Designer-proposed furniture for one cell from ``decor-furniture.json``.

    File shape: ``{"tx,tz": {"evidence": "...", "items": [{"x","z","kind","note"?}]}}``.
    Missing file or missing cell key → empty list.

    @param cell: (tx, tz)
    @param path: override spec path (tests)
    @returns: item dicts with at least ``x``, ``z``, ``kind``
    """
    spec = Path(path) if path is not None else DESIGNER_SPEC
    if not spec.is_file():
        return []
    try:
        data = json.loads(spec.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    entry = data.get(f'{cell[0]},{cell[1]}')
    if not isinstance(entry, dict):
        return []
    out: list[dict] = []
    for raw in entry.get('items') or []:
        if not isinstance(raw, dict):
            continue
        kind = raw.get('kind')
        if kind not in RECIPES:
            continue
        try:
            x, z = int(raw['x']), int(raw['z'])
        except (KeyError, TypeError, ValueError):
            continue
        item = {'x': x, 'z': z, 'kind': kind, 'source': 'designer'}
        if raw.get('note'):
            item['note'] = str(raw['note'])
        if entry.get('evidence'):
            item['evidence'] = str(entry['evidence'])
        out.append(item)
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
