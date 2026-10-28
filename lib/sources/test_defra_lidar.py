#!/usr/bin/env python3
"""Unit tests for defra_lidar (no network: tiles are tiny synthetic GeoTIFFs, HTTP is stubbed)."""
from __future__ import annotations

import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parent.parent
for _p in (_ROOT / "shims", _ROOT / "scripts"):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

import contextlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

import defra_lidar as dl


def write_tif(path: Path, grid: np.ndarray, e0: float, n_top: float, nodata: float | None = -3.4028235e38) -> None:
    """Float32 GeoTIFF with the same georeferencing tags as a DEFRA tile (1 m pixels)."""
    im = Image.fromarray(grid.astype(np.float32), mode='F')
    info = {33550: (1.0, 1.0, 0.0), 33922: (0.0, 0.0, 0.0, float(e0), float(n_top), 0.0)}
    if nodata is not None:
        info[42113] = str(nodata)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, tiffinfo=info)


class TileMaths(unittest.TestCase):
    def test_tile_origin_floors_to_5km(self):
        self.assertEqual(dl.tile_origin(441900, 112100), (440000, 110000))
        self.assertEqual(dl.tile_origin(440000, 110000), (440000, 110000))

    def test_tiles_for_spans_edge_and_the_bilinear_metre(self):
        self.assertEqual(dl.tiles_for(441000, 111000, 442000, 112000), [(440000, 110000)])
        self.assertEqual(dl.tiles_for(444000, 111000, 444999, 112000), [(440000, 110000), (445000, 110000)])

    def test_pick_uri_takes_latest_year_at_1m(self):
        cat = {'results': [
            {'product': {'id': 'lidar_composite_dtm'}, 'year': {'id': '2020'}, 'resolution': {'id': '1'}, 'uri': 'old'},
            {'product': {'id': 'lidar_composite_dtm'}, 'year': {'id': '2022'}, 'resolution': {'id': '1'}, 'uri': 'new'},
            {'product': {'id': 'lidar_composite_dtm'}, 'year': {'id': '2024'}, 'resolution': {'id': '2'}, 'uri': 'coarse'},
            {'product': {'id': 'lidar_composite_last_return_dsm'}, 'year': {'id': '2022'}, 'resolution': {'id': '1'}, 'uri': 'dsm'},
        ]}
        self.assertEqual(dl.pick_uri(cat, 'DTM'), 'new?subscription-key=public')
        self.assertEqual(dl.pick_uri(cat, 'DSM'), 'dsm?subscription-key=public')
        self.assertIsNone(dl.pick_uri(cat, 'DSM_FIRST'))


