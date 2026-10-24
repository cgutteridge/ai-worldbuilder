"""site_kit.apply / undo on the cell journal, site_recover.py over in-flight journal entries, and the
fail-closed mask undo. Everything runs against FakePaced over MemoryBlocks and a tmp world root
(journal_testkit.LiveApplyCase): no Minecraft, no real site/ files, no lock claim, no world write."""
import json
import time
import unittest
from unittest import mock

import mask_undo
import pass_mask as pm
import restore_structure_backup as rsb
import site_kit
import site_lock
import site_recover as sr
from cell_journal import Build
from journal_testkit import AIR, FakePaced, LiveApplyCase, View

CELL = (48, -44)
X0, Z0 = 6150, -5600


def make_plan(name='p'):
    plan = site_kit.Plan(name)
    plan.fill((X0, 60, Z0), (X0 + 1, 60, Z0), 'minecraft:stone')
    plan.fill((X0, 61, Z0), (X0 + 1, 61, Z0), 'minecraft:oak_log')
    return plan


class Base(LiveApplyCase):
    def setUp(self):
        super().setUp()
        self.blocks.write((X0, 60, Z0), 'minecraft:dirt')
        self.blocks.flush()

    def entry(self, plan='p'):
        return self.entries(plan)[-1]

    def view(self, cell=CELL):
        return self.reg().cell(cell)

    def set_lock(self, owner='alice', age_min=0):
        hb = time.time() - age_min * 60
        site_lock.LOCK.write_text(json.dumps({'owner': owner, 'task': 't', 'claimed': hb, 'heartbeat': hb,
                                              'claimed_at': 'x', 'heartbeat_at': 'x'}))

    def ground(self):
        """Generated ground in CELL (trees and decor need it)."""
        g = site_kit.Plan('g')
        g.set(X0 + 5, 59, Z0, 'minecraft:grass_block')
        self.run_apply(g, stage='ground-layer', cell=CELL)


