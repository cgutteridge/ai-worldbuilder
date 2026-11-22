#!/usr/bin/env python3
"""Restore a site_kit apply backup structure into the live world.

Uses the structure recorded with the first cell journal entry of that build (site_kit.apply).
Restoring blocks bypasses the journal: follow it with a journal Undo or a re-apply.

  python3 scripts/restore_structure_backup.py cell_48_m44_foliage
  SITE_OWNER=you python3 scripts/restore_structure_backup.py cell_48_m44_foliage --apply
"""
from __future__ import annotations

import argparse

from script_env import ROOT  # noqa: E402

from mc_paced import Paced, box, D, xyz  # noqa: E402


def load_record(build: str) -> dict:
    """First-apply structure backup of a build, from its first cell journal entry.

    @param build: kit plan name
    @returns: {'structure', 'from', 'to'}
    """
    from cell_registry import view_registry
    for e in view_registry().entries(plan=build):
        st = e.meta.get('structure')
        if st:
            return {'structure': st['name'], 'from': st['from'], 'to': st['to']}
    raise SystemExit(f'no structure backup recorded for {build!r} in the cell journal')


def load_structure(name: str, origin, m=None) -> str:
    """Load a saved structure back into the world at ``origin`` and save the world.

    The caller holds the lock. Raises if the server reports a failure.

    @param name: structure id, e.g. worldbuilder:before_<plan>
    @param origin: [x, y, z] the structure was saved from
    @param m: optional Paced-like client (tests)
    @returns: the server's reply text
    """
    if m is None:
        m = Paced()
    res = m.text('structure_load_to_world', {'name': name, 'dimension': D, 'origin': xyz(origin),
                                             'rotation': 'none', 'mirror': 'none'})
    if any(w in res.lower() for w in ('error', 'fail', 'not found', 'unknown', 'no such')):
        raise SystemExit(f'structure load of {name} failed: {res}')
    m.call('server_save_all_worlds', {})
    return res


def restore(build: str, *, apply: bool) -> None:
    """Load ``before_<build>`` structure at the saved box origin.

    @param build: kit plan name
    @param apply: when False, print intent only
    """
    rec = load_record(build)
    name = rec['structure']
    origin = rec['from']
    lo, hi = rec['from'], rec['to']
    print(f'restore {name} at {origin} box {lo}..{hi}')
    if not apply:
        print('dry-run: pass --apply with SITE_OWNER lock held')
        return
    import site_lock

    site_lock.check()
    print(load_structure(name, origin))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('build', help='plan name, e.g. cell_48_m44_foliage')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    restore(args.build, apply=args.apply)
