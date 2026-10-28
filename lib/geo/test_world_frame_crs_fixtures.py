#!/usr/bin/env python3
"""Fictional WorldFrame CRS fixtures for multi-grid point conversion (#85).

Seams under test (approved plan 2026-10-25):
  - ``transform_epsg`` / BNG helpers for lat/lon ↔ projected E/N
  - ``osgb_to_mc`` / ``mc_to_osgb`` with an explicit ``WorldFrame`` for E/N ↔ MC

Permanent tests use only pyproj + fixed literals. No GDAL, network, or real
world checkout. Expected projected values come from published references
where available (OS FBM, LINZ NZTM example, UTM zone-centre identity);
NY-area UTM sample is a fixed recording cross-checked in the #85 validation
report (not recomputed here).

  .venv/bin/python scripts/run_tests.py --only test_world_frame_crs_fixtures
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'shims'))
import world_frame as wf  # noqa: E402


def _frame(crs: str, easting: float, northing: float) -> wf.WorldFrame:
    """Build a fictional WorldFrame (no disk, no active world)."""
    return wf.WorldFrame({
        'place': f'Fictional fixture {crs}',
        'crs': crs,
        'offsets': {'easting': easting, 'northing': northing},
        'y_offset': 11,
    })


def _dms(deg: int, minutes: int, seconds: float, hemi: str) -> float:
    """Degrees from signed DMS (S/W negative)."""
    value = deg + minutes / 60.0 + seconds / 3600.0
    return -value if hemi in ('S', 'W') else value


# ---- British National Grid (EPSG:27700) ------------------------------------
# Southampton O.S.O. FBM: bench-marks.org.uk/bm1150
# With OSTN15 these agree to centimetres; Helmert fallback is ~metre-level.
BNG_LAT, BNG_LON = 50.91313038, -1.40294699
BNG_E, BNG_N = 442070.0, 112800.0
# Helmert / no-grid path (same recording as test_world_frame.py).
BNG_HELMERT_E = 442070.61748948053
BNG_HELMERT_N = 112799.04379774761
BNG_FRAME = _frame('EPSG:27700', 440000.0, 110000.0)
# E/N → MC via offsets (north is −Z): x=2070, z=-2800
BNG_MC = (2070, -2800)
# West of origin → negative Minecraft X
BNG_WEST_E, BNG_WEST_N = 439500.0, 111200.0
BNG_WEST_MC = (-500, -1200)


def _assert_bng_forward_en(testcase: unittest.TestCase, e: float, n: float) -> None:
    """Assert WGS84→BNG against published FBM; tolerance depends on OSTN15."""
    if wf.ostn15_grid_available():
        testcase.assertAlmostEqual(e, BNG_E, delta=0.05)
        testcase.assertAlmostEqual(n, BNG_N, delta=0.05)
    else:
        testcase.assertAlmostEqual(e, BNG_HELMERT_E, places=3)
        testcase.assertAlmostEqual(n, BNG_HELMERT_N, places=3)
        testcase.assertLess(abs(e - BNG_E), 5.0)
        testcase.assertLess(abs(n - BNG_N), 5.0)

# ---- WGS84 / UTM zone 18N (EPSG:32618) -------------------------------------
# Zone central meridian: lon −75° → false easting 500000 exactly.
UTM_CENTRE_LAT, UTM_CENTRE_LON = 42.0, -75.0
UTM_CENTRE_E, UTM_CENTRE_N = 500000.0, 4649776.224819178
# Empire State vicinity — fixed recording (PROJ 9.8.1 / GDAL 3.13.3; see report).
UTM_NY_LAT, UTM_NY_LON = 40.7484, -73.9857
UTM_NY_E, UTM_NY_N = 585628.409087783, 4511322.44749613
UTM_FRAME = _frame('EPSG:32618', 580000.0, 4505000.0)
UTM_NY_MC = (5628, -6322)

# ---- NZTM2000 (EPSG:2193) --------------------------------------------------
# LINZ projection example points (Transverse Mercator table).
NZTM_LAT = _dms(41, 43, 44.6973, 'S')
NZTM_LON = _dms(172, 29, 58.2847, 'E')
NZTM_E, NZTM_N = 1558376.32, 5380181.71
NZTM_FRAME = _frame('EPSG:2193', 1550000.0, 5380000.0)
NZTM_MC = (8376, -182)


class BngFictionalFrameTest(unittest.TestCase):
    """EPSG:27700 fictional frame: published FBM + MC offsets."""

    def test_frame_crs_and_forward_to_published_osgb(self):
        self.assertEqual(BNG_FRAME.crs, 'EPSG:27700')
        e, n = wf.wgs84_to_osgb(BNG_LAT, BNG_LON)
        _assert_bng_forward_en(self, e, n)

    def test_projected_to_mc_known_and_negative_x(self):
        self.assertEqual(wf.osgb_to_mc(BNG_E, BNG_N, BNG_FRAME), BNG_MC)
        self.assertEqual(
            wf.osgb_to_mc(BNG_WEST_E, BNG_WEST_N, BNG_FRAME), BNG_WEST_MC,
        )
        self.assertLess(BNG_WEST_MC[0], 0)
        self.assertLess(BNG_MC[1], 0)

    def test_mc_to_projected_and_wgs84_roundtrip(self):
        e, n = wf.mc_to_osgb(*BNG_MC, BNG_FRAME)
        self.assertAlmostEqual(e, BNG_E, places=6)
        self.assertAlmostEqual(n, BNG_N, places=6)
        lat, lon = wf.osgb_to_wgs84(e, n)
        self.assertAlmostEqual(lat, BNG_LAT, places=5)
        self.assertAlmostEqual(lon, BNG_LON, places=5)

    def test_batch_forward_reverse(self):
        pts = [(BNG_LAT, BNG_LON), (50.903, -1.4044)]
        batch = wf.wgs84_batch_to_osgb(pts)
        self.assertEqual(len(batch), 2)
        _assert_bng_forward_en(self, batch[0][0], batch[0][1])
        back = wf.osgb_batch_to_wgs84(batch)
        for (lat, lon), (blat, blon) in zip(pts, back):
            self.assertAlmostEqual(blat, lat, places=5)
            self.assertAlmostEqual(blon, lon, places=5)

    def test_always_xy_lon_first_for_4326(self):
        e, n = wf.transform_epsg([(BNG_LON, BNG_LAT)], 'EPSG:4326', 'EPSG:27700')[0]
        _assert_bng_forward_en(self, e, n)
        # Lat-first would not land near published National Grid.
        bad = wf.transform_epsg([(BNG_LAT, BNG_LON)], 'EPSG:4326', 'EPSG:27700')[0]
        self.assertGreater(abs(bad[0] - BNG_E), 1000.0)


class Utm18nFictionalFrameTest(unittest.TestCase):
    """EPSG:32618 fictional frame: zone centre + NY sample + MC."""

    def test_zone_centre_false_easting_identity(self):
        self.assertEqual(UTM_FRAME.crs, 'EPSG:32618')
        e, n = wf.transform_epsg(
            [(UTM_CENTRE_LON, UTM_CENTRE_LAT)], 'EPSG:4326', 'EPSG:32618',
        )[0]
        self.assertAlmostEqual(e, UTM_CENTRE_E, places=3)
        self.assertAlmostEqual(n, UTM_CENTRE_N, places=2)

    def test_ny_sample_forward_reverse(self):
        e, n = wf.transform_epsg(
            [(UTM_NY_LON, UTM_NY_LAT)], 'EPSG:4326', 'EPSG:32618',
        )[0]
        self.assertAlmostEqual(e, UTM_NY_E, places=3)
        self.assertAlmostEqual(n, UTM_NY_N, places=3)
        lon, lat = wf.transform_epsg([(e, n)], 'EPSG:32618', 'EPSG:4326')[0]
        self.assertAlmostEqual(lat, UTM_NY_LAT, places=7)
        self.assertAlmostEqual(lon, UTM_NY_LON, places=7)

    def test_projected_to_mc_nonzero_offsets(self):
        self.assertEqual(wf.osgb_to_mc(UTM_NY_E, UTM_NY_N, UTM_FRAME), UTM_NY_MC)
        self.assertLess(UTM_NY_MC[1], 0)
        e, n = wf.mc_to_osgb(*UTM_NY_MC, UTM_FRAME)
        self.assertAlmostEqual(e, UTM_NY_E, delta=0.5)
        self.assertAlmostEqual(n, UTM_NY_N, delta=0.5)

    def test_batch_matches_singles(self):
        pairs = [
            (UTM_CENTRE_LON, UTM_CENTRE_LAT),
            (UTM_NY_LON, UTM_NY_LAT),
            (-74.0445, 40.6892),
        ]
        batch = wf.transform_epsg(pairs, 'EPSG:4326', 'EPSG:32618')
        singles = [
            wf.transform_epsg([p], 'EPSG:4326', 'EPSG:32618')[0] for p in pairs
        ]
        for (be, bn), (se, sn) in zip(batch, singles):
            self.assertAlmostEqual(be, se, places=6)
            self.assertAlmostEqual(bn, sn, places=6)


class NztmFictionalFrameTest(unittest.TestCase):
    """EPSG:2193 fictional frame: LINZ published example + MC."""

    def test_linz_published_forward(self):
        self.assertEqual(NZTM_FRAME.crs, 'EPSG:2193')
        e, n = wf.transform_epsg(
            [(NZTM_LON, NZTM_LAT)], 'EPSG:4326', 'EPSG:2193',
        )[0]
        self.assertAlmostEqual(e, NZTM_E, delta=0.05)
        self.assertAlmostEqual(n, NZTM_N, delta=0.05)

    def test_linz_reverse_and_roundtrip(self):
        lon, lat = wf.transform_epsg(
            [(NZTM_E, NZTM_N)], 'EPSG:2193', 'EPSG:4326',
        )[0]
        self.assertAlmostEqual(lat, NZTM_LAT, places=6)
        self.assertAlmostEqual(lon, NZTM_LON, places=6)
        e2, n2 = wf.transform_epsg([(lon, lat)], 'EPSG:4326', 'EPSG:2193')[0]
        self.assertAlmostEqual(e2, NZTM_E, places=2)
        self.assertAlmostEqual(n2, NZTM_N, places=2)

    def test_projected_to_mc_and_back(self):
        self.assertEqual(wf.osgb_to_mc(NZTM_E, NZTM_N, NZTM_FRAME), NZTM_MC)
        # Integer MC blocks; reverse recovers the block centre EN, not sub-metre
        # published decimals (same rounding as osgb_to_mc).
        e, n = wf.mc_to_osgb(*NZTM_MC, NZTM_FRAME)
        self.assertEqual(
            (e, n),
            (NZTM_FRAME.offset_e + NZTM_MC[0], NZTM_FRAME.offset_n - NZTM_MC[1]),
        )
        self.assertEqual(wf.osgb_to_mc(e, n, NZTM_FRAME), NZTM_MC)

    def test_batch_empty_and_two_points(self):
        self.assertEqual(wf.transform_epsg([], 'EPSG:4326', 'EPSG:2193'), [])
        pts = [
            (NZTM_LON, NZTM_LAT),
            (174.7762, -41.2865),
        ]
        batch = wf.transform_epsg(pts, 'EPSG:4326', 'EPSG:2193')
        self.assertEqual(len(batch), 2)
        self.assertAlmostEqual(batch[0][0], NZTM_E, delta=0.05)


class NoGdalOrNetworkFixtureTest(unittest.TestCase):
    """Fixtures must not call GDAL or the network."""

    def test_no_subprocess_gdal(self):
        with mock.patch('subprocess.run', side_effect=AssertionError('GDAL forbidden')):
            wf.transform_epsg([(BNG_LON, BNG_LAT)], 'EPSG:4326', 'EPSG:27700')
            wf.transform_epsg([(UTM_NY_LON, UTM_NY_LAT)], 'EPSG:4326', 'EPSG:32618')
            wf.transform_epsg([(NZTM_LON, NZTM_LAT)], 'EPSG:4326', 'EPSG:2193')
            wf.osgb_to_mc(BNG_E, BNG_N, BNG_FRAME)
            wf.osgb_to_mc(UTM_NY_E, UTM_NY_N, UTM_FRAME)
            wf.osgb_to_mc(NZTM_E, NZTM_N, NZTM_FRAME)


if __name__ == '__main__':
    unittest.main()