class Sampling(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name)
        # 4 x 4 pixels, north edge at N=104, west edge at E=100. Row 0 is the northernmost row (N 103..104).
        self.dtm = np.array([[10, 10, 10, 10],
                             [10, 12, 14, 10],
                             [10, 10, 10, 10],
                             [10, 10, 10, 10]], dtype=np.float32)
        write_tif(self.cache / 'DTM' / 't.tif', self.dtm, 100, 104)
        self.lidar = dl.DefraLidar(self.cache, download=False)

    def test_read_tile_georeferencing(self):
        t = dl.read_tile(self.cache / 'DTM' / 't.tif')
        self.assertEqual((t.e0, t.n_top, t.n0, t.cell, t.ncols, t.nrows), (100, 104, 100, 1.0, 4, 4))

    def test_pixel_is_value_at_its_south_west_corner(self):
        # pixel row 1, col 1 spans E 101..102, N 102..103 and is 12
        self.assertEqual(float(self.lidar.sample('DTM', 101.0, 102.0)), 12.0)
        self.assertEqual(float(self.lidar.sample('DTM', 102.0, 102.0)), 14.0)

    def test_bilinear_between_corners(self):
        # halfway between corners 12 (101,102) and 14 (102,102), other row corners 10 and 10 (101,101)/(102,101)
        got = float(self.lidar.sample('DTM', 101.5, 102.0))
        self.assertEqual(got, 13.0)
        got = float(self.lidar.sample('DTM', 101.0, 101.5))
        self.assertEqual(got, 11.0)

    def test_geocraft_rounds_each_pixel_to_whole_metres(self):
        write_tif(self.cache / 'DSM' / 't.tif', self.dtm + 0.4, 100, 104)
        write_tif(self.cache / 'DSM' / 'u.tif', self.dtm + 0.4, 200, 104)
        self.assertEqual(float(self.lidar.sample('DSM', 101.0, 102.0)), 12.0)           # 12.4 -> 12
        self.assertAlmostEqual(float(self.lidar.sample('DSM', 101.0, 102.0, geocraft=False)), 12.4, places=5)

    def test_half_rounds_up_like_gdal_int32(self):
        g = self.dtm.copy()
        g[1, 1] = 12.5
        write_tif(self.cache / 'DSM' / 't.tif', g, 100, 104)
        self.assertEqual(float(self.lidar.sample('DSM', 101.0, 102.0)), 13.0)

    def test_outside_every_tile_is_zero_for_geocraft_and_nan_otherwise(self):
        self.assertEqual(float(self.lidar.sample('DTM', 500.0, 500.0)), 0.0)
        self.assertTrue(np.isnan(self.lidar.sample('DTM', 500.0, 500.0, geocraft=False)))

    def test_nodata_pixel_poisons_its_corner(self):
        g = self.dtm.copy()
        g[1, 1] = -3.4028235e38
        write_tif(self.cache / 'DSM' / 't.tif', g, 100, 104)
        lidar = dl.DefraLidar(self.cache, download=False)
        self.assertTrue(np.isnan(lidar.sample('DSM', 101.5, 102.5, geocraft=False)))
        self.assertEqual(float(lidar.sample('DSM', 101.5, 102.5)), 0.0)
        self.assertEqual(float(lidar.sample('DSM', 102.0, 100.0, geocraft=False)), 10.0)

    def test_negative_ground_is_clamped_to_zero_like_geocraft(self):
        write_tif(self.cache / 'DSM' / 't.tif', self.dtm - 20, 100, 104)
        self.assertEqual(float(self.lidar.sample('DSM', 101.0, 101.0)), 0.0)
        self.assertEqual(float(self.lidar.sample('DSM', 101.0, 101.0, geocraft=False)), -10.0)

    def test_corners_across_two_tiles(self):
        write_tif(self.cache / 'DTM' / 'east.tif', np.full((4, 4), 20, dtype=np.float32), 104, 104)
        lidar = dl.DefraLidar(self.cache, download=False)
        # E 103.5 sits between the last column of t (10) and the first column of east (20)
        self.assertEqual(float(lidar.sample('DTM', 103.5, 101.0)), 15.0)

    def test_arrays_are_vectorised(self):
        e = np.array([[101.0, 102.0]])
        n = np.array([[102.0, 102.0]])
        self.assertEqual(self.lidar.sample('DTM', e, n).tolist(), [[12.0, 14.0]])

    def test_ensure_without_download_names_the_missing_tile(self):
        with self.assertRaises(LookupError):
            self.lidar.ensure(441000, 111000, 442000, 112000)


