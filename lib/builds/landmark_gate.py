#!/usr/bin/env python3
"""Landmark (tier A) candidates need a street-level image before apply (#335).

Church, cathedral, chapel, cinema and shopping-centre footprints are flagged in
``propose-features``. ``feature_build --apply`` refuses until a street-level image
source is recorded for the feature's cell via ``cell_sources.py --record``, unless
a non-empty ``--landmark-override`` reason is given.

Wording stays vendor-neutral: an image source the owner supplies, never a product name.
"""
from __future__ import annotations

from typing import Any, Iterable

# Mapped ``building=*`` values that are tier A landmarks (plan industrial-commercial §4/§7).
LANDMARK_BUILDINGS = frozenset({'church', 'cathedral', 'chapel', 'cinema'})
# ``amenity=cinema`` is the usual cinema tag when ``building`` is generic.
LANDMARK_AMENITIES = frozenset({'cinema'})
# Shopping centre: ``shop=mall`` only — ``building=retail`` alone is every shop, not a centre.
LANDMARK_SHOPS = frozenset({'mall'})


def landmark_class(tags: dict | None) -> str | None:
    """Return the landmark class id for building tags, or ``None`` when not a landmark.

    @param tags: flattened vector-map tags (``building``, ``amenity``, ``shop``, …)
    @returns: ``church`` / ``cathedral`` / ``chapel`` / ``cinema`` / ``shopping_centre``, or None
    """
    if not tags:
        return None
    building = tags.get('building')
    if building in LANDMARK_BUILDINGS:
        return building
    amenity = tags.get('amenity')
    if amenity in LANDMARK_AMENITIES:
        return amenity
    if tags.get('shop') in LANDMARK_SHOPS:
        return 'shopping_centre'
    return None


def is_landmark(tags: dict | None) -> bool:
    """True when tags are a tier A landmark class.

    @param tags: flattened vector-map tags
    @returns: whether apply needs a street-level image (or override)
    """
    return landmark_class(tags) is not None


def landmark_fields(tags: dict | None) -> dict[str, Any]:
    """Candidate fields to merge when tags are a landmark (empty dict otherwise).

    @param tags: flattened vector-map tags
    @returns: ``{'landmark': True, 'landmark_class': ...}`` or ``{}``
    """
    cls = landmark_class(tags)
    if not cls:
        return {}
    return {'landmark': True, 'landmark_class': cls}


def annotate_landmark_candidates(
    items: list[dict[str, Any]],
    *,
    tags_by_ref: dict[str, dict] | None = None,
) -> list[dict[str, Any]]:
    """Copy candidates, setting ``landmark`` / ``landmark_class`` from tags when known.

    Existing ``landmark`` True on a candidate is kept. When ``tags_by_ref`` is given,
    each building (or group) candidate is flagged if any of its refs is a landmark.

    @param items: propose-features candidate dicts
    @param tags_by_ref: optional ``{osm_ref: tags}`` for refs that still lack a landmark field
    @returns: new list of candidate dicts (inputs are not mutated)
    """
    out: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        if row.get('landmark'):
            out.append(row)
            continue
        if row.get('kind') not in ('building', 'group') or not tags_by_ref:
            out.append(row)
            continue
        for ref in row.get('refs') or []:
            fields = landmark_fields(tags_by_ref.get(ref))
            if fields:
                row.update(fields)
                break
        out.append(row)
    return out


def tags_by_ref_from_index(idx) -> dict[str, dict]:
    """Flattened tags keyed by ``osm_ref`` for every building in an index.

    @param idx: ``OSMIndex`` (or object with ``.buildings`` of ``(rings, props)``)
    @returns: ``{ref: tags}``
    """
    from coverage import osm_ref
    from osm_footprints import osm_tags

    out: dict[str, dict] = {}
    for _rings, props in idx.buildings:
        out[osm_ref(props)] = osm_tags(props)
    return out


def cell_has_street_level_source(tx: int, tz: int) -> bool:
    """True when any street-kind checklist source is HAVE for the cell.

    A ``found`` / ``partial`` ``--record`` on a street-level provider, or a useful
    street/facade manifest row, counts. Stale HAVE does not (re-check first).

    @param tx: cell X
    @param tz: cell Z
    @returns: whether a street-level image source is recorded for the cell
    """
    from cell_sources import SOURCE_FAMILIES, source_status

    status = source_status(tx, tz)
    for name, fam in SOURCE_FAMILIES.items():
        if fam.get('kind') != 'street':
            continue
        if (status.get(name) or {}).get('state') == 'have':
            return True
    return False


