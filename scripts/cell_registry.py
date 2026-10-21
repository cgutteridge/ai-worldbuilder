#!/usr/bin/env python3
"""Cell and feature registry for the active world (tickets #29–#33).

World data lives under ``<world>/registry/cells.json`` and
``registry/features.json``. Sparse: an absent cell is empty. All writes go
through :class:`RegistryStore` (atomic JSON, sorted keys). LLMs should read
state only via this module's management CLI — not by editing the JSON.

Cell record fields (stored)::

    ground: none | ghost | ghost+features | generated
    trees: bool
    decor: bool
    marker: up | none
    hold: reason string | null
    coverage_warnings: [{id, detail}, ...]
    coverage_dismissals: [{id, reason, detail?}, ...]

Derived (never stored): empty, DONE, current cell (= marker cell), features in
a cell. DONE = ground generated + trees + decor + every registered feature in
the cell built + no marker + no open vector-map coverage warnings.

Feature record::

    id (= build slug / site/builds/<name>.json stem)
    name, cells (list of 'tx,tz'), status: planned | in-progress | built

Invariants (refused write raises :class:`RegistryError` naming the rule; nothing
changes on disk)::

    - single marker
    - ghost+features only when current ground is ghost or ghost+features
    - trees/decor only on generated ground
    - rebuilding ground clears trees+decor
    - reopening a feature clears trees+decor in every cell it touches

  python3 scripts/cell_registry.py status [--cell tx,tz]
  python3 scripts/cell_registry.py query --feature <id>
  python3 scripts/cell_registry.py query --cell tx,tz
  python3 scripts/cell_registry.py register-feature <id> --name N --cells a,b:c,d
  python3 scripts/cell_registry.py propose-features --cell tx,tz
  python3 scripts/cell_registry.py hold set tx,tz --reason R
  python3 scripts/cell_registry.py hold clear tx,tz
  python3 scripts/cell_registry.py dismiss-coverage tx,tz --warning-id ID --reason R
  python3 scripts/cell_registry.py next
  python3 scripts/cell_registry.py add-issue --cell tx,tz [--layer trees] --reason R
  python3 scripts/cell_registry.py add-issue --feature ID --reason R
  python3 scripts/cell_registry.py issues
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from script_env import ROOT, make_parser  # noqa: E402

GROUND_VALUES = frozenset({'none', 'ghost', 'ghost+features', 'generated'})
MARKER_VALUES = frozenset({'up', 'none'})
FEATURE_STATUS = frozenset({'planned', 'in-progress', 'built'})

DEFAULT_CELL: dict[str, Any] = {
    'ground': 'none',
    'trees': False,
    'decor': False,
    'marker': 'none',
    'hold': None,
    'coverage_warnings': [],
    'coverage_dismissals': [],
}


class RegistryError(ValueError):
    """A registry write was refused; the named invariant would be broken."""


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


def _write_json_sorted(path: Path, value: Any) -> None:
    """Atomic JSON write with sorted object keys."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    temp = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


