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

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import cellmath
import outbuildings as ob
import provenance as prov
from tree_species import DEFAULT_LEAVES

HEDGE_BLOCK = DEFAULT_LEAVES
DEFAULT_HEIGHT = {
    'hedge': 2,
    'brick_wall': 1,
    'iron_bars': 1,
    'spruce_fence': 2,
    'plank_fence': 1,
}
DEFAULT_BLOCK = {
    'hedge': HEDGE_BLOCK,
    'brick_wall': 'brick_wall',
    'iron_bars': 'iron_bars',
    'spruce_fence': 'spruce_fence',
    'plank_fence': 'oak_planks',
}
BARRIER_KINDS = frozenset(DEFAULT_BLOCK)
# Close-boarded timber panels: oak is the default tone; the others only where evidence shows a
# distinct tone (docs/plans/244-boundary-evidence-findings.md). Block names are from memory and
# not yet confirmed in-world.
PLANK_TONES = {
    'oak': ('oak_planks',),
    'dark': ('spruce_planks', 'dark_oak_planks'),
    'reddy': ('jungle_planks', 'acacia_planks', 'mangrove_planks'),
    'pale': ('birch_planks',),
    'white': ('pale_oak_planks',),   # painted / white-looking fences; near white, lighter than birch
}
PLANK_BLOCKS = frozenset(b for blocks in PLANK_TONES.values() for b in blocks)

FACINGS = ('north', 'south', 'east', 'west')   # Minecraft: north is -z, south +z, east +x, west -x


def shelf_block(plank: str, facing: str | None) -> str:
    """Wooden shelf block state for a plank-style panel column, or the solid plank when no out is known.

    A shelf has a ``facing`` (its open front looks that way) and does not fill the square, so a panel
    run stops reading as a row of crates. Geometry (``template_shelf_body`` in the client assets, a
    north-facing shelf; the other facings are rotations): a solid back board 3/16 thick, flush with
    the block edge OPPOSITE the facing, plus a 2/16 lip at the top and bottom, 4/16 and 16/16 -- the
    whole thing is 5/16 deep and the open compartments face the way it points. So ``facing=<out>`` puts
    the board on the INNER (garden) edge of the boundary column and leaves 11/16 of the column empty
    on the outward side. Facing inward would hide the board's back to the street and put the open
    side in the garden. The shelf wood follows the plank tone (``oak_planks`` ->
    ``oak_shelf``, ``pale_oak_planks`` -> ``pale_oak_shelf``). With no outward direction on the item
    there is nothing to face, so the solid plank stays (labelled ``solid plank`` in the placement report).

    @param plank: plank block id from the tone table (``minecraft:`` prefix allowed)
    @param facing: ``north`` / ``south`` / ``east`` / ``west`` or None
    @returns: block state string, e.g. ``oak_shelf[facing=north]``
    """
    name = plank.removeprefix('minecraft:')
    if facing not in FACINGS or not name.endswith('_planks'):
        return name
    return f"{name.removesuffix('_planks')}_shelf[facing={facing}]"


def barrier_kind(type_: str, block: str | None = None) -> str:
    """Barrier kind for a convention ``type``: ``fence`` with a plank block is a plank panel run.

    ``fence`` with no block or a fence-post block stays ``spruce_fence``. Other types pass through
    unchanged (an unknown one is reported unplaced by the plan, never dropped).
    """
    if type_ == 'fence':
        return 'plank_fence' if (block or '').removeprefix('minecraft:') in PLANK_BLOCKS else 'spruce_fence'
    return type_

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
    for i, group in enumerate(sorted(cellmath.components(shade, cellmath.N8), key=lambda g: (min(g), len(g)))):
        cells = sorted(group)
        item: dict[str, Any] = {
            'id': _item_id('hedge-border', i, cells[0][0], cells[0][1]),
            'kind': 'hedge',
            'role': 'barrier',
            'cells': [list(c) for c in cells],
            'block': HEDGE_BLOCK,
            'height': DEFAULT_HEIGHT['hedge'],
            'height_source': 'default',
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
    """DSM minus DTM (blocks) for each of ``cells`` inside the grid; tree, roof, structure and no-data columns are skipped."""
    from lidar_heightmap import TREE, TREE_OVER_ROOF, ROOF, STRUCTURE

    out: list[int] = []
    for x, z in cells:
        i, j = x - int(hm['x0']), z - int(hm['z0'])
        if 0 <= i < hm['h'].shape[0] and 0 <= j < hm['h'].shape[1]:
            if hm['kind'][i, j] in (TREE, TREE_OVER_ROOF, ROOF, STRUCTURE) or hm['terrain'][i, j] <= 0:
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

    skip = (canopy or set()) | (taken or set()) | footprint
    shade = {q for q, c in rgb.items() if q not in skip and aerial_class(c) == 'shade'}
    use_lidar = bool(lidar_hm) and 'terrain' in lidar_hm
    out: list[dict[str, Any]] = []
    for group in sorted(cellmath.components(shade, cellmath.N8), key=min):
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
        'height_source': 'default' if height is None else 'explicit',
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
    rotation: int | None = None,
    rotation_source: str = 'default',
) -> dict[str, Any]:
    """Furniture plan item at an anchor column.

    A multi-block kind (``street_furniture.FOOTPRINTS``) carries ``rotation`` (0, 90, 180 or 270 degrees
    clockwise from above, default 0) and ``rotation_source``; its anchor is the centre column.
    """
    import street_furniture as sf

    item: dict[str, Any] = {
        'id': _item_id(kind, x, z),
        'kind': kind,
        'role': 'furniture',
        'cell': [int(x), int(z)],
    }
    if kind in sf.FOOTPRINTS:
        item['rotation'] = int(rotation or 0)
        item['rotation_source'] = rotation_source
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
    from osm_footprints import OSMIndex

    x0, z0, x1, z1 = cellmath.bounds(cell)
    return {
        q for q in OSMIndex().cells()
        if x0 <= q[0] <= x1 and z0 <= q[1] <= z1
    }


