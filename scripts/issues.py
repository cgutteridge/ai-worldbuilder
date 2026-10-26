#!/usr/bin/env python3
"""Issues and snags CLI for the active world (``<world>/registry/issues.json``, task #120).

A snag is evidence inside an issue; there are no loose snags. Issues never block a cell.

  python3 scripts/issues.py --world W match  --words TEXT --element ground:48,-44 [--element ...]
  python3 scripts/issues.py --world W add    (--issue I-004 | --new TITLE) --words TEXT --element E ...
                                             [--image PNG] [--source user|agent] [--confidence suspected]
                                             [--pos x,y,z] [--facing yaw,pitch] [--cell tx,tz] [--agent-notes T]
  python3 scripts/issues.py --world W diagnose I-004 [--title T] [--cause T] [--stage S ...]
  python3 scripts/issues.py --world W fix    I-004 --summary T --merged-at 2026-10-26T14:20:00Z
                                             --check S-001="what should look different" ... [--ticket #110] [--branch B]
  python3 scripts/issues.py --world W sync                       # open -> fix-landed where the journal shows a redesign
  python3 scripts/issues.py --world W review I-004               # the packet: every snag, its check, where to teleport
  python3 scripts/issues.py --world W verdict I-004 (--all-fixed | --fixed S-001 S-002 [--not-fixed S-003])
  python3 scripts/issues.py --world W move   S-007 (--to I-002 | --new TITLE)
  python3 scripts/issues.py --world W merge  I-002 I-004         # fold the second into the first
  python3 scripts/issues.py --world W wontfix I-004 --reason T   # the owner's call
  python3 scripts/issues.py --world W list [--status open|fix-landed|confirmed|wontfix] [--cell tx,tz]
  python3 scripts/issues.py --world W show I-004
  python3 scripts/issues.py --world W digest                     # one line for a run summary
  python3 scripts/issues.py --world W verify
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from script_env import ROOT, make_parser, add_world_arg, activate_world_from_args  # noqa: E402

import issue_log as L  # noqa: E402


def _floats(spec: str | None, n: int, what: str):
    if not spec:
        return None
    parts = [float(p) for p in spec.split(',')]
    if len(parts) != n:
        raise L.IssueError(f'{what} needs {n} numbers')
    return parts


def _cell(spec: str | None):
    if not spec:
        return None
    a, b = spec.split(',')
    return [int(a), int(b)]


def _line(it: dict) -> str:
    return f"{it['id']}  [{it['status']}]  {it['title']}  ({len(it['snags'])} snag{'s' * (len(it['snags']) != 1)})"


def _show(it: dict) -> str:
    out = [_line(it), f"  cause: {it['cause'] or '(not diagnosed)'}"]
    if it['stages']:
        out.append('  stages: ' + ', '.join(it['stages']))
    for s in it['snags']:
        flags = ' '.join(f for f in (s['source'], s['confidence'], 'REGRESSION' if s['regression'] else '',
                                     s['resolution'] or '') if f)
        out.append(f"  {s['id']} ({flags}) {', '.join(L.element_label(e) for e in s['elements'])}")
        out.append(f"      \"{s['words']}\"" + (f"  pic: registry/{s['image']}" if s['image'] else ''))
    for f in it['fixes']:
        out.append(f"  fix {f['id']} {f['ticket']} merged {f['merged_at']}: {f['summary']}")
        out += [f'      check {sid}: {line}' for sid, line in f['checks'].items()]
    return '\n'.join(out)


def main(argv: list[str] | None = None) -> int:
    """Run the CLI.

    @param argv: optional argv override
    @returns: exit code
    """
    ap = make_parser(__doc__)
    add_world_arg(ap)
    ap.add_argument('--root', type=Path, default=None, help='world root override (tests)')
    sub = ap.add_subparsers(dest='cmd', required=True)

    def elements(p):
        p.add_argument('--element', action='append', default=[], metavar='E',
                       help='feature:<plan> or ground|trees|decor:<tx,tz> (repeatable)')

    p = sub.add_parser('match', help='candidate issues for a new snag (closed ones included)')
    p.add_argument('--words', required=True)
    elements(p)
    p = sub.add_parser('add', help='file a snag on an existing issue, or start a new one')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--issue')
    g.add_argument('--new', metavar='TITLE')
    p.add_argument('--words', required=True)
    elements(p)
    p.add_argument('--image')
    p.add_argument('--source', choices=L.SOURCES, default='user')
    p.add_argument('--confidence', choices=L.CONFIDENCES, default=None)
    p.add_argument('--pos')
    p.add_argument('--facing')
    p.add_argument('--cell')
    p.add_argument('--agent-notes', default='')
    p = sub.add_parser('diagnose', help='record title, cause and stages')
    p.add_argument('issue')
    p.add_argument('--title')
    p.add_argument('--cause')
    p.add_argument('--stage', action='append')
    p = sub.add_parser('fix', help='log fix work with a check line per covered snag')
    p.add_argument('issue')
    p.add_argument('--summary', required=True)
    p.add_argument('--merged-at', required=True)
    p.add_argument('--check', action='append', default=[], metavar='S-001=TEXT')
    p.add_argument('--ticket', default='')
    p.add_argument('--branch', default='')
    sub.add_parser('sync', help='promote open issues whose elements were redesigned after the fix')
    p = sub.add_parser('review', help='print the review packet')
    p.add_argument('issue')
    p = sub.add_parser('verdict', help="record the owner's judgement")
    p.add_argument('issue')
    p.add_argument('--all-fixed', action='store_true')
    p.add_argument('--fixed', nargs='*', default=[])
    p.add_argument('--not-fixed', nargs='*', default=[])
    p = sub.add_parser('move', help='re-home a snag (split an issue)')
    p.add_argument('snag')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--to')
    g.add_argument('--new', metavar='TITLE')
    p = sub.add_parser('merge', help='fold the second issue into the first')
    p.add_argument('keep')
    p.add_argument('absorb')
    p = sub.add_parser('wontfix')
    p.add_argument('issue')
    p.add_argument('--reason', required=True)
    p = sub.add_parser('list')
    p.add_argument('--status', choices=L.STATUSES)
    p.add_argument('--cell')
    p = sub.add_parser('show')
    p.add_argument('issue')
    sub.add_parser('digest')
    sub.add_parser('verify')

    args = ap.parse_args(argv)
    activate_world_from_args(args)
    root = Path(args.root) if args.root else Path(ROOT)
    try:
        if args.cmd == 'match':
            els = [L.parse_element(e) for e in args.element]
            print(json.dumps(L.match(root, words=args.words, elements=els), indent=2))
        elif args.cmd == 'add':
            els = [L.parse_element(e) for e in args.element]
            conf = args.confidence or ('confirmed' if args.source == 'user' else 'suspected')
            issue, snag = L.add_snag(
                root, issue_id=args.issue, new_title=args.new, source=args.source, words=args.words,
                elements=els, confidence=conf, pos=_floats(args.pos, 3, '--pos'),
                facing=_floats(args.facing, 2, '--facing'), cell=_cell(args.cell), image=args.image,
                agent_notes=args.agent_notes)
            print(f"filed {snag['id']} on {issue['id']} ({issue['title']})"
                  + ('  REGRESSION: issue reopened' if snag['regression'] else ''))
        elif args.cmd == 'diagnose':
            print(_line(L.diagnose(root, args.issue, title=args.title, cause=args.cause, stages=args.stage)))
        elif args.cmd == 'fix':
            checks = {}
            for c in args.check:
                sid, sep, text = c.partition('=')
                if not sep:
                    raise L.IssueError(f'--check wants S-001=TEXT, got {c!r}')
                checks[sid.strip()] = text
            fx = L.log_fix(root, args.issue, summary=args.summary, merged_at=args.merged_at, checks=checks,
                           ticket=args.ticket, branch=args.branch)
            print(f"logged {fx['id']} on {args.issue}")
            rep = L.rebuild_report(root, L.find_issue(L.load(root), args.issue))
            for label, st in rep['elements']:
                print(f'  {label}: {st}')
            if rep['uncovered']:
                print('  also needs a fix covering: ' + ', '.join(rep['uncovered']))
            print('  (the issue becomes fix-landed once every element above is redesigned)')
        elif args.cmd == 'sync':
            moved = L.sync(root)
            print('fix-landed: ' + (', '.join(moved) if moved else '(none)'))
        elif args.cmd == 'review':
            print(json.dumps(L.review_packet(root, args.issue), indent=2))
        elif args.cmd == 'verdict':
            print(_line(L.verdict(root, args.issue, fixed=args.fixed, all_fixed=args.all_fixed,
                                  not_fixed=args.not_fixed)))
        elif args.cmd == 'move':
            print(f'{args.snag} -> {_line(L.move_snag(root, args.snag, to_issue=args.to, new_title=args.new))}')
        elif args.cmd == 'merge':
            print(_line(L.merge_issues(root, args.keep, args.absorb)))
        elif args.cmd == 'wontfix':
            print(_line(L.set_wontfix(root, args.issue, args.reason)))
        elif args.cmd == 'list':
            L.sync(root)
            for it in L.load(root)['issues']:
                if args.status and it['status'] != args.status:
                    continue
                if args.cell and not any(e.get('cell') == _cell(args.cell) for s in it['snags'] for e in s['elements']):
                    continue
                print(_line(it))
        elif args.cmd == 'show':
            print(_show(L.find_issue(L.load(root), args.issue)))
        elif args.cmd == 'digest':
            print(L.digest(root))
        elif args.cmd == 'verify':
            problems = L.verify(root)
            print('\n'.join(problems) if problems else 'issues: OK')
            return 1 if problems else 0
    except (L.IssueError, ValueError) as exc:
        print(f'issues error: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
