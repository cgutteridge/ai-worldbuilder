"""Read-only audit: ground surfaces that stand over air, across a world (#296, #297).

Reads the cell journal only, never the live world. A ground column floats when, under the top
block a real ground build owns, some position is unowned or owned as air before solid ground is
reached: two owned non-air blocks in a row (:func:`ground_layer.support_y`).

Between the feature and the ground every column is accounted for (task 297). Each floating
column says whose it is and the command that settles it:

  ground   no feature owns a block at or above the top, and the gap lies above the highest
           block a feature owns there (a buried door stoop, a footing): the ground redo fills
           from that block up (``ground_layer.plan_cell``).
  feature  a feature owns a solid block at or above the top, or the gap lies within the
           feature's own blocks (air it owns, or air under its lowest block): the ground never
           touches it, so the feature's reapply or redo is the clear.

Two more holes the ground leaves are listed per cell beside them (task 298):

  cavity   a column with a real ground top whose support is a crust of solid blocks over unowned
           or air positions (ghost stone left when a stone ring was undone): the ground redo fills
           the air below the crust (``ground_layer.hollow_ys``). Rows say how many blocks, and a
           feature's own blocks and air below its highest block stay the feature's.
  bridge   a column under a mapped bridge that no real ground owns, whose ghost surface sits
           below the lidar terrain: the ground redo surfaces it (the deck is a structure's).

  python3 scripts/ground_float_audit.py --world <world>                # every cell with ground
  python3 scripts/ground_float_audit.py --world <world> --cells 48,-44 --json
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict

from ground_layer import is_air, support_y

GROUND_PASS = 'ground'
GHOST_PASSES = ('ghost-ground', 'ghost-features')


def floating_ground_tops(owners: dict, ground_plans, passes=None) -> list:
    """Columns whose top ground-owned block stands over an unowned or air gap.

    @param owners: ``{(x, y, z): (plan, state)}`` as ``CellView.owners`` returns it
    @param ground_plans: plan names whose pass is real ground
    @param passes: optional ``{plan: pass}``; ghost passes are support, never a feature
    @returns: ``[{'x', 'z', 'surface_y', 'support_y', 'gap', 'plan', 'state', 'belongs', 'owner',
      'owner_y'}]`` sorted by column; ``belongs`` is ``ground`` or ``feature``, ``owner`` the feature
      plan the column's verdict turns on (None when no feature owns a block there)
    """
    plans = set(ground_plans)
    passes = passes or {}
    columns: dict = defaultdict(dict)
    for (x, y, z), owner in owners.items():
        columns[(x, z)][y] = owner
    out = []
    for (x, z), col in sorted(columns.items()):
        tops = [y for y, (plan, state) in col.items() if plan in plans and not is_air(state)]
        if not tops:
            continue
        top = max(tops)

        def state(_x, y, _z, col=col):
            return col[y][1] if y in col else None
        floor = min(col) - 1
        base = support_y(state, x, top, z, floor=floor)
        low = floor if base is None else base
        gap = sum(1 for y in range(low + 1, top) if is_air(state(x, y, z)))
        if base is None or gap:
            plan, block = col[top]
            out.append({'x': x, 'z': z, 'surface_y': top, 'support_y': base, 'gap': gap,
                        'plan': plan, 'state': block,
                        **_whose(col, top, low, plans, passes)})
    return out


def buried_cavities(owners: dict, ground_plans, passes=None) -> list:
    """Columns whose real ground sits on a crust of solid blocks over air (task 298).

    Reads the journal only. The crust is the :func:`ground_layer.support_y` run under the top; a
    cavity block is any position between the column's lowest owned block and that run that nothing
    owns, or that is owned as air. A feature's highest owned position and everything below it is
    the feature's (a basement, a footing); only the air above it is the ground's to fill.

    @param owners: ``{(x, y, z): (plan, state)}`` as ``CellView.owners`` returns it
    @param ground_plans: plan names whose pass is real ground
    @param passes: optional ``{plan: pass}``; ghost passes are support, never a feature
    @returns: ``[{'x', 'z', 'surface_y', 'support_y', 'cavity', 'feature', 'plan', 'belongs'}]`` sorted by
      column: ``cavity`` counts the ground's air blocks, ``feature`` the feature's; ``belongs`` is
      ``ground`` when any block is the ground's, else ``feature``
    """
    plans = set(ground_plans)
    passes = passes or {}
    columns: dict = defaultdict(dict)
    for (x, y, z), owner in owners.items():
        columns[(x, z)][y] = owner
    out = []
    for (x, z), col in sorted(columns.items()):
        tops = [y for y, (plan, state) in col.items() if plan in plans and not is_air(state)]
        if not tops:
            continue
        top = max(tops)

        def state(_x, y, _z, col=col):
            return col[y][1] if y in col else None
        low = min(col)
        base = support_y(state, x, top, z, floor=low - 1)
        if base is None:
            continue                                       # floats: the float rows own it
        feature = [y for y, (plan, s) in col.items()
                   if plan not in plans and passes.get(plan) not in GHOST_PASSES and y < base]
        owned = max(feature) if feature else low - 1
        air = [y for y in range(low + 1, base) if is_air(state(x, y, z))]
        ground = sum(1 for y in air if y > owned)
        if air:
            out.append({'x': x, 'z': z, 'surface_y': top, 'support_y': base, 'cavity': ground,
                        'feature': len(air) - ground, 'plan': col[top][0],
                        'belongs': 'ground' if ground else 'feature'})
    return out


def unsurfaced_bridge_columns(owners: dict, deck, terrain: dict, passes=None) -> list:
    """Columns under a mapped bridge that no real ground owns, with the ghost surface below the terrain.

    @param owners: ``{(x, y, z): (plan, state)}``
    @param deck: columns of mapped bridges (``OSMGround.deck``), minus any water the caller removed
    @param terrain: ``{(x, z): lidar terrain y}``; a column without one is not listed
    @param passes: ``{plan: pass}``; a ghost pass is a placeholder, not an owner
    @returns: ``[{'x', 'z', 'ghost_y', 'terrain_y', 'gap'}]`` sorted by column
    """
    passes = passes or {}
    columns: dict = defaultdict(dict)
    for (x, y, z), owner in owners.items():
        if (x, z) in deck:
            columns[(x, z)][y] = owner
    out = []
    for q in sorted(deck):
        col = columns.get(q, {})
        if any(passes.get(plan) not in GHOST_PASSES and not is_air(s) for plan, s in col.values()):
            continue                                       # a real build already owns something here
        ghost = max((y for y, (_p, s) in col.items() if not is_air(s)), default=None)
        want = terrain.get(q)
        if want is None or (ghost is not None and ghost >= want):
            continue
        out.append({'x': q[0], 'z': q[1], 'ghost_y': ghost, 'terrain_y': want,
                    'gap': want - (ghost if ghost is not None else 0)})
    return out


def _whose(col: dict, top: int, low: int, ground: set, passes: dict) -> dict:
    """Whether a floating column is the ground's or a feature's to settle (task 297).

    @param col: ``{y: (plan, state)}`` for one column
    @param top: the floating ground top
    @param low: y the gap is counted from (the support, or the column's floor)
    @returns: ``{'belongs', 'owner', 'owner_y'}``
    """
    feature = {y: o for y, o in col.items()
               if o[0] not in ground and passes.get(o[0]) not in GHOST_PASSES}
    above = sorted(y for y, (_plan, s) in feature.items() if y >= top and not is_air(s))
    if above:
        return {'belongs': 'feature', 'owner': feature[above[0]][0], 'owner_y': above[0]}
    below = [y for y in feature if y < top]
    if not below:
        return {'belongs': 'ground', 'owner': None, 'owner_y': None}
    fy = max(below)
    gap_above = sum(1 for y in range(max(low, fy) + 1, top) if y not in col or is_air(col[y][1]))
    return {'belongs': 'ground' if gap_above else 'feature', 'owner': feature[fy][0], 'owner_y': fy}


def cell_evidence(cell) -> tuple[set, set, dict]:
    """Bridge columns, water columns with their bank, and lidar terrain under the bridges, for a cell.

    Reads the vector map and lidar references only, never the live world.

    @returns: ``(deck, water, terrain)``
    """
    import cellmath
    from ground_layer import _ring, lidar_base_map
    from ground_sources import OSMGround
    vmap = OSMGround(tuple(cell))
    deck = {q for q in getattr(vmap, 'deck', set()) if _inside(cellmath.bounds(tuple(cell)), q)}
    return deck, _ring(vmap.water), lidar_base_map(deck)


def _inside(box, q) -> bool:
    x0, z0, x1, z1 = box
    return x0 <= q[0] <= x1 and z0 <= q[1] <= z1


def hollows(view, cells=None, evidence=None) -> dict:
    """Buried cavities and unsurfaced bridge columns per cell (task 298), from the journal view.

    @param view: ``cell_registry.view_registry()`` (or any view with ``entries`` and ``owners``)
    @param cells: optional cells to audit; default every cell a live real ground build touches
    @param evidence: optional ``callable(cell) -> (deck, water, terrain)``; default :func:`cell_evidence`
    @returns: ``{(tx, tz): {'cavities': [...], 'bridges': [...]}}`` for every audited cell
    """
    ground, builds = _ground_plans(view)
    passes = {plan: p for plan, (p, _cell) in builds.items()}
    want = sorted({tuple(c) for c in cells}) if cells else sorted(set().union(*ground.values()) if ground else ())
    out = {}
    for cell in want:
        owners = view.owners([cell])
        deck, water, terrain = (evidence or cell_evidence)(cell)
        out[cell] = {'cavities': buried_cavities(owners, ground, passes),
                     'bridges': unsurfaced_bridge_columns(owners, deck - water, terrain, passes)}
    return out


def _ground_plans(view) -> tuple[dict, dict]:
    """Live real ground plans with their cells, and every live build plan's ``(pass, cell)``."""
    ground, builds = {}, {}
    for e in view.entries():
        if e.kind != 'build' or e.undone:
            continue
        builds[e.plan] = (e.pass_, tuple(e.cell))
        if e.pass_ == GROUND_PASS and e.status != 'begun':
            ground.setdefault(e.plan, set()).add(tuple(e.cell))
    return ground, builds


def audit(view, cells=None) -> dict:
    """Floating ground tops per cell, from the journal view.

    @param view: ``cell_registry.view_registry()`` (or any view with ``entries`` and ``owners``)
    @param cells: optional cells to audit; default every cell a live real ground build touches
    @returns: ``{(tx, tz): [row, ...]}`` for every audited cell (empty list when clean)
    """
    ground, builds = _ground_plans(view)
    passes = {plan: p for plan, (p, _cell) in builds.items()}
    want = sorted({tuple(c) for c in cells}) if cells else sorted(set().union(*ground.values()) if ground else ())
    return {cell: floating_ground_tops(view.owners([cell]), ground, passes) for cell in want}


def clear_command(view, cell, row) -> str:
    """The sanctioned command that settles one floating column (never a hand-placed block).

    A ground column: the cell's ground redo (with its landed trees/decor redone after it). A
    feature column: the feature's reapply, or its evidence-led redo when reapply is not offered.

    @param view: the journal (``cell_registry.view_registry()``)
    @param cell: ``(tx, tz)`` audited
    @param row: a :func:`floating_ground_tops` row
    @returns: command text
    """
    from cell_readiness import ground_redo_command, reapply_command
    _ground, builds = _ground_plans(view)
    if row['belongs'] == 'feature':
        layer, home = builds.get(row['owner'], ('feature', tuple(cell)))
        return reapply_command(row['owner'], layer, home, registry=view)
    landed = {e.pass_ for e in view.entries(cells=[tuple(cell)])
              if e.kind == 'build' and not e.undone and e.status == 'landed' and tuple(e.cell) == tuple(cell)}
    return ground_redo_command(cell, {'trees': 'trees' in landed, 'decor': 'decor' in landed})


def describe(row) -> str:
    """One line for a floating column: where, whose, and why."""
    where = f"({row['x']},{row['z']}) y{row['surface_y']} gap {row['gap']}"
    if row['belongs'] == 'feature':
        return f"{where}: feature {row['owner']}'s (block at y{row['owner_y']})"
    if row['owner']:
        return f"{where}: ground's, above {row['owner']} y{row['owner_y']}"
    return f"{where}: ground's"


def describe_hollow(kind: str, row: dict) -> str:
    """One line for a buried cavity (``kind`` 'cavity') or an unsurfaced bridge column ('bridge')."""
    if kind == 'bridge':
        ghost = 'no surface' if row['ghost_y'] is None else f"ghost top y{row['ghost_y']}"
        return f"({row['x']},{row['z']}) under a mapped bridge: {ghost}, terrain y{row['terrain_y']} ({row['gap']} short)"
    where = f"({row['x']},{row['z']}) y{row['surface_y']} on a crust to y{row['support_y']}"
    if row['belongs'] == 'feature':
        return f"{where}: {row['feature']} air block(s) under a feature's block, the feature's"
    return f"{where}: {row['cavity']} hidden air block(s), ground's" + (
        f" (+{row['feature']} the feature's)" if row['feature'] else '')


def main(argv=None) -> int:
    """CLI: print floating ground tops, buried cavities and unsurfaced bridge columns per cell.

    Exit 1 when any is found.
    """
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cells', nargs='*', help='tx,tz cells (default: every cell with real ground)')
    ap.add_argument('--json', action='store_true', help='print every row as JSON')
    ap.add_argument('--no-bridges', action='store_true',
                    help='skip the unsurfaced bridge columns (they read the vector map and lidar per cell)')
    a = ap.parse_args(argv)
    from cell_registry import view_registry
    view = view_registry()
    cells = [tuple(int(v) for v in c.split(',')) for c in a.cells] if a.cells else None
    found = audit(view, cells)
    holes = hollows(view, cells, evidence=(lambda _c: (set(), set(), {})) if a.no_bridges else None)
    for cell, rows in found.items():
        for row in rows:
            row['clear'] = clear_command(view, cell, row)
    total = sum(len(rows) for rows in found.values())
    n_cav = sum(len(h['cavities']) for h in holes.values())
    n_bridge = sum(len(h['bridges']) for h in holes.values())
    if a.json:
        out = {f'{tx},{tz}': {'floating': found[(tx, tz)], **holes.get((tx, tz), {})}
               for (tx, tz) in found if found[(tx, tz)] or any(holes.get((tx, tz), {}).values())}
        print(json.dumps(out, indent=1))
        return 1 if total or n_cav or n_bridge else 0
    for (tx, tz), rows in found.items():
        h = holes.get((tx, tz), {'cavities': [], 'bridges': []})
        if rows or h['cavities'] or h['bridges']:
            print(f"{tx},{tz}: {len(rows)} floating, {len(h['cavities'])} buried cavity column(s) "
                  f"({sum(r['cavity'] for r in h['cavities'])} blocks), {len(h['bridges'])} unsurfaced bridge column(s)")
            for row in rows[:10]:
                print(f"  {describe(row)}  [clear: {row['clear']}]")
            for row in h['cavities'][:10]:
                print(f"  {describe_hollow('cavity', row)}")
            for row in h['bridges'][:10]:
                print(f"  {describe_hollow('bridge', row)}")
    print(f'ground float audit: {total} floating column(s) in {sum(1 for r in found.values() if r)} '
          f'of {len(found)} cell(s) with ground; {n_cav} buried cavity column(s) in '
          f"{sum(1 for h in holes.values() if h['cavities'])} cell(s); {n_bridge} unsurfaced bridge column(s) in "
          f"{sum(1 for h in holes.values() if h['bridges'])} cell(s)")
    return 1 if total or n_cav or n_bridge else 0


if __name__ == '__main__':
    raise SystemExit(main())
