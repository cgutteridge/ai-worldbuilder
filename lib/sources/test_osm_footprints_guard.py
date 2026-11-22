"""Unit tests for osm_footprints.guard — synthetic index + ground, no live world."""
from __future__ import annotations

import unittest

import osm_footprints as OF


class _FlatGround:
    """World stub: every column's ground is a fixed Y."""

    def __init__(self, y: int = 64):
        self.y = y

    def ground(self, x, z, top=120, bottom=0):
        return self.y


class _OpenMappedGround:
    """OSMIndex stub: covered map, no nearby footprints, not a street-front zone."""

    def covered(self, x, z):
        return True

    def near_footprint(self, x, z, margin=OF.MARGIN):
        return False

    def front_zone(self, x, z, reach=30):
        return False

    def nearest_street(self, x, z, named_only=True):
        return (8.0, 'Piazza', 'pedestrian', (x + 0.5, z + 0.5))


class GuardRoofLikeOutsideTest(unittest.TestCase):
    """R-008: concrete / slab canopies at BACK_RISE must not slip past the shed exemption."""

    def setUp(self):
        self.world = _FlatGround(64)
        self.idx = _OpenMappedGround()

    def test_red_concrete_canopy_at_back_rise_is_outside(self):
        # Parasol canopy: red_concrete four above ground on open mapped terrace (not a shed).
        cells = {(100, 68, 200): 'minecraft:red_concrete'}
        rep = OF.guard(cells, world=self.world, idx=self.idx)
        self.assertIn((100, 68, 200), rep['outside'])
        self.assertEqual(rep['outside'][(100, 68, 200)][1], OF.BACK_RISE)

    def test_slab_at_back_rise_is_outside(self):
        cells = {(101, 68, 200): 'minecraft:smooth_stone_slab[type=bottom]'}
        rep = OF.guard(cells, world=self.world, idx=self.idx)
        self.assertIn((101, 68, 200), rep['outside'])

    def test_wood_shed_at_back_rise_still_exempt(self):
        # Intended BACK_RISE use: garden shed / outbuilding materials, not roof plates.
        cells = {(102, 68, 200): 'minecraft:oak_planks'}
        rep = OF.guard(cells, world=self.world, idx=self.idx)
        self.assertNotIn((102, 68, 200), rep['outside'])

    def test_concrete_at_max_rise_still_fine(self):
        # Kerbs / low paving anywhere: MAX_RISE exemption unchanged.
        cells = {(103, 66, 200): 'minecraft:light_gray_concrete'}
        rep = OF.guard(cells, world=self.world, idx=self.idx)
        self.assertNotIn((103, 66, 200), rep['outside'])


if __name__ == '__main__':
    unittest.main()