class TestApplyRecordsInTheJournal(Base):
    def test_begun_before_writes_landed_after(self):
        FakePaced.fill_reader = lambda: [e.status for e in self.entries('p')]
        rep = self.run_apply(make_plan())
        self.assertEqual(FakePaced.seen_at_fill, [['begun']] * len(FakePaced.seen_at_fill))
        self.assertTrue(FakePaced.seen_at_fill)
        e = self.entry()
        self.assertEqual((e.status, e.pass_, e.cell, e.owner), ('landed', 'feature', CELL, 'alice'))
        self.assertEqual(rep['mismatches'], 0)
        self.assertEqual(rep['journal_seq'], e.seq)
        self.assertEqual(json.loads((site_kit.OUT / 'p-result.json').read_text())['mismatches'], 0)

    def test_no_retired_records_are_written(self):
        self.run_apply(make_plan())
        names = {p.name for p in site_kit.OUT.iterdir()}
        self.assertEqual(names, {'p-result.json'})                        # no -cells/-planned/-backup json
        self.assertFalse((self.t / 'site' / 'cells').exists())            # no STAGES
        self.assertFalse((self.t / 'registry' / 'cells.json').exists())

    def test_clean_feature_apply_marks_the_feature_built(self):
        self.run_apply(make_plan())
        self.assertEqual([(f.id, f.status) for f in self.view().features], [('p', 'built')])

    def test_clean_ground_apply_sets_ground_generated(self):
        self.run_apply(make_plan(), stage='ground-layer', cell=CELL)
        self.assertEqual(self.view().ground, 'generated')

    def test_strict_mismatch_leaves_begun_and_points_at_site_recover(self):
        FakePaced.skip_fills = True
        with self.assertRaises(SystemExit) as ctx:
            self.run_apply(make_plan(), stage='ground-layer', cell=CELL, strict=True)
        msg = str(ctx.exception)
        self.assertIn('readback has 4 mismatches', msg)
        self.assertIn("left 'begun'", msg)
        self.assertIn('site_recover.py status', msg)
        self.assertEqual(self.entry().status, 'begun')
        self.assertEqual(self.view().ground, 'none')

    def test_entry_records_what_recovery_needs(self):
        FakePaced.abort_on_save = True
        with self.assertRaises(RuntimeError):
            self.run_apply(make_plan())
        e = self.entry()
        self.assertEqual(e.status, 'begun')
        self.assertEqual(e.owner, 'alice')
        self.assertEqual(e.meta['structure']['name'], 'southampton_refine:before_p')
        self.assertEqual(e.meta['structure']['from'], [X0 - 1, 60, Z0 - 1])
        self.assertTrue(e.ts)

    def test_abort_mid_apply_leaves_begun(self):
        FakePaced.fill_limit = 1
        with self.assertRaises(RuntimeError):
            self.run_apply(make_plan())
        self.assertEqual(self.entry().status, 'begun')
        self.assertEqual(self.view().in_flight[0].plan, 'p')

    def test_reapply_takes_a_fresh_structure_backup(self):
        self.run_apply(make_plan())
        self.blocks.write((X0, 60, Z0), 'minecraft:coal_block')
        FakePaced.abort_on_save = True
        with self.assertRaises(RuntimeError):
            self.run_apply(make_plan())
        name = self.entry().meta['structure']['name']
        self.assertNotEqual(name, 'southampton_refine:before_p')          # the first-time one is the original
        self.assertEqual(FakePaced.structures[name][(X0, 60, Z0)], 'minecraft:coal_block')

    def test_mismatch_without_strict_is_recorded(self):
        FakePaced.skip_fills = True
        self.run_apply(make_plan())
        self.assertEqual(self.entry().status, 'mismatched')
        self.assertEqual(self.view().features[0].status, 'in-progress')

    def test_apply_over_an_aborted_entry_is_refused(self):
        FakePaced.abort_on_save = True
        with self.assertRaises(RuntimeError):
            self.run_apply(make_plan())
        FakePaced.abort_on_save = False
        with self.assertRaises(SystemExit) as ctx:
            self.run_apply(make_plan('q'))
        self.assertIn('site_recover.py status', str(ctx.exception))
        # different block states must still be InFlight (not the ownership Clash wording)
        other = site_kit.Plan('bricks')
        for (x, y, z), _st in make_plan().cells.items():
            other.set(x, y, z, 'minecraft:bricks')
        with self.assertRaises(SystemExit) as ctx:
            self.run_apply(other)
        self.assertIn('site_recover.py status', str(ctx.exception))
        self.assertNotIn('belong to earlier builds', str(ctx.exception))

    def test_trees_before_ground_are_refused_loudly(self):
        with self.assertRaises(SystemExit) as ctx:
            self.run_apply(make_plan(), stage='trees', cell=CELL)
        self.assertIn('generated ground', str(ctx.exception))
        self.assertEqual(self.entries(), ())

    def test_clash_report_comes_from_journal_ownership(self):
        self.run_apply(make_plan('house'))
        other = site_kit.Plan('shed')
        other.set(X0, 60, Z0, 'minecraft:bricks')
        with self.assertRaises(SystemExit) as ctx:
            self.run_apply(other, dry=True)
        self.assertIn('1 cells belong to earlier builds', str(ctx.exception))
        clashes = json.loads((site_kit.OUT / 'shed-clashes.json').read_text())
        self.assertEqual(list(clashes.values()), [['house', 'minecraft:stone', 'minecraft:bricks']])
        self.run_apply(other, modifies=('house',))                        # modifies lifts the clash

    def test_signs_and_entities_are_written_and_journalled(self):
        plan = make_plan()
        plan.sign((X0, 62, Z0), ['Hi'])
        plan.entity('minecraft:armor_stand', (X0, 62, Z0))
        self.run_apply(plan)
        self.assertIn('block_entity_set_nbt', FakePaced.calls)
        self.assertIn('entity_summon', FakePaced.calls)
        self.assertEqual(set(self.reg().recorded('p')), set(plan.cells))
        begin = next(l for l in (self.t / 'registry' / 'journal.jsonl').read_text().splitlines()
                     if '"op": "begin"' in l or '"op":"begin"' in l)
        import json as _json
        line = _json.loads(begin)
        self.assertEqual(line['signs'][0][1][0], 'Hi')
        self.assertEqual(line['entities'][0][0], 'minecraft:armor_stand')

    def test_unknown_stage_is_refused(self):
        with self.assertRaises(ValueError):
            self.run_apply(make_plan(), stage='above-ground-strip')

    def test_dry_run_writes_no_journal(self):
        self.run_apply(make_plan(), dry=True)
        self.assertFalse((self.t / 'registry' / 'journal.jsonl').exists())

    def test_landed_layer_closes_its_issue(self):
        import registry_issues as RI
        self.ground()
        issue = RI.add_issue(reason='crowns wrong', cell=CELL, layer='trees', root=self.t)
        self.assertIn('issue I-001', self.view().left)
        plan = site_kit.Plan('t')
        plan.set(X0 + 3, 61, Z0, 'minecraft:oak_log')
        self.run_apply(plan, stage='trees', cell=CELL)
        self.assertEqual(RI.load_issues(self.t), [])
        self.assertNotIn(f'issue {RI.issue_ref(issue)}', self.view().left)


