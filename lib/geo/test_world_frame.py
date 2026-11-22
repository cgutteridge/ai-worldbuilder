#!/usr/bin/env python3
"""Unit tests for world_frame (no GDAL executable; disposable unit-test world only).

EPSG point conversion is covered with explicit fixtures (independent of any
selected place checkout). OSGB↔MC / cells / WM math use the unit-test frame.

  .venv/bin/python scripts/run_tests.py --only test_world_frame
"""
from __future__ import annotations

import io
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import world_frame as wf

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


class ProjDataDiscoveryTest(unittest.TestCase):
    def test_explicit_proj_data_directory_is_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            grid_dir = Path(tmp) / 'custom-proj'
            grid_dir.mkdir()
            (grid_dir / wf._OSTN15_GRID).touch()
            with mock.patch.object(wf, '_PROJ_DATA_READY', False), \
                    mock.patch.object(wf, '_SYSTEM_PROJ_DATA_CANDIDATES', ()), \
                    mock.patch.dict(wf.os.environ, {'PROJ_DATA': str(grid_dir)}), \
                    mock.patch.object(wf.datadir, 'append_data_dir') as append:
                wf._ensure_proj_data()
                wf._ensure_proj_data()
            append.assert_called_once_with(str(grid_dir))

    def test_legacy_proj_lib_directory_is_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / wf._OSTN15_GRID).touch()
            with mock.patch.object(wf, '_PROJ_DATA_READY', False), \
                    mock.patch.object(wf, '_SYSTEM_PROJ_DATA_CANDIDATES', ()), \
                    mock.patch.dict(wf.os.environ, {'PROJ_DATA': '', 'PROJ_LIB': tmp}), \
                    mock.patch.object(wf.datadir, 'append_data_dir') as append:
                wf._ensure_proj_data()
            append.assert_called_once_with(tmp)


class OsGbMcTest(unittest.TestCase):
    def test_roundtrip(self):
        e, n = wf.mc_to_osgb(5652, -2492)
        x, z = wf.osgb_to_mc(e, n)
        self.assertEqual((x, z), (5652, -2492))

    def test_legacy_offsets_shape(self):
        o = wf.legacy_offsets_dict()
        self.assertEqual(o['OFFSET_E'], 436248)
        self.assertEqual(o['SEA_LEVEL'], 11)
        self.assertNotIn('TOP_OF_WORLD', o)

    def test_legacy_origin_frame_has_migration_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'world-frame.json'
            path.write_text(json.dumps({
                'offsets': {'easting': 0, 'northing': 0},
                'cell': {'origin': [1, 2]},
            }))
            with self.assertRaisesRegex(SystemExit, r'cell\.origin.*cell\.home'):
                wf.load_frame(path)


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


# ---- XYZ tile positions + pixel offsets (#86) ---------------------------------
# Fixed slippy-map fixtures (independent of any place checkout). Integer Chicago
# tile matches the common OSM wiki example for ~41.85°N, 87.65°W at zoom 10.

CHICAGO_LAT, CHICAGO_LON, CHICAGO_Z = 41.85, -87.65, 10
CHICAGO_TX, CHICAGO_TY = 262, 380
CHICAGO_EAST_PX = 175.2177777777688
CHICAGO_SOUTH_PX = 179.12478410147014

SYDNEY_LAT, SYDNEY_LON, SYDNEY_Z = -33.8568, 151.2153, 12
SYDNEY_TX, SYDNEY_TY = 3768, 2457
SYDNEY_EAST_PX = 126.48447999998461
SYDNEY_SOUTH_PX = 208.24015835428145

CHICAGO_NW_LAT = 42.03297433244139
CHICAGO_NW_LON = -87.890625


def _fictional_frame_pinning(
    lat: float, lon: float, mx: int, mz: int,
) -> wf.WorldFrame:
    """WorldFrame whose MC (mx, mz) is the OSGB of (lat, lon) — no place checkout."""
    e, n = wf.wgs84_to_osgb(lat, lon)
    return wf.WorldFrame({
        'crs': 'EPSG:27700',
        'offsets': {'easting': e - mx, 'northing': n + mz},
        'y_offset': 11,
    })


