"""#327: ground-layer work through ``Plan`` is refused by ``site_kit.apply`` on a cell without a ground pass.

Seam: ``site_kit.apply`` over MemoryBlocks via ``journal_testkit.LiveApplyCase``. No Minecraft.
"""
from __future__ import annotations

import unittest

import cell_registry
import site_kit
from cell_journal import Build
from journal_testkit import AIR, LiveApplyCase

CELL = (48, -44)
X0, Z0 = CELL[0] * 128, CELL[1] * 128
GREY = 'minecraft:light_gray_concrete'
LINE = 'minecraft:white_concrete'


class TestPlanGroundLayer(unittest.TestCase):
    def test_marking_api_flags_the_plan_and_plain_set_does_not(self):
        plain = site_kit.Plan('p')
        plain.set(X0, 61, Z0, LINE)
        self.assertFalse(plain.ground_layer)
        one = site_kit.Plan('a')
        one.ground_marking(X0, 61, Z0, LINE)
        self.assertTrue(one.ground_layer)
        self.assertEqual(one.cells, {(X0, 61, Z0): LINE})
        run = site_kit.Plan('b')
        run.ground_marking_fill((X0, 61, Z0), (X0 + 2, 61, Z0), LINE)
        self.assertTrue(run.ground_layer)
        self.assertEqual(len(run.cells), 3)


class TestApplyRefusesGroundLayerWithoutGround(LiveApplyCase):
    def ghost_only_cell(self):
        reg = cell_registry.open_registry(blocks=self.blocks)
        ghost = {(X0, 59, Z0): GREY, (X0 + 1, 59, Z0): GREY}
        reg.change(Build('ghost_48_m44_ground', 'ghost-ground', ghost, cell=CELL),
                   lambda w: self.blocks.write_many(w.cells))
        self.assertEqual(self.reg().cell(CELL).ground, 'ghost')

    def bay_lines(self, name='bay_lines'):
        plan = site_kit.Plan(name)
        plan.ground_marking_fill((X0, 60, Z0), (X0 + 4, 60, Z0), LINE)
        return plan

    def test_ground_marking_refused_on_ghost_only_cell_and_nothing_written(self):
        self.ghost_only_cell()
        plan = self.bay_lines()
        for stage in ('other', None):
            with self.assertRaises(SystemExit) as ctx:
                self.run_apply(plan, stage=stage, cell=CELL)
            self.assertIn('landed ground pass', str(ctx.exception))
        for p in plan.cells:
            self.assertEqual(self.blocks.read(p), AIR)
        self.assertEqual(self.entries('bay_lines'), ())

    def test_same_blocks_through_plain_set_are_not_flagged(self):
        """The gate is by construction: only the marking API flags the plan (documented in rules)."""
        self.ghost_only_cell()
        plan = site_kit.Plan('plain_lines')
        plan.fill((X0, 60, Z0), (X0 + 4, 60, Z0), LINE)
        self.run_apply(plan, stage='other', cell=CELL)
        self.assertEqual(self.blocks.read((X0, 60, Z0)), LINE)

    def test_ground_marking_lands_once_the_ground_pass_has(self):
        self.ghost_only_cell()
        ground = site_kit.Plan('highfield_48_m44_ground')
        ground.set(X0 + 10, 59, Z0, 'minecraft:stone')
        self.run_apply(ground, stage='ground-layer', cell=CELL)
        self.run_apply(self.bay_lines(), stage='other', cell=CELL)
        self.assertEqual(self.blocks.read((X0 + 4, 60, Z0)), LINE)


if __name__ == '__main__':
    unittest.main()
