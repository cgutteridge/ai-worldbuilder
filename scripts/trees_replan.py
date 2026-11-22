#!/usr/bin/env python3
"""Dry plan of the trees pass for a cell that already has trees: its own old trees mask is treated as undone. Writes nothing to the world.

Like trees_layer.py's dry run, this plans as if scripts/element_undo.py had run
(the old crowns' blocks are air, their mask positions are not owned), prints the
counts and skips, and writes the plan JSON that shims/google_window.py plan draws over the aerial sheet.

  python3 scripts/trees_replan.py --cell 48,-44 --out plan.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_TOOL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL / 'scripts'))
import script_env  # noqa: E402

script_env.ensure_import_paths()

import trees_layer as tl  # noqa: E402
from cellmath import parse_cell  # noqa: E402
from layer_planning import plan_after_undo  # noqa: E402
from site_kit import Plan, World  # noqa: E402


def replan(cell: tuple[int, int], *, world=None, inputs=None, mask_dir=None):
    """Plan after the same fail-closed undo used by mask_undo, without mutating globals."""
    world = World() if world is None else world
    undo = plan_after_undo('trees', cell, world, mask_dir=mask_dir)
    if undo.conflicts:
        raise ValueError(f'{len(undo.conflicts)} tree undo conflicts; preview cannot simulate a clean undo')
    inputs = tl.planning_inputs(cell) if inputs is None else inputs
    placed, rep = tl.plan_cell(cell, Plan('replan'), undo.world, inputs.after_undo(undo.masked))
    return undo.own, placed, rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--cell', required=True)
    ap.add_argument('--out', help='write the plan JSON here')
    script_env.add_world_arg(ap)
    a = ap.parse_args(argv)
    script_env.activate_world_from_args(a)
    cell = parse_cell(a.cell)
    own, placed, rep = replan(cell)
    print(f'old mask {len(own)} blocks -> planned {len(placed)} blocks, {len(rep["trees"])} crowns, skipped {len(rep["skipped"])}')
    print(dict(Counter(w.split(' of ')[0] if ' of ' not in w else 'voxels blocked' for *_x, w in rep['skipped'])))
    for s in rep['skipped']:
        print('  skipped', s)
    if a.out:
        json.dump({'trees': rep['trees'], 'skipped': rep['skipped'], 'rooting': rep['rooting'],
                   'crown_snaps': rep['crown_snaps'], 'mapped_snaps': rep['mapped_snaps']}, open(a.out, 'w'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
