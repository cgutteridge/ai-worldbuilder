#!/usr/bin/env python3
"""Garden outbuildings (sheds, garages) for the decor pass (#251).

Decor owns them (``rules/frontage-and-evidence.md``); attached garages inside a house footprint
stay with the feature pass. An outbuilding is a decor plan item (``kind`` ``outbuilding``,
``role`` ``outbuilding``) with shared provenance, found in this order and never from aerial
colour alone:

  1. MAPPED    a footprint-map building tagged in ``coverage.OUTBUILDINGS`` -> confidence high.
  2. LIDAR     a raw nDSM blob (DSM - DTM 1.6-4.5 m, >= 6 connected columns) off building
               footprints plus a one-column ring, off lidar tree canopy and off other decor
               objects, whose aerial colour is not green (lawn + shade < 0.5) -> medium.
               ``lidar_heightmap.Maps`` cannot serve this: it marks roof only on mapped footprints.
  3. DESIGNER  a trace in the world's ``reference/decor-furniture.json`` (kind ``shed`` /
               ``garage``, ``cells``, an evidence id) -> medium.

No default scatter: nothing here invents an outbuilding without one of those three.

Massing is a squared rectangle from the evidence cells, walls 2 high (shed) or 3 (garage) under
a one-block roof layer, planks or brick by kind, flat or mono-pitch roof, a door facing the house
or route, the top clamped to the nDSM height. Ground height comes from lidar; the placer in
``decor_layer`` refuses (with an unplaced reason) a footprint on a route, clearance column,
entrance opening, carriageway or across a plot line.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import provenance as prov

KIND = 'outbuilding'
TYPES = ('shed', 'garage')
DESIGNER_KINDS = frozenset(TYPES)

# Mapped ``building=*`` values that read as a garage; the rest of coverage.OUTBUILDINGS are sheds.
GARAGE_TAGS = frozenset({'garage', 'garages', 'carport'})

RISE_MIN, RISE_MAX = 1.6, 4.5     # nDSM band (metres above bare ground) of a garden outbuilding
MIN_BLOB_COLUMNS = 6              # smaller lumps are bins, cars and noise: ignored
MAX_BLOB_COLUMNS = 120            # larger is a missed building, not a shed
MIN_SHORT_SIDE = 2                # a 1-wide raised strip is a fence or wall line
GREEN_SHARE_MAX = 0.5             # lawn + shade share of the blob's aerial colour; at or above = vegetation
GARAGE_MIN_COLUMNS = 14           # lidar blobs this large (about 3 x 5 m) read as a garage
MIN_FILL = 0.5                    # edge rows / columns thinner than this share are trimmed when squaring
LIDAR_MARGIN = 8                  # blocks of lidar read past the cell so blobs on its edge stay whole

DEFAULT_TOTAL = {'shed': 3, 'garage': 4}            # walls + one roof layer
WALL_BLOCK = {'shed': 'spruce_planks', 'garage': 'bricks'}
ROOF_SLAB = {'shed': 'spruce_slab', 'garage': 'stone_brick_slab'}
ROOF_STAIR = {'shed': 'spruce_stairs', 'garage': 'stone_brick_stairs'}
DOOR_BLOCK = {'shed': 'spruce_door', 'garage': 'iron_door'}
FACINGS = {(0, -1): 'north', (0, 1): 'south', (-1, 0): 'west', (1, 0): 'east'}


def _item_id(*parts: Any) -> str:
    return 'outbuilding-' + '-'.join(str(p).replace('-', 'm') for p in parts)


def _components(cells: Iterable[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    from garden_sources import components
    return components(cells)


def mapped_type(building_tag: str) -> str:
    """``shed`` or ``garage`` for a mapped ``building=*`` outbuilding tag."""
    return 'garage' if building_tag in GARAGE_TAGS else 'shed'


def mapped_outbuilding_footprints(idx, cell_box: tuple[int, int, int, int]) -> list[dict[str, Any]]:
    """Mapped outbuildings with a column inside ``cell_box`` (all their columns, even past its edge), from an ``OSMIndex``.

    @param idx: ``osm_footprints.OSMIndex``
    @param cell_box: inclusive ``x0, z0, x1, z1``
    @returns: ``[{ref, building, cells}]`` ordered by first column
    """
    from coverage import OUTBUILDINGS

    x0, z0, x1, z1 = cell_box
    whole: dict[int, list[tuple[int, int]]] = {}
    touching: set[int] = set()
    for q, i in idx.cells().items():
        if idx.buildings[i][1].get('building') in OUTBUILDINGS:
            whole.setdefault(i, []).append(q)
            if x0 <= q[0] <= x1 and z0 <= q[1] <= z1:
                touching.add(i)
    by_index = {i: whole[i] for i in touching}      # whole footprint: one that crosses the edge is refused, not halved
    out = []
    for i, cols in by_index.items():
        props = idx.buildings[i][1]
        out.append({'ref': str(props.get('osm_id') or props.get('osm_way_id') or i),
                    'building': props['building'], 'cells': sorted(cols)})
    return sorted(out, key=lambda r: r['cells'][0])


def lidar_rise(cell: tuple[int, int], margin: int = LIDAR_MARGIN) -> dict[str, Any] | None:
    """Raw nDSM (DSM - DTM, metres) around ``cell`` from the active lidar provider.

    Reads cached tiles only (never downloads). Returns ``None`` when tiles are missing.

    @returns: ``{'x0', 'z0', 'rise'}`` with ``rise[x - x0, z - z0]``; NaN where there is no data
    """
    import numpy as np
    from ground_sources import cell_box
    from lidar_provider import mc_heights, tile_client

    box = cell_box(cell, margin)
    try:
        h = mc_heights(tile_client(download=False), *box, geocraft=False)
    except (LookupError, FileNotFoundError, OSError):
        return None
    return {'x0': box[0], 'z0': box[1], 'rise': np.asarray(h['dsm'], float) - np.asarray(h['dtm'], float)}


def rise_at(rise: Mapping[str, Any] | None, x: int, z: int) -> float | None:
    """nDSM at one column, or ``None`` outside the grid or without data."""
    if not rise:
        return None
    i, j = x - int(rise['x0']), z - int(rise['z0'])
    grid = rise['rise']
    if not (0 <= i < grid.shape[0] and 0 <= j < grid.shape[1]):
        return None
    v = float(grid[i, j])
    return None if math.isnan(v) else v


def ndsm_height(rise: Mapping[str, Any] | None, cells: Iterable[tuple[int, int]]) -> int | None:
    """Blocks of height lidar reads over ``cells`` (75th percentile of the raise, rounded up)."""
    vals = sorted(v for q in cells if (v := rise_at(rise, *q)) is not None and v >= 1.0)
    if not vals:
        return None
    return int(math.ceil(vals[min(len(vals) - 1, int(0.75 * len(vals)))]))


def square_rectangle(cells: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Axis-aligned rectangle that squares up ``cells``: the bounding box, trimmed.

    An edge row or column covered by less than ``MIN_FILL`` of its length is dropped (repeatedly),
    so an L-shaped or ragged blob does not square out into a block much bigger than the evidence.
    """
    pts = {(int(x), int(z)) for x, z in cells}
    if not pts:
        return []
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    z0, z1 = min(p[1] for p in pts), max(p[1] for p in pts)
    while x1 > x0 or z1 > z0:
        def share(edge):
            kind, v = edge
            if kind == 'x':
                return sum((v, z) in pts for z in range(z0, z1 + 1)) / (z1 - z0 + 1)
            return sum((x, v) in pts for x in range(x0, x1 + 1)) / (x1 - x0 + 1)
        edges = [(('x', x0), 'x0'), (('x', x1), 'x1'), (('z', z0), 'z0'), (('z', z1), 'z1')]
        weak = [(share(e), name) for e, name in edges
                if (name[0] == 'x' and x1 > x0 or name[0] == 'z' and z1 > z0)]
        worst = min(weak) if weak else (1.0, '')
        if worst[0] >= MIN_FILL:
            break
        name = worst[1]
        x0, x1, z0, z1 = (x0 + (name == 'x0'), x1 - (name == 'x1'), z0 + (name == 'z0'), z1 - (name == 'z1'))
    return [(x, z) for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)]


