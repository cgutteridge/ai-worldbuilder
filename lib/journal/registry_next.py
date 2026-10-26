"""Deterministic ``next`` work picker for the cell registry (ticket #31).

Read-only and idempotent: open ISSUES first, then the next undone registry item.

Cell choice: marker cell if not DONE, else nearest undone non-held cell to the
configured origin (Chebyshev distance, then clockwise ring order from north), over the
cell journal views (``CellRegistry.view()``).
Order inside a cell: ground → features (by id) → trees → decor.

The build task raises the marker; ``next`` does not.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import cellmath
from cell_journal import CellRegistry, CellView
from cell_registry import cell_id, view_registry
from registry_issues import issue_as_task, next_open_issue


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
    """Return the next work item, or None when the journal/issues are idle.

    @param registry: optional CellRegistry
    @param root: world root when opening one
    @returns: task dict or None
    """
    reg = registry or view_registry(root)
    issue = next_open_issue(root or reg.root)
    if issue is not None:
        return issue_as_task(issue)
    v = _pick_cell(reg.view(), reg.marker_cell())
    if v is None:
        return None
    work = _undone_work_in_cell(v)
    return work[0] if work else None

def format_next(task: dict[str, Any] | None) -> str:
    """Human-readable one-liner for a next task.

    @param task: from :func:`next_task`
    @returns: text
    """
    if task is None:
        return 'next: (nothing — registry idle)'
    if task.get('kind') == 'issue':
        return f"next: issue I-{task['issue_id']:03d}  {task.get('raw', '')}"
    if task.get('kind') == 'feature':
        return (
            f"next: feature {task['feature']} in cell {task['cell']} "
            f"(status={task.get('status')})"
        )
    return f"next: cell {task['cell']} {task['layer']}"
