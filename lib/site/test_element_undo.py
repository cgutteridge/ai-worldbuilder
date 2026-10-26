"""element_undo: journal Undo for any element (feature / ground / trees / decor).

Seam: ``element_undo.run`` over MemoryBlocks via ``journal_testkit.LiveApplyCase``
(``run_apply`` / live journal Undo). No Minecraft.
"""
from __future__ import annotations

import json
import time
import unittest
from unittest import mock

import element_undo
import site_kit
import site_lock
from journal_testkit import AIR, LiveApplyCase

CELL = (48, -44)
X0, Z0 = 6150, -5600


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

        self.assertEqual(self.blocks.read((X0, 60, Z0)), AIR)
        self.assertEqual([f.id for f in self.reg().cell(CELL).features], [])

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


if __name__ == '__main__':
    unittest.main()
