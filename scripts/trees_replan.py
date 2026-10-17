#!/usr/bin/env python3
"""Dry plan of the trees pass for a cell that already has trees: its own old trees mask is treated as undone. Writes nothing to the world.

trees_layer.py's dry run plans against the live world, where the cell's own old trees block every column, so it cannot preview a
redo. This plans as if scripts/mask_undo.py had run (the old crowns' blocks are air, their mask positions are not owned), prints the
counts and skips, and writes the plan JSON that shims/google_window.py plan draws over Google.

  python3 scripts/trees_replan.py --cell 48,-44 --out plan.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter

import trees_layer as tl
from cellmath import parse_cell
from pass_mask import mask_path, mask_placements, read_mask
from site_kit import Plan, World


def replan(cell: tuple[int, int], *, world=None, inputs=None, mask_dir=None):
    """Plan after the same fail-closed undo used by mask_undo, without mutating globals."""
    from pass_mask import MASK_DIR, plan_undo
    from site_kit import PlannedWorld
    directory = MASK_DIR if mask_dir is None else mask_dir
    path = mask_path('trees', cell, directory)
    mask = read_mask(path) if path.exists() else None
    own = set(mask_placements(mask)) if mask else set()
    remaining = {}
    for other in sorted(directory.glob('*.mask.json')):
        if other == path:
            continue
        m = read_mask(other)
        remaining.update({p: f"{m['pass']} {m['cell'][0]},{m['cell'][1]}" for p in mask_placements(m)})
    world = World() if world is None else world
    cleared, conflicts = plan_undo(mask, lambda p: world.block(*p), own & remaining.keys()) if mask else ([], [])
    if conflicts:
        raise ValueError(f'{len(conflicts)} tree undo conflicts; preview cannot simulate a clean undo')
    inputs = tl.planning_inputs(cell) if inputs is None else inputs
    view = PlannedWorld(Plan('undo-preview'), world, ignore=cleared)
    placed, rep = tl.plan_cell(cell, Plan('replan'), view, inputs.after_undo(remaining))
    return own, placed, rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--cell', required=True)
    ap.add_argument('--out', help='write the plan JSON here')
    a = ap.parse_args(argv)
    cell = parse_cell(a.cell)
    own, placed, rep = replan(cell)
    print(f'old mask {len(own)} blocks -> planned {len(placed)} blocks, {len(rep["trees"])} crowns, skipped {len(rep["skipped"])}')
    print(dict(Counter(w.split(' of ')[0] if ' of ' not in w else 'voxels blocked' for *_x, w in rep['skipped'])))
    for s in rep['skipped']:
        print('  skipped', s)
    if a.out:
        json.dump({'trees': rep['trees'], 'skipped': rep['skipped']}, open(a.out, 'w'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