class WmTilePositionForwardTest(unittest.TestCase):
    """Seam: wgs84_to_wm_position (zoom + lat/lon → tile + east_px/south_px)."""

    def test_antimeridian_equator_zoom0_exact(self):
        pos = wf.wgs84_to_wm_position(0.0, -180.0, 0)
        self.assertEqual(pos['zoom'], 0)
        self.assertEqual(pos['x'], 0)
        self.assertEqual(pos['y'], 0)
        self.assertEqual(pos['tile'], '0/0/0')
        self.assertEqual(pos['east_px'], 0.0)
        self.assertEqual(pos['south_px'], 128.0)

    def test_prime_meridian_equator_zoom0_centre(self):
        pos = wf.wgs84_to_wm_position(0.0, 0.0, 0)
        self.assertEqual(pos['tile'], '0/0/0')
        self.assertEqual(pos['east_px'], 128.0)
        self.assertEqual(pos['south_px'], 128.0)

    def test_prime_meridian_equator_zoom1_tile_corner(self):
        pos = wf.wgs84_to_wm_position(0.0, 0.0, 1)
        self.assertEqual(pos['tile'], '1/1/1')
        self.assertEqual(pos['east_px'], 0.0)
        self.assertEqual(pos['south_px'], 0.0)

    def test_chicago_known_tile_and_fractional_offsets(self):
        pos = wf.wgs84_to_wm_position(CHICAGO_LAT, CHICAGO_LON, CHICAGO_Z)
        self.assertEqual(pos['tile'], f'{CHICAGO_Z}/{CHICAGO_TX}/{CHICAGO_TY}')
        self.assertAlmostEqual(pos['east_px'], CHICAGO_EAST_PX, places=9)
        self.assertAlmostEqual(pos['south_px'], CHICAGO_SOUTH_PX, places=9)

    def test_sydney_southern_hemisphere(self):
        pos = wf.wgs84_to_wm_position(SYDNEY_LAT, SYDNEY_LON, SYDNEY_Z)
        self.assertEqual(pos['tile'], f'{SYDNEY_Z}/{SYDNEY_TX}/{SYDNEY_TY}')
        self.assertAlmostEqual(pos['east_px'], SYDNEY_EAST_PX, places=9)
        self.assertAlmostEqual(pos['south_px'], SYDNEY_SOUTH_PX, places=9)

    def test_no_world_frame_required_for_wgs84(self):
        with mock.patch.object(wf, 'load_frame', side_effect=AssertionError('no frame')):
            pos = wf.wgs84_to_wm_position(CHICAGO_LAT, CHICAGO_LON, CHICAGO_Z)
        self.assertEqual(pos['x'], CHICAGO_TX)


class WmTilePositionReverseTest(unittest.TestCase):
    """Seam: wm_position_to_wgs84 (tile + offsets → lat/lon)."""

    def test_tile_centre_zoom0(self):
        ll = wf.wm_position_to_wgs84(0, 0, 0, 128.0, 128.0)
        self.assertAlmostEqual(ll['lat'], 0.0, places=12)
        self.assertAlmostEqual(ll['lon'], 0.0, places=12)

    def test_nw_corner_zero_offsets(self):
        ll = wf.wm_position_to_wgs84(CHICAGO_Z, CHICAGO_TX, CHICAGO_TY, 0.0, 0.0)
        self.assertAlmostEqual(ll['lat'], CHICAGO_NW_LAT, places=12)
        self.assertAlmostEqual(ll['lon'], CHICAGO_NW_LON, places=12)

    def test_forward_reverse_roundtrip_preserves_fraction(self):
        fwd = wf.wgs84_to_wm_position(CHICAGO_LAT, CHICAGO_LON, CHICAGO_Z)
        back = wf.wm_position_to_wgs84(
            fwd['zoom'], fwd['x'], fwd['y'], fwd['east_px'], fwd['south_px'],
        )
        self.assertAlmostEqual(back['lat'], CHICAGO_LAT, places=10)
        self.assertAlmostEqual(back['lon'], CHICAGO_LON, places=10)

    def test_tile_edge_offsets_just_inside(self):
        # Offsets in [0, 256): 255.999 stays in-tile; reverse is finite & in range.
        ll = wf.wm_position_to_wgs84(CHICAGO_Z, CHICAGO_TX, CHICAGO_TY, 255.999, 255.999)
        self.assertTrue(math.isfinite(ll['lat']) and math.isfinite(ll['lon']))
        self.assertGreater(ll['lat'], -wf.WM_MAX_LAT)
        self.assertLess(ll['lat'], wf.WM_MAX_LAT)
        self.assertGreaterEqual(ll['lon'], -180.0)
        self.assertLess(ll['lon'], 180.0)