def massing(kind: str, cells: Sequence[tuple[int, int]], ndsm: int | None) -> dict[str, Any]:
    """Wall height, roof form and total height for a rectangle of ``cells``.

    ``walls + 1`` (the roof layer) is the default total for the kind, clamped to the nDSM height
    when lidar gives one (never below one wall block). Garages with a short side of 3+ get a
    mono-pitch roof, everything else is flat. The door needs two wall blocks.

    @returns: ``{'walls', 'total', 'roof', 'size': [dx, dz]}``
    """
    xs, zs = [c[0] for c in cells], [c[1] for c in cells]
    dx, dz = max(xs) - min(xs) + 1, max(zs) - min(zs) + 1
    total = DEFAULT_TOTAL[kind]
    if ndsm is not None:
        total = min(total, max(2, int(ndsm)))
    return {'walls': total - 1, 'total': total,
            'roof': 'mono' if kind == 'garage' and min(dx, dz) >= 3 else 'flat', 'size': [dx, dz]}


def door_for(cells: Sequence[tuple[int, int]], targets: Iterable[tuple[int, int]], walls: int) -> dict[str, Any] | None:
    """Door in the middle of the rectangle side that faces the nearest of ``targets`` (house, route columns).

    A door needs a jamb either side along the wall (``site_kit.door_frame_issues``), so only a side
    three or more columns long can hold one; the next best facing side is used when the best is too short.

    @returns: ``{'cell': [x, z], 'facing': 'north'|...}`` on the perimeter, or ``None``
        when the walls are too low for a door, there is no target or no side is long enough
    """
    targets = list(targets)
    if walls < 2 or not targets:
        return None
    xs, zs = [c[0] for c in cells], [c[1] for c in cells]
    x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
    cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
    tx, tz = min(targets, key=lambda t: ((t[0] - cx) ** 2 + (t[1] - cz) ** 2, t))
    ax, az = tx - cx, tz - cz
    sides = [  # (facing score, door cell, columns along that wall)
        (ax, (x1, int(round(cz))), z1 - z0 + 1, (1, 0)), (-ax, (x0, int(round(cz))), z1 - z0 + 1, (-1, 0)),
        (az, (int(round(cx)), z1), x1 - x0 + 1, (0, 1)), (-az, (int(round(cx)), z0), x1 - x0 + 1, (0, -1))]
    for _score, cell, length, step in sorted(sides, key=lambda s: -s[0]):
        if length >= 3:
            return {'cell': list(cell), 'facing': FACINGS[step]}
    return None


