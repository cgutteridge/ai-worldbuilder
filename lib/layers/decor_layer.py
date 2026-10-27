#!/usr/bin/env python3
"""Layered decor pass for one 128² cell — HANDOFF-LAYERED-PASSES §1 (pass 4), §2, §3.

Decor places OSM-mapped boundary features (hedges, brick walls, iron-bar fences) plus
street furniture (benches, bins, bike racks, lamp posts, bollards, signs) from mapped
points or a designer spec (``reference/decor-furniture.json``). Barriers sit on soft
ground; furniture sits on paving, never on a path-line column or against a build.
Placed only where the live block is air (or a stem/flower, as in §10) and not owned by a
neighbour or by another mask, clipped to the cell. Then any wall or fence pieces of the same
type that one extra block could link (face to face, diagonal is disjoint) are joined, one
block per join, repeating until none remain; supported spots first, air/flowers only. Writes a decor mask (§3) only after a
clean readback. An empty plan (nothing to place) still lands so the cell can finish (#127).

  python3 scripts/decor_layer.py --cell 48,-44            # dry run
  SITE_OWNER=<owner> python3 scripts/decor_layer.py --cell 48,-44 --apply
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

from script_env import ROOT  # noqa: E402

from ground_layer import (BLOCK_KIND, bare, cell_contains, ignored_builds, is_air, neighbour_owned,  # noqa: E402
                          load_masked_positions, warm_surface_probe)
from pass_mask import build_mask, clip_to_cell  # noqa: E402
from site_kit import Plan, World  # noqa: E402
import street_furniture as sf  # noqa: E402


def plan_name(tx: int, tz: int) -> str:
    """Build name, e.g. highfield_48_m44_decor."""
    return f"highfield_{tx}_{'m' if tz < 0 else ''}{abs(tz)}_decor"


JOINABLE = ('brick_wall', 'iron_bars', 'spruce_fence')
NEIGHBOURS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
# Ground kinds the barrier pass must not sit on or straddle (live surface, not only map way columns).
# Brick paths count like roads (#144): hedges must not stand on polished_granite garden paths.
_CARRIAGE_KINDS = frozenset({'tarmac', 'paving', 'brick', 'mark', 'kerb'})
_NEAR_CARRIAGE = frozenset({'tarmac', 'paving', 'brick'})


def column_ground_kind(world: World, x: int, z: int) -> str | None:
    """Classified ground kind at the live surface of (x, z), or None if empty."""
    from surface_probe import surface_y

    top = surface_y(world, x, z)
    if not top:
        return None
    return BLOCK_KIND.get(top[1])


def on_carriageway(world: World, x: int, z: int) -> bool:
    """True when a hedge/fence column would sit on road/brick path or within two blocks of one."""
    kind = column_ground_kind(world, x, z)
    if kind in _CARRIAGE_KINDS:
        return True
    for dx in range(-2, 3):
        for dz in range(-2, 3):
            if dx == 0 and dz == 0:
                continue
            nk = column_ground_kind(world, x + dx, z + dz)
            if nk in _NEAR_CARRIAGE:
                return True
    return False


def join_pieces(found: dict[tuple[int, int, int], str], live, can_place,
                present: dict[tuple[int, int, int], str] | None = None) -> dict[tuple[int, int, int], str]:
    """Extra blocks that link separate walls/fences of the same type, one block per join.

    Pieces touch only face to face (diagonal is disjoint). A candidate is an empty spot
    (`can_place(pos)`) with face neighbours in two or more different pieces of one type;
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
            cands = [c for c in cands if can_place(c) and c not in found and c not in added]

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
) -> tuple[dict[tuple[int, int, int], str], list[dict]]:
    """Place street-furniture recipes on paving columns.

    Skips path-line columns (``way``), columns against a build (``near1``), non-paving
    surfaces, and any recipe spot that is not air / is owned or masked.

    @param cell: (tx, tz)
    @param items: ``{x,z,kind,source}`` from map and/or designer
    @param world: live or planning world
    @param way: mapped road / path / water columns
    @param near1: footprint and structure columns dilated by 1
    @param owned: neighbour-owned block positions
    @param masked: positions owned by another pass mask
    @returns: (placements, report rows that were placed)
    """
    from surface_probe import surface_y

    found: dict[tuple[int, int, int], str] = {}
    report: list[dict] = []
    for item in items:
        x, z, kind = int(item['x']), int(item['z']), item['kind']
        q = (x, z)
        if not cell_contains(cell, *q) or q in near1 or q in way:
            continue
        if column_ground_kind(world, x, z) not in sf.PAVING_KINDS:
            continue
        top = surface_y(world, x, z)
        if not top:
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
            continue
        found.update(voxels)
        row = {'x': x, 'z': z, 'kind': kind, 'source': item.get('source', 'map'),
               'blocks': len(voxels)}
        if item.get('note'):
            row['note'] = item['note']
        report.append(row)
    return found, report


