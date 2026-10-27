"""element_undo: journal Undo for any element (feature / ground / trees / decor).

Seam: ``element_undo.run`` over MemoryBlocks via ``journal_testkit.LiveApplyCase``
(``run_apply`` / live journal Undo). No Minecraft.

#123 also covers ``site_kit.diff_against_before`` / post-undo before_<plan> check.
"""
from __future__ import annotations

import json
import time
import unittest
from unittest import mock

import element_undo
import site_kit
import site_lock
from journal_testkit import AIR, FakePaced, LiveApplyCase

CELL = (48, -44)
X0, Z0 = 6150, -5600
GREY = 'minecraft:light_gray_concrete'


class ElementUndoCase(LiveApplyCase):
    """LiveApplyCase plus a claimed lock so ``site_kit.undo`` gates pass."""

    def setUp(self):
        super().setUp()
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

    def test_reject_ambiguous(self):
        with self.assertRaises(SystemExit):
            element_undo.resolve_target(plan='house', cell=(0, 0), pass_name='ground')
        with self.assertRaises(SystemExit):
            element_undo.resolve_target()


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

        g = site_kit.Plan('highfield_48_m44_ground')
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


if __name__ == '__main__':
    unittest.main()
