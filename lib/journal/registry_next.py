"""Deterministic ``next`` work picker for the cell registry (ticket #31).

Read-only and idempotent: the next undone registry item (ISSUES.txt / I-nnn no
longer steers this; snags live in ``registry/issues.json`` / ``registry/issues-closed.json`` via ``scripts/issues.py``
and never block a cell — ticket #121).

Cell choice: marker cell if not DONE, else nearest undone non-held cell to the
configured home cell (Chebyshev distance, then clockwise ring order from north), over the
cell journal views (``CellRegistry.view()``).
Unready cells return needs-prep before any build. Prepared-cell order is ground →
features (by id) → trees → decor → marker-clear. Callers explicitly exclude blocked cells.

The build task raises the marker; ``next`` does not.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import cellmath
from cell_journal import CellRegistry, CellView
from cell_registry import cell_id, view_registry


def _undone_work_in_cell(v: CellView) -> list[dict[str, Any]]:
    """Ordered remaining work items inside one cell (excluding holds).

    @param v: the cell's journal view
    @returns: list of task dicts
    """
    if v.hold:
        return []
    cid = cell_id(*v.cell)
    out: list[dict[str, Any]] = []
    if v.ground != 'generated':
        out.append({'kind': 'layer', 'cell': cid, 'layer': 'ground'})
        return out  # ground first; later layers wait
    for feat in v.features:
        if feat.status != 'built':
            out.append({
                'kind': 'feature',
                'cell': cid,
                'feature': feat.id,
                'status': feat.status,
                'name': feat.id,
            })
    if not v.trees:
        out.append({'kind': 'layer', 'cell': cid, 'layer': 'trees'})
    if not v.decor:
        out.append({'kind': 'layer', 'cell': cid, 'layer': 'decor'})
    return out


def unplanned_footprint_candidates(reg: CellRegistry, cid: str) -> list[dict[str, Any]]:
    """Vector-map buildings still missing a ``plan_feature`` / feature build (#140).

    Uses ``propose_feature_candidates`` (same as ``propose-features``), then drops
    ids already on the cell plan or deliberately dismissed.

    @param reg: cell journal
    @param cid: ``tx,tz``
    @returns: candidate dicts each with an ``id`` plan key
    @raises RuntimeError: when propose-features fails for a reason other than no world
    """
    import cell_registry as CR  # noqa: WPS433
    cell = CR.parse_cell_id(cid)
    items = CR.propose_feature_candidates(cid)
    skip = {f.id for f in reg.cell(cell).features} | reg.plan_dismissed_ids(cell)
    out: list[dict[str, Any]] = []
    for item, fid in zip(items, CR.plan_ids_for_candidates(reg, cell, items)):
        if fid in skip:
            continue
        out.append({'id': fid, **item})
    return out


def describe_uncovered(candidates: list[dict[str, Any]]) -> str:
    """Plain words for footprints no planned or built feature covers ('' when none).

    @param candidates: from :func:`unplanned_footprint_candidates`
    @returns: e.g. ``41 building footprints in 4 groups not covered by any planned or built feature (...)``
    """
    if not candidates:
        return ''
    refs = sorted({str(r) for c in candidates for r in c.get('refs') or ()})
    shown = ', '.join(refs[:8]) + (f', ... {len(refs) - 8} more' if len(refs) > 8 else '')
    return (f'{len(refs)} building footprint{"s" if len(refs) != 1 else ""} in '
            f'{len(candidates)} group{"s" if len(candidates) != 1 else ""} not covered by any '
            f'planned or built feature ({shown})')


def needs_clip_coverage(reg: CellRegistry, cell: tuple[int, int]) -> dict[str, Any] | None:
    """Undismissed incomplete query coverage, shared by next and verify.

    @param reg: cell journal
    @param cell: (tx, tz)
    @returns: needs-clip-coverage task, or None when complete or dismissed
    """
    from cell_features import clip_coverage
    if reg.clip_coverage_dismissed(cell):
        return None
    fraction = clip_coverage(cell)
    if fraction >= 1:
        return None
    return {'kind': 'needs-clip-coverage', 'cell': cell_id(*cell), 'clip_coverage': fraction}


def next_task(registry: CellRegistry | None = None, *, root: Path | None = None,
              policy=None, exclude=()) -> dict[str, Any] | None:
    """Read-only deterministic selection over validated readiness and journal work.

    ``needs-prep`` names an unready selected cell. Unattended callers log its reasons
    and pass explicit run-local exclusions on their next call. ``blocked`` means
    unfinished cells remain but all are held/excluded; None means all complete/empty.
    No candidate generation, extraction, ghost application or marker movement occurs.
    """
    reg = registry or view_registry(root)
    views = reg.view()
    if not views:
        return None
    if policy is None:
        from readiness_evidence import policy_for
        policy = policy_for(reg)
    excluded = set(exclude)
    reports = {c: policy.check(c, inspect_candidates=False) for c in views}
    pending = {c: v for c, v in views.items() if not reports[c]['complete']}
    eligible = [c for c, v in pending.items() if not v.hold and c not in excluded]
    if not eligible:
        if pending:
            return {'kind': 'blocked', 'cells': [cell_id(*c) for c in sorted(pending)],
                    'reasons': {cell_id(*c): reports[c]['blockers'] or ['excluded for this run']
                                for c in sorted(pending)}}
        return None
    home, marker = cellmath.home_cell(), reg.marker_cell()

    def order(c):
        distance = cellmath.ring_distance(c, home)
        ring = cellmath.ring_cells(distance, home)
        return (c != marker, distance, ring.index(c) if c in ring else 0, c)

    cell = min(eligible, key=order)
    report, v = reports[cell], views[cell]
    if not report['ready']:
        return {'kind': 'needs-prep', 'cell': cell_id(*cell), 'reasons': report['blockers'],
                'clip_coverage': report.get('clip_coverage'),
                'aerial_coverage': report.get('aerial_coverage'),
                'next_actions': report.get('next_actions', [])}
    effective = replace(v, trees=report['trees_current'], decor=report['decor_current'])
    work = _undone_work_in_cell(effective)
    if work:
        return work[0]
    if v.marker:
        return {'kind': 'marker-clear', 'cell': cell_id(*cell)}
    # An unfinished journal invariant must never be reported as an idle queue.
    return {'kind': 'needs-prep', 'cell': cell_id(*cell), 'reasons': list(v.left)}


def format_next(task: dict[str, Any] | None) -> str:
    """Human-readable one-liner for a next task.

    @param task: from :func:`next_task`
    @returns: text
    """
    if task is None:
        return 'next: (nothing — registry idle)'
    if task.get('kind') == 'feature':
        return (
            f"next: feature {task['feature']} in cell {task['cell']} "
            f"(status={task.get('status')})"
        )
    if task.get('kind') == 'needs-prep':
        return f"next: needs-prep in cell {task['cell']}: " + '; '.join(task['reasons'])
    if task.get('kind') == 'blocked':
        return 'next: blocked (unfinished cells are held or excluded for this run)'
    if task.get('kind') == 'marker-clear':
        return f"next: marker-clear in cell {task['cell']}"
    return f"next: cell {task['cell']} {task['layer']}"
