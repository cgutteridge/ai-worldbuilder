"""Deterministic ``next`` work picker for the cell registry (ticket #31).

Read-only and idempotent: the next undone registry item (ISSUES.txt / I-nnn no
longer steers this; snags live in ``registry/issues.json`` via ``scripts/issues.py``
and never block a cell — ticket #121).

Cell choice: marker cell if not DONE, else nearest undone non-held cell to the
configured origin (Chebyshev distance, then clockwise ring order from north), over the
cell journal views (``CellRegistry.view()``).
Order inside a cell: ground → features (by id) → needs-feature-plan when the
vector map still has unplanned footprints (#140) → trees → decor.

The build task raises the marker; ``next`` does not.
"""
from __future__ import annotations

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
    try:
        items = CR.propose_feature_candidates(cid)
    except RuntimeError as exc:
        # Unit fixtures often have no ``--world``; without a world there is no map to check.
        if 'no world selected' not in str(exc):
            raise
        return []
    skip = {f.id for f in reg.cell(cell).features} | reg.plan_dismissed_ids(cell)
    out: list[dict[str, Any]] = []
    for item in items:
        fid = CR.candidate_plan_id(item)
        if fid in skip:
            continue
        out.append({'id': fid, **item})
    return out


def _needs_feature_plan_task(reg: CellRegistry, cid: str) -> dict[str, Any] | None:
    """If unplanned footprints remain, a ``needs-feature-plan`` item; else None.

    @param reg: cell journal
    @param cid: ``tx,tz``
    @returns: task dict or None
    """
    candidates = unplanned_footprint_candidates(reg, cid)
    if not candidates:
        return None
    return {
        'kind': 'needs-feature-plan',
        'cell': cid,
        'count': len(candidates),
        'features': [c['id'] for c in candidates],
    }


def _pick_cell(views: dict[tuple[int, int], CellView], marker: tuple[int, int] | None) -> CellView | None:
    """Choose the cell to work next (marker if not done, else nearest undone).

    @param views: every journal cell view
    @param marker: the marker cell, or None
    @returns: cell view or None when everything is done
    """
    origin = cellmath.origin_cell()
    if marker is not None:
        v = views.get(marker)
        if v is not None and not v.done and not v.hold and _undone_work_in_cell(v):
            return v

    # Nearest undone non-held: distance then clockwise ring from north.
    best: tuple[int, int, tuple[int, int]] | None = None
    for cell, v in views.items():
        if v.done or v.hold or not _undone_work_in_cell(v):
            continue
        dist = cellmath.ring_distance(cell, origin)
        ring = cellmath.ring_cells(dist, origin)
        try:
            idx = ring.index(cell)
        except ValueError:
            idx = 0
        key = (dist, idx, cell)
        if best is None or key < best:
            best = key
    return views[best[2]] if best else None


def next_task(registry: CellRegistry | None = None, *, root: Path | None = None) -> dict[str, Any] | None:
    """Return the next work item, or None when the journal is idle.

    After ground, if the cell would move on to trees (or later) while vector-map
    footprints still lack a ``plan_feature``, returns ``needs-feature-plan``
    instead (#140). Run ``propose-features --cell … --record`` then call ``next``
    again.

    @param registry: optional CellRegistry
    @param root: world root when opening one
    @returns: task dict or None
    """
    reg = registry or view_registry(root)
    v = _pick_cell(reg.view(), reg.marker_cell())
    if v is None:
        return None
    work = _undone_work_in_cell(v)
    if not work:
        return None
    task = work[0]
    # Ground skipped building footprints; do not step to trees/decor until they are planned.
    if (task.get('kind') == 'layer'
            and task.get('layer') in ('trees', 'decor')
            and not any(f.status != 'built' for f in v.features)):
        blocked = _needs_feature_plan_task(reg, task['cell'])
        if blocked is not None:
            return blocked
    return task


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
    if task.get('kind') == 'needs-feature-plan':
        n = task.get('count', 0)
        return (
            f"next: needs-feature-plan in cell {task['cell']} "
            f"({n} unplanned footprint{'s' if n != 1 else ''}; "
            f"run propose-features --cell {task['cell']} --record)"
        )
    return f"next: cell {task['cell']} {task['layer']}"