def blob_green_share(group: Iterable[tuple[int, int]], rgb: Mapping[tuple[int, int], Any]) -> float | None:
    """Share of the blob's sampled aerial columns that read lawn or shade; ``None`` with no samples."""
    from ground_sources import aerial_class

    seen = [aerial_class(rgb[q]) for q in group if q in rgb]
    return sum(c in ('lawn', 'shade') for c in seen) / len(seen) if seen else None


def _item(kind: str, evidence: Sequence[tuple[int, int]], source: str, confidence: str, *,
          item_id: str, ndsm: int | None, targets, plot_owner, evidence_ids, note: str,
          unresolved_reason: str = '', root=None) -> dict[str, Any]:
    cells = square_rectangle(evidence)
    mass = massing(kind, cells, ndsm)
    owners = sorted({plot_owner[q] for q in cells if q in (plot_owner or {})})
    if not unresolved_reason and min(mass['size']) < 2:
        unresolved_reason = 'too small to build once squared to a rectangle'
    item: dict[str, Any] = {
        'id': item_id, 'kind': KIND, 'role': KIND, 'type': kind,
        'cells': [list(c) for c in cells], 'evidence_cells': [list(c) for c in sorted(evidence)],
        'walls': mass['walls'], 'height': mass['total'], 'roof': mass['roof'], 'size': mass['size'],
        'lidar_height': ndsm, 'plot_ids': owners,
        'door': door_for(cells, targets, mass['walls']),
    }
    if note:
        item['note'] = note
    return prov.attach_provenance(item, source=source, confidence=confidence, evidence_ids=evidence_ids,
                                  unresolved_reason=unresolved_reason, root=root)


