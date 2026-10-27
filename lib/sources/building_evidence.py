"""Mapped building tags and entrance nodes for the designer evidence table (#159).

Lidar remains the authority for height and roof form. Vector-map tags
``roof:shape``, ``roof:levels``, ``height`` and ``building:levels`` are
surfaced only as **hints** beside lidar — never an override.

``entrance=*`` points (fetched by the cell-extract ``entrances`` preset) become
candidate door positions: each node is snapped to the nearest point on the
footprint outer ring and ranked main → yes → service → emergency → other for
``facade_plan`` input (after portals / street photos; see evidence-order).
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from osm_footprints import osm_tags

# Tags offered as map hints in the feature evidence table.
HINT_KEYS = ('roof:shape', 'roof:levels', 'height', 'building:levels')

# Lower rank = listed first for the designer.
_ENTRANCE_RANK: dict[str, int] = {
    'main': 0,
    'yes': 1,
    'home': 1,
    'staircase': 2,
    'service': 3,
    'secondary': 3,
    'exit': 4,
    'emergency': 5,
}
_ENTRANCE_RANK_OTHER = 6


def map_building_hints(props: Mapping[str, Any] | None) -> dict[str, str]:
    """Extract map roof / height / levels tags from one footprint's properties.

    @param props: ogr2ogr feature properties (may include ``other_tags`` hstore)
    @returns: subset of :data:`HINT_KEYS` present on the footprint (string values)
    """
    if not props:
        return {}
    tags = osm_tags(dict(props))
    out: dict[str, str] = {}
    for key in HINT_KEYS:
        raw = tags.get(key)
        if raw is None or raw == '':
            continue
        out[key] = str(raw)
    return out


def evidence_table(
    *,
    lidar: Mapping[str, Any] | None = None,
    map_hints: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Structured feature evidence rows: lidar facts plus map tags as hints.

    Map rows always carry ``role='hint'``. Lidar rows carry ``role='lidar'``.
    Callers must not treat hints as overrides of lidar storeys / roof / height.

    @param lidar: optional lump dict (``storeys``, ``roof``, ``ridge_axis``, ``height``…)
    @param map_hints: from :func:`map_building_hints`
    @returns: ordered row dicts for :func:`format_evidence_table`
    """
    rows: list[dict[str, Any]] = []
    if lidar:
        rows.append({
            'role': 'lidar',
            'storeys': lidar.get('storeys'),
            'roof': lidar.get('roof'),
            'ridge_axis': lidar.get('ridge_axis'),
            'height': lidar.get('height'),
        })
    hints = dict(map_hints or {})
    if hints:
        rows.append({'role': 'hint', 'tags': hints})
    return rows