class WmTilePositionMcTest(unittest.TestCase):
    """Seam: mc ↔ wm position via an explicit fictional frame (no place checkout)."""

    def test_mc_forward_matches_wgs84_on_fictional_frame(self):
        mx, mz = 100, -200
        frame = _fictional_frame_pinning(CHICAGO_LAT, CHICAGO_LON, mx, mz)
        from_mc = wf.mc_to_wm_position(mx, mz, CHICAGO_Z, frame)
        from_ll = wf.wgs84_to_wm_position(CHICAGO_LAT, CHICAGO_LON, CHICAGO_Z)
        self.assertEqual(from_mc['tile'], from_ll['tile'])
        self.assertAlmostEqual(from_mc['east_px'], from_ll['east_px'], places=6)
        self.assertAlmostEqual(from_mc['south_px'], from_ll['south_px'], places=6)

    def test_mc_reverse_uses_existing_block_rounding(self):
        mx, mz = 100, -200
        frame = _fictional_frame_pinning(CHICAGO_LAT, CHICAGO_LON, mx, mz)
        fwd = wf.wgs84_to_wm_position(CHICAGO_LAT, CHICAGO_LON, CHICAGO_Z)
        back = wf.wm_position_to_mc(
            fwd['zoom'], fwd['x'], fwd['y'], fwd['east_px'], fwd['south_px'], frame,
        )
        self.assertEqual(back['x'], mx)
        self.assertEqual(back['z'], mz)
        self.assertIsInstance(back['x'], int)
        self.assertIsInstance(back['z'], int)

    def test_mc_requires_frame_when_none_selected(self):
        with mock.patch.object(
            wf, 'load_frame', side_effect=SystemExit('no world-frame'),
        ):
            with self.assertRaises((SystemExit, ValueError)):
                wf.mc_to_wm_position(0, 0, 10, frame=None)


class WmTilePositionValidationTest(unittest.TestCase):
    """Reject out-of-range zoom, lon, lat, tile indices, and pixel offsets."""

    def test_reject_zoom_out_of_range(self):
        with self.assertRaises(ValueError):
            wf.wgs84_to_wm_position(0.0, 0.0, -1)
        with self.assertRaises(ValueError):
            wf.wgs84_to_wm_position(0.0, 0.0, 31)

    def test_reject_longitude_180_and_below_neg180(self):
        with self.assertRaises(ValueError):
            wf.wgs84_to_wm_position(0.0, 180.0, 1)
        with self.assertRaises(ValueError):
            wf.wgs84_to_wm_position(0.0, -180.1, 1)

    def test_reject_latitude_outside_web_mercator(self):
        with self.assertRaises(ValueError):
            wf.wgs84_to_wm_position(wf.WM_MAX_LAT + 0.01, 0.0, 1)
        with self.assertRaises(ValueError):
            wf.wgs84_to_wm_position(-(wf.WM_MAX_LAT + 0.01), 0.0, 1)

    def test_reject_exact_web_mercator_latitude_limits(self):
        # Exact ±WM_MAX_LAT must not emit invalid tile y / south_px.
        with self.assertRaises(ValueError):
            wf.wgs84_to_wm_position(wf.WM_MAX_LAT, 0.0, 5)
        with self.assertRaises(ValueError):
            wf.wgs84_to_wm_position(-wf.WM_MAX_LAT, 0.0, 5)

    def test_reject_non_finite(self):
        with self.assertRaises(ValueError):
            wf.wgs84_to_wm_position(float('nan'), 0.0, 1)
        with self.assertRaises(ValueError):
            wf.wm_position_to_wgs84(1, 0, 0, float('inf'), 0.0)

    def test_reject_tile_index_out_of_range(self):
        with self.assertRaises(ValueError):
            wf.wm_position_to_wgs84(1, 2, 0, 0.0, 0.0)
        with self.assertRaises(ValueError):
            wf.wm_position_to_wgs84(1, 0, -1, 0.0, 0.0)

    def test_reject_offsets_outside_half_open_unit_interval(self):
        with self.assertRaises(ValueError):
            wf.wm_position_to_wgs84(1, 0, 0, -0.001, 0.0)
        with self.assertRaises(ValueError):
            wf.wm_position_to_wgs84(1, 0, 0, 256.0, 0.0)
        with self.assertRaises(ValueError):
            wf.wm_position_to_wgs84(1, 0, 0, 0.0, 256.0)


