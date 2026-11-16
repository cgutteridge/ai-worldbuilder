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
import math
import random
from collections.abc import Mapping, Sequence
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


BARRIER_KINDS = frozenset({'hedge', 'brick_wall', 'iron_bars', 'spruce_fence', 'plank_fence'})
OUTBUILDING_KINDS = frozenset({'shed', 'garage'})   # designer traces: ``cells`` plus an evidence id (#251)


def _int_cells(raw) -> list[list[int]]:
    out = []
    for c in raw:
        try:
            out.append([int(c[0]), int(c[1])])
        except (TypeError, ValueError, IndexError):
            continue
    return out


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
      {"kind":"hedge","cells":[[x,z],...],"note"?,"allow_against_build"?},
      {"kind":"shed"|"garage","cells":[[x,z],...],"evidence_ids":["<id>"],"note"?}
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
        if kind in OUTBUILDING_KINDS and raw.get('cells'):
            cells = _int_cells(raw['cells'])
            if not cells:
                continue
            item = {'kind': kind, 'cells': cells, 'source': 'designer'}
            ids = raw.get('evidence_ids') or ([raw['evidence_id']] if raw.get('evidence_id') else [])
            if ids:
                item['evidence_ids'] = [str(i) for i in ids]
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


# --- inferred furniture (#260, I-008) ----------------------------------------------------
# Where the map has no points, lamps and benches are inferred from the ways themselves. Every
# item is labelled ``source='inference'``, ``confidence='low'``: a guess from road class or land
# use, never an observation. The plan (``decor_plan``) merges them under mapped and designer rows.
INFERRED = 'inference'
LAMP_VERGE = 1.5        # blocks from the carriageway or path edge to the lamp column centre
BENCH_VERGE = 1.5
LIT_FALLBACK_SPACING = 40.0   # OSM lit=yes on a way the table leaves unlit (path, cycleway, service)
UNLIT_FOOTWAYS = ('sidewalk', 'crossing')   # footway=* values lit by the road they run beside


def stable_seed(*parts) -> int:
    """Deterministic integer seed from identity parts (cell, way id): same input, same lamps."""
    import hashlib

    return int.from_bytes(hashlib.sha256('|'.join(str(p) for p in parts).encode()).digest()[:8], 'big')


def polyline_length(points: Sequence[Sequence[float]]) -> float:
    """Length of a polyline in blocks."""
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def _stations(points: Sequence[Sequence[float]], distances: Sequence[float]):
    """``(x, z, tx, tz)`` at each arc distance along a polyline: the point and unit tangent there.

    @param points: polyline vertices ``(x, z)``
    @param distances: ascending arc distances; those beyond the line's end are dropped
    """
    segments = []
    run = 0.0
    for a, b in zip(points, points[1:]):
        length = math.dist(a, b)
        if length > 0:
            segments.append((run, length, a, b))
        run += length
    k = 0
    for d in distances:
        while k < len(segments) and d > segments[k][0] + segments[k][1]:
            k += 1
        if k == len(segments):
            return
        start, length, a, b = segments[k]
        t = (d - start) / length
        tx, tz = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        yield a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, tx, tz


def _item(kind: str, x: float, z: float, **extra) -> dict:
    return {'kind': kind, 'x': math.floor(x), 'z': math.floor(z),
            'source': INFERRED, 'confidence': 'low', **extra}


def lamp_spacing_for(highway_class: str, spacing_table: Mapping[str, float | None], lit: str | None = None):
    """Lamp spacing in blocks for one way, or None when it gets no lamps.

    ``lit=no`` (OSM) always means none. ``lit=yes`` lights a way the table leaves unlit, at
    :data:`LIT_FALLBACK_SPACING`. A table value of None or a missing class is unlit.

    @param highway_class: OSM highway value
    @param spacing_table: ``{highway: spacing or None}``
    @param lit: OSM ``lit`` tag value, or None when the way carries none
    @returns: spacing, or None
    """
    if lit == 'no':
        return None
    spacing = spacing_table.get(highway_class)
    if spacing is None:
        return LIT_FALLBACK_SPACING if lit == 'yes' else None
    return float(spacing) if spacing > 0 else None


