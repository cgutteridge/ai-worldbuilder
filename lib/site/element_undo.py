#!/usr/bin/env python3
"""Undo one element — or everything above ghost in a cell — through the journal.

Plan name (features) or ``--cell tx,tz --pass feature|ground|trees|decor``. With
``--cell`` alone, undo every Element above ghost in fixed reverse-build order
(decor, trees, features, ground); stop at the first refusal and report what is
left (#134). Dry run by default; ``--apply`` needs ``SITE_OWNER`` and a claimed
lock (prefer ``scripts/site_lock.py run``). Trees and decor keep the mask
integrity path (``mask_undo``); feature and ground go straight to ``site_kit.undo``.

  python3 scripts/element_undo.py --world <name> house
  python3 scripts/element_undo.py --world <name> --cell 48,-44 --pass ground
  python3 scripts/element_undo.py --world <name> --cell 48,-44
  SITE_OWNER=<owner> python3 scripts/element_undo.py --world <name> --cell 48,-44 --apply
"""
from __future__ import annotations

import argparse
import os
import sys

ELEMENTS = ('feature', 'ground', 'trees', 'decor')
MASK_PASSES = frozenset({'trees', 'decor'})
# Reverse of build order (purge skill §1 / layered passes): decor first.
CELL_UNDO_ORDER = ('decor', 'trees', 'feature', 'ground')


def resolve_target(*, plan: str | None = None, cell: tuple[int, int] | None = None,
                   pass_name: str | None = None):
    """Pick a journal Undo target from CLI-shaped arguments.

    @param plan: feature (or other) plan name
    @param cell: ``(tx, tz)`` when undoing a cell pass or the whole cell
    @param pass_name: one of :data:`ELEMENTS`, or None for whole-cell (#134)
    @returns: plan name string, or ``((tx, tz), pass_or_None)``
    @raises SystemExit: missing, ambiguous, or invalid combination
    """
    if plan and (cell is not None or pass_name is not None):
        raise SystemExit('use either a plan name or --cell with --pass, not both')
    if plan:
        return plan
    if cell is not None and pass_name:
        if pass_name not in ELEMENTS:
            raise SystemExit(f'--pass must be one of {ELEMENTS}, got {pass_name!r}')
        return (tuple(cell), pass_name)
    if cell is not None and pass_name is None:
        return (tuple(cell), None)
    raise SystemExit('need a plan name, --cell tx,tz [--pass <element>], or --cell alone for the whole cell')


def _label(target) -> str:
    if isinstance(target, str):
        return target
    (tx, tz), pass_ = target
    if pass_ is None:
        return f'{tx},{tz} (whole cell)'
    return f'{tx},{tz} {pass_}'


def _journal_preview(target) -> tuple[list[str], int]:
    """Names and block count the journal would undo (no writes).

    @param target: plan name or ``(cell, pass)``
    @returns: ``(plan_names, block_count)``
    @raises SystemExit: journal has nothing to undo
    """
    import cell_registry

    registry = cell_registry.view_registry()
    if isinstance(target, str):
        names = [target]
        entries = [e for e in registry.entries(plan=target)
                   if e.kind == 'build' and not e.undone and e.status != 'begun']
        if not entries:
            raise SystemExit(f'nothing to undo for {target!r}')
    else:
        cell, pass_ = target
        names = sorted({e.plan for e in registry.entries(cells=[cell])
                        if e.kind == 'build' and e.pass_ == pass_ and not e.undone
                        and e.status != 'begun'
                        and (e.cell == cell or pass_ == 'feature')})
        if not names:
            raise SystemExit(f'nothing to undo for {_label(target)!r}')
    positions = [p for n in names for p in (registry.recorded(n) or {})]
    return names, len(positions)


