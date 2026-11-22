"""element_undo: journal Undo for any element (feature / ground / trees / decor).

Seam: ``element_undo.run`` over MemoryBlocks via ``journal_testkit.LiveApplyCase``
(``run_apply`` / live journal Undo). No Minecraft.

#123 also covers ``site_kit.diff_against_before`` / post-undo before_<plan> check.
"""
from __future__ import annotations

import json
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import element_undo
import site_kit
import site_lock
from journal_testkit import AIR, FakePaced, LiveApplyCase

CELL = (48, -44)
X0, Z0 = 6150, -5600
ORIGIN = (X0, 60, Z0)
GREY = 'minecraft:light_gray_concrete'


class ElementUndoCase(LiveApplyCase):
    """LiveApplyCase plus a claimed lock so ``site_kit.undo`` gates pass."""

    def setUp(self):
        super().setUp()
        policy = mock.patch('readiness_evidence.policy_for', return_value=SimpleNamespace(
            check=lambda *args, **kw: {'ready': True}))
        policy.start()
        self.addCleanup(policy.stop)
        self.blocks.write((X0, 60, Z0), 'minecraft:dirt')
        self.blocks.flush()
        hb = time.time()
        site_lock.LOCK.write_text(json.dumps({
            'owner': 'alice', 'task': 't', 'claimed': hb, 'heartbeat': hb,
            'claimed_at': 'x', 'heartbeat_at': 'x',
        }))

    def run_live(self, **kw):
        with mock.patch.object(site_lock, 'check', lambda owner=None: None):
            return element_undo.run(**kw)


class TestFeatureUndo(ElementUndoCase):
    def test_apply_named_plan_erases_blocks_and_clears_feature(self):
        plan = site_kit.Plan('house')
        plan.set(X0, 60, Z0, 'minecraft:bricks')
        self.run_apply(plan)
        self.assertEqual(self.blocks.read((X0, 60, Z0)), 'minecraft:bricks')
        self.assertEqual([f.id for f in self.reg().cell(CELL).features], ['house'])

        self.assertEqual(self.run_live(plan='house', live=True), 0)

        # setUp dirt is journalled as prior; undo restores it (not air).
        # #128: undo clears the build only; house stays on the cell plan as planned.
        self.assertEqual(self.blocks.read((X0, 60, Z0)), 'minecraft:dirt')
        self.assertEqual([(f.id, f.status) for f in self.reg().cell(CELL).features],
                         [('house', 'planned')])

    def test_dry_run_named_plan_does_not_mutate(self):
        plan = site_kit.Plan('house')
        plan.set(X0, 60, Z0, 'minecraft:bricks')
        self.run_apply(plan)

        self.assertEqual(element_undo.run(plan='house', live=False), 0)

        self.assertEqual(self.blocks.read((X0, 60, Z0)), 'minecraft:bricks')
        self.assertEqual([f.id for f in self.reg().cell(CELL).features], ['house'])

    def test_refuse_when_plan_missing(self):
        with self.assertRaises(SystemExit) as ctx:
            element_undo.run(plan='missing', live=False)
        self.assertIn('nothing to undo', str(ctx.exception))

    def test_unready_neighbour_refuses_before_undo_and_force_bypasses(self):
        plan = site_kit.Plan('edge_house')
        plan.set(X0, 60, Z0, 'minecraft:bricks')
        plan.set(X0 + 128, 60, Z0, 'minecraft:bricks')
        self.run_apply(plan)
        before = self.reg().entries()
        check = mock.Mock(side_effect=lambda c, op, **kw: {'ready': c == CELL,
                         'blockers': ['validation stale', 'aerial coverage: 0%']})
        with mock.patch('readiness_evidence.policy_for', return_value=SimpleNamespace(check=check)):
            with self.assertRaisesRegex(SystemExit, 'undo refused before writes: needs-prep'):
                self.run_live(plan='edge_house', live=True)
            self.assertEqual(self.reg().entries(), before)
            self.assertEqual({c.args[0] for c in check.call_args_list}, {CELL, (49, -44)})
            self.assertEqual(self.run_live(plan='edge_house', live=True, force=True), 0)

    def test_undo_may_pass_the_mismatch_it_removes(self):
        plan = site_kit.Plan('edge_house')
        plan.set(X0, 60, Z0, 'minecraft:bricks')
        self.run_apply(plan)
        check = mock.Mock(return_value={'ready': True})
        with mock.patch('readiness_evidence.policy_for', return_value=SimpleNamespace(check=check)):
            self.assertEqual(self.run_live(plan='edge_house', live=True), 0)
        self.assertEqual({tuple(c.kwargs['redoing']) for c in check.call_args_list}, {('edge_house',)})

    def test_whole_cell_checks_feature_neighbour_before_first_pass(self):
        feature = site_kit.Plan('edge_house')
        feature.set(X0, 60, Z0, 'minecraft:bricks')
        feature.set(X0 + 128, 60, Z0, 'minecraft:bricks')
        self.run_apply(feature)
        ground = site_kit.Plan('g')
        ground.set(X0 + 5, 59, Z0, 'grass_block')
        self.run_apply(ground, stage='ground', cell=CELL)
        before = self.reg().entries()
        check = lambda c, op, **kw: {'ready': c == CELL, 'blockers': ['validation stale']}
        with mock.patch('readiness_evidence.policy_for', return_value=SimpleNamespace(check=check)):
            with self.assertRaisesRegex(SystemExit, '49,-44'):
                self.run_live(cell=CELL, live=True)
        self.assertEqual(self.reg().entries(), before)


