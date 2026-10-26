"""Issues and snags (``<world>/registry/issues.json``): evidence-backed cause tickets (task #120).

An **issue** is one hypothesised cause with one fix. A **snag** is one observation (your words, a
pic, a position, the elements it sits on) and exists only inside an issue; there is no loose snag.
Issues never block a cell and never steer ``cell_registry.py next``.

States: ``open`` -> ``fix-landed`` -> ``confirmed``, plus ``wontfix``.

* ``fix`` logs work on an issue (ticket, branch, summary, merge time, one **check** line per
  affected snag). A fix without check lines is refused.
* ``fix-landed`` is derived, never typed: every element tagged on the snags a fix covers must have a
  new *landed* build in the cell journal after the fix merged, with different content (blob hash)
  from the last build before it. A rerun with unchanged content is not a redesign.
* ``confirmed`` needs the owner's verdict: every snag ``fixed``. Anything less returns the issue to
  ``open`` with the named snags ``unresolved``.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from atomic_json import write_json

STATUSES = ('open', 'fix-landed', 'confirmed', 'wontfix')
ELEMENT_KINDS = ('feature', 'ground', 'trees', 'decor')
SOURCES = ('user', 'agent')
CONFIDENCES = ('confirmed', 'suspected')
RESOLUTIONS = (None, 'fixed', 'unresolved')
_STOP = frozenset(
    'a an and are as at be but by for from has have in is it its of on or so that the this to was '
    'with not no there these those they are were been into over under than then too very'.split())


class IssueError(Exception):
    """A refused issue operation (bad input or a rule broken); never swallowed."""


def issues_path(root: Path | str) -> Path:
    """Path of ``issues.json`` under a world root.

    @param root: world root
    @returns: path
    """
    return Path(root) / 'registry' / 'issues.json'


def image_dir(root: Path | str) -> Path:
    """Directory holding snag pictures.

    @param root: world root
    @returns: path
    """
    return Path(root) / 'registry' / 'issues' / 'img'


def now_iso() -> str:
    """Current UTC time, second precision, as the journal writes it.

    @returns: ``2026-10-26T03:08:49Z``
    """
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def load(root: Path | str) -> dict[str, Any]:
    """Load the issue store (empty store when none exists yet).

    @param root: world root
    @returns: ``{'version', 'next_issue', 'next_snag', 'issues'}``
    """
    p = issues_path(root)
    if not p.exists():
        return {'version': 1, 'next_issue': 1, 'next_snag': 1, 'issues': []}
    return json.loads(p.read_text(encoding='utf-8'))


def save(root: Path | str, store: dict[str, Any]) -> None:
    """Write the store atomically.

    @param root: world root
    @param store: issue store
    """
    p = issues_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_json(p, store)


# --- elements -----------------------------------------------------------------------------------

def parse_element(spec: str) -> dict[str, Any]:
    """Parse ``feature:<plan>``, or ``ground|trees|decor:<tx,tz>``.

    @param spec: element spec
    @returns: ``{'kind', 'id'}`` for a feature, ``{'kind', 'cell'}`` for a layer
    @raises IssueError: on bad input
    """
    kind, _, rest = str(spec).partition(':')
    if kind not in ELEMENT_KINDS or not rest.strip():
        raise IssueError(f'bad element {spec!r}: use feature:<plan> or ground|trees|decor:<tx,tz>')
    if kind == 'feature':
        return {'kind': 'feature', 'id': rest.strip()}
    m = re.fullmatch(r'\s*(-?\d+)\s*,\s*(-?\d+)\s*', rest)
    if not m:
        raise IssueError(f'bad cell in element {spec!r}: use tx,tz')
    return {'kind': kind, 'cell': [int(m.group(1)), int(m.group(2))]}


def element_label(el: dict[str, Any]) -> str:
    """Plain label for an element.

    @param el: element dict
    @returns: e.g. ``feature q001_x`` or ``trees 48,-44``
    """
    if el['kind'] == 'feature':
        return f"feature {el['id']}"
    return f"{el['kind']} {el['cell'][0]},{el['cell'][1]}"


def _same_element(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return element_label(a) == element_label(b)


# --- journal facts ------------------------------------------------------------------------------

def landed_builds(root: Path | str) -> list[dict[str, Any]]:
    """Landed, not-undone build entries of the cell journal, in order.

    Reads ``registry/journal.jsonl`` directly (read-only) and replays it as ``CellRegistry`` does: a
    ``begin`` of kind ``build`` whose ``finish`` said ``landed``, minus builds voided by a landed undo
    (``targets``), an ``abandoned`` finish, or a ``revive`` that cancels the undo.

    @param root: world root
    @returns: ``[{'seq', 'ts', 'pass', 'plan', 'cell', 'blob'}]``
    """
    path = Path(root) / 'registry' / 'journal.jsonl'
    if not path.exists():
        return []
    begins: dict[int, dict[str, Any]] = {}
    status: dict[int, str] = {}
    undone: set[int] = set()
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        op = e.get('op')
        if op == 'begin':
            begins[e['seq']] = e
        elif op == 'finish':
            ref = e['ref']
            status[ref] = e['status']
            if e['status'] == 'abandoned':
                undone.add(ref)                           # rolled back: counts for nothing
            elif e['status'] == 'landed' and begins.get(ref, {}).get('kind') == 'undo':
                undone.update(begins[ref].get('targets', ()))
        elif op == 'revive':
            undone.difference_update(e.get('targets', ()))
            if e.get('undo') is not None:
                undone.add(e['undo'])
    return [{'seq': s, 'ts': b.get('ts', ''), 'pass': b.get('pass'), 'plan': b.get('plan'),
             'cell': list(b['cell']) if b.get('cell') else None, 'blob': b.get('blob')}
            for s, b in sorted(begins.items())
            if b.get('kind') == 'build' and status.get(s) == 'landed' and s not in undone]


def element_builds(builds: Iterable[dict[str, Any]], el: dict[str, Any]) -> list[dict[str, Any]]:
    """Builds that belong to an element.

    @param builds: from :func:`landed_builds`
    @param el: element dict
    @returns: the element's builds in journal order
    """
    out = []
    for b in builds:
        if el['kind'] == 'feature':
            if b['pass'] == 'feature' and b['plan'] == el['id']:
                out.append(b)
        elif b['pass'] == el['kind'] and b['cell'] == el['cell']:
            out.append(b)
    return out


def redesign_status(root: Path | str, el: dict[str, Any], merged_at: str) -> str:
    """Whether an element was redesigned after a fix merged.

    @param root: world root
    @param el: element dict
    @param merged_at: ISO time the fix merged
    @returns: ``redesigned`` | ``rerun-unchanged`` | ``not-rebuilt``
    """
    mine = element_builds(landed_builds(root), el)
    before = [b for b in mine if b['ts'] <= merged_at]
    after = [b for b in mine if b['ts'] > merged_at]
    if not after:
        return 'not-rebuilt'
    last_before = before[-1]['blob'] if before else None
    return 'redesigned' if any(b['blob'] != last_before for b in after) else 'rerun-unchanged'


# --- lookups ------------------------------------------------------------------------------------

def _norm(prefix: str, ident: Any) -> str:
    """``7``, ``I-7`` and ``I-007`` all name issue 7 (likewise ``S-``)."""
    m = re.fullmatch(rf'(?:{prefix}-)?0*(\d+)', str(ident).strip(), re.I)
    if not m:
        raise IssueError(f'bad id {ident!r}: expected {prefix}-007')
    return f'{prefix}-{int(m.group(1)):03d}'


def find_issue(store: dict[str, Any], issue_id: str) -> dict[str, Any]:
    """The issue with this id.

    @param store: issue store
    @param issue_id: ``I-007`` (or ``7``)
    @returns: issue dict
    @raises IssueError: when unknown
    """
    want = _norm('I', issue_id)
    for it in store['issues']:
        if it['id'] == want:
            return it
    raise IssueError(f'no issue {issue_id}')


def find_snag(store: dict[str, Any], snag_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The snag with this id and its issue.

    @param store: issue store
    @param snag_id: ``S-012``
    @returns: (issue, snag)
    @raises IssueError: when unknown
    """
    snag_id = _norm('S', snag_id)
    for it in store['issues']:
        for s in it['snags']:
            if s['id'] == snag_id:
                return it, s
    raise IssueError(f'no snag {snag_id}')