class RegistryStore:
    """Sole writer for ``registry/cells.json`` and ``registry/features.json``.

    @param root: world root (default: active world ``ROOT``)
    """

    def __init__(self, root: Path | None = None) -> None:
        if root is None:
            env = os.environ.get('AI_WORLDBUILDER_REGISTRY_ROOT')
            root = Path(env) if env else Path(ROOT)
        self.root = Path(root)
        self.dir = self.root / 'registry'
        self.cells_path = self.dir / 'cells.json'
        self.features_path = self.dir / 'features.json'

    def _load_cells(self) -> dict[str, dict]:
        if not self.cells_path.exists():
            return {}
        return json.loads(self.cells_path.read_text(encoding='utf-8'))

    def _load_features(self) -> dict[str, dict]:
        if not self.features_path.exists():
            return {}
        return json.loads(self.features_path.read_text(encoding='utf-8'))

    def _save(self, cells: dict[str, dict], features: dict[str, dict]) -> None:
        _write_json_sorted(self.cells_path, cells)
        _write_json_sorted(self.features_path, features)

    def _ensure_cell(self, cells: dict[str, dict], cid: str) -> dict:
        parse_cell_id(cid)
        if cid not in cells:
            cells[cid] = deepcopy(DEFAULT_CELL)
        return cells[cid]

    def cell(self, cid: str) -> dict[str, Any]:
        """Return a cell record (defaults if absent). Does not create on disk.

        @param cid: ``tx,tz``
        @returns: cell dict (copy)
        """
        parse_cell_id(cid)
        raw = self._load_cells().get(cid)
        return deepcopy(raw) if raw is not None else deepcopy(DEFAULT_CELL)

    def feature(self, fid: str) -> dict[str, Any] | None:
        """Return a feature record or None.

        @param fid: build slug
        @returns: feature dict copy, or None
        """
        raw = self._load_features().get(fid)
        return deepcopy(raw) if raw is not None else None

    def is_empty(self, cid: str) -> bool:
        """True when the cell is absent or still at defaults with no warnings.

        @param cid: ``tx,tz``
        @returns: whether the cell counts as empty
        """
        parse_cell_id(cid)
        cells = self._load_cells()
        if cid not in cells:
            return True
        c = cells[cid]
        return (
            c.get('ground', 'none') == 'none'
            and not c.get('trees')
            and not c.get('decor')
            and c.get('marker', 'none') == 'none'
            and not c.get('hold')
            and not c.get('coverage_warnings')
        )

    def features_in_cell(self, cid: str) -> list[dict[str, Any]]:
        """Features whose ``cells`` list includes ``cid`` (registry order).

        @param cid: ``tx,tz``
        @returns: list of feature dicts with ``id`` added
        """
        parse_cell_id(cid)
        out = []
        for fid, rec in self._load_features().items():
            if cid in rec.get('cells', []):
                item = deepcopy(rec)
                item['id'] = fid
                out.append(item)
        return out

    def current_cell(self) -> tuple[int, int] | None:
        """The cell with ``marker=up``, or None.

        @returns: (tx, tz) or None
        """
        for cid, rec in self._load_cells().items():
            if rec.get('marker') == 'up':
                return parse_cell_id(cid)
        return None

    def open_coverage_warnings(self, cid: str) -> list[dict]:
        """Open (not dismissed) vector-map coverage warnings for a cell.

        @param cid: ``tx,tz``
        @returns: warning dicts
        """
        return list(self.cell(cid).get('coverage_warnings') or [])

    def is_done(self, cid: str) -> bool:
        """Whether the cell meets the DONE rule.

        @param cid: ``tx,tz``
        @returns: True when fully done
        """
        c = self.cell(cid)
        if c.get('ground') != 'generated':
            return False
        if not c.get('trees') or not c.get('decor'):
            return False
        if c.get('marker') == 'up':
            return False
        if self.open_coverage_warnings(cid):
            return False
        feats = self.features_in_cell(cid)
        if not feats:
            return True
        return all(f.get('status') == 'built' for f in feats)

    def what_is_left(self, cid: str) -> list[str]:
        """Human-readable remaining work for a cell.

        @param cid: ``tx,tz``
        @returns: list of short reasons
        """
        left: list[str] = []
        c = self.cell(cid)
        if c.get('ground') != 'generated':
            left.append(f"ground={c.get('ground', 'none')}")
        for feat in self.features_in_cell(cid):
            if feat.get('status') != 'built':
                left.append(f"feature {feat['id']}={feat.get('status')}")
        if c.get('ground') == 'generated' and not c.get('trees'):
            left.append('trees')
        if c.get('ground') == 'generated' and not c.get('decor'):
            left.append('decor')
        if c.get('marker') == 'up':
            left.append('marker up')
        for w in self.open_coverage_warnings(cid):
            left.append(f"coverage warning {w.get('id')}")
        if c.get('hold'):
            left.append(f"hold: {c['hold']}")
        return left

    def set_ground(self, cid: str, ground: str) -> None:
        """Set cell ground. Rebuild (any write) clears trees and decor.

        @param cid: ``tx,tz``
        @param ground: one of GROUND_VALUES
        @raises RegistryError: on bad value or illegal transition to ghost+features
        """
        if ground not in GROUND_VALUES:
            raise RegistryError(f'invalid ground {ground!r}; want one of {sorted(GROUND_VALUES)}')
        cells = self._load_cells()
        features = self._load_features()
        cell = self._ensure_cell(cells, cid)
        current = cell.get('ground', 'none')
        if ground == 'ghost+features' and current not in ('ghost', 'ghost+features'):
            raise RegistryError(
                'ghost+features only on ghost ground '
                f'(cell {cid} has ground={current!r})'
            )
        cell['ground'] = ground
        cell['trees'] = False
        cell['decor'] = False
        self._save(cells, features)

    def set_trees(self, cid: str, value: bool) -> None:
        """Set trees flag (only allowed when ground is generated).

        @param cid: ``tx,tz``
        @param value: trees done
        @raises RegistryError: if ground is not generated
        """
        cells = self._load_cells()
        features = self._load_features()
        cell = self._ensure_cell(cells, cid)
        if value and cell.get('ground') != 'generated':
            raise RegistryError(
                f'trees/decor only on generated ground (cell {cid} has ground={cell.get("ground")!r})'
            )
        cell['trees'] = bool(value)
        self._save(cells, features)

    def set_decor(self, cid: str, value: bool) -> None:
        """Set decor flag (only allowed when ground is generated).

        @param cid: ``tx,tz``
        @param value: decor done
        @raises RegistryError: if ground is not generated
        """
        cells = self._load_cells()
        features = self._load_features()
        cell = self._ensure_cell(cells, cid)
        if value and cell.get('ground') != 'generated':
            raise RegistryError(
                f'trees/decor only on generated ground (cell {cid} has ground={cell.get("ground")!r})'
            )
        cell['decor'] = bool(value)
        self._save(cells, features)

    def set_marker(self, cid: str, marker: str, *, replace_existing: bool = False) -> None:
        """Raise or clear the marker. At most one cell may have marker=up.

        @param cid: ``tx,tz``
        @param marker: ``up`` or ``none``
        @param replace_existing: when raising, clear any other marker first
            (used by cell_marker writers; LLM CLI keeps the refuse-on-conflict rule)
        @raises RegistryError: if raising would create a second marker and
            ``replace_existing`` is false
        """
        if marker not in MARKER_VALUES:
            raise RegistryError(f'invalid marker {marker!r}; want up|none')
        cells = self._load_cells()
        features = self._load_features()
        if marker == 'up':
            for other, rec in list(cells.items()):
                if other != cid and rec.get('marker') == 'up':
                    if not replace_existing:
                        raise RegistryError(
                            f'single marker: cell {other} already has marker=up'
                        )
                    rec['marker'] = 'none'
        cell = self._ensure_cell(cells, cid)
        cell['marker'] = marker
        self._save(cells, features)

    def set_hold(self, cid: str, reason: str | None) -> None:
        """Set or clear a cell hold.

        @param cid: ``tx,tz``
        @param reason: hold text, or None to clear
        """
        cells = self._load_cells()
        features = self._load_features()
        cell = self._ensure_cell(cells, cid)
        cell['hold'] = reason
        self._save(cells, features)

    def register_feature(
        self,
        fid: str,
        *,
        name: str,
        cells: list[str],
        status: str = 'planned',
    ) -> None:
        """Create or overwrite a feature record.

        @param fid: build slug
        @param name: display name
        @param cells: list of ``tx,tz`` ids the feature touches
        @param status: planned | in-progress | built
        @raises RegistryError: on bad status or empty cells
        """
        if status not in FEATURE_STATUS:
            raise RegistryError(f'invalid feature status {status!r}')
        if not cells:
            raise RegistryError('register-feature requires a non-empty --cells list')
        for c in cells:
            parse_cell_id(c)
        store_cells = self._load_cells()
        features = self._load_features()
        prev = features.get(fid) or {}
        prev_status = prev.get('status')
        # Union of old and new cells so a reopen still clears every prior touch.
        clear_cells = set(prev.get('cells') or []) | set(cells)
        features[fid] = {
            'name': name,
            'cells': list(cells),
            'status': status,
        }
        if prev_status == 'built' and status != 'built':
            for cid in clear_cells:
                cell = self._ensure_cell(store_cells, cid)
                cell['trees'] = False
                cell['decor'] = False
        self._save(store_cells, features)

    def set_feature_status(self, fid: str, status: str) -> None:
        """Update feature status. Reopening (leaving built) clears trees/decor.

        @param fid: build slug
        @param status: planned | in-progress | built
        @raises RegistryError: if feature missing or status invalid
        """
        if status not in FEATURE_STATUS:
            raise RegistryError(f'invalid feature status {status!r}')
        cells = self._load_cells()
        features = self._load_features()
        if fid not in features:
            raise RegistryError(f'unknown feature {fid!r}')
        prev = features[fid].get('status')
        features[fid]['status'] = status
        if prev == 'built' and status != 'built':
            for cid in features[fid].get('cells', []):
                cell = self._ensure_cell(cells, cid)
                cell['trees'] = False
                cell['decor'] = False
        self._save(cells, features)

    def add_coverage_warning(self, cid: str, warning_id: str, detail: str) -> None:
        """Add an open vector-map coverage warning (idempotent on id).

        @param cid: ``tx,tz``
        @param warning_id: stable warning key
        @param detail: human detail
        """
        cells = self._load_cells()
        features = self._load_features()
        cell = self._ensure_cell(cells, cid)
        warnings = cell.setdefault('coverage_warnings', [])
        if any(w.get('id') == warning_id for w in warnings):
            self._save(cells, features)
            return
        warnings.append({'id': warning_id, 'detail': detail})
        self._save(cells, features)

    def dismiss_coverage_warning(self, cid: str, warning_id: str, *, reason: str) -> None:
        """Dismiss an open coverage warning with a reason.

        @param cid: ``tx,tz``
        @param warning_id: warning id
        @param reason: why it is safe to dismiss
        @raises RegistryError: if warning not open or reason empty
        """
        if not reason or not str(reason).strip():
            raise RegistryError('dismiss coverage warning requires a non-empty reason')
        cells = self._load_cells()
        features = self._load_features()
        cell = self._ensure_cell(cells, cid)
        warnings = cell.setdefault('coverage_warnings', [])
        match = next((w for w in warnings if w.get('id') == warning_id), None)
        if match is None:
            raise RegistryError(f'no open coverage warning {warning_id!r} on cell {cid}')
        warnings.remove(match)
        cell.setdefault('coverage_dismissals', []).append({
            'id': warning_id,
            'reason': reason,
            'detail': match.get('detail'),
        })
        self._save(cells, features)


