"""Unit tests for surface_probe (no Minecraft)."""
import unittest
from pathlib import Path

import numpy as np

import surface_probe as sp


NEW_GROUND_COVER = (
    'blue_orchid', 'orange_tulip', 'pink_tulip', 'lily_of_the_valley', 'wither_rose',
    'sunflower', 'rose_bush', 'peony', 'lilac',
    'short_dry_grass', 'tall_dry_grass', 'leaf_litter', 'bush', 'firefly_bush',
    'wildflowers', 'pink_petals', 'open_eyeblossom', 'closed_eyeblossom',
    'torchflower', 'pitcher_plant', 'dead_bush', 'sweet_berry_bush',
    'brown_mushroom', 'red_mushroom',
)


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


class TestGroundCover(unittest.TestCase):
    def test_ids_are_in_the_vendored_mojang_block_list(self):
        source = Path(__file__).resolve().parents[2] / 'config/minecraft-block-ids-26.2.txt'
        ids = set(source.read_text().splitlines())
        for plant in NEW_GROUND_COVER:
            with self.subTest(plant=plant):
                self.assertIn(f'minecraft:{plant}', ids)

    def test_every_new_plant_is_skipped_above_each_floor(self):
        for plant in NEW_GROUND_COVER:
            for ground in ('grass_block', 'dirt', 'podzol'):
                with self.subTest(plant=plant, ground=ground):
                    blocks = {42: f'minecraft:{ground}', 43: f'minecraft:{plant}'}
                    self.assertEqual(sp.surface_y(_Col(blocks), 0, 0, top=45), (42, ground))
                    self.assertTrue(sp.is_tree_or_plant_block(blocks[43]))

    def test_plant_at_lidar_terrain_is_not_ground(self):
        for plant in NEW_GROUND_COVER:
            with self.subTest(plant=plant):
                world = _Grid({(1, 64, 1): f'minecraft:{plant}'})
                self.assertIsNone(sp.ground_at(world, hm(), 1, 1))

    def test_litter_states_and_both_halves_remain_vegetation(self):
        for state in ('leaf_litter[facing=west,segment_amount=4]',
                      'leaf_litter[facing=north,segment_amount=1]',
                      'pitcher_plant[half=lower]', 'pitcher_plant[half=upper]'):
            with self.subTest(state=state):
                world = _Col({42: 'minecraft:podzol', 43: f'minecraft:{state}'})
                self.assertEqual(sp.surface_y(world, 0, 0, top=44), (42, 'podzol'))
                self.assertTrue(sp.is_tree_or_plant_block(f'minecraft:{state}'))

    def test_mushroom_blocks_are_still_ground(self):
        for block in ('brown_mushroom_block', 'red_mushroom_block'):
            with self.subTest(block=block):
                world = _Col({42: f'minecraft:{block}'})
                self.assertEqual(sp.surface_y(world, 0, 0, top=43), (42, block))
                self.assertFalse(sp.is_tree_or_plant_block(f'minecraft:{block}'))


def bound_world(y_offset):
    """Patch a selected world whose ``world-frame.json`` sets ``y_offset``."""
    from contextlib import ExitStack
    from unittest import mock
    from world_frame import WorldFrame
    stack = ExitStack()
    stack.enter_context(mock.patch('script_env.world_bound', return_value=True))
    stack.enter_context(mock.patch('world_frame.load_frame',
                                   return_value=WorldFrame({'offsets': {'easting': 0, 'northing': 0},
                                                            'y_offset': y_offset})))
    return stack


class _Reads(_Col):
    """Column stub that records the Y of every read."""

    def __init__(self, blocks):
        super().__init__(blocks)
        self.ys = []

    def block(self, x, y, z):
        self.ys.append(y)
        return super().block(x, y, z)


class TestProbeFloor(unittest.TestCase):
    """Low ground (river valleys, coasts) must be seen: the floor comes from the world, not Y 30."""

    def test_floor_is_sea_level_less_the_margin(self):
        with bound_world(11):                                    # southampton-build
            self.assertEqual(sp.probe_floor(), 11 - sp.LOW_GROUND_MARGIN_M)
        with bound_world(64):
            self.assertEqual(sp.probe_floor(), 64 - sp.LOW_GROUND_MARGIN_M)

    def test_floor_never_goes_under_the_ghost_fill(self):
        with bound_world(0):
            self.assertEqual(sp.probe_floor(), sp.WORLD_FLOOR_Y)
        with bound_world(-900):                                  # a mountain site
            self.assertEqual(sp.probe_floor(), sp.WORLD_FLOOR_Y)

    def test_ghost_fill_starts_one_above_the_world_floor(self):
        import lidar_ghost
        self.assertEqual(lidar_ghost.FILL_BOTTOM, sp.WORLD_FLOOR_Y + 1)

    def test_no_world_selected_uses_the_world_floor(self):
        from unittest import mock
        with mock.patch('script_env.world_bound', return_value=False):
            self.assertEqual(sp.probe_floor(), sp.WORLD_FLOOR_Y)

    def test_ground_at_y14_is_found(self):
        """Riverside Park / the industrial trial cell: ground at Y 13-16 read as an empty column."""
        with bound_world(11):
            self.assertEqual(sp.surface_y(_Col({14: 'minecraft:grass_block'}), 0, 0), (14, 'grass_block'))
            self.assertEqual(sp.surface_y(_Col({3: 'minecraft:water'}), 0, 0), (3, 'water'))

    def test_empty_column_reads_down_to_the_floor_only(self):
        col = _Reads({})
        with bound_world(11):
            self.assertIsNone(sp.surface_y(col, 0, 0))
            floor = sp.probe_floor()
        self.assertEqual(min(col.ys), floor + 1)

    def test_explicit_floor_wins(self):
        col = _Reads({14: 'minecraft:stone'})
        self.assertIsNone(sp.surface_y(col, 0, 0, floor=30))
        self.assertEqual(min(col.ys), 31)


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

    def test_missing_reason_names_the_cause(self):
        h = hm()
        self.assertEqual(sp.ground_missing_reason(h, 1, 1), 'no ground at terrain')
        self.assertEqual(sp.ground_missing_reason(h, 1, 1, owned={(1, 64, 1)}), 'feature above terrain')
        self.assertEqual(sp.ground_missing_reason(h, 1, 1, owned={(1, 70, 1)}), 'no ground at terrain')
        self.assertEqual(sp.ground_missing_reason(None, 1, 1), 'no surface')

    def test_raised_over_terrain(self):
        w = _Grid({(1, 64, 1): 'minecraft:grass_block', (1, 70, 1): 'minecraft:red_concrete', (2, 64, 1): 'minecraft:grass_block'})
        self.assertTrue(sp.raised_over_terrain(w, 1, 1, 64))
        self.assertFalse(sp.raised_over_terrain(w, 2, 1, 64))


if __name__ == '__main__':
    unittest.main()