def _touch(issue: dict[str, Any], event: str, detail: str = '') -> None:
    issue['updated'] = now_iso()
    issue['history'].append({'ts': issue['updated'], 'event': event, 'detail': detail})


def _reopen(issue: dict[str, Any]) -> None:
    """Back to ``open``; the fixes so far are judged, so a new fix is needed to reach ``fix-landed``."""
    issue['status'] = 'open'
    for f in issue['fixes']:
        f['judged'] = True


def _new_snag(store: dict[str, Any], *, source: str, words: str, elements: list[dict[str, Any]],
              confidence: str, pos=None, facing=None, cell=None, image: str | None = None,
              agent_notes: str = '', root: Path | str | None = None,
              snag_id: str | None = None) -> dict[str, Any]:
    if source not in SOURCES:
        raise IssueError(f'source must be one of {SOURCES}')
    if confidence not in CONFIDENCES:
        raise IssueError(f'confidence must be one of {CONFIDENCES}')
    if not words.strip():
        raise IssueError('a snag needs the reporter\'s words (or the agent\'s evidence)')
    if not elements:
        raise IssueError('a snag must be tagged with at least one element (feature:<plan>, '
                         'ground|trees|decor:<tx,tz>)')
    if source == 'user' and not image:
        raise IssueError('a snag you report needs a picture (--image PATH); only agent snags may go without')
    sid = snag_id or f"S-{store['next_snag']:03d}"
    if snag_id is None:
        store['next_snag'] += 1
    rel = None
    if image:
        src = Path(image)
        if not src.exists():
            raise IssueError(f'image not found: {image}')
        if root is None:
            raise IssueError('root required to store an image')
        dest_dir = image_dir(root)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f'{sid}{src.suffix.lower() or ".png"}'
        if src.resolve() != dest.resolve():
            shutil.copyfile(src, dest)
        rel = f'issues/img/{dest.name}'
    return {'id': sid, 'source': source, 'confidence': confidence, 'words': words.strip(),
            'agent_notes': agent_notes.strip(), 'pos': list(pos) if pos else None,
            'facing': list(facing) if facing else None, 'cell': list(cell) if cell else None,
            'image': rel, 'elements': elements, 'added': now_iso(), 'resolution': None,
            'regression': False}


