"""Which map refs a planned or built feature covers (tasks 293, 328, 233).

A feature plan is named for what it is, not always for the map way it builds: ``west_w34391673``
builds way ``w34391673``, and the candidate list proposes that way as ``w34391673``. Comparing plan
ids with candidate ids literally proposed it a second time. Every "is this candidate already
accounted for" check asks this module instead — including house groups, which match by recorded
building refs rather than street name alone (#233).

Refs come from the plan's hand-off spec (``site/builds/<plan>.json``, its ``ways``): that is what the
build actually covers. A non-house plan with no readable spec falls back to the trailing ``w<id>`` or
``r<id>`` of its name; a spec's ways always win over the name. House plans (``houses_*``) never use
that name fallback: a trailing ref is not a reliable record of a terrace's extent (#233).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

NAME_REF = re.compile(r'(?:^|_)([wr]\d+)$')
SPEC_REFS, NAME_REFS, NO_REFS = 'spec', 'name', 'none'


def norm_ref(ref) -> str:
    """Map ref in comparable form: ``w123`` (a bare ``123`` from a spec is a way id)."""
    r = str(ref)
    return r if r[:1] in ('w', 'r') else 'w' + r


def spec_refs(root, plan_id: str) -> set[str] | None:
    """Refs recorded on a plan's hand-off spec (its ``ways``).

    @param root: world root
    @param plan_id: feature plan id
    @returns: refs like ``w176658456``, or None when there is no readable spec to say
    """
    try:
        data = json.loads((Path(root) / 'site' / 'builds' / f'{plan_id}.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    ways = data.get('ways') if isinstance(data, dict) else None
    return {norm_ref(k) for k in ways} if isinstance(ways, dict) and ways else None


def plan_refs(root, plan_id: str) -> tuple[set[str], str]:
    """Map refs a plan covers, and where they came from.

    @param root: world root
    @param plan_id: feature plan id
    @returns: ``(refs, source)``; source is ``spec`` (refs stored on the build spec), ``name`` (no
        refs stored: the trailing ref of a non-house plan name) or ``none`` (neither; refs is empty).
        House plans without a readable ``ways`` list always return ``none`` (#233).
    """
    refs = spec_refs(root, plan_id)
    if refs:
        return refs, SPEC_REFS
    # A houses_<street>[_<ref>] name does not record which buildings the plan holds.
    if str(plan_id).startswith('houses_'):
        return set(), NO_REFS
    m = NAME_REF.search(plan_id)
    return ({m.group(1)}, NAME_REFS) if m else (set(), NO_REFS)


def covered_by_plans(root, plan_ids) -> set[str]:
    """Plan ids themselves plus every map ref those plans cover.

    The ids stay in the set so a candidate keyed by a non-ref id (``water_<x>_<z>``) still matches
    its own plan literally.

    @param root: world root
    @param plan_ids: feature plan ids (planned, built or dismissed)
    @returns: ids and refs
    """
    out: set[str] = set()
    for pid in plan_ids:
        out.add(pid)
        out |= plan_refs(root, pid)[0]
    return out


def candidate_covered(item, covered) -> bool:
    """Whether a candidate is fully covered by planned/built refs.

    House groups and ``kind: group`` rows are covered only when every ref is covered (#233, #328).
    A single building or water body matches if its id or any ref is covered. Street name alone
    never covers a house group: matching is by handoff ``ways`` (house plans do not recover a
    trailing name ref as coverage).

    @param item: candidate dict (``kind``, ``id`` and/or ``refs``)
    @param covered: :func:`covered_by_plans` result
    @returns: bool
    """
    refs = {norm_ref(r) for r in item.get('refs') or () if r}
    if item.get('kind') in ('houses', 'group'):
        return bool(refs) and refs <= set(covered)
    keys = {str(item['id'])} if item.get('id') else set()
    keys |= refs
    return bool(keys & covered)


def _houses_remainder(item, left: list) -> dict:
    """A house-group candidate keeping only still-uncovered map refs (#233).

    @param item: original ``kind: houses`` candidate
    @param left: uncovered map refs (same spelling as ``item['refs']``)
    @returns: houses candidate with ``refs``/``label`` (and proportional ``area``) updated
    """
    street = item.get('street')
    n = len(left)
    out = dict(item)
    out['refs'] = left
    out['label'] = f'{street or "unnamed street"} ({n} houses)'
    if 'area' in item:
        total = max(1, len([r for r in (item.get('refs') or ()) if r]))
        out['area'] = max(1, int(item.get('area') or 0) * n // total)
    return out


def uncovered_candidates(item, covered) -> list:
    """Still-needed candidates after plans in ``covered``: drop full hits; split partial groups.

    A ``kind: group`` whose refs are only partly covered becomes one ``kind: building`` row
    per uncovered ref (regrouping happens on the next propose from the map). A ``kind: houses``
    row that is only partly covered keeps one houses candidate with the uncovered refs (#233).
    Fully covered candidates yield an empty list; untouched candidates are returned unchanged.

    @param item: candidate dict (``kind``, ``refs``, …)
    @param covered: :func:`covered_by_plans` result
    @returns: zero or more candidate dicts
    """
    if item.get('kind') == 'houses':
        refs = [r for r in (item.get('refs') or ()) if r]
        left = [r for r in refs if norm_ref(r) not in covered]
        if not left:
            return []
        if len(left) == len(refs):
            return [item]
        return [_houses_remainder(item, left)]
    if item.get('kind') == 'group':
        refs = [r for r in (item.get('refs') or ()) if r]
        left = [r for r in refs if norm_ref(r) not in covered]
        if not left:
            return []
        if len(left) == len(refs):
            return [item]
        return [{
            'kind': 'building',
            'label': str(item.get('label') or r),
            'street': None,
            'refs': [r],
            'bbox': item.get('bbox', (0, 0, 0, 0)),
            'area': item.get('area', 0),
            'portals': list(item.get('portals') or ()),
            'pictures': list(item.get('pictures') or ()),
        } for r in left]
    return [] if candidate_covered(item, covered) else [item]


def duplicate_plans(root, plans: dict[str, dict]) -> list[dict]:
    """Plans whose refs another, differently named plan already covers.

    @param root: world root
    @param plans: ``{plan id: {'status': ..., 'cells': [...], 'dismissed': bool}}``
    @returns: rows ``{'plan', 'refs', 'covered_by', 'source', 'status', 'cells', 'dismissed'}``, one per
        plan/cover pair, sorted by plan then cover. The plan with a spec is the cover; of two
        name-only plans neither is: both are listed so the owner can choose.
    """
    info = {pid: plan_refs(root, pid) for pid in plans}
    rows = []
    for pid, (refs, source) in info.items():
        for other, (orefs, osource) in info.items():
            shared = refs & orefs
            if other == pid or not shared:
                continue
            if osource != SPEC_REFS and source == SPEC_REFS:
                continue            # the spec'd plan is the original; the name-only one is the copy
            if osource == source and pid > other:
                continue            # same standing: list the pair once
            rows.append({'plan': pid, 'refs': sorted(shared), 'covered_by': other, 'source': source,
                         'status': plans[pid].get('status'), 'cells': plans[pid].get('cells', []),
                         'dismissed': bool(plans[pid].get('dismissed'))})
    return sorted(rows, key=lambda r: (r['plan'], r['covered_by']))
