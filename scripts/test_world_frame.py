#!/usr/bin/env python3
"""Unit tests for world_frame (no GDAL executable; disposable unit-test world only).

EPSG point conversion is covered with explicit fixtures (independent of any
selected place checkout). OSGB↔MC / cells / WM math use the unit-test frame.

  .venv/bin/python scripts/run_tests.py --only test_world_frame
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'shims'))
import world_frame as wf  # noqa: E402

# Independent fixtures (not derived from this module's transform).
# Southampton O.S.O. Fundamental Bench Mark (bench-marks.org.uk/bm1150):
# published OSGB36 E 442070 N 112800 with WGS84 50.91313038, -1.40294699.
# With OSTN15 these agree to centimetres; Helmert fallback is ~metre-level.
FBM_LAT = 50.91313038
FBM_LON = -1.40294699
FBM_E = 442070.0
FBM_N = 112800.0

# Helmert / no-grid path for the same WGS84 (EPSG:4326→27700 via
# OSGB36 to WGS 84 (6) + British National Grid). Recorded from pyproj 3.8
# without the OSTN15 grid so fallback tests stay deterministic offline.
FBM_HELMERT_E = 442070.61748948053
FBM_HELMERT_N = 112799.04379774761


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


class EpsgPointConversionTest(unittest.TestCase):
    """Seam: transform_epsg / wgs84↔osgb (+ batches). No GDAL subprocess."""

    def test_empty_input_returns_empty(self):
        self.assertEqual(wf.transform_epsg([], 'EPSG:4326', 'EPSG:27700'), [])
        self.assertEqual(wf.wgs84_batch_to_osgb([]), [])
        self.assertEqual(wf.osgb_batch_to_wgs84([]), [])

    def test_always_xy_axis_order_lon_lat_in_epsg4326(self):
        # Lon-first pair for 4326; lat-first would land far outside GB.
        pairs = wf.transform_epsg([(FBM_LON, FBM_LAT)], 'EPSG:4326', 'EPSG:27700')
        self.assertEqual(len(pairs), 1)
        e, n = pairs[0]
        self.assertGreater(e, 400_000)
        self.assertLess(e, 500_000)
        self.assertGreater(n, 100_000)
        self.assertLess(n, 200_000)

    def test_wgs84_helpers_use_lat_lon_call_order(self):
        e, n = wf.wgs84_to_osgb(FBM_LAT, FBM_LON)
        self.assertGreater(e, 400_000)
        lat, lon = wf.osgb_to_wgs84(e, n)
        self.assertAlmostEqual(lat, FBM_LAT, places=5)
        self.assertAlmostEqual(lon, FBM_LON, places=5)

    def test_small_batch_matches_singles(self):
        pts = [
            (FBM_LAT, FBM_LON),
            (50.903, -1.4044),
            (51.0, -1.0),
        ]
        batch = wf.wgs84_batch_to_osgb(pts)
        singles = [wf.wgs84_to_osgb(lat, lon) for lat, lon in pts]
        self.assertEqual(len(batch), 3)
        for (be, bn), (se, sn) in zip(batch, singles):
            self.assertAlmostEqual(be, se, places=6)
            self.assertAlmostEqual(bn, sn, places=6)
        back = wf.osgb_batch_to_wgs84(batch)
        for (lat, lon), (blat, blon) in zip(pts, back):
            self.assertAlmostEqual(blat, lat, places=5)
            self.assertAlmostEqual(blon, lon, places=5)

    def test_forward_inverse_roundtrip_metres(self):
        e, n = wf.wgs84_to_osgb(FBM_LAT, FBM_LON)
        lat, lon = wf.osgb_to_wgs84(e, n)
        e2, n2 = wf.wgs84_to_osgb(lat, lon)
        self.assertAlmostEqual(e2, e, places=3)
        self.assertAlmostEqual(n2, n, places=3)

    def test_no_gdal_subprocess(self):
        with mock.patch('subprocess.run', side_effect=AssertionError('GDAL forbidden')):
            wf.wgs84_to_osgb(FBM_LAT, FBM_LON)
            wf.osgb_to_wgs84(FBM_E, FBM_N)
            wf.transform_epsg([(FBM_LON, FBM_LAT)], 'EPSG:4326', 'EPSG:27700')

    def test_fbm_accuracy_against_published_osgb(self):
        """Independent FBM fixture; tolerance depends on OSTN15 availability."""
        e, n = wf.wgs84_to_osgb(FBM_LAT, FBM_LON)
        if wf.ostn15_grid_available():
            self.assertAlmostEqual(e, FBM_E, delta=0.05)
            self.assertAlmostEqual(n, FBM_N, delta=0.05)
        else:
            # Documented Helmert fallback: metre-level, not centimetre.
            self.assertAlmostEqual(e, FBM_HELMERT_E, places=3)
            self.assertAlmostEqual(n, FBM_HELMERT_N, places=3)
            self.assertLess(abs(e - FBM_E), 5.0)
            self.assertLess(abs(n - FBM_N), 5.0)


class CliSmokeTest(unittest.TestCase):
    def test_info_and_to_osgb(self):
        self.assertEqual(wf.main(['info']), 0)
        self.assertEqual(wf.main(['to-osgb', '--mc', '5652,-2492']), 0)
        self.assertEqual(wf.main(['to-cell', '--mc', '6144,-5632']), 0)
        self.assertEqual(wf.main(['y', '--aod', '45.2']), 0)


if __name__ == '__main__':
    unittest.main()
