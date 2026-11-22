import unittest
from types import SimpleNamespace
from unittest import mock

import stoop


class _Live:
    """Live stub: ground top per (x, z), natural stone below it."""

    def __init__(self, tops):
        self.tops = tops

    def block(self, x, y, z):
        top = self.tops.get((x, z))
        if top is None or y > top:
            return 'minecraft:air'
        return 'minecraft:grass_block' if y == top else 'minecraft:stone'


def _build(tops, owner=(), step_max=4, ledge_stair=False):
    return SimpleNamespace(live=_Live(tops), owner={c: 0 for c in owner}, foreign=set(),
                           spec={'step_max': step_max, 'ledge_stair': ledge_stair})


class VocabularyTest(unittest.TestCase):
    def test_stair_uses_catalogue_names_and_optional_shape(self):
        self.assertEqual(stoop.stair('stone', 'north'), 'stone_stairs[facing=north,half=bottom]')
        self.assertEqual(stoop.stair('brick', 'east', half='top', shape='inner_left'),
                         'brick_stairs[facing=east,half=top,shape=inner_left]')

    def test_toward_is_the_opposite_of_face(self):
        opposite = {'north': 'south', 'south': 'north', 'east': 'west', 'west': 'east'}
        for d, facing in stoop.FACE.items():
            self.assertEqual(stoop.TOWARD[d], opposite[facing])

    def test_top_solid_finds_natural_ground_and_skips_stairs(self):
        live = mock.Mock()
        live.block.side_effect = lambda x, y, z: {
            30: 'minecraft:stone_stairs[facing=north]', 29: 'minecraft:grass_block'}.get(y, 'minecraft:air')
        self.assertEqual(stoop.top_solid(live, 0, 0, 28), 29)
        self.assertIsNone(stoop.top_solid(_Live({}), 0, 0, 28))


class StepOutTest(unittest.TestCase):
    def run_step(self, tops, floor=50, **kw):
        placed = {}
        log = []
        build = _build(tops, **kw)
        stoop.step_out(build, (10, 10), 'S', floor, lambda x, y, z, st: placed.__setitem__((x, y, z), st),
                       log=log.append)
        return placed, log

    def test_ground_below_floor_gets_a_stair_rising_toward_the_house(self):
        placed, log = self.run_step({(10, 11): 48})
        self.assertEqual(placed[(10, 50, 11)], 'stone_stairs[facing=north,half=bottom]')
        self.assertEqual(placed[(10, 49, 11)], 'stone')
        self.assertEqual([placed[(10, y, 11)] for y in (51, 52, 53)], ['air'] * 3)
        self.assertEqual(log, ['step at (10, 11) ground 48 floor 50'])

    def test_ledge_above_floor_is_lowered_and_the_walk_continues(self):
        placed, _ = self.run_step({(10, 11): 51, (10, 12): 50})
        self.assertEqual(placed[(10, 50, 11)], 'stone')
        self.assertEqual(placed[(10, 51, 11)], 'air')
        self.assertNotIn((10, 50, 12), placed)

    def test_real_slope_is_left_alone(self):
        placed, _ = self.run_step({(10, 11): 53})
        self.assertEqual(placed, {})

    def test_owned_columns_are_skipped(self):
        placed, _ = self.run_step({(10, 11): 40, (10, 12): 48}, owner=[(10, 11)])
        self.assertNotIn((10, 49, 11), placed)
        self.assertEqual(placed[(10, 50, 12)], 'stone_stairs[facing=north,half=bottom]')

    def test_prefetch_gets_every_cell_the_walk_can_read(self):
        seen = []
        stoop.step_out(_build({}), (10, 10), 'S', 50, lambda *a: None, prefetch=lambda cells: seen.extend(cells))
        self.assertEqual({(x, z) for x, _y, z in seen}, {(10, 10 + k) for k in range(1, 5)})
        self.assertTrue(all(isinstance(c, tuple) and len(c) == 3 for c in seen))


if __name__ == '__main__':
    unittest.main()
