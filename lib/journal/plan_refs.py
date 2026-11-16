"""Which map refs a planned or built feature covers (task 293).

A feature plan is named for what it is, not always for the map way it builds: ``west_w34391673``
builds way ``w34391673``, and the candidate list proposes that way as ``w34391673``. Comparing plan
ids with candidate ids literally proposed it a second time. Every "is this candidate already
accounted for" check asks this module instead.

Refs come from the plan's hand-off spec (``site/builds/<plan>.json``, its ``ways``): that is what the
build actually covers. Only a plan with no readable spec falls back to the trailing ``w<id>`` or
``r<id>`` of its name; a spec's ways always win over the name.
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
        refs stored: the trailing ref of the plan name) or ``none`` (neither; refs is empty)
    """
    refs = spec_refs(root, plan_id)
    if refs:
        return refs, SPEC_REFS
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
    """Whether a non-house candidate's id or any of its refs is covered.

    House groups key by street, not by a global map ref (see ``plan_ids_for_candidates``), so they
    are never matched here.

    @param item: candidate dict (``kind``, ``id`` and/or ``refs``)
    @param covered: :func:`covered_by_plans` result
    @returns: bool
    """
    if item.get('kind') == 'houses':
        return False
    keys = {str(item['id'])} if item.get('id') else set()
    keys |= {norm_ref(r) for r in item.get('refs') or () if r}
    return bool(keys & covered)


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