def mapped_items(footprints: Sequence[Mapping[str, Any]], *, rise=None, targets=(), plot_owner=None,
                 evidence_ids=(), root=None) -> list[dict[str, Any]]:
    """High-confidence items for mapped outbuilding footprints (``mapped_outbuilding_footprints`` rows)."""
    out = []
    for fp in footprints:
        cells = [tuple(c) for c in fp['cells']]
        kind = mapped_type(fp['building'])
        out.append(_item(
            kind, cells, 'vector_map', 'high', item_id=_item_id('map', fp['ref']),
            ndsm=ndsm_height(rise, cells), targets=targets, plot_owner=plot_owner,
            evidence_ids=evidence_ids, note=f"mapped building={fp['building']}", root=root))
    return out


def lidar_items(rise: Mapping[str, Any] | None, *, exclude: set[tuple[int, int]],
                canopy: set[tuple[int, int]], rgb: Mapping[tuple[int, int], Any],
                cell_box: tuple[int, int, int, int], targets=(), plot_owner=None,
                evidence_ids=(), root=None) -> list[dict[str, Any]]:
    """Medium-confidence items for raw nDSM blobs; blobs that fail a test come back unresolved.

    A blob is 1.6-4.5 m above bare ground on at least ``MIN_BLOB_COLUMNS`` connected columns, off
    ``exclude`` (building footprints dilated by one column, labelled lidar objects)
    and off ``canopy``. A blob that reads green on the aerial (lawn + shade share at least
    ``GREEN_SHARE_MAX``), is a thin line, or is too large is listed with the reason rather than built.
    Only blobs with a column inside ``cell_box`` count here.

    @param rise: ``lidar_rise`` result; ``None`` gives no items
    @param exclude: columns that cannot be part of a blob
    @param canopy: lidar tree-canopy columns
    @param rgb: aerial colours ``{(x, z): rgb}``
    @param cell_box: inclusive ``x0, z0, x1, z1`` of the cell being planned
    """
    if not rise:
        return []
    import numpy as np

    grid = rise['rise']
    band = (grid >= RISE_MIN) & (grid <= RISE_MAX)
    columns = {(int(rise['x0']) + int(i), int(rise['z0']) + int(j)) for i, j in zip(*np.nonzero(band))}
    columns -= exclude | canopy
    x0, z0, x1, z1 = cell_box
    out = []
    for group in sorted(_components(columns), key=min):
        if len(group) < MIN_BLOB_COLUMNS:
            continue
        if not any(x0 <= x <= x1 and z0 <= z <= z1 for x, z in group):
            continue
        xs, zs = [q[0] for q in group], [q[1] for q in group]
        dx, dz = max(xs) - min(xs) + 1, max(zs) - min(zs) + 1
        share = blob_green_share(group, rgb)
        reason = ''
        if share is None:
            reason = 'no aerial colour under the lidar blob: cannot rule out vegetation'
        elif share >= GREEN_SHARE_MAX:
            reason = f'lidar blob reads green on the aerial ({share:.0%} lawn/shade): vegetation, not an outbuilding'
        elif min(dx, dz) < MIN_SHORT_SIDE:
            reason = 'lidar blob is a 1-wide raised line (fence or wall), not an outbuilding'
        elif len(group) > MAX_BLOB_COLUMNS:
            reason = f'lidar blob too large for a garden outbuilding ({len(group)} columns)'
        cells = square_rectangle(group)
        kind = 'garage' if len(cells) >= GARAGE_MIN_COLUMNS else 'shed'
        lo = min(group)
        out.append(_item(
            kind, group, 'lidar', 'medium', item_id=_item_id('lidar', lo[0], lo[1]),
            ndsm=ndsm_height(rise, group), targets=targets, plot_owner=plot_owner,
            evidence_ids=evidence_ids, root=root, unresolved_reason=reason,
            note=f'nDSM blob {len(group)} columns, aerial green share ' + ('n/a' if share is None else f'{share:.2f}')))
    return out