def propose_feature_candidates(cid: str) -> list[dict[str, Any]]:
    """Propose vector-map building feature candidates for a cell (helper for register).

    Thin wrapper around ``cell_features`` when available; returns [] if vector-map
    data or the helper cannot run.

    @param cid: ``tx,tz``
    @returns: list of candidate dicts with id/name hints
    """
    try:
        import cell_features as cf  # noqa: WPS433
        tx, tz = parse_cell_id(cid)
        # cell_features.main prints; use its packaging helpers when present
        if hasattr(cf, 'propose_for_cell'):
            return list(cf.propose_for_cell(tx, tz))
    except Exception:
        return []
    return []


def _format_status(store: RegistryStore, cid: str | None) -> str:
    lines = []
    cur = store.current_cell()
    lines.append(f'current_cell: {cell_id(*cur) if cur else "(none)"}')
    if cid is None:
        cells = store._load_cells()
        lines.append(f'registered_cells: {len(cells)}')
        feats = store._load_features()
        lines.append(f'registered_features: {len(feats)}')
        return '\n'.join(lines)
    c = store.cell(cid)
    lines.append(f'cell {cid}:')
    lines.append(f'  ground={c["ground"]} trees={c["trees"]} decor={c["decor"]} '
                 f'marker={c["marker"]} hold={c["hold"]!r}')
    lines.append(f'  empty={store.is_empty(cid)} done={store.is_done(cid)}')
    left = store.what_is_left(cid)
    lines.append(f'  left: {", ".join(left) if left else "(nothing)"}')
    for f in store.features_in_cell(cid):
        lines.append(f'  feature {f["id"]}: {f.get("status")} ({f.get("name")})')
    for w in store.open_coverage_warnings(cid):
        lines.append(f'  coverage warning {w["id"]}: {w.get("detail")}')
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    """Management CLI for the cell registry.

    @param argv: optional argv override
    @returns: exit code
    """
    ap = make_parser(__doc__)
    ap.add_argument('--root', type=Path, default=None,
                    help='world root (default: active world)')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('status', help='print registry summary or one cell')
    p.add_argument('--cell', default=None, help='tx,tz')

    p = sub.add_parser('query', help='query a feature or what is left in a cell')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--feature', help='feature id (build slug)')
    g.add_argument('--cell', help='tx,tz')

    p = sub.add_parser('register-feature', help='register a feature explicitly')
    p.add_argument('id', help='build slug')
    p.add_argument('--name', required=True)
    p.add_argument('--cells', required=True,
                   help='comma-separated cell ids, or colon-separated list of tx,tz')
    p.add_argument('--status', default='planned', choices=sorted(FEATURE_STATUS))

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

    sub.add_parser('next', help='print the next work item (issues first, then registry)')
    sub.add_parser('issues', help='list open ISSUES.txt lines')

    p = sub.add_parser('add-issue', help='append a snagging issue and reopen the target')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--cell', help='tx,tz')
    g.add_argument('--feature', help='feature id')
    p.add_argument('--layer', choices=('ground', 'trees', 'decor'), default=None)
    p.add_argument('--reason', required=True)

    p = sub.add_parser('repair', help='backup + wipe + replay one cell from the registry (#32)')
    p.add_argument('cell', help='tx,tz')
    p.add_argument('--confirm-logged-out', action='store_true',
                   help='required unless --dry-run: confirm the Minecraft save is closed')
    p.add_argument('--dry-run', action='store_true', help='plan only (no region writes)')

    p = sub.add_parser('undo-repair', help='restore a repair snap (regions + registry slice)')
    p.add_argument('snap', help='repair snap directory name')
    p.add_argument('--confirm-logged-out', action='store_true', required=True)

    p = sub.add_parser('verify', help='registry integrity report (exit 1 on errors)')
    p.add_argument('--json', action='store_true', help='print JSON report')
    p.add_argument('--check-world', action='store_true',
                   help='also check region files for non-empty cells')

    args = ap.parse_args(argv)
    store = RegistryStore(args.root)

    try:
        if args.cmd == 'status':
            print(_format_status(store, args.cell))
            return 0

        if args.cmd == 'query':
            if args.feature:
                feat = store.feature(args.feature)
                if feat is None:
                    print(f'feature {args.feature}: not registered', file=sys.stderr)
                    return 1
                print(json.dumps({'id': args.feature, **feat}, indent=2, sort_keys=True))
                print(f'built: {feat.get("status") == "built"}')
                return 0
            print(_format_status(store, args.cell))
            return 0

        if args.cmd == 'register-feature':
            # --cells accepts "48,-44:48,-45" or "48,-44,48,-45" (ambiguous).
            # Prefer colon between cell ids; also accept space-separated via repeats.
            raw = args.cells
            if ':' in raw:
                cell_list = [c.strip() for c in raw.split(':') if c.strip()]
            else:
                # "48,-44,49,-44" → pairs
                nums = [p.strip() for p in raw.split(',')]
                if len(nums) % 2 != 0:
                    raise RegistryError(
                        '--cells must be tx,tz:tx,tz:... or an even list of ints'
                    )
                cell_list = [f'{nums[i]},{nums[i + 1]}' for i in range(0, len(nums), 2)]
            store.register_feature(
                args.id, name=args.name, cells=cell_list, status=args.status,
            )
            print(f'registered {args.id} cells={cell_list} status={args.status}')
            return 0

        if args.cmd == 'propose-features':
            for item in propose_feature_candidates(args.cell):
                print(json.dumps(item, sort_keys=True))
            return 0

        if args.cmd == 'hold':
            if args.hold_cmd == 'set':
                store.set_hold(args.cell, args.reason)
                print(f'hold set on {args.cell}: {args.reason}')
            else:
                store.set_hold(args.cell, None)
                print(f'hold cleared on {args.cell}')
            return 0

        if args.cmd == 'dismiss-coverage':
            store.dismiss_coverage_warning(args.cell, args.warning_id, reason=args.reason)
            print(f'dismissed {args.warning_id} on {args.cell}')
            return 0

        if args.cmd == 'next':
            from registry_next import format_next, next_task
            task = next_task(store, root=args.root or store.root)
            print(format_next(task))
            if task is not None:
                print(json.dumps(task, indent=2, sort_keys=True))
            return 0

        if args.cmd == 'issues':
            from registry_issues import load_issues
            for issue in load_issues(args.root or store.root):
                print(issue.raw)
            return 0

        if args.cmd == 'add-issue':
            from registry_issues import add_issue
            if args.feature:
                issue = add_issue(
                    reason=args.reason, feature_id=args.feature,
                    root=args.root or store.root,
                )
            else:
                tx, tz = parse_cell_id(args.cell)
                issue = add_issue(
                    reason=args.reason, cell=(tx, tz), layer=args.layer,
                    root=args.root or store.root,
                )
            print(f'added {issue.raw}')
            return 0

        if args.cmd == 'repair':
            import registry_repair as RR
            cell = parse_cell_id(args.cell)
            try:
                report = RR.run_repair(
                    cell,
                    confirm_logged_out=bool(args.confirm_logged_out),
                    dry_run=bool(args.dry_run),
                    world_root=args.root or store.root,
                )
            except Exception as exc:  # noqa: BLE001
                print(f'repair error: {exc}', file=sys.stderr)
                return 2
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0

        if args.cmd == 'undo-repair':
            import registry_repair as RR
            try:
                report = RR.undo_repair(
                    args.snap,
                    confirm_logged_out=bool(args.confirm_logged_out),
                    world_root=args.root or store.root,
                )
            except Exception as exc:  # noqa: BLE001
                print(f'undo-repair error: {exc}', file=sys.stderr)
                return 2
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0

        if args.cmd == 'verify':
            import registry_verify as RV
            report = RV.run_verify(
                root=args.root or store.root,
                check_world=bool(args.check_world),
            )
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True))
            else:
                print(RV.format_report(report))
            return RV.exit_code(report)
    except RegistryError as exc:
        print(f'registry error: {exc}', file=sys.stderr)
        return 2

    return 2


if __name__ == '__main__':
    raise SystemExit(main())