class WmTileBatchTest(unittest.TestCase):
    """Seam: convert_wm_batch — ordered JSON items, empty ok, reuses singles."""

    def test_empty_batch(self):
        self.assertEqual(wf.convert_wm_batch([]), [])

    def test_mixed_batch_preserves_order(self):
        items = [
            {'op': 'to-wm', 'zoom': 0, 'wgs84': {'lat': 0.0, 'lon': -180.0}},
            {
                'op': 'from-wm',
                'tile': '0/0/0',
                'east_px': 128.0,
                'south_px': 128.0,
                'as': 'wgs84',
            },
            {'op': 'to-wm', 'zoom': CHICAGO_Z, 'wgs84': {
                'lat': CHICAGO_LAT, 'lon': CHICAGO_LON,
            }},
        ]
        out = wf.convert_wm_batch(items)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0]['tile'], '0/0/0')
        self.assertAlmostEqual(out[1]['lat'], 0.0, places=12)
        self.assertEqual(out[2]['tile'], f'{CHICAGO_Z}/{CHICAGO_TX}/{CHICAGO_TY}')

    def test_batch_mc_with_explicit_frame(self):
        mx, mz = 7, -3
        frame = _fictional_frame_pinning(SYDNEY_LAT, SYDNEY_LON, mx, mz)
        out = wf.convert_wm_batch(
            [{'op': 'to-wm', 'zoom': SYDNEY_Z, 'mc': {'x': mx, 'z': mz}}],
            frame=frame,
        )
        self.assertEqual(out[0]['tile'], f'{SYDNEY_Z}/{SYDNEY_TX}/{SYDNEY_TY}')

    def test_batch_rejects_invalid_item(self):
        with self.assertRaises(ValueError):
            wf.convert_wm_batch([{'op': 'to-wm', 'zoom': 99, 'wgs84': {'lat': 0, 'lon': 0}}])


class WmTileCliTest(unittest.TestCase):
    """CLI: to-wm / from-wm JSON and batch-wm from file or stdin."""

    def test_to_wm_wgs84_json(self):
        buf = io.StringIO()
        with mock.patch('sys.stdout', buf):
            rc = wf.main([
                'to-wm', '--wgs84', f'{CHICAGO_LAT},{CHICAGO_LON}',
                '--zoom', str(CHICAGO_Z),
            ])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data['tile'], f'{CHICAGO_Z}/{CHICAGO_TX}/{CHICAGO_TY}')
        self.assertIn('east_px', data)
        self.assertIn('south_px', data)

    def test_from_wm_json(self):
        buf = io.StringIO()
        with mock.patch('sys.stdout', buf):
            rc = wf.main([
                'from-wm', '--tile', '0/0/0',
                '--east-px', '128', '--south-px', '128', '--as', 'wgs84',
            ])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertAlmostEqual(data['lat'], 0.0, places=12)
        self.assertAlmostEqual(data['lon'], 0.0, places=12)

    def test_batch_wm_file_and_stdin(self):
        payload = [
            {'op': 'to-wm', 'zoom': 0, 'wgs84': {'lat': 0.0, 'lon': 0.0}},
        ]
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as fh:
            json.dump(payload, fh)
            path = fh.name
        buf = io.StringIO()
        with mock.patch('sys.stdout', buf):
            rc = wf.main(['batch-wm', '--file', path])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue())[0]['east_px'], 128.0)

        buf2 = io.StringIO()
        with mock.patch('sys.stdin', io.StringIO(json.dumps(payload))):
            with mock.patch('sys.stdout', buf2):
                rc2 = wf.main(['batch-wm', '--file', '-'])
        self.assertEqual(rc2, 0)
        self.assertEqual(len(json.loads(buf2.getvalue())), 1)

    def test_to_wm_mc_needs_frame_or_world(self):
        # Explicit --frame path with fictional offsets.
        mx, mz = 5, 5
        frame = _fictional_frame_pinning(0.0, 0.0, mx, mz)
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as fh:
            json.dump(frame.data, fh)
            path = fh.name
        buf = io.StringIO()
        with mock.patch('sys.stdout', buf):
            rc = wf.main([
                'to-wm', '--mc', f'{mx},{mz}', '--zoom', '1', '--frame', path,
            ])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue())['tile'], '1/1/1')


if __name__ == '__main__':
    unittest.main()
