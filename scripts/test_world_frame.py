#!/usr/bin/env python3
"""Unit tests for world_frame (no gdal required for OSGB↔MC / cells / WM math).

  .venv/bin/python -m unittest scripts.test_world_frame
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'shims'))
import world_frame as wf  # noqa: E402


class OsGbMcTest(unittest.TestCase):
    def test_roundtrip(self):
        e, n = wf.mc_to_osgb(5652, -2492)
        x, z = wf.osgb_to_mc(e, n)
        self.assertEqual((x, z), (5652, -2492))

    def test_legacy_offsets_shape(self):
        o = wf.legacy_offsets_dict()
        self.assertEqual(o['OFFSET_E'], 436248)
        self.assertEqual(o['SEA_LEVEL'], 11)
        self.assertTrue(wf.in_bounds(5652, -2492))


class CellTest(unittest.TestCase):
    def test_cell_of_and_bounds(self):
        self.assertEqual(wf.cell_of(6144, -5632), (48, -44))
        self.assertEqual(wf.cell_bounds((48, -44)), (6144, -5632, 6271, -5505))


class YOffsetTest(unittest.TestCase):
    def test_aod_to_y(self):
        self.assertEqual(wf.metres_aod_to_y(45.9), 11 + 45)
        self.assertEqual(wf.y_to_metres_aod(56), 45.0)


class WebMercatorTest(unittest.TestCase):
    def test_deg2num_num2deg_roundtrip_corner(self):
        # Known Southampton-ish point; integer tile of NW corner maps back.
        lat, lon = 50.935, -1.396
        z = 18
        tx, ty = wf.deg2num(lat, lon, z)
        lat2, lon2 = wf.num2deg(tx, ty, z)
        self.assertLess(abs(lat - lat2), 0.01)
        self.assertLess(abs(lon - lon2), 0.01)
        fx, fy = wf.tile_float(lat, lon, z)
        self.assertEqual(int(fx), tx)
        self.assertEqual(int(fy), ty)

    def test_parse_wm(self):
        self.assertEqual(wf.parse_wm('18/130062/87838'), (18, 130062, 87838))


class CliSmokeTest(unittest.TestCase):
    def test_info_and_to_osgb(self):
        self.assertEqual(wf.main(['info']), 0)
        self.assertEqual(wf.main(['to-osgb', '--mc', '5652,-2492']), 0)
        self.assertEqual(wf.main(['to-cell', '--mc', '6144,-5632']), 0)
        self.assertEqual(wf.main(['y', '--aod', '45.2']), 0)


if __name__ == '__main__':
    unittest.main()