# --- operations ---------------------------------------------------------------------------------

def add_snag(root: Path | str, *, issue_id: str | None, new_title: str | None, **snag_kw: Any) -> tuple[dict, dict]:
    """File one snag: as new evidence on an existing issue, or as the first snag of a new one.

    A snag filed on a ``confirmed`` or ``wontfix`` issue reopens it and is flagged a regression.

    @param root: world root
    @param issue_id: existing issue (None to start a new one)
    @param new_title: plain title of the new issue (when ``issue_id`` is None)
    @param snag_kw: ``source``, ``words``, ``elements``, ``confidence``, ``pos``, ``facing``,
                    ``cell``, ``image``, ``agent_notes``
    @returns: (issue, snag)
    @raises IssueError: on bad input
    """
    if bool(issue_id) == bool(new_title):
        raise IssueError('pass exactly one of an existing issue or a new issue title')
    store = load(root)
    issue = find_issue(store, issue_id) if issue_id else None
    snag = _new_snag(store, root=root, **snag_kw)
    if issue:
        if issue['status'] in ('confirmed', 'wontfix'):
            snag['regression'] = True
            _touch(issue, 'reopened', f"regression: {snag['id']} filed on a {issue['status']} issue")
        if issue['status'] != 'open':
            _reopen(issue)                    # the new snag is not covered by any fix yet
        _touch(issue, 'snag-added', snag['id'])
    else:
        issue = {'id': f"I-{store['next_issue']:03d}", 'title': new_title.strip(), 'cause': '',
                 'stages': [], 'status': 'open', 'opened': now_iso(), 'updated': now_iso(),
                 'snags': [], 'fixes': [], 'history': []}
        store['next_issue'] += 1
        store['issues'].append(issue)
        _touch(issue, 'opened', snag['id'])
    issue['snags'].append(snag)
    save(root, store)
    return issue, snag


