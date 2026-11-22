#!/usr/bin/env python3
"""Layered decor pass for one 128² cell — HANDOFF-LAYERED-PASSES §1 (pass 4), §2, §3.

Decor builds from a decor plan (#150): aerial/photo/map/designer barriers and street
furniture, each with shared provenance. Barriers sit anywhere but the carriageway; furniture
sits on paving, never on a path-line column. Building-border hedges from the aerial may sit
against a footprint; other barriers and furniture stay clear of builds. Items the plan
cannot resolve are listed, not silently skipped. Placed only where the live block is air
(or a stem/flower, as in §10) and not owned by a neighbour or by another mask, clipped to
the cell. Items stand on the true ground: the block at lidar terrain Y (``surface_probe.ground_at``), never
the live top block, so a roof or deck over the ground is not ground (#301). A column with no solid unowned
block at terrain Y is unplaced ('no ground at terrain'); an item whose own column holds a feature-owned
block from terrain up to its top (the ground block itself included) is unplaced ('feature above terrain'); where terrain is missing the live
surface is used as before. Then any wall or fence pieces of the same type that one extra block could link
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

from ground_layer import (AIRS, BLOCK_KIND, cell_contains, ignored_builds,  # noqa: E402
                          is_air, neighbour_owned, load_masked_positions, probe_y_range)
from block_store import SCAN_VOLUME_CAP, block_id  # noqa: E402
from pass_mask import build_mask, clip_to_cell  # noqa: E402
from site_kit import Plan, World  # noqa: E402
import barrier_vocab as bv  # noqa: E402
import decor_plan as dplan  # noqa: E402
import outbuildings as ob  # noqa: E402
from stage_timing import timed_stage
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


def decor_columns(decor_items, furniture_items) -> set[tuple[int, int]]:
    """Columns the decor plan can read: barrier cells and furniture spots, plus a ring."""
    cols: set[tuple[int, int]] = set()
    for item in decor_items:
        if item.get('role') == 'barrier':
            cols.update((int(c[0]), int(c[1])) for c in item.get('cells') or [])
        elif item.get('role') == ob.KIND:
            cols.update((int(c[0]), int(c[1])) for c in item.get('cells') or [])
    cols.update((int(i['x']), int(i['z'])) for i in furniture_items if i.get('kind') not in sf.ROADSIDE_KINDS)
    r = WARM_RING
    out = {(x + a, z + b) for x, z in cols for a in range(-r, r + 1) for b in range(-r, r + 1)}
    r = sf.ROADSIDE_REACH + WARM_RING                    # a post moves off the road node, so its neighbourhood is read
    for i in furniture_items:
        if i.get('kind') in sf.ROADSIDE_KINDS:
            x, z = int(i['x']), int(i['z'])
            out.update((x + a, z + b) for a in range(-r, r + 1) for b in range(-r, r + 1))
    return out


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


def snapshot_groups(cols, per_col: int, cap: int = SCAN_VOLUME_CAP) -> list[list[tuple[int, int]]]:
    """Pack columns into groups whose bounding box times ``per_col`` fits one MCP scan (#263).

    Columns run chunk-row by chunk-row, west to east, and a group grows until its x/z
    bounding box would pass ``cap`` blocks. Dense runs share a scan (about three chunks
    at 90 levels); sparse far-apart columns shrink to separate small boxes, so a group
    never scans a big empty area.

    @param cols: iterable of ``(x, z)``
    @param per_col: y levels read per column
    @param cap: MCP scan volume limit
    @returns: groups of columns, each one scan and at most ``cap`` positions
    """
    groups: list[list[tuple[int, int]]] = []
    cur: list[tuple[int, int]] = []
    x0 = x1 = z0 = z1 = 0
    for x, z in sorted(set(cols), key=lambda c: (c[1] >> 4, c[0], c[1])):
        if cur:
            nx0, nx1, nz0, nz1 = min(x0, x), max(x1, x), min(z0, z), max(z1, z)
            if (nx1 - nx0 + 1) * (nz1 - nz0 + 1) * per_col <= cap:
                cur.append((x, z))
                x0, x1, z0, z1 = nx0, nx1, nz0, nz1
                continue
            groups.append(cur)
        cur, x0, x1, z0, z1 = [(x, z)], x, x, z, z
    if cur:
        groups.append(cur)
    return groups


def snapshot_decor_columns(world, cols):
    """Read the blocks decor planning needs once, in few bounded bulk scans.

    One up-front warm of every decor column can exceed the live store's cache cap; the
    store then clears itself on the next cold read and every later read is its own paced
    section scan (a cell with a long fence hung for an hour). Here columns are packed
    into groups that each fill one MCP scan (:func:`snapshot_groups`), warmed just
    before they are copied out, so each position is scanned once and the live cache never
    holds more than one group. Per-scan pacing, not volume, dominates (#263: 122 chunk
    scans took 145 s, of which parsing 10 s), so fewer, fuller scans are faster.

    @param world: :class:`World` or any duck; returned unchanged without ``prefetch``
    @param cols: iterable of ``(x, z)``
    @returns: a world-like with ``block`` / ``prefetch`` serving the columns from memory
    """
    if getattr(world, 'prefetch', None) is None:
        return world
    ylo, yhi = probe_y_range()
    blocks: dict[tuple[int, int, int], str | None] = {}
    for group in snapshot_groups(cols, yhi - ylo + 1):
        world.prefetch((x, y, z) for x, z in group for y in range(ylo, yhi + 1))
        for x, z in group:
            for y in range(ylo, yhi + 1):
                blocks[(x, y, z)] = world.block(x, y, z)
    return DecorColumnView(world, blocks)


def column_ground_kind(world: World, x: int, z: int, lidar_hm: Mapping[str, Any] | None = None) -> str | None:
    """Classified ground kind at the ground block of (x, z), or None if there is none.

    Ground is the block at lidar terrain Y, so a roof over the column does not make it a carriageway
    or paving; the live surface is read only where terrain is missing (#301).
    """
    from surface_probe import ground_at

    top = ground_at(world, lidar_hm, x, z)
    if not top:
        return None
    return BLOCK_KIND.get(top[1])


def on_carriageway(world: World, x: int, z: int, lidar_hm: Mapping[str, Any] | None = None) -> bool:
    """True when a barrier column would sit on the carriageway (tarmac, marking, kerb)."""
    return column_ground_kind(world, x, z, lidar_hm) in sf.ROAD_KINDS


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
    keep_clear=frozenset(),
) -> bool:
    """True when ``join_pieces`` may place ``mat`` at ``pos``.

    Footprint-ring columns (``near1``) are banned by default. Only materials that
    already have ``allow_against_build`` placements (building-border hedges) may
    join into that ring. Live block must be air/flower, and the spot must not be
    owned or masked. There is no ground check: a join across a gap or over water is
    kept on purpose (``test_join_pieces_has_no_terrain_veto``, #307).

    @param pos: candidate (x, y, z)
    @param mat: joinable block id being considered
    @param cell: (tx, tz) cell bounds
    @param near1: footprint and structure columns dilated by 1
    @param allow_near1_mats: materials already placed with allow_against_build
    @param owned: neighbour-owned block positions
    @param masked: positions owned by another pass mask
    @param world: live or planning world
    @param keep_clear: shared entrance openings, routes and approach clearance columns
    @returns: whether the join candidate is allowed
    """
    xz = (pos[0], pos[2])
    if xz in keep_clear:
        return False
    if xz in near1 and mat not in allow_near1_mats:
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
                if q not in found and block_id(live(q), policy='read') == f'minecraft:{mat}':
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
    keep_clear: set | frozenset = frozenset(),
    barrier_voxels: Mapping[tuple[int, int, int], str] | None = None,
) -> tuple[dict[tuple[int, int, int], str], list[dict], list[dict]]:
    """Place street-furniture recipes on any non-carriageway surface column.

    A kind is a footprint of columns about the item's anchor (:func:`street_furniture.footprint`; a
    single-column kind is one column, a picnic table nine), placed whole or refused whole
    (``docs/specs/decor-furniture-footprints.md``, #305). Every footprint column must pass: inside the
    cell (``crosses the cell edge`` when only the anchor is), off path-line columns (``way``), off
    columns against a build (``near1``; ``allow_against_build`` lifts it), off ``keep_clear``
    (route, clearance and entrance-opening columns), off the carriageway (road, marking, kerb) and on
    lidar ground within one block of the anchor's (``uneven ground``). Each voxel must be air, not
    owned or masked, not already taken by another furniture item (``overlaps another furniture
    item``) and, for a multi-block kind, not a planned barrier block (``overlaps a planned barrier``).
    Failed rows are returned as unplaced plan-shaped dicts, with ``blocked_column`` on a multi-block one.

    Road-edge kinds (#334): ``traffic_signals`` and ``crossing`` come from OSM nodes on the carriageway, so
    each stands at the nearest column (within :data:`street_furniture.ROADSIDE_REACH` blocks) that passes
    every check above, reported with ``placed_at`` (``x``, ``z`` stay the mapped node) or refused with
    ``no kerbside column within N blocks``. ``traffic_island`` (mapped only) skips the path-line and
    carriageway refusals and stands where mapped.

    @param cell: (tx, tz)
    @param items: ``{x,z,kind,source}`` from the decor plan; ``rotation`` (degrees clockwise) for a multi-block kind
    @param world: live or planning world
    @param way: mapped road / path / water columns
    @param near1: footprint and structure columns dilated by 1
    @param owned: neighbour-owned block positions
    @param masked: positions owned by another pass mask
    @param lidar_hm: lidar height map; items stand on the block at terrain Y (:func:`surface_probe.ground_at`),
                     not on a roof over it; the live surface is used only where terrain is missing
    @param keep_clear: columns no furniture may use (entrance openings, door routes, clearance)
    @param barrier_voxels: blocks already planned by the barrier pass; a multi-block item refuses to replace them
    @returns: (placements, report rows that were placed, unplaced rows with reasons)
    """
    from surface_probe import ground_at, ground_missing_reason, raised_over_terrain, terrain_y

    found: dict[tuple[int, int, int], str] = {}
    report: list[dict] = []
    unplaced: list[dict] = []

    def attempt(item, x, z):
        """Try one anchor column. Returns ``(voxels, tops, columns, None, None)`` or ``(None, None, columns, reason, column)``."""
        kind = item['kind']
        allow = bool(item.get('allow_against_build'))
        multi = kind in sf.FOOTPRINTS
        rotation = int(item.get('rotation') or 0) % 360 if multi else 0
        rows = sf.footprint(kind, rotation)
        columns = list(dict.fromkeys((x + dx, z + dz) for dx, dz, _dy, _block in rows))
        if not cell_contains(cell, x, z):
            return None, None, columns, 'outside cell', None
        edge = next((q for q in columns if not cell_contains(cell, *q)), None)
        if edge is not None:
            return None, None, columns, 'crosses the cell edge', edge
        checks = [(lambda q: q in keep_clear, 'entrance opening or route reserved')]
        if kind not in sf.ON_ROAD_KINDS:
            checks.append((lambda q: q in way, 'on path/road/water column'))
        checks.append((lambda q: q in near1 and not allow, 'against build'))
        if kind not in sf.ON_ROAD_KINDS:
            checks.append((lambda q: column_ground_kind(world, *q, lidar_hm) in sf.ROAD_KINDS, 'on carriageway'))
        for test, reason in checks:
            bad = next((q for q in columns if test(q)), None)
            if bad is not None:
                return None, None, columns, reason, bad
        tops: dict[tuple[int, int], int] = {}
        for q in columns:
            top = ground_at(world, lidar_hm, q[0], q[1], owned, masked)
            if not top:
                return None, None, columns, ground_missing_reason(lidar_hm, q[0], q[1], owned), q
            tops[q] = top[0]
        if any(abs(tops[q] - tops[(x, z)]) > 1 for q in columns):
            return None, None, columns, 'uneven ground', next(q for q in columns if abs(tops[q] - tops[(x, z)]) > 1)
        voxels: dict[tuple[int, int, int], str] = {}
        for dx, dz, dy, block in rows:
            pos = (x + dx, tops[(x + dx, z + dz)] + dy, z + dz)
            why = ''
            if pos in owned:
                why = 'feature above terrain'              # a feature's own block stands in this column over the ground
            elif pos in found:
                why = 'overlaps another furniture item'
            elif multi and pos in (barrier_voxels or ()):
                why = 'overlaps a planned barrier'
            elif not is_air(world.block(*pos)) or pos in masked:
                why = 'blocked by live or owned block'
            if why:
                return None, None, columns, why, (pos[0], pos[2])
            voxels[pos] = block
        return voxels, tops, columns, None, None

    for item in items:
        x, z, kind = int(item['x']), int(item['z']), item['kind']
        multi = kind in sf.FOOTPRINTS
        rotation = int(item.get('rotation') or 0) % 360 if multi else 0
        voxels, tops, columns, why, bad = attempt(item, x, z)
        at = (x, z)
        if voxels is None and kind in sf.ROADSIDE_KINDS and why != 'outside cell':
            # The mapped node is on the road: stand the post at the nearest column the checks accept.
            for q in sf.roadside_candidates(x, z):
                voxels, tops, columns, _why, _bad = attempt(item, *q)
                if voxels is not None:
                    at = q
                    break
            else:
                why, bad = f'no kerbside column within {sf.ROADSIDE_REACH} blocks', None
        if voxels is None:
            row = dplan.mark_unplaced(item, why)
            if multi and bad is not None:
                row['blocked_column'] = list(bad)
            unplaced.append(row)
            continue
        found.update(voxels)
        row = {'x': x, 'z': z, 'kind': kind, 'source': item.get('source', 'map'),
               'blocks': len(voxels)}
        if at != (x, z):
            row['placed_at'] = list(at)                    # x, z stay the mapped node
        if multi:
            row['rotation'] = rotation
            row['footprint'] = [list(q) for q in columns]
        if terrain_y(lidar_hm, *at) is not None:
            row['on_terrain'] = True
            row['moved_to_terrain'] = any(
                terrain_y(lidar_hm, *q) is not None and raised_over_terrain(world, q[0], q[1], tops[q])
                for q in columns)
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

    Which columns exist, and their kind, block and height, come from
    :func:`decor_plan.resolved_barriers`. Building-border hedges with
    ``allow_against_build`` may occupy the near1 ring; other barriers still skip
    it. Columns that cannot be placed are returned as unplaced copies with an
    unresolved reason.

    @param cell: (tx, tz)
    @param items: barrier role plan items
    @param world: live or planning world
    @param way: mapped road / path / water columns
    @param near1: footprint and structure columns dilated by 1
    @param owned: neighbour-owned block positions
    @param masked: positions owned by another pass mask
    @param lidar_hm: lidar height map; barriers stand on the block at terrain Y (:func:`surface_probe.ground_at`)
    @returns: (placements, placed report rows, unplaced items)
    """
    from surface_probe import ground_at, ground_missing_reason, raised_over_terrain, terrain_y

    found: dict[tuple[int, int, int], str] = {}
    report: list[dict] = []
    unplaced: list[dict] = []
    sources: dict[tuple[int, int], Mapping[str, Any]] = {}
    columns = dplan.resolved_barriers({'items': list(items)}, unplaced, sources)
    for item in items:
        if item.get('role') != 'barrier' or item.get('cells'):
            continue
        if prov.is_unresolved(item.get('provenance')) or item.get('kind') not in bv.BARRIER_KINDS:
            continue
        unplaced.append(dplan.mark_unplaced(item, 'no cells'))
    for q in sorted(columns):
        kind, mat, height = columns[q]
        item = sources[q]
        allow = bool(item.get('allow_against_build'))
        facings = item.get('facing') or {}              # '{x},{z}' -> outward direction (plank panels)
        bases = item.get('facing_basis') or {}
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
        top = ground_at(world, lidar_hm, *q, owned, masked)
        if not top or q in way:
            unplaced.append(dplan.mark_unplaced(
                {**item, 'cells': [list(q)], 'id': f"{item.get('id', kind)}-{q[0]}-{q[1]}"},
                'on road/path/water or no surface' if q in way or terrain_y(lidar_hm, *q) is None
                else ground_missing_reason(lidar_hm, *q, owned),
            ))
            continue
        if on_carriageway(world, q[0], q[1], lidar_hm):
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
        facing = facings.get(f'{q[0]},{q[1]}') if kind == 'plank_fence' else None
        state = dplan.shelf_block(mat, facing) if kind == 'plank_fence' else mat
        for i in range(1, height + 1):
            pos = (q[0], top[0] + i, q[1])
            reason = ('not air' if not is_air(world.block(*pos)) else
                      'owned block' if pos in owned else
                      'masked block' if pos in masked else
                      'overlapping placement' if pos in found else None)
            if reason:
                excluded.append({'position': list(pos), 'reason': reason})
                continue
            found[pos] = state
            placed_positions.append(list(pos))
            col_ok = True
        if col_ok:
            report.append({
                'x': q[0], 'z': q[1], 'kind': kind, 'block': mat, 'height': height,
                **({'state': state, 'facing': facing,
                    'facing_basis': bases.get(f'{q[0]},{q[1]}') or 'no outward evidence: solid plank'}
                   if kind == 'plank_fence' else {}),
                'source': (item.get('provenance') or {}).get('source', ''),
                'provenance': dict(item.get('provenance') or {}),
                'scope': dict(item.get('scope') or {}),
                'reason': item.get('reason', ''),
                'plan_id': item.get('id'),
                'allow_against_build': allow,
                'placed_positions': placed_positions, 'excluded_positions': excluded,
                **({'on_terrain': True, 'moved_to_terrain': raised_over_terrain(world, q[0], q[1], top[0])}
                   if terrain_y(lidar_hm, *q) is not None else {}),
                **({'partial': True} if excluded else {}),   # placed, but some of the column was refused
            })
        else:
            unplaced.append(dplan.mark_unplaced(
                {**item, 'cells': [list(q)], 'id': f"{item.get('id', kind)}-{q[0]}-{q[1]}",
                 'excluded_positions': excluded},
                'feature above terrain' if any(tuple(e['position']) in owned for e in excluded)
                else 'blocked by live or owned block',
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
    rectangle leaves the cell, spans plots (no plot holds half of it), sits on a route, clearance column or
    entrance opening (``keep_clear``), a mapped road/path/water column (``way``), a house footprint,
    the carriageway, a column with no solid unowned block at lidar terrain Y (``no ground at terrain``),
    or any block that is not air or is owned / masked (``feature above terrain`` when a feature owns it).
    Ground height is lidar terrain, read through :func:`surface_probe.ground_at`; without a lidar ground
    the item is unplaced.

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
    from surface_probe import ground_at, ground_missing_reason

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
        elif len(item.get('plot_ids') or ()) > 1 and float(item.get('plot_share', 0)) < ob.PLOT_MAJORITY:
            refuse('across a plot line')
        elif any(q in keep_clear for q in cells):
            refuse('on frontage route, clearance or entrance opening')
        elif any(q in way for q in cells):
            refuse('on path/road/water column')
        elif any(q in houses for q in cells):
            refuse('on a house footprint')
        elif any(column_ground_kind(world, *q, lidar_hm) in sf.ROAD_KINDS for q in cells):
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
            missing = [q for q in cells if not ground_at(world, lidar_hm, *q, owned, masked)]
            if missing:
                refuse(ground_missing_reason(lidar_hm, *missing[0], owned))
                continue
            blocks = ob.voxels(item, ground)
            bad = [p for p in blocks if not is_air(world.block(*p)) or p in owned or p in masked or p in found]
            if bad:
                refuse('feature above terrain' if any(p in owned for p in bad) else 'blocked by live or owned block')
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


def half_slab_positions(placed: Mapping[tuple[int, int, int], str],
                        clipped: Mapping[tuple[int, int, int], str]) -> set[tuple[int, int, int]]:
    """Positions of ``placed`` that survive clipping as bottom-half slabs.

    They go in ``plan.keep_slab_cells``; otherwise ``rewrite_bottom_half_slabs`` turns each into a full block.
    """
    return {p for p in placed if p in clipped and clipped[p].endswith('[type=bottom]')}


def furniture_by_provenance(decor: Mapping[str, Any], report: list[dict], unplaced: list[dict]) -> dict:
    """Furniture counts by provenance source, so an inferred item never reads as observed (#260).

    @param decor: decor plan
    @param report: placed furniture rows (``source`` is the provenance source)
    @param unplaced: furniture rows that failed placement or were held back, with reasons
    @returns: ``{'planned': {...}, 'placed': {...}, 'unplaced': {...}}``, each ``{source: count}``
    """
    def source(row):
        return (row.get(prov.PROVENANCE_KEY) or {}).get('source') or row.get('source') or 'unknown'

    return {
        'planned': dict(Counter(source(i) for i in decor.get('items') or []
                                if i.get('role') == 'furniture' and not prov.is_unresolved(i.get(prov.PROVENANCE_KEY)))),
        'placed': dict(Counter(source(r) for r in report)),
        'unplaced': dict(Counter(source(r) for r in unplaced)),
    }


# Woodland understorey (#351): land_use.WOOD_TAGS and SCRUB_TAGS take woodland_decor when the class ships it.
# Exclusive roll order after bushes; shares come from config/land-use-classes.json.
WOODLAND_PLANT_ROLL = (
    'fern', 'large_fern', 'short_grass', 'short_dry_grass', 'tall_dry_grass', 'leaf_litter',
)
_LEAF_LITTER_FACINGS = ('north', 'south', 'east', 'west')


def _land_class_id(hit) -> str | None:
    """Class id from a ``land_class_columns`` string or an ``area_classes`` tuple.

    ``plan_cell`` passes the string map (park-umbrella aware). Tests and the
    ``area_classes`` fallback still use ``(class, ref, kind)``.

    @param hit: class id string, ``(class, …)`` tuple, or None
    @returns: class id, or None when absent
    """
    if hit is None:
        return None
    if isinstance(hit, str):
        return hit
    if isinstance(hit, (tuple, list)) and hit:
        return hit[0]
    return None


def woodland_decor_columns(cell: tuple[int, int], idx, classes: dict | None = None) -> dict:
    """``{column: class id}`` for woodland understorey columns gated by OSM tag (#351).

    Eligible when the column's land-use class ships ``woodland_decor``, the column sits in
    ``natural=wood``, ``landuse=forest``, or (when that class sets ``scrub`` true)
    ``natural=scrub``, and it is not a lawn. Does not use ``planting`` (null when
    ``frontage.inference`` is false).

    @param cell: ``(tx, tz)``
    @param idx: ``OSMIndex``
    @param classes: ``land_class_columns`` map (``{column: class id}``) or
        ``area_classes`` map (``{column: (class, ref, kind)}``); when omitted,
        ``area_classes`` is loaded for the cell box
    @returns: eligible columns (empty when no class ships woodland decor)
    """
    import land_use
    from coverage import scan_polygon
    import cellmath
    from ground_sources import MARGIN
    from ground_layer import LAWN_TAGS
    from osm_footprints import osm_tags

    if classes is None:
        from coverage import area_classes
        classes = area_classes(idx, cellmath.bounds(cell, MARGIN))
    rules = {}
    for cid in land_use.CLASSES:
        rule = land_use.woodland_decor(cid)
        if rule:
            rules[cid] = rule
    if not rules:
        return {}
    bbox = cellmath.bounds(cell, MARGIN)
    lawn: set = set()
    wood: set = set()
    scrub: set = set()
    areas = getattr(idx, 'areas', None)
    if not isinstance(areas, (list, tuple)):
        areas = ()
    for rings, props in areas:
        tags = osm_tags(props)
        pairs = [(k, tags[k]) for k in ('landuse', 'leisure', 'natural') if tags.get(k)]
        if any(pair in LAWN_TAGS for pair in pairs):
            lawn.update(scan_polygon(rings, bbox))
            continue
        if any(pair in land_use.WOOD_TAGS for pair in pairs):
            wood.update(scan_polygon(rings, bbox))
        if any(pair in land_use.SCRUB_TAGS for pair in pairs):
            scrub.update(scan_polygon(rings, bbox))
    wood -= lawn
    scrub -= lawn
    eligible = {}
    for q in wood:
        cid = _land_class_id(classes.get(q))
        if cid in rules:
            eligible[q] = cid
    for q in scrub:
        if q in wood:
            continue
        cid = _land_class_id(classes.get(q))
        if not cid or cid not in rules or not rules[cid].get('scrub'):
            continue
        eligible[q] = cid
    return eligible


def _tree_solid(block: str | None) -> bool:
    """True when a live block is a trunk or crown solid that understorey must not enter.

    @param block: full or short block id
    @returns: whether the voxel is owned by a tree body
    """
    if not block or is_air(block):
        return False
    name = block_id(block, policy='read')
    short = name.split(':')[-1]
    return '_log' in short or 'leaves' in short or short.endswith('_wood')


def _woodland_plant_block(kind: str, rnd) -> str | tuple[str, str]:
    """Block id (or lower/upper pair) for one woodland ground-cover kind.

    ``leaf_litter`` uses the historical facing/segment_amount shape from older Mojang
    assets; 26.2 states remain unverified (#347), so the shipped share stays 0 until #318.

    @param kind: plant kind key
    @param rnd: seeded RNG for litter facing
    @returns: one block string, or ``(lower, upper)`` for two-block ferns
    """
    if kind == 'fern':
        return 'fern'
    if kind == 'large_fern':
        return ('large_fern[half=lower]', 'large_fern[half=upper]')
    if kind == 'short_grass':
        return 'short_grass'
    if kind == 'short_dry_grass':
        return 'short_dry_grass'
    if kind == 'tall_dry_grass':
        return 'tall_dry_grass'
    if kind == 'leaf_litter':
        facing = _LEAF_LITTER_FACINGS[rnd.randrange(4)]
        return f'leaf_litter[facing={facing},segment_amount=1]'
    raise ValueError(f'unknown woodland plant kind: {kind}')


def plan_woodland_decor(
    plan: Plan,
    world: World,
    cell: tuple[int, int],
    columns: dict,
    *,
    lidar_hm=None,
    masked=(),
    owned=(),
    clear=(),
    way=(),
    near1=(),
    taken=(),
) -> tuple[dict, dict]:
    """Place woodland understorey bushes and ground cover inside wood polygons (#351).

    Driven by ``woodland_decor`` shares, not by ``planting``. One kind per column, seeded
    per cell and column. Bushes reuse :func:`tree_voxels.bush_voxels` (height 1 or 2
    from ``bush_tall``). Skips roads, water, footprints, clearance, neighbour/feature ownership,
    tree and other pass masks, and any voxel that is not air or that sits in a trunk or crown.
    Counts land in the decor report per kind; placements join the decor mask.

    @param plan: decor plan under construction
    @param world: live world reader (after trees)
    @param cell: ``(tx, tz)`` for seeds
    @param columns: ``{column: class id}`` from :func:`woodland_decor_columns`
    @param lidar_hm: lidar heightmap for terrain Y
    @param masked: tree/decor/other mask positions
    @param owned: feature and neighbour owned positions
    @param clear: reserved route, opening and footprint columns
    @param way: road, path and water columns
    @param near1: building-neighbour clearance columns
    @param taken: positions already claimed by this decor pass
    @returns: ``(placements, report)`` with per-kind counts and item rows
    """
    import random
    import land_use
    import provenance as prov
    from tree_voxels import bush_voxels
    from surface_probe import ground_at

    report = {k: 0 for k in ('bushes',) + WOODLAND_PLANT_ROLL}
    report.update({'skipped': 0, 'items': []})
    found: dict[tuple[int, int, int], str] = {}
    if not columns:
        return found, report
    tx, tz = cell
    protected = set(masked) | set(owned) | set(taken)
    blocked = set(clear) | set(way) | set(near1) | {(x, z) for x, _y, z in protected}
    rules: dict = {}

    def live(p):
        return found[p] if p in found else (plan.cells[p] if p in plan.cells else world.block(*p))

    def free_air(p):
        if p in protected or p in found:
            return False
        block = live(p)
        return block is not None and block_id(block, policy='read') in AIRS

    for q in sorted(columns):
        if q in blocked or not cell_contains(cell, *q):
            report['skipped'] += 1
            continue
        x, z = q
        top = ground_at(world, lidar_hm, x, z, owned, masked)
        if not top:
            report['skipped'] += 1
            continue
        gy, ground = top
        if _tree_solid(ground) or block_id(ground, policy='read').endswith('water'):
            report['skipped'] += 1
            continue
        cid = columns[q]
        if cid not in rules:
            rules[cid] = land_use.woodland_decor(cid)
        rule = rules[cid]
        if not rule:
            report['skipped'] += 1
            continue
        rnd = random.Random(f'woodland_decor:{tx}:{tz}:{x}:{z}')
        roll = rnd.random()
        kind = None
        if roll < rule['bushes']:
            kind = 'bushes'
        else:
            cursor = rule['bushes']
            for name in WOODLAND_PLANT_ROLL:
                cursor += rule[name]
                if roll < cursor:
                    kind = name
                    break
        if kind is None:
            continue
        if kind == 'bushes':
            tall = rnd.random() < rule['bush_tall']
            # Reuse bush_voxels for leaf id and Y+1/+2; keep only this column so neighbours
            # do not fight over the clump's cardinals (trial: 1 or 2 leaf blocks high).
            voxels = {p: b for p, b in bush_voxels(x, z, gy).items() if p[0] == x and p[2] == z}
            if not tall:
                voxels.pop((x, gy + 2, z), None)
            if not voxels or any(not free_air(pos) or _tree_solid(live(pos)) for pos in voxels):
                report['skipped'] += 1
                continue
            for pos, block in voxels.items():
                found[pos] = block
                plan.set(*pos, block)
            report['bushes'] += 1
            report['items'].append({
                'kind': 'bushes', 'position': [x, gy + 1, z], 'block': 'oak_leaves',
                'tall': tall, 'label': 'woodland understorey bush (#351)',
                'provenance': prov.provenance_record(source='inference', confidence='low'),
            })
            continue
        plant = (x, gy + 1, z)
        if not free_air(plant):
            report['skipped'] += 1
            continue
        block = _woodland_plant_block(kind, rnd)
        if isinstance(block, tuple):
            upper = (x, gy + 2, z)
            if not free_air(upper):
                report['skipped'] += 1
                continue
            lower_b, upper_b = block
            found[plant] = lower_b
            found[upper] = upper_b
            plan.set(*plant, lower_b)
            plan.set(*upper, upper_b)
            placed_block = lower_b.split('[')[0]
        else:
            found[plant] = block
            plan.set(*plant, block)
            placed_block = block.split('[')[0]
        report[kind] += 1
        report['items'].append({
            'kind': kind, 'position': list(plant), 'block': placed_block,
            'label': f'woodland ground cover {kind} (#351)',
            'provenance': prov.provenance_record(source='inference', confidence='low'),
        })
    return found, report


def plan_cell(cell: tuple[int, int], plan: Plan, world: World, *, masked: dict | None = None):
    """Plan barriers, street furniture and woodland understorey for one cell.

    Builds a decor plan (#150) from aerial, photos, map and designer, then places
    resolved rows plus woodland decor (#351). Unresolved plan items and failed
    placements are listed in stats.
    """
    from ground_sources import OSMGround
    from coverage import OUTBUILDINGS
    from osm_footprints import OSMIndex
    from surface_probe import ground_at

    with timed_stage('decor source preparation'):
        owned = neighbour_owned(cell, ignored_builds(cell))
        owned_cols = {(x, z) for x, _y, z in owned}
        masked = load_masked_positions() if masked is None else masked
        idx = OSMIndex()
        fp = set(idx.cells())
        near = fp | owned_cols
        near1 = {(x + a, z + b) for x, z in near for a in (-1, 0, 1) for b in (-1, 0, 1)}
        vmap = OSMGround(cell)
        way = set(vmap.road) | set(vmap.feat) | set(vmap.water) | set(vmap.water_line)  # road/path/water

        from lidar_heightmap import reference_world

        lidar_hm = reference_world().heightmap(cell)
        import cellmath

        land_classes = dplan.land_class_columns(idx, cellmath.bounds(cell, 12))
        wood_columns = woodland_decor_columns(cell, idx, land_classes)
        decor = dplan.build_decor_plan(
            cell,
            map_barriers={'hedge': set(vmap.hedge), 'wall': set(vmap.wall), 'fence': set(vmap.fence)},
            map_furniture=list(vmap.furniture),
            designer_items=sf.designer_items(cell),
            photo_observations=sf.designer_observations(cell),
            lidar_hm=lidar_hm,
            footprint={q for q in fp if cell_contains(cell, *q)},
            outbuilding_footprints=ob.mapped_outbuilding_footprints(idx, cellmath.bounds(cell)),
            lidar_rise=ob.lidar_rise(cell),
            building_columns=set(fp),
            road_lines=vmap.road_lines,
            path_lines=vmap.path_lines,
            land_classes=land_classes,
            carriageway=set(vmap.road) | set(vmap.marks),
            furniture_avoid=way | near1,
            water=set(vmap.water) | set(vmap.water_line),
        )
    unresolved = list(dplan.unresolved_items(decor))
    with timed_stage('decor surface warm-up'):
        warm_cols = decor_columns(decor['items'], dplan.resolved_furniture(decor)) | set(wood_columns)
        world = snapshot_decor_columns(world, warm_cols)                      # batch reads (#110)

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
        mat = item.get('block') or bv.DEFAULT_BLOCK.get(item.get('kind') or '', '')
        if item.get('kind') == 'plank_fence':           # panels stand as shelves: same wood, other block id
            mat = dplan.shelf_block(mat, 'north').split('[')[0] if item.get('facing') else mat
        for raw in item.get('cells') or []:
            q = (int(raw[0]), int(raw[1]))
            if not cell_contains(cell, *q):
                continue
            top = ground_at(world, lidar_hm, *q)
            if top and top[1] == mat:
                present[(q[0], top[0], q[1])] = mat
            if top:
                for i in range(1, int(item.get('height') or 1) + 1):
                    pos = (q[0], top[0] + i, q[1])
                    if block_id(world.block(*pos), policy='read') == f'minecraft:{mat}':
                        present[pos] = mat

    furniture, furniture_report, furniture_unplaced = place_furniture(
        cell, dplan.resolved_furniture(decor), world, way=way, near1=near1, owned=owned, masked=masked,
        lidar_hm=lidar_hm, keep_clear=reserved_gap, barrier_voxels=found,
    )
    # Furniture wins over a barrier block at the same position (rare overlap).
    barrier_positions = len(found)
    overlap_positions = len(found.keys() & furniture.keys())
    found.update(furniture)
    found.update(outbuilding_found)
    woodland_found, woodland_report = plan_woodland_decor(
        plan, world, cell, wood_columns, lidar_hm=lidar_hm, masked=masked, owned=owned,
        clear=reserved_gap | set(fp), way=way, near1=near1, taken=found,
    )
    found.update(woodland_found)

    stats: Counter = Counter()
    stats['candidate_items'] = len(decor['items'])
    stats['accepted_barrier_columns'] = len(barrier_report)
    stats['accepted_furniture_items'] = len(furniture_report)
    stats['barrier_positions'] = barrier_positions
    stats['furniture_positions'] = len(furniture)
    stats['overlap_positions'] = overlap_positions
    stats['furniture'] = len(furniture_report)
    stats['furniture map'] = sum(1 for r in furniture_report if r['source'] == 'vector_map')
    stats['furniture designer'] = sum(1 for r in furniture_report if r['source'] in ('aerial', 'photo_read'))
    stats['furniture inference'] = sum(1 for r in furniture_report if r['source'] == 'inference')
    stats['barriers'] = len(barrier_report)
    stats['unresolved'] = len(unresolved)
    stats['moved_to_terrain'] = (sum(1 for r in furniture_report if r.get('moved_to_terrain'))
                                 + sum(1 for r in barrier_report if r.get('moved_to_terrain')))
    # no lidar terrain for the column: the live surface was used (rows carry on_terrain only with terrain)
    stats['terrain_fallback'] = (sum(1 for r in furniture_report if not r.get('on_terrain'))
                                 + sum(1 for r in barrier_report if not r.get('on_terrain')))
    stats['barrier_partial_columns'] = sum(1 for r in barrier_report if r.get('partial'))
    stats['outbuildings'] = len(outbuilding_report)
    stats['outbuilding_blocks'] = len(outbuilding_found)
    stats['woodland_decor'] = {k: woodland_report[k] for k in woodland_report if k != 'items'}
    stats['woodland_decor_items'] = woodland_report.get('items') or []
    furniture_unplaced = list(furniture_unplaced)
    stats['furniture_by_provenance'] = furniture_by_provenance(decor, furniture_report, furniture_unplaced)
    if decor.get('furniture_inference') is not None:
        stats['furniture_inference'] = decor['furniture_inference']
    stats['unplaced'] = len(barrier_unplaced) + len(furniture_unplaced) + len(outbuilding_unplaced)

    # Joins may enter near1 only for materials already placed with allow_against_build.
    allow_near1_mats = {
        r['block'] for r in barrier_report if r.get('allow_against_build') and r.get('block')
    }

    def can_place(pos, mat=''):
        return may_join_at(
            pos, mat, cell=cell, near1=near1, allow_near1_mats=allow_near1_mats,
            owned=owned, masked=masked, world=world,
            keep_clear=reserved_gap,
        )

    joins = join_pieces(found, lambda p: world.block(*p), can_place, present)
    found.update(joins)
    stats['joins'] = len(joins)
    stats['positions_before_clip'] = len(found)
    clipped, dropped = clip_to_cell(found, *cell)
    stats['clipped_positions'] = [{'position': list(p), 'reason': 'outside cell'} for p in found if p not in clipped]
    for p, b in clipped.items():
        plan.set(*p, b)
    roof_slabs = half_slab_positions(outbuilding_found, clipped)
    bench_slabs = half_slab_positions(furniture, clipped)
    plan.keep_slab_cells |= roof_slabs | bench_slabs    # roof and bench slabs stay half height (else rewritten to full blocks)
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
    dplan.print_unresolved_barriers(stats.get('unresolved_items') or [])
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
    from layer_planning import plan_after_undo
    from layer_reports import artifact_path, write_report

    name = plan_name(*cell)
    plan = Plan(f'{name}-dry', input_cells=[cell])
    undo = plan_after_undo('decor', cell, fresh_planning_world())
    if undo.conflicts:
        print(f'  {len(undo.conflicts)} decor undo conflicts; planning against the unmodified world')
    placed, stats = plan_cell(cell, plan, undo.world, masked=undo.masked)
    write_report(name, {'stats': dict(stats), 'placement_positions': len(placed)}, dry=True)
    furniture_report = stats.pop('furniture_report', [])
    barrier_report = stats.pop('barrier_report', [])
    outbuilding_report = stats.pop('outbuilding_report', [])
    unresolved = stats.pop('unresolved_items', [])
    unplaced = stats.pop('unplaced_items', [])
    stats.pop('decor_plan', None)
    stats.pop('clipped_positions', None)
    stats.pop('woodland_decor_items', None)
    kinds = Counter(placed.values())
    by_provenance = stats.pop('furniture_by_provenance', None)
    inference = stats.pop('furniture_inference', None)
    woodland = stats.pop('woodland_decor', None)
    print(f'{name}: {dict(stats)}' + (' (empty decor plan)' if not placed else ''))
    if by_provenance is not None:
        print(f'  furniture by provenance: {by_provenance}')
    if inference is not None:
        print(f'  furniture inference: {inference}')
    if woodland is not None:
        print(f'  woodland decor: {woodland}')
    print(f'  by block: {dict(kinds)}')
    _print_plan_notes({
        'furniture_report': furniture_report,
        'barrier_report': barrier_report,
        'unresolved_items': unresolved,
        'unplaced_items': unplaced,
    })
    _print_outbuildings(outbuilding_rows(outbuilding_report, unresolved, unplaced))
    if placed:
        preview(plan, artifact_path(name, 'preview.png', dry=True), scale=2)
        # the simulated undo reads this pass's own earlier build, so its name must be in `modifies`
        apply(plan, dry=True, modifies=(*ignored_builds(cell), name))
    else:
        print('  no preview written: empty decor plan')
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
    tx, tz = cell
    name = plan_name(tx, tz)
    require_ready([cell], 'decor', redoing=(name,))    # a re-apply may pass its own mismatch (#284)
    plan = Plan(name, input_cells=[cell])
    world = World()
    with timed_stage('layer planning (includes surface warm-up)'):
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
    stats.pop('woodland_decor_items', None)
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