class TestMasks(Base):
    def setUp(self):
        super().setUp()
        self.ground()

    def test_mask_failure_stays_recoverable_and_recovery_publishes_mask(self):
        plan = make_plan()
        mask = pm.build_mask('trees', CELL, plan.name, plan.cells, lambda p: True)
        with mock.patch.object(pm, 'write_mask', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.run_apply(plan, stage='trees', cell=CELL, mask=mask, meta={'crowns': 1})
        e = self.entry()
        self.assertEqual(e.status, 'begun')
        self.assertEqual(e.meta['crowns'], 1)
        writer = pm.write_mask
        with mock.patch.object(pm, 'write_mask', side_effect=lambda m: writer(m, self.t / 'masks')):
            result = sr.recover(CELL, apply=True, flush=lambda: None,
                                world_factory=lambda: View(self.blocks))
        self.assertEqual(result['action'], 'applied')
        self.assertEqual(self.entry().status, 'landed')
        self.assertTrue(self.view().trees)
        self.assertTrue((self.t / 'masks' / 'trees-48_-44.mask.json').exists())

    def test_masked_pass_publishes_the_mask_and_lands(self):
        plan = make_plan()
        placements = {p: b.removeprefix('minecraft:') for p, b in plan.cells.items()}
        mask = pm.build_mask('trees', CELL, plan.name, placements, lambda p: True)
        writer = pm.write_mask
        with mock.patch.object(pm, 'write_mask', side_effect=lambda m: writer(m, self.t / 'masks')):
            self.run_apply(plan, stage='trees', cell=CELL, mask=mask)
        e = self.entry()
        self.assertEqual(e.status, 'landed')
        self.assertEqual(self.reg().entries()[-1].meta['structure']['name'], 'southampton_refine:before_p')
        self.assertTrue((self.t / 'masks' / 'trees-48_-44.mask.json').exists())

    def test_masked_mismatch_does_not_publish_mask_or_finish(self):
        plan = make_plan()
        mask = pm.build_mask('trees', CELL, plan.name, plan.cells, lambda p: True)
        FakePaced.skip_fills = True
        with mock.patch.object(pm, 'write_mask') as publish:
            with self.assertRaises(SystemExit):
                self.run_apply(plan, stage='trees', cell=CELL, mask=mask)
        publish.assert_not_called()
        self.assertEqual(self.entry().status, 'begun')


class Aborted(Base):
    """A trees apply that died after (some of) its writes, plus a lock that has gone stale."""

    def abort(self, **kw):
        FakePaced.fills_done = 0
        for k, v in kw.items():
            setattr(FakePaced, k, v)
        with self.assertRaises(RuntimeError):
            self.run_apply(make_plan(), stage='trees', cell=CELL)
        FakePaced.fill_limit, FakePaced.abort_on_save = None, False

    def recover(self, **kw):
        return sr.recover(CELL, apply=kw.pop('apply', True), flush=lambda: None,
                          world_factory=lambda: View(self.blocks), **kw)

    def loader(self, record=None):
        def load(name, origin):
            if record is not None:
                record.append((name, origin))
            return rsb.load_structure(name, origin, m=FakePaced())
        return load


class TestRecover(Aborted):
    def setUp(self):
        super().setUp()
        self.ground()

    def test_world_matches_plan_lands_the_entry_and_releases_stale_lock(self):
        self.abort(abort_on_save=True)                   # all fills landed, died before the readback
        self.set_lock('alice', age_min=200)
        res = self.recover()
        self.assertEqual(res['action'], 'applied')
        self.assertEqual(self.entry().status, 'landed')
        self.assertTrue(self.view().trees)
        self.assertFalse(site_lock.LOCK.exists())

    def test_world_differs_restores_recorded_structure_and_rolls_back(self):
        before = dict(self.blocks._blocks)
        self.abort(fill_limit=1)                         # first box written, second never
        self.assertNotEqual(dict(self.blocks._blocks), before)
        name = self.entry().meta['structure']['name']
        self.set_lock('alice', age_min=200)
        loaded = []
        res = self.recover(loader=self.loader(loaded))
        self.assertEqual(res['action'], 'rolled-back')
        self.assertEqual(loaded, [(name, [X0 - 1, 60, Z0 - 1])])
        self.assertEqual(dict(self.blocks._blocks), before)
        self.assertEqual(self.entry().status, 'abandoned')
        self.assertEqual(self.reg().owned(make_plan().cells), {})            # rolled back: owns nothing
        self.assertFalse(site_lock.LOCK.exists())
        self.assertEqual([r['plan'] for r in sr.status()['rolled_back']], ['p'])

    def test_rolled_back_is_not_done_and_reappears(self):
        import site_handoff as H
        self.abort(fill_limit=1)
        self.set_lock('alice', age_min=200)
        self.recover(loader=self.loader())
        self.assertIn('trees', H.cell_gaps(CELL, features=False)['layers'])
        self.assertEqual(H.cell_gaps(CELL, features=False)['in_flight'], [])

    def test_in_flight_is_listed_by_cell_gaps(self):
        import site_handoff as H
        self.abort(fill_limit=1)
        self.assertEqual(H.cell_gaps(CELL, features=False)['in_flight'], ['in-flight: p'])

    def test_dry_run_changes_nothing(self):
        self.abort(fill_limit=1)
        self.set_lock('alice', age_min=200)
        res = self.recover(apply=False, loader=lambda n, o: self.fail('must not load'))
        self.assertEqual(res['action'], 'would-rolled-back')
        self.assertEqual(self.entry().status, 'begun')
        self.assertTrue(site_lock.LOCK.exists())

    def test_fresh_lock_is_never_taken_over(self):
        self.abort(fill_limit=1)
        for owner in ('alice', 'bob'):
            self.set_lock(owner, age_min=5)
            before = site_lock.LOCK.read_text()
            res = self.recover(loader=lambda n, o: self.fail('must not load'))
            self.assertEqual(res['action'], 'unsure')
            self.assertEqual(site_lock.LOCK.read_text(), before)
            self.assertEqual(self.entry().status, 'begun')

    def test_stale_lock_of_another_owner_is_not_ours_to_clear(self):
        self.abort(fill_limit=1)
        self.set_lock('bob', age_min=300)
        before = site_lock.LOCK.read_text()
        res = self.recover(loader=lambda n, o: self.fail('must not load'))
        self.assertEqual(res['action'], 'unsure')
        self.assertEqual(site_lock.LOCK.read_text(), before)

    def test_unsure_when_flush_fails_or_plan_missing(self):
        self.abort(fill_limit=1)
        self.set_lock('alice', age_min=200)

        def boom_os():
            raise OSError('no server')
        res = sr.recover(CELL, apply=True, flush=boom_os, loader=lambda n, o: self.fail('must not load'))
        self.assertEqual(res['action'], 'unsure')
        # unexpected errors stay loud (#55); only typed flush failures soft-fail
        with self.assertRaises(RuntimeError):
            sr.recover(CELL, apply=True, flush=lambda: (_ for _ in ()).throw(RuntimeError('bug')),
                       loader=lambda n, o: self.fail('must not load'))
        for blob in (self.t / 'registry' / 'blobs').glob('*.json'):
            blob.unlink()
        res = self.recover(loader=lambda n, o: self.fail('must not load'))
        self.assertEqual(res['action'], 'unsure')
        self.assertEqual(self.entry().status, 'begun')

    def test_status_lists_stale_lock_and_in_flight(self):
        self.abort(fill_limit=1)
        self.set_lock('alice', age_min=200)
        st = sr.status()
        self.assertEqual(st['lock']['state'], 'stale')
        self.assertEqual([(f['cell'], f['stage'], f['plan']) for f in st['in_flight']], [(CELL, 'trees', 'p')])
        self.assertEqual(sr.main(['status']), 1)


class TestUndo(Aborted):
    def setUp(self):
        super().setUp()
        self.ground()
        self.mpath = self.t / 'masks' / 'trees-48_-44.mask.json'
        mp = lambda p, c, d=None: self.t / 'masks' / f'{p}-{c[0]}_{c[1]}.mask.json'
        for p in (mock.patch.object(mask_undo, 'mask_path', mp), mock.patch('pass_mask.mask_path', mp),
                  mock.patch.object(mask_undo, 'shared_positions', lambda m: set()),
                  mock.patch.object(mask_undo, 'World', lambda: View(self.blocks)),
                  mock.patch.object(mask_undo, 'ignored_builds', lambda c: ())):
            p.start()
            self.addCleanup(p.stop)

    def trees(self):
        """A landed trees pass with its mask on disk."""
        plan = site_kit.Plan('tp')
        plan.set(X0, 61, Z0, 'minecraft:oak_leaves')
        plan.set(X0 + 1, 61, Z0, 'minecraft:oak_leaves')
        self.run_apply(plan, stage='trees', cell=CELL)
        mask = pm.build_mask('trees', CELL, 'tp', dict(plan.cells), lambda p: True)
        pm.write_mask(mask, self.t / 'masks')
        return plan

    def live_undo(self):
        with mock.patch.object(site_lock, 'check', lambda owner=None: None):
            return mask_undo.run(CELL, 'trees', live=True)

    def test_clean_live_undo_voids_the_pass_and_archives_the_mask(self):
        plan = self.trees()
        self.assertTrue(self.view().trees)
        self.assertEqual(self.live_undo(), 0)
        self.assertFalse(self.mpath.exists())
        self.assertFalse(self.view().trees)
        self.assertEqual({self.blocks.read(p) for p in plan.cells}, {AIR})
        undo = self.reg().entries()[-1]
        self.assertEqual((undo.kind, undo.status), ('undo', 'landed'))
        self.assertTrue(undo.meta['mask_backup'].endswith('.mask.json'))

    def test_conflict_raises_instead_of_returning(self):
        self.trees()
        self.blocks.write((X0, 61, Z0), 'minecraft:stone')                  # no longer the masked block
        self.blocks.flush()
        with self.assertRaises(SystemExit):
            mask_undo.run(CELL, 'trees', live=False)
        with self.assertRaises(SystemExit):
            self.live_undo()
        self.assertTrue(self.mpath.exists())                             # mask kept: a caller cannot carry on over it
        self.assertTrue(self.view().trees)

    def test_live_flush_failure_raises(self):
        self.trees()
        FakePaced.abort_on_save = True
        with self.assertRaises(SystemExit):
            self.live_undo()

    def test_live_undo_readback_mismatch_raises_and_keeps_mask(self):
        self.trees()
        FakePaced.skip_fills = True
        with self.assertRaises(SystemExit) as ctx:
            self.live_undo()
        self.assertIn('site_recover.py status', str(ctx.exception))
        self.assertTrue(self.mpath.exists())
        self.assertEqual(self.reg().entries()[-1].status, 'begun')

    def test_aborted_undo_that_matches_finishes_the_bookkeeping(self):
        self.trees()
        FakePaced.abort_on_save = True                                   # the undo's writes land, the save dies
        with self.assertRaises(RuntimeError):
            self.run_undo((CELL, 'trees'), meta={'undo_plan': 'highfield_48_m44_trees_undo'})
        FakePaced.abort_on_save = False
        self.set_lock('alice', age_min=200)
        res = self.recover()
        self.assertEqual(res['action'], 'applied')
        self.assertFalse(self.mpath.exists())
        self.assertTrue((self.t / 'masks' / 'undone' / 'highfield_48_m44_trees_undo.mask.json').exists())
        self.assertFalse(self.view().trees)

    def test_reapply_aborted_after_undo_restores_the_before_undo_snapshot(self):
        plan = self.trees()
        self.assertEqual(self.live_undo(), 0)
        bare = dict(self.blocks._blocks)
        with mock.patch.object(pm, 'write_mask'):
            self.abort(fill_limit=1)                                     # trees re-applied, died mid-way
        self.set_lock('alice', age_min=200)
        loaded = []
        res = self.recover(loader=self.loader(loaded))
        self.assertEqual(res['action'], 'rolled-back')
        self.assertEqual(loaded[0][0], 'southampton_refine:before_p')     # this apply first, then the pre-undo world
        self.assertTrue(loaded[1][0].startswith('southampton_refine:before_undo_tp'))
        self.assertEqual({self.blocks.read(p) for p in plan.cells}, {'minecraft:oak_leaves'})   # not left bare
        self.assertNotEqual(dict(self.blocks._blocks), bare)
        self.assertTrue(self.mpath.exists())                                                     # mask put back
        self.assertTrue(self.view().trees)                                                       # #53: undo void cleared

    def test_undo_of_a_named_plan_erases_to_the_earlier_owner(self):
        self.run_apply(make_plan('house'))
        self.run_undo('house')
        self.assertEqual(self.blocks.read((X0, 60, Z0)), AIR)
        self.assertEqual([f.id for f in self.view().features], [])


class TestFollowUps(Base):
    """A landed real apply runs the journal follow-ups: frontier ghost ground (ring 1 here)."""

    def setUp(self):
        self.planned = []

        def ghosts(cell, layer):
            self.planned.append((cell, layer))
            x0, z0 = cell[0] * 128, cell[1] * 128
            if layer != 'ground':
                raise AssertionError(f'unexpected layer {layer!r}')
            return {(x0, 50, z0): 'minecraft:light_gray_concrete'}
        self.ghosts = ghosts
        super().setUp()

    def test_real_ground_ensures_the_frontier_and_supersedes_its_ghost_ground(self):
        self.run_apply(make_plan(), stage='ground-layer', cell=CELL)
        reg = self.reg()
        ring = {(CELL[0] + dx, CELL[1] + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)} - {CELL}
        self.assertEqual({c for c, v in reg.view().items() if v.ghost.ground}, ring)
        self.assertEqual(reg.cell(CELL).ground, 'generated')
        self.assertEqual(self.blocks.read((49 * 128, 50, -44 * 128)), 'minecraft:light_gray_concrete')
        self.assertEqual(reg.follow_ups(), ())

    def test_feature_apply_does_not_require_ghost_feature_strip(self):
        from cell_journal import CellRegistry
        reg = CellRegistry(self.t, self.blocks)
        x0, z0 = CELL[0] * 128, CELL[1] * 128
        reg.change(Build('ghost_48_m44_ground', 'ghost-ground',
                         {(x0, 50, z0): 'minecraft:light_gray_concrete'}, cell=CELL),
                   lambda w: self.blocks.write_many(w.cells))
        plan = make_plan()
        plan.set(X0 + 1, 63, Z0, 'minecraft:bricks')
        self.run_apply(plan)
        self.assertEqual(self.reg().cell(CELL).ground, 'ghost')  # seed cell still ghost until real ground
        self.assertEqual([e.plan for e in self.entries() if e.plan.startswith('ghost_strip')], [])

    def entry(self, plan):
        return self.entries(plan)[-1]


if __name__ == '__main__':
    unittest.main()