def diagnose(root: Path | str, issue_id: str, *, title: str | None = None, cause: str | None = None,
             stages: list[str] | None = None) -> dict[str, Any]:
    """Record a diagnosis: plain title, cause statement, and the stages where the cause lives.

    @param root: world root
    @param issue_id: issue
    @param title: new title (optional)
    @param cause: cause statement (optional)
    @param stages: stage names, free text (optional)
    @returns: the issue
    """
    store = load(root)
    issue = find_issue(store, issue_id)
    if title:
        issue['title'] = title.strip()
    if cause is not None:
        issue['cause'] = cause.strip()
    if stages is not None:
        issue['stages'] = list(stages)
    _touch(issue, 'diagnosed', cause or '')
    save(root, store)
    return issue


def log_fix(root: Path | str, issue_id: str, *, summary: str, merged_at: str, checks: dict[str, str],
            ticket: str = '', branch: str = '') -> dict[str, Any]:
    """Log fix work on an issue. Snags it covers go back to unjudged.

    @param root: world root
    @param issue_id: issue
    @param summary: what changed, plainly
    @param merged_at: ISO time the fix merged (``YYYY-MM-DDTHH:MM:SSZ``)
    @param checks: ``{snag id: what to look at there to tell it is fixed}``
    @param ticket: tracker ticket, e.g. ``#110``
    @param branch: branch name
    @returns: the fix entry
    @raises IssueError: without a check line per covered snag, or on an unknown snag
    """
    if not summary.strip():
        raise IssueError('a fix needs a summary')
    if not checks or any(not str(v).strip() for v in checks.values()):
        raise IssueError('a fix needs a check line for each snag it covers (what should look different)')
    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', merged_at):
        raise IssueError('merged_at must look like 2026-10-26T14:20:00Z')
    store = load(root)
    issue = find_issue(store, issue_id)
    checks = {_norm('S', k): v for k, v in checks.items()}
    have = {s['id']: s for s in issue['snags']}
    for sid in checks:
        if sid not in have:
            raise IssueError(f'{sid} is not a snag of {issue["id"]}')
    fix = {'id': f"F-{len(issue['fixes']) + 1}", 'ticket': ticket, 'branch': branch,
           'summary': summary.strip(), 'merged_at': merged_at,
           'checks': {_norm('S', k): v.strip() for k, v in checks.items()}, 'logged': now_iso(),
           'judged': False}
    issue['fixes'].append(fix)
    for sid in checks:
        have[sid]['resolution'] = None
    if issue['status'] != 'open':
        _reopen(issue)
        fix['judged'] = False
    _touch(issue, 'fix-logged', f"{fix['id']} {ticket} {summary.strip()}")
    save(root, store)
    return fix


def fix_elements(issue: dict[str, Any], fix: dict[str, Any]) -> list[dict[str, Any]]:
    """Elements a fix has to be redesigned on: those of the snags it covers.

    @param issue: issue dict
    @param fix: fix dict
    @returns: distinct elements
    """
    out: list[dict[str, Any]] = []
    for s in issue['snags']:
        if s['id'] in fix['checks']:
            for el in s['elements']:
                if not any(_same_element(el, o) for o in out):
                    out.append(el)
    return out