def any_cell_has_street_level_source(cells: Iterable[tuple[int, int]]) -> bool:
    """True when at least one cell has a recorded street-level image source.

    @param cells: ``(tx, tz)`` pairs
    @returns: whether any cell passes :func:`cell_has_street_level_source`
    """
    return any(cell_has_street_level_source(tx, tz) for tx, tz in cells)


def record_street_level_command(tx: int, tz: int) -> str:
    """CLI the owner runs to record a street-level image source for a cell.

    ``SOURCE`` is a street-level provider id from the cell sources checklist
    (``cell_sources.py --cell …`` / ``--todo``).

    @param tx: cell X
    @param tz: cell Z
    @returns: one-line command with the real ``--record`` flags
    """
    return (
        f'python3 scripts/cell_sources.py --cell {tx},{tz} '
        f'--record SOURCE --result found|partial|none|blocked --note "..."'
    )


def landmark_refuse_message(
    *,
    feature: str,
    landmark: str,
    cell: tuple[int, int],
) -> str:
    """User-facing refusal when a landmark apply lacks a street-level image source.

    @param feature: plan / spec name
    @param landmark: landmark class id (e.g. ``church``)
    @param cell: cell to record against
    @returns: refusal text naming the record command and ``--landmark-override``
    """
    tx, tz = cell
    return (
        f'{feature}: landmark {landmark} — do not build until a street-level image '
        f'source is recorded for cell {tx},{tz}. '
        f'Record one: {record_street_level_command(tx, tz)} '
        f'(SOURCE is a street-level provider id from the cell sources checklist). '
        f'Or pass --landmark-override "<reason>" to build without that source.'
    )


def feature_cells_for_plan(plan: str) -> list[tuple[int, int]]:
    """Cells that list ``plan`` on their journal feature plan.

    @param plan: feature / plan id
    @returns: sorted ``(tx, tz)`` list (may be empty)
    """
    from cell_registry import view_registry

    cells = [c for c, v in view_registry().view().items() if any(f.id == plan for f in v.features)]
    return sorted(cells)


def tags_for_spec_ways(spec: dict, *, idx=None) -> list[dict]:
    """Flattened tags for each way id listed in ``spec['ways']``.

    @param spec: feature build spec
    @param idx: optional ``OSMIndex``; default constructs one
    @returns: one tags dict per matched way (unmatched ways omitted)
    """
    from osm_footprints import OSMIndex, osm_tags

    way_ids = {str(w) for w in (spec.get('ways') or {})}
    if not way_ids:
        return []
    index = idx if idx is not None else OSMIndex()
    out: list[dict] = []
    for _rings, props in index.buildings:
        wid = str(props.get('osm_way_id') or props.get('osm_id') or '')
        if wid in way_ids:
            out.append(osm_tags(props))
    return out


def require_landmark_street_check(
    spec: dict,
    *,
    override_reason: str = '',
    idx=None,
    cells: list[tuple[int, int]] | None = None,
) -> None:
    """Refuse a landmark apply when no street-level image source is recorded.

    @param spec: feature build spec (``name``, ``ways``)
    @param override_reason: non-empty reasoned override skips the street-level check
    @param idx: optional ``OSMIndex`` for way tags (tests)
    @param cells: optional cells to check; default is the feature's planned cells
    @returns: None
    @raises SystemExit: when the feature is a landmark and the gate fails
    """
    reason = (override_reason or '').strip()
    if reason:
        return
    tags_list = tags_for_spec_ways(spec, idx=idx)
    cls = next((landmark_class(t) for t in tags_list if landmark_class(t)), None)
    if not cls:
        return
    planned = list(cells) if cells is not None else feature_cells_for_plan(spec['name'])
    if not planned:
        # Caller should run readiness first; without a cell the record command has no target.
        return
    if any_cell_has_street_level_source(planned):
        return
    raise SystemExit(
        landmark_refuse_message(feature=spec['name'], landmark=cls, cell=planned[0])
    )