def plan_cell(cell: tuple[int, int], plan: Plan, world: World):
    """Plan barriers and street furniture for one cell. Returns (placements, stats).

    Barriers: soft ground only, never within a block of a building footprint or structure.
    Furniture: paving only, never on a path-line column or against a build. Sources are
    mapped OSM points and ``reference/decor-furniture.json`` (designer).
    """
    from ground_sources import OSMGround
    from osm_footprints import OSMIndex
    from surface_probe import surface_y

    owned = neighbour_owned(cell, ignored_builds(cell))
    owned_cols = {(x, z) for x, _y, z in owned}
    masked = load_masked_positions()
    fp = set(OSMIndex().cells())
    near = fp | owned_cols
    near1 = {(x + a, z + b) for x, z in near for a in (-1, 0, 1) for b in (-1, 0, 1)}
    vmap = OSMGround(cell)
    found: dict[tuple[int, int, int], str] = {}
    present: dict[tuple[int, int, int], str] = {}
    stats = Counter()
    from garden_sources import garden_barriers
    groups = [(vmap.hedge, 'oak_leaves[persistent=true]', 2), (vmap.wall, 'brick_wall', 1),
              (vmap.fence, 'iron_bars', 1)]
    mapped = {q for g in groups for q in g[0]}
    by_kind: dict = {}
    for q, (_t, blk, gh) in garden_barriers(cell).items():     # derived per-parcel garden boundaries
        if q not in mapped:                                    # a mapped barrier wins
            by_kind.setdefault((blk, gh), set()).add(q)
    groups += [(cells, blk, gh) for (blk, gh), cells in sorted(by_kind.items())]
    stats['garden columns'] = sum(len(c) for c in by_kind.values())
    warm_surface_probe(world, cell[0] * 128 - 2, cell[1] * 128 - 2,
                       cell[0] * 128 + 129, cell[1] * 128 + 129)           # batch reads (#110)
    way = set(vmap.road) | set(vmap.feat) | set(vmap.water)      # mapped road, path or water columns
    for cells, mat, h in groups:
        for q in sorted(cells):
            if not cell_contains(cell, *q) or q in near1:
                continue
            top = surface_y(world, *q)
            if top and top[1] == mat:
                present[(q[0], top[0], q[1])] = mat   # already standing: a piece for joining
            if not top or q in way:
                stats['on road/path/water'] += 1
                continue
            if on_carriageway(world, q[0], q[1]):
                stats['on carriageway'] += 1
                continue
            if top[1].endswith('_carpet'):      # structure top (roof/deck), not ground: posts would float
                stats['on carpet top'] += 1
                continue
            for i in range(1, h + 1):
                pos = (q[0], top[0] + i, q[1])
                if not is_air(world.block(*pos)):
                    stats['blocked_live'] += 1
                    if bare(world.block(*pos)) == f'minecraft:{mat}':
                        present[pos] = mat
                elif pos in owned or pos in masked:
                    stats['blocked_owned'] += 1
                else:
                    found[pos] = mat

    items = sf.merge_items(sf.map_items(vmap.furniture), sf.designer_items(cell))
    furniture, furniture_report = place_furniture(
        cell, items, world, way=way, near1=near1, owned=owned, masked=masked,
    )
    # Furniture wins over a barrier block at the same position (rare overlap).
    found.update(furniture)
    stats['furniture'] = len(furniture_report)
    stats['furniture map'] = sum(1 for r in furniture_report if r['source'] == 'map')
    stats['furniture designer'] = sum(1 for r in furniture_report if r['source'] == 'designer')

    def can_place(pos):
        return (cell_contains(cell, pos[0], pos[2]) and (pos[0], pos[2]) not in near1
                and is_air(world.block(*pos)) and pos not in owned and pos not in masked)

    joins = join_pieces(found, lambda p: world.block(*p), can_place, present)
    found.update(joins)
    stats['joins'] = len(joins)
    clipped, dropped = clip_to_cell(found, *cell)
    for p, b in clipped.items():
        plan.set(*p, b)
    stats['dropped_outside_cell'] = dropped
    stats['blocks'] = len(clipped)
    stats['furniture_report'] = furniture_report  # list, not a Counter value; printed by run()
    return clipped, stats


def run(cell: tuple[int, int]) -> int:
    """Dry run: plan and run the kit's guards. Never writes the world."""
    from site_kit import apply, fresh_planning_world, preview

    name = plan_name(*cell)
    plan = Plan(name)
    placed, stats = plan_cell(cell, plan, fresh_planning_world())
    furniture_report = stats.pop('furniture_report', [])
    kinds = Counter(placed.values())
    print(f'{name}: {dict(stats)}')
    print(f'  by block: {dict(kinds)}')
    if furniture_report:
        print('  street furniture:')
        for row in furniture_report:
            note = f" ({row['note']})" if row.get('note') else ''
            print(f"    {row['kind']} at {row['x']},{row['z']} source={row['source']}{note}")
    if placed:
        preview(plan, ROOT / f'verification/site/{name}-preview.png', scale=2)
        apply(plan, dry=True, modifies=ignored_builds(cell))
    return 0


def apply_cell(cell: tuple[int, int]) -> int:
    """Live apply: lock first, re-plan from the live world, readback, then the mask.

    An empty placement set still journals a landed decor pass (and an empty mask) so
    a cell with nothing to place can finish; regenerating ground clears it as usual.
    """
    import os
    from site_kit import apply

    if not os.environ.get('SITE_OWNER'):
        raise SystemExit('SITE_OWNER is not set; claim the lock first (scripts/site_lock.py claim <owner> ...)')
    tx, tz = cell
    name = plan_name(tx, tz)
    plan = Plan(name)
    world = World()
    placed, stats = plan_cell(cell, plan, world)
    furniture_report = stats.pop('furniture_report', [])
    mask = build_mask('decor', cell, name, placed, lambda p: is_air(world.block(*p)))
    print(f'{name}: {len(placed)} blocks ({dict(stats)})')
    if furniture_report:
        for row in furniture_report:
            print(f"  furniture {row['kind']} at {row['x']},{row['z']} source={row['source']}")
    rb = apply(plan, modifies=ignored_builds(cell), stage='decor', cell=cell,
               strict=True, meta={'furniture': furniture_report} if furniture_report else None,
               mask=mask)
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
