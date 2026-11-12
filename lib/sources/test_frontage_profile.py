"""Synthetic-raster tests for the aerial frontage profile (#269); no world."""
from __future__ import annotations

import math
import unittest

import frontage_profile as fpf

ROAD, PAVE, GRAVEL, LAWN = (.25, .25, .27), (.65, .65, .66), (.7, .62, .5), (.35, .55, .25)


def raster(angle_deg, pave, court, house_at=None, surface=GRAVEL):
    """Road centre at origin; distances along direction ``angle``: road to 5, pavement, forecourt, house."""
    a = math.radians(angle_deg)
    ax, az = math.cos(a), math.sin(a)
    house_d = 5 + pave + court

    def sample(x, z):
        d = x * ax + z * az
        return ROAD if d < 5 else PAVE if d < 5 + pave else surface

    house = set()
    if house_at != 'none':
        for u in range(-6, 7):
            for v in range(0, 8):
                d = house_d + v
                house.add((math.floor(ax * d - az * u), math.floor(az * d + ax * u)))
    centre = (ax * (house_d + 4), az * (house_d + 4))
    return sample, house, centre


class FrontageProfileTest(unittest.TestCase):
    def test_two_pavement_four_forecourt(self):
        s, h, c = raster(0, 2, 4)
        p = fpf.profile_frontage(s, (0.5, 0.5), (c[0] + .5, c[1] + .5), {(x + 0, z) for x, z in h})
        self.assertEqual(p['status'], 'ok')
        self.assertAlmostEqual(p['pavement'], 2, delta=.5)
        self.assertAlmostEqual(p['forecourt'], 4, delta=.7)
        self.assertEqual(p['surface'], 'gravel')

    def test_road_at_27_degrees(self):
        s, h, c = raster(27, 2, 4)
        p = fpf.profile_frontage(s, (0, 0), c, h)
        self.assertEqual(p['status'], 'ok')
        self.assertAlmostEqual(p['pavement'], 2, delta=.6)
        self.assertAlmostEqual(p['forecourt'], 4, delta=1.0)

    def test_no_house_returns_none(self):
        s = raster(0, 2, 4)[0]
        p = fpf.profile_frontage(s, (0, 0), (20, 0), set())
        self.assertEqual(p['status'], 'no house')
        self.assertIsNone(p['pavement'])
        self.assertIsNone(p['forecourt'])

    def test_surface_classes(self):
        self.assertEqual(fpf.colour_class(LAWN), 'lawn')
        self.assertEqual(fpf.colour_class((.15, .3, .12)), 'planting')
        self.assertEqual(fpf.colour_class((.6, .35, .3)), 'paving')
        self.assertEqual(fpf.colour_class(GRAVEL), 'gravel')
        self.assertEqual(fpf.colour_class(PAVE), 'pavement')

    def test_summary_median_range(self):
        rows = [{'street': 'A', 'side': 'north', 'status': 'ok', 'pavement': p, 'forecourt': f, 'surface': 'lawn'}
                for p, f in ((2, 3), (2.5, 5), (3, 4))]
        rows.append({'street': 'A', 'side': 'north', 'status': 'no house', 'pavement': None,
                     'forecourt': None, 'surface': None})
        s = fpf.summarise(rows)[0]
        self.assertEqual((s['frontages'], s['measured']), (4, 3))
        self.assertEqual(s['pavement'], {'median': 2.5, 'min': 2, 'max': 3})
        self.assertEqual(s['forecourt']['median'], 4)
        self.assertEqual(s['surface'], 'lawn')


if __name__ == '__main__':
    unittest.main()