def require_rebuild_ready(target):
    """Check every touched cell before undo can remove a rebuild's prerequisites."""
    import cell_registry
    import cellmath
    import site_lock
    from cell_readiness import describe_blockers
    from readiness_evidence import policy_for

    site_lock.check()
    registry = cell_registry.view_registry()
    targets = ([(target[0], p) for p in passes_above_ghost(target[0])]
               if isinstance(target, tuple) and target[1] is None else [target])
    cells = set()
    undoing = set()     # an undo removes its own mismatched apply, so that mismatch cannot block it
    for part in targets:
        names, _ = _journal_preview(part)
        undoing.update(names)
        if isinstance(part, tuple):
            cells.add(part[0])
        for name in names:
            for entry in registry.entries(plan=name):
                if entry.kind == 'build' and not entry.undone and entry.status != 'begun':
                    cells.update(entry.cells)
            cells.update(cellmath.cell_of(x, z) for x, _y, z in (registry.recorded(name) or {}))
    policy = policy_for(registry)
    refused = []
    for cell in sorted(cells):
        report = policy.check(cell, 'build', redoing=undoing)
        if not report['ready']:
            label = f'{cell[0]},{cell[1]}'
            refused.append(f"{label}: {describe_blockers(report)}; "
                           f'run cell_registry.py --world <name> prepare --cell {label} '
                           '(resolve listed aerial coverage/cache steps first)')
    if refused:
        raise SystemExit('undo refused before writes: needs-prep\n' + '\n'.join(refused)
                         + '\nUse --force to undo without rebuild readiness.')


def _apply_journal(target) -> int:
    """Live ``site_kit.undo`` after SITE_OWNER check.

    @param target: plan name or ``(cell, pass)``
    @returns: 0
    @raises SystemExit: owner missing or undo refused
    """
    if not os.environ.get('SITE_OWNER'):
        raise SystemExit('SITE_OWNER is not set; claim the lock first (scripts/site_lock.py run ...)')
    from site_kit import undo
    undo(target, strict=True)
    return 0


def passes_above_ghost(cell: tuple[int, int]) -> list[str]:
    """Elements present above ghost in ``cell``, in :data:`CELL_UNDO_ORDER`.

    Matches :func:`scripts.cell_registry.cells_above_ghost` membership: generated
    ground, trees, decor, or any feature that is not merely planned.

    @param cell: ``(tx, tz)``
    @returns: pass names to undo (subset of :data:`CELL_UNDO_ORDER`)
    """
    import cell_registry

    v = cell_registry.view_registry().cell(tuple(cell))
    present: list[str] = []
    for pass_ in CELL_UNDO_ORDER:
        if pass_ == 'decor' and v.decor:
            present.append(pass_)
        elif pass_ == 'trees' and v.trees:
            present.append(pass_)
        elif pass_ == 'feature' and any(f.status != 'planned' for f in v.features):
            present.append(pass_)
        elif pass_ == 'ground' and v.ground == 'generated':
            present.append(pass_)
    return present


def _run_one_pass(cell: tuple[int, int], pass_name: str, *, live: bool,
                  skip_changed: bool) -> int:
    """Undo a single cell pass (preview or apply). Same path as a lone ``--pass``.

    @param cell: ``(tx, tz)``
    @param pass_name: one of :data:`ELEMENTS`
    @param live: write when True
    @param skip_changed: trees/decor mask path only
    @returns: 0 on success
    @raises SystemExit: nothing to undo or apply refused
    """
    target = (tuple(cell), pass_name)
    names, n = _journal_preview(target)

    if pass_name in MASK_PASSES:
        from pass_mask import mask_path
        import mask_undo
        if mask_path(pass_name, cell).exists():
            return mask_undo.run(cell, pass_name, live, skip_changed)

    print(f'undo {_label(target)}: {len(names)} plan(s) ({", ".join(names)}), {n} blocks')
    if not live:
        print('(dry run; pass --apply to write)')
        return 0
    return _apply_journal(target)


