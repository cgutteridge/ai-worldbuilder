#!/usr/bin/env python3
"""Recover aborted live runs: one mechanism for every pass (rules/process.md, lock / apply protocol).

The cell journal (``registry/journal.jsonl``, ADR 004) is the only record. site_kit.apply()
journals each live apply ``begun`` (plan, owner, structure backup in its meta) before the first
block write and finishes it after the readback. A run that dies in between leaves the entry
``begun`` (in flight). This tool resolves it.

Split with ``python3 scripts/cell_registry.py reconcile`` (``CellRegistry.reconcile``):
  site_recover = resolve ONE interrupted apply (land it, or roll the world back to the structure saved before it);
  reconcile    = detect and repair drift forward (re-commit recorded plans; never restores an older state).
Rolling back to the pre-apply structure is not forward repair, so the two stay separate tools. The
land-or-not decision here reads the world through ``reconcile`` (its in-flight finding), not a second readback.

  python3 scripts/site_recover.py status                  # read-only: stale lock, in-flight, rolled-back entries
  python3 scripts/site_recover.py recover 46,-43          # dry run: what it would do
  python3 scripts/site_recover.py recover 46,-43 --apply  # do it

Decision, per in-flight entry touching the cell (conservative: when unsure it reports and does nothing):
  * lock held and fresh (heartbeat < site_lock.STALE_MIN) -> refuse; the run may still be alive.
  * lock stale but owned by someone other than the entry's owner -> refuse (unsure).
  * flush the world, compare the recorded plan via BlockStore (MCP):
      (``reconcile`` in-flight finding on the cell: planned blocks vs the world)
      all planned blocks match -> entry finished 'landed' (recovered). A staged pass mask is
      published; an aborted mask undo also gets its bookkeeping finished (mask_undo.finish_undo).
      otherwise -> restore the structure saved for THIS apply (restore_structure_backup), entry
      'abandoned' (rolled back: it owns nothing, so the work reappears). Trees/decor re-applied after
      a journal undo: the structure taken before that undo is restored too (and the archived mask
      put back), so the cell is not left bare.
  * the stale lock is released only after a successful recovery and only when its owner is the
    owner recorded on the entry.
Sign text / entities are journalled with the plan (#52) and re-written when reconcile --fix
re-commits a full plan; readback still only checks blocks. A rollback is not compared against
any earlier world state (region snapshots were removed, ADR 004).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import script_env  # noqa: E402,F401

import site_lock  # noqa: E402
from atomic_json import write_json  # noqa: E402
from block_store import BlockStoreError  # noqa: E402
from cell_journal import JournalError  # noqa: E402

OWNER = 'site-recover'


def _registry():
    import cell_registry
    return cell_registry.view_registry()


class _SavedWorld:
    """BlockStore over an injected factory: ``flush`` saves, then opens a fresh store to read.

    ``world_factory`` may return a :class:`block_store.BlockStore` (``read`` / ``read_many``)
    or a duck-typed ``World`` with ``block(x, y, z)``.
    """

    def __init__(self, flush, world_factory):
        self._flush, self._factory, self.world = flush, world_factory, None

    def flush(self):
        self._flush()
        self.world = self._factory()

    def read(self, pos):
        w = self.world
        if hasattr(w, 'read'):
            return w.read(pos)
        return w.block(*pos)

    def read_many(self, positions):
        w = self.world
        if hasattr(w, 'read_many'):
            return w.read_many(positions)
        return {tuple(p): w.block(*p) for p in positions}


def lock_state() -> dict:
    lk = site_lock.read()
    if not lk:
        return {'state': 'none', 'lock': None}
    age = (time.time() - lk['heartbeat']) / 60
    return {'state': 'fresh' if site_lock.fresh(lk) else 'stale', 'lock': lk, 'age_min': round(age)}


def status() -> dict:
    """Read-only: stale lock, in-flight ('begun') and rolled-back ('abandoned') journal entries."""
    reg = _registry()
    inflight, rolled = [], []
    entries = reg.entries()
    live = {e.plan for e in entries if not e.undone}
    for e in entries:
        if e.status == 'begun':
            inflight.append({'cell': e.cell, 'stage': e.pass_, 'plan': e.plan, 'owner': e.owner,
                             'started': e.ts, 'seq': e.seq})
        elif e.status == 'abandoned' and e.plan not in live:
            rolled.append({'cell': e.cell, 'stage': e.pass_, 'plan': e.plan, 'seq': e.seq})
    return {'lock': lock_state(), 'in_flight': inflight, 'rolled_back': rolled}


def _flush():
    from mc_paced import Paced
    Paced().call('server_save_all_worlds', {})
    time.sleep(3)


def _undo_before(reg, e):
    """The landed undo of this trees/decor pass that the in-flight re-apply follows, or None."""
    if e.kind != 'build' or e.pass_ not in ('trees', 'decor'):
        return None
    last = None
    for x in reg.entries(cells=[e.cell]):
        if x.seq >= e.seq or x.pass_ != e.pass_ or x.cell != e.cell or x.status != 'landed' or x.undone:
            continue
        last = x
    return last if last is not None and last.kind == 'undo' else None


def recover(cell, apply: bool = False, flush=None, world_factory=None, loader=None) -> dict:
    """Resolve the in-flight journal entry of one cell. Returns {'cell', 'action', 'detail'}.

    action: 'none' | 'applied' | 'rolled-back' | 'unsure' | 'failed' (prefixed 'would-' unless apply).
    flush / world_factory / loader are injectable for tests.
    """
    import cell_registry
    import site_kit
    flush = flush or _flush
    world_factory = world_factory or site_kit.live_blocks
    cell = tuple(cell)
    store = _SavedWorld(flush, world_factory)
    reg = cell_registry.open_registry(blocks=store, ghosts=None)
    flying = [e for e in reg.entries(cells=[cell]) if e.status == 'begun']
    res = {'cell': list(cell), 'action': 'none', 'detail': []}
    say = res['detail'].append
    if not flying:
        say('no journal entry is in flight')
        return res
    if len(flying) > 1:
        res['action'] = 'unsure'
        say('several entries are in flight (' + ', '.join(f'{e.seq} {e.plan}' for e in flying) + '); resolve by hand')
        return res
    e = flying[0]
    say(f'entry {e.seq} ({e.kind} {e.pass_}): plan {e.plan}, owner {e.owner}, started {e.ts}')
    ls = lock_state()
    if ls['state'] == 'fresh':
        res['action'] = 'unsure'
        say(f'lock held by {ls["lock"]["owner"]} with a fresh heartbeat ({ls["age_min"]} min); the run may be '
            f'alive. Wait for it to go stale (> {site_lock.STALE_MIN} min) or ask the user. Nothing done.')
        return res
    if ls['state'] == 'stale' and (not e.owner or ls['lock']['owner'] != e.owner):
        res['action'] = 'unsure'
        say(f'stale lock belongs to {ls["lock"]["owner"]}, the in-flight entry to {e.owner}: not the same run. Nothing done.')
        return res
    try:
        ticket = reg.ticket(e.seq)
    except JournalError as exc:
        res['action'] = 'unsure'
        say(f'recorded plan of entry {e.seq} is unreadable ({exc}): cannot compare the world. Nothing done.')
        return res
    planned = dict(ticket.writes.cells)
    try:
        drift = reg.reconcile([cell])                     # flushes the world, then compares the recorded plans
    except (JournalError, OSError, BlockStoreError) as exc:
        res['action'] = 'unsure'
        say(f'could not flush the world ({exc}); reading without flush would mislead. Nothing done.')
        return res
    mine = [i for i in drift.by_kind('in-flight') if i.seq == e.seq]
    mismatches = mine[0].count if mine else 0
    say(f'live world vs plan: {len(planned)} planned, {mismatches} mismatches')
    prefix = '' if apply else 'would-'
    original_lock = ls['lock']

    def release_stale():
        if original_lock is not None:
            site_lock.LOCK.unlink(missing_ok=True)
            say('stale lock released (same owner as the in-flight entry)')

    if mismatches == 0:
        res['action'] = prefix + 'applied'
        say("every planned block is in the world: entry -> 'landed' (recovered)")
        if apply:
            if ticket.writes.signs or ticket.writes.entities:
                # blocks match; still re-write journalled signs/entities (crash may have skipped them)
                import site_kit as sk
                sk.McpWriter()(ticket.writes)
                flush()
                say('re-wrote journalled signs/entities')
            pending = e.meta.get('pending_mask')
            if pending and Path(pending).exists():
                import pass_mask
                mask = pass_mask.read_mask(Path(pending))
                if not pass_mask.verify_integrity(mask):
                    res['action'] = 'unsure'
                    say('the staged pass mask fails its integrity check; entry left in flight. Report to the user.')
                    return res
                say(f'pass mask published at {pass_mask.write_mask(mask)}')
            if e.kind == 'undo':
                import mask_undo
                mask_undo.finish_undo(cell, e.pass_, e.meta.get('undo_plan') or e.plan)
                say("mask undo bookkeeping finished (mask archived)")
            ticket.finish(store.read_many(planned))
            release_stale()
        return res

    # --- roll back ---------------------------------------------------------------------------
    steps = []
    if not e.meta.get('structure'):
        res['action'] = 'unsure'
        say('world differs from the plan and no structure backup is recorded for this apply. Nothing done.')
        return res
    steps.append(('apply', e.meta['structure'], list(planned)))
    undo = _undo_before(reg, e)
    if undo is not None:
        if not undo.meta.get('structure'):
            res['action'] = 'unsure'
            say('the pass was undone before this apply but the undo snapshot structure is not recorded. Nothing done.')
            return res
        steps.append(('before-undo', undo.meta['structure'], []))
    say('would restore: ' + ' then '.join(f'{n} {s["name"]}' for n, s, *_ in steps))
    if not apply:
        res['action'] = 'would-rolled-back'
        return res
    original = site_lock.read()
    verified = []
    try:
        site_lock.claim(OWNER, f'recover {cell[0]},{cell[1]} {e.plan}')
        if loader is None:
            import restore_structure_backup
            loader = restore_structure_backup.load_structure
        for name, struct, _positions in steps:
            loader(struct['name'], struct['from'])
            flush()
            verified.append({'step': name, 'structure': struct['name']})
            say(f'restored {name}: {struct["name"]}')
    except (OSError, SystemExit) as exc:
        res['action'] = 'failed'
        say(f'rollback failed: {exc}. Entry stays in flight; original lock restored. Report to the user.')
        if original:
            write_json(site_lock.LOCK, original)
        else:
            site_lock.LOCK.unlink(missing_ok=True)
        return res
    backup = (undo.meta.get('mask_backup') if undo is not None else None)
    if backup and Path(backup).exists():
        from pass_mask import mask_path
        dest = mask_path(e.pass_, cell)
        shutil.copy2(backup, dest)
        say(f'archived {e.pass_} mask put back at {dest}')
    ticket.abandon(json.dumps({'restored': verified,
                               'used_undo_snapshot': undo.meta['structure']['name'] if undo else None}))
    if undo is not None and undo.targets:
        # pre-undo world is back: clear the void so ownership/views match the restored blocks (#53)
        reg.revive(undo.targets, undo_seq=undo.seq)
        say(f'revived {len(undo.targets)} plan(s) voided by undo seq {undo.seq}')
    site_lock.LOCK.unlink(missing_ok=True)
    say('lock released; entry rolled back (abandoned; the work reappears in cell_gaps)')
    res['action'] = 'rolled-back'
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('status', help='read-only')
    r = sub.add_parser('recover', help='resolve one cell')
    r.add_argument('cell', help='tx,tz')
    r.add_argument('--apply', action='store_true', help='LIVE: otherwise a dry run')
    a = ap.parse_args(argv)
    if a.cmd == 'status':
        st = status()
        lk = st['lock']
        print('lock:', 'none' if lk['state'] == 'none' else
              f'{lk["state"]} (owner {lk["lock"]["owner"]}, {lk["age_min"]} min since heartbeat, task: {lk["lock"].get("task")})')
        for f in st['in_flight']:
            print(f'in-flight: cell {f["cell"][0]},{f["cell"][1]} stage {f["stage"]} plan {f["plan"]} owner {f["owner"]} since {f["started"]}')
        for f in st['rolled_back']:
            print(f'rolled-back (needs re-run): cell {f["cell"][0]},{f["cell"][1]} stage {f["stage"]} plan {f["plan"]}')
        if not (st['in_flight'] or st['rolled_back'] or lk['state'] == 'stale'):
            print('clean')
        return 1 if (st['in_flight'] or lk['state'] == 'stale') else 0
    res = recover(tuple(int(v) for v in a.cell.split(',')), apply=a.apply)
    print(f'{res["cell"][0]},{res["cell"][1]}: {res["action"]}')
    for line in res['detail']:
        print('  ' + line)
    return 1 if res['action'] in ('unsure', 'failed') else 0


if __name__ == '__main__':
    sys.exit(main())