def designer_items(rows: Sequence[Mapping[str, Any]], *, rise=None, targets=(), plot_owner=None,
                   default_evidence=(), root=None) -> list[dict[str, Any]]:
    """Medium-confidence items for designer traces (``kind`` shed / garage with ``cells``).

    A trace needs its own evidence id (``evidence_ids`` on the row, else the entry's); without one
    it is listed unresolved. The designer's cells are the evidence; the same squaring applies.
    """
    out = []
    for raw in rows:
        kind = raw.get('kind')
        cells = [(int(c[0]), int(c[1])) for c in raw.get('cells') or []]
        if kind not in DESIGNER_KINDS or not cells:
            continue
        ids = list(raw.get('evidence_ids') or default_evidence or ())
        lo = min(cells)
        source = 'photo_read' if 'photo' in str(raw.get('evidence') or '').lower() else 'aerial'
        out.append(_item(
            kind, cells, source, 'medium', item_id=_item_id('designer', lo[0], lo[1]),
            ndsm=ndsm_height(rise, cells), targets=targets, plot_owner=plot_owner,
            evidence_ids=ids, root=root, note=str(raw.get('note') or 'designer trace'),
            unresolved_reason='' if ids else 'designer outbuilding trace names no evidence id'))
    return out


def merge_sources(*groups: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Concatenate item groups in evidence order; a later item sharing a column with an earlier
    resolved one is dropped (mapped beats lidar beats designer, so one outbuilding is one item)."""
    taken: set[tuple[int, int]] = set()
    out: list[dict[str, Any]] = []
    for group in groups:
        fresh_taken: set[tuple[int, int]] = set()
        for item in group:
            cols = {tuple(c) for c in item['evidence_cells']} | {tuple(c) for c in item['cells']}
            if cols & taken:
                continue
            out.append(item)
            if not prov.is_unresolved(item.get(prov.PROVENANCE_KEY)):
                fresh_taken |= cols
        taken |= fresh_taken
    return out


def voxels(item: Mapping[str, Any], ground: Mapping[tuple[int, int], int]) -> dict[tuple[int, int, int], str]:
    """Blocks of one outbuilding, from its plan item and the ground Y of each of its columns.

    Walls stand on the perimeter from the column's own ground up to the shared top (ground of the
    highest column + walls), a roof layer covers the footprint at the top. The door is two blocks
    in the perimeter on the facing side. The interior is left empty (no private interiors).
    Roof slabs and stairs are bottom-half only, so none floats as a top slab over air.

    @param item: ``outbuilding`` plan item with ``cells``, ``type``, ``walls``, ``roof``, ``door``
    @param ground: ``{(x, z): Y of the top ground block}`` for every cell of the item
    @returns: ``{(x, y, z): block}``
    """
    cells = [tuple(c) for c in item['cells']]
    kind, walls = item['type'], int(item['walls'])
    xs, zs = [c[0] for c in cells], [c[1] for c in cells]
    x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
    base = max(ground[c] for c in cells)
    roof_y = base + walls + 1
    door = tuple(item['door']['cell']) if item.get('door') else None
    out: dict[tuple[int, int, int], str] = {}
    for x, z in cells:
        edge = x in (x0, x1) or z in (z0, z1)
        if edge:
            for y in range(ground[(x, z)] + 1, base + walls + 1):
                out[(x, y, z)] = WALL_BLOCK[kind]
        if item['roof'] == 'mono':
            face = (item.get('door') or {}).get('facing', 'south')
            out[(x, roof_y, z)] = f'{ROOF_STAIR[kind]}[facing={face},half=bottom]'
        else:
            out[(x, roof_y, z)] = f'{ROOF_SLAB[kind]}[type=bottom]'
    if door is not None and door in set(cells):
        y = ground[door] + 1
        facing = item['door']['facing']
        out[(door[0], y, door[1])] = f'{DOOR_BLOCK[kind]}[facing={facing},half=lower,hinge=left,open=false]'
        out[(door[0], y + 1, door[1])] = f'{DOOR_BLOCK[kind]}[facing={facing},half=upper,hinge=left,open=false]'
    return out