def _lidar_components(hm, footprint, canopy) -> list[list[tuple[int, int]]]:
    """Connected DSM-above-DTM columns outside footprints, canopy, trees and roofs."""
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
    return sorted((sorted(g) for g in cellmath.components(columns, cellmath.N8)), key=min)


def _group_height(hm, group) -> int:
    """Tallest DSM-above-DTM rise (blocks) over a group of lidar columns."""
    return max(int(hm['h'][a - hm['x0'], b - hm['z0']] - hm['terrain'][a - hm['x0'], b - hm['z0']])
               for a, b in group)


def lidar_objects(hm, footprint, canopy, observations, *, root=None, components=None):
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
    for group in (_lidar_components(hm, footprint, canopy) if components is None else components):
        group = set(group)
        labels = [o for o in observations if o.get('cell') and tuple(o['cell']) in group]
        if not labels:
            continue
        columns |= group
        valid = [o for o in labels if o.get('kind') in sf.FURNITURE_KINDS
                 and o.get('source', 'photo_read') in ('photo_read', 'aerial')
                 and not o.get('unresolved_reason')]
        # Any incompatible label blocks the whole component, including a vehicle
        # touching an otherwise plausible furniture observation.
        kinds = {o.get('kind') for o in labels}
        resolved = bool(valid) and len(kinds) == 1 and len(valid) == len(labels)
        label = valid[0] if resolved else {}
        x, z = tuple(label['cell']) if resolved else min(group)
        kind = str(label.get('kind') or 'unknown')
        rotation, rotation_error = sf.parse_rotation(label.get('rotation'))
        item = _furniture_item(
            x, z, kind, source=label.get('source', 'photo_read') if resolved else 'inference',
            confidence='medium' if resolved else 'low',
            evidence_ids=label.get('evidence_ids') or (), root=root,
            note=str(label.get('note') or 'lidar surface above bare terrain'),
            rotation=rotation, rotation_source='observation',
        )
        item['id'] = _item_id('lidar-object', *min(group))
        item['lidar_cells'] = [list(q) for q in sorted(group)]
        item['lidar_height'] = _group_height(hm, group)
        if not resolved:
            item['provenance']['unresolved_reason'] = (
                'lidar object needs an unambiguous photo/aerial permanent furniture label')
        elif rotation_error and kind in sf.FOOTPRINTS:
            item['provenance']['unresolved_reason'] = rotation_error
        out.append(item)
    return out, columns


def _lidar_hints(items, hm, components) -> None:
    """Add lidar size hints, in place, to items something else already named.

    Barriers whose height is only the kind's default take a height from :func:`lidar_barrier_height`
    (an explicit garden or designer height wins); furniture standing on a lidar
    object carries ``lidar_height`` and ``lidar_cells`` for the placer.
    """
    if not hm or 'terrain' not in hm:
        return
    by_cell = {q: g for g in components for q in g}
    for item in items:
        if item.get('role') == 'barrier' and item.get('height_source') == 'default':
            hint = lidar_barrier_height(hm, (tuple(c) for c in item.get('cells') or []))
            if hint is not None:
                item['height'] = hint
                item['height_source'] = 'lidar'
        elif item.get('role') == 'furniture' and item.get('cell') and 'lidar_cells' not in item:
            group = by_cell.get(tuple(item['cell']))
            if group:
                item['lidar_cells'] = [list(q) for q in sorted(group)]
                item['lidar_height'] = _group_height(hm, group)


