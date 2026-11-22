#!/usr/bin/env python3
"""Decision record: which references were available and which were consulted, per phase of every element.

Source of truth is JSON in the active world, regenerated into HTML by ``site_pages`` on every write:

  registry/evidence/<id>.json     one reference, stored once; any number of elements and cells point at it
  registry/decisions/<element>.json   {element, kind, cells, phases: [{phase, ts, owner, available, consulted, decision}]}

An element is a plan id (``cell_48_m44_ground``, a feature id). A phase is a decision question
(``footprint``, ``roof form``, ``ground surface``, ``tree cover``; see ``rules/evidence-order.md``).
``available`` is what the tool offered (build tools may pass it automatically); ``consulted`` is what the
agent actually used, each with ``used_for``. Anything consulted is also available.

  python3 scripts/decision_record.py evidence add ID --type aerial --provider P --title T [--url U] [--path F] [--note N]
  python3 scripts/decision_record.py phase add ELEMENT PHASE --kind feature --cell 48,-44 [--cell ...]
        --available a,b,c --consulted a:"roof ridge" --consulted b --decision "text"
  python3 scripts/decision_record.py show ELEMENT
  python3 scripts/decision_record.py missing        # landed builds with no record (recording starts with the first record)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from script_env import ROOT  # noqa: E402
from atomic_json import write_json  # noqa: E402

KINDS = ('feature', 'ground', 'trees', 'decor')
SAFE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')


class RecordError(ValueError):
    pass


def evidence_dir(root: Path | None = None) -> Path:
    return Path(root or ROOT) / 'registry/evidence'


def decisions_dir(root: Path | None = None) -> Path:
    return Path(root or ROOT) / 'registry/decisions'


def _check_id(name: str, what: str) -> str:
    if not SAFE_ID.match(name or ''):
        raise RecordError(f'bad {what} id {name!r}: letters, digits, _ . - only')
    return name


def _load(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def load_evidence(root: Path | None = None) -> dict[str, dict]:
    d = evidence_dir(root)
    return {p.stem: json.loads(p.read_text()) for p in sorted(d.glob('*.json'))} if d.is_dir() else {}


def load_decisions(root: Path | None = None) -> dict[str, dict]:
    d = decisions_dir(root)
    return {p.stem: json.loads(p.read_text()) for p in sorted(d.glob('*.json'))} if d.is_dir() else {}


def add_evidence(ref: str, *, type: str, provider: str, title: str, url: str = '', path: str = '',
                 note: str = '', root: Path | None = None) -> dict:
    """Create or replace one reference record.

    @returns: the stored record
    """
    rec = {'id': _check_id(ref, 'evidence'), 'type': type, 'provider': provider, 'title': title,
           'url': url, 'path': path, 'note': note}
    write_json(evidence_dir(root) / f'{ref}.json', rec)
    return rec


def add_phase(element: str, phase: str, *, kind: str, cells, available=(), consulted=(),
              decision: str, owner: str = '', root: Path | None = None, now: str | None = None) -> dict:
    """Append one decision phase to an element's record (created on first use).

    @param consulted: ids, or (id, used_for) pairs
    @raises RecordError: unknown kind, empty decision, or a reference with no evidence record
    """
    _check_id(element, 'element')
    if kind not in KINDS:
        raise RecordError(f'kind must be one of {KINDS}, got {kind!r}')
    if not (phase or '').strip() or not (decision or '').strip():
        raise RecordError('phase and decision must be non-empty')
    cons = [{'ref': c, 'used_for': ''} if isinstance(c, str) else {'ref': c[0], 'used_for': c[1]} for c in consulted]
    avail = list(dict.fromkeys([*available, *(c['ref'] for c in cons)]))
    known = load_evidence(root)
    missing = [r for r in avail if r not in known]
    if missing:
        raise RecordError(f'no evidence record for: {", ".join(missing)} (evidence add first)')
    path = decisions_dir(root) / f'{element}.json'
    rec = _load(path) or {'element': element, 'kind': kind, 'cells': [], 'phases': []}
    if rec['kind'] != kind:
        raise RecordError(f'{element} is recorded as {rec["kind"]}, not {kind}')
    for c in cells:
        c = [int(c[0]), int(c[1])]
        if c not in rec['cells']:
            rec['cells'].append(c)
    rec['phases'].append({'phase': phase, 'ts': now or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                          'owner': owner, 'available': avail, 'consulted': cons, 'decision': decision})
    write_json(path, rec)
    return rec


def element_cells(rec: dict) -> list[tuple[int, int]]:
    return [tuple(c) for c in rec['cells']]


def cell_elements(cell, decisions: dict[str, dict]) -> list[dict]:
    return [r for r in decisions.values() if tuple(cell) in element_cells(r)]


def evidence_users(decisions: dict[str, dict]) -> dict[str, dict]:
    """{evidence id: {'elements': [ids], 'cells': [cells]}} so a shared record shows every user."""
    out: dict[str, dict] = {}
    for r in decisions.values():
        for ph in r['phases']:
            for ref in ph['available']:
                u = out.setdefault(ref, {'elements': [], 'cells': []})
                if r['element'] not in u['elements']:
                    u['elements'].append(r['element'])
                for c in element_cells(r):
                    if c not in u['cells']:
                        u['cells'].append(c)
    return out


def missing_records(entries, decisions: dict[str, dict]) -> list[str]:
    """Plans with a landed build and no record, counted only from the first record onwards.

    @param entries: journal EntryView objects (``plan``, ``status``, ``ts``, ``undone``)
    """
    starts = [p['ts'] for r in decisions.values() for p in r['phases']]
    if not starts:
        return []
    start = min(starts)
    out = []
    for e in entries:
        plan = re.split(r'[:\s]', str(e.plan))[0]
        if e.status == 'landed' and not e.undone and str(e.ts) >= start and plan not in decisions and plan not in out:
            out.append(plan)
    return out


def _regen(element: str | None = None) -> None:
    try:
        import site_pages
        site_pages.regenerate(cached_gaps=True)
    except Exception as exc:  # the record is already saved; pages can be rebuilt with site_pages.py
        print(f'warning: record saved but pages not regenerated: {exc}', file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    ev = sub.add_parser('evidence').add_subparsers(dest='sub', required=True).add_parser('add')
    ev.add_argument('id')
    ev.add_argument('--type', required=True)
    ev.add_argument('--provider', required=True)
    ev.add_argument('--title', required=True)
    for k in ('url', 'path', 'note'):
        ev.add_argument(f'--{k}', default='')
    ph = sub.add_parser('phase').add_subparsers(dest='sub', required=True).add_parser('add')
    ph.add_argument('element')
    ph.add_argument('phase')
    ph.add_argument('--kind', required=True, choices=KINDS)
    ph.add_argument('--cell', action='append', required=True, help='tx,tz (repeat for several cells)')
    ph.add_argument('--available', default='', help='comma-separated evidence ids')
    ph.add_argument('--consulted', action='append', default=[], help='id or id:used_for (repeat)')
    ph.add_argument('--decision', required=True)
    ph.add_argument('--owner', default='')
    sh = sub.add_parser('show')
    sh.add_argument('element')
    sub.add_parser('missing')
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'evidence':
            add_evidence(a.id, type=a.type, provider=a.provider, title=a.title, url=a.url, path=a.path, note=a.note)
            print(f'evidence {a.id} saved')
        elif a.cmd == 'phase':
            cells = [tuple(int(x) for x in c.split(',')) for c in a.cell]
            cons = [tuple(c.split(':', 1)) if ':' in c else c for c in a.consulted]
            add_phase(a.element, a.phase, kind=a.kind, cells=cells, available=[x for x in a.available.split(',') if x],
                      consulted=cons, decision=a.decision, owner=a.owner)
            print(f'{a.element}: phase {a.phase!r} recorded')
        elif a.cmd == 'show':
            print(json.dumps(load_decisions().get(a.element), indent=2))
            return 0
        else:
            from cell_registry import view_registry
            miss = missing_records(view_registry().entries(), load_decisions())
            print('\n'.join(miss) or 'none')
            return 1 if miss else 0
    except RecordError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    _regen()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
