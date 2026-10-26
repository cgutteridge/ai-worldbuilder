"""Unit tests for surface_probe (no Minecraft)."""
import unittest

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


if __name__ == '__main__':
    unittest.main()
