#!/usr/bin/env python3
"""Decor plan read from aerial, photos, map and designer (#150).

Each barrier or furniture item carries shared ``provenance``
(``lib/site/provenance.py``). Items that cannot be placed are kept on the plan
with a non-empty ``unresolved_reason`` — never silently dropped. The decor
layer builds only resolved rows.

Sources (cheapest first; later still listed when they add something new):

  * vector map barriers and furniture points
  * garden plot barriers (convention / aerial hedge)
  * aerial dark-green rings immediately outside building footprints
  * aerial dark-green thin strips standing free of buildings (lidar-confirmed when given)
  * designer ``reference/decor-furniture.json`` (and optional observations)
  * photo observations that name a kind but no coordinate → unresolved
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import provenance as prov

HEDGE_BLOCK = 'oak_leaves[persistent=true]'
DEFAULT_HEIGHT = {
    'hedge': 2,
    'brick_wall': 1,
    'iron_bars': 1,
    'spruce_fence': 2,
}
DEFAULT_BLOCK = {
    'hedge': HEDGE_BLOCK,
    'brick_wall': 'brick_wall',
    'iron_bars': 'iron_bars',
    'spruce_fence': 'spruce_fence',
}
BARRIER_KINDS = frozenset(DEFAULT_BLOCK)
CARDINAL = ((1, 0), (-1, 0), (0, 1), (0, -1))
MIN_BORDER_CELLS = 4
MAX_FREE_HEDGE_WIDTH = 3
HEDGE_RAISE = (1, 3)
MIN_RAISED_SHARE = 0.5


def _item_id(prefix: str, *parts: Any) -> str:
    body = '-'.join(str(p).replace(',', 'm') for p in parts)
    return f'{prefix}-{body}'


def aerial_building_border_hedges(
    footprint: set[tuple[int, int]],
    rgb: Mapping[tuple[int, int], Any],
    canopy: set[tuple[int, int]] | None = None,
    *,
    min_cells: int = MIN_BORDER_CELLS,
    evidence_ids: Sequence[str] = (),
    root=None,
) -> list[dict[str, Any]]:
    """Hedge plan items from dark-green aerial columns immediately outside footprints.

    Tree-canopy columns are ignored. Components shorter than ``min_cells`` are
    still returned, marked unresolved, so the gap is listed rather than skipped.

    @param footprint: building footprint columns
    @param rgb: aerial colour samples ``{(x, z): rgb}``
    @param canopy: lidar/nDSM tree-canopy columns to ignore
    @param min_cells: minimum connected columns for a resolved hedge strip
    @param evidence_ids: evidence ids attached to each item's provenance
    @param root: world root for evidence id checks; ``None`` skips the check
    @returns: hedge barrier items (resolved and unresolved)
    """
    from ground_sources import aerial_class
    from garden_sources import components

    skip = canopy or set()
    shade: set[tuple[int, int]] = set()
    for x, z in footprint:
        for dx, dz in CARDINAL:
            q = (x + dx, z + dz)
            if q in footprint or q in skip or q not in rgb:
                continue
            if aerial_class(rgb[q]) == 'shade':
                shade.add(q)
    if not shade:
        return []

    out: list[dict[str, Any]] = []
    for i, group in enumerate(sorted(components(shade), key=lambda g: (min(g), len(g)))):
        cells = sorted(group)
        item: dict[str, Any] = {
            'id': _item_id('hedge-border', i, cells[0][0], cells[0][1]),
            'kind': 'hedge',
            'role': 'barrier',
            'cells': [list(c) for c in cells],
            'block': HEDGE_BLOCK,
            'height': DEFAULT_HEIGHT['hedge'],
            'allow_against_build': True,
            'note': 'aerial dark-green strip against building footprint',
        }
        reason = ''
        if len(cells) < min_cells:
            reason = f'too short for a hedge strip ({len(cells)} < {min_cells} columns)'
        prov.attach_provenance(
            item,
            source='aerial',
            confidence='low' if reason else 'medium',
            evidence_ids=evidence_ids,
            unresolved_reason=reason,
            root=root,
        )
        out.append(item)
    return out


def _lidar_raises(hm: Mapping[str, Any], cells: Iterable[tuple[int, int]]) -> list[int]:
    """DSM minus DTM (blocks) for each of ``cells`` inside the grid; tree and roof columns are skipped."""
    from lidar_heightmap import TREE, TREE_OVER_ROOF, ROOF

    out: list[int] = []
    for x, z in cells:
        i, j = x - int(hm['x0']), z - int(hm['z0'])
        if 0 <= i < hm['h'].shape[0] and 0 <= j < hm['h'].shape[1]:
            if hm['kind'][i, j] in (TREE, TREE_OVER_ROOF, ROOF):
                continue
            out.append(int(hm['h'][i, j] - hm['terrain'][i, j]))
    return out


def _raised_fraction(hm: Mapping[str, Any], cells: Iterable[tuple[int, int]]) -> float:
    """Share of ``cells`` whose DSM stands 1-3 blocks above the DTM (tree and roof columns never count)."""
    cells = list(cells)
    lo, hi = HEDGE_RAISE
    return sum(lo <= r <= hi for r in _lidar_raises(hm, cells)) / len(cells)


def lidar_barrier_height(hm: Mapping[str, Any] | None, cells: Iterable[tuple[int, int]]) -> int | None:
    """Barrier height hinted by lidar, or ``None`` when lidar gives no clear reading.

    At least ``MIN_RAISED_SHARE`` of the columns must stand 1-3 blocks above bare terrain
    (tree and roof columns excluded); the hint is the median of those raises. Lidar only
    sizes a barrier something else already named; it never makes one.
    """
    cells = list(cells)
    if not hm or 'terrain' not in hm or not cells:
        return None
    lo, hi = HEDGE_RAISE
    raises = sorted(r for r in _lidar_raises(hm, cells) if lo <= r <= hi)
    if len(raises) / len(cells) < MIN_RAISED_SHARE:
        return None
    return raises[len(raises) // 2]


def aerial_free_hedges(
    footprint: set[tuple[int, int]],
    rgb: Mapping[tuple[int, int], Any],
    canopy: set[tuple[int, int]] | None = None,
    taken: set[tuple[int, int]] | None = None,
    *,
    lidar_hm: Mapping[str, Any] | None = None,
    min_cells: int = MIN_BORDER_CELLS,
    max_width: int = MAX_FREE_HEDGE_WIDTH,
    evidence_ids: Sequence[str] = (),
    root=None,
) -> list[dict[str, Any]]:
    """Hedge items from thin dark-green aerial strips standing free of buildings (I-035).

    Shade columns outside footprints, canopy and ``taken`` barrier columns are
    grouped; only elongated groups (at least ``min_cells`` long, mean width at
    most ``max_width``) count, so a shaded lawn patch is not a hedge. When a
    lidar heightmap is given, at least ``MIN_RAISED_SHARE`` of the strip must stand 1-3 blocks
    above bare terrain (medium confidence); without lidar the item is low.

    @param footprint: building footprint columns
    @param rgb: aerial colour samples ``{(x, z): rgb}``
    @param canopy: tree-canopy columns to ignore
    @param taken: columns already planned as barriers
    @param lidar_hm: optional DSM/DTM heightmap confirming the strip is raised
    @param evidence_ids: evidence ids attached to each item's provenance
    @param root: world root for evidence id checks
    @returns: resolved hedge barrier items
    """
    from ground_sources import aerial_class
    from garden_sources import components

    skip = (canopy or set()) | (taken or set()) | footprint
    shade = {q for q, c in rgb.items() if q not in skip and aerial_class(c) == 'shade'}
    use_lidar = bool(lidar_hm) and 'terrain' in lidar_hm
    out: list[dict[str, Any]] = []
    for group in sorted(components(shade), key=min):
        xs, zs = [q[0] for q in group], [q[1] for q in group]
        length = max(max(xs) - min(xs), max(zs) - min(zs)) + 1
        if length < min_cells or len(group) / length > max_width:
            continue
        if use_lidar and _raised_fraction(lidar_hm, group) < MIN_RAISED_SHARE:
            continue
        cells = sorted(group)
        out.append(_barrier_item(
            'hedge', cells,
            source='aerial', confidence='medium' if use_lidar else 'low',
            evidence_ids=evidence_ids, root=root,
            note='aerial dark-green strip away from buildings',
            item_id=_item_id('hedge-free', cells[0][0], cells[0][1]),
        ))
    return out


def _barrier_item(
    kind: str,
    cells: Iterable[tuple[int, int]],
    *,
    source: str,
    confidence: str,
    evidence_ids: Sequence[str] = (),
    allow_against_build: bool = False,
    note: str = '',
    block: str | None = None,
    height: int | None = None,
    item_id: str = '',
    root=None,
) -> dict[str, Any]:
    ordered = sorted({(int(x), int(z)) for x, z in cells})
    item: dict[str, Any] = {
        'id': item_id or _item_id(kind, ordered[0][0], ordered[0][1]) if ordered else _item_id(kind, 'empty'),
        'kind': kind,
        'role': 'barrier',
        'cells': [list(c) for c in ordered],
        'block': block or DEFAULT_BLOCK[kind],
        'height': int(height if height is not None else DEFAULT_HEIGHT[kind]),
        'allow_against_build': bool(allow_against_build),
    }
    if note:
        item['note'] = note
    prov.attach_provenance(
        item,
        source=source,
        confidence=confidence,
        evidence_ids=evidence_ids,
        root=root,
    )
    return item


def _furniture_item(
    x: int,
    z: int,
    kind: str,
    *,
    source: str,
    confidence: str,
    evidence_ids: Sequence[str] = (),
    note: str = '',
    root=None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        'id': _item_id(kind, x, z),
        'kind': kind,
        'role': 'furniture',
        'cell': [int(x), int(z)],
    }
    if note:
        item['note'] = note
    prov.attach_provenance(
        item,
        source=source,
        confidence=confidence,
        evidence_ids=evidence_ids,
        root=root,
    )
    return item


def _designer_source(item: Mapping[str, Any]) -> tuple[str, str]:
    """Map a designer row to (provenance source, confidence)."""
    blob = f"{item.get('evidence') or ''} {item.get('note') or ''}".lower()
    if 'photo' in blob or 'streetview' in blob or 'street view' in blob:
        return 'photo_read', 'medium'
    if 'aerial' in blob or item.get('source') == 'designer':
        return 'aerial', 'medium'
    return 'inference', 'low'


def _footprint_columns(cell: tuple[int, int]) -> set[tuple[int, int]]:
    """Building footprint columns that touch the cell (live OSM index)."""
    from ground_sources import cell_box
    from osm_footprints import OSMIndex

    x0, z0, x1, z1 = cell_box(cell)
    return {
        q for q in OSMIndex().cells()
        if x0 <= q[0] <= x1 and z0 <= q[1] <= z1
    }


def _lidar_components(hm, footprint, canopy) -> list[list[tuple[int, int]]]:
    """Connected DSM-above-DTM columns outside footprints, canopy, trees and roofs."""
    from garden_sources import components
    from lidar_heightmap import TREE, TREE_OVER_ROOF, ROOF

    if not hm or 'terrain' not in hm:
        return []
    columns = set()
    for i in range(hm['h'].shape[0]):
        for j in range(hm['h'].shape[1]):
            q = (int(hm['x0']) + i, int(hm['z0']) + j)
            if q in footprint or q in canopy or hm['kind'][i, j] in (TREE, TREE_OVER_ROOF, ROOF):
                continue
            if hm['terrain'][i, j] > 0 and hm['h'][i, j] > hm['terrain'][i, j]:
                columns.add(q)
    return sorted(components(columns), key=min)


def lidar_objects(hm, footprint, canopy, observations, *, root=None):
    """Plan items for lidar objects that a photo or aerial label names; lone lumps are ignored.

    Lidar is a guide, never a trigger: a DSM-above-DTM group with no label on it adds
    nothing, not even an unresolved row. Labels use the existing observation schema:
    ``kind``, ``cell: [x,z]``, optional ``source`` (photo_read or aerial), evidence_ids and
    note. A label must lie on the object. Conflicting, unknown and transient labels never
    authorize placement (unresolved), and a labelled object vetoes map/designer points on it.
    Returns plan items plus the columns of the labelled objects.
    """
    import street_furniture as sf

    out = []
    columns: set[tuple[int, int]] = set()
    for group in _lidar_components(hm, footprint, canopy):
        group = set(group)
        labels = [o for o in observations if o.get('cell') and tuple(o['cell']) in group]
        if not labels:
            continue
        columns |= group
        valid = [o for o in labels if o.get('kind') in sf.RECIPES
                 and o.get('source', 'photo_read') in ('photo_read', 'aerial')
                 and not o.get('unresolved_reason')]
        # Any incompatible label blocks the whole component, including a vehicle
        # touching an otherwise plausible furniture observation.
        kinds = {o.get('kind') for o in labels}
        resolved = bool(valid) and len(kinds) == 1 and len(valid) == len(labels)
        label = valid[0] if resolved else {}
        x, z = tuple(label['cell']) if resolved else min(group)
        kind = str(label.get('kind') or 'unknown')
        item = _furniture_item(
            x, z, kind, source=label.get('source', 'photo_read') if resolved else 'inference',
            confidence='medium' if resolved else 'low',
            evidence_ids=label.get('evidence_ids') or (), root=root,
            note=str(label.get('note') or 'lidar surface above bare terrain'),
        )
        item['id'] = _item_id('lidar-object', *min(group))
        item['lidar_cells'] = [list(q) for q in sorted(group)]
        item['lidar_height'] = max(int(hm['h'][a - hm['x0'], b - hm['z0']]
                                       - hm['terrain'][a - hm['x0'], b - hm['z0']])
                                   for a, b in group)
        if not resolved:
            item['provenance']['unresolved_reason'] = (
                'lidar object needs an unambiguous photo/aerial permanent furniture label')
        out.append(item)
    return out, columns


def _lidar_hints(items, hm, footprint, canopy) -> None:
    """Add lidar size hints, in place, to items something else already named.

    Barriers take a height from :func:`lidar_barrier_height`; furniture standing on a lidar
    object carries ``lidar_height`` and ``lidar_cells`` for the placer.
    """
    if not hm or 'terrain' not in hm:
        return
    by_cell = {q: g for g in _lidar_components(hm, footprint, canopy) for q in g}
    for item in items:
        if item.get('role') == 'barrier':
            hint = lidar_barrier_height(hm, (tuple(c) for c in item.get('cells') or []))
            if hint is not None:
                item['height'] = hint
                item['height_source'] = 'lidar'
        elif item.get('role') == 'furniture' and item.get('cell') and 'lidar_cells' not in item:
            group = by_cell.get(tuple(item['cell']))
            if group:
                item['lidar_cells'] = [list(q) for q in sorted(group)]
                item['lidar_height'] = max(
                    int(hm['h'][a - hm['x0'], b - hm['z0']] - hm['terrain'][a - hm['x0'], b - hm['z0']])
                    for a, b in group)


def build_decor_plan(
    cell: tuple[int, int],
    *,
    map_barriers: Mapping[str, set[tuple[int, int]]] | None = None,
    map_furniture: Sequence[tuple[int, int, str]] = (),
    designer_items: Sequence[Mapping[str, Any]] = (),
    photo_observations: Sequence[Mapping[str, Any]] = (),
    lidar_hm: Mapping[str, Any] | None = None,
    garden: Mapping[tuple[int, int], tuple[str, str, int]] | None = None,
    rgb: Mapping[tuple[int, int], Any] | None = None,
    canopy: set[tuple[int, int]] | None = None,
    footprint: set[tuple[int, int]] | None = None,
    free_hedges: bool = False,
    evidence_ids: Sequence[str] = (),
    root=None,
) -> dict[str, Any]:
    """Build a decor plan for one cell from map, garden, aerial and photos.

    @param cell: ``(tx, tz)``
    @param map_barriers: optional ``{'hedge'|'wall'|'fence': set[(x,z)]}``; fetched when omitted
    @param map_furniture: ``(x, z, kind)`` triples from the vector map
    @param designer_items: rows from ``decor-furniture.json`` (may include ``evidence``)
    @param photo_observations: photo/aerial kind/note rows; cell coordinates label lidar objects
    @param lidar_hm: DSM/DTM heightmap; omitted means no lidar candidates
    @param garden: optional garden barrier map; ``garden_barriers(cell)`` when omitted
    @param rgb: optional aerial colours; fetched when omitted
    @param canopy: optional tree-canopy columns
    @param footprint: optional building columns; OSM index when omitted
    @param free_hedges: also plan free-standing aerial hedges (off: on 48,-45 it found only
        carriageway shadow and missed the hedges OSM maps)
    @param evidence_ids: default evidence ids for aerial-derived rows
    @param root: world root for provenance evidence checks
    @returns: ``{'cell': [tx, tz], 'items': [...]}``
    """
    from ground_sources import OSMGround, aerial_rgb
    import garden_sources as gs
    import street_furniture as sf

    items: list[dict[str, Any]] = []
    seen_barrier: set[tuple[int, int]] = set()
    seen_furniture: set[tuple[int, int]] = set()

    vmap = None
    if map_barriers is None or garden is None:
        vmap = OSMGround(cell)
    if map_barriers is None:
        assert vmap is not None
        map_barriers = {'hedge': set(vmap.hedge), 'wall': set(vmap.wall), 'fence': set(vmap.fence)}
    if not map_furniture and vmap is not None:
        map_furniture = list(vmap.furniture)
    if garden is None:
        garden = gs.garden_barriers(cell, rgb=rgb)
    if rgb is None:
        rgb = aerial_rgb(cell)
    if canopy is None:
        canopy = gs.tree_canopy_columns(cell)
    if footprint is None:
        footprint = _footprint_columns(cell)

    kind_map = {'hedge': 'hedge', 'wall': 'brick_wall', 'fence': 'iron_bars'}
    for key, kind in kind_map.items():
        cols = map_barriers.get(key) or set()
        if not cols:
            continue
        for group in gs.components(cols):
            for q in group:
                seen_barrier.add(q)
            items.append(_barrier_item(
                kind, group,
                source='vector_map', confidence='high',
                evidence_ids=evidence_ids, root=root,
                item_id=_item_id(f'map-{kind}', min(group)[0], min(group)[1]),
            ))

    by_garden: dict[tuple[str, str, int], list[tuple[int, int]]] = {}
    for q, (typ, blk, h) in garden.items():
        if q in seen_barrier:
            continue
        by_garden.setdefault((typ, blk, int(h)), []).append(q)
    for (typ, blk, h), cols in sorted(by_garden.items()):
        kind = typ if typ in BARRIER_KINDS else 'hedge'
        # Garden aerial hedges already went through garden_sources; treat as aerial.
        src = 'aerial' if kind == 'hedge' and blk == HEDGE_BLOCK else 'register_entry'
        for group in gs.components(cols):
            for q in group:
                seen_barrier.add(q)
            items.append(_barrier_item(
                kind, group,
                source=src, confidence='medium',
                evidence_ids=evidence_ids, root=root,
                block=blk, height=h,
                item_id=_item_id(f'garden-{kind}', min(group)[0], min(group)[1]),
            ))

    border_items = aerial_building_border_hedges(
        footprint, rgb, canopy,
        evidence_ids=evidence_ids, root=root,
    )
    for item in border_items:
        cells = [tuple(c) for c in item.get('cells') or []]
        fresh = [c for c in cells if c not in seen_barrier]
        if not fresh and not prov.is_unresolved(item.get('provenance')):
            continue
        if fresh and len(fresh) != len(cells):
            item['cells'] = [list(c) for c in sorted(fresh)]
            if len(fresh) < MIN_BORDER_CELLS and not prov.is_unresolved(item.get('provenance')):
                item['provenance'] = prov.provenance_record(
                    source='aerial',
                    confidence='low',
                    evidence_ids=evidence_ids,
                    unresolved_reason=(
                        f'too short for a hedge strip after map overlap '
                        f'({len(fresh)} < {MIN_BORDER_CELLS} columns)'
                    ),
                    root=root,
                )
        for c in fresh:
            seen_barrier.add(c)
        items.append(item)

    for x, z, kind in map_furniture:
        if kind not in sf.RECIPES or (x, z) in seen_furniture:
            continue
        seen_furniture.add((x, z))
        items.append(_furniture_item(
            x, z, kind,
            source='vector_map', confidence='high',
            evidence_ids=evidence_ids, root=root,
        ))

    for raw in designer_items:
        kind = raw.get('kind')
        if kind not in sf.RECIPES:
            # Designer may propose barrier strips with cells.
            if kind in BARRIER_KINDS and raw.get('cells'):
                cells = [(int(c[0]), int(c[1])) for c in raw['cells']]
                fresh = [c for c in cells if c not in seen_barrier]
                if not fresh:
                    continue
                for c in fresh:
                    seen_barrier.add(c)
                src, conf = _designer_source(raw)
                items.append(_barrier_item(
                    kind, fresh,
                    source=src, confidence=conf,
                    evidence_ids=evidence_ids, root=root,
                    allow_against_build=bool(raw.get('allow_against_build', kind == 'hedge')),
                    note=str(raw.get('note') or ''),
                    item_id=_item_id(f'designer-{kind}', fresh[0][0], fresh[0][1]),
                ))
            continue
        try:
            x, z = int(raw['x']), int(raw['z'])
        except (KeyError, TypeError, ValueError):
            items.append(prov.attach_provenance(
                {
                    'id': _item_id('designer', kind, 'missing-xy'),
                    'kind': kind,
                    'role': 'furniture',
                    'note': str(raw.get('note') or ''),
                },
                source='aerial',
                confidence='low',
                evidence_ids=evidence_ids,
                unresolved_reason='designer furniture row has no x,z',
                root=root,
            ))
            continue
        if (x, z) in seen_furniture:
            continue
        seen_furniture.add((x, z))
        src, conf = _designer_source(raw)
        items.append(_furniture_item(
            x, z, kind,
            source=src, confidence=conf,
            evidence_ids=evidence_ids, root=root,
            note=str(raw.get('note') or ''),
        ))

    # Last of the barrier sources: designer and map rows outrank aerial inference.
    for item in (aerial_free_hedges(
        footprint, rgb, canopy, seen_barrier, lidar_hm=lidar_hm,
        evidence_ids=evidence_ids, root=root,
    ) if free_hedges else ()):
        seen_barrier.update(tuple(c) for c in item['cells'])
        items.append(item)

    object_items, object_columns = lidar_objects(
        lidar_hm, footprint, canopy, photo_observations, root=root)
    # A lidar component supersedes generic map/designer points: a map bench
    # must not resurrect a component identified as a parked vehicle.
    items = [item for item in items
             if not (item.get('role') == 'furniture'
                     and tuple(item.get('cell') or ()) in object_columns)]
    _lidar_hints(items, lidar_hm, footprint, canopy)
    # A raised column already planned as a barrier is that barrier, not an unknown object.
    items.extend(o for o in object_items
                 if not all(tuple(c) in seen_barrier for c in o['lidar_cells']))
    for i, obs in enumerate(photo_observations):
        if obs.get('cell') and tuple(obs['cell']) in object_columns:
            continue
        kind = str(obs.get('kind') or 'unknown')
        reason = str(obs.get('unresolved_reason') or '').strip() or (
            'photo observation has no MC coordinate'
        )
        item = {
            'id': _item_id('photo', kind, i),
            'kind': kind,
            'role': 'furniture' if kind in sf.RECIPES else 'barrier',
            'note': str(obs.get('note') or ''),
        }
        if obs.get('cell') is not None:
            item['cell'] = [int(obs['cell'][0]), int(obs['cell'][1])]
        if obs.get('cells'):
            item['cells'] = [[int(c[0]), int(c[1])] for c in obs['cells']]
        src = 'photo_read'
        # Resolved photo rows (have coords) keep empty unresolved_reason.
        if item.get('cell') or item.get('cells'):
            reason = str(obs.get('unresolved_reason') or '').strip()
        prov.attach_provenance(
            item,
            source=src,
            confidence='low' if reason else 'medium',
            evidence_ids=list(obs.get('evidence_ids') or evidence_ids),
            unresolved_reason=reason,
            root=root,
        )
        items.append(item)

    return {'cell': [int(cell[0]), int(cell[1])], 'items': items}


def unresolved_items(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Plan items whose provenance names a non-empty unresolved reason.

    @param plan: decor plan from :func:`build_decor_plan`
    @returns: unresolved items in plan order
    """
    return [i for i in plan.get('items') or [] if prov.is_unresolved(i.get(prov.PROVENANCE_KEY))]


