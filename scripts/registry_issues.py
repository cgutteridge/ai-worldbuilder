"""Snagging backlog (site/ISSUES.txt) tied to registry targets (ticket #31).

One open issue per line::

    I-014  cell 48,-44 trees  <why>
    I-015  feature q001_university_road_42  <why>
    I-016  cell 48,-44  <why>          # whole cell

``add_issue`` reopens the target (clears the matching registry flag / feature
status). Clean rebuilds call :func:`auto_close_for_target` to archive with a date.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from script_env import ROOT  # noqa: E402

# I-014  cell 48,-44 trees  reason…
# I-015  feature slug  reason…
_LINE = re.compile(
    r'^I-(\d+)\s+'
    r'(?:cell\s+(-?\d+)\s*,\s*(-?\d+)(?:\s+(ground|trees|decor))?'
    r'|feature\s+(\S+))'
    r'\s+(.*)$'
)


@dataclass(frozen=True)
class Issue:
    """One open snagging line.

    @param id: integer issue number
    @param kind: cell | feature
    @param cell: (tx, tz) when kind is cell
    @param layer: ground|trees|decor|None (whole cell when None)
    @param feature_id: build slug when kind is feature
    @param reason: free text
    @param raw: original line body (without trailing newline)
    """

    id: int
    kind: str
    cell: tuple[int, int] | None
    layer: str | None
    feature_id: str | None
    reason: str
    raw: str

    def target_key(self) -> str:
        """Stable target key for matching auto-close.

        @returns: e.g. ``cell:48,-44:trees`` or ``feature:q001_x``
        """
        if self.kind == 'feature':
            return f'feature:{self.feature_id}'
        layer = self.layer or '*'
        return f'cell:{self.cell[0]},{self.cell[1]}:{layer}'


def issues_path(root: Path | None = None) -> Path:
    """Path to ISSUES.txt under a world root.

    @param root: world root (default active)
    @returns: path
    """
    return (Path(root) if root else Path(ROOT)) / 'site' / 'ISSUES.txt'


def archive_path(root: Path | None = None) -> Path:
    """Path to archived closed issues.

    @param root: world root
    @returns: path
    """
    return (Path(root) if root else Path(ROOT)) / 'site' / 'archive' / 'ISSUES-closed.txt'


def parse_issues(text: str) -> list[Issue]:
    """Parse open issue lines (ignores blanks and ``#`` comments).

    @param text: file contents
    @returns: issues in file order
    """
    out: list[Issue] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith('#'):
            continue
        m = _LINE.match(raw)
        if not m:
            continue
        num = int(m.group(1))
        if m.group(5):
            out.append(Issue(
                id=num, kind='feature', cell=None, layer=None,
                feature_id=m.group(5), reason=m.group(6).strip(), raw=raw,
            ))
        else:
            layer = m.group(4)
            out.append(Issue(
                id=num, kind='cell',
                cell=(int(m.group(2)), int(m.group(3))),
                layer=layer, feature_id=None,
                reason=m.group(6).strip(), raw=raw,
            ))
    return out


def load_issues(root: Path | None = None) -> list[Issue]:
    """Load open issues from disk.

    @param root: world root
    @returns: issues
    """
    path = issues_path(root)
    if not path.exists():
        return []
    return parse_issues(path.read_text(encoding='utf-8'))


def _next_id(root: Path | None = None) -> int:
    open_ids = [i.id for i in load_issues(root)]
    closed = 0
    ap = archive_path(root)
    if ap.exists():
        for m in re.finditer(r'^I-(\d+)\b', ap.read_text(encoding='utf-8'), re.M):
            closed = max(closed, int(m.group(1)))
    return max(open_ids + [closed] + [0]) + 1


def format_issue_line(
    num: int,
    *,
    cell: tuple[int, int] | None = None,
    layer: str | None = None,
    feature_id: str | None = None,
    reason: str,
) -> str:
    """Format one ISSUES.txt line.

    @param num: issue id
    @param cell: cell target
    @param layer: optional layer
    @param feature_id: feature target
    @param reason: why
    @returns: line without newline
    """
    if feature_id:
        return f'I-{num:03d}  feature {feature_id}  {reason}'
    assert cell is not None
    if layer:
        return f'I-{num:03d}  cell {cell[0]},{cell[1]} {layer}  {reason}'
    return f'I-{num:03d}  cell {cell[0]},{cell[1]}  {reason}'


def add_issue(
    *,
    reason: str,
    cell: tuple[int, int] | None = None,
    layer: str | None = None,
    feature_id: str | None = None,
    root: Path | None = None,
    reopen: bool = True,
) -> Issue:
    """Append an issue and optionally reopen the registry target.

    @param reason: why the snag exists
    @param cell: cell target
    @param layer: ground|trees|decor or None for whole cell
    @param feature_id: feature target
    @param root: world root
    @param reopen: clear matching registry flags
    @returns: the new Issue
    @raises ValueError: on bad target
    """
    if bool(feature_id) == bool(cell is not None):
        if feature_id and cell is not None:
            raise ValueError('pass either feature_id or cell, not both')
        if not feature_id and cell is None:
            raise ValueError('pass feature_id or cell')
    if layer and layer not in ('ground', 'trees', 'decor'):
        raise ValueError(f'invalid layer {layer!r}')
    if not reason or not str(reason).strip():
        raise ValueError('reason required')
    num = _next_id(root)
    line = format_issue_line(
        num, cell=cell, layer=layer, feature_id=feature_id, reason=reason.strip(),
    )
    path = issues_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as fh:
        if path.exists() and path.stat().st_size and not path.read_text(encoding='utf-8').endswith('\n'):
            fh.write('\n')
        fh.write(line + '\n')
    issue = parse_issues(line)[0]
    if reopen:
        _reopen_target(issue, root=root)
    return issue


def _reopen_target(issue: Issue, *, root: Path | None = None) -> None:
    from cell_registry import RegistryStore, cell_id

    store = RegistryStore(root)
    if issue.kind == 'feature' and issue.feature_id:
        if store.feature(issue.feature_id):
            store.set_feature_status(issue.feature_id, 'in-progress')
        return
    assert issue.cell is not None
    cid = cell_id(*issue.cell)
    if issue.layer == 'ground' or issue.layer is None:
        store.set_ground(cid, 'none')
    if issue.layer in ('trees', None):
        c = store.cell(cid)
        if c.get('ground') == 'generated' or issue.layer == 'trees':
            try:
                store.set_trees(cid, False)
            except Exception:
                pass
    if issue.layer in ('decor', None):
        try:
            store.set_decor(cid, False)
        except Exception:
            pass
    if issue.layer is None:
        # Whole-cell snag: reopen every registered feature in the cell too.
        for feat in store.features_in_cell(cid):
            if feat.get('status') == 'built':
                store.set_feature_status(feat['id'], 'in-progress')


def remove_open_issue(num: int, *, root: Path | None = None) -> Issue | None:
    """Remove one open issue by id (caller archives).

    @param num: issue id
    @param root: world root
    @returns: removed issue or None
    """
    path = issues_path(root)
    if not path.exists():
        return None
    lines = path.read_text(encoding='utf-8').splitlines(keepends=True)
    kept: list[str] = []
    removed: Issue | None = None
    for line in lines:
        parsed = parse_issues(line.strip())
        if parsed and parsed[0].id == num:
            removed = parsed[0]
            continue
        kept.append(line)
    path.write_text(''.join(kept), encoding='utf-8')
    return removed


def archive_issue(issue: Issue, *, root: Path | None = None, when: str | None = None) -> None:
    """Append a closed issue line with a date.

    @param issue: issue to archive
    @param root: world root
    @param when: ISO date (default today)
    """
    day = when or date.today().isoformat()
    ap = archive_path(root)
    ap.parent.mkdir(parents=True, exist_ok=True)
    with ap.open('a', encoding='utf-8') as fh:
        fh.write(f'{issue.raw}  closed {day}\n')


def auto_close_for_target(
    *,
    cell: tuple[int, int] | None = None,
    layer: str | None = None,
    feature_id: str | None = None,
    root: Path | None = None,
) -> list[Issue]:
    """Close open issues whose target matches a clean rebuild.

    @param cell: cell that was rebuilt
    @param layer: layer cleaned (or None for whole-cell / feature)
    @param feature_id: feature that reached built
    @param root: world root
    @returns: closed issues
    """
    closed: list[Issue] = []
    for issue in list(load_issues(root)):
        match = False
        if feature_id and issue.kind == 'feature' and issue.feature_id == feature_id:
            match = True
        elif cell is not None and issue.kind == 'cell' and issue.cell == cell:
            if issue.layer is None:
                match = layer is None  # whole-cell issue closes on whole-cell rebuild only
            elif layer is None or issue.layer == layer:
                match = True
        if not match:
            continue
        remove_open_issue(issue.id, root=root)
        archive_issue(issue, root=root)
        closed.append(issue)
    return closed


def next_open_issue(root: Path | None = None) -> Issue | None:
    """First open issue in file order.

    @param root: world root
    @returns: issue or None
    """
    issues = load_issues(root)
    return issues[0] if issues else None


def issue_as_task(issue: Issue) -> dict[str, Any]:
    """Shape an issue as a registry ``next`` task dict.

    @param issue: open issue
    @returns: task dict
    """
    task: dict[str, Any] = {
        'kind': 'issue',
        'issue_id': issue.id,
        'reason': issue.reason,
        'raw': issue.raw,
    }
    if issue.kind == 'feature':
        task['target'] = 'feature'
        task['feature'] = issue.feature_id
    else:
        task['target'] = 'cell'
        task['cell'] = f'{issue.cell[0]},{issue.cell[1]}'
        if issue.layer:
            task['layer'] = issue.layer
    return task