class Fetching(unittest.TestCase):
    def test_fetch_tile_unzips_basename_only_and_caches_catalogue(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            tif = io.BytesIO()
            Image.fromarray(np.zeros((2, 2), dtype=np.float32), mode='F').save(
                tif, format='TIFF', tiffinfo={33550: (1.0, 1.0, 0.0), 33922: (0.0, 0.0, 0.0, 440000.0, 115000.0, 0.0)})
            zbuf = io.BytesIO()
            with zipfile.ZipFile(zbuf, 'w') as z:
                z.writestr('../../evil/SU41sw_DTM_1m.tif', tif.getvalue())
                z.writestr('readme.txt', 'x')
            cat = {'results': [{'product': {'id': 'lidar_composite_dtm'}, 'year': {'id': '2022'},
                                'resolution': {'id': '1'}, 'uri': 'https://example.test/dtm'}]}
            calls = []

            def fake_http(url, data=None, headers=None, tries=3, label=None):
                calls.append(url)
                return json.dumps(cat).encode() if data is not None else zbuf.getvalue()

            lidar = dl.DefraLidar(cache)
            with mock.patch.object(dl, '_http', fake_http), \
                    mock.patch.object(dl, 'catalogue_polygon', lambda e, n: {'type': 'Polygon', 'coordinates': []}):
                path = lidar.fetch_tile('DTM', 440000, 110000)
                self.assertEqual(path, cache / 'DTM' / 'SU41sw_DTM_1m.tif')
                self.assertTrue(path.is_file())
                self.assertEqual(sorted(p.name for p in cache.rglob('*.tif')), ['SU41sw_DTM_1m.tif'])
                lidar.fetch_tile('DTM', 440000, 110000)
                self.assertEqual(sum(1 for c in calls if 'search' in c), 1)      # catalogue asked once
                self.assertEqual(calls[-1], 'https://example.test/dtm?subscription-key=public')

    def test_fetch_tile_returns_none_when_not_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            lidar = dl.DefraLidar(Path(tmp))
            with mock.patch.object(dl, '_http', lambda *a, **k: json.dumps({'results': []}).encode()), \
                    mock.patch.object(dl, 'catalogue_polygon', lambda e, n: {}):
                self.assertIsNone(lidar.fetch_tile('DTM', 0, 0))

    def test_ensure_reports_tile_count_and_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            lidar = dl.DefraLidar(Path(tmp))
            err = io.StringIO()
            with mock.patch.object(dl, '_http', lambda *a, **k: json.dumps({'results': []}).encode()), \
                    mock.patch.object(dl, 'catalogue_polygon', lambda e, n: {}), \
                    contextlib.redirect_stderr(err):
                absent = lidar.ensure(455100, 75100, 456100, 76100)
            out = err.getvalue()
            self.assertEqual(absent, [('DTM', 455000, 75000), ('DSM', 455000, 75000)])
            self.assertIn('2 tile download(s) needed', out)
            self.assertIn('[2/2] DSM 455000E-75000N  none published here', out)
            self.assertIn('fetch finished in', out)

    def test_ensure_is_silent_when_nothing_to_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                dl.DefraLidar(Path(tmp)).ensure(0, 0, 0, 0, models=())
            self.assertEqual(err.getvalue(), '')

    def test_read_with_progress_prints_bytes_and_returns_all(self):
        class Resp:
            headers = {'Content-Length': str(3 << 20)}
            def __init__(self): self.n = 3
            def read(self, size):
                self.n -= 1
                return b'x' * (1 << 20) if self.n >= 0 else b''
        err = io.StringIO()
        with mock.patch.object(dl, 'PROGRESS_EVERY_S', 0), contextlib.redirect_stderr(err):
            data = dl._read_with_progress(Resp(), 'DTM 1E-1N')
        self.assertEqual(len(data), 3 << 20)
        self.assertIn('DTM 1E-1N  3/3 MB', err.getvalue())

    def test_short_read_raises_so_http_retries(self):
        class Resp:
            headers = {'Content-Length': '10'}
            n = 0
            def read(self, size):
                self.n += 1
                return b'xxx' if self.n == 1 else b''
        with self.assertRaises(dl.urllib.error.URLError):
            dl._read_with_progress(Resp(), 'DTM 1E-1N')

    def test_lookup_line_only_when_catalogue_not_cached(self):
        cat = {'results': []}
        with tempfile.TemporaryDirectory() as tmp:
            lidar = dl.DefraLidar(Path(tmp))
            with mock.patch.object(dl, '_http', lambda *a, **k: json.dumps(cat).encode()), \
                    mock.patch.object(dl, 'catalogue_polygon', lambda e, n: {}):
                first, second = io.StringIO(), io.StringIO()
                with contextlib.redirect_stderr(first):
                    lidar.fetch_tile('DTM', 0, 0, step='[1/1]')
                with contextlib.redirect_stderr(second):
                    lidar.fetch_tile('DTM', 0, 0)
            self.assertIn('  [1/1] DTM 0E-0N  looking up', first.getvalue())
            self.assertEqual(second.getvalue(), '')

    def test_error_page_is_not_cached_as_catalogue(self):
        with tempfile.TemporaryDirectory() as tmp:
            lidar = dl.DefraLidar(Path(tmp))
            with mock.patch.object(dl, '_http', lambda *a, **k: b'{"message": "denied"}'), \
                    mock.patch.object(dl, 'catalogue_polygon', lambda e, n: {}):
                with self.assertRaises(RuntimeError):
                    lidar.catalogue(0, 0)
            self.assertEqual(list(Path(tmp).rglob('*.json')), [])


class MinecraftFrame(unittest.TestCase):
    def test_heights_follow_geocraft_block_rules(self):
        from osgb import load_offsets
        o = load_offsets()
        e0, n0 = o['OFFSET_E'] + 100, o['OFFSET_N'] + 50          # block x=100, z=-50
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            # 1 m pixel at (E e0, N n0): DTM 7.6 -> rounds to 8, DSM 15.2 -> rounds to 15
            for model, v in (('DTM', 7.6), ('DSM', 15.2)):
                g = np.full((6, 6), v, dtype=np.float32)
                write_tif(cache / model / 't.tif', g, e0 - 2, n0 + 3)
            h = dl.mc_heights(dl.DefraLidar(cache, download=False), 100, -50, 100, -50)
        self.assertEqual(int(h['dtm'][0, 0]), 8)
        self.assertEqual(int(h['feature'][0, 0]), 7)                       # int(15 - 8)
        self.assertEqual(int(h['diff'][0, 0]), 7)                          # alias of feature (DSM − DTM)
        self.assertIs(h['diff'], h['feature'])
        self.assertEqual(int(h['ground_y'][0, 0]), int(o['SEA_LEVEL']) + 8)
        self.assertEqual(int(h['top_y'][0, 0]), int(o['SEA_LEVEL']) + 15)


if __name__ == '__main__':
    unittest.main()
