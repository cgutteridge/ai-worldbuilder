"""World.ground: air matched by exact id, so stairs (a substring hit on 'air') count as ground (Q-181)."""
from __future__ import annotations

import unittest

from site_kit import World


class FakeWorld(World):
    def __init__(self, column):
        self.column = column

    def block(self, x, y, z):
        return self.column.get(y)


class TestGround(unittest.TestCase):
    def test_woodland_cover_is_not_ground_or_a_walking_obstacle(self):
        from site_walk import solid
        for state in ('short_dry_grass', 'tall_dry_grass', 'bush',
                      'leaf_litter[facing=south,segment_amount=3]', 'closed_eyeblossom'):
            with self.subTest(state=state):
                w = FakeWorld({61: f'minecraft:{state}', 60: 'minecraft:podzol'})
                self.assertEqual(w.ground(0, 0, top=70, bottom=50), 60)
                self.assertFalse(solid(f'minecraft:{state}'))

    def test_stairs_are_ground_not_air(self):
        w = FakeWorld({60: 'minecraft:stone_stairs[facing=north]', 59: 'minecraft:stone'})
        self.assertEqual(w.ground(0, 0, top=70, bottom=50), 60)

    def test_air_variants_are_skipped(self):
        w = FakeWorld({62: 'minecraft:air', 61: 'minecraft:cave_air', 60: 'minecraft:stone'})
        self.assertEqual(w.ground(0, 0, top=70, bottom=50), 60)

    def test_leaves_logs_water_still_skipped(self):
        w = FakeWorld({62: 'minecraft:oak_leaves', 61: 'minecraft:oak_log[axis=y]', 60: 'minecraft:water', 59: 'minecraft:grass_block'})
        self.assertEqual(w.ground(0, 0, top=70, bottom=50), 59)


if __name__ == '__main__':
    unittest.main()
