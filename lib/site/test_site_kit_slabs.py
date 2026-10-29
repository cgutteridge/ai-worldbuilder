"""Bottom-slab rewrite guard for block_fill."""
from __future__ import annotations

import unittest

from site_kit import (
    Plan,
    full_block_for_bottom_slab,
    is_bottom_slab,
    rewrite_bottom_slabs,
)


class TestBottomSlabs(unittest.TestCase):
    def test_detects_bottom_and_default(self):
        self.assertTrue(is_bottom_slab('light_gray_concrete_slab'))
        self.assertTrue(is_bottom_slab('minecraft:oak_slab[type=bottom]'))
        self.assertFalse(is_bottom_slab('minecraft:oak_slab[type=top]'))
        self.assertFalse(is_bottom_slab('minecraft:stone'))

    def test_full_block_mapping(self):
        self.assertEqual(
            full_block_for_bottom_slab('light_gray_concrete_slab'),
            'minecraft:light_gray_concrete',
        )
        self.assertEqual(
            full_block_for_bottom_slab('oak_slab[type=bottom]'),
            'minecraft:oak_planks',
        )
        self.assertIsNone(full_block_for_bottom_slab('oak_slab[type=top]'))

    def test_rewrite_mutates_plan(self):
        p = Plan('t')
        p.set(1, 60, 1, 'light_gray_concrete_slab')
        p.set(2, 60, 1, 'oak_slab[type=top]')
        n = rewrite_bottom_slabs(p)
        self.assertEqual(n, 1)
        self.assertEqual(p.cells[(1, 60, 1)], 'minecraft:light_gray_concrete')
        self.assertIn('type=top', p.cells[(2, 60, 1)])

    def test_keep_flag_skips(self):
        p = Plan('t')
        p.keep_bottom_slabs = True
        p.set(1, 60, 1, 'light_gray_concrete_slab')
        self.assertEqual(rewrite_bottom_slabs(p), 0)
        self.assertIn('slab', p.cells[(1, 60, 1)])

    def test_roof_slab_cells_survive_rewrite(self):
        p = Plan('t')
        p.set(1, 60, 1, 'brick_slab[type=bottom,waterlogged=false]')
        p.set(2, 60, 1, 'brick_slab[type=bottom,waterlogged=false]')
        p.keep_slab_cells.add((1, 60, 1))
        self.assertEqual(rewrite_bottom_slabs(p), 1)
        self.assertIn('type=bottom', p.cells[(1, 60, 1)])
        self.assertEqual(p.cells[(2, 60, 1)], 'minecraft:bricks')

    def test_block_arg_splits_properties(self):
        from site_kit import _block_arg
        self.assertEqual(
            _block_arg('brick_slab[type=bottom,waterlogged=false]'),
            {'id': 'minecraft:brick_slab',
             'properties': {'type': 'bottom', 'waterlogged': 'false'}},
        )


if __name__ == '__main__':
    unittest.main()