class TestGroundUndo(ElementUndoCase):
    def test_apply_cell_pass_voids_ground(self):
        g = site_kit.Plan('g')
        g.set(X0 + 5, 59, Z0, 'minecraft:grass_block')
        self.run_apply(g, stage='ground-layer', cell=CELL)
        self.assertEqual(self.reg().cell(CELL).ground, 'generated')

        self.assertEqual(self.run_live(cell=CELL, pass_name='ground', live=True), 0)

        self.assertEqual(self.blocks.read((X0 + 5, 59, Z0)), AIR)
        self.assertEqual(self.reg().cell(CELL).ground, 'none')

    def test_refuse_when_pass_not_done(self):
        with self.assertRaises(SystemExit) as ctx:
            element_undo.run(cell=CELL, pass_name='ground', live=False)
        self.assertIn('nothing to undo', str(ctx.exception))


class TestResolveTarget(unittest.TestCase):
    def test_plan_name(self):
        self.assertEqual(element_undo.resolve_target(plan='house'), 'house')

    def test_cell_and_pass(self):
        self.assertEqual(
            element_undo.resolve_target(cell=(48, -44), pass_name='ground'),
            ((48, -44), 'ground'),
        )

    def test_cell_alone_is_whole_cell(self):
        self.assertEqual(
            element_undo.resolve_target(cell=(48, -44)),
            ((48, -44), None),
        )

    def test_reject_ambiguous(self):
        with self.assertRaises(SystemExit):
            element_undo.resolve_target(plan='house', cell=(0, 0), pass_name='ground')
        with self.assertRaises(SystemExit):
            element_undo.resolve_target()


