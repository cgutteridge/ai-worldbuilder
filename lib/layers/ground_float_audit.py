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


def main(argv=None) -> int:
    """CLI: print floating ground tops per cell, whose each is and its clear. Exit 1 when any floats."""
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cells', nargs='*', help='tx,tz cells (default: every cell with real ground)')
    ap.add_argument('--json', action='store_true', help='print every floating column as JSON')
    a = ap.parse_args(argv)
    from cell_registry import view_registry
    view = view_registry()
    cells = [tuple(int(v) for v in c.split(',')) for c in a.cells] if a.cells else None
    found = audit(view, cells)
    for cell, rows in found.items():
        for row in rows:
            row['clear'] = clear_command(view, cell, row)
    total = sum(len(rows) for rows in found.values())
    if a.json:
        print(json.dumps({f'{tx},{tz}': rows for (tx, tz), rows in found.items() if rows}, indent=1))
        return 1 if total else 0
    for (tx, tz), rows in found.items():
        if rows:
            print(f'{tx},{tz}: {len(rows)} floating')
            for row in rows[:10]:
                print(f"  {describe(row)}  [clear: {row['clear']}]")
    print(f'ground float audit: {total} floating column(s) in {sum(1 for r in found.values() if r)} '
          f'of {len(found)} cell(s) with ground')
    return 1 if total else 0


if __name__ == '__main__':
    raise SystemExit(main())