def rebuild_report(root: Path | str, issue: dict[str, Any]) -> dict[str, Any] | None:
    """Per-element redesign status for an issue's latest fix.

    @param root: world root
    @param issue: issue dict
    @returns: ``{'fix', 'elements': [(label, status)], 'ready'}`` or None with no fix logged
    """
    if not issue['fixes']:
        return None
    fix = issue['fixes'][-1]
    if fix.get('judged'):
        return {'fix': fix, 'elements': [], 'ready': False}
    rows = [(element_label(el), redesign_status(root, el, fix['merged_at'])) for el in fix_elements(issue, fix)]
    return {'fix': fix, 'elements': rows, 'ready': bool(rows) and all(st == 'redesigned' for _, st in rows)}


def sync(root: Path | str) -> list[str]:
    """Promote ``open`` issues to ``fix-landed`` where the journal shows the elements redesigned.

    @param root: world root
    @returns: ids promoted
    """
    store = load(root)
    moved = []
    for issue in store['issues']:
        if issue['status'] != 'open':
            continue
        rep = rebuild_report(root, issue)
        if rep and rep['ready']:
            issue['status'] = 'fix-landed'
            _touch(issue, 'fix-landed', rep['fix']['id'])
            moved.append(issue['id'])
    if moved:
        save(root, store)
    return moved


def review_packet(root: Path | str, issue_id: str) -> dict[str, Any]:
    """Everything the owner needs to judge an issue, snag by snag.

    @param root: world root
    @param issue_id: issue
    @returns: issue facts, the last fix, and for every snag: words, picture, position, elements, check
    @raises IssueError: when the issue has no fix logged
    """
    store = load(root)
    issue = find_issue(store, issue_id)
    if not issue['fixes']:
        raise IssueError(f'{issue["id"]} has no fix logged; nothing to review')
    fix = issue['fixes'][-1]
    snags = []
    for s in issue['snags']:
        snags.append({**{k: s[k] for k in ('id', 'words', 'agent_notes', 'image', 'pos', 'facing', 'cell',
                                            'source', 'confidence', 'regression', 'resolution')},
                      'elements': [element_label(e) for e in s['elements']],
                      'check': fix['checks'].get(s['id'], '(not covered by the last fix)')})
    return {'issue': issue['id'], 'title': issue['title'], 'cause': issue['cause'], 'stages': issue['stages'],
            'status': issue['status'], 'fix': {k: fix[k] for k in ('id', 'ticket', 'branch', 'summary', 'merged_at')},
            'rebuilds': rebuild_report(root, issue)['elements'], 'snags': snags}


def verdict(root: Path | str, issue_id: str, *, fixed: list[str] | None = None, all_fixed: bool = False,
            not_fixed: list[str] | None = None) -> dict[str, Any]:
    """Record the owner's judgement. Only here can an issue become ``confirmed``.

    @param root: world root
    @param issue_id: issue
    @param fixed: snag ids judged fixed
    @param all_fixed: every snag fixed
    @param not_fixed: snag ids still wrong (the rest of the snags must be named fixed or all_fixed)
    @returns: the issue
    @raises IssueError: on conflicting or incomplete verdicts
    """
    store = load(root)
    issue = find_issue(store, issue_id)
    if issue['status'] != 'fix-landed':
        raise IssueError(f'{issue["id"]} is {issue["status"]}; a verdict needs a landed fix '
                         '(status fix-landed: a fix logged and its elements redesigned)')
    ids = [s['id'] for s in issue['snags']]
    if all_fixed:
        if fixed or not_fixed:
            raise IssueError('--all-fixed cannot be combined with other verdicts')
        fixed, not_fixed = ids, []
    fixed, not_fixed = [_norm('S', i) for i in fixed or []], [_norm('S', i) for i in not_fixed or []]
    unknown = [i for i in fixed + not_fixed if i not in ids]
    if unknown or set(fixed) & set(not_fixed):
        raise IssueError(f'unknown or doubly-judged snags: {unknown or sorted(set(fixed) & set(not_fixed))}')
    if not not_fixed:
        missing = [i for i in ids if i not in fixed]
        if missing:
            raise IssueError(f'every snag needs a verdict; not judged: {missing} (use --all-fixed or --not-fixed)')
    by = {s['id']: s for s in issue['snags']}
    for i in fixed:
        by[i]['resolution'] = 'fixed'
    for i in not_fixed:
        by[i]['resolution'] = 'unresolved'
    if all(s['resolution'] == 'fixed' for s in issue['snags']):
        issue['status'] = 'confirmed'
        issue['fixes'][-1]['judged'] = True
        _touch(issue, 'confirmed', 'all snags fixed')
    else:
        _reopen(issue)
        _touch(issue, 'verdict', f'unresolved: {", ".join(not_fixed)}')
    save(root, store)
    return issue