class TestWholeCellUndo(ElementUndoCase):
    """#134: one command undoes everything above ghost in fixed order."""

    def _seed_above_ghost(self):
        """Ground + feature + trees + decor in CELL (build order)."""
        g = site_kit.Plan('g')
        g.set(X0, 59, Z0, 'minecraft:grass_block')
        self.run_apply(g, stage='ground-layer', cell=CELL)
        house = site_kit.Plan('house')
        house.set(X0, 60, Z0, 'minecraft:bricks')
        self.run_apply(house)
        trees = site_kit.Plan('t')
        trees.set(X0 + 1, 61, Z0, 'minecraft:oak_log')
        self.run_apply(trees, stage='trees', cell=CELL)
        decor = site_kit.Plan('d')
        decor.set(X0 + 2, 61, Z0, 'minecraft:poppy')
        self.run_apply(decor, stage='decor', cell=CELL)
        v = self.reg().cell(CELL)
        self.assertEqual(v.ground, 'generated')
        self.assertTrue(v.trees)
        self.assertTrue(v.decor)
        self.assertEqual([f.id for f in v.features], ['house'])

    def test_dry_run_lists_undo_order_without_mutating(self):
        self._seed_above_ghost()
        with mock.patch('builtins.print') as printed:
            self.assertEqual(element_undo.run(cell=CELL, live=False), 0)
        text = '\n'.join(str(c.args[0]) for c in printed.call_args_list if c.args)
        self.assertIn('decor', text)
        self.assertIn('trees', text)
        self.assertIn('feature', text)
        self.assertIn('ground', text)
        # Fixed order: decor before trees before feature before ground
        self.assertLess(text.index('decor'), text.index('trees'))
        self.assertLess(text.index('trees'), text.index('feature'))
        self.assertLess(text.index('feature'), text.index('ground'))
        self.assertIn('dry run', text.lower())
        v = self.reg().cell(CELL)
        self.assertEqual(v.ground, 'generated')
        self.assertTrue(v.trees and v.decor)
        self.assertEqual([f.id for f in v.features], ['house'])
        self.assertEqual(self.blocks.read((X0, 60, Z0)), 'minecraft:bricks')

    def test_apply_undoes_all_above_ghost_in_order(self):
        self._seed_above_ghost()
        self.assertEqual(self.run_live(cell=CELL, live=True), 0)
        v = self.reg().cell(CELL)
        self.assertEqual(v.ground, 'none')
        self.assertFalse(v.trees)
        self.assertFalse(v.decor)
        self.assertEqual([(f.id, f.status) for f in v.features],
                         [('house', 'planned')])
        self.assertEqual(self.blocks.read((X0, 60, Z0)), 'minecraft:dirt')
        self.assertEqual(self.blocks.read((X0, 59, Z0)), AIR)
        self.assertEqual(self.blocks.read((X0 + 1, 61, Z0)), AIR)
        self.assertEqual(self.blocks.read((X0 + 2, 61, Z0)), AIR)

    def test_stops_at_first_refusal_and_reports_what_is_left(self):
        self._seed_above_ghost()
        before = f'{site_kit.STRUCTURE_NS}:before_house'
        FakePaced.structures[before][(X0, 60, Z0)] = 'minecraft:gold_block'

        with mock.patch('builtins.print') as printed:
            with self.assertRaises(SystemExit) as ctx:
                self.run_live(cell=CELL, live=True)
        msg = str(ctx.exception).lower()
        text = '\n'.join(str(c.args[0]) for c in printed.call_args_list if c.args)
        combined = f'{msg}\n{text}'.lower()
        self.assertTrue('left' in combined or 'remaining' in combined)
        self.assertIn('feature', combined)
        self.assertIn('ground', combined)
        # decor + trees already undone; feature refused; ground untouched
        v = self.reg().cell(CELL)
        self.assertFalse(v.decor)
        self.assertFalse(v.trees)
        self.assertEqual([f.id for f in v.features], ['house'])
        self.assertEqual(v.ground, 'generated')
        self.assertEqual(self.blocks.read((X0, 60, Z0)), 'minecraft:bricks')
        self.assertEqual(self.blocks.read((X0, 59, Z0)), 'minecraft:grass_block')

    def test_refuse_when_nothing_above_ghost(self):
        with self.assertRaises(SystemExit) as ctx:
            element_undo.run(cell=CELL, live=False)
        self.assertIn('nothing', str(ctx.exception).lower())


