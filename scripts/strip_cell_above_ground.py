#!/usr/bin/env python3
"""Stage 5a: strip vendor-import above-ground features across one whole cell (dry-run by default).

Plans air for every tree / plant / junk block directly above each column's surface, for
every column inside the cell (no margin, so neighbours are not written). The strip is a
journal build of pass ``other``; the cell's trees and decor passes are then undone in the
journal (``site_kit.undo``), so the cell shows them as not done.

  python3 scripts/strip_cell_above_ground.py --cell 48,-45            # dry-run + preview
  SITE_OWNER=<lock-owner> python3 scripts/strip_cell_above_ground.py --cell 48,-45 --apply
"""
from __future__ import annotations

import argparse

from script_env import ROOT  # noqa: E402



from cellmath import parse_cell  # noqa: E402,F401

from foliage import strip_foliage_above_surface  # noqa: E402
from cellmath import CELL  # noqa: E402
from site_kit import Plan, World, apply, preview, undo  # noqa: E402


def cell_columns(cell: tuple[int, int]) -> list[tuple[int, int]]:
    """Every (x, z) column inside one 128-block coverage cell.

    @param cell: (tx, tz)
    @returns: list of columns
    """
    x0, z0 = cell[0] * CELL, cell[1] * CELL
    return [(x, z) for x in range(x0, x0 + 128) for z in range(z0, z0 + 128)]


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--cell', required=True)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    cell = parse_cell(args.cell)
    name = f'highfield_{cell[0]}_m{abs(cell[1])}_stripabove'
    plan = Plan(name)
    w = World()
    n = strip_foliage_above_surface(plan, w, cell_columns(cell))
    print(f'{name}: columns {len(cell_columns(cell))} blocks to strip {n} cells {len(plan.cells)}')
    preview(plan, ROOT / f'verification/site/{name}-preview.png', scale=2)
    apply(plan, dry=not args.apply, modifies=(), stage='other')
    if args.apply:
        # the strip removed the cell's foliage / decor mass: void those passes in the journal too
        from cell_registry import view_registry
        view = view_registry().cell(cell)
        for pass_name, done in (('decor', view.decor), ('trees', view.trees)):
            if done:
                undo((cell, pass_name))
    else:
        print('journal not written (dry-run)')