def lamp_positions(
    road_polyline: Sequence[Sequence[float]],
    highway_class: str,
    spacing_table: Mapping[str, float | None],
    rng_seed: int,
    *,
    half_width: float = 3.0,
    lit: str | None = None,
    verge: float = LAMP_VERGE,
) -> list[dict]:
    """Street lamps along one way, staggered either side, labelled as inference.

    Lamps stand every ``spacing`` blocks along the line, alternating sides, ``half_width + verge``
    from the centreline (beside the carriageway, never on it). The seed sets the starting phase and
    first side, so a way always gets the same lamps. No spacing for the class (or ``lit=no``) gives
    none. Keep-outs (door routes, openings, buildings, mapped points) are the caller's business.

    @param road_polyline: centreline vertices ``(x, z)`` in world columns
    @param highway_class: OSM highway value
    @param spacing_table: ``{highway: spacing in blocks or None}`` (resolved by the caller)
    @param rng_seed: deterministic seed for phase and first side
    @param half_width: half the carriageway or path width in blocks
    @param lit: OSM ``lit`` tag value if the way carries one
    @param verge: gap between the way's edge and the lamp column centre
    @returns: items ``{kind: 'street_lamp', x, z, source: 'inference', confidence: 'low', highway,
        spacing, side}``
    """
    spacing = lamp_spacing_for(highway_class, spacing_table, lit)
    if spacing is None or len(road_polyline) < 2:
        return []
    rng = random.Random(rng_seed)
    start = rng.uniform(0, spacing)
    side = rng.choice((1, -1))
    count = int((polyline_length(road_polyline) - start) // spacing) + 1
    stations = _stations(road_polyline, [start + i * spacing for i in range(max(count, 0))])
    out, seen = [], set()
    offset = half_width + verge
    for x, z, tx, tz in stations:
        item = _item('street_lamp', x - tz * offset * side, z + tx * offset * side,
                     highway=highway_class, spacing=spacing, side='left' if side > 0 else 'right')
        side = -side
        if (item['x'], item['z']) not in seen:
            seen.add((item['x'], item['z']))
            out.append(item)
    return out


def bench_positions(
    path_polyline: Sequence[Sequence[float]],
    per_100m: float,
    rng_seed: int,
    *,
    half_width: float = 1.0,
    verge: float = BENCH_VERGE,
) -> list[dict]:
    """Benches beside one path at ``per_100m`` per 100 blocks, labelled as inference.

    The expected count is ``per_100m * length / 100``, rounded up or down by the seed, so a short
    path gets a bench now and then rather than always or never. Benches spread evenly along the
    path (a small jitter per bench), each on a seeded side, ``half_width + verge`` off the centreline.

    @param path_polyline: path centreline vertices ``(x, z)``
    @param per_100m: bench density (from the class row, a profile or a convention); 0 or None gives none
    @param rng_seed: deterministic seed
    @param half_width: half the path width in blocks
    @param verge: gap between the path edge and the bench column centre
    @returns: items ``{kind: 'bench', x, z, source: 'inference', confidence: 'low', side}``
    """
    if not per_100m or per_100m <= 0 or len(path_polyline) < 2:
        return []
    length = polyline_length(path_polyline)
    rng = random.Random(rng_seed)
    expected = per_100m * length / 100.0
    n = int(expected) + (1 if rng.random() < expected - int(expected) else 0)
    if n == 0:
        return []
    gap = length / n
    distances = [min(length, max(0.0, (i + 0.5) * gap + rng.uniform(-gap / 4, gap / 4))) for i in range(n)]
    out, seen = [], set()
    offset = half_width + verge
    for x, z, tx, tz in _stations(path_polyline, sorted(distances)):
        side = rng.choice((1, -1))
        item = _item('bench', x - tz * offset * side, z + tx * offset * side,
                     side='left' if side > 0 else 'right')
        if (item['x'], item['z']) not in seen:
            seen.add((item['x'], item['z']))
            out.append(item)
    return out