def resolved_barriers(
    plan: Mapping[str, Any],
) -> dict[tuple[int, int], tuple[str, str, int]]:
    """Resolved barrier columns → ``(kind, block, height)`` for the decor layer.

    @param plan: decor plan
    @returns: column map ready for placement
    """
    out: dict[tuple[int, int], tuple[str, str, int]] = {}
    for item in plan.get('items') or []:
        if item.get('role') != 'barrier' or prov.is_unresolved(item.get(prov.PROVENANCE_KEY)):
            continue
        kind = item.get('kind')
        if kind not in BARRIER_KINDS:
            continue
        block = item.get('block') or DEFAULT_BLOCK[kind]
        height = int(item.get('height') or DEFAULT_HEIGHT[kind])
        for c in item.get('cells') or []:
            out[(int(c[0]), int(c[1]))] = (kind, block, height)
    return out


def resolved_furniture(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Resolved furniture rows as ``{x,z,kind,source,...}`` for :func:`place_furniture`.

    @param plan: decor plan
    @returns: furniture item dicts
    """
    out: list[dict[str, Any]] = []
    for item in plan.get('items') or []:
        if item.get('role') != 'furniture' or prov.is_unresolved(item.get(prov.PROVENANCE_KEY)):
            continue
        cell = item.get('cell')
        if not cell:
            continue
        row = {
            'x': int(cell[0]),
            'z': int(cell[1]),
            'kind': item['kind'],
            'source': (item.get(prov.PROVENANCE_KEY) or {}).get('source', 'inference'),
            'plan_id': item.get('id'),
            'allow_against_build': bool(item.get('allow_against_build')),
        }
        if item.get('note'):
            row['note'] = item['note']
        out.append(row)
    return out


def mark_unplaced(item: Mapping[str, Any], reason: str) -> dict[str, Any]:
    """Copy a plan item with ``unresolved_reason`` set after a failed place attempt.

    Accepts full plan items or thin furniture rows (``x``/``z``/``kind``/``source``).

    @param item: original plan item or furniture row
    @param reason: why placement failed
    @returns: new item dict with updated provenance
    """
    out = dict(item)
    prev = dict(item.get(prov.PROVENANCE_KEY) or {})
    source = prev.get('source') or item.get('source') or 'inference'
    # Furniture rows use legacy source labels (map/designer); map them onto SOURCES.
    if source == 'map':
        source = 'vector_map'
    elif source == 'designer':
        source = 'aerial'
    if source not in prov.SOURCES:
        source = 'inference'
    out[prov.PROVENANCE_KEY] = prov.provenance_record(
        source=source,
        confidence=prev.get('confidence') or 'low',
        evidence_ids=prev.get('evidence_ids') or (),
        unresolved_reason=reason,
    )
    return out
