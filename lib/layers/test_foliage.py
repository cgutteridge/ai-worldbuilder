"""Tests for refine foliage allowlist and strip helpers."""
from __future__ import annotations

import unittest

from foliage import (
    REFINE_PLANTS,
    assert_canopy_replant_ok,
    expand_tree,
    is_refine_foliage,
    plan_tree,
    should_strip_corridor_canopy,
    strip_foliage_above_surface,
    tree_voxels,
)
from site_kit import Plan, World


class TestFoliageAllowlist(unittest.TestCase):
    def test_persistent_leaves(self):
        self.assertTrue(is_refine_foliage('minecraft:oak_leaves[persistent=true]'))
        self.assertFalse(is_refine_foliage('minecraft:oak_leaves'))

    def test_log_and_plants(self):
        self.assertTrue(is_refine_foliage('minecraft:oak_log'))
        self.assertTrue(is_refine_foliage('minecraft:poppy'))
        self.assertFalse(is_refine_foliage('minecraft:grass_block'))
        self.assertTrue(is_refine_foliage('minecraft:oak_sapling'))

    def test_woodland_plant_states_are_strip_targets(self):
        for state in ('short_dry_grass', 'tall_dry_grass', 'bush',
                      'leaf_litter[facing=west,segment_amount=4]',
                      'leaf_litter[facing=north,segment_amount=1]',
                      'large_fern[half=upper]', 'pitcher_plant[half=lower]',
                      'pitcher_plant[half=upper]'):
            with self.subTest(state=state):
                self.assertTrue(is_refine_foliage(f'minecraft:{state}'))
                self.assertTrue(should_strip_corridor_canopy(f'minecraft:{state}'))

    def test_recognition_does_not_authorize_unverified_placement_states(self):
        for plant in ('short_dry_grass', 'tall_dry_grass', 'leaf_litter', 'bush',
                      'wildflowers', 'pink_petals', 'large_fern', 'pitcher_plant'):
            with self.subTest(plant=plant):
                self.assertNotIn(plant, REFINE_PLANTS)
                self.assertTrue(is_refine_foliage(plant))


class TestTreeVoxels(unittest.TestCase):
    def test_trunk_and_leaves(self):
        v = tree_voxels(10, 20, 54, 6, skip_xz=frozenset())
        self.assertIn((10, 55, 20), v)
        self.assertEqual(v[(10, 55, 20)], 'oak_log')
        self.assertTrue(any('leaves' in st for st in v.values()))

    def test_skip_footprint(self):
        v = tree_voxels(10, 20, 54, 6, skip_xz=frozenset({(11, 20)}))
        self.assertNotIn((11, 55, 20), v)


class TestCanopyGuard(unittest.TestCase):
    def test_refuses_sparse_replant(self):
        audit = {
            'removed_logs': 200,
            'removed_leaves': 2000,
            'added_logs': 50,
            'added_leaves': 300,
        }
        with self.assertRaises(SystemExit):
            assert_canopy_replant_ok(audit, accept_loss=False)

    def test_accepts_override(self):
        audit = {
            'removed_logs': 10,
            'removed_leaves': 100,
            'added_logs': 5,
            'added_leaves': 10,
        }
        assert_canopy_replant_ok(audit, accept_loss=True)

    def test_small_strip_skips_guard(self):
        audit = {
            'removed_logs': 2,
            'removed_leaves': 10,
            'added_logs': 0,
            'added_leaves': 0,
        }
        assert_canopy_replant_ok(audit, accept_loss=False)


class TestCorridorStrip(unittest.TestCase):
    def test_stock_and_geocraft_leaves_on_footway(self):
        self.assertTrue(should_strip_corridor_canopy('minecraft:oak_leaves'))
        self.assertTrue(
            should_strip_corridor_canopy(
                'minecraft:oak_leaves[distance=7,persistent=true,waterlogged=false]',
            ),
        )
        self.assertTrue(should_strip_corridor_canopy('minecraft:oak_log[axis=y]'))

    def test_sapling_stripped(self):
        self.assertTrue(should_strip_corridor_canopy('minecraft:oak_sapling'))


class TestStrip(unittest.TestCase):
    def test_strip_clears_woodland_cover_without_changing_floor(self):
        class Column:
            def __init__(self, plant):
                self.plant = plant

            def block(self, x, y, z):
                return {40: 'minecraft:podzol', 41: self.plant}.get(y, 'minecraft:air')

        for state in ('short_dry_grass', 'tall_dry_grass', 'bush',
                      'leaf_litter[facing=west,segment_amount=4]',
                      'leaf_litter[facing=north,segment_amount=1]'):
            with self.subTest(state=state):
                plan = Plan('strip')
                count = strip_foliage_above_surface(plan, Column(f'minecraft:{state}'), {(0, 0)}, y_top=43)
                self.assertEqual(count, 1)
                self.assertEqual(plan.cells, {(0, 41, 0): 'minecraft:air'})

    def test_sapling_forbidden_on_plan(self):
        plan = Plan('t')
        with self.assertRaises(SystemExit):
            plan.set(0, 60, 0, 'oak_sapling')

    def test_expand_tree_writes_cells(self):
        plan = Plan('t')
        v = expand_tree(0, 0, 50, height=5, skip_xz=frozenset())
        plan_tree(plan, v)
        self.assertGreater(len(plan.cells), 3)


if __name__ == '__main__':
    unittest.main()
