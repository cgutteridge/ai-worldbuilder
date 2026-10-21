"""CellRegistry at its interface: MemoryBlocks + a tmp world root (ADR 004). Cell size 16 keeps plans tiny."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from block_store import MemoryBlocks
from cell_journal import (AIR, Build, CellRegistry, Clash, InFlight, JournalError, Mismatch, Note,
                          Refused, Undo, Writes)

STONE, DIRT, GREY, GLASS = 'minecraft:stone', 'minecraft:dirt', 'minecraft:gray_concrete', 'minecraft:glass'
FEATURE_GREY = 'minecraft:light_gray_concrete'


def P(tx: int, tz: int, i: int = 0, y: int = 64) -> tuple[int, int, int]:
    """A position inside cell (tx, tz) for cell size 16."""
    return (16 * tx + i, y, 16 * tz)


def plan(tx: int, tz: int, state: str, n: int = 3, y: int = 64) -> dict:
    return {P(tx, tz, i, y): state for i in range(n)}


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.blocks = MemoryBlocks(default=AIR, strict_flush=True)
        self.applied: list[Writes] = []
        self.reg = self.registry()

    def registry(self, ghosts=None, **kw) -> CellRegistry:
        return CellRegistry(self.root, self.blocks, ghosts, cell_size=16, **kw)

    def apply(self, w: Writes) -> None:
        self.applied.append(w)
        self.blocks.write_many(w.cells)

    def build(self, name, pass_, cells, reg=None, **kw):
        return (reg or self.reg).change(Build(name, pass_, cells, **kw), self.apply)

    def lines(self) -> list[dict]:
        return [json.loads(l) for l in (self.root / 'registry' / 'journal.jsonl').read_text().splitlines()]

    def real_cell(self, tx=0, tz=0):
        """Generated ground, trees and decor: a done cell."""
        self.build(f'ground_{tx}_{tz}', 'ground', plan(tx, tz, STONE, y=60), cell=(tx, tz))
        self.build(f'trees_{tx}_{tz}', 'trees', plan(tx, tz, 'minecraft:oak_log', y=61), cell=(tx, tz))
        self.build(f'decor_{tx}_{tz}', 'decor', plan(tx, tz, 'minecraft:poppy', y=62, n=1), cell=(tx, tz))


class TestCrashSafety(Base):
    def test_begun_is_durable_before_blocks_change(self):
        seen = {}
        real_fsync = os.fsync

        def apply(w):
            seen['lines'] = self.lines()
            seen['fsyncs'] = fsync.call_count
            seen['block'] = self.blocks.read(P(0, 0))
            self.apply(w)

        with mock.patch('cell_journal.os.fsync', wraps=real_fsync) as fsync:
            rec = self.reg.change(Build('house', 'feature', plan(0, 0, STONE)), apply)
        self.assertEqual([l['op'] for l in seen['lines']], ['begin'])
        self.assertGreaterEqual(seen['fsyncs'], 1)
        self.assertNotEqual(seen['block'], STONE)
        self.assertEqual([l['op'] for l in self.lines()], ['begin', 'finish'])
        self.assertEqual(self.lines()[1]['status'], 'landed')
        self.assertEqual(rec.status, 'landed')

    def test_crash_after_begin_leaves_entry_in_flight_for_a_new_process(self):
        def boom(_w):
            raise RuntimeError('mcp died')

        with self.assertRaises(RuntimeError):
            self.reg.change(Build('house', 'feature', plan(0, 0, STONE)), boom)
        fresh = self.registry()
        v = fresh.cell((0, 0))
        self.assertEqual([f.plan for f in v.in_flight], ['house'])
        self.assertFalse(v.done)
        with self.assertRaises(InFlight):
            fresh.change(Build('other', 'feature', plan(0, 0, DIRT)), self.apply)

    def test_torn_last_line_is_ignored_and_healed_on_next_write(self):
        self.build('house', 'feature', plan(0, 0, STONE))
        before = self.reg.view()
        with open(self.root / 'registry' / 'journal.jsonl', 'ab') as fh:
            fh.write(b'{"op":"begin","seq":3,"kind":"build","pl')
        fresh = self.registry()
        self.assertEqual(fresh.view(), before)
        self.build('house2', 'feature', plan(1, 0, STONE), reg=fresh)
        self.assertEqual([l['seq'] for l in self.lines()], [1, 2, 3, 4])
        self.assertEqual(self.registry().cell((1, 0)).features[0].id, 'house2')

    def test_corrupt_middle_line_is_loud(self):
        self.build('a', 'feature', plan(0, 0, STONE))
        path = self.root / 'registry' / 'journal.jsonl'
        first, rest = path.read_bytes().split(b'\n', 1)
        path.write_bytes(first + b'\nnot json\n' + rest)
        with self.assertRaises(JournalError):
            self.registry().view()

    def test_journal_write_failure_means_no_blocks_and_no_line(self):
        with mock.patch('cell_journal.os.fsync', side_effect=OSError('disk full')):
            with self.assertRaises(JournalError):
                self.reg.change(Build('house', 'feature', plan(0, 0, STONE)), self.apply)
        self.assertEqual(self.applied, [])
        self.assertEqual(self.blocks.read(P(0, 0)), AIR)
        self.assertEqual(self.registry().view(), {})

    def test_seq_gap_is_loud(self):
        self.build('a', 'feature', plan(0, 0, STONE))
        path = self.root / 'registry' / 'journal.jsonl'
        first, second, _ = path.read_text().split('\n')
        path.write_text(first + '\n' + second.replace('"seq":2', '"seq":5') + '\n')
        with self.assertRaises(JournalError):
            self.registry().view()


class TestViews(Base):
    def test_layers_fold_into_a_done_cell(self):
        self.real_cell()
        v = self.reg.cell((0, 0))
        self.assertEqual((v.ground, v.trees, v.decor, v.done, v.left), ('generated', True, True, True, ()))

    def test_views_never_disagree(self):
        self.real_cell(0, 0)
        self.build('house', 'feature', {**plan(0, 1, STONE), **plan(1, 1, STONE)})
        self.reg.change(Note((1, 1), 'hold', 'wait for survey'))
        self.reg.change(Note((0, 0), 'marker', 'up'))
        whole = self.reg.view()
        for cell, v in whole.items():
            self.assertEqual(self.reg.view([cell])[cell], v)
            self.assertEqual(self.reg.cell(cell), v)
            self.assertEqual(self.registry().cell(cell), v)           # cold rebuild from the file
            self.assertEqual(v.done, not v.left)
        self.assertEqual(sorted(whole), [(0, 0), (0, 1), (1, 1)])

    def test_two_registries_on_one_journal_stay_in_step(self):
        other = self.registry()
        self.build('house', 'feature', plan(0, 0, STONE))
        self.assertEqual(other.cell((0, 0)).features[0].status, 'built')
        self.build('barn', 'feature', plan(1, 0, STONE), reg=other)
        self.assertEqual(self.reg.cell((1, 0)).features[0].id, 'barn')

    def test_view_of_unknown_cell_is_an_empty_default(self):
        v = self.reg.cell((9, 9))
        self.assertEqual((v.ground, v.trees, v.decor, v.features, v.in_flight, v.marker, v.hold, v.done),
                         ('none', False, False, (), (), False, None, False))
        self.assertEqual(v.left, ('ground=none',))

    def test_views_are_frozen(self):
        v = self.reg.cell((0, 0))
        with self.assertRaises(Exception):
            v.trees = True

    def test_feature_lifecycle_in_progress_then_built_and_reopen_clears_trees_decor(self):
        self.real_cell()
        self.build('house', 'feature', plan(0, 0, GLASS, y=70))
        self.assertTrue(self.reg.cell((0, 0)).done)
        # reopen: a begun apply (crash) leaves the feature in progress and clears trees + decor
        with self.assertRaises(RuntimeError):
            self.reg.change(Build('house', 'feature', plan(0, 0, GLASS, y=71)), lambda w: _raise())
        v = self.reg.cell((0, 0))
        self.assertEqual([(f.id, f.status) for f in v.features], [('house', 'in-progress')])
        self.assertFalse(v.trees or v.decor or v.done)
        self.assertIn('feature house=in-progress', v.left)

    def test_ground_rebuild_clears_trees_and_decor(self):
        self.real_cell()
        self.build('ground_0_0', 'ground', plan(0, 0, DIRT, y=60), cell=(0, 0))
        v = self.reg.cell((0, 0))
        self.assertEqual((v.ground, v.trees, v.decor), ('generated', False, False))
        self.assertEqual(v.left, ('trees', 'decor'))

    def test_non_strict_mismatch_is_journalled_and_blocks_flags(self):
        def drop_one(w):
            self.blocks.write_many({p: s for p, s in list(w.cells.items())[1:]})

        rec = self.reg.change(Build('ground_0_0', 'ground', plan(0, 0, STONE), cell=(0, 0)), drop_one)
        self.assertEqual((rec.status, rec.mismatches), ('mismatched', 1))
        self.assertEqual(self.reg.cell((0, 0)).ground, 'none')
        rec = self.reg.change(Build('house', 'feature', plan(1, 0, STONE)), drop_one)
        self.assertEqual(rec.status, 'mismatched')
        self.assertEqual(self.reg.cell((1, 0)).features[0].status, 'in-progress')

    def test_notes_marker_hold_coverage_and_issues(self):
        r = self.reg
        r.change(Note((0, 0), 'marker', 'up'))
        r.change(Note((1, 0), 'marker', 'up'))                      # the single marker moves
        self.assertEqual(r.marker_cell(), (1, 0))
        self.assertFalse(r.cell((0, 0)).marker)
        r.change(Note((1, 0), 'hold', 'owner asked'))
        r.change(Note((1, 0), 'coverage', 'Q1', ref='shops'))
        r.change(Note((1, 0), 'issue', 'roof wrong', ref='I-1'))
        v = r.cell((1, 0))
        self.assertEqual((v.marker, v.hold), (True, 'owner asked'))
        self.assertEqual([(n.kind, n.ref) for n in v.notes], [('coverage', 'shops'), ('issue', 'I-1')])
        self.assertIn('coverage warning shops', v.left)
        with self.assertRaises(Refused):
            r.change(Note((1, 0), 'coverage-dismiss', '  ', ref='shops'))
        with self.assertRaises(Refused):
            r.change(Note((1, 0), 'coverage-dismiss', 'ok', ref='nope'))
        r.change(Note((1, 0), 'coverage-dismiss', 'checked on site', ref='shops'))
        r.change(Note((1, 0), 'close', ref='I-1'))
        r.change(Note((1, 0), 'hold', ''))
        r.change(Note((1, 0), 'marker', 'none'))
        v = r.cell((1, 0))
        self.assertEqual((v.notes, v.hold, v.marker, r.marker_cell()), ((), None, False, None))


def _raise():
    raise RuntimeError('crash')


class TestOwnership(Base):
    def test_later_build_wins_and_air_cancels(self):
        self.build('a', 'feature', {P(0, 0, 0): STONE, P(0, 0, 1): STONE})
        self.build('b', 'feature', {P(0, 0, 0): DIRT, P(0, 0, 1): AIR}, modifies=('a',))
        got = self.reg.owned([P(0, 0, 0), P(0, 0, 1)])
        self.assertEqual(got, {P(0, 0, 0): ('b', DIRT)})
        self.assertEqual(self.reg.owned([P(0, 0, 0)], exclude=['b']), {P(0, 0, 0): ('a', STONE)})

    def test_clash_with_an_unlisted_earlier_owner(self):
        self.build('a', 'feature', {P(0, 0): STONE})
        with self.assertRaises(Clash) as ctx:
            self.build('b', 'feature', {P(0, 0): DIRT})
        self.assertEqual(ctx.exception.clashes, {P(0, 0): ('a', STONE, DIRT)})
        self.assertEqual(len(self.lines()), 2)                      # nothing journalled for the refusal

    def test_same_state_is_not_a_clash_and_own_plan_may_rebuild(self):
        self.build('a', 'feature', {P(0, 0): STONE})
        self.build('b', 'feature', {P(0, 0): STONE})
        self.build('a', 'feature', {P(0, 0): DIRT}, modifies=('b',))   # a may overwrite itself; b listed

    def test_ghost_owners_yield_and_never_clash(self):
        self.reg.change(Build('ghost_0_0_ground', 'ghost-ground', plan(0, 0, GREY, y=60), cell=(0, 0)), self.apply)
        self.reg.change(Build('ghost_0_0_features', 'ghost-features', {P(0, 0, 0, 61): FEATURE_GREY}), self.apply)
        self.build('house', 'feature', {P(0, 0, 0, 61): GLASS})       # over grey mass, no modifies needed
        self.build('ground_0_0', 'ground', plan(0, 0, STONE, y=60), cell=(0, 0))
        self.assertEqual(self.reg.owned([P(0, 0, 0, 61)]), {P(0, 0, 0, 61): ('house', GLASS)})

    def test_ghost_build_never_overwrites_real_work(self):
        self.build('house', 'feature', {P(0, 0): STONE})
        with self.assertRaises(Clash):
            self.reg.change(Build('ghost_0_0_ground', 'ghost-ground', {P(0, 0): GREY}, cell=(0, 0)), self.apply)

    def test_protect_refuses_before_anything_is_journalled(self):
        self.blocks.write((0, 64, 0), 'minecraft:chest')
        self.blocks.flush()
        with self.assertRaises(Refused) as ctx:
            self.build('a', 'feature', {(0, 64, 0): STONE}, protect=lambda p, s: s == 'minecraft:chest')
        self.assertEqual(ctx.exception.detail, {(0, 64, 0): 'minecraft:chest'})
        self.assertFalse((self.root / 'registry' / 'journal.jsonl').exists())


class TestRules(Base):
    def test_trees_and_decor_need_generated_ground(self):
        for p in ('trees', 'decor'):
            with self.assertRaises(Refused):
                self.build('x', p, plan(0, 0, STONE), cell=(0, 0))
        self.assertEqual(self.applied, [])

    def test_bad_requests_are_refused_loudly(self):
        with self.assertRaises(Refused):
            self.build('x', 'bogus', plan(0, 0, STONE))
        with self.assertRaises(Refused):
            self.build('x', 'feature', {})
        with self.assertRaises(Refused):
            self.reg.change(Build('x', 'feature', plan(0, 0, STONE)))        # blocks change, no apply
        with self.assertRaises(Refused):
            self.reg.change(Note((0, 0), 'nonsense'))
        self.assertFalse((self.root / 'registry' / 'journal.jsonl').exists())

    def test_strict_mismatch_leaves_entry_begun(self):
        def bad(w):
            self.blocks.write_many({**w.cells, P(0, 0, 1): DIRT})

        with self.assertRaises(Mismatch) as ctx:
            self.reg.change(Build('ground_0_0', 'ground', plan(0, 0, STONE), strict=True, cell=(0, 0)), bad)
        self.assertEqual(ctx.exception.mismatches, 1)
        self.assertEqual([l['op'] for l in self.lines()], ['begin'])
        self.assertEqual(self.reg.cell((0, 0)).ground, 'none')
        self.assertEqual(len(self.reg.cell((0, 0)).in_flight), 1)

    def test_mask_makes_mismatch_strict(self):
        with self.assertRaises(Mismatch):
            self.reg.change(Build('t', 'feature', plan(0, 0, STONE), mask={'cell': [0, 0]}), lambda w: None)
        self.assertEqual([l['op'] for l in self.lines()], ['begin'])
        self.assertTrue(self.lines()[0]['mask'])

    def test_in_flight_blocks_new_work_but_only_in_its_cells(self):
        with self.assertRaises(RuntimeError):
            self.reg.change(Build('a', 'feature', plan(0, 0, STONE)), lambda w: _raise())
        with self.assertRaises(InFlight) as ctx:
            self.build('b', 'feature', plan(0, 0, DIRT))
        self.assertEqual(ctx.exception.cells, ((0, 0),))
        self.assertEqual(ctx.exception.seqs, (1,))
        self.build('c', 'feature', plan(5, 5, DIRT))                  # elsewhere is fine

    def test_ticket_api_finish_once_and_blocks_after_begin(self):
        t = self.reg.begin(Build('a', 'feature', plan(0, 0, STONE)))
        self.assertEqual(t.writes.cells, plan(0, 0, STONE))
        self.blocks.write_many(t.writes.cells)
        self.blocks.flush()
        rec = t.finish(self.blocks.read_many(t.writes.cells))
        self.assertEqual(rec.status, 'landed')
        with self.assertRaises(Refused):
            t.finish({})

    def test_reset_starts_a_fresh_journal(self):
        self.build('a', 'feature', plan(0, 0, STONE))
        archived = self.reg.reset()
        self.assertTrue(archived.exists())
        self.assertEqual(self.reg.view(), {})
        self.build('b', 'feature', plan(0, 0, DIRT))                  # owner 'a' is forgotten: no clash
        self.assertEqual([l['seq'] for l in self.lines()], [1, 2])


class TestUndo(Base):
    def test_undo_feature_erases_blocks_voids_state_and_reveals_earlier_owner(self):
        self.build('base', 'feature', {P(0, 0): STONE})
        self.build('house', 'feature', {P(0, 0): GLASS, P(0, 0, 1): GLASS}, modifies=('base',))
        rec = self.reg.change(Undo('house'), self.apply)
        self.assertEqual(rec.status, 'landed')
        self.assertEqual(self.blocks.read(P(0, 0)), STONE)             # the earlier owner is put back
        self.assertEqual(self.blocks.read(P(0, 0, 1)), AIR)
        self.assertEqual(self.reg.owned([P(0, 0), P(0, 0, 1)]), {P(0, 0): ('base', STONE)})
        self.assertEqual([f.id for f in self.reg.cell((0, 0)).features], ['base'])

    def test_undo_by_cell_and_pass_clears_the_flag(self):
        self.real_cell()
        self.reg.change(Undo(((0, 0), 'trees')), self.apply)
        v = self.reg.cell((0, 0))
        self.assertEqual((v.ground, v.trees, v.decor, v.left), ('generated', False, True, ('trees',)))
        self.assertEqual(self.blocks.read(P(0, 0, 0, 61)), AIR)

    def test_undo_real_ground_brings_back_ghost_ground(self):
        self.reg.change(Build('ghost_0_0_ground', 'ghost-ground', plan(0, 0, GREY, y=60), cell=(0, 0)), self.apply)
        self.build('ground_0_0', 'ground', plan(0, 0, STONE, y=60), cell=(0, 0))
        self.assertEqual(self.reg.cell((0, 0)).ground, 'generated')
        self.reg.change(Undo('ground_0_0'), self.apply)
        self.assertEqual(self.reg.cell((0, 0)).ground, 'ghost')
        self.assertEqual(self.blocks.read(P(0, 0, 0, 60)), GREY)

    def test_undo_leaves_blocks_of_a_later_owner_alone(self):
        self.build('a', 'feature', {P(0, 0): STONE, P(0, 0, 1): STONE})
        self.build('b', 'feature', {P(0, 0): GLASS}, modifies=('a',))
        self.reg.change(Undo('a'), self.apply)
        self.assertEqual(self.blocks.read(P(0, 0)), GLASS)
        self.assertEqual(self.blocks.read(P(0, 0, 1)), AIR)

    def test_undo_nothing_is_refused_and_strict_failure_stays_begun(self):
        with self.assertRaises(Refused):
            self.reg.change(Undo('missing'), self.apply)
        self.build('a', 'feature', {P(0, 0): STONE})
        with self.assertRaises(Mismatch):
            self.reg.change(Undo('a'), lambda w: None)                # blocks not erased
        self.assertEqual(len(self.reg.cell((0, 0)).in_flight), 1)
        self.assertEqual(self.reg.cell((0, 0)).features[0].id, 'a')   # still counted until the undo lands


class TestGhosts(Base):
    def planner(self, cell, layer):
        tx, tz = cell
        if layer == 'ground':
            return plan(tx, tz, GREY, y=60)
        return {P(tx, tz, 0, 61): FEATURE_GREY, P(tx, tz, 1, 61): FEATURE_GREY}

    def test_ghost_features_need_ghost_or_real_ground_and_ghost_ground_not_over_real(self):
        with self.assertRaises(Refused):
            self.reg.change(Build('ghost_0_0_features', 'ghost-features', {P(0, 0, 0, 61): FEATURE_GREY}), self.apply)
        self.build('ground_0_0', 'ground', plan(0, 0, STONE, y=60), cell=(0, 0))
        with self.assertRaises(Refused):
            self.reg.change(Build('ghost_0_0_ground', 'ghost-ground', plan(0, 0, GREY, y=60), cell=(0, 0)), self.apply)
        self.reg.change(Build('ghost_0_0_features', 'ghost-features', {P(0, 0, 0, 61): FEATURE_GREY}), self.apply)

    def test_ghost_ground_then_features_then_real_ground_supersedes_ghost_ground(self):
        self.reg.change(Build('ghost_0_0_ground', 'ghost-ground', plan(0, 0, GREY, y=60), cell=(0, 0)), self.apply)
        self.assertEqual(self.reg.cell((0, 0)).ground, 'ghost')
        self.reg.change(Build('ghost_0_0_features', 'ghost-features', {P(0, 0, 0, 61): FEATURE_GREY}), self.apply)
        v = self.reg.cell((0, 0))
        self.assertEqual((v.ground, v.ghost.ground, v.ghost.features), ('ghost+features', True, True))
        self.build('ground_0_0', 'ground', {P(0, 0, 0, 60): STONE}, cell=(0, 0))
        v = self.reg.cell((0, 0))
        self.assertEqual((v.ground, v.ghost.ground, v.ghost.features), ('generated', False, True))
        self.assertEqual(self.reg.owned([P(0, 0, 1, 60)]), {})        # superseded ghost ground is no longer owned

    def test_real_feature_strips_overlapping_ghost_features(self):
        self.reg.change(Build('ghost_0_0_ground', 'ghost-ground', plan(0, 0, GREY, y=60), cell=(0, 0)), self.apply)
        ghost = {P(0, 0, i, y): FEATURE_GREY for i in range(3) for y in (61, 62)}
        self.reg.change(Build('ghost_0_0_features', 'ghost-features', ghost), self.apply)
        rec = self.build('house', 'feature', {P(0, 0, 0, 61): GLASS, P(0, 0, 0, 62): GLASS})
        self.assertFalse(self.reg.cell((0, 0)).ghost.features)       # logically stripped at once
        self.assertEqual(rec.follow_ups, ())                          # the house overwrote every ghost block in its columns
        self.assertEqual(self.reg.owned([P(0, 0, 1, 61)])[P(0, 0, 1, 61)], ('ghost_0_0_features', FEATURE_GREY))

    def test_strip_follow_up_covers_leftover_ghost_in_footprint_columns(self):
        self.reg.change(Build('ghost_0_0_ground', 'ghost-ground', plan(0, 0, GREY, y=60), cell=(0, 0)), self.apply)
        ghost = {P(0, 0, 0, y): FEATURE_GREY for y in (61, 62, 63)}
        self.reg.change(Build('ghost_0_0_features', 'ghost-features', ghost), self.apply)
        rec = self.build('house', 'feature', {P(0, 0, 0, 61): GLASS, P(0, 0, 0, 63): GLASS})
        self.assertEqual([(b.plan, set(b.cells)) for b in rec.follow_ups],
                         [('ghost_strip_0_0_features', {P(0, 0, 0, 62)})])
        drift = self.reg.reconcile()
        self.assertEqual([i.kind for i in drift], ['ghost-stale'])
        self.reg.ensure(self.apply)
        self.assertEqual(self.blocks.read(P(0, 0, 0, 62)), AIR)
        self.assertEqual(self.reg.follow_ups(), ())
        self.assertEqual(len(self.reg.reconcile()), 0)

    def test_frontier_is_ring_two_around_cells_with_real_work(self):
        self.assertEqual(self.reg.frontier(), frozenset())
        self.build('ground_3_3', 'ground', plan(3, 3, STONE, y=60), cell=(3, 3))
        ring = self.reg.frontier()
        self.assertEqual(self.reg.seeds(), [(3, 3)])
        self.assertEqual(len(ring), 25)
        self.assertIn((1, 1), ring)
        self.assertIn((5, 5), ring)
        self.assertNotIn((6, 3), ring)
        self.assertEqual(len(self.reg.frontier(ring=1)), 9)
        self.assertEqual(self.reg.follow_ups(), ())                   # no planner: nothing to ensure

    def test_ensure_plants_ghost_ground_and_features_across_the_frontier(self):
        reg = self.registry(ghosts=self.planner)
        rec = reg.change(Build('ground_0_0', 'ground', plan(0, 0, STONE, y=60), cell=(0, 0)), self.apply)
        self.assertEqual({b.plan for b in rec.follow_ups}, {f'ghost_{x}_{z if z >= 0 else "m" + str(-z)}_ground'
                                                            for x in range(-2, 3) for z in range(-2, 3)
                                                            if (x, z) != (0, 0)} | {'ghost_0_0_features'})
        done = reg.ensure(self.apply)
        self.assertEqual({r.status for r in done}, {'landed'})
        self.assertEqual(reg.cell((2, 2)).ground, 'ghost+features')
        self.assertEqual(reg.cell((0, 0)).ground, 'generated')
        self.assertTrue(reg.cell((0, 0)).ghost.features)
        self.assertEqual(reg.ensure(self.apply), [])                  # idempotent
        self.assertEqual(self.blocks.read(P(-2, -2, 0, 60)), GREY)
        # a real feature in a cell stops ghost features being ensured there
        reg.change(Build('house', 'feature', {P(1, 1, 0, 70): STONE}), self.apply)
        self.assertNotIn((1, 1), [c for c, _ in reg._ghost_needs()])

    def test_change_cascade_applies_follow_ups_and_ghost_never_covers_real_blocks(self):
        reg = self.registry(ghosts=self.planner)
        rec = reg.change(Build('ground_0_0', 'ground', plan(0, 0, STONE, y=60), cell=(0, 0)), self.apply,
                         cascade=True)
        self.assertGreater(len(rec.followed), 24)
        self.assertEqual(reg.follow_ups(), ())
        self.assertEqual(reg.cell((-1, 0)).ground, 'ghost+features')

    def test_empty_planner_result_is_a_landed_no_op_and_none_is_unfixable(self):
        reg = self.registry(ghosts=lambda c, l: {} if c == (0, 0) else None, ring=0)
        self.build('ground_0_0', 'ground', plan(0, 0, STONE, y=60), cell=(0, 0), reg=reg)
        reg.ensure(self.apply)
        self.assertTrue(reg.cell((0, 0)).ghost.features)           # an empty plan still lands: nothing to place
        self.assertEqual(reg.follow_ups(), ())


class TestReconcile(Base):
    def landed(self):
        self.build('house', 'feature', {P(0, 0, 0): STONE, P(0, 0, 1): STONE, P(0, 0, 2): STONE})

    def test_clean_world_has_no_drift(self):
        self.landed()
        self.assertEqual(len(self.reg.reconcile()), 0)
        self.assertTrue(self.reg.reconcile().clean)

    def test_detects_missing_and_foreign(self):
        self.landed()
        self.blocks.write(P(0, 0, 0), AIR)
        self.blocks.write(P(0, 0, 1), DIRT)
        drift = self.reg.reconcile()
        self.assertEqual({i.kind: i.count for i in drift}, {'missing': 1, 'foreign': 1})
        self.assertEqual(drift.by_kind('missing')[0].plans, ('house',))
        self.assertEqual(drift.by_kind('foreign')[0].sample, {'1,64,0': [STONE, DIRT]})
        self.assertFalse(drift.clean)
        self.assertEqual(self.applied[-1].plan, 'house')              # report only: no re-write happened
        self.assertEqual(len(self.applied), 1)

    def test_scope_limits_the_findings(self):
        self.landed()
        self.blocks.write(P(0, 0, 0), AIR)
        self.assertEqual(len(self.reg.reconcile([(7, 7)])), 0)
        self.assertEqual(len(self.reg.reconcile([(0, 0)])), 1)

    def test_fix_recommits_recorded_plan_from_current_owner(self):
        self.landed()
        self.build('patch', 'feature', {P(0, 0, 2): GLASS}, modifies=('house',))
        self.blocks.write(P(0, 0, 0), AIR)
        self.blocks.write(P(0, 0, 2), DIRT)
        drift = self.reg.reconcile(fix=True, apply=self.apply)
        self.assertTrue(drift.clean)
        self.assertEqual(self.blocks.read(P(0, 0, 0)), STONE)
        self.assertEqual(self.blocks.read(P(0, 0, 2)), GLASS)          # the later owner's state, not house's
        self.assertEqual(len(self.reg.reconcile()), 0)
        self.assertIn('repair', [l['op'] for l in self.lines()])

    def test_fix_needs_an_apply_callable(self):
        with self.assertRaises(Refused):
            self.reg.reconcile(fix=True)

    def test_in_flight_entry_with_blocks_already_written_lands_without_rewriting(self):
        t = self.reg.begin(Build('house', 'feature', plan(0, 0, STONE)))
        self.blocks.write_many(t.writes.cells)                         # crash after the fills, before finish
        before = len(self.applied)
        drift = self.reg.reconcile(fix=True, apply=self.apply)
        self.assertEqual([(i.kind, i.fixed) for i in drift], [('in-flight', True)])
        self.assertEqual(len(self.applied), before)
        self.assertEqual(self.reg.cell((0, 0)).features[0].status, 'built')
        self.assertEqual(self.reg.cell((0, 0)).in_flight, ())

    def test_in_flight_entry_with_blocks_missing_is_recommitted(self):
        with self.assertRaises(RuntimeError):
            self.reg.change(Build('house', 'feature', plan(0, 0, STONE)), lambda w: _raise())
        self.assertEqual(self.reg.reconcile().by_kind('in-flight')[0].count, 3)
        self.assertEqual(self.blocks.read(P(0, 0)), AIR)
        drift = self.reg.reconcile(fix=True, apply=self.apply)
        self.assertTrue(drift.clean)
        self.assertEqual(self.blocks.read(P(0, 0)), STONE)
        self.build('next', 'feature', plan(0, 0, GLASS, y=70))         # the cell accepts work again

    def test_strict_mismatch_entry_is_fixed_by_reconcile(self):
        with self.assertRaises(Mismatch):
            self.reg.change(Build('ground_0_0', 'ground', plan(0, 0, STONE), strict=True, cell=(0, 0)),
                            lambda w: self.blocks.write(P(0, 0), STONE))
        drift = self.reg.reconcile(fix=True, apply=self.apply)
        self.assertTrue(drift.clean)
        self.assertEqual(self.reg.cell((0, 0)).ground, 'generated')

    def test_mismatched_entry_is_upgraded_once_blocks_read_back(self):
        rec = self.reg.change(Build('house', 'feature', plan(0, 0, STONE)), lambda w: None)
        self.assertEqual(rec.status, 'mismatched')
        drift = self.reg.reconcile(fix=True, apply=self.apply)
        self.assertEqual(drift.upgraded, 1)
        self.assertEqual(self.reg.cell((0, 0)).features[0].status, 'built')

    def test_ghost_missing_reported_with_a_planner_and_fixed(self):
        reg = self.registry(ghosts=lambda c, l: plan(*c, GREY, y=60) if l == 'ground' else {}, ring=1)
        self.build('ground_0_0', 'ground', plan(0, 0, STONE, y=60), cell=(0, 0), reg=reg)
        drift = reg.reconcile()
        self.assertEqual(len(drift.by_kind('ghost-missing')), 9)       # 8 ring cells lack ground + the seed lacks features
        fixed = reg.reconcile(fix=True, apply=self.apply)
        self.assertTrue(fixed.clean)
        self.assertEqual(len(reg.reconcile()), 0)
        self.assertEqual(len(self.registry(ring=1).reconcile()), 0)    # without a planner nothing is claimed missing


if __name__ == '__main__':
    unittest.main()
