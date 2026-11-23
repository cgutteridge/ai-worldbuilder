#!/usr/bin/env python3
"""Cell registry CLI for the active world, over the cell journal (ADR 004).

The only record is ``<world>/registry/journal.jsonl``, written by
``cell_journal.CellRegistry.change`` (``site_kit.apply`` for builds, ``Note`` for marker / hold /
coverage / review facts). Everything here reads :meth:`CellRegistry.view` and friends; there are
no setters. Open the active world's registry with :func:`open_registry`.

Snags and issues live in ``registry/issues.json`` / ``registry/issues-closed.json`` via ``scripts/issues.py`` (the ``snag`` skill);
they never block a cell and never steer ``next`` (ticket #121). The old ``add-issue`` /
``issues`` CLI and ``site/ISSUES.txt`` I-nnn lines are retired.

Per-cell view (folded, never stored)::

    ground: none | ghost | generated
    trees, decor: bool
    features: (id, planned | in-progress | built) for every feature on the cell plan
    marker: bool (single marker)   hold: reason | None   notes: coverage warnings, reviews
    done / left: ground generated + trees + decor + every planned feature built + no marker + no open
                 coverage warning + nothing in flight

  python3 scripts/cell_registry.py status [--cell tx,tz]
  python3 scripts/cell_registry.py above-ghost [--json]
  python3 scripts/cell_registry.py query --feature <id>
  python3 scripts/cell_registry.py query --cell tx,tz
  python3 scripts/cell_registry.py propose-features --cell tx,tz [--record]
  python3 scripts/cell_registry.py duplicate-plans [--json]
  python3 scripts/cell_registry.py hold set tx,tz --reason R
  python3 scripts/cell_registry.py hold clear tx,tz
  python3 scripts/cell_registry.py dismiss-coverage tx,tz --warning-id ID --reason R
  python3 scripts/cell_registry.py dismiss-clip-coverage tx,tz --reason R
  python3 scripts/cell_registry.py dismiss-feature tx,tz --feature ID --reason R
  python3 scripts/cell_registry.py note tx,tz --kind review --ref REF --body TEXT
  python3 scripts/cell_registry.py approve tx,tz --layers ghost,buildings,ground,...
  python3 scripts/cell_registry.py next
  python3 scripts/cell_registry.py verify
  python3 scripts/cell_registry.py reconcile [CELL ...] [--fix]

reconcile compares the journal with the blocks (dry run by default; ``--fix`` re-commits over MCP).
site_recover = resolve an interrupted apply (land or roll back); reconcile = detect and repair drift forward.

A feature enters the cell plan by a ``plan_feature`` note (``propose-features --record``) or by any
feature Build that touched the cell (including after undo). Undo clears the build only; dismiss
(``dismiss-feature`` / ``plan_feature-dismiss``) is a separate act with a reason (#128).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from script_env import ROOT, make_parser, add_world_arg, activate_world_from_args  # noqa: E402

import cellmath
import plan_refs

from block_store import BlockStoreError  # noqa: E402
from cell_journal import APPROVAL_LAYERS, CellJournalError, CellRegistry, CellView, Note  # noqa: E402


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
                  ring: int | None = None, build_policy='default') -> CellRegistry:
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
    reg = CellRegistry(registry_root(root), blocks if blocks is not None else _NoReads(), ghosts, ring=ring)
    if build_policy == 'default':
        from readiness_evidence import enforce_live
        reg.build_policy = lambda build, touched: enforce_live(reg, build, touched)
    else:
        reg.build_policy = build_policy
    return reg


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


def feature_cells(reg: CellRegistry) -> dict[str, dict[str, Any]]:
    """Every feature on a cell plan: ``{id: {'status', 'cells': ['tx,tz', ...]}}``.

    Status is ``planned``, ``in-progress`` or ``built`` (cell views; #128).

    @param reg: registry
    @returns: feature map
    """
    out: dict[str, dict[str, Any]] = {}
    for v in reg.view().values():
        for f in v.features:
            rec = out.setdefault(f.id, {'status': f.status, 'cells': []})
            # Prefer built / in-progress over planned when the same id spans cells.
            if f.status == 'built' or (f.status == 'in-progress' and rec['status'] == 'planned'):
                rec['status'] = f.status
            rec['cells'].append(cellmath.cell_id(v.cell))
    return out


def duplicate_plans(reg: CellRegistry) -> list[dict[str, Any]]:
    """Plan notes whose map refs another, differently named plan already covers (task 293).

    Read-only. Covers every feature plan in the journal, dismissed ones included (flagged), so
    the owner sees what is left to ``dismiss-feature``. A plan counts as dismissed only when
    no cell still has it on its plan.

    @param reg: registry
    @returns: rows from ``plan_refs.duplicate_plans`` (``plan``, ``refs``, ``covered_by``, ``source``,
        ``status``, ``cells``, ``dismissed``)
    """
    from plan_refs import duplicate_plans as find
    plans = {pid: {**rec, 'dismissed': False} for pid, rec in feature_cells(reg).items()}
    for cell in reg.view():
        for pid in reg.plan_dismissed_ids(cell):
            rec = plans.setdefault(pid, {'status': 'dismissed', 'cells': [], 'dismissed': True})
            if rec['dismissed']:
                rec['cells'].append(cellmath.cell_id(cell))
    return find(reg.root, plans)


def format_duplicate_plans(rows: list[dict[str, Any]]) -> str:
    """Plain words for :func:`duplicate_plans` rows.

    @param rows: duplicate rows
    @returns: report text
    """
    if not rows:
        return 'no duplicate plans: every map ref has one plan'
    live = [r for r in rows if not r['dismissed']]
    lines = [f'{len(rows)} duplicate plan note{"s" if len(rows) != 1 else ""} '
             f'({len(live)} not dismissed):']
    for r in rows:
        basis = 'refs on its spec' if r['source'] == 'spec' else 'ref read from its name (no spec)'
        state = 'dismissed' if r['dismissed'] else r['status']
        cells = ' '.join(r['cells'])
        lines.append(f"  {r['plan']} [{state}] in {cells}: covers {','.join(r['refs'])} like {r['covered_by']} ({basis})")
        if not r['dismissed']:
            for c in r['cells']:
                lines.append(f"    python3 scripts/cell_registry.py --world <world> dismiss-feature {c} "
                             f"--feature {r['plan']} --reason 'duplicate of {r['covered_by']}'")
    return '\n'.join(lines)


def cells_above_ghost(reg: CellRegistry) -> dict[str, Any]:
    """Every cell with anything above ghost, with per-kind counts (#135).

    Membership matches :meth:`CellRegistry.seeds`: generated ground, trees, decor,
    or a feature that is not merely planned. Ghost-only and planned-only cells are
    omitted. Counts are 0/1 for ground, trees and decor; features is the number of
    non-planned features on the cell plan. Totals sum those counts across cells.

    @param reg: registry
    @returns: ``{'cells': [{'cell', 'ground', 'trees', 'decor', 'features'}, ...],
               'totals': {'cells', 'ground', 'trees', 'decor', 'features'}}``
               with cells sorted by id
    """
    rows: list[dict[str, Any]] = []
    for cell in sorted(reg.seeds()):
        v = reg.cell(cell)
        features = sum(1 for f in v.features if f.status != 'planned')
        rows.append({
            'cell': cellmath.cell_id(cell),
            'ground': 1 if v.ground == 'generated' else 0,
            'trees': 1 if v.trees else 0,
            'decor': 1 if v.decor else 0,
            'features': features,
        })
    totals = {
        'cells': len(rows),
        'ground': sum(r['ground'] for r in rows),
        'trees': sum(r['trees'] for r in rows),
        'decor': sum(r['decor'] for r in rows),
        'features': sum(r['features'] for r in rows),
    }
    return {'cells': rows, 'totals': totals}


def format_cells_above_ghost(report: dict[str, Any]) -> str:
    """Human text for :func:`cells_above_ghost` (purge counting).

    @param report: return value of :func:`cells_above_ghost`
    @returns: multi-line summary then one line per cell
    """
    t = report['totals']
    lines = [
        f"cells: {t['cells']}",
        f"ground: {t['ground']}  trees: {t['trees']}  decor: {t['decor']}  "
        f"features: {t['features']}",
    ]
    for r in report['cells']:
        lines.append(
            f"{r['cell']}: ground={r['ground']} trees={r['trees']} "
            f"decor={r['decor']} features={r['features']}"
        )
    return '\n'.join(lines)


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

    The journal replacement of merging every ``*-cells.json`` by mtime (site_patrol,
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


def _ground_floating(reg, cell) -> list:
    """Floating ground columns in a cell with generated ground, each with whose it is and its clear (task 297).

    @returns: ``[{'column', 'surface_y', 'belongs', 'owner', 'clear'}]``
    """
    from ground_float_audit import audit, clear_command
    return [{'column': [r['x'], r['z']], 'surface_y': r['surface_y'], 'belongs': r['belongs'],
             'owner': r['owner'], 'clear': clear_command(reg, cell, r)}
            for r in audit(reg, [cell])[tuple(cell)]]


def _format_status(reg: CellRegistry, cid: str | None, *, policy=None) -> str:
    lines = []
    cur = reg.marker_cell()
    lines.append(f'current_cell: {cellmath.cell_id(cur) if cur else "(none)"}')
    if cid is None:
        views = reg.view()
        lines.append(f'registered_cells: {len(views)}')
        lines.append(f'registered_features: {len(feature_cells(reg))}')
        flying = [e for e in reg.entries() if e.status == 'begun']
        lines.append(f'in_flight: {len(flying)}')
        return '\n'.join(lines)
    v = reg.cell(cellmath.parse_cell(cid))
    lines.append(f'cell {cid}:')
    lines.append(f'  ground={v.ground} trees={v.trees} decor={v.decor} '
                 f'marker={"up" if v.marker else "none"} hold={v.hold!r}')
    if policy is None:
        from readiness_evidence import policy_for
        policy = policy_for(reg)
    readiness = policy.check(v.cell)
    lines.append(f"  empty={is_empty(v)} done={readiness['complete']} journal_done={v.done}")
    for blocker in readiness['blockers']:
        lines.append(f'  not complete: {blocker}')
    if readiness.get('candidates'):
        from registry_next import describe_uncovered
        lines.append('  not complete: ' + describe_uncovered(readiness['candidates']))
    for layer in ('trees', 'decor'):
        if getattr(v, layer) and not readiness.get(f'{layer}_current'):
            lines.append(f'  not complete: {layer} inputs stale or legacy unvalidated')
    lines.append(f'  left: {", ".join(v.left) if v.left else "(nothing)"}')
    for f in v.features:
        lines.append(f'  feature {f.id}: {f.status}')
    # #142 / #154: unbuilt footprint (incl. portals left inside a built outline)
    try:
        from site_status import cell_footprint, format_footprint
        fp = cell_footprint(cellmath.parse_cell(cid))
        if fp.get('built_area') or fp.get('unbuilt_area'):
            lines.append(f'  footprint {format_footprint(fp)}')
    except (OSError, ValueError, KeyError, RuntimeError, SystemExit, ImportError):
        pass
    for n in v.notes:
        label = 'coverage warning' if n.kind == 'coverage' else n.kind
        lines.append(f'  {label} {n.ref}: {n.body}')
    return '\n'.join(lines)


def candidate_plan_id(item: dict[str, Any]) -> str:
    """Stable feature plan id for a ``propose-features`` candidate (#128).

    Buildings and water bodies (#148) use the first vector-map ref (e.g. ``w93729745``
    or ``water_<x>_<z>``); one cell's clip of a water body over cell lines keeps the
    per-cell ``id`` it was proposed with (``w93729745_63_m45``, #321). Touching non-house groups (#328) use the lowest ref among
    the group's ways. Houses use ``houses_<street_slug>`` when a street is known,
    else ``houses_`` plus sorted refs. Build specs should reuse this id as ``name`` so
    the plan matches the Build.

    @param item: one dict from :func:`propose_feature_candidates`
    @returns: feature plan id for a ``plan_feature`` note ``ref``
    @raises RegistryError: when the candidate has no refs to key on
    """
    import re
    if item.get('kind') == 'railway':
        from railway_feature import feature_name
        return feature_name(item['cell'])
    refs = [str(r) for r in (item.get('refs') or []) if r]
    if not refs:
        raise RegistryError(f'candidate {item.get("label")!r} has no refs for a plan id')
    if item.get('kind') == 'houses':
        street = (item.get('street') or '').strip()
        if street:
            slug = re.sub(r'[^a-z0-9]+', '_', street.lower()).strip('_') or 'unnamed'
            return f'houses_{slug}'
        return 'houses_' + '_'.join(sorted(refs))
    if item.get('kind') == 'group':
        return min(refs)
    if item.get('kind') == 'water_body' and item.get('clip') and item.get('id'):
        return str(item['id'])
    return refs[0]


def feature_plan_ids(reg: CellRegistry) -> set[str]:
    """Every feature plan id currently on any cell (planned or built).

    @param reg: registry
    @returns: plan ids from every cell view
    """
    return {f.id for v in reg.view().values() for f in v.features}


def plan_ids_for_candidates(reg: CellRegistry, cell: tuple[int, int],
                            items: list[dict[str, Any]]) -> list[str]:
    """Plan id for each candidate, keeping distinct building groups of one street apart.

    ``candidate_plan_id`` keys a house group by street, so two groups on one street
    collide. New street groups always take ``houses_<street>_<lowest ref>`` (stable while
    the group's buildings are unchanged, so re-runs and dismissals hold). That way a plan
    prepare just wrote is never a specless bare id that later looks like unresolved legacy,
    and a second group on the same street can be recorded before the first has a build
    spec (#233). An already-planned bare street id whose hand-off spec lists every ref of
    the candidate still means that plan. A specless bare street id is not reused: the
    candidate gets a suffixed id so callers can flag the bare plan as ambiguous legacy
    without inventing a second claim on the same id.

    @param reg: registry
    @param cell: (tx, tz)
    @param items: candidates from :func:`propose_feature_candidates`
    @returns: plan ids, parallel to ``items``
    """
    bases = [candidate_plan_id(i) for i in items]
    streets = [i.get('kind') == 'houses' and bool((i.get('street') or '').strip()) for i in items]
    known = feature_plan_ids(reg)
    dismissed = reg.plan_dismissed_ids(cell)
    out: list[str] = []
    for item, base, is_street in zip(items, bases, streets):
        if not is_street or base in dismissed:
            out.append(base)
            continue
        refs = {plan_refs.norm_ref(r) for r in item['refs'] if r}
        if base in known:
            covered = plan_refs.spec_refs(reg.root, base)
            if covered is not None and refs <= covered:
                out.append(base)
                continue
        out.append(f'{base}_{min(refs)}')
    return out


def _unresolved_legacy_house_pids(reg: CellRegistry, item: dict[str, Any], fid: str,
                                  known: set[str]) -> list[str]:
    """Specless house plan ids that make this candidate an unresolved legacy match (#233).

    Warns only for a real collision with this candidate: a specless bare street id this
    candidate was not assigned (prepare did not just create it under ``fid``), or a specless
    ``houses_<street>_<ref>`` whose trailing name ref sits in the candidate's refs and is not
    merely this candidate's own assigned (already prepared) id. An unrelated prepared group on
    the same street is not legacy ambiguity.

    @param reg: registry
    @param item: ``kind: houses`` candidate
    @param fid: plan id :func:`plan_ids_for_candidates` assigns this item
    @param known: every feature plan id on the registry
    @returns: colliding specless plan ids (may be empty)
    """
    from plan_refs import NAME_REF
    base = candidate_plan_id(item)
    refs = {plan_refs.norm_ref(r) for r in (item.get('refs') or ()) if r}
    out: list[str] = []
    # Same exemption as the suffixed loop: the candidate's own assigned id is not legacy.
    if base in known and base != fid and plan_refs.spec_refs(reg.root, base) is None:
        out.append(base)
    for pid in known:
        if pid == fid or not pid.startswith(f'{base}_'):
            continue
        if plan_refs.spec_refs(reg.root, pid) is not None:
            continue
        m = NAME_REF.search(pid)
        if m and m.group(1) in refs:
            out.append(pid)
    return out


def ambiguous_legacy_house_plans(reg: CellRegistry, cell: tuple[int, int],
                                 items: list[dict[str, Any]]) -> list[str]:
    """Warnings for house candidates that collide with a street plan that has no readable refs.

    A bare ``houses_<street>`` plan, or a specless ``houses_<street>_<ref>`` whose name ref
    lands in the candidate group, cannot be matched to buildings safely. Callers must not
    record another plan for those candidates and must not treat them as silently covered
    (#233). A plan this tool just prepared under the candidate's assigned id is not legacy.

    @param reg: registry
    @param cell: (tx, tz) — reserved for symmetry with other plan helpers; ids are world-wide
    @param items: house (and other) candidates after coverage filtering
    @returns: warning strings, one per ambiguous candidate/plan pair
    """
    known = feature_plan_ids(reg)
    warnings: list[str] = []
    for item, fid in zip(items, plan_ids_for_candidates(reg, cell, items)):
        if item.get('kind') != 'houses':
            continue
        for pid in sorted(set(_unresolved_legacy_house_pids(reg, item, fid, known))):
            warnings.append(
                f'ambiguous legacy plan {pid}: no readable handoff spec; audit refs {item.get("refs")}')
    return warnings


def drop_unresolved_legacy_houses(reg: CellRegistry, cell: tuple[int, int],
                                  items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Candidates minus house groups blocked by an unresolved specless legacy plan (#233).

    @param reg: registry
    @param cell: (tx, tz)
    @param items: candidates after coverage filtering
    @returns: items that may still be recorded or proposed
    """
    known = feature_plan_ids(reg)
    out: list[dict[str, Any]] = []
    for item, fid in zip(items, plan_ids_for_candidates(reg, cell, items)):
        if (item.get('kind') == 'houses'
                and _unresolved_legacy_house_pids(reg, item, fid, known)):
            continue
        out.append(item)
    return out


def propose_feature_candidates(cid: str) -> list[dict[str, Any]]:
    """Propose vector-map building and water-body feature candidates for a cell.

    Calls ``cell_features.missing_features`` for the cell. Errors propagate
    (no silent empty list). Landmark footprints already carry ``landmark`` /
    ``landmark_class`` from ``footprint_progress`` (#335).

    @param cid: ``tx,tz``
    @returns: list of candidate dicts from ``missing_features`` (kind/label/refs/…)
    """
    import cell_features as cf  # noqa: WPS433
    tx, tz = cellmath.parse_cell(cid)
    from railway_feature import proposal
    return list(cf.missing_features(tx, tz)) + proposal((tx, tz), view_registry())


def candidate_home(item: dict[str, Any]) -> tuple[int, int] | None:
    """Home cell recorded on a propose candidate.

    @param item: candidate dict (``home`` is ``[tx, tz]`` when the proposer set it)
    @returns: ``(tx, tz)``, or None when the candidate does not name a home
    """
    home = item.get('home')
    if not home or isinstance(home, str):
        return None
    return (int(home[0]), int(home[1]))


def covers_candidate(reg: CellRegistry, cell: tuple[int, int], item: dict[str, Any]) -> set[str]:
    """Plan ids and map refs that already account for ``item`` in ``cell``.

    When the candidate names this cell as its home, only a plan or dismissal in
    this cell counts. A plan or dismissal in another cell is the wrong cell and
    does not hide the feature (task 433). A candidate with no home, or whose home
    is another cell, stays covered by plans and dismissals elsewhere, so a feature
    planned in its real home is not proposed again next door (task 288).

    @param reg: registry
    @param cell: ``(tx, tz)`` being proposed
    @param item: candidate dict
    @returns: ids and refs
    """
    from cell_readiness import planned_elsewhere, planned_here
    cell = tuple(cell)
    here = planned_here(reg, cell)
    if candidate_home(item) == cell:
        return here
    return here | planned_elsewhere(reg, cell)


def candidate_recorded(reg: CellRegistry, cell: tuple[int, int], item: dict[str, Any],
                       fid: str) -> bool:
    """Whether ``fid`` must not be written again for ``item`` in ``cell``.

    The home cell may record a feature that is planned or dismissed only in
    another cell. A different cell still refuses an id planned anywhere, or
    dismissed in the cell being recorded. A candidate whose home is another
    cell is not recorded here.

    @param reg: registry
    @param cell: ``(tx, tz)``
    @param item: candidate dict
    @param fid: plan id
    @returns: True when record should skip it
    """
    cell = tuple(cell)
    home = candidate_home(item)
    if home is not None and home != cell:
        return True
    on_cell = (any(f.id == fid for f in reg.cell(cell).features)
               or fid in reg.plan_dismissed_ids(cell))
    if home == cell:
        return on_cell
    return on_cell or fid in feature_plan_ids(reg)


def drop_covered_candidates(reg: CellRegistry, cell: tuple[int, int],
                            items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Candidates minus map-ref features already accounted for (tasks 288, 293, 233, 433).

    A feature crossing a cell line is planned once, in the cell with most of its
    columns. That home cell still proposes it when the only plan or dismissal is
    in another cell. Other cells do not re-propose a feature planned or dismissed
    in its home. A plan covers a candidate by map ref, not only by id:
    ``west_w34391673`` covers ``w34391673`` (``plan_refs``). A ``kind: group`` is
    dropped only when every ref is covered; a partial hit keeps the uncovered refs
    as building candidates (#328). House groups match the same way (#233): a
    spec-backed plan under a different id covers its ways; a partial hit keeps the
    uncovered houses.

    @param reg: registry
    @param cell: (tx, tz)
    @param items: candidates from :func:`propose_feature_candidates` or readiness evidence
    @returns: the candidates still to account for in this cell
    """
    from plan_refs import uncovered_candidates
    out: list[dict[str, Any]] = []
    for item in items:
        covered = covers_candidate(reg, cell, item)
        # A house plan id already on the registry accounts for that whole candidate when
        # there is no hand-off ways list yet (prepare records the note before a build spec
        # exists). House plans never recover coverage from a name suffix, so this keeps a
        # just-prepared group from being re-proposed on the next run (#233). A readable
        # spec still drives partial splits.
        if item.get('kind') == 'houses':
            fid = plan_ids_for_candidates(reg, cell, [item])[0]
            if fid in covered and plan_refs.spec_refs(reg.root, fid) is None:
                continue
        out.extend(uncovered_candidates(item, covered))
    return drop_unresolved_legacy_houses(reg, cell, out)


def _release_stray_plan(reg: CellRegistry, home: tuple[int, int], fid: str) -> None:
    """Dismiss ``fid`` on every cell other than its home.

    The home-cell plan is the one that counts (task 433). An active plan left on
    another cell would still look like a second claim.

    @param reg: registry
    @param home: ``(tx, tz)`` that now holds the plan
    @param fid: plan id
    """
    home = tuple(home)
    for other in list(reg.view()):
        if tuple(other) == home:
            continue
        if any(f.id == fid for f in reg.cell(other).features):
            note(reg, other, 'plan_feature-dismiss',
                 'recorded in the home cell (most columns)', fid)


def record_feature_candidates(reg: CellRegistry, cid: str,
                              candidates: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Write ``plan_feature`` notes for propose-features candidates (#128).

    Skips ids already on this cell's plan, and ids dismissed in this cell, so a
    re-run of ``--record`` does not undo a deliberate dismiss here (a fresh
    ``plan_feature`` note would clear the dismissal in the note fold). A feature
    whose home is this cell is still recorded when the only plan or dismissal is
    in another cell, and that stray plan is dismissed (task 433). A candidate
    with no home is skipped when its id is planned in any cell (tasks 233, 288).
    House groups blocked by an unresolved specless legacy plan are skipped (#233).
    Map-ref coverage is applied first by :func:`drop_covered_candidates` (task 293).

    @param reg: registry
    @param cid: ``tx,tz``
    @param candidates: optional precomputed list; default loads via :func:`propose_feature_candidates`
    @returns: list of ``{id, label, kind}`` recorded (skipped when already planned or dismissed)
    """
    cell = cellmath.parse_cell(cid)
    items = candidates if candidates is not None else propose_feature_candidates(cid)
    items = drop_covered_candidates(reg, cell, items)
    recorded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item, fid in zip(items, plan_ids_for_candidates(reg, cell, items)):
        if fid in seen or candidate_recorded(reg, cell, item, fid):
            continue
        note(reg, cell, 'plan_feature', str(item.get('label') or ''), fid)
        if candidate_home(item) == cell:
            _release_stray_plan(reg, cell, fid)
        seen.add(fid)
        recorded.append({'id': fid, 'label': item.get('label'), 'kind': item.get('kind')})
    return recorded


def note(reg: CellRegistry, cell: tuple[int, int], kind: str, body: str = '', ref: str = '') -> None:
    """Journal one Note (marker / hold / coverage / plan_feature / review / issue facts).

    @param reg: registry
    @param cell: (tx, tz)
    @param kind: note kind (cell_journal.NOTE_KINDS)
    @param body: text
    @param ref: reference id
    """
    reg.change(Note(tuple(cell), kind, body, ref))


DRIFT_ORDER = ('in-flight', 'missing', 'foreign', 'mismatched', 'ghost-missing', 'ghost-stale')
DRIFT_NOTES = {
    'in-flight': 'an apply began and never finished (site_recover.py can also roll it back)',
    'missing': 'journal owns the block, the world has air / nothing',
    'foreign': 'journal owns the block, the world holds another block',
    'mismatched': 'an apply finished mismatched and still blocks preflight for its cell',
    'ghost-missing': 'frontier cell lacks ghost ground / features',
    'ghost-stale': 'ghost features still inside a real feature footprint',
}
RECONCILE_HELP = ('compare the journal with the blocks; dry run unless --fix. '
                  'site_recover = resolve an interrupted apply (land or roll back); '
                  'reconcile = detect and repair drift forward')


def mismatch_next_step(item, fix: bool, registry) -> str:
    """The sanctioned next command for a mismatched entry that reconcile did not clear.

    @param item: a ``mismatched`` DriftItem
    @param fix: the run was a ``--fix`` run
    @param registry: the journal that produced the drift
    @returns: one line naming the command
    """
    plan = item.plans[0] if item.plans else '?'
    cells = ' '.join(cellmath.cell_id(c) for c in item.cells)
    if not fix:
        return (f'clear with: reconcile {cellmath.cell_id(item.cell)} --fix (the entry also touches cell(s) {cells}; '
                'name them all if positions differ there)')
    from cell_readiness import reapply_command
    reapply = reapply_command(plan, item.layer, item.owner or item.cell, registry=registry)
    return (f'still mismatched after repair ({item.count} position(s) differ now; cells {cells}). Next: '
            f'reconcile the other cell(s) above if they drifted, or redo the plan past its own mismatch: {reapply}')


def format_drift(drift, *, registry, fix: bool = False) -> str:
    """Human report of a :class:`cell_journal.Drift`, grouped by kind.

    @param drift: result of ``CellRegistry.reconcile``
    @param fix: the run was a ``--fix`` run (marks each cell fixed / still drifting)
    @param registry: the journal that produced the drift
    @returns: report text
    """
    if not drift.items and not drift.upgraded:
        return 'reconcile: clean (the journal matches the blocks)'
    cleared = [f'{plan} (seq {seq})' for plan, seq in drift.cleared]
    lines = []
    for kind in DRIFT_ORDER:
        items = drift.by_kind(kind)
        if not items:
            continue
        lines.append(f'{kind}: {sum(i.count for i in items)} in {len(items)} cell(s), {DRIFT_NOTES[kind]}')
        for i in sorted(items, key=lambda i: (i.cell, i.seq)):
            tag = (' [fixed]' if i.fixed else ' [NOT fixed]') if fix else ''
            what = f'{i.count} position(s)' if i.count or i.kind != 'in-flight' else 'every planned block is present'
            lines.append(f'  {cellmath.cell_id(i.cell)}  {what}  plans {", ".join(i.plans) or "-"}'
                         f'{f"  seq {i.seq}" if i.seq else ""}{tag}')
            for pos, (want, got) in list(i.sample.items())[:3]:
                lines.append(f'      {pos}: want {want}, got {got}')
            if i.kind == 'mismatched':
                lines.append('      ' + mismatch_next_step(i, fix, registry))
    left = [i for i in drift.items if not i.fixed]
    if drift.upgraded:
        lines.append(f'upgraded {drift.upgraded} mismatched entr{"y" if drift.upgraded == 1 else "ies"} to landed: '
                     + ', '.join(cleared) + ' (preflight no longer blocks on it)')
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
    scope = [cellmath.parse_cell(c) for c in cells] or None
    if fix:
        reg, writer = site_kit.live_session(root=root)
        drift = reg.reconcile(scope, fix=True, apply=writer)
    else:
        reg = open_registry(root, blocks=site_kit.disk_blocks())
        drift = reg.reconcile(scope)
    print(format_drift(drift, registry=reg, fix=fix))
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

    for command in ('check', 'prepare'):
        p = sub.add_parser(command, help='read-only named-cell readiness' if command == 'check'
                           else 'report local preparation; --apply repairs under the world lock')
        p.add_argument('--cell', required=True, help='tx,tz; independent of queue order')
        p.add_argument('--json', action='store_true')
        if command == 'check':
            p.add_argument('--operation', choices=('build', 'ground', 'feature', 'trees', 'decor'), default='build')
        else:
            p.add_argument('--apply', action='store_true')
            p.add_argument('--owner', default=None, help='lock owner (default: prepare-<pid>)')
            p.add_argument('--evidence-only', action='store_true',
                           help='cross-cell neighbour: check evidence only; records no feature plans (task 292)')

    p = sub.add_parser('above-ghost',
                       help='list cells with anything above ghost and count each kind (#135)')
    p.add_argument('--json', action='store_true', help='print JSON report')

    p = sub.add_parser('query', help='query a feature or what is left in a cell')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--feature', help='feature id (build slug)')
    g.add_argument('--cell', help='tx,tz')

    p = sub.add_parser('propose-features', help='list vector-map feature candidates for a cell')
    p.add_argument('--cell', required=True)
    p.add_argument('--record', action='store_true',
                   help='journal plan_feature notes for each candidate (#128)')

    p = sub.add_parser('duplicate-plans',
                       help='read-only: plan notes whose map ref another plan already covers (task 293)')
    p.add_argument('--json', action='store_true', help='print JSON rows')

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

    p = sub.add_parser('dismiss-clip-coverage', help='accept incomplete vector-map query coverage')
    p.add_argument('cell')
    p.add_argument('--reason', required=True)

    p = sub.add_parser('dismiss-feature', help='dismiss a planned feature from the cell plan (#128)')
    p.add_argument('cell')
    p.add_argument('--feature', required=True, help='feature plan id')
    p.add_argument('--reason', required=True)

    p = sub.add_parser('note', help='journal a review note (e.g. trees over roof resolved)')
    p.add_argument('cell')
    p.add_argument('--kind', choices=('review',), default='review')
    p.add_argument('--ref', required=True, help='note id, e.g. trees-over-roof:x0,z0,x1,z1')
    p.add_argument('--body', default='', help='text (the reason)')

    p = sub.add_parser('approve', help='record which layers the owner approved on a cell (#327)')
    p.add_argument('cell')
    p.add_argument('--layers', required=True, metavar='L,L',
                   help='comma-separated, from ' + ', '.join(APPROVAL_LAYERS) + '; replaces the earlier approval')

    p = sub.add_parser('next', help='print the next undone registry work item')
    p.add_argument('--exclude-cell', action='append', default=[], metavar='TX,TZ',
                   help='run-local exclusion; repeat for each blocked cell')
    p.add_argument('--cell', metavar='TX,TZ',
                   help='only this cell: what next would do there (read-only)')

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
        except (CellJournalError, OSError, ValueError, LookupError, RuntimeError, ImportError, SystemExit) as exc:
            print(f'registry error: {exc}', file=sys.stderr)
            return 2
    root = registry_root(args.root)
    reg = view_registry(root)

    try:
        if args.cmd in ('check', 'prepare'):
            from cell_readiness import format_readiness
            from readiness_evidence import policy_for
            cell = cellmath.parse_cell(args.cell)
            if args.cmd == 'prepare' and args.apply and not os.environ.get('SITE_OWNER'):
                import site_lock
                owner = args.owner or f'prepare-{os.getpid()}'
                command = [sys.executable, str(Path(__file__).resolve()), '--world', args.world]
                if args.root:
                    command += ['--root', str(args.root)]
                command += ['prepare', '--cell', args.cell, '--apply']
                if args.evidence_only:
                    command.append('--evidence-only')
                if args.json:
                    command.append('--json')
                return site_lock.run(owner, f'prepare cell {args.cell}', command)
            policy = policy_for(reg)
            if args.cmd == 'check':
                report = policy.check(cell, args.operation)
                if report.get('ground') == 'generated':
                    report['ground_floating'] = _ground_floating(reg, cell)
            else:
                from cell_preparation import prepare_cell
                report = prepare_cell(reg, policy.evidence, cell, apply=args.apply,
                                      evidence_only=args.evidence_only)
            print(json.dumps(report, indent=2, sort_keys=True) if args.json else format_readiness(report))
            return 0 if report['ready'] else 1

        if args.cmd == 'status':
            print(_format_status(reg, args.cell))
            return 0

        if args.cmd == 'above-ghost':
            report = cells_above_ghost(reg)
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True))
            else:
                print(format_cells_above_ghost(report))
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
            from cell_features import clip_coverage
            fraction = clip_coverage(cellmath.parse_cell(args.cell))
            print(json.dumps({'cell': args.cell, 'clip_coverage': fraction,
                              'kind': 'clip-query-coverage'}, sort_keys=True))
            items = drop_covered_candidates(reg, cellmath.parse_cell(args.cell),
                                            propose_feature_candidates(args.cell))
            for item in items:
                print(json.dumps(item, sort_keys=True, default=_json_default))
            if args.record:
                recorded = record_feature_candidates(reg, args.cell, items)
                for rec in recorded:
                    print(f"recorded plan_feature {rec['id']}: {rec.get('label')}")
                if not recorded and items:
                    print('recorded: (all candidates already on the cell plan)')
                elif not items:
                    print('recorded: (no candidates)')
            return 0

        if args.cmd == 'duplicate-plans':
            rows = duplicate_plans(reg)
            print(json.dumps(rows, indent=2, sort_keys=True) if args.json else format_duplicate_plans(rows))
            return 0

        if args.cmd == 'hold':
            cell = cellmath.parse_cell(args.cell)
            if args.hold_cmd == 'set':
                note(reg, cell, 'hold', args.reason)
                print(f'hold set on {args.cell}: {args.reason}')
            else:
                note(reg, cell, 'hold', '')
                print(f'hold cleared on {args.cell}')
            return 0

        if args.cmd == 'dismiss-coverage':
            note(reg, cellmath.parse_cell(args.cell), 'coverage-dismiss', args.reason, args.warning_id)
            print(f'dismissed {args.warning_id} on {args.cell}')
            return 0

        if args.cmd == 'dismiss-clip-coverage':
            from cell_features import clip_coverage
            cell = cellmath.parse_cell(args.cell)
            if clip_coverage(cell) >= 1:
                raise RegistryError(f'no incomplete clip query coverage on {args.cell}')
            from readiness_evidence import policy_for
            facts = policy_for(reg).evidence.facts(cell)
            note(reg, cell, 'clip_coverage-dismiss', args.reason,
                 facts.get('map_revision', facts['revision']))
            print(f'dismissed clip query coverage on {args.cell}: {args.reason}')
            return 0

        if args.cmd == 'dismiss-feature':
            note(reg, cellmath.parse_cell(args.cell), 'plan_feature-dismiss',
                 args.reason, args.feature)
            print(f'dismissed feature {args.feature} on {args.cell}: {args.reason}')
            return 0

        if args.cmd == 'note':
            note(reg, cellmath.parse_cell(args.cell), args.kind, args.body, args.ref)
            print(f'{args.kind} {args.ref} on {args.cell}')
            return 0

        if args.cmd == 'approve':
            note(reg, cellmath.parse_cell(args.cell), 'approval', args.layers.replace(' ', ''))
            print(f'approval on {args.cell}: {sorted(reg.approved_layers(cellmath.parse_cell(args.cell)))}')
            return 0

        if args.cmd == 'next':
            from registry_next import format_next, next_task
            task = next_task(reg, root=root, exclude={cellmath.parse_cell(c) for c in args.exclude_cell},
                             cell=cellmath.parse_cell(args.cell) if args.cell else None)
            print(format_next(task))
            if task is not None:
                print(json.dumps(task, indent=2, sort_keys=True))
            return 0

        if args.cmd == 'verify':
            import registry_verify as RV
            report = RV.run_verify(root=root, check_world=bool(args.check_world))
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True))
            else:
                print(RV.format_report(report))
            return RV.exit_code(report)
    except (CellJournalError, OSError, ValueError, LookupError, RuntimeError, ImportError, SystemExit) as exc:
        print(f'registry error: {exc}', file=sys.stderr)
        return 2

    return 2


if __name__ == '__main__':
    raise SystemExit(main())
