#!/usr/bin/env python3
"""Spike #323 prototype: tree lines along mapped hedges and lanes, through the trees pass.

Leaves ``lib/layers/trees_layer.py`` unchanged. Crowns come from a designer hand-off in the
``reference/trees-extra-crowns.json`` format (``{"tx,tz": [{"x","z","r","evidence"}]}``). The
trees pass then plans as usual; hand-off crowns that sit on hedge or lane line evidence
(``tree_lines.line_evidence``) are added after the #317 source gate as aerial-read crowns. Hand-off
crowns off that evidence are refused, so open pasture stays clear even if the hand-off errs.

  python3 scripts/tree_lines_trial.py --world <w> propose --cell tx,tz          # evidence + proposal for the designer
  python3 scripts/tree_lines_trial.py --world <w> seam --cell tx,tz --handoff F  # ordinary extra-crowns path: what the gate drops
  python3 scripts/tree_lines_trial.py --world <w> seam --cell tx,tz --reader first  # trees pass on first-return height
  python3 scripts/tree_lines_trial.py --world <w> plan --cell tx,tz --handoff F  # trees dry run with line crowns admitted
  python3 scripts/site_lock.py --world <w> run <owner> "trial323" -- \\
    python3 scripts/tree_lines_trial.py --world <w> apply --cell tx,tz --handoff F   # LIVE trees pass with line crowns
  python3 scripts/tree_lines_trial.py --world <w> lines-plan --cell tx,tz --handoff F   # additive trial build, dry
  python3 scripts/site_lock.py --world <w> run <owner> "trial323" -- \\
    python3 scripts/tree_lines_trial.py --world <w> lines-apply --cell tx,tz --handoff F  # LIVE additive build

Undo ``apply`` like any trees pass (``mask_undo.py --cell tx,tz --pass trees --apply``). Undo
``lines-apply`` by its own plan: ``element_undo.py trial323_tree_lines_<tx>_<tz> --apply`` (``m`` for minus).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

_TOOL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL / 'scripts'))
import script_env  # noqa: E402

script_env.ensure_import_paths()

from cellmath import CELL, parse_cell  # noqa: E402

NEAR = 2   # a hand-off crown must sit within this many blocks of line evidence


def _artifact(cell, suffix: str) -> Path:
    out = script_env.ROOT / 'verification' / 'site'
    out.mkdir(parents=True, exist_ok=True)
    return out / f'trial323_{cell[0]}_m{abs(cell[1])}_tree_lines-{suffix}' if cell[1] < 0 else \
        out / f'trial323_{cell[0]}_{cell[1]}_tree_lines-{suffix}'


def load_handoff(path: Path, cell) -> list:
    """Rows for ``cell`` from a hand-off file in the extra-crowns format."""
    return json.loads(Path(path).read_text()).get(f'{cell[0]},{cell[1]}', [])


def admit(inputs, cell, rows, belt_cols):
    """Add hand-off crowns on line evidence to post-gate trees inputs.

    @param inputs: ``trees_layer.TreeInputs`` after the source gate
    @param cell: ``(tx, tz)``
    @param rows: hand-off rows ``{x, z, r, ...}``
    @param belt_cols: world columns of corridor/belt line evidence
    @returns: ``(new inputs, admitted [(x, z, r)], refused [(x, z, reason)])``
    """
    x0, z0 = cell[0] * CELL, cell[1] * CELL
    near = [(dx, dz) for dx in range(-NEAR, NEAR + 1) for dz in range(-NEAR, NEAR + 1)]
    crowns = list(inputs.crowns)
    added, refused = [], []
    for row in rows:
        x, z, r = int(row['x']), int(row['z']), float(row['r'])
        i, j = x - x0, z - z0
        if not (0 <= i < CELL and 0 <= j < CELL):
            refused.append((x, z, 'outside cell'))
        elif not any((x + dx, z + dz) in belt_cols for dx, dz in near):
            refused.append((x, z, 'no hedge or lane line evidence'))
        elif any((i - ci) ** 2 + (j - cj) ** 2 <= (cr * 0.75) ** 2 for ci, cj, cr in crowns):
            refused.append((x, z, 'duplicate of a retained crown'))
        else:
            crowns.append((i, j, r))
            added.append((x, z, r))
    origins = list(inputs.crown_origins or []) + [(x, z) for x, z, _r in added]
    counts = {**(inputs.source_counts or {}), 'tree_lines': len(added)}
    new = replace(inputs, crowns=crowns, crown_origins=origins,
                  aerial_sheet=set(inputs.aerial_sheet) | {(x, z) for x, z, _r in added},
                  source_counts=counts)
    return new, added, refused


def _install(cell, rows):
    """Wrap ``trees_layer.planning_inputs`` so the ordinary dry run and apply see line crowns."""
    import tree_lines
    import trees_layer as tl
    proposal = tree_lines.propose(cell)
    belt_cols = set(proposal['_evidence']['belt'])
    original = tl.planning_inputs
    log = {}

    def planning_inputs(c, osm_ground=None):
        inputs = original(c, osm_ground)
        if tuple(c) != tuple(cell):
            return inputs
        new, added, refused = admit(inputs, cell, rows, belt_cols)
        log.update(admitted=added, refused=refused, height_source=inputs.height_source,
                   line_height_source=proposal['height_source'])
        print(f'  tree lines: {len(added)} hand-off crowns admitted, {len(refused)} refused '
              f'(height source {proposal["height_source"]})')
        for x, z, why in refused:
            print(f'    refused {x},{z}: {why}')
        return new

    tl.planning_inputs = planning_inputs
    return tl, log


def cmd_propose(cell) -> int:
    import tree_lines
    p = tree_lines.propose(cell)
    out = _artifact(cell, 'proposal.json')
    out.write_text(json.dumps({k: v for k, v in p.items() if not k.startswith('_')}, indent=1))
    _overlay(cell, p, _artifact(cell, 'proposal.png'))
    kinds = {}
    for row in p['crowns']:
        kinds[row['kind']] = kinds.get(row['kind'], 0) + 1
    last = sum(1 for r in p['crowns'] if r['last_return_gate'])
    print(f"cell {p['cell']}: height source {p['height_source']}; aerial darkness {p['dark']}")
    print(f"  corridor {p['counts']['corridor']} columns, canopy evidence {p['counts']['evidence']}, "
          f"seeds {p['counts']['seeds']}, belt {p['counts']['belt']}")
    print(f"  proposed crowns {len(p['crowns'])} by kind {kinds}; last-return gate would keep {last}")
    if p['height_source'] == 'first-return':
        first = sum(1 for r in p['crowns'] if r['first_return_gate'])
        print(f'  first-return gate would keep {first}')
    print(f'  proposal: {out}\n  overlay: {_artifact(cell, "proposal.png")} '
          '(yellow corridor, cyan belt, magenta proposed crowns; grid every 16 blocks)')
    return 0


def _overlay(cell, p, path: Path, scale: int = 6) -> None:
    from PIL import Image, ImageDraw
    from aerial_coverage import cell_image
    x0, z0 = cell[0] * CELL, cell[1] * CELL
    img = cell_image(cell).convert('RGB').resize((CELL * scale, CELL * scale), Image.NEAREST)
    over = Image.new('RGBA', img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    ev = p['_evidence']
    for x, z in ev['corridor']:
        d.rectangle(((x - x0) * scale, (z - z0) * scale, (x - x0 + 1) * scale - 1, (z - z0 + 1) * scale - 1),
                    fill=(255, 255, 0, 40))
    for x, z in ev['belt']:
        d.rectangle(((x - x0) * scale + 2, (z - z0) * scale + 2, (x - x0) * scale + 3, (z - z0) * scale + 3),
                    fill=(0, 255, 255, 200))
    img = Image.alpha_composite(img.convert('RGBA'), over)
    d = ImageDraw.Draw(img)
    for row in p['crowns']:
        cx, cy, r = (row['x'] - x0) * scale, (row['z'] - z0) * scale, row['r'] * scale
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(255, 0, 255), width=2)
    for k in range(0, CELL, 16):
        d.line((k * scale, 0, k * scale, 6), fill=(255, 255, 255))
        d.text((k * scale + 2, 2), str(x0 + k), fill=(255, 255, 255))
        d.text((2, k * scale + 2), str(z0 + k), fill=(255, 255, 255))
    img.convert('RGB').save(path)


def cmd_seam(cell, handoff: Path | None, reader: str = 'auto') -> int:
    """Run the ordinary trees path with an optional explicit return comparison.

    Auto uses the production reader. First requires cached first-return data;
    last selects the shared reader's float last-return fallback for comparison.
    Reader overrides are scoped to this command and always restored.
    """
    from unittest.mock import patch
    from contextlib import ExitStack
    import tree_lines
    import trees_layer as tl
    original = tree_lines.standing_height

    def selected(c, margin=0):
        h, source = original(c, margin=margin, prefer_first=reader != 'last')
        if reader == 'first' and source != 'first-return':
            raise SystemExit(f'first-return standing height unavailable for {c} (reader: {source})')
        return h, source

    with ExitStack() as stack:
        if handoff is not None:
            stack.enter_context(patch.object(tl, 'EXTRA_CROWNS', Path(handoff)))
        if reader != 'auto':
            stack.enter_context(patch.object(tree_lines, 'standing_height', selected))
        return tl.run(cell)


def cmd_plan(cell, handoff: Path) -> int:
    tl, log = _install(cell, load_handoff(handoff, cell))
    rc = tl.run(cell)
    _artifact(cell, 'admission-dry.json').write_text(json.dumps(log, indent=1))
    return rc


def element_name(cell) -> str:
    """Journal plan name of the additive trial build for ``cell``."""
    return f'trial323_tree_lines_{cell[0]}_{"m" if cell[1] < 0 else ""}{abs(cell[1])}'


def inside_cell(rows, cell):
    """Split hand-off rows into crowns whose leaves stay in ``cell`` and those that would overhang.

    @returns: ``(kept rows, refused [(x, z, reason)])``
    """
    import math
    x0, z0 = cell[0] * CELL, cell[1] * CELL
    kept, refused = [], []
    for row in rows:
        reach = math.ceil(float(row['r'])) + 1
        x, z = int(row['x']), int(row['z'])
        if x0 + reach <= x <= x0 + CELL - 1 - reach and z0 + reach <= z <= z0 + CELL - 1 - reach:
            kept.append(row)
        else:
            refused.append((x, z, 'crown would overhang a neighbour cell'))
    return kept, refused


def cmd_lines(cell, handoff: Path, live: bool) -> int:
    """Additive trial build: only the admitted line crowns, planned by ``trees_layer.plan_cell``.

    Journalled as its own plan in the trees pass, with no pass mask, so it does not replace the
    cell's trees mask (``highfield_<cell>_trees``) and ``element_undo.py <plan> --apply`` removes
    exactly these blocks through the journal. Readiness applies as for any trees apply (stage
    ``other`` is reserved for clean-ups and markers). Crowns whose leaves would leave the cell are refused.
    """
    import os
    import trees_layer as tl
    import tree_lines
    from ground_layer import ignored_builds
    from site_kit import Plan, apply, fresh_planning_world, preview
    if live and not os.environ.get('SITE_OWNER'):
        raise SystemExit('SITE_OWNER is not set; run through scripts/site_lock.py run')
    rows, edge = inside_cell(load_handoff(handoff, cell), cell)
    proposal = tree_lines.propose(cell)
    world = fresh_planning_world(strict=live)
    base = tl.planning_inputs(cell)
    empty = replace(base, crowns=[], crown_origins=[], source_counts={})
    inputs, added, refused = admit(empty, cell, rows, set(proposal['_evidence']['belt']))
    refused = edge + refused
    name = element_name(cell)
    plan = Plan(name if live else f'{name}-dry', input_cells=[cell])
    placed, rep = tl.plan_cell(cell, plan, world, inputs)
    own = {tl.plan_name(*cell), tl.plan_name(*cell).replace('_trees', '_decor')}
    modifies = tuple(b for b in ignored_builds(cell) if b not in own)
    trees = rep['trees']
    print(f'{plan.name}: hand-off {len(rows) + len(edge)}, admitted {len(added)}, refused {len(refused)}; '
          f'planted {len(trees)} crowns, {len(placed)} blocks; skipped {len(rep["skipped"])}')
    for x, z, why in refused:
        print(f'  refused {x},{z}: {why}')
    for x, z, _r, why in rep['skipped']:
        print(f'  skipped {x},{z}: {why}')
    record = {'plan': plan.name, 'height_source': inputs.height_source,
              'line_height_source': proposal['height_source'], 'admitted': added,
              'refused': refused, 'trees': trees, 'skipped': rep['skipped'], 'blocks': len(placed),
              'rooting': rep['rooting'], 'species': rep['species'], 'tree_species': rep['tree_species']}
    _artifact(cell, 'lines.json' if live else 'lines-dry.json').write_text(json.dumps(record, indent=1))
    if not placed:
        raise SystemExit('nothing to plant')
    if not live:
        preview(plan, _artifact(cell, 'lines-preview.png'), scale=2)
        apply(plan, dry=True, modifies=modifies)
        return 0
    rb = apply(plan, modifies=modifies, stage='trees', cell=cell, strict=True,
               meta={'note': 'trial323 hedge/lane tree lines; undo: element_undo.py ' + name,
                     'crowns': len(trees), 'handoff': str(handoff)})
    print(f'readback: {rb["planned"]} planned, {rb["mismatches"]} mismatches')
    return 0


def cmd_apply(cell, handoff: Path) -> int:
    tl, log = _install(cell, load_handoff(handoff, cell))
    rc = tl.apply_cell(cell)
    _artifact(cell, 'admission.json').write_text(json.dumps(log, indent=1))
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    script_env.add_world_arg(ap)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('propose', 'seam', 'plan', 'apply', 'lines-plan', 'lines-apply'):
        p = sub.add_parser(name)
        p.add_argument('--cell', required=True, help='tx,tz')
        if name == 'seam':
            p.add_argument('--handoff', type=Path, help='extra-crowns format hand-off JSON (optional)')
            p.add_argument('--reader', choices=('auto', 'last', 'first'), default='auto',
                           help='standing-height reader for the trees pass (default: production cached first-return with fallback)')
        elif name != 'propose':
            p.add_argument('--handoff', required=True, type=Path, help='extra-crowns format hand-off JSON')
    a = ap.parse_args(argv)
    script_env.activate_world_from_args(a)
    cell = parse_cell(a.cell)
    if a.cmd == 'propose':
        return cmd_propose(cell)
    if a.cmd == 'seam':
        return cmd_seam(cell, a.handoff, a.reader)
    if a.cmd == 'plan':
        return cmd_plan(cell, a.handoff)
    if a.cmd in ('lines-plan', 'lines-apply'):
        return cmd_lines(cell, a.handoff, a.cmd == 'lines-apply')
    return cmd_apply(cell, a.handoff)


if __name__ == '__main__':
    raise SystemExit(main())
