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


    def test_later_set_or_fill_prunes_keep_slab_cell(self):
        p = Plan('t')
        p.set(1, 60, 1, 'brick_slab[type=bottom,waterlogged=false]')
        p.keep_slab_cells.add((1, 60, 1))
        p.set(1, 60, 1, 'stone')
        self.assertNotIn((1, 60, 1), p.keep_slab_cells)
        p.keep_slab_cells.add((2, 60, 1))
        p.fill((2, 60, 1), (2, 60, 1), 'stone')
        self.assertEqual(p.keep_slab_cells, set())


class TestMcpWriterSlabs(unittest.TestCase):
    SLAB = 'minecraft:brick_slab[type=bottom,waterlogged=false]'

    def _run(self, writes, results=None):
        from unittest import mock
        from site_kit import McpWriter
        m = mock.Mock()
        m.text.return_value = 'ok'
        m.call.side_effect = results or (lambda n, a: {'content': [{'type': 'text', 'text': 'done'}]})
        with mock.patch('site_kit._bring_player', return_value=None), \
                mock.patch('mc_player.tell_player'):
            McpWriter(m)(writes)
        return m

    def _writes(self):
        from types import SimpleNamespace
        return SimpleNamespace(
            intent=None, kind='build', plan='t', pass_='build', signs=(), entities=(),
            cells={(1, 60, 1): self.SLAB, (2, 60, 1): 'minecraft:bricks'})

    def test_replay_places_slab_after_fills_with_arguments(self):
        m = self._run(self._writes())
        names = [c.args[0] for c in m.call.call_args_list]
        self.assertEqual(names, ['block_set_state'])
        self.assertEqual(m.text.call_args_list[0].args[0], 'block_fill_batch')
        args = m.call.call_args_list[0].args[1]
        self.assertEqual(args['position'], {'x': 1, 'y': 60, 'z': 1})
        self.assertEqual(args['block'], {
            'id': 'minecraft:brick_slab',
            'properties': {'type': 'bottom', 'waterlogged': 'false'}})

    def test_failed_slab_placement_raises(self):
        err = lambda n, a: {'isError': True, 'content': [{'type': 'text', 'text': 'bad state'}]}
        with self.assertRaises(RuntimeError):
            self._run(self._writes(), err)


if __name__ == '__main__':
    unittest.main()
