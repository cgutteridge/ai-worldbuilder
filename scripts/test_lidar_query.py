#!/usr/bin/env python3
"""Unit tests for lidar_query (synthetic tiles, no network)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import defra_lidar as dl
import lidar_query as q
from osgb import load_offsets
from test_defra_lidar import write_tif


class QueryTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        o = load_offsets()
        self.o = o
        # 16 x 16 m tile: west edge x=98, north edge z=-54, so it holds blocks x 98..113, z -54..-39 with room for bilinear corners
        e0, n_top = o['OFFSET_E'] + 98, o['OFFSET_N'] + 54
        dtm = np.full((16, 16), 20.0, dtype=np.float32)
        dsm = dtm.copy()
        dsm[6:9, 4:7] = 29.0                                   # a 9 m-tall block of 3 x 3 pixels, x 102..104, z -51..-49
        write_tif(Path(tmp.name) / 'DTM' / 't.tif', dtm, e0, n_top)
        write_tif(Path(tmp.name) / 'DSM' / 't.tif', dsm, e0, n_top)
        self.lidar = dl.DefraLidar(Path(tmp.name), download=False)
        self.lidar.ensure = lambda *a, **k: []                  # the tile is tiny and not 5 km aligned

    def run_q(self, area, **kw):
        return q.query(area, lidar=self.lidar, **kw)

    def test_mc_box_resolves_to_both_frames(self):
        a = q.resolve_area(('mc', (100, -50, 103, -47)))
        self.assertEqual(a['mc'], [100, -50, 103, -47])
        self.assertEqual(a['osgb'], [self.o['OFFSET_E'] + 100, self.o['OFFSET_N'] + 47,
                                     self.o['OFFSET_E'] + 103, self.o['OFFSET_N'] + 50])

    def test_cell_matches_cellmath(self):
        a = q.resolve_area(('cell', (1, -1)))
        self.assertEqual(a['mc'], [128, -128, 255, -1])

    def test_layers_are_row_per_z_north_first(self):
        r = self.run_q(('mc', (100, -50, 110, -42)), layers=('dtm', 'dsm', 'feature', 'top_y'), step=1)
        self.assertEqual(r['layers']['dtm'].shape, (9, 11))            # rows = z span, cols = x span
        self.assertTrue((r['layers']['dtm'] == 20).all())
        feat = r['layers']['feature']
        self.assertEqual(int(feat.max()), 9)
        # the raised block is in the tile's rows 3..5 from the north; north is z=-50 so it must be near the top
        self.assertTrue(feat[:5].sum() > feat[5:].sum())
        sea = int(self.o['SEA_LEVEL'])
        self.assertEqual(int(r['layers']['top_y'].max()), sea + 29)

    def test_diff_is_dsm_minus_dtm_same_as_feature(self):
        r = self.run_q(('mc', (100, -50, 110, -42)), layers=('dtm', 'dsm', 'diff', 'feature'), step=1)
        np.testing.assert_array_equal(r['layers']['diff'], r['layers']['feature'])
        self.assertEqual(int(r['layers']['diff'].max()), 9)
        s = q.format_summary(r)
        self.assertEqual(s['diff_share']['>=3m'], s['feature_share']['>=3m'])

    def test_canopy_layer_is_first_minus_last(self):
        first = np.full((16, 16), 20.0, dtype=np.float32)
        first[6:9, 4:7] = 29.0
        first[10:12, 8:10] = 25.0                                 # foliage: first return 5 m above last
        o = self.o
        write_tif(Path(self.lidar.cache) / 'DSM_FIRST' / 't.tif', first, o['OFFSET_E'] + 98, o['OFFSET_N'] + 54)
        r = self.run_q(('mc', (100, -50, 110, -42)), layers=('canopy',), step=1)
        self.assertEqual(float(r['layers']['canopy'].max()), 5.0)
        self.assertEqual(float(r['layers']['canopy'].min()), 0.0)
        self.assertEqual(q.UNITS['canopy'], 'm')

    def test_osgb_frame_rejects_block_layers(self):
        with self.assertRaises(ValueError):
            self.run_q(('osgb', (0, 0, 3, 3)), layers=('ground_y',))

    def test_unknown_layer_named(self):
        with self.assertRaises(ValueError) as c:
            self.run_q(('mc', (100, -50, 101, -49)), layers=('dtmm',))
        self.assertIn('dtmm', str(c.exception))

    def test_automatic_step_caps_each_layer(self):
        r = self.run_q(('mc', (100, -50, 110, -42)), max_cells=20)
        self.assertGreater(r['step'], 1)
        self.assertIn('step', ' '.join(r['notes']))
        t = r['layers']['dtm'][::r['step'], ::r['step']]
        self.assertLessEqual(t.size, 20)

    def test_summary_is_small_and_has_stats(self):
        r = self.run_q(('mc', (100, -50, 110, -42)), layers=('dtm', 'dsm', 'feature'), step=1)
        s = q.format_summary(r)
        self.assertEqual(s['layers']['dtm'], {'min': 20, 'median': 20, 'mean': 20.0, 'max': 20})
        self.assertEqual(s['layers']['feature']['max'], 9)
        self.assertIn('>=3m', s['feature_share'])
        self.assertLess(len(json.dumps(s)), 2000)

    def test_grid_text_labels_axes(self):
        r = self.run_q(('mc', (100, -50, 103, -48)), step=1)
        text = q.format_grid(r)
        self.assertIn('x 100..103', text)
        self.assertIn('z -50..-48', text)
        self.assertIn('dtm (m)', text)
        self.assertEqual(text.count('\n-50 '), 2)                      # one row for z=-50 in each of the two layers

    def test_json_round_trips_and_thins(self):
        r = self.run_q(('mc', (100, -50, 110, -42)), step=2)
        d = json.loads(q.format_json(r))
        self.assertEqual((d['x0'], d['z0'], d['step']), (100, -50, 2))
        self.assertEqual((len(d['layers']['dtm']), len(d['layers']['dtm'][0])), (d['rows'], d['cols']))
        self.assertEqual((d['rows'], d['cols']), (5, 6))

    def test_float_mode_gives_null_outside_data(self):
        r = self.run_q(('mc', (150, -50, 151, -49)), layers=('dtm',), geocraft=False, step=1)
        self.assertEqual(r['no_data_columns'], 4)
        self.assertIn('null', ' '.join(r['notes']))
        self.assertEqual(json.loads(q.format_json(r))['layers']['dtm'], [[None, None], [None, None]])

    def test_npz_and_png_are_written(self):
        r = self.run_q(('mc', (100, -50, 110, -42)), layers=('dtm', 'dsm'), step=1)
        with tempfile.TemporaryDirectory() as tmp:
            p = q.save_npz(r, Path(tmp) / 'a.npz')
            z = np.load(p)
            self.assertEqual(z['dsm'].shape, (9, 11))
            self.assertEqual(int(z['x0']), 100)
            img = q.save_png(r, Path(tmp) / 'a.png')
            self.assertGreater(img.stat().st_size, 100)


if __name__ == '__main__':
    unittest.main()
