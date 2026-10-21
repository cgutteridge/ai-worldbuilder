"""Deterministic ``next`` work picker for the cell registry (ticket #31).

Read-only and idempotent: open ISSUES first, then the next undone registry item.

Cell choice: marker cell if not DONE, else nearest undone non-held cell to the
configured origin (Chebyshev distance, then clockwise ring order from north).
Order inside a cell: ground → features (registry order) → trees → decor.

The build task raises the marker; ``next`` does not.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import cellmath
from cell_registry import RegistryStore, cell_id, parse_cell_id
from registry_issues import issue_as_task, next_open_issue


def _cell_tuple(cid: str) -> tuple[int, int]:
    return parse_cell_id(cid)


def _undone_work_in_cell(store: RegistryStore, cid: str) -> list[dict[str, Any]]:
    """Ordered remaining work items inside one cell (excluding holds).

    @param store: registry
    @param cid: ``tx,tz``
    @returns: list of task dicts
    """
    c = store.cell(cid)
    if c.get('hold'):
        return []
    out: list[dict[str, Any]] = []
    if c.get('ground') != 'generated':
        out.append({'kind': 'layer', 'cell': cid, 'layer': 'ground'})
        return out  # ground first; later layers wait
    for feat in store.features_in_cell(cid):
        if feat.get('status') != 'built':
            out.append({
                'kind': 'feature',
                'cell': cid,
                'feature': feat['id'],
                'status': feat.get('status'),
                'name': feat.get('name'),
            })
    if not c.get('trees'):
        out.append({'kind': 'layer', 'cell': cid, 'layer': 'trees'})
    if not c.get('decor'):
        out.append({'kind': 'layer', 'cell': cid, 'layer': 'decor'})
    return out


def _candidate_cells(store: RegistryStore) -> list[str]:
    """Cell ids that appear in the registry or are implied by features.

    @param store: registry
    @returns: unique cell ids
    """
    cells = set(store._load_cells().keys())
    for feat in store._load_features().values():
        cells.update(feat.get('cells') or [])
    return sorted(cells, key=lambda s: _cell_tuple(s))


def _pick_cell(store: RegistryStore) -> str | None:
    """Choose the cell to work next (marker if not done, else nearest undone).

    @param store: registry
    @returns: cell id or None when everything done
    """
    origin = cellmath.origin_cell()
    marker = store.current_cell()
    if marker is not None:
        cid = cell_id(*marker)
        if not store.is_done(cid) and not store.cell(cid).get('hold'):
            if _undone_work_in_cell(store, cid):
                return cid

    # Nearest undone non-held: distance then clockwise ring from north.
    best: tuple[int, int, str] | None = None  # (dist, ring_index, cid)
    for cid in _candidate_cells(store):
        if store.is_done(cid) or store.cell(cid).get('hold'):
            continue
        if not _undone_work_in_cell(store, cid):
            continue
        cell = _cell_tuple(cid)
        dist = cellmath.ring_distance(cell, origin)
        ring = cellmath.ring_cells(dist, origin)
        try:
            idx = ring.index(cell)
        except ValueError:
            idx = 0
        key = (dist, idx, cid)
        if best is None or key < best:
            best = key
    return best[2] if best else None


def next_task(store: RegistryStore | None = None, *, root: Path | None = None) -> dict[str, Any] | None:
    """Return the next work item, or None when the registry/issues are idle.

    @param store: optional store
    @param root: world root when constructing a store
    @returns: task dict or None
    """
    reg = store or RegistryStore(root)
    issue = next_open_issue(root or reg.root)
    if issue is not None:
        return issue_as_task(issue)

    cid = _pick_cell(reg)
    if cid is None:
        return None
    work = _undone_work_in_cell(reg, cid)
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
