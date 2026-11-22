#!/usr/bin/env python3
"""List landed feature plans whose recorded builder or spec has moved on (read-only, #248).

  python3 scripts/builder_audit.py --world <name> [--since <sha>]

For the latest landed, not-undone feature build of each plan, the journal meta may carry
``builder_name``, ``builder_sha`` (tool git HEAD at build time) and ``spec_hash`` (see
``builder_provenance.provenance``). A plan is

  stale       builder sha is strictly older than ``--since`` (an ancestor of it), and/or the spec file
              now hashes differently from the recorded hash;
  current     recorded and neither condition holds;
  unrecorded  built before provenance was recorded (journal schema 1) or recorded without a sha/hash.
              Listed separately; historical values are never inferred or backfilled. Explicitly
              registered reapply builders are annotated; historical provenance stays unrecorded.

Writes nothing. ``reapply_feature`` is unchanged (#278).
"""
from __future__ import annotations

import argparse
import subprocess
import sys

from builder_provenance import provenance_status, resolve_builder_path, spec_hash
from script_env import TOOL_ROOT


def landed_feature_entries(entries) -> list:
    """Latest landed, not-undone feature build entry per plan, in plan order."""
    latest = {}
    for e in entries:
        if e.kind == 'build' and e.pass_ == 'feature' and e.status == 'landed' and not e.undone:
            latest[e.plan] = e
    return [latest[k] for k in sorted(latest)]


def git_older_than(since: str, root=TOOL_ROOT):
    """callable(sha): True when sha is a strict ancestor of ``since`` (unknown shas count as not older)."""
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=20)
    resolved = git('rev-parse', '--verify', since + '^{commit}')
    if resolved.returncode:
        raise SystemExit(f'--since {since!r} is not a commit in {root}')
    target = resolved.stdout.strip()
    return lambda sha: sha != target and git('merge-base', '--is-ancestor', sha, target).returncode == 0


def current_hash(meta, resolve=resolve_builder_path):
    """Hash of the recorded spec path as it is now; None when the file is gone or unreadable."""
    path = meta.get('spec_path')
    if not path:
        return None
    try:
        return spec_hash(resolve(path))
    except (OSError, ValueError):
        return None


def audit(entries, since_ancestor=None, resolve=resolve_builder_path) -> dict:
    """Classify every landed feature plan: {'stale': [...], 'current': [...], 'unrecorded': [...]}."""
    out = {'stale': [], 'current': [], 'unrecorded': []}
    for e in landed_feature_entries(entries):
        meta = e.meta
        st = provenance_status(meta, current_hash(meta, resolve), since_ancestor)
        row = {'plan': e.plan, 'seq': e.seq, 'schema': e.schema, 'builder': meta.get('builder_name'),
               'builder_sha': meta.get('builder_sha'), 'reasons': st['reasons'],
               'registered_builder': meta.get('builder_registration', {}).get('builder')}
        out[st['state']].append(row)
    return out


def main(argv=None) -> int:
    from script_env import add_world_arg, activate_world_from_args
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    add_world_arg(ap)
    ap.add_argument('--since', help='list plans built by a builder older than this commit')
    args = ap.parse_args(argv)
    activate_world_from_args(args)
    from cell_registry import view_registry
    res = audit(view_registry().entries(), git_older_than(args.since) if args.since else None)
    for row in res['stale']:
        print(f'stale: {row["plan"]} ({row["builder"]} {str(row["builder_sha"])[:10]}) {", ".join(row["reasons"])}')
    for row in res['unrecorded']:
        registered = row['registered_builder']
        detail = f'; reapply builder registered: {registered["name"]}, historical provenance unknown' if registered else ''
        print(f'unrecorded: {row["plan"]} (seq {row["seq"]}, schema {row["schema"]}){detail}')
    print(f'{len(res["stale"])} stale, {len(res["current"])} current, {len(res["unrecorded"])} unrecorded')
    return 0


if __name__ == '__main__':
    sys.exit(main())
