"""Shared provenance for evidence-read plan items (facade, water, decor, …).

Plan items record *how* they were read, without a new registry store: each
``provenance`` dict cites existing ``registry/evidence`` ids (via
``decision_record.load_evidence``), a source kind, confidence, and an optional
unresolved reason. Consumers (#145 facade, #148 water, #150 decor) import this
module and must not invent private provenance fields.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import Any

import decision_record as dr

SOURCES = (
    'given_coordinate',
    'register_entry',
    'photo_read',
    'aerial',
    'lidar',
    'vector_map',
    'inference',
)
CONFIDENCES = ('high', 'medium', 'low')
PROVENANCE_KEY = 'provenance'


class ProvenanceError(ValueError):
    """Refused provenance input (bad source, confidence, or unknown evidence id)."""


def provenance_record(
    *,
    source: str,
    confidence: str,
    evidence_ids: Sequence[str] = (),
    unresolved_reason: str = '',
    root: Path | None = None,
) -> dict[str, Any]:
    """Build one validated provenance record for a plan item.

    @param source: how the item was read; one of ``SOURCES``
    @param confidence: certainty of the read; one of ``CONFIDENCES``
    @param evidence_ids: ``registry/evidence`` ids (must already exist when ``root`` is set)
    @param unresolved_reason: non-empty when the item could not be placed; empty when resolved
    @param root: world root used to check evidence ids; ``None`` skips the existence check
    @returns: ``{source, confidence, evidence_ids, unresolved_reason}``
    @raises ProvenanceError: unknown source/confidence, or an evidence id with no record
    """
    if source not in SOURCES:
        raise ProvenanceError(f'source must be one of {SOURCES}, got {source!r}')
    if confidence not in CONFIDENCES:
        raise ProvenanceError(f'confidence must be one of {CONFIDENCES}, got {confidence!r}')
    ids = list(dict.fromkeys(str(i) for i in evidence_ids))
    if root is not None:
        known = dr.load_evidence(root)
        missing = [i for i in ids if i not in known]
        if missing:
            raise ProvenanceError(
                f'no evidence record for: {", ".join(missing)} (evidence add first)'
            )
    return {
        'source': source,
        'confidence': confidence,
        'evidence_ids': ids,
        'unresolved_reason': (unresolved_reason or '').strip(),
    }


def is_unresolved(provenance: Mapping[str, Any] | None) -> bool:
    """True when a provenance record names a non-empty unresolved reason.

    @param provenance: a provenance dict, or ``None``
    @returns: whether the item is unresolved
    """
    if not provenance:
        return False
    return bool(str(provenance.get('unresolved_reason') or '').strip())


def attach_provenance(
    item: MutableMapping[str, Any],
    *,
    source: str,
    confidence: str,
    evidence_ids: Sequence[str] = (),
    unresolved_reason: str = '',
    root: Path | None = None,
) -> MutableMapping[str, Any]:
    """Attach a provenance record to a plan item under ``PROVENANCE_KEY``.

    @param item: mutable plan-item dict (entrance, decor row, water body, …)
    @param source: see ``provenance_record``
    @param confidence: see ``provenance_record``
    @param evidence_ids: see ``provenance_record``
    @param unresolved_reason: see ``provenance_record``
    @param root: see ``provenance_record``
    @returns: the same ``item``, with ``item['provenance']`` set
    """
    item[PROVENANCE_KEY] = provenance_record(
        source=source,
        confidence=confidence,
        evidence_ids=evidence_ids,
        unresolved_reason=unresolved_reason,
        root=root,
    )
    return item


def _item_cells(item: Mapping[str, Any]) -> list[tuple[int, int]]:
    if 'cells' in item and item['cells'] is not None:
        return [(int(c[0]), int(c[1])) for c in item['cells']]
    if 'cell' in item and item['cell'] is not None:
        c = item['cell']
        return [(int(c[0]), int(c[1]))]
    return []


def unresolved_for_cell(cell: Sequence[int] | tuple[int, int], items: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """List unresolved plan items that touch a cell (for the cell view).

    Each item is included when its ``provenance`` has a non-empty
    ``unresolved_reason`` and its ``cell`` or ``cells`` includes ``cell``.

    @param cell: ``(tx, tz)``
    @param items: plan items that may carry ``provenance`` plus ``cell`` / ``cells``
    @returns: matching items in input order
    """
    target = (int(cell[0]), int(cell[1]))
    out: list[Mapping[str, Any]] = []
    for item in items:
        if not is_unresolved(item.get(PROVENANCE_KEY)):
            continue
        if target in _item_cells(item):
            out.append(item)
    return out
