"""Redo preview uses the same placement rules and undo semantics as the actual workflow."""
import tempfile
import unittest
from pathlib import Path

from pass_mask import build_mask, write_mask
from site_kit import Plan, full
from trees_layer import TreeInputs, plan_cell
from trees_replan import replan

CELL = (0, 0)
AIR = 'minecraft:air'


class World:
    def __init__(self, blocks=None):
        self.blocks = {p: full(b) for p, b in (blocks or {}).items()}

    def block(self, x, y, z):
        return self.blocks.get((x, y, z), 'minecraft:grass_block' if y == 60 else AIR)


def inputs(masked=None):
    return TreeInputs([(64, 64, 3.0)], 1, set(), set(), set(), None, masked or {}, set())


class TestReplan(unittest.TestCase):
    def test_conflicting_undo_leaves_all_old_blocks_and_ownership_intact(self):
        from layer_planning import plan_after_undo
        old = {(64, 61, 64): 'oak_log', (64, 62, 64): 'oak_log'}
        live = World({**old, (64, 62, 64): 'stone'})
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            write_mask(build_mask('trees', CELL, 'old', old, lambda p: True), directory)
            undo = plan_after_undo('trees', CELL, live, mask_dir=directory)
        self.assertEqual(len(undo.conflicts), 1)
        self.assertIs(undo.world, live)
        self.assertEqual(undo.world.block(64, 61, 64), 'minecraft:oak_log')
        self.assertEqual(set(undo.masked), set(old))

    def test_no_own_mask_returns_the_world_unwrapped(self):
        from layer_planning import plan_after_undo
        live = World()
        with tempfile.TemporaryDirectory() as d:
            undo = plan_after_undo('trees', CELL, live, mask_dir=Path(d))
        self.assertIs(undo.world, live)
        self.assertEqual((undo.conflicts, undo.own), ([], set()))

    def test_preview_matches_actual_undo_and_preserves_shared_crown(self):
        placed, _ = plan_cell(CELL, Plan('first'), World(), inputs())
        self.assertGreater(len(placed), 0)
        shared = next(p for p, b in placed.items() if 'leaves' in b)
        neighbour = {shared: placed[shared]}
        old_mask = build_mask('trees', CELL, 'old', placed, lambda p: True)
        other_mask = build_mask('trees', (1, 0), 'neighbour', neighbour, lambda p: True)
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            write_mask(old_mask, directory)
            write_mask(other_mask, directory)
            own, preview, report = replan(CELL, world=World(placed), inputs=inputs(), mask_dir=directory)
            # After real undo only the shared leaf remains, and only the neighbour owns it.
            actual, actual_report = plan_cell(CELL, Plan('after-undo'), World(neighbour),
                                              inputs({shared: 'trees 1,0'}))
        self.assertEqual(own, set(placed))
        self.assertEqual(preview, actual)
        self.assertEqual(report, actual_report)
        self.assertEqual(len(report['trees']), 1)

    def test_changed_old_trunk_refuses_preview(self):
        p = (64, 61, 64)
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            write_mask(build_mask('trees', CELL, 'old', {p: 'minecraft:oak_log'}, lambda p: True), directory)
            with self.assertRaisesRegex(ValueError, 'undo conflicts'):
                replan(CELL, world=World({p: 'minecraft:bricks'}), inputs=inputs(), mask_dir=directory)


if __name__ == '__main__':
    unittest.main()
