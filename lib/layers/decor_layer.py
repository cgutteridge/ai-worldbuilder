#!/usr/bin/env python3
"""Layered decor pass for one 128² cell — HANDOFF-LAYERED-PASSES §1 (pass 4), §2, §3.

Decor builds from a decor plan (#150): aerial/photo/map/designer barriers and street
furniture, each with shared provenance. Barriers sit anywhere but the carriageway; furniture
sits on paving, never on a path-line column. Building-border hedges from the aerial may sit
against a footprint; other barriers and furniture stay clear of builds. Items the plan
cannot resolve are listed, not silently skipped. Placed only where the live block is air
(or a stem/flower, as in §10) and not owned by a neighbour or by another mask, clipped to
the cell. Then any wall or fence pieces of the same type that one extra block could link
(face to face, diagonal is disjoint) are joined, one block per join, repeating until none
remain; supported spots first, air/flowers only. Writes a decor mask (§3) only after a
clean readback. An empty plan (nothing to place) still lands so the cell can finish (#127).
Garden outbuildings (sheds, garages) are decor plan items too (``outbuildings.py``, #251): mapped
footprint, raw lidar blob or designer trace; each is placed whole or listed unplaced with a reason.

  python3 scripts/decor_layer.py --cell 48,-44            # dry run
  SITE_OWNER=<owner> python3 scripts/decor_layer.py --cell 48,-44 --apply
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from script_env import ROOT  # noqa: E402

from ground_layer import (BLOCK_KIND, PROBE_Y_RANGE, bare, cell_contains, ignored_builds,  # noqa: E402
                          is_air, neighbour_owned, load_masked_positions)
from pass_mask import build_mask, clip_to_cell  # noqa: E402
from site_kit import Plan, World  # noqa: E402
import decor_plan as dplan  # noqa: E402
import outbuildings as ob  # noqa: E402
import provenance as prov  # noqa: E402
import street_furniture as sf  # noqa: E402


def plan_name(tx: int, tz: int) -> str:
    """Build name, e.g. highfield_48_m44_decor."""
    return f"highfield_{tx}_{'m' if tz < 0 else ''}{abs(tz)}_decor"


JOINABLE = ('brick_wall', 'iron_bars', 'spruce_fence')
NEIGHBOURS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
# Ground kinds the barrier pass must not sit on (live surface, not only map way columns):
# the carriageway (as for furniture). Paving and brick paths are fair ground for hedges and fences.


WARM_RING = 1                    # columns around each decor column warmed (join candidates sit beside pieces)
WARM_BUDGET = 60_000             # positions warmed at most; LiveBlocks drops its whole cache past 65 536


def decor_columns(decor_items, furniture_items) -> set[tuple[int, int]]:
    """Columns the decor plan can read: barrier cells and furniture spots, plus a ring."""
    cols: set[tuple[int, int]] = set()
    for item in decor_items:
        if item.get('role') == 'barrier':
            cols.update((int(c[0]), int(c[1])) for c in item.get('cells') or [])
        elif item.get('role') == ob.KIND:
            cols.update((int(c[0]), int(c[1])) for c in item.get('cells') or [])
    cols.update((int(i['x']), int(i['z'])) for i in furniture_items)
    r = WARM_RING
    return {(x + a, z + b) for x, z in cols for a in range(-r, r + 1) for b in range(-r, r + 1)}


def warm_decor_columns(world, cols) -> int:
    """Prefetch the blocks ``surface_y`` and the placement checks read, for ``cols`` only.

    A whole-cell probe (132 x 132 x 90) is far over the live store's cache cap, so it
    evicted itself and every later read was its own paced MCP scan. Stops at
    :data:`WARM_BUDGET` positions; reads beyond that still work, one section scan each.

    @param world: :class:`World` or any duck; no-op without ``prefetch``
    @param cols: iterable of ``(x, z)``
    @returns: number of positions requested
    """
    warm = getattr(world, 'prefetch', None)
    if warm is None:
        return 0
    ylo, yhi = PROBE_Y_RANGE
    per_col = yhi - ylo + 1
    ordered = sorted(cols)[:max(0, WARM_BUDGET // per_col)]
    for i in range(0, len(ordered), 64):
        warm((x, y, z) for x, z in ordered[i:i + 64] for y in range(ylo, yhi + 1))
    return len(ordered) * per_col


class DecorColumnView:
    """Read-only block view over a world: decor columns come from a local snapshot.

    Reads at snapshotted positions never touch the live scan cache, so later reads
    cannot evict (or be evicted by) one another. Other positions fall through to ``world``.
    """

    def __init__(self, world, blocks: dict[tuple[int, int, int], str | None]):
        self.world = world
        self.blocks = blocks

    def block(self, x, y, z):
        pos = (x, y, z)
        if pos in self.blocks:
            return self.blocks[pos]
        return self.world.block(x, y, z)

    def prefetch(self, positions) -> None:
        warm = getattr(self.world, 'prefetch', None)
        if warm is not None:
            warm(positions)


def snapshot_decor_columns(world, cols):
    """Read the blocks decor planning needs once, one 16x16 chunk at a time.

    One up-front warm of every decor column can exceed the live store's cache cap; the
    store then clears itself on the next cold read and every later read is its own paced
    section scan (a cell with a long fence hung for an hour). Here each chunk's columns
    are warmed just before they are copied out, so each section is scanned about once
    and the live cache never holds more than one chunk.

    @param world: :class:`World` or any duck; returned unchanged without ``prefetch``
    @param cols: iterable of ``(x, z)``
    @returns: a world-like with ``block`` / ``prefetch`` serving the columns from memory
    """
    if getattr(world, 'prefetch', None) is None:
        return world
    ylo, yhi = PROBE_Y_RANGE
    chunks: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for x, z in sorted(cols):
        chunks.setdefault((x >> 4, z >> 4), []).append((x, z))
    blocks: dict[tuple[int, int, int], str | None] = {}
    for key in sorted(chunks):
        world.prefetch((x, y, z) for x, z in chunks[key] for y in range(ylo, yhi + 1))  # <= 23 040
        for x, z in chunks[key]:
            for y in range(ylo, yhi + 1):
                blocks[(x, y, z)] = world.block(x, y, z)
    return DecorColumnView(world, blocks)


def column_ground_kind(world: World, x: int, z: int) -> str | None:
    """Classified ground kind at the live surface of (x, z), or None if empty."""
    from surface_probe import surface_y

    top = surface_y(world, x, z)
    if not top:
        return None
    return BLOCK_KIND.get(top[1])


def on_carriageway(world: World, x: int, z: int) -> bool:
    """True when a barrier column would sit on the carriageway (tarmac, marking, kerb)."""
    return column_ground_kind(world, x, z) in sf.ROAD_KINDS


def above_lidar_ground(x: int, y: int, z: int, lidar_hm: Mapping[str, Any] | None) -> bool:
    """Veto a live surface over two blocks above DTM; missing terrain gives no veto."""
    if lidar_hm is None or 'terrain' not in lidar_hm:
        return False
    terrain = lidar_hm['terrain']
    i, j = x - int(lidar_hm['x0']), z - int(lidar_hm['z0'])
    if not (0 <= i < terrain.shape[0] and 0 <= j < terrain.shape[1]):
        return False
    ground = int(terrain[i, j])
    return ground > 0 and y - ground > 2


def may_join_at(
    pos: tuple[int, int, int],
    mat: str,
    *,
    cell: tuple[int, int],
    near1: set,
    allow_near1_mats: set[str],
    owned: set,
    masked: set,
    world: World,
    lidar_hm: Mapping[str, Any] | None = None,
) -> bool:
    """True when ``join_pieces`` may place ``mat`` at ``pos``.

    Footprint-ring columns (``near1``) are banned by default. Only materials that
    already have ``allow_against_build`` placements (building-border hedges) may
    join into that ring. Live block must be air/flower, and the spot must not be
    owned or masked.

    @param pos: candidate (x, y, z)
    @param mat: joinable block id being considered
    @param cell: (tx, tz) cell bounds
    @param near1: footprint and structure columns dilated by 1
    @param allow_near1_mats: materials already placed with allow_against_build
    @param owned: neighbour-owned block positions
    @param masked: positions owned by another pass mask
    @param world: live or planning world
    @param lidar_hm: optional lidar height map; terrain vetoes raised live surfaces
    @returns: whether the join candidate is allowed
    """
    xz = (pos[0], pos[2])
    if xz in near1 and mat not in allow_near1_mats:
        return False
    if lidar_hm is not None:
        from surface_probe import surface_y

        top = surface_y(world, *xz)
        if top and above_lidar_ground(pos[0], top[0], pos[2], lidar_hm):
            return False
    return (cell_contains(cell, pos[0], pos[2])
            and is_air(world.block(*pos)) and pos not in owned and pos not in masked)


def join_pieces(found: dict[tuple[int, int, int], str], live, can_place,
                present: dict[tuple[int, int, int], str] | None = None) -> dict[tuple[int, int, int], str]:
    """Extra blocks that link separate walls/fences of the same type, one block per join.

    Pieces touch only face to face (diagonal is disjoint). A candidate is an empty spot
    (`can_place(pos, mat)`) with face neighbours in two or more different pieces of one type;
    live same-type blocks next to the plan count as pieces too (`live(pos)` -> block id), as
    do `present` blocks (planned spots where that type is already standing).
    Supported candidates (solid block below, or planned) go first, then the lowest x, y, z;
    each placement merges its pieces, so later candidates that would only duplicate that
    join are dropped, and the sweep repeats until nothing more joins.
    """
    added: dict[tuple[int, int, int], str] = {}
    for mat in JOINABLE:
        nodes = {p for p, b in found.items() if b == mat}
        nodes |= {p for p, b in (present or {}).items() if b == mat}
        for p in list(nodes):
            for d in NEIGHBOURS:
                q = (p[0] + d[0], p[1] + d[1], p[2] + d[2])
                if q not in found and bare(live(q)) == f'minecraft:{mat}':
                    nodes.add(q)
        parent = {p: p for p in nodes}

        def find(p):
            while parent[p] != p:
                parent[p] = parent[parent[p]]
                p = parent[p]
            return p

        for p in nodes:
            for d in NEIGHBOURS:
                q = (p[0] + d[0], p[1] + d[1], p[2] + d[2])
                if q in parent:
                    parent[find(p)] = find(q)
        while True:
            cands = {(p[0] + d[0], p[1] + d[1], p[2] + d[2]) for p in nodes for d in NEIGHBOURS} - nodes
            cands = [c for c in cands if can_place(c, mat) and c not in found and c not in added]

            def supported(c):
                below = (c[0], c[1] - 1, c[2])
                return below in found or below in added or not is_air(live(below))
            cands.sort(key=lambda c: (not supported(c), c))
            placed = False
            for c in cands:
                roots = {find((c[0] + d[0], c[1] + d[1], c[2] + d[2]))
                         for d in NEIGHBOURS if (c[0] + d[0], c[1] + d[1], c[2] + d[2]) in parent}
                if len(roots) < 2:
                    continue
                added[c] = mat
                parent[c] = c
                nodes.add(c)
                for r in roots:
                    parent[r] = c
                placed = True
                break
            if not placed:
                break
    return added


def place_furniture(
    cell: tuple[int, int],
    items: list[dict],
    world: World,
    *,
    way: set,
    near1: set,
    owned: set,
    masked: set,
    lidar_hm: Mapping[str, Any] | None = None,
) -> tuple[dict[tuple[int, int, int], str], list[dict], list[dict]]:
    """Place street-furniture recipes on any non-carriageway surface column.

    Skips path-line columns (``way``), columns against a build (``near1``) unless the
    item sets ``allow_against_build``, carriageway surfaces (road, marking, kerb), and any recipe spot that is
    not air / is owned or masked. Failed rows are returned as unplaced plan-shaped dicts.

    @param cell: (tx, tz)
    @param items: ``{x,z,kind,source}`` from the decor plan
    @param world: live or planning world
    @param way: mapped road / path / water columns
    @param near1: footprint and structure columns dilated by 1
    @param owned: neighbour-owned block positions
    @param masked: positions owned by another pass mask
    @param lidar_hm: optional lidar height map; terrain vetoes raised live surfaces
    @returns: (placements, report rows that were placed, unplaced rows with reasons)
    """
    from surface_probe import surface_y

    found: dict[tuple[int, int, int], str] = {}
    report: list[dict] = []
    unplaced: list[dict] = []
    for item in items:
        x, z, kind = int(item['x']), int(item['z']), item['kind']
        q = (x, z)
        allow = bool(item.get('allow_against_build'))
        if not cell_contains(cell, *q):
            unplaced.append(dplan.mark_unplaced(item, 'outside cell'))
            continue
        if q in way:
            unplaced.append(dplan.mark_unplaced(item, 'on path/road/water column'))
            continue
        if q in near1 and not allow:
            unplaced.append(dplan.mark_unplaced(item, 'against build'))
            continue
        if column_ground_kind(world, x, z) in sf.ROAD_KINDS:
            unplaced.append(dplan.mark_unplaced(item, 'on carriageway'))
            continue
        top = surface_y(world, x, z)
        if not top:
            unplaced.append(dplan.mark_unplaced(item, 'no surface'))
            continue
        if above_lidar_ground(x, top[0], z, lidar_hm):
            unplaced.append(dplan.mark_unplaced(item, 'above lidar ground'))
            continue
        stack = sf.recipe(kind)
        voxels: dict[tuple[int, int, int], str] = {}
        ok = True
        for dy, block in stack:
            pos = (x, top[0] + dy, z)
            if not is_air(world.block(*pos)) or pos in owned or pos in masked or pos in found:
                ok = False
                break
            voxels[pos] = block
        if not ok:
            unplaced.append(dplan.mark_unplaced(item, 'blocked by live or owned block'))
            continue
        found.update(voxels)
        row = {'x': x, 'z': z, 'kind': kind, 'source': item.get('source', 'map'),
               'blocks': len(voxels)}
        if item.get('note'):
            row['note'] = item['note']
        if item.get('plan_id'):
            row['plan_id'] = item['plan_id']
        report.append(row)
    return found, report, unplaced


def place_barriers_from_plan(
    cell: tuple[int, int],
    items: Sequence[Mapping[str, Any]],
    world: World,
    *,
    way: set,
    near1: set,
    owned: set,
    masked: set,
    lidar_hm: Mapping[str, Any] | None = None,
) -> tuple[dict[tuple[int, int, int], str], list[dict], list[dict]]:
    """Place barrier plan items (hedges, walls, fences) anywhere but the carriageway.

    Building-border hedges with ``allow_against_build`` may occupy the near1 ring;
    other barriers still skip it. Columns that cannot be placed are returned as
    unplaced copies with an unresolved reason.

    @param cell: (tx, tz)
    @param items: barrier role plan items
    @param world: live or planning world
    @param way: mapped road / path / water columns
    @param near1: footprint and structure columns dilated by 1
    @param owned: neighbour-owned block positions
    @param masked: positions owned by another pass mask
    @param lidar_hm: optional lidar height map; terrain vetoes raised live surfaces
    @returns: (placements, placed report rows, unplaced items)
    """
    from surface_probe import surface_y

    found: dict[tuple[int, int, int], str] = {}
    present: dict[tuple[int, int, int], str] = {}
    report: list[dict] = []
    unplaced: list[dict] = []
    for item in items:
        if item.get('role') != 'barrier':
            continue
        if prov.is_unresolved(item.get('provenance')):
            continue
        kind = item.get('kind')
        mat = item.get('block') or dplan.DEFAULT_BLOCK.get(kind or '', '')
        height = int(item.get('height') or dplan.DEFAULT_HEIGHT.get(kind or '', 1))
        allow = bool(item.get('allow_against_build'))
        cells = item.get('cells') or []
        if not cells:
            unplaced.append(dplan.mark_unplaced(item, 'no cells'))
            continue
        for raw in cells:
            q = (int(raw[0]), int(raw[1]))
            if not cell_contains(cell, *q):
                unplaced.append(dplan.mark_unplaced(
                    {**item, 'cells': [list(q)], 'id': f"{item.get('id', kind)}-{q[0]}-{q[1]}"}, 'outside cell'))
                continue
            if q in near1 and not allow:
                unplaced.append(dplan.mark_unplaced(
                    {**item, 'cells': [list(q)], 'id': f"{item.get('id', kind)}-{q[0]}-{q[1]}"},
                    'against build',
                ))
                continue
            top = surface_y(world, *q)
            if top and above_lidar_ground(q[0], top[0], q[1], lidar_hm):
                unplaced.append(dplan.mark_unplaced(
                    {**item, 'cells': [list(q)], 'id': f"{item.get('id', kind)}-{q[0]}-{q[1]}"},
                    'above lidar ground',
                ))
                continue
            if not top or q in way:
                unplaced.append(dplan.mark_unplaced(
                    {**item, 'cells': [list(q)], 'id': f"{item.get('id', kind)}-{q[0]}-{q[1]}"},
                    'on road/path/water or no surface',
                ))
                continue
            if on_carriageway(world, q[0], q[1]):
                unplaced.append(dplan.mark_unplaced(
                    {**item, 'cells': [list(q)], 'id': f"{item.get('id', kind)}-{q[0]}-{q[1]}"},
                    'on carriageway',
                ))
                continue
            if top[1].endswith('_carpet'):
                unplaced.append(dplan.mark_unplaced(
                    {**item, 'cells': [list(q)], 'id': f"{item.get('id', kind)}-{q[0]}-{q[1]}"},
                    'on carpet top',
                ))
                continue
            col_ok = False
            excluded = []
            placed_positions = []
            for i in range(1, height + 1):
                pos = (q[0], top[0] + i, q[1])
                reason = ('not air' if not is_air(world.block(*pos)) else
                          'owned block' if pos in owned else
                          'masked block' if pos in masked else
                          'overlapping placement' if pos in found else None)
                if reason:
                    excluded.append({'position': list(pos), 'reason': reason})
                    continue
                found[pos] = mat
                placed_positions.append(list(pos))
                col_ok = True
            if col_ok:
                report.append({
                    'x': q[0], 'z': q[1], 'kind': kind, 'block': mat, 'height': height,
                    'source': (item.get('provenance') or {}).get('source', ''),
                    'plan_id': item.get('id'),
                    'allow_against_build': allow,
                    'placed_positions': placed_positions, 'excluded_positions': excluded,
                })
            else:
                unplaced.append(dplan.mark_unplaced(
                    {**item, 'cells': [list(q)], 'id': f"{item.get('id', kind)}-{q[0]}-{q[1]}",
                     'excluded_positions': excluded},
                    'blocked by live or owned block',
                ))
    return found, report, unplaced


def place_outbuildings(
    cell: tuple[int, int],
    items: Sequence[Mapping[str, Any]],
    world: World,
    *,
    way: set,
    houses: set,
    keep_clear: set,
    owned: set,
    masked: set,
    lidar_hm: Mapping[str, Any] | None = None,
) -> tuple[dict[tuple[int, int, int], str], list[dict], list[dict]]:
    """Place resolved ``outbuilding`` plan items whole, or list them unplaced with a reason (#251).

    An outbuilding is all or nothing: a half-built shed is worse than none. Refused when its
    rectangle leaves the cell, spans more than one plot, sits on a route, clearance column or
    entrance opening (``keep_clear``), a mapped road/path/water column (``way``), a house footprint,
    the carriageway, ground more than two blocks under the live surface (``above lidar ground``),
    or any block that is not air or is owned / masked. Ground height is lidar terrain; without a
    lidar ground the item is unplaced.

    @param cell: (tx, tz)
    @param items: ``outbuilding`` plan items
    @param world: live or planning world
    @param way: mapped road / path / water columns
    @param houses: footprint columns of non-outbuilding buildings and neighbour-owned builds
    @param keep_clear: route, clearance and entrance-opening columns
    @param owned: neighbour-owned block positions
    @param masked: positions owned by another pass mask
    @param lidar_hm: lidar height map with ``terrain``
    @returns: (placements, placed report rows, unplaced items)
    """
    from surface_probe import surface_y

    found: dict[tuple[int, int, int], str] = {}
    report: list[dict] = []
    unplaced: list[dict] = []
    for item in items:
        if item.get('role') != ob.KIND or prov.is_unresolved(item.get('provenance')):
            continue
        cells = [(int(c[0]), int(c[1])) for c in item.get('cells') or []]

        def refuse(reason, _item=item):
            unplaced.append(dplan.mark_unplaced(_item, reason))

        if not cells:
            refuse('no cells')
        elif not all(cell_contains(cell, *q) for q in cells):
            refuse('crosses the cell edge')
        elif len(item.get('plot_ids') or ()) > 1:
            refuse('across a plot line')
        elif any(q in keep_clear for q in cells):
            refuse('on frontage route, clearance or entrance opening')
        elif any(q in way for q in cells):
            refuse('on path/road/water column')
        elif any(q in houses for q in cells):
            refuse('on a house footprint')
        elif any(column_ground_kind(world, *q) in sf.ROAD_KINDS for q in cells):
            refuse('on carriageway')
        elif lidar_hm is None or 'terrain' not in lidar_hm:
            refuse('no lidar ground')
        else:
            ground = {}
            for q in cells:
                i, j = q[0] - int(lidar_hm['x0']), q[1] - int(lidar_hm['z0'])
                inside = 0 <= i < lidar_hm['terrain'].shape[0] and 0 <= j < lidar_hm['terrain'].shape[1]
                ground[q] = int(lidar_hm['terrain'][i, j]) if inside else 0
            if min(ground.values()) <= 0:
                refuse('no lidar ground')
                continue
            tops = {q: surface_y(world, *q) for q in cells}
            if any(t and above_lidar_ground(q[0], t[0], q[1], lidar_hm) for q, t in tops.items()):
                refuse('above lidar ground')
                continue
            if any(t and t[0] < ground[q] - 1 for q, t in tops.items()):
                refuse('below lidar ground')
                continue
            blocks = ob.voxels(item, ground)
            bad = [p for p in blocks if not is_air(world.block(*p)) or p in owned or p in masked or p in found]
            if bad:
                refuse('blocked by live or owned block')
                continue
            found.update(blocks)
            report.append({
                'kind': ob.KIND, 'type': item['type'], 'plan_id': item.get('id'),
                'source': (item.get('provenance') or {}).get('source', ''),
                'confidence': (item.get('provenance') or {}).get('confidence', ''),
                'cells': len(cells), 'size': item.get('size'), 'walls': item.get('walls'),
                'roof': item.get('roof'), 'lidar_height': item.get('lidar_height'),
                'door': item.get('door'), 'blocks': len(blocks),
                'footprint': [list(c) for c in cells],
                'x': min(c[0] for c in cells), 'z': min(c[1] for c in cells)})
    return found, report, unplaced


def plan_cell(cell: tuple[int, int], plan: Plan, world: World):
    """Plan barriers and street furniture for one cell. Returns (placements, stats).

    Builds a decor plan (#150) from aerial, photos, map and designer, then places
    resolved rows. Unresolved plan items and failed placements are listed in stats.
    """
    from ground_sources import OSMGround
    from coverage import OUTBUILDINGS
    from osm_footprints import OSMIndex
    from surface_probe import surface_y

    owned = neighbour_owned(cell, ignored_builds(cell))
    owned_cols = {(x, z) for x, _y, z in owned}
    masked = load_masked_positions()
    idx = OSMIndex()
    fp = set(idx.cells())
    near = fp | owned_cols
    near1 = {(x + a, z + b) for x, z in near for a in (-1, 0, 1) for b in (-1, 0, 1)}
    vmap = OSMGround(cell)
    way = set(vmap.road) | set(vmap.feat) | set(vmap.water) | set(vmap.water_line)  # road/path/water

    from lidar_heightmap import reference_world

    lidar_hm = reference_world().heightmap(cell)
    from ground_sources import cell_box

    decor = dplan.build_decor_plan(
        cell,
        map_barriers={'hedge': set(vmap.hedge), 'wall': set(vmap.wall), 'fence': set(vmap.fence)},
        map_furniture=list(vmap.furniture),
        designer_items=sf.designer_items(cell),
        photo_observations=sf.designer_observations(cell),
        lidar_hm=lidar_hm,
        footprint={q for q in fp if cell_contains(cell, *q)},
        outbuilding_footprints=ob.mapped_outbuilding_footprints(idx, cell_box(cell)),
        lidar_rise=ob.lidar_rise(cell),
        building_columns=set(fp),
    )
    unresolved = list(dplan.unresolved_items(decor))
    world = snapshot_decor_columns(                                      # batch reads (#110)
        world, decor_columns(decor['items'], dplan.resolved_furniture(decor)))

    reserved_gap = {tuple(q) for q in (decor.get('reserved') or {}).get('openings', ())}
    houses = {q for q, k in idx.cells().items()
              if idx.buildings[k][1].get('building') not in OUTBUILDINGS} | owned_cols
    outbuilding_found, outbuilding_report, outbuilding_unplaced = place_outbuildings(
        cell, decor['items'], world, way=way, houses=houses, keep_clear=reserved_gap,
        owned=owned, masked=masked, lidar_hm=lidar_hm)
    way = way | {tuple(c) for r in outbuilding_report for c in r['footprint']}   # interior too: nothing else goes under a roof
    masked = {**masked, **dict.fromkeys(outbuilding_found, 'outbuilding')}   # barriers and furniture fill only what is left
    barrier_items = [
        i for i in decor['items']
        if i.get('role') == 'barrier' and not prov.is_unresolved(i.get('provenance'))
    ]
    found, barrier_report, barrier_unplaced = place_barriers_from_plan(
        cell, barrier_items, world, way=way, near1=near1, owned=owned, masked=masked,
        lidar_hm=lidar_hm,
    )
    present: dict[tuple[int, int, int], str] = {}
    # Re-scan for present standing pieces (join_pieces needs them).
    for item in barrier_items:
        mat = item.get('block') or dplan.DEFAULT_BLOCK.get(item.get('kind') or '', '')
        for raw in item.get('cells') or []:
            q = (int(raw[0]), int(raw[1]))
            if not cell_contains(cell, *q):
                continue
            top = surface_y(world, *q)
            if top and top[1] == mat:
                present[(q[0], top[0], q[1])] = mat
            if top:
                for i in range(1, int(item.get('height') or 1) + 1):
                    pos = (q[0], top[0] + i, q[1])
                    if bare(world.block(*pos)) == f'minecraft:{mat}':
                        present[pos] = mat

    all_furniture = dplan.resolved_furniture(decor)
    furniture_items = [i for i in all_furniture if (int(i['x']), int(i['z'])) not in reserved_gap]
    gap_furniture = [dplan.mark_unplaced(i, 'entrance opening or route reserved')
                     for i in all_furniture if (int(i['x']), int(i['z'])) in reserved_gap]
    furniture, furniture_report, furniture_unplaced = place_furniture(
        cell, furniture_items, world, way=way, near1=near1, owned=owned, masked=masked,
        lidar_hm=lidar_hm,
    )
    # Furniture wins over a barrier block at the same position (rare overlap).
    barrier_positions = len(found)
    overlap_positions = len(found.keys() & furniture.keys())
    found.update(furniture)
    found.update(outbuilding_found)

    stats: Counter = Counter()
    stats['candidate_items'] = len(decor['items'])
    stats['accepted_barrier_columns'] = len(barrier_report)
    stats['accepted_furniture_items'] = len(furniture_report)
    stats['barrier_positions'] = barrier_positions
    stats['furniture_positions'] = len(furniture)
    stats['overlap_positions'] = overlap_positions
    stats['furniture'] = len(furniture_report)
    stats['furniture map'] = sum(1 for r in furniture_report if r['source'] == 'vector_map')
    stats['furniture designer'] = sum(
        1 for r in furniture_report if r['source'] in ('aerial', 'photo_read', 'inference')
    )
    stats['barriers'] = len(barrier_report)
    stats['unresolved'] = len(unresolved)
    stats['outbuildings'] = len(outbuilding_report)
    stats['outbuilding_blocks'] = len(outbuilding_found)
    furniture_unplaced = list(furniture_unplaced) + gap_furniture
    stats['unplaced'] = len(barrier_unplaced) + len(furniture_unplaced) + len(outbuilding_unplaced)

    # Joins may enter near1 only for materials already placed with allow_against_build.
    allow_near1_mats = {
        r['block'] for r in barrier_report if r.get('allow_against_build') and r.get('block')
    }

    def can_place(pos, mat=''):
        if (pos[0], pos[2]) in reserved_gap:
            return False                    # entrance opening: final joins must not close it
        return may_join_at(
            pos, mat, cell=cell, near1=near1, allow_near1_mats=allow_near1_mats,
            owned=owned, masked=masked, world=world,
            lidar_hm=lidar_hm,
        )

    joins = join_pieces(found, lambda p: world.block(*p), can_place, present)
    found.update(joins)
    stats['joins'] = len(joins)
    stats['positions_before_clip'] = len(found)
    clipped, dropped = clip_to_cell(found, *cell)
    stats['clipped_positions'] = [{'position': list(p), 'reason': 'outside cell'} for p in found if p not in clipped]
    for p, b in clipped.items():
        plan.set(*p, b)
    roof_slabs = {p for p in outbuilding_found if p in clipped and clipped[p].endswith('[type=bottom]')}
    plan.keep_slab_cells |= roof_slabs                  # roof slabs stay half height (else rewritten to full blocks)
    unmapped = [r for r in outbuilding_report if r['source'] != 'vector_map']
    if unmapped:                                        # not on a mapped footprint: osm_footprints.guard needs a reason
        plan.outside_footprint_ok = ('garden outbuildings from lidar blob / designer trace evidence: '
                                     + ', '.join(str(r['plan_id']) for r in unmapped))
    stats['dropped_outside_cell'] = dropped
    stats['blocks'] = len(clipped)
    stats['furniture_report'] = furniture_report  # list, not a Counter value; printed by run()
    stats['barrier_report'] = barrier_report
    stats['outbuilding_report'] = outbuilding_report
    stats['unresolved_items'] = unresolved
    stats['unplaced_items'] = barrier_unplaced + furniture_unplaced + outbuilding_unplaced
    stats['decor_plan'] = decor
    return clipped, stats


def _print_plan_notes(stats: dict) -> None:
    """Print counted exclusion reasons; full rows live in the report artifact."""
    from layer_reports import reason_summary
    for key in ('unresolved_items', 'unplaced_items'):
        items = stats.get(key) or []
        reasons = [(i.get('provenance') or {}).get('unresolved_reason', 'unspecified') for i in items]
        print(f'  {key}: {len(items)}; by reason: {reason_summary(reasons)}')
    partial = [p for row in stats.get('barrier_report') or [] for p in row.get('excluded_positions') or []]
    if partial:
        print(f'  barrier position exclusions: {len(partial)}; by reason: {reason_summary(p["reason"] for p in partial)}')


def outbuilding_rows(report, unresolved, unplaced) -> list[dict]:
    """One row per outbuilding candidate: id, type, source, confidence, columns, ``accepted`` or the reason."""
    rows = [{'id': r['plan_id'], 'type': r['type'], 'source': r['source'], 'confidence': r['confidence'],
             'cells': r['cells'], 'size': r['size'], 'height': (r['walls'] or 0) + 1, 'status': 'accepted'}
            for r in report]
    for i in list(unresolved) + list(unplaced):
        if i.get('role') != ob.KIND:
            continue
        p = i.get('provenance') or {}
        rows.append({'id': i.get('id'), 'type': i.get('type'), 'source': p.get('source', ''),
                     'confidence': p.get('confidence', ''), 'cells': len(i.get('cells') or ()),
                     'size': i.get('size'), 'height': i.get('height'),
                     'status': 'unplaced: ' + p.get('unresolved_reason', '')})
    return sorted(rows, key=lambda r: str(r['id']))


def _print_outbuildings(rows) -> None:
    """Print the outbuilding candidate table (source, accepted or the unplaced reason)."""
    print(f'  outbuildings: {sum(r["status"] == "accepted" for r in rows)} accepted of {len(rows)} candidates')
    for r in rows:
        print(f'    {r["id"]}  {r["type"]}  {r["source"]}/{r["confidence"]}  {r["cells"]} columns  {r["status"]}')


def run(cell: tuple[int, int]) -> int:
    """Dry run: plan and run the kit's guards. Never writes the world."""
    from site_kit import apply, fresh_planning_world, preview

    name = plan_name(*cell)
    plan = Plan(name)
    placed, stats = plan_cell(cell, plan, fresh_planning_world())
    from layer_reports import write_report
    write_report(name, {'stats': dict(stats), 'placement_positions': len(placed)})
    furniture_report = stats.pop('furniture_report', [])
    barrier_report = stats.pop('barrier_report', [])
    outbuilding_report = stats.pop('outbuilding_report', [])
    unresolved = stats.pop('unresolved_items', [])
    unplaced = stats.pop('unplaced_items', [])
    stats.pop('decor_plan', None)
    stats.pop('clipped_positions', None)
    kinds = Counter(placed.values())
    print(f'{name}: {dict(stats)}' + (' (empty decor plan)' if not placed else ''))
    print(f'  by block: {dict(kinds)}')
    _print_plan_notes({
        'furniture_report': furniture_report,
        'barrier_report': barrier_report,
        'unresolved_items': unresolved,
        'unplaced_items': unplaced,
    })
    _print_outbuildings(outbuilding_rows(outbuilding_report, unresolved, unplaced))
    if placed:
        preview(plan, ROOT / f'verification/site/{name}-preview.png', scale=2)
        apply(plan, dry=True, modifies=ignored_builds(cell))
    return 0


def apply_cell(cell: tuple[int, int]) -> int:
    """Live apply: lock first, re-plan from the live world, readback, then the mask.

    An empty placement set still journals a landed decor pass (and an empty mask) so
    a cell with nothing to place can finish; tree generation requires decor again; ground redo preserves completion.
    """
    import os
    from site_kit import apply

    if not os.environ.get('SITE_OWNER'):
        raise SystemExit('SITE_OWNER is not set; claim the lock first (scripts/site_lock.py claim <owner> ...)')
    from readiness_evidence import require_ready
    require_ready([cell], 'decor')
    tx, tz = cell
    name = plan_name(tx, tz)
    plan = Plan(name)
    world = World()
    placed, stats = plan_cell(cell, plan, world)
    from layer_reports import write_report
    write_report(name, {'stats': dict(stats), 'placement_positions': len(placed)})
    furniture_report = stats.pop('furniture_report', [])
    barrier_report = stats.pop('barrier_report', [])
    outbuilding_report = stats.pop('outbuilding_report', [])
    unresolved = stats.pop('unresolved_items', [])
    unplaced = stats.pop('unplaced_items', [])
    decor = stats.pop('decor_plan', None)
    stats.pop('clipped_positions', None)
    warm = getattr(world, 'prefetch', None)
    if warm is not None:
        warm(placed)                                                       # mask air check, batched
    mask = build_mask('decor', cell, name, placed, lambda p: is_air(world.block(*p)))
    print(f'{name}: {len(placed)} blocks ({dict(stats)})' + (' (empty decor pass; journal completion retained)' if not placed else ''))
    _print_plan_notes({
        'furniture_report': furniture_report,
        'barrier_report': barrier_report,
        'unresolved_items': unresolved,
        'unplaced_items': unplaced,
    })
    _print_outbuildings(outbuilding_rows(outbuilding_report, unresolved, unplaced))
    meta = {
        'furniture': furniture_report,
        'barriers': barrier_report,
        'outbuildings': outbuilding_report,
        'unresolved': [
            {'id': i.get('id'), 'kind': i.get('kind'),
             'reason': (i.get('provenance') or {}).get('unresolved_reason', '')}
            for i in unresolved
        ],
        'unplaced': [
            {'id': i.get('id'), 'kind': i.get('kind'),
             'reason': (i.get('provenance') or {}).get('unresolved_reason', '')}
            for i in unplaced
        ],
    }
    if decor is not None:
        meta['plan_item_count'] = len(decor.get('items') or [])
    rb = apply(plan, modifies=ignored_builds(cell), stage='decor', cell=cell,
               strict=True, meta=meta, mask=mask)
    print(f'readback: {rb["planned"]} planned, {rb["mismatches"]} mismatches')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cell', required=True, help='tx,tz e.g. 48,-44')
    ap.add_argument('--apply', action='store_true', help='LIVE: needs SITE_OWNER + claimed lock')
    a = ap.parse_args()
    cell = tuple(int(v) for v in a.cell.split(','))
    return apply_cell(cell) if a.apply else run(cell)


if __name__ == '__main__':
    sys.exit(main())