def format_evidence_table(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Plain-text evidence-table lines for the feature brief / designer hand-off.

    @param rows: :func:`evidence_table` output
    @returns: brief lines (empty when no rows)
    """
    if not rows:
        return []
    lines = ['Evidence table (lidar decides height/roof; map tags are hints only):']
    for row in rows:
        role = row.get('role')
        if role == 'lidar':
            bits = ['lidar']
            if row.get('storeys') is not None:
                bits.append(f'{row["storeys"]} storeys (estimate)')
            if row.get('roof') is not None:
                bits.append(f'roof {row["roof"]}')
            if row.get('ridge_axis'):
                bits.append(f'ridge axis {row["ridge_axis"]}')
            if row.get('height') is not None:
                bits.append(f'height {row["height"]}')
            lines.append('  ' + ', '.join(bits))
        elif role == 'hint':
            tags = row.get('tags') or {}
            parts = [f'{k}={v}' for k, v in tags.items()]
            lines.append('  map hint (never override lidar): ' + ', '.join(parts))
    return lines


def _ring_segments(rings: Sequence[Sequence[Sequence[float]]]) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Outer-ring edge segments from footprint rings (closed or open)."""
    if not rings or not rings[0]:
        return []
    outer = list(rings[0])
    if len(outer) < 2:
        return []
    if outer[0] != outer[-1]:
        outer = outer + [outer[0]]
    return [(tuple(a[:2]), tuple(b[:2])) for a, b in zip(outer, outer[1:])]


def _seg_foot(px: float, pz: float, a: tuple[float, float], b: tuple[float, float]) -> tuple[float, tuple[float, float]]:
    ax, az = a
    bx, bz = b
    dx, dz = bx - ax, bz - az
    if dx == 0 and dz == 0:
        return math.hypot(px - ax, pz - az), (ax, az)
    t = max(0.0, min(1.0, ((px - ax) * dx + (pz - az) * dz) / (dx * dx + dz * dz)))
    fx, fz = ax + t * dx, az + t * dz
    return math.hypot(px - fx, pz - fz), (fx, fz)


def snap_entrance_to_edge(
    x: float,
    z: float,
    rings: Sequence[Sequence[Sequence[float]]],
) -> dict[str, Any]:
    """Snap an entrance node to the nearest point on the footprint outer edge.

    @param x: entrance easting (MC)
    @param z: entrance southing (MC)
    @param rings: footprint rings ``[[(x, z), ...], ...]`` (outer first)
    @returns: ``{at: (int, int), raw: (x, z), foot: (float, float), dist: float}``
    @raises ValueError: when the footprint has no edge segments
    """
    segs = _ring_segments(rings)
    if not segs:
        raise ValueError('footprint has no edge to snap to')
    best_d = None
    best_foot: tuple[float, float] = (float(x), float(z))
    for a, b in segs:
        d, foot = _seg_foot(float(x), float(z), a, b)
        if best_d is None or d < best_d:
            best_d = d
            best_foot = foot
    assert best_d is not None
    fx, fz = best_foot
    return {
        'at': (int(round(fx)), int(round(fz))),
        'raw': (float(x), float(z)),
        'foot': (fx, fz),
        'dist': float(best_d),
    }


def entrance_rank(kind: str | None) -> int:
    """Sort key for an ``entrance=*`` value (lower = listed first).

    @param kind: OSM entrance tag value
    @returns: rank integer
    """
    if not kind:
        return _ENTRANCE_RANK_OTHER
    return _ENTRANCE_RANK.get(str(kind).lower(), _ENTRANCE_RANK_OTHER)


def mapped_entrance_candidates(
    nodes: Sequence[Mapping[str, Any]],
    rings: Sequence[Sequence[Sequence[float]]],
) -> list[dict[str, Any]]:
    """Snap entrance nodes to the footprint edge and rank for the designer.

    Order: main, yes/home, service/secondary, emergency, then other. Nodes
    without an ``entrance`` key are skipped.

    @param nodes: ``{x, z, entrance, osm_id?}`` (MC floats)
    @param rings: footprint rings to snap against
    @returns: candidate dicts with snapped ``at``, ``entrance``, ``raw``, ``dist``, ``osm_id``
    """
    out: list[dict[str, Any]] = []
    for node in nodes:
        kind = node.get('entrance')
        if kind in (None, ''):
            continue
        snap = snap_entrance_to_edge(float(node['x']), float(node['z']), rings)
        out.append({
            'entrance': str(kind),
            'at': snap['at'],
            'raw': snap['raw'],
            'dist': snap['dist'],
            'osm_id': node.get('osm_id') or node.get('osm_way_id'),
        })
    out.sort(key=lambda c: (entrance_rank(c['entrance']), c['dist'], c['at']))
    return out


def format_entrance_candidates(candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    """Brief lines: snapped map entrance candidates for ``facade_plan``.

    @param candidates: :func:`mapped_entrance_candidates` output
    @returns: brief lines (empty when none)
    """
    if not candidates:
        return []
    lines = [
        'Mapped entrance=* candidates (vector_map; snap to footprint edge; '
        'rank main > yes > service > emergency; last-resort after portals/photos):',
    ]
    for c in candidates:
        oid = c.get('osm_id')
        oid_bit = f' osm_id={oid}' if oid is not None else ''
        ax, az = c['at']
        lines.append(
            f'  candidate entrance={c["entrance"]} at {ax},{az} '
            f'(raw {c["raw"][0]:.1f},{c["raw"][1]:.1f}, dist {c["dist"]:.1f}){oid_bit}'
        )
    lines.append(
        'Offer as facade_plan door positions when portals/street photos do not '
        'already place the opening; do not auto-place from the map alone.'
    )
    return lines


# GDAL extract job for entrance points (installed under LAYER_PRESETS['entrances']).
ENTRANCE_EXTRACT_JOBS: list[tuple[str, str, list[str]]] = [
    (
        'points',
        "entrance IS NOT NULL AND entrance <> ''",
        ['context'],
    ),
]


def install_entrances_layer_preset(
    layer_presets: dict[str, list[tuple[str, str, list[str]]]],
) -> None:
    """Wire entrance point extract jobs into an osm_extract ``LAYER_PRESETS`` dict.

    @param layer_presets: mutable world (or test) ``LAYER_PRESETS`` mapping
    """
    layer_presets['entrances'] = list(ENTRANCE_EXTRACT_JOBS)
