"""Unit tests for surface_probe (no Minecraft)."""
import unittest

import numpy as np

import surface_probe as sp


class _Col:
    """World stub: one column, ``{y: block}``, air elsewhere."""

    def __init__(self, blocks):
        self.blocks = blocks

    def block(self, x, y, z):
        return self.blocks.get(y, 'minecraft:air')


class TestSurfaceY(unittest.TestCase):
    def test_marker_wool_below_80_is_not_ground(self):
        col = _Col({42: 'minecraft:stone', 70: 'minecraft:yellow_wool'})
        self.assertEqual(sp.surface_y(col, 0, 0), (42, 'stone'))

    def test_marker_wool_high_is_not_ground(self):
        col = _Col({42: 'minecraft:stone', 90: 'minecraft:black_wool'})
        self.assertEqual(sp.surface_y(col, 0, 0), (42, 'stone'))

    def test_other_wool_is_still_a_surface(self):
        col = _Col({42: 'minecraft:stone', 50: 'minecraft:red_wool'})
        self.assertEqual(sp.surface_y(col, 0, 0), (50, 'red_wool'))

    def test_plain_ground_unchanged(self):
        self.assertEqual(sp.surface_y(_Col({42: 'minecraft:grass_block'}), 0, 0), (42, 'grass_block'))


class _Grid:
    """World stub: ``{(x, y, z): block}``, air elsewhere."""

    def __init__(self, blocks):
        self.blocks = blocks

    def block(self, x, y, z):
        return self.blocks.get((x, y, z), 'minecraft:air')


def hm(terrain=64, x0=0, z0=0, size=8):
    return {'x0': x0, 'z0': z0, 'terrain': np.full((size, size), terrain)}


class TestGroundAt(unittest.TestCase):
    """#301: ground is the block at lidar terrain Y, never the live top block (a roof)."""

    def test_reads_the_block_at_terrain_not_the_roof_above(self):
        w = _Grid({(1, 64, 1): 'minecraft:grass_block', (1, 70, 1): 'minecraft:red_concrete'})
        self.assertEqual(sp.surface_y(w, 1, 1), (70, 'red_concrete'))
        self.assertEqual(sp.ground_at(w, hm(), 1, 1), (64, 'grass_block'))

    def test_no_solid_block_at_terrain_is_no_ground(self):
        for block in ('minecraft:air', 'minecraft:short_grass', 'minecraft:oak_log', 'minecraft:oak_leaves',
                      'minecraft:yellow_wool'):
            with self.subTest(block=block):
                self.assertIsNone(sp.ground_at(_Grid({(1, 64, 1): block, (1, 70, 1): 'minecraft:stone'}), hm(), 1, 1))

    def test_floating_base_is_no_ground(self):
        self.assertIsNone(sp.ground_at(_Grid({(1, 66, 1): 'minecraft:grass_block'}), hm(), 1, 1))

    def test_owned_or_masked_ground_is_not_ground(self):
        w = _Grid({(1, 64, 1): 'minecraft:stone'})
        self.assertIsNone(sp.ground_at(w, hm(), 1, 1, owned={(1, 64, 1)}))
        self.assertIsNone(sp.ground_at(w, hm(), 1, 1, masked={(1, 64, 1): 'decor 0,0'}))
        self.assertEqual(sp.ground_at(w, hm(), 1, 1, owned={(1, 65, 1)}), (64, 'stone'))

    def test_water_at_terrain_is_returned_for_the_caller_to_refuse(self):
        self.assertEqual(sp.ground_at(_Grid({(1, 64, 1): 'minecraft:water'}), hm(), 1, 1), (64, 'water'))

    def test_missing_terrain_falls_back_to_the_live_surface(self):
        w = _Grid({(1, 70, 1): 'minecraft:red_concrete'})
        for h in (None, {'x0': 0, 'z0': 0}, hm(terrain=0), hm(x0=100)):
            with self.subTest(h=h):
                self.assertIsNone(sp.terrain_y(h, 1, 1))
                self.assertEqual(sp.ground_at(w, h, 1, 1), (70, 'red_concrete'))
        self.assertEqual(sp.ground_at(w, None, 1, 1, owned={(1, 70, 1)}), (70, 'red_concrete'))   # today's behaviour
        self.assertEqual(sp.ground_at(w, None, 1, 1, fallback=lambda *_a: (3, 'x')), (3, 'x'))

    def test_raised_over_terrain(self):
        w = _Grid({(1, 64, 1): 'minecraft:grass_block', (1, 70, 1): 'minecraft:red_concrete', (2, 64, 1): 'minecraft:grass_block'})
        self.assertTrue(sp.raised_over_terrain(w, 1, 1, 64))
        self.assertFalse(sp.raised_over_terrain(w, 2, 1, 64))


if __name__ == '__main__':
    unittest.main()