def set_wontfix(root: Path | str, issue_id: str, reason: str) -> dict[str, Any]:
    """Close an issue as not worth fixing (the owner's call).

    @param root: world root
    @param issue_id: issue
    @param reason: why
    @returns: the issue
    """
    if not reason.strip():
        raise IssueError('wontfix needs a reason')
    store = load(root)
    issue = find_issue(store, issue_id)
    issue['status'] = 'wontfix'
    _touch(issue, 'wontfix', reason.strip())
    save(root, store)
    return issue


def move_snag(root: Path | str, snag_id: str, *, to_issue: str | None, new_title: str | None) -> dict[str, Any]:
    """Re-home a snag to another issue or to a new one (a split).

    @param root: world root
    @param snag_id: snag
    @param to_issue: destination issue id
    @param new_title: title of a new destination issue
    @returns: destination issue
    @raises IssueError: when it would empty the source issue or the target is unclear
    """
    if bool(to_issue) == bool(new_title):
        raise IssueError('pass exactly one of --to ISSUE or --new TITLE')
    store = load(root)
    src, snag = find_snag(store, snag_id)
    if len(src['snags']) == 1:
        raise IssueError(f'{snag_id} is the only snag of {src["id"]}; merge or wontfix the issue instead')
    if to_issue:
        dest = find_issue(store, to_issue)
        if dest is src:
            raise IssueError('snag is already on that issue')
    else:
        dest = {'id': f"I-{store['next_issue']:03d}", 'title': new_title.strip(), 'cause': '', 'stages': [],
                'status': 'open', 'opened': now_iso(), 'updated': now_iso(), 'snags': [], 'fixes': [],
                'history': []}
        store['next_issue'] += 1
        store['issues'].append(dest)
    src['snags'].remove(snag)
    snag['resolution'] = None
    dest['snags'].append(snag)
    if dest['status'] in ('confirmed', 'wontfix'):
        snag['regression'] = True
    if dest['status'] != 'open':
        _reopen(dest)
    snag_id = snag['id']
    for it in src['fixes']:
        it['checks'].pop(snag_id, None)
    src['fixes'] = [f for f in src['fixes'] if f['checks']]
    _touch(src, 'snag-moved-out', f'{snag_id} -> {dest["id"]}')
    _touch(dest, 'snag-moved-in', f'{snag_id} <- {src["id"]}')
    save(root, store)
    return dest


def merge_issues(root: Path | str, keep: str, absorb: str) -> dict[str, Any]:
    """Merge ``absorb`` into ``keep`` (same case). The absorbed issue is removed; its history is kept.

    @param root: world root
    @param keep: issue that stays
    @param absorb: issue folded into it
    @returns: the kept issue
    """
    store = load(root)
    a, b = find_issue(store, keep), find_issue(store, absorb)
    if a is b:
        raise IssueError('cannot merge an issue into itself')
    a['snags'] += b['snags']
    a['fixes'] += [{**f, 'id': f"F-{len(a['fixes']) + i + 1}", 'from': b['id']} for i, f in enumerate(b['fixes'])]
    a['history'] += [{**h, 'detail': f"[{b['id']}] {h['detail']}"} for h in b['history']]
    if not (a['status'] == b['status'] and a['status'] in ('confirmed', 'wontfix')):
        _reopen(a)                      # a merge reopens the question: log a fix covering every snag
    store['issues'].remove(b)
    _touch(a, 'merged', f"absorbed {b['id']} ({b['title']})")
    save(root, store)
    return a


# --- matching -----------------------------------------------------------------------------------