class TestDiffAgainstBefore(unittest.TestCase):
    """Pure seam: compare a footprint to an expected before_<plan> map."""

    def test_clean_when_equal(self):
        cells = {(1, 2, 3): 'minecraft:dirt', (1, 3, 3): AIR}
        rep = site_kit.diff_against_before(cells, cells)
        self.assertEqual(rep['mismatches'], 0)
        self.assertEqual(rep['checked'], 2)

    def test_reports_pit_as_mismatch(self):
        # #123 symptom: undo wrote air where before_<plan> had solid substrate
        expected = {(X0, 58, Z0): GREY, (X0, 59, Z0): GREY, (X0, 60, Z0): GREY}
        actual = {(X0, 58, Z0): AIR, (X0, 59, Z0): AIR, (X0, 60, Z0): AIR}
        rep = site_kit.diff_against_before(expected, actual)
        self.assertEqual(rep['mismatches'], 3)
        self.assertIn(str((X0, 58, Z0)), rep['mismatch_sample'])


class TestUndoMatchesBeforeStructure(ElementUndoCase):
    """After undo, footprint must match the apply-time before_<plan> structure (#123)."""

    def test_undo_restores_world_matching_before_structure(self):
        plan = site_kit.Plan('house')
        plan.set(X0, 60, Z0, 'minecraft:bricks')
        plan.set(X0, 61, Z0, 'minecraft:bricks')
        footprint = list(plan.cells)
        self.run_apply(plan)
        before = f'{site_kit.STRUCTURE_NS}:before_house'
        self.assertIn(before, FakePaced.structures)
        snap = FakePaced.structures[before]

        self.assertEqual(self.run_live(plan='house', live=True), 0)

        for p in footprint:
            want = snap.get(p) or AIR
            self.assertEqual(self.blocks.read(p), want)

    def test_undo_feature_over_ghost_matches_before_not_air_pit(self):
        # Cause of #123: real ground elsewhere must not leave a pit under the feature.
        import cell_registry
        from cell_journal import Build

        ghost_cells = {
            (X0, 58, Z0): GREY, (X0, 59, Z0): GREY, (X0, 60, Z0): GREY,
            (X0 + 5, 60, Z0): GREY,
        }
        for p, s in ghost_cells.items():
            self.blocks.write(p, s)
        self.blocks.flush()
        reg = cell_registry.open_registry(blocks=self.blocks)
        reg.change(
            Build('ghost_48_m44_ground', 'ghost-ground', ghost_cells, cell=CELL),
            lambda w: self.blocks.write_many(w.cells),
        )

        plan = site_kit.Plan('house')
        plan.set(X0, 60, Z0, 'minecraft:bricks')
        plan.set(X0, 61, Z0, 'minecraft:bricks')
        footprint = list(plan.cells)
        self.run_apply(plan)
        before = f'{site_kit.STRUCTURE_NS}:before_house'
        snap = dict(FakePaced.structures[before])

        g = site_kit.Plan('cell_48_m44_ground')
        g.set(X0 + 5, 60, Z0, 'minecraft:stone')
        self.run_apply(g, stage='ground-layer', cell=CELL)

        self.assertEqual(self.run_live(plan='house', live=True), 0)

        self.assertEqual(self.blocks.read((X0, 60, Z0)), GREY)
        self.assertEqual(self.blocks.read((X0, 58, Z0)), GREY)
        for p in footprint:
            want = snap.get(p) or AIR
            self.assertEqual(self.blocks.read(p), want)

    def test_undo_fails_closed_when_erase_differs_from_before(self):
        plan = site_kit.Plan('house')
        plan.set(X0, 60, Z0, 'minecraft:bricks')
        self.run_apply(plan)
        before = f'{site_kit.STRUCTURE_NS}:before_house'
        # Poison the snapshot so the erase plan (restore dirt) disagrees with before_<plan>
        FakePaced.structures[before][(X0, 60, Z0)] = 'minecraft:gold_block'

        with self.assertRaises(SystemExit) as ctx:
            self.run_live(plan='house', live=True)
        msg = str(ctx.exception).lower()
        self.assertIn('before_', msg)
        self.assertTrue('differ' in msg or 'mismatch' in msg)
        # Refused before write: feature still built, block still bricks, undo abandoned
        self.assertEqual(self.blocks.read((X0, 60, Z0)), 'minecraft:bricks')
        self.assertEqual([f.id for f in self.reg().cell(CELL).features], ['house'])
        undos = [e for e in self.reg().entries() if e.kind == 'undo']
        self.assertEqual({e.status for e in undos}, {'abandoned'})
        self.assertTrue(all(e.undone for e in undos))

    def test_cell_pass_undo_of_rebuilt_ground_compares_against_earliest_before(self):
        # Rebuild over a live build: the second before_<plan> holds the first build,
        # but a cell-pass undo erases both back to the base ghost under them.
        info = self.setup_layer_rebuilds(
            cell=CELL, origin=ORIGIN, shapes=('same', 'same'),
            rebuild_blocks=('minecraft:grass_block', 'minecraft:stone'),
        )

        self.assertEqual(self.run_live(cell=CELL, pass_name='ground', live=True), 0)

        self.assertEqual(self.blocks.read(ORIGIN), info['start'][ORIGIN])

    def test_cell_pass_undo_of_rebuilt_ground_with_no_base_restores_start(self):
        # #138: no owner underneath — journal priors alone must restore the start
        # world (the 2026-10-27 bent fixture hid this behind a ghost base).
        info = self.setup_layer_rebuilds(
            cell=CELL, origin=ORIGIN, shapes=('same', 'same', 'same'),
            with_base=False,
            rebuild_blocks=('minecraft:grass_block', 'minecraft:stone', 'minecraft:dirt'),
        )

        self.assertEqual(self.run_live(cell=CELL, pass_name='ground', live=True), 0)

        self.assertEqual(self.blocks.read(ORIGIN), info['start'][ORIGIN])

    def test_rebuild_wider_than_first_build_still_undoes(self):
        # The rebuild covers a column the first build never wrote; that column must
        # use the second build's before_<plan>, not be forced to air or an earlier box.
        far = (X0 + 20, 60, Z0)
        self.setup_layer_rebuilds(
            cell=CELL, origin=ORIGIN, shapes=('same',),
            rebuild_blocks=('minecraft:grass_block',),
        )
        # FakePaced snapshots the whole world into every structure; poison the first
        # snapshot at ``far`` so a box-based check would disagree — writer membership
        # must keep ``far`` on the second before_<plan> only (#138).
        FakePaced.structures[f'{site_kit.STRUCTURE_NS}:before_g'][far] = 'minecraft:gold_block'
        # Widen in a second rebuild (helper already landed base+first; far was not in
        # the base, so seed it then rebuild wider over ghost + self).
        self.blocks.write(far, GREY)
        self.blocks.flush()
        second = site_kit.Plan('g')
        second.set(X0, 60, Z0, 'minecraft:stone')
        second.set(*far, 'minecraft:stone')
        self.run_apply(second, stage='ground-layer', cell=CELL, modifies=['ghost', 'g'])

        self.assertEqual(self.run_live(cell=CELL, pass_name='ground', live=True), 0)

        self.assertEqual(self.blocks.read((X0, 60, Z0)), GREY)
        self.assertEqual(self.blocks.read(far), GREY)
        undo = [e for e in self.reg().entries() if e.kind == 'undo']
        self.assertEqual([e.status for e in undo], ['landed'])

    def test_interleaved_feature_inside_earlier_ground_box_still_undoes(self):
        # Feature sits inside the first ground's structure box but was never written
        # by that build — only the rebuild covers it. Box-based check would use
        # before_g1 (predates the feature) and refuse; writer membership uses
        # before_g2 (#138).
        left, mid, right = (X0, 60, Z0), (X0 + 1, 60, Z0), (X0 + 2, 60, Z0)
        base = site_kit.Plan('ghost')
        for p in (left, mid, right):
            base.set(*p, GREY)
        self.run_apply(base, stage='other', cell=CELL)
        first = site_kit.Plan('g')
        first.set(*left, 'minecraft:grass_block')
        first.set(*right, 'minecraft:grass_block')  # box spans mid without writing it
        self.run_apply(first, stage='ground-layer', cell=CELL, modifies=['ghost'])
        house = site_kit.Plan('house')
        house.set(*mid, 'minecraft:bricks')
        self.run_apply(house, modifies=['ghost'])
        second = site_kit.Plan('g')
        second.set(*left, 'minecraft:stone')
        second.set(*mid, 'minecraft:stone')
        second.set(*right, 'minecraft:stone')
        self.run_apply(second, stage='ground-layer', cell=CELL, modifies=['ghost', 'house'])

        self.assertEqual(self.run_live(cell=CELL, pass_name='ground', live=True), 0)

        self.assertEqual(self.blocks.read(left), GREY)
        self.assertEqual(self.blocks.read(right), GREY)
        # mid restores the interleaved feature (under-owner / g2 prior), not ghost
        self.assertEqual(self.blocks.read(mid), 'minecraft:bricks')
        undo = [e for e in self.reg().entries() if e.kind == 'undo']
        self.assertEqual([e.status for e in undo], ['landed'])

    def test_shared_column_with_interleaved_under_owner_still_undoes(self):
        # Shared column written by g1 → house → g2. Journal erase restores house
        # (under-owner); earliest live writer's before_g1 predates house and would
        # false-refuse if the footprint check bound that column (#138).
        mid = ORIGIN
        self.setup_layer_rebuilds(
            cell=CELL, origin=ORIGIN, shapes=('same', 'same'),
            rebuild_blocks=('minecraft:grass_block', 'minecraft:stone'),
            interleaved={'name': 'house', 'pos': mid, 'block': 'minecraft:bricks', 'after': 0},
        )

        self.assertEqual(self.run_live(cell=CELL, pass_name='ground', live=True), 0)

        self.assertEqual(self.blocks.read(mid), 'minecraft:bricks')
        undo = [e for e in self.reg().entries() if e.kind == 'undo']
        self.assertEqual([e.status for e in undo], ['landed'])


