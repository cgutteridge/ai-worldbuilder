#!/usr/bin/env python3
"""Cell registry CLI for the active world, over the cell journal (ADR 004).

The only record is ``<world>/registry/journal.jsonl``, written by
``cell_journal.CellRegistry.change`` (``site_kit.apply`` for builds, ``Note`` for marker / hold /
coverage / issue facts). Everything here reads :meth:`CellRegistry.view` and friends; there are
no setters. Open the active world's registry with :func:`open_registry`.

Per-cell view (folded, never stored)::

    ground: none | ghost | generated
    trees, decor: bool
    features: (id, in-progress | built) for every feature plan touching the cell
    marker: bool (single marker)   hold: reason | None   notes: coverage warnings, reviews, issues
    done / left: ground generated + trees + decor + every feature built + no marker + no open
                 coverage warning or issue + nothing in flight

  python3 scripts/cell_registry.py status [--cell tx,tz]
  python3 scripts/cell_registry.py query --feature <id>
  python3 scripts/cell_registry.py query --cell tx,tz
  python3 scripts/cell_registry.py propose-features --cell tx,tz
  python3 scripts/cell_registry.py hold set tx,tz --reason R
  python3 scripts/cell_registry.py hold clear tx,tz
  python3 scripts/cell_registry.py dismiss-coverage tx,tz --warning-id ID --reason R
  python3 scripts/cell_registry.py note tx,tz --kind review --ref REF --body TEXT
  python3 scripts/cell_registry.py next
  python3 scripts/cell_registry.py add-issue --cell tx,tz [--layer trees] --reason R
  python3 scripts/cell_registry.py add-issue --feature ID --reason R
  python3 scripts/cell_registry.py issues
  python3 scripts/cell_registry.py verify
  python3 scripts/cell_registry.py reconcile [CELL ...] [--fix]

reconcile compares the journal with the blocks (dry run by default; ``--fix`` re-commits over MCP).
site_recover = resolve an interrupted apply (land or roll back); reconcile = detect and repair drift forward.

A feature is registered by its first apply (a ``feature`` Build); there is no register command.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from script_env import ROOT, make_parser, add_world_arg, activate_world_from_args  # noqa: E402

from block_store import BlockStoreError  # noqa: E402
from cell_journal import CellJournalError, CellRegistry, CellView, Note  # noqa: E402


def _json_default(obj: Any) -> Any:
    """JSON fallback for ``missing_features`` tuples (bbox) and similar.

    @param obj: non-JSON-native value
    @returns: list when ``obj`` is a tuple
    @raises TypeError: for unsupported types
    """
    if isinstance(obj, tuple):
        return list(obj)
    raise TypeError(f'Object of type {type(obj).__name__} is not JSON serializable')

REGISTRY_ROOT_ENV = 'AI_WORLDBUILDER_REGISTRY_ROOT'


class RegistryError(ValueError):
    """Bad registry CLI input (cell id, note kind)."""


def cell_id(tx: int, tz: int) -> str:
    """Canonical cell id string ``tx,tz``.

    @param tx: cell X index
    @param tz: cell Z index
    @returns: ``\"tx,tz\"``
    """
    return f'{tx},{tz}'


def parse_cell_id(spec: str) -> tuple[int, int]:
    """Parse a cell id string.

    @param spec: ``tx,tz``
    @returns: (tx, tz)
    @raises RegistryError: on bad input
    """
    parts = [p.strip() for p in str(spec).split(',')]
    if len(parts) != 2:
        raise RegistryError(f'expected cell id tx,tz got {spec!r}')
    try:
        return int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise RegistryError(f'expected cell id tx,tz got {spec!r}') from exc


def registry_root(root: Path | str | None = None) -> Path:
    """World root holding ``registry/journal.jsonl``.

    @param root: explicit root (wins)
    @returns: ``root``, else ``$AI_WORLDBUILDER_REGISTRY_ROOT`` (tests), else the active world
    """
    if root is not None:
        return Path(root)
    env = os.environ.get(REGISTRY_ROOT_ENV)
    return Path(env) if env else Path(ROOT)


class _NoReads:
    """Block store of a registry opened only for views: any block read is refused."""

    def read(self, pos):
        raise BlockStoreError('this registry was opened read-only (no block store); pass blocks=')

    def read_many(self, positions):
        raise BlockStoreError('this registry was opened read-only (no block store); pass blocks=')

    def flush(self) -> None:
        return None


def default_ghosts() -> Callable | None:
    """Ghost planner for the active world: lidar ``plan_ghost_*`` builders (tests patch this).

    @returns: ``callable(cell, 'ground'|'features') -> {pos: state} | None``
    """
    import lidar_ghost
    return lidar_ghost.ghost_planner()


def open_registry(root: Path | str | None = None, *, blocks=None, ghosts: Any = 'default',
                  ring: int | None = None) -> CellRegistry:
    """The active world's :class:`CellRegistry`.

    @param root: world root (default :func:`registry_root`)
    @param blocks: BlockStore for readback / reconcile; None opens a view-only registry
    @param ghosts: ghost planner; ``'default'`` uses :func:`default_ghosts`, None disables ghosts
    @param ring: frontier ring depth (default ``LIDAR_GHOST_RING`` or 2)
    @returns: CellRegistry
    """
    if ghosts == 'default':
        ghosts = default_ghosts()
    if ring is None:
        import lidar_ghost
        ring = lidar_ghost.ring_from_env()
    return CellRegistry(registry_root(root), blocks if blocks is not None else _NoReads(), ghosts, ring=ring)


def view_registry(root: Path | str | None = None) -> CellRegistry:
    """View-only registry without a ghost planner (fast; for readers that never plan ghosts).

    @param root: world root
    @returns: CellRegistry
    """
    return CellRegistry(registry_root(root), _NoReads(), None)


def is_empty(v: CellView) -> bool:
    """Whether a cell view is still at defaults (old ``RegistryStore.is_empty``).

    @param v: CellView
    @returns: True when nothing is recorded for the cell
    """
    return (v.ground == 'none' and not v.trees and not v.decor and not v.marker and not v.hold
            and not any(n.kind == 'coverage' for n in v.notes) and not v.features and not v.in_flight)


def coverage_warnings(v: CellView) -> list[dict[str, str]]:
    """Open vector-map coverage warnings of a cell view.

    @param v: CellView
    @returns: ``[{'id', 'detail'}]``
    """
    return [{'id': n.ref, 'detail': n.body} for n in v.notes if n.kind == 'coverage']


def feature_cells(reg: CellRegistry) -> dict[str, dict[str, Any]]:
    """Every feature in the journal: ``{id: {'status', 'cells': ['tx,tz', ...]}}`` (latest live entry).

    @param reg: registry
    @returns: feature map
    """
    out: dict[str, dict[str, Any]] = {}
    for v in reg.view().values():
        for f in v.features:
            rec = out.setdefault(f.id, {'status': f.status, 'cells': []})
            rec['cells'].append(cell_id(*v.cell))
    return out


def latest_entries(reg: CellRegistry, *, prefix: str = '', ghosts: bool = False) -> list:
    """The latest live (not undone) build entry of every plan, oldest first.

    @param reg: registry
    @param prefix: only plan names starting with this
    @param ghosts: include lidar ghost plans
    @returns: [EntryView, ...] in seq order
    """
    latest = {}
    for e in reg.entries():
        if e.kind == 'build' and not e.undone and e.plan.startswith(prefix) and (
                ghosts or not e.pass_.startswith('ghost-')):
            latest[e.plan] = e
    return sorted(latest.values(), key=lambda e: e.seq)


def latest_cells(reg: CellRegistry | None = None, *, prefix: str = '', ghosts: bool = False) -> dict:
    """Every recorded block of the latest plan of each build, later builds win (air included).

    The journal replacement of merging every ``*-cells.json`` by mtime (site_audit, site_patrol,
    door_audit, osm_footprints).

    @param reg: registry (default: view registry of the active world)
    @param prefix: only plans whose name starts with this
    @param ghosts: include lidar ghost plans (default no: grey massing is not a build)
    @returns: {(x, y, z): (plan, state)}
    """
    reg = reg or view_registry()
    out: dict = {}
    for e in latest_entries(reg, prefix=prefix, ghosts=ghosts):
        for pos, st in (reg.recorded(e.plan) or {}).items():
            out[pos] = (e.plan, st)
    return out


def _format_status(reg: CellRegistry, cid: str | None) -> str:
    lines = []
    cur = reg.marker_cell()
    lines.append(f'current_cell: {cell_id(*cur) if cur else "(none)"}')
    if cid is None:
        views = reg.view()
        lines.append(f'registered_cells: {len(views)}')
        lines.append(f'registered_features: {len(feature_cells(reg))}')
        flying = [e for e in reg.entries() if e.status == 'begun']
        lines.append(f'in_flight: {len(flying)}')
        return '\n'.join(lines)
    v = reg.cell(parse_cell_id(cid))
    lines.append(f'cell {cid}:')
    lines.append(f'  ground={v.ground} trees={v.trees} decor={v.decor} '
                 f'marker={"up" if v.marker else "none"} hold={v.hold!r}')
    lines.append(f'  empty={is_empty(v)} done={v.done}')
    lines.append(f'  left: {", ".join(v.left) if v.left else "(nothing)"}')
    for f in v.features:
        lines.append(f'  feature {f.id}: {f.status}')
    for n in v.notes:
        label = 'coverage warning' if n.kind == 'coverage' else n.kind
        lines.append(f'  {label} {n.ref}: {n.body}')
    return '\n'.join(lines)


def propose_feature_candidates(cid: str) -> list[dict[str, Any]]:
    """Propose vector-map building feature candidates for a cell (helper for register).

    Calls ``cell_features.missing_features`` for the cell. Errors propagate
    (no silent empty list).

    @param cid: ``tx,tz``
    @returns: list of candidate dicts from ``missing_features`` (kind/label/refs/…)
    """
    import cell_features as cf  # noqa: WPS433
    tx, tz = parse_cell_id(cid)
    return list(cf.missing_features(tx, tz))


def note(reg: CellRegistry, cell: tuple[int, int], kind: str, body: str = '', ref: str = '') -> None:
    """Journal one Note (the only way to set marker / hold / coverage / review / issue facts).

    @param reg: registry
    @param cell: (tx, tz)
    @param kind: note kind (cell_journal.NOTE_KINDS)
    @param body: text
    @param ref: reference id
    """
    reg.change(Note(tuple(cell), kind, body, ref))


DRIFT_ORDER = ('in-flight', 'missing', 'foreign', 'ghost-missing', 'ghost-stale')
DRIFT_NOTES = {
    'in-flight': 'an apply began and never finished (site_recover.py can also roll it back)',
    'missing': 'journal owns the block, the world has air / nothing',
    'foreign': 'journal owns the block, the world holds another block',
    'ghost-missing': 'frontier cell lacks ghost ground / features',
    'ghost-stale': 'ghost features still inside a real feature footprint',
}
RECONCILE_HELP = ('compare the journal with the blocks; dry run unless --fix. '
                  'site_recover = resolve an interrupted apply (land or roll back); '
                  'reconcile = detect and repair drift forward')


def format_drift(drift, *, fix: bool = False) -> str:
    """Human report of a :class:`cell_journal.Drift`, grouped by kind.

    @param drift: result of ``CellRegistry.reconcile``
    @param fix: the run was a ``--fix`` run (marks each cell fixed / still drifting)
    @returns: report text
    """
    if not drift.items and not drift.upgraded:
        return 'reconcile: clean (the journal matches the blocks)'
    lines = []
    for kind in DRIFT_ORDER:
        items = drift.by_kind(kind)
        if not items:
            continue
        lines.append(f'{kind}: {sum(i.count for i in items)} in {len(items)} cell(s), {DRIFT_NOTES[kind]}')
        for i in sorted(items, key=lambda i: (i.cell, i.seq)):
            tag = (' [fixed]' if i.fixed else ' [NOT fixed]') if fix else ''
            what = f'{i.count} position(s)' if i.count or i.kind != 'in-flight' else 'every planned block is present'
            lines.append(f'  {cell_id(*i.cell)}  {what}  plans {", ".join(i.plans) or "-"}'
                         f'{f"  seq {i.seq}" if i.seq else ""}{tag}')
            for pos, (want, got) in list(i.sample.items())[:3]:
                lines.append(f'      {pos}: want {want}, got {got}')
    left = [i for i in drift.items if not i.fixed]
    if drift.upgraded:
        lines.append(f'upgraded {drift.upgraded} mismatched entr{"y" if drift.upgraded == 1 else "ies"} to landed')
    if fix:
        lines.append(f'repaired {len(drift.items) - len(left)} of {len(drift.items)} finding(s)'
                     + ('; journalled signs/entities are re-written with the blocks' if drift.items else ''))
    else:
        lines.append(f'{len(drift.items)} finding(s); dry run, nothing changed. Re-run with --fix to repair over MCP')
    return '\n'.join(lines)


def run_reconcile(root: Path | None, cells: list[str], fix: bool) -> int:
    """The ``reconcile`` command: report drift, and with ``fix`` repair it over MCP.

    Dry run reads the saved world through a read-only registry; ``fix`` goes through
    ``site_kit.live_session`` (region gate and lock).

    @param root: world root (None: active world)
    @param cells: ``tx,tz`` specs (empty: every cell the journal mentions)
    @param fix: repair what is found
    @returns: 0 when nothing is left drifting, 1 otherwise
    """
    import site_kit
    scope = [parse_cell_id(c) for c in cells] or None
    if fix:
        reg, writer = site_kit.live_session(root=root)
        drift = reg.reconcile(scope, fix=True, apply=writer)
    else:
        reg = open_registry(root, blocks=site_kit.live_blocks())
        drift = reg.reconcile(scope)
    print(format_drift(drift, fix=fix))
    if not drift.items:
        return 0
    return 0 if fix and drift.clean else 1


def main(argv: list[str] | None = None) -> int:
    """Management CLI for the cell registry.

    @param argv: optional argv override
    @returns: exit code
    """
    ap = make_parser(__doc__)
    add_world_arg(ap)
    ap.add_argument('--root', type=Path, default=None,
                    help='registry root override (default: active world)')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('status', help='print registry summary or one cell')
    p.add_argument('--cell', default=None, help='tx,tz')

    p = sub.add_parser('query', help='query a feature or what is left in a cell')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--feature', help='feature id (build slug)')
    g.add_argument('--cell', help='tx,tz')

    p = sub.add_parser('propose-features', help='list vector-map feature candidates for a cell')
    p.add_argument('--cell', required=True)

    p = sub.add_parser('hold', help='set or clear a cell hold')
    hold_sub = p.add_subparsers(dest='hold_cmd', required=True)
    ps = hold_sub.add_parser('set')
    ps.add_argument('cell')
    ps.add_argument('--reason', required=True)
    pc = hold_sub.add_parser('clear')
    pc.add_argument('cell')

    p = sub.add_parser('dismiss-coverage', help='dismiss a vector-map coverage warning')
    p.add_argument('cell')
    p.add_argument('--warning-id', required=True)
    p.add_argument('--reason', required=True)

    p = sub.add_parser('note', help='journal a review note (e.g. trees over roof resolved)')
    p.add_argument('cell')
    p.add_argument('--kind', choices=('review', 'close'), default='review')
    p.add_argument('--ref', required=True, help='note id, e.g. trees-over-roof:x0,z0,x1,z1')
    p.add_argument('--body', default='', help='text (the reason)')

    sub.add_parser('next', help='print the next work item (issues first, then registry)')
    sub.add_parser('issues', help='list open ISSUES.txt lines')

    p = sub.add_parser('add-issue', help='append a snagging issue and note it on the target cells')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--cell', help='tx,tz')
    g.add_argument('--feature', help='feature id')
    p.add_argument('--layer', choices=('ground', 'trees', 'decor'), default=None)
    p.add_argument('--reason', required=True)

    p = sub.add_parser('verify', help='registry integrity report (exit 1 on errors)')
    p.add_argument('--json', action='store_true', help='print JSON report')
    p.add_argument('--check-world', action='store_true',
                   help='also check region files for non-empty cells')

    p = sub.add_parser('reconcile', help=RECONCILE_HELP, description=RECONCILE_HELP)
    p.add_argument('cells', nargs='*', metavar='CELL', help='tx,tz (default: every cell in the journal)')
    p.add_argument('--fix', action='store_true',
                   help='re-commit recorded plans over MCP (needs the lock and the region gate); block cells only')

    args = ap.parse_args(argv)
    activate_world_from_args(args)
    if args.cmd == 'reconcile':
        try:
            return run_reconcile(args.root, args.cells, args.fix)
        except (RegistryError, CellJournalError) as exc:
            print(f'registry error: {exc}', file=sys.stderr)
            return 2
    root = registry_root(args.root)
    reg = view_registry(root)

    try:
        if args.cmd == 'status':
            print(_format_status(reg, args.cell))
            return 0

        if args.cmd == 'query':
            if args.feature:
                feat = feature_cells(reg).get(args.feature)
                if feat is None:
                    print(f'feature {args.feature}: not in the journal', file=sys.stderr)
                    return 1
                print(json.dumps({'id': args.feature, **feat}, indent=2, sort_keys=True))
                print(f'built: {feat.get("status") == "built"}')
                return 0
            print(_format_status(reg, args.cell))
            return 0

        if args.cmd == 'propose-features':
            for item in propose_feature_candidates(args.cell):
                print(json.dumps(item, sort_keys=True, default=_json_default))
            return 0

        if args.cmd == 'hold':
            cell = parse_cell_id(args.cell)
            if args.hold_cmd == 'set':
                note(reg, cell, 'hold', args.reason)
                print(f'hold set on {args.cell}: {args.reason}')
            else:
                note(reg, cell, 'hold', '')
                print(f'hold cleared on {args.cell}')
            return 0

        if args.cmd == 'dismiss-coverage':
            note(reg, parse_cell_id(args.cell), 'coverage-dismiss', args.reason, args.warning_id)
            print(f'dismissed {args.warning_id} on {args.cell}')
            return 0

        if args.cmd == 'note':
            note(reg, parse_cell_id(args.cell), args.kind, args.body, args.ref)
            print(f'{args.kind} {args.ref} on {args.cell}')
            return 0

        if args.cmd == 'next':
            from registry_next import format_next, next_task
            task = next_task(reg, root=root)
            print(format_next(task))
            if task is not None:
                print(json.dumps(task, indent=2, sort_keys=True))
            return 0

        if args.cmd == 'issues':
            from registry_issues import load_issues
            for issue in load_issues(root):
                print(issue.raw)
            return 0

        if args.cmd == 'add-issue':
            from registry_issues import add_issue
            if args.feature:
                issue = add_issue(reason=args.reason, feature_id=args.feature, root=root, registry=reg)
            else:
                issue = add_issue(reason=args.reason, cell=parse_cell_id(args.cell), layer=args.layer,
                                  root=root, registry=reg)
            print(f'added {issue.raw}')
            return 0

        if args.cmd == 'verify':
            import registry_verify as RV
            report = RV.run_verify(root=root, check_world=bool(args.check_world))
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True))
            else:
                print(RV.format_report(report))
            return RV.exit_code(report)
    except (RegistryError, CellJournalError) as exc:
        print(f'registry error: {exc}', file=sys.stderr)
        return 2

    return 2


if __name__ == '__main__':
    raise SystemExit(main())
