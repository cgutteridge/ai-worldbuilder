"""Synthetic tests for the spike #323 tree-line evidence (no world data)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tree_lines as tln  # noqa: E402

N = 40
BOUNDS = (0, 0, N - 1, N - 1)


def scene():
    """A lane along z=10, a dark belt north of it, open pasture south, mapped wood in the west."""
    lum = np.full((N, N), 120.0)           # bright open grass
    wood = np.zeros((N, N), bool)
    wood[:8, :] = True                      # mapped wood, dark
    lum[:8, :] = 70.0
    lum[15:30, 2:9] = 75.0                  # unmapped dark belt north of the lane
    open_grass = ~wood
    open_grass[15:30, 2:9] = True           # the belt sits inside the grass polygon
    allowed = np.ones((N, N), bool)
    lane = [{'highway': 'service', 'points': [(0.0, 10.0), (39.0, 10.0)], 'half': 1.0}]
    return lum, wood, open_grass, allowed, lane


class CorridorTest(unittest.TestCase):
    def test_hedge_and_lane_reach(self):
        cor = tln.corridor({(20, 20)}, [{'highway': 'service', 'points': [(0, 5), (10, 5)], 'half': 1}],
                           BOUNDS, reach=2)
        self.assertIn((22, 20), cor)
        self.assertNotIn((23, 20), cor)
        self.assertIn((5, 8), cor)           # half 1 + reach 2
        self.assertNotIn((5, 9), cor)

    def test_footpaths_ignored_and_carriageway(self):
        path = [{'highway': 'footway', 'points': [(0, 5), (10, 5)], 'half': 1}]
        self.assertEqual(tln.corridor((), path, BOUNDS), set())
        road = tln.corridor((), path, BOUNDS, reach=0, highways=None)
        self.assertIn((5, 5), road)
        self.assertNotIn((5, 7), road)


class DarkCanopyTest(unittest.TestCase):
    def test_calibrated_between_medians(self):
        lum, wood, open_grass, _allowed, _lane = scene()
        mask, info = tln.dark_canopy(lum, wood, open_grass, min_columns=10)
        self.assertEqual(info['threshold'], 95.0)
        self.assertTrue(mask[20, 5])
        self.assertFalse(mask[20, 30])

    def test_refuses_without_references(self):
        lum, wood, open_grass, _allowed, _lane = scene()
        mask, info = tln.dark_canopy(lum, np.zeros_like(wood), open_grass, min_columns=10)
        self.assertIsNone(mask)
        self.assertIn('too few', info['refused'])
        mask, info = tln.dark_canopy(np.full((N, N), 100.0), wood, open_grass, min_columns=10)
        self.assertIsNone(mask)


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self._min = tln.MIN_REFERENCE_COLUMNS
        tln.MIN_REFERENCE_COLUMNS = 10

    def tearDown(self):
        tln.MIN_REFERENCE_COLUMNS = self._min

    def test_belt_kept_pasture_clear(self):
        lum, wood, open_grass, allowed, lane = scene()
        ev = tln.line_evidence(BOUNDS, (), lane, lum, wood, open_grass & ~wood, allowed)
        self.assertIn((20, 5), ev['belt'])               # belt north of the lane
        self.assertFalse(any(z > 14 for _x, z in ev['belt']))   # nothing on the pasture south
        self.assertFalse(any(x < 8 for x, _z in ev['belt']))    # mapped wood already passes the gate

    def test_belt_grows_through_mapped_wood_but_not_into_it(self):
        lum, wood, open_grass, allowed, lane = scene()
        lum[30:36, 0:4] = 72.0                            # dark patch reached only through more wood
        wood[30:33, 0:4] = True
        ev = tln.line_evidence(BOUNDS, (), lane, lum, wood, open_grass & ~wood, allowed)
        self.assertIn((34, 2), ev['belt'])
        self.assertNotIn((31, 2), ev['belt'])

    def test_disconnected_dark_patch_ignored(self):
        lum, wood, open_grass, allowed, lane = scene()
        lum[20:24, 30:34] = 70.0                          # dark patch out in the pasture
        ev = tln.line_evidence(BOUNDS, (), lane, lum, wood, open_grass & ~wood, allowed)
        self.assertNotIn((22, 32), ev['belt'])

    def test_height_alone_is_evidence(self):
        lum, wood, open_grass, allowed, lane = scene()
        h = np.zeros((N, N))
        h[30, 12] = 6.0
        ev = tln.line_evidence(BOUNDS, (), lane, lum, wood, open_grass & ~wood, allowed, h, 'first-return')
        self.assertIn((30, 12), ev['belt'])
        self.assertEqual(ev['height_source'], 'first-return')


class CrownTest(unittest.TestCase):
    def test_spacing_and_determinism(self):
        cols = [(x, 0) for x in range(20)]
        score = {q: float(q[0]) for q in cols}
        a = tln.place_crowns(cols, score)
        self.assertEqual(a, tln.place_crowns(list(reversed(cols)), score))
        xs = [x for x, _z, _r in a]
        self.assertEqual(xs[0], 19)
        self.assertTrue(all(abs(p - q) >= tln.CROWN_SPACING for p in xs for q in xs if p != q))

    def test_radius_guide(self):
        self.assertEqual(tln.radius_for(None), tln.DEFAULT_RADIUS)
        self.assertEqual(tln.radius_for(float('nan')), tln.DEFAULT_RADIUS)
        self.assertEqual(tln.radius_for(14), 6.0)
        self.assertEqual(tln.radius_for(8), 4.0)


class SeamTest(unittest.TestCase):
    def test_first_return_height_not_canopy_layer(self):
        h = tln.first_return_height(np.array([2.0, 2.0, np.nan]), np.array([9.0, 1.0, 5.0]))
        self.assertEqual(h[0], 7.0)
        self.assertEqual(h[1], 0.0)
        self.assertTrue(np.isnan(h[2]))

    def test_gate_window(self):
        h = np.zeros((10, 10))
        h[5, 5] = 2.5
        ok = tln.gated_crowns([(3, 5, 3.0), (2, 5, 3.0)], 0, 0, h)
        self.assertEqual(ok, {(3, 5): True, (2, 5): False})
        self.assertEqual(tln.gated_crowns([(3, 5, 3.0)], 0, 0, None), {(3, 5): False})


if __name__ == '__main__':
    unittest.main()