class VerifyEraseMatchesBefore(unittest.TestCase):
    """The #123 guard: ghost-filled positions are exempt, lost ground is not."""

    AIR, GREY, STONE = 'minecraft:air', 'minecraft:gray_concrete', 'minecraft:stone'

    def setUp(self):
        import site_kit

        class M:
            structures = {'before_x': {(0, 59, 0): self.AIR, (1, 59, 0): self.STONE}}

        self.kit, self.m = site_kit, M()
        self.fp = [('x', {'name': 'before_x', 'from': (0, 59, 0)}, [(0, 59, 0), (1, 59, 0)])]

    def test_ghost_fill_over_air_snapshot_passes(self):
        erase = {(0, 59, 0): self.GREY, (1, 59, 0): self.STONE}
        self.kit.verify_erase_matches_before(self.fp, erase, self.m, ghost_filled=frozenset({(0, 59, 0)}))

    def test_non_ghost_non_air_over_air_snapshot_still_refused(self):
        erase = {(0, 59, 0): self.GREY, (1, 59, 0): self.STONE}
        with self.assertRaises(SystemExit):
            self.kit.verify_erase_matches_before(self.fp, erase, self.m)

    def test_lost_ground_still_refused(self):
        erase = {(0, 59, 0): self.AIR, (1, 59, 0): self.AIR}
        with self.assertRaises(SystemExit):
            self.kit.verify_erase_matches_before(self.fp, erase, self.m, ghost_filled=frozenset({(0, 59, 0)}))


if __name__ == '__main__':
    unittest.main()
