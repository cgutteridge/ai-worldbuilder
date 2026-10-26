#!/usr/bin/env python3
"""Undo one element through the cell journal — feature, ground, trees, or decor.

Plan name (features) or ``--cell tx,tz --pass feature|ground|trees|decor``. Dry run by
default; ``--apply`` needs ``SITE_OWNER`` and a claimed lock (prefer
``scripts/site_lock.py run``). Trees and decor keep the mask integrity path
(``mask_undo``); feature and ground go straight to ``site_kit.undo``.

  python3 scripts/element_undo.py --world <name> house
  python3 scripts/element_undo.py --world <name> --cell 48,-44 --pass ground
  SITE_OWNER=<owner> python3 scripts/element_undo.py --world <name> house --apply
"""
from __future__ import annotations

import argparse
import os
import sys

ELEMENTS = ('feature', 'ground', 'trees', 'decor')
MASK_PASSES = frozenset({'trees', 'decor'})


def resolve_target(*, plan: str | None = None, cell: tuple[int, int] | None = None,
                   pass_name: str | None = None):
    """Pick a journal Undo target from CLI-shaped arguments.

    @param plan: feature (or other) plan name
    @param cell: ``(tx, tz)`` when undoing a cell pass
    @param pass_name: one of :data:`ELEMENTS`
    @returns: plan name string, or ``((tx, tz), pass)``
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
    raise SystemExit('need a plan name, or both --cell tx,tz and --pass <element>')


def _label(target) -> str:
    if isinstance(target, str):
        return target
    (tx, tz), pass_ = target
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


def run(*, plan: str | None = None, cell: tuple[int, int] | None = None,
        pass_name: str | None = None, live: bool = False,
        skip_changed: bool = False) -> int:
    """Plan (and with ``live`` apply) an element undo. Returns 0 on success.

    Trees/decor with a mask on disk delegate to ``mask_undo`` (conflict check + archive).
    Feature/ground, and trees/decor without a mask, use ``site_kit.undo``. Dry run prints
    what the journal would erase and writes nothing. Refuses when the journal shows
    nothing to undo.

    @param plan: plan name (mutually exclusive with cell/pass)
    @param cell: ``(tx, tz)`` for a cell pass undo
    @param pass_name: feature | ground | trees | decor
    @param live: when True, write through the journal (needs SITE_OWNER + lock)
    @param skip_changed: passed to mask_undo for trees/decor only
    @returns: 0 on success
    @raises SystemExit: nothing to undo, lock/owner missing, or apply failure
    """
    target = resolve_target(plan=plan, cell=cell, pass_name=pass_name)
    names, n = _journal_preview(target)

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
    ap.add_argument('--cell', help='tx,tz for a cell pass undo')
    ap.add_argument('--pass', dest='pass_name', choices=ELEMENTS,
                    help='element to undo in --cell')
    ap.add_argument('--apply', action='store_true',
                    help='LIVE: needs SITE_OWNER + claimed lock')
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
               live=args.apply, skip_changed=args.skip_changed)


if __name__ == '__main__':
    sys.exit(main())
