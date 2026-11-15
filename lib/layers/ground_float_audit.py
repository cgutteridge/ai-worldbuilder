"""Read-only audit: ground surfaces that stand over air, across a world (#296).

Reads the cell journal only, never the live world. A ground column floats when, under the top
block a real ground build owns, some position is unowned or owned as air before solid ground is
reached: two owned non-air blocks in a row (:func:`ground_layer.support_y`).

  python3 scripts/ground_float_audit.py --world <world>                # every cell with ground
  python3 scripts/ground_float_audit.py --world <world> --cells 48,-44 --json
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict

from ground_layer import is_air, support_y

GROUND_PASS = 'ground'


def floating_ground_tops(owners: dict, ground_plans) -> list:
    """Columns whose top ground-owned block stands over an unowned or air gap.

    @param owners: ``{(x, y, z): (plan, state)}`` as ``CellView.owners`` returns it
    @param ground_plans: plan names whose pass is real ground
    @returns: ``[{'x', 'z', 'surface_y', 'support_y', 'gap', 'plan', 'state'}]`` sorted by column
    """
    plans = set(ground_plans)
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
                        'plan': plan, 'state': block})
    return out


def audit(view, cells=None) -> dict:
    """Floating ground tops per cell, from the journal view.

    @param view: ``cell_registry.view_registry()`` (or any view with ``entries`` and ``owners``)
    @param cells: optional cells to audit; default every cell a live real ground build touches
    @returns: ``{(tx, tz): [row, ...]}`` for every audited cell (empty list when clean)
    """
    ground = {}
    for e in view.entries():
        if e.kind == 'build' and e.pass_ == GROUND_PASS and not e.undone and e.status != 'begun':
            ground.setdefault(e.plan, set()).add(tuple(e.cell))
    want = sorted({tuple(c) for c in cells}) if cells else sorted(set().union(*ground.values()) if ground else ())
    return {cell: floating_ground_tops(view.owners([cell]), ground) for cell in want}


def main(argv=None) -> int:
    """CLI: print floating ground tops per cell. Exit 1 when any column floats."""
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cells', nargs='*', help='tx,tz cells (default: every cell with real ground)')
    ap.add_argument('--json', action='store_true', help='print every floating column as JSON')
    a = ap.parse_args(argv)
    from cell_registry import view_registry
    cells = [tuple(int(v) for v in c.split(',')) for c in a.cells] if a.cells else None
    found = audit(view_registry(), cells)
    total = sum(len(rows) for rows in found.values())
    if a.json:
        print(json.dumps({f'{tx},{tz}': rows for (tx, tz), rows in found.items() if rows}, indent=1))
        return 1 if total else 0
    for (tx, tz), rows in found.items():
        if rows:
            eg = ', '.join(f"({r['x']},{r['z']}) y{r['surface_y']} gap {r['gap']}" for r in rows[:3])
            print(f'{tx},{tz}: {len(rows)} floating  e.g. {eg}')
    print(f'ground float audit: {total} floating column(s) in {sum(1 for r in found.values() if r)} '
          f'of {len(found)} cell(s) with ground')
    return 1 if total else 0


if __name__ == '__main__':
    raise SystemExit(main())