def run_whole_cell(cell: tuple[int, int], *, live: bool = False,
                   skip_changed: bool = False, force: bool = False) -> int:
    """Undo every Element above ghost in ``cell``, in :data:`CELL_UNDO_ORDER`.

    Dry run lists the ordered passes and previews each without writing. ``--apply``
    undoes one pass at a time; the first refusal stops the chain and reports what
    is left (including the refused pass). Under the site lock when live (via
    ``SITE_OWNER`` / ``site_lock.py run``).

    @param cell: ``(tx, tz)``
    @param live: when True, write through the journal
    @param skip_changed: forwarded to mask_undo for trees/decor
    @param force: skip rebuild readiness checks for deliberate destructive cleanup
    @returns: 0 when every present pass undid cleanly
    @raises SystemExit: nothing above ghost, or a pass refused mid-chain
    """
    if live and not force:
        require_rebuild_ready((tuple(cell), None))
    tx, tz = cell
    order = passes_above_ghost(cell)
    if not order:
        raise SystemExit(f'nothing above ghost to undo in {tx},{tz}')

    print(f'undo cell {tx},{tz}: {", ".join(order)}')
    undone: list[str] = []
    left = list(order)
    for pass_name in order:
        try:
            _run_one_pass(cell, pass_name, live=live, skip_changed=skip_changed)
        except SystemExit as exc:
            print(f'stopped at {pass_name}: {exc}')
            print(f'undone: {", ".join(undone) if undone else "(none)"}')
            print(f'left: {", ".join(left)}')
            raise SystemExit(
                f'stopped at {pass_name}; left: {", ".join(left)}'
            ) from exc
        undone.append(pass_name)
        left.pop(0)

    if not live:
        print('(dry run; pass --apply to write)')
    else:
        print(f'undone: {", ".join(undone)}')
        print('left: (none)')
    return 0


def run(*, plan: str | None = None, cell: tuple[int, int] | None = None,
        pass_name: str | None = None, live: bool = False,
        skip_changed: bool = False, force: bool = False) -> int:
    """Plan (and with ``live`` apply) an element undo. Returns 0 on success.

    ``--cell`` alone undoes the whole cell (#134). Trees/decor with a mask on disk
    delegate to ``mask_undo`` (conflict check + archive). Feature/ground, and
    trees/decor without a mask, use ``site_kit.undo``. Dry run prints what the
    journal would erase and writes nothing. Refuses when the journal shows
    nothing to undo.

    @param plan: plan name (mutually exclusive with cell/pass)
    @param cell: ``(tx, tz)`` for a cell pass or whole-cell undo
    @param pass_name: feature | ground | trees | decor, or None for whole cell
    @param live: when True, write through the journal (needs SITE_OWNER + lock)
    @param skip_changed: passed to mask_undo for trees/decor only
    @param force: skip rebuild readiness checks for deliberate destructive cleanup
    @returns: 0 on success
    @raises SystemExit: nothing to undo, lock/owner missing, or apply failure
    """
    target = resolve_target(plan=plan, cell=cell, pass_name=pass_name)
    if isinstance(target, tuple) and target[1] is None:
        return run_whole_cell(target[0], live=live, skip_changed=skip_changed, force=force)

    names, n = _journal_preview(target)
    if live and not force:
        require_rebuild_ready(target)

    if isinstance(target, tuple) and target[1] in MASK_PASSES:
        from pass_mask import mask_path
        import mask_undo
        if mask_path(target[1], target[0]).exists():
            return mask_undo.run(target[0], target[1], live, skip_changed)

    print(f'undo {_label(target)}: {len(names)} plan(s) ({", ".join(names)}), {n} blocks')
    if not live:
        print('(dry run; pass --apply to write)')
        return 0
    return _apply_journal(target)


def main(argv: list[str] | None = None) -> int:
    """CLI entry after ``scripts/element_undo.py`` has activated ``--world``.

    @param argv: argument vector without the program name
    @returns: process exit code
    """
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('plan', nargs='?', help='plan name to undo (features)')
    ap.add_argument('--cell', help='tx,tz for a cell pass or whole-cell undo')
    ap.add_argument('--pass', dest='pass_name', choices=ELEMENTS,
                    help='element to undo in --cell (omit for whole cell)')
    ap.add_argument('--apply', action='store_true',
                    help='LIVE: needs SITE_OWNER + claimed lock')
    ap.add_argument('--force', action='store_true', help='undo even when rebuild inputs are not ready')
    ap.add_argument('--skip-changed', action='store_true',
                    help='trees/decor only: leave mask positions that no longer match')
    args = ap.parse_args(argv)
    cell = None
    if args.cell:
        parts = args.cell.split(',')
        if len(parts) != 2:
            raise SystemExit(f'--cell must be tx,tz, got {args.cell!r}')
        cell = (int(parts[0]), int(parts[1]))
    return run(plan=args.plan, cell=cell, pass_name=args.pass_name,
               live=args.apply, skip_changed=args.skip_changed, force=args.force)


if __name__ == '__main__':
    sys.exit(main())
