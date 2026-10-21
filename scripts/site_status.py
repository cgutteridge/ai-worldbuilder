#!/usr/bin/env python3
"""Human-readable progress for Southampton Refine: what is done, what happened since X, and a run log.

Read-only except `log`, which appends to site/RUN-LOG.md. Progress comes from the cell
journal (ADR 004): every journal entry is shown as a stage record of its primary cell
(ground-layer, trees, decor, feature-<plan>, ghost-*, <pass>-undo, other-<plan>).

  .venv/bin/python scripts/site_status.py status                       # grid of cells by ring, marker, lock
  .venv/bin/python scripts/site_status.py since "2026-10-15 17:00"     # every journal entry begun since then
  .venv/bin/python scripts/site_status.py log --cell 47,-45 --did "..." --look "6139,-5742: oak" --problem "..."
  .venv/bin/python scripts/site_status.py morning [--since "2026-10-15 17:00"]   # run log + stage activity + eye list

`status` shows whether ground, trees and decor are applied (layers only). `status --gaps` adds the real done-bar,
`site_handoff.cell_gaps`, per cell (about 20 s).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from cellmath import origin_cell, ring_distance

from script_env import ROOT  # noqa: E402
CELLS = ROOT / 'site/cells'
MARKER = ROOT / 'site/CURRENT-CELL.json'
LOCK = ROOT / 'site/LOCK.json'
RUN_LOG = ROOT / 'site/RUN-LOG.md'
STAMP = re.compile(r'(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d)(?::(\d\d))?')


def __getattr__(name: str):
    """``CENTRE`` is the configured origin cell (no longer hard-coded)."""
    if name == 'CENTRE':
        return origin_cell()
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def ring(cell: tuple[int, int]) -> int:
    return ring_distance(cell, origin_cell())


def parse_stamp(s) -> datetime | None:
    m = STAMP.search(str(s or ''))
    return datetime.fromisoformat(f'{m[1]} {m[2]}:{m[3] or "00"}') if m else None


STATUS = {'begun': 'applying', 'landed': 'applied', 'mismatched': 'applied-with-mismatches',
          'abandoned': 'rolled-back'}
PASS_KEY = {'ground': 'ground-layer', 'trees': 'trees', 'decor': 'decor', 'ghost-ground': 'ghost-ground',
            'ghost-features': 'ghost-features'}


def stage_key(e) -> str:
    """Stage-record name of a journal entry (the names the old STAGES files used).

    @param e: ``cell_journal.EntryView``
    @returns: e.g. ``ground-layer``, ``feature-<plan>``, ``trees-undo``, ``other-<plan>``
    """
    if e.kind == 'undo':
        return f'{e.pass_}-undo'
    if e.pass_ in PASS_KEY:
        return PASS_KEY[e.pass_]
    return f'{e.pass_}-{e.plan}'


def load_stages() -> dict:
    """Progress per cell from the cell journal: the latest entry of each stage key.

    @returns: {(tx, tz): {stage key: {'status', 'plan', 'blocks', 'started', 'readback'}}}
    """
    from cell_registry import view_registry
    reg = view_registry()
    out: dict = {cell: {} for cell in reg.view()}
    for e in reg.entries():
        status = 'undone' if e.undone and e.status == 'landed' else STATUS[e.status]
        out.setdefault(e.cell, {})[stage_key(e)] = {
            'status': status, 'plan': e.plan, 'blocks': e.n, 'started': e.ts,
            'readback': {'mismatches': e.mismatches} if e.status in ('landed', 'mismatched') else {},
            'note': (e.meta or {}).get('note', '')}
    return out


def stage_time(v: dict) -> datetime | None:
    return parse_stamp(v.get('finished')) or parse_stamp((v.get('readback') or {}).get('checked')) \
        or parse_stamp(v.get('started'))


def events(stages: dict, since: datetime | None = None) -> list[dict]:
    """Every stage entry with a timestamp, oldest first: {when, cell, stage, status, blocks, mismatches, note}."""
    out = []
    for cell, sts in stages.items():
        for name, v in sts.items():
            when = stage_time(v)
            if when is None or (since and when < since):
                continue
            rb = v.get('readback') or {}
            out.append({'when': when, 'cell': cell, 'stage': name, 'status': v.get('status', '?'),
                        'blocks': v.get('blocks'), 'mismatches': rb.get('mismatches'),
                        'note': (v.get('note') or '')})
    return sorted(out, key=lambda e: (e['when'], e['cell'], e['stage']))


LAYER_KEYS = {'ground': ('ground-layer', 'ground'), 'trees': ('trees',), 'decor': ('decor',)}
GOOD = ('applied', 'verified')


def layer_status(sts: dict, layer: str):
    """Status of a layer from the latest-stamped of its aliases (hand-made cells say 'ground', later runs 'ground-layer')."""
    best = None
    for key in LAYER_KEYS[layer]:
        v = sts.get(key)
        if v is None:
            continue
        rank = (stage_time(v) or datetime.min, key == LAYER_KEYS[layer][0])
        if best is None or rank > best[0]:
            best = (rank, v.get('status'))
    return best[1] if best else None


def layer_marks(sts: dict) -> str:
    def mark(layer):
        s = layer_status(sts, layer)
        return '✓' if s in GOOD else {'applying': '!', 'undone': '–', None: '·'}.get(s, '⚠')
    return ' '.join(f'{n} {mark(n)}' for n in LAYER_KEYS)


def layers_applied(sts: dict) -> bool:
    return all(layer_status(sts, n) in GOOD for n in LAYER_KEYS)


def gap_summary(cell) -> str:
    """Authoritative: site_handoff.cell_gaps (slow). '' when the cell is truly done."""
    import site_handoff
    g = site_handoff.cell_gaps(cell)
    parts = [k.replace('_', ' ') if v is True else f"{k} {len(v) if isinstance(v, (list, tuple, set, dict)) else v}"
             for k, v in g.items() if v and k != 'in_flight']
    if g.get('in_flight'):
        parts.insert(0, 'IN FLIGHT')
    return ', '.join(parts)


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def lock_line() -> str:
    lk = read_json(LOCK)
    if not lk:
        return 'free'
    return f"HELD by {lk.get('owner')} since {lk.get('claimed_at')} (last heartbeat {lk.get('heartbeat_at')}): {lk.get('task')}"


def marker_cell():
    """Focus cell from the cell journal marker note (ticket #31)."""
    from cell_registry import view_registry
    return view_registry().marker_cell()


def fmt_event(e: dict, with_note: bool = False) -> str:
    bits = [e['when'].strftime('%d %b %H:%M'), f"{e['cell'][0]},{e['cell'][1]}", e['stage'], e['status']]
    if e['blocks'] is not None:
        bits.append(f"{e['blocks']} blocks")
    if e['mismatches']:
        bits.append(f"MISMATCHES {e['mismatches']}")
    line = '  ' + ' · '.join(bits)
    if with_note and e['note']:
        line += '\n      ' + e['note'].strip().replace('\n', ' ')[:200]
    return line


def cmd_status(stages: dict, gaps: bool = False) -> str:
    here = marker_cell()
    lines = [f'MARKER (where work is): {here[0]},{here[1]}' if here else 'MARKER: none',
             f'LOCK: {lock_line()}', '']
    by_ring: dict[int, list] = {}
    for c in stages:
        by_ring.setdefault(ring(c), []).append(c)
    n_layers = n_done = 0
    for r in sorted(by_ring):
        cs = sorted(by_ring[r])
        la = [c for c in cs if layers_applied(stages[c])]
        n_layers += len(la)
        lines.append(f'Ring {r}: {len(la)}/{len(cs)} have ground+trees+decor applied')
        for c in cs:
            flag = '▶' if c == here else ' '
            bad = sorted({n for n, v in stages[c].items() if v.get('status') in ('applying', 'applied-with-mismatches', 'unreadable')})
            row = f"  {flag} {c[0]:>3},{c[1]:<4} {layer_marks(stages[c])}"
            if bad:
                row += f"   ⚠ {', '.join(bad)}"
            if gaps:
                g = gap_summary(c)
                n_done += not g
                row += f"   {'DONE' if not g else 'gaps: ' + g}"
            lines.append(row)
    lines += ['', f'{n_layers}/{len(stages)} cells have all three layers applied (layers only; says nothing about houses, lumps, canopies).']
    lines.append(f'{n_done}/{len(stages)} pass cell_gaps (the real done-bar).' if gaps else
                 'Add --gaps for the real done-bar (site_handoff.cell_gaps per cell, about 20 s). Cells the journal never mentions are not listed.')
    return '\n'.join(lines)


def cmd_since(stages: dict, since: datetime) -> str:
    ev = events(stages, since)
    cells = sorted({e['cell'] for e in ev})
    problems = [e for e in ev if e['mismatches'] or e['status'] in ('applying', 'applied-with-mismatches')]
    lines = [f'SINCE {since:%Y-%m-%d %H:%M}: {len(ev)} journal entries across {len(cells)} cells'
             + (f' ({", ".join(f"{a},{b}" for a, b in cells)})' if cells else '')]
    if problems:
        lines.append('PROBLEMS:')
        lines += [fmt_event(e, True) for e in problems]
    lines.append('ACTIVITY (oldest first):')
    lines += [fmt_event(e) for e in ev] or ['  nothing recorded']
    return '\n'.join(lines)


def run_log_blocks(since: datetime | None = None) -> list[str]:
    if not RUN_LOG.exists():
        return []
    blocks = re.split(r'(?m)^(?=## )', RUN_LOG.read_text())
    out = []
    for b in blocks:
        m = re.match(r'## (\d{4}-\d\d-\d\d \d\d:\d\d)', b)
        if m and (since is None or datetime.fromisoformat(m[1]) >= since):
            out.append(b.rstrip())
    return out


def cmd_log(stages: dict, args) -> str:
    cell = tuple(int(v) for v in args.cell.split(','))
    sts = stages.get(cell, {})
    here = marker_cell()
    block = [f'## {datetime.now():%Y-%m-%d %H:%M} — cell {cell[0]},{cell[1]}',
             f'- Did: {args.did}',
             f'- Layers now: {layer_marks(sts)}  ({"all applied" if layers_applied(sts) else "NOT all applied"})']
    for look in args.look or []:
        block.append(f'- Look at: {look}')
    for prob in args.problem or []:
        block.append(f'- Problem / open: {prob}')
    block.append(f"- Marker: {f'{here[0]},{here[1]}' if here else 'none'} · lock: {'free' if not read_json(LOCK) else 'HELD'}")
    text = '\n'.join(block)
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    if not RUN_LOG.exists():
        RUN_LOG.write_text('# Run log\n\nOne block per finished cell, newest last. Written by the orchestrator via '
                           '`scripts/site_status.py log`. Read with `site_status.py morning`.\n')
    with RUN_LOG.open('a') as fh:
        fh.write('\n' + text + '\n')
    return text


def cmd_morning(stages: dict, since: datetime | None) -> str:
    since = since or datetime.fromtimestamp(0)
    blocks = run_log_blocks(since)
    looks = [ln[2:].strip() for b in blocks for ln in b.splitlines() if ln.startswith('- Look at:')]
    probs = [ln[2:].strip() for b in blocks for ln in b.splitlines() if ln.startswith('- Problem')]
    lines = ['MORNING REPORT' + (f' (since {since:%Y-%m-%d %H:%M})' if since.year > 1970 else ''), '',
             f'Marker: {marker_cell()} · Lock: {lock_line()}', '']
    lines += [f'RUN LOG: {len(blocks)} cell entr{"y" if len(blocks) == 1 else "ies"}']
    lines += blocks or ['  (no run-log entries: use `site_status.py since` for raw stage activity)']
    if probs:
        lines += ['', 'PROBLEMS / OPEN:'] + [f'  {p}' for p in probs]
    if looks:
        lines += ['', 'NEEDS YOUR EYE:'] + [f'  {l}' for l in looks]
    lines += ['', cmd_since(stages, since)]
    return '\n'.join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('status'); p.add_argument('--gaps', action='store_true')
    p = sub.add_parser('since'); p.add_argument('when')
    p = sub.add_parser('morning'); p.add_argument('--since')
    p = sub.add_parser('log')
    p.add_argument('--cell', required=True)
    p.add_argument('--did', required=True)
    p.add_argument('--look', action='append')
    p.add_argument('--problem', action='append')
    a = ap.parse_args(argv)
    stages = load_stages()
    if a.cmd == 'status':
        print(cmd_status(stages, a.gaps))
    elif a.cmd == 'since':
        w = parse_stamp(a.when)
        if not w:
            sys.exit('give a time like "2026-10-15 17:00"')
        print(cmd_since(stages, w))
    elif a.cmd == 'log':
        print(cmd_log(stages, a))
    else:
        print(cmd_morning(stages, parse_stamp(a.since) if a.since else None))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