def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r'[a-z0-9]+', text.lower()) if len(w) > 2 and w not in _STOP}


def match(root: Path | str, *, words: str, elements: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    """Candidate issues a new snag may belong to, closed ones included.

    Scores element overlap (same feature, same layer and cell, same cell) and word overlap with the
    issue title, cause and snag words. The result carries what a person needs to decide.

    @param root: world root
    @param words: the new snag's words
    @param elements: its elements
    @param limit: how many candidates
    @returns: ``[{'issue', 'title', 'cause', 'status', 'score', 'why', 'snags': [{'id','words','image'}], 'last_fix', 'confirmed'}]``
    """
    store = load(root)
    want = _tokens(words)
    out = []
    for it in store['issues']:
        score, why = 0.0, []
        for el in elements:
            for have in (e for s in it['snags'] for e in s['elements']):
                if _same_element(el, have):
                    score += 3
                    why.append(f'same element ({element_label(el)})')
                    break
                if el['kind'] != 'feature' and have['kind'] != 'feature' and el['cell'] == have['cell']:
                    score += 1
                    why.append(f"same cell ({el['cell'][0]},{el['cell'][1]})")
                    break
        have_words = _tokens(' '.join([it['title'], it['cause']] + [s['words'] for s in it['snags']]))
        shared = sorted(want & have_words)
        if shared:
            score += min(len(shared), 5)
            why.append('shared wording: ' + ', '.join(shared[:5]))
        if score <= 0:
            continue
        out.append({'issue': it['id'], 'title': it['title'], 'cause': it['cause'], 'status': it['status'],
                    'score': score, 'why': sorted(set(why)),
                    'snags': [{'id': s['id'], 'words': s['words'], 'image': s['image']} for s in it['snags']],
                    'last_fix': it['fixes'][-1]['summary'] if it['fixes'] else None,
                    'confirmed': it['status'] == 'confirmed'})
    out.sort(key=lambda c: -c['score'])
    return out[:limit]


# --- listing and checks -------------------------------------------------------------------------

def digest(root: Path | str) -> str:
    """One run-summary line, e.g. ``issues: 6 open, 2 fixes ready for your review``.

    @param root: world root
    @returns: line
    """
    sync(root)
    its = load(root)['issues']
    n = lambda st: sum(1 for i in its if i['status'] == st)  # noqa: E731
    return f"issues: {n('open')} open, {n('fix-landed')} fixes ready for your review"


def verify(root: Path | str) -> list[str]:
    """Problems in the store (empty when sound).

    @param root: world root
    @returns: messages
    """
    store = load(root)
    out: list[str] = []
    seen_i, seen_s = set(), set()
    for it in store['issues']:
        if it['id'] in seen_i:
            out.append(f"duplicate issue id {it['id']}")
        seen_i.add(it['id'])
        if it['status'] not in STATUSES:
            out.append(f"{it['id']}: bad status {it['status']!r}")
        if not it['snags']:
            out.append(f"{it['id']}: no snags (an issue exists only to hold snags)")
        for s in it['snags']:
            if s['id'] in seen_s:
                out.append(f"duplicate snag id {s['id']}")
            seen_s.add(s['id'])
            if not s['elements']:
                out.append(f"{s['id']}: no element tagged")
            if s['source'] == 'user' and not s['image']:
                out.append(f"{s['id']}: your snag has no picture")
            if s['image'] and not (Path(root) / 'registry' / s['image']).exists():
                out.append(f"{s['id']}: picture missing ({s['image']})")
            if s['resolution'] not in RESOLUTIONS:
                out.append(f"{s['id']}: bad resolution {s['resolution']!r}")
        if it['status'] == 'confirmed' and any(s['resolution'] != 'fixed' for s in it['snags']):
            out.append(f"{it['id']}: confirmed with snags not agreed fixed")
        for f in it['fixes']:
            for sid in f['checks']:
                if sid not in {s['id'] for s in it['snags']}:
                    out.append(f"{it['id']} {f['id']}: check for {sid}, which is not on this issue")
    return out