def build_decor_plan(
    cell: tuple[int, int],
    *,
    map_barriers: Mapping[str, set[tuple[int, int]]] | None = None,
    map_furniture: Sequence[tuple[int, int, str]] = (),
    designer_items: Sequence[Mapping[str, Any]] = (),
    photo_observations: Sequence[Mapping[str, Any]] = (),
    lidar_hm: Mapping[str, Any] | None = None,
    garden: Sequence[Mapping[str, Any]] | None = None,
    rgb: Mapping[tuple[int, int], Any] | None = None,
    canopy: set[tuple[int, int]] | None = None,
    footprint: set[tuple[int, int]] | None = None,
    free_hedges: bool = False,
    openings: set[tuple[int, int]] | None = None,
    frontages: Sequence[Mapping[str, Any]] | None = None,
    outbuilding_footprints: Sequence[Mapping[str, Any]] = (),
    lidar_rise: Mapping[str, Any] | None = None,
    plot_owner: Mapping[tuple[int, int], int] | None = None,
    building_columns: set[tuple[int, int]] | None = None,
    road_lines: Sequence[Mapping[str, Any]] = (),
    path_lines: Sequence[Mapping[str, Any]] = (),
    land_classes: Mapping[tuple[int, int], str] | None = None,
    carriageway: set[tuple[int, int]] | None = None,
    furniture_avoid: set[tuple[int, int]] | None = None,
    water: set[tuple[int, int]] | None = None,
    district_profile: Mapping[str, Any] | None = None,
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
    @param garden: optional garden barrier items with provenance; ``garden_barriers(cell)`` when omitted
    @param rgb: optional aerial colours; fetched when omitted
    @param canopy: optional tree-canopy columns
    @param footprint: optional building columns; OSM index when omitted
    @param free_hedges: also plan free-standing aerial hedges (off: on 48,-45 it found only
        carriageway shadow and missed the hedges OSM maps)
    @param openings: entrance-gap columns reserved in garden boundaries; read from the garden
        report when ``garden`` is built here
    @param frontages: shared residential frontage descriptions (``garden_sources``); echoed in
        the plan as ``frontages`` (compact) for the report
    @param outbuilding_footprints: mapped outbuildings (``outbuildings.mapped_outbuilding_footprints``)
    @param lidar_rise: raw nDSM (``outbuildings.lidar_rise``); ``None`` plans no lidar outbuildings
    @param plot_owner: ``{(x, z): plot id}``; read from the garden report when ``garden`` is built here
    @param building_columns: every building column near the cell (outbuilding blobs keep one column
        clear of them); ``footprint`` when omitted
    @param road_lines: mapped carriageways for lamp inference (``OSMGround.road_lines``); none, none inferred
    @param path_lines: mapped paths for bench and lamp inference (``OSMGround.path_lines``)
    @param land_classes: ``{(x, z): land-use class}`` around the cell (:func:`land_class_columns`);
        picks the class that decides a way's lamp spacing and bench density
    @param carriageway: road columns no inferred furniture may stand on
    @param furniture_avoid: further columns inferred furniture skips (buildings, paths, water)
    @param water: mapped water columns; no lidar outbuilding candidate is proposed on them (#324)
    @param district_profile: district profile for the cell; loaded from the world when omitted and
        any ways are given
    @param evidence_ids: default evidence ids for aerial-derived rows
    @param root: world root for provenance evidence checks
    @returns: ``{'cell': [tx, tz], 'items': [...], 'reserved': {...}, 'frontages': [...]}``; with
        ways given also ``'furniture_inference'`` (counts by outcome and reason, see
        :func:`infer_street_furniture`)
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
    route: set[tuple[int, int]] = set()
    if garden is None:
        garden_report: dict[str, Any] = {}
        garden = gs.garden_barriers(cell, rgb=rgb, report=garden_report)
        if openings is None:
            openings = {tuple(q) for q in garden_report.get('openings', ())}
            route = {tuple(q) for q in garden_report.get('route_reserved', ())}
        if frontages is None:
            frontages = garden_report.get('frontages', [])
        if plot_owner is None:
            plot_owner = garden_report.get('plots')
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
        for group in sorted(map(sorted, cellmath.components(cols, cellmath.N8)), key=min):
            for q in group:
                seen_barrier.add(q)
            items.append(_barrier_item(
                kind, group,
                source='vector_map', confidence='high',
                evidence_ids=evidence_ids, root=root,
                item_id=_item_id(f'map-{kind}', min(group)[0], min(group)[1]),
            ))

    district_items = []
    for garden_item in garden:
        if garden_item.get('kind') not in BARRIER_KINDS:
            items.append(mark_unplaced(
                garden_item, f"unknown barrier kind {garden_item.get('kind')!r}"))
            continue
        if (garden_item.get('scope') or {}).get('layer') == 'district':
            district_items.append(garden_item)       # area inference follows every observed source
            continue
        cols = [tuple(q) for q in garden_item['cells'] if tuple(q) not in seen_barrier]
        if not cols:
            continue
        seen_barrier.update(cols)
        items.append({**garden_item, 'cells': [list(q) for q in cols]})

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
        if kind not in sf.FURNITURE_KINDS or (x, z) in seen_furniture:
            continue
        seen_furniture.add((x, z))
        items.append(_furniture_item(
            x, z, kind,
            source='vector_map', confidence='high',
            evidence_ids=evidence_ids, root=root,
        ))

    for raw in designer_items:
        kind = raw.get('kind')
        if kind in ob.DESIGNER_KINDS:
            continue                                  # outbuilding traces: planned below, after lidar
        if kind not in sf.FURNITURE_KINDS:
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
        rotation, rotation_error = sf.parse_rotation(raw.get('rotation'))
        rotation_error = str(raw.get('rotation_error') or rotation_error) if kind in sf.FOOTPRINTS else ''
        item = _furniture_item(
            x, z, kind,
            source=src, confidence=conf,
            evidence_ids=evidence_ids, root=root,
            note=str(raw.get('note') or ''),
            rotation=rotation, rotation_source='designer',
        )
        if rotation_error:
            item['provenance']['unresolved_reason'] = rotation_error
        items.append(item)

    # Last of the barrier sources: designer and map rows outrank aerial inference.
    for item in (aerial_free_hedges(
        footprint, rgb, canopy, seen_barrier, lidar_hm=lidar_hm,
        evidence_ids=evidence_ids, root=root,
    ) if free_hedges else ()):
        seen_barrier.update(tuple(c) for c in item['cells'])
        items.append(item)

    lidar_groups = _lidar_components(lidar_hm, footprint, canopy)
    object_items, object_columns = lidar_objects(
        lidar_hm, footprint, canopy, photo_observations, root=root, components=lidar_groups)
    # A lidar component supersedes generic map/designer points: a map bench
    # must not resurrect a component identified as a parked vehicle.
    items = [item for item in items
             if not (item.get('role') == 'furniture'
                     and tuple(item.get('cell') or ()) in object_columns)]
    _lidar_hints(items, lidar_hm, lidar_groups)
    # A raised column already planned as a barrier is that barrier, not an unknown object.
    items.extend(o for o in object_items
                 if not all(tuple(c) in seen_barrier for c in o['lidar_cells']))
    items.extend(_outbuilding_items(
        cell, footprints=outbuilding_footprints, designer=designer_items, rise=lidar_rise,
        building_columns=building_columns if building_columns is not None else footprint,
        taken=object_columns, canopy=canopy, rgb=rgb, route=route,
        plot_owner=plot_owner, evidence_ids=evidence_ids, root=root, water=water or set()))
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
            'role': 'furniture' if kind in sf.FURNITURE_KINDS else 'barrier',
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
        if kind in sf.FOOTPRINTS:                      # a table's rotation counts on this path too
            rotation, rotation_error = sf.parse_rotation(obs.get('rotation'))
            item['rotation'], item['rotation_source'] = rotation or 0, 'observation'
            if rotation_error and not reason:
                reason = rotation_error
        prov.attach_provenance(
            item,
            source=src,
            confidence='low' if reason else 'medium',
            evidence_ids=list(obs.get('evidence_ids') or evidence_ids),
            unresolved_reason=reason,
            root=root,
        )
        items.append(item)

    observed_columns = seen_barrier | seen_furniture
    for item in items:
        observed_columns.update(tuple(c) for c in item.get('cells', ()))
        if item.get('cell'):
            observed_columns.add(tuple(item['cell']))
    for item in district_items:
        fresh = [list(c) for c in item['cells'] if tuple(c) not in observed_columns]
        if fresh:
            items.append({**item, 'cells': fresh})
            observed_columns.update(map(tuple, fresh))

    keep_clear = (set(openings or ()) | set(route)
                  | gs.reserved_openings(frontages or ()) | gs.reserved_route(frontages or ()))
    reserved = reserve_openings(items, keep_clear)
    inference = None
    if road_lines or path_lines:
        # Last of all: a mapped point, designer trace or labelled object at or near a spot wins.
        observed = [i for i in reserved['items'] if i.get('role') == 'furniture' and i.get('cell')
                    and (i.get(prov.PROVENANCE_KEY) or {}).get('source') != 'inference']
        barrier_columns = set(seen_barrier) | {
            tuple(c) for i in reserved['items'] if i.get('role') == 'barrier' for c in i.get('cells') or ()}
        inferred, inference = infer_street_furniture(
            cell, road_lines=road_lines, path_lines=path_lines, land_classes=land_classes or {},
            observed=observed, profile=district_profile, evidence_ids=evidence_ids, root=root,
            keep_out={
                'door route or entrance opening': _dilate(keep_clear, ROUTE_CLEARANCE),
                'carriageway': set(carriageway or ()),
                'barrier column': barrier_columns,
                'building, path or water column': set(furniture_avoid or ()) | object_columns,
            })
        reserved['items'] = list(reserved['items']) + inferred
    plan = {'cell': [int(cell[0]), int(cell[1])], 'items': reserved['items'],
            'reserved': {'openings': sorted([int(x), int(z)] for x, z in keep_clear),
                         'conflicts': reserved['conflicts']},
            'frontages': [compact_frontage(f) for f in frontages or ()]}
    if inference is not None:
        plan['furniture_inference'] = inference
    return plan


# --- inferred street furniture (#260, I-008) -----------------------------------------------
ROUTE_CLEARANCE = 1       # columns kept clear around a door route or entrance opening
LAMP_MIN_GAP = 10         # blocks between two inferred lamps, whichever ways they stand on
BENCH_MIN_GAP = 8
OBSERVED_LAMP_SHARE = 0.75    # a mapped or designed lamp suppresses inferred ones within this share of the spacing
OBSERVED_BENCH_RADIUS = 8     # ... and a mapped or designed bench within this many blocks
BENCH_PATHS = ('footway', 'path', 'pedestrian')
CLASS_REACH = 10          # blocks either side of a way looked at to find the land it adjoins
CLASS_STEP = 3.0          # blocks between samples along the way


def _dilate(cols: Iterable[tuple[int, int]], n: int) -> set[tuple[int, int]]:
    cols = {tuple(c) for c in cols}
    if n <= 0:
        return cols
    return {(x + a, z + b) for x, z in cols for a in range(-n, n + 1) for b in range(-n, n + 1)}


PARK_UMBRELLA_OVER = ('rural', 'natural')   # nested land that a park polygon still reads as park (#322)


def park_umbrella_columns(idx, box: tuple[int, int, int, int]) -> set[tuple[int, int]]:
    """Columns of ``box`` inside any park-class use polygon, however small a polygon nests inside it.

    The coverage rule gives a column to the smallest use polygon, so a path through a
    ``landuse=meadow`` patch inside ``leisure=park`` reads as rural and takes no benches (#322). A
    path inside a park is still a park path: the park polygon is the umbrella. Park-class cover
    polygons (``landuse=grass``) are not an umbrella; they only classify columns no use polygon holds.

    @param idx: ``OSMIndex`` (reads ``idx.areas``)
    @param box: inclusive ``(x0, z0, x1, z1)``
    @returns: set of ``(x, z)`` columns
    """
    from coverage import osm_tags, scan_polygon
    import land_use

    cols: set[tuple[int, int]] = set()
    for rings, props in idx.areas:
        hit = land_use.area_class(osm_tags(props))
        if hit and hit['kind'] == 'use' and hit['class'] == 'park':
            cols.update(scan_polygon(rings, box))
    return cols


def land_class_columns(idx, box: tuple[int, int, int, int]) -> dict[tuple[int, int], str]:
    """Land-use class (#253) of each column in ``box`` that a polygon or building classifies.

    Area polygons by the coverage rule (smallest use polygon, then cover), except that a park polygon
    is an umbrella over nested rural or natural land (#322, :func:`park_umbrella_columns`), then each
    building's own class over its footprint. Columns nothing classifies are absent (``unknown``
    claims nothing). Only furniture reads this map; the coverage rule itself is unchanged.

    @param idx: ``OSMIndex``
    @param box: inclusive ``(x0, z0, x1, z1)``
    @returns: ``{(x, z): class id}``
    """
    from coverage import area_classes, building_land_use

    classes = area_classes(idx, box)
    out = {q: v[0] for q, v in classes.items() if v[0] != 'unknown'}
    for q in park_umbrella_columns(idx, box):
        if out.get(q) in PARK_UMBRELLA_OVER:
            out[q] = 'park'
    x0, z0, x1, z1 = box
    by_building: dict[int, str] = {}
    for q, i in idx.cells().items():
        if not (x0 <= q[0] <= x1 and z0 <= q[1] <= z1):
            continue
        if i not in by_building:
            rings, props = idx.buildings[i]
            by_building[i] = building_land_use(idx, rings, props, classes)['class']
        if by_building[i] != 'unknown':
            out[q] = by_building[i]
    return out


def adjoining_class(points: Sequence[Sequence[float]], half_width: float,
                    land_classes: Mapping[tuple[int, int], str], *,
                    reach: float = CLASS_REACH, step: float = CLASS_STEP) -> str:
    """Land-use class a way adjoins: the most common class within ``reach`` blocks of its edges.

    Samples the way's own columns and both sides out to ``half_width + reach`` every ``step``
    blocks. Ties go to the class id that sorts first, so a way always gets the same answer.

    @param points: way centreline ``(x, z)``
    @param half_width: half the carriageway or path width
    @param land_classes: :func:`land_class_columns` result
    @param reach: how far past the edge to look
    @param step: sampling interval
    @returns: class id, or ``'unknown'`` when nothing classified is near
    """
    import street_furniture as sf

    length = sf.polyline_length(points)
    n = max(int(length // step) + 1, 1)
    offsets = [0.0] + [sign * d for d in range(1, int(half_width + reach) + 1, 2) for sign in (1, -1)]
    votes: Counter = Counter()
    for x, z, tx, tz in sf._stations(points, [min(i * step, length) for i in range(n)]):
        for d in offsets:
            cls = land_classes.get((math.floor(x - tz * d), math.floor(z + tx * d)))
            if cls:
                votes[cls] += 1
    return min(votes, key=lambda c: (-votes[c], c)) if votes else 'unknown'


def infer_street_furniture(
    cell: tuple[int, int],
    *,
    road_lines: Sequence[Mapping[str, Any]] = (),
    path_lines: Sequence[Mapping[str, Any]] = (),
    land_classes: Mapping[tuple[int, int], str],
    observed: Sequence[Mapping[str, Any]] = (),
    keep_out: Mapping[str, set[tuple[int, int]]] | None = None,
    profile: Mapping[str, Any] | None = None,
    evidence_ids: Sequence[str] = (),
    root=None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Labelled lamp and bench plan items where the map holds no point for them (#260, I-008).

    Lamps follow mapped roads and paths at a spacing resolved by ``land_use.resolve`` from the land
    the way adjoins (district profile, else the table's ``highways`` map; null or a class that allows
    no inference means none; OSM ``lit=no`` means none, ``lit=yes`` lights a way the table does not).
    Benches follow footways, paths and pedestrian ways whose adjoining class row (or profile) gives a
    ``bench_per_100m`` (park only, as shipped). Every item is ``inference`` / ``low`` and never reads
    as observed. A candidate is dropped, and counted by reason, when it:

      * is outside the cell,
      * stands on a column an observed item holds, or within the suppression radius of an observed
        item of its kind (a mapped point, designer trace or labelled object wins),
      * stands in any ``keep_out`` set (door route and entrance opening, carriageway, building, path
        or water column),
      * is closer than the minimum gap to an inferred item of its kind already kept.

    @param cell: ``(tx, tz)``
    @param road_lines: ``{'highway', 'points', 'half', 'lit'?, 'footway'?, 'seed'?}`` per mapped road
    @param path_lines: same shape per mapped path
    @param land_classes: :func:`land_class_columns` result
    @param observed: plan furniture items from map, designer or photo (rows with a ``cell``)
    @param keep_out: ``{reason: columns}``; the first set holding a candidate names its reason
    @param profile: district profile; loaded from the world when None
    @param evidence_ids: evidence ids from the caller; a profile's own ids are added for its spacing
    @param root: world root for the evidence check
    @returns: ``(items, summary)``; summary has ``lamps``, ``benches``, ``candidates``, ``dropped``
        (reason: count), ``ways_skipped`` (reason: count), ``lit_tags`` and ``spacing_layers``
    """
    import garden_sources as gs
    import street_furniture as sf

    profile = gs.load_district(cell) if profile is None else profile
    x0, z0, x1, z1 = cellmath.bounds(cell)
    taken = {tuple(i['cell']) for i in observed}
    seen_kind = {}
    for item in observed:
        seen_kind.setdefault(item.get('kind'), []).append(tuple(item['cell']))
    keep_out = dict(keep_out or {})
    dropped: Counter = Counter()
    skipped: Counter = Counter()
    lit_tags: Counter = Counter()
    layers: Counter = Counter()
    items: list[dict[str, Any]] = []
    kept: dict[str, list[tuple[int, int]]] = {'street_lamp': [], 'bench': []}
    summary = {'lamps': 0, 'benches': 0, 'candidates': 0}

    def admit(cand: dict[str, Any], radius: float, gap: float, note: str, ids) -> None:
        summary['candidates'] += 1
        q = (cand['x'], cand['z'])
        if not (x0 <= q[0] <= x1 and z0 <= q[1] <= z1):
            dropped['outside cell'] += 1
            return
        near = [o for o in seen_kind.get(cand['kind'], ()) if math.dist(o, q) <= radius]
        if q in taken or near:
            dropped['mapped or designed item here or nearby'] += 1
            return
        for reason, cols in keep_out.items():
            if q in cols:
                dropped[reason] += 1
                return
        if any(math.dist(o, q) < gap for o in kept[cand['kind']]):
            dropped['too close to another inferred item'] += 1
            return
        kept[cand['kind']].append(q)
        item = _furniture_item(q[0], q[1], cand['kind'], source='inference', confidence='low',
                               evidence_ids=ids, note=note, root=root)
        item['id'] = _item_id('infer-' + cand['kind'], *q)
        item['inferred'] = {k: v for k, v in cand.items() if k not in ('kind', 'x', 'z', 'source', 'confidence')}
        items.append(item)
        summary['lamps' if cand['kind'] == 'street_lamp' else 'benches'] += 1

    for way, is_path in [(w, False) for w in road_lines] + [(w, True) for w in path_lines]:
        highway, pts = way.get('highway'), way.get('points') or []
        lit = way.get('lit') or None
        lit_tags[lit or 'absent'] += 1
        if way.get('footway') in sf.UNLIT_FOOTWAYS:
            skipped['sidewalk or crossing (the road carries the lamps)'] += 1
            continue
        half = float(way.get('half') or (1.0 if is_path else 3.0))
        cls = adjoining_class(pts, half, land_classes)
        # The seed names the way, never the cell: one way planned from two cells gets one phase.
        seed = int(way.get('seed') if way.get('seed') is not None
                   else sf.stable_seed(highway, *(round(c) for c in (pts[0] if pts else (0, 0)))))
        resolved = gs.lamp_spacing(cell, cls, highway, profile=profile or {})
        stop = resolved['value'] is None and resolved['layer'] is not None   # an explicit null: unlit on purpose
        if not stop:
            table = {highway: resolved['value']}
            candidates = sf.lamp_positions(pts, highway, table, seed, half_width=half, lit=lit)
            if candidates:
                layers[resolved['layer'] or 'lit tag'] += 1
                spacing = candidates[0]['spacing']
                ids = list(evidence_ids) + list(resolved['provenance']['evidence_ids'] if resolved['layer'] else ())
                basis = {'district': 'district profile', 'class': 'class table', None: 'OSM lit=yes'}.get(
                    resolved['layer'], f"{resolved['layer']} convention")
                for cand in candidates:
                    cand.update(land_class=cls, layer=resolved['layer'] or 'lit tag')
                    admit(cand, spacing * OBSERVED_LAMP_SHARE, LAMP_MIN_GAP,
                          f'inferred lamp: {highway} way beside {cls} land, {spacing:g}-block spacing ({basis}); not observed',
                          ids)
            else:
                skipped['no lamp spacing for this way (class, highway or lit=no)'] += 1
        else:
            skipped['profile sets no lamps for this highway'] += 1
        if is_path and highway in BENCH_PATHS:
            dens = gs.bench_density(cell, cls, profile=profile or {})
            if dens['value'] is None:
                skipped['no bench density for the adjoining class'] += 1
                continue
            ids = list(evidence_ids) + list(dens['provenance']['evidence_ids'] if dens['layer'] else ())
            for cand in sf.bench_positions(pts, dens['value'], seed + 1, half_width=half):
                cand.update(land_class=cls, layer=dens['layer'], per_100m=dens['value'])
                admit(cand, OBSERVED_BENCH_RADIUS, BENCH_MIN_GAP,
                      f'inferred bench: {highway} through {cls} land, {dens["value"]:g} per 100 blocks '
                      f'({dens["layer"]}); not observed', ids)
    summary.update(dropped=dict(dropped), ways_skipped=dict(skipped), lit_tags=dict(lit_tags),
                   spacing_layers=dict(layers))
    return items, summary


def _outbuilding_items(
    cell: tuple[int, int],
    *,
    footprints: Sequence[Mapping[str, Any]],
    designer: Sequence[Mapping[str, Any]],
    rise: Mapping[str, Any] | None,
    building_columns: set[tuple[int, int]],
    taken: set[tuple[int, int]],
    canopy: set[tuple[int, int]],
    rgb: Mapping[tuple[int, int], Any],
    route: set[tuple[int, int]],
    plot_owner: Mapping[tuple[int, int], int] | None,
    evidence_ids: Sequence[str],
    root=None,
    water: set[tuple[int, int]] = frozenset(),
) -> list[dict[str, Any]]:
    """Outbuilding plan items in evidence order: mapped, lidar blob, designer trace (#251).

    Mapped water columns never take a lidar blob (#324): the placer refuses them, so a blob
    there (a boat or pontoon on a river) would only show as an unresolved item.
    """

    mapped_cols = {tuple(c) for f in footprints for c in f['cells']}
    houses = set(building_columns) - mapped_cols
    targets = sorted(houses | set(route))
    common = {'rise': rise, 'targets': targets, 'plot_owner': plot_owner}
    ring = {(x + a, z + b) for x, z in building_columns for a in (-1, 0, 1) for b in (-1, 0, 1)}
    mapped = ob.mapped_items(footprints, evidence_ids=evidence_ids, root=root, **common)
    lidar = ob.lidar_items(
        rise, exclude=ring | taken | set(water), canopy=canopy, rgb=rgb, cell_box=cellmath.bounds(cell),
        evidence_ids=evidence_ids, root=root, targets=targets, plot_owner=plot_owner)
    traced = ob.designer_items(
        [r for r in designer if r.get('kind') in ob.DESIGNER_KINDS],
        default_evidence=evidence_ids, root=root, **common)
    return ob.merge_sources(mapped, lidar, traced)


def reserve_openings(items: Sequence[dict[str, Any]], openings) -> dict[str, Any]:
    """Keep entrance gaps clear of every barrier source (#240).

    Inferred, convention, designer and aerial barrier columns inside an opening are dropped (an
    emptied item goes with them). A mapped (``vector_map``) boundary is explicit observation and
    stays; the overlap is reported in ``conflicts`` instead of silently erased.

    @param items: decor plan items
    @param openings: reserved ``(x, z)`` columns
    @returns: ``{'items': kept items, 'conflicts': [{item, cells, reason}]}``
    """
    gap = {tuple(q) for q in openings}
    kept: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for item in items:
        cells = [tuple(c) for c in item.get('cells') or ()]
        hit = [c for c in cells if c in gap]
        if item.get('role') != 'barrier' or not hit:
            kept.append(item)
            continue
        if (item.get('provenance') or {}).get('source') == 'vector_map':
            conflicts.append({'item': item.get('id'), 'cells': [list(c) for c in hit],
                              'reason': 'mapped boundary crosses a reserved entrance opening; '
                                        'kept as explicit observation'})
            kept.append(item)
            continue
        rest = [c for c in cells if c not in gap]
        conflicts.append({'item': item.get('id'), 'cells': [list(c) for c in hit],
                          'reason': 'entrance opening reserved; barrier columns dropped'})
        if rest:
            kept.append({**item, 'cells': [list(c) for c in rest]})
    return {'items': kept, 'conflicts': conflicts}


def compact_frontage(f: Mapping[str, Any]) -> dict[str, Any]:
    """Report-sized view of a frontage description (no per-column lists)."""
    return {'id': f.get('id'), 'scope': f.get('scope'), 'eligible': f.get('eligible'),
            'reason': f.get('reason') or (f.get('provenance') or {}).get('unresolved_reason', ''),
            'open_frontage': f.get('open_frontage'), 'door': f.get('door'),
            'connection': f.get('connection'), 'opening': f.get('opening'),
            'opening_evidence': f.get('opening_evidence'),
            'land_use': f.get('land_use'), 'private_surface': f.get('private_surface'),
            'pavement_columns': len(f.get('pavement') or ()),
            'forecourt_columns': len(f.get('forecourt') or ()),
            'provenance': f.get('provenance')}


def unresolved_items(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Plan items whose provenance names a non-empty unresolved reason.

    @param plan: decor plan from :func:`build_decor_plan`
    @returns: unresolved items in plan order
    """
    return [i for i in plan.get('items') or [] if prov.is_unresolved(i.get(prov.PROVENANCE_KEY))]


def resolved_barriers(
    plan: Mapping[str, Any],
    unplaced: list[dict[str, Any]] | None = None,
    sources: dict[tuple[int, int], Mapping[str, Any]] | None = None,
) -> dict[tuple[int, int], tuple[str, str, int]]:
    """Resolved barrier columns → ``(kind, block, height)`` for the decor layer.

    Later items win when two resolved barriers share a column. ``sources``
    receives that winning item so the placer can keep facing and provenance.

    @param plan: decor plan
    @param unplaced: optional list receiving skipped unresolved or unknown-kind items, with reasons
    @param sources: optional map filled with the plan item that owns each column
    @returns: column map ready for placement
    """
    out: dict[tuple[int, int], tuple[str, str, int]] = {}
    for item in plan.get('items') or []:
        if item.get('role') != 'barrier':
            continue
        provenance = item.get(prov.PROVENANCE_KEY)
        if prov.is_unresolved(provenance):
            if unplaced is not None and item.get('kind') != 'none':
                unplaced.append(mark_unplaced(
                    item, 'barrier skipped: provenance carries an unresolved reason: '
                    f"{provenance['unresolved_reason']}"))
            continue
        kind = item.get('kind')
        if kind not in BARRIER_KINDS:
            if unplaced is not None:
                unplaced.append(mark_unplaced(item, f'unknown barrier kind {kind!r}'))
            continue
        block = item.get('block') or DEFAULT_BLOCK[kind]
        height = int(item.get('height') or DEFAULT_HEIGHT[kind])
        for c in item.get('cells') or []:
            key = (int(c[0]), int(c[1]))
            out[key] = (kind, block, height)
            if sources is not None:
                sources[key] = item
    return out


def print_unresolved_barriers(items: Sequence[Mapping[str, Any]]) -> None:
    """Print one bounded warning for barrier runs skipped because of provenance.

    @param items: plan items, including unresolved rows retained by the decor report
    """
    skipped = [i for i in items if i.get('role') == 'barrier' and i.get('kind') != 'none'
               and prov.is_unresolved(i.get(prov.PROVENANCE_KEY))]
    if skipped:
        ids = ', '.join(str(i.get('id') or '<unnamed>') for i in skipped[:5])
        more = ', ...' if len(skipped) > 5 else ''
        print(f'  {len(skipped)} barrier runs skipped as unresolved: {ids}{more}')


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
        if 'rotation' in item:
            row['rotation'] = int(item['rotation'])
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
