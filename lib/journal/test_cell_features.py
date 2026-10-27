"""Unit tests for cell_features and handoff ring order."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'shims'))

import cell_features as cf  # noqa: E402
import site_handoff as sh  # noqa: E402


def _square(x0, z0, x1, z1):
    return [[(float(x0), float(z0)), (float(x1), float(z0)),
             (float(x1), float(z1)), (float(x0), float(z1)), (float(x0), float(z0))]]


class TestCellFeatures(unittest.TestCase):
    def test_layer_builds_are_not_features(self):
        for n in ('highfield_49_m43_ground', 'highfield_48_m44_trees_undo',
                  'highfield_grounds_mc', 'cell_marker_48_m44', 'highfield_48_m45_foliage'):
            self.assertFalse(cf.is_feature_build(n), n)
        for n in ('highfield_b35', 'highfield_engineering', 'chamberlain_rd_8_58'):
            self.assertTrue(cf.is_feature_build(n), n)

    def test_split_runs_joins_neighbours_only(self):
        a = {'bbox': (0, 0, 8, 8)}
        b = {'bbox': (14, 0, 22, 8)}
        c = {'bbox': (100, 0, 108, 8)}
        runs = cf._split_runs([a, b, c])
        self.assertEqual(sorted(len(g) for g in runs), [1, 2])

    def test_ring_order(self):
        self.assertEqual(sh.ring_cells(1), [(48, -45), (49, -45), (49, -44), (49, -43),
                                            (48, -43), (47, -43), (47, -44), (47, -45)])
        self.assertEqual(len(set(sh.ring_cells(2))), 16)

    def test_footprint_progress_built_vs_unbuilt_area_and_names(self):
        """#142: footprint_progress reports column area and names of unbuilt buildings."""
        # Cell 48,-44 is x 6144..6271, z -5632..-5505. Two 20x20 footprints inside it.
        built_rings = _square(6160, -5600, 6180, -5580)
        unbuilt_rings = _square(6200, -5600, 6220, -5580)
        idx = SimpleNamespace(
            buildings=[
                (built_rings, {'building': 'university', 'name': 'Built Hall', 'id': 'w1'}),
                (unbuilt_rings, {'building': 'university', 'name': 'Empty Hall', 'id': 'w2'}),
            ],
            streets=[],
        )
        # Cover every column of Built Hall; leave Empty Hall untouched.
        from coverage import scan_polygon, cell_bbox
        cb = cell_bbox(48, -44, 48, -44)
        covered = set(scan_polygon(built_rings, cb))
        with mock.patch('cell_features.for_footprint', return_value=[]), \
             mock.patch('cell_features.pictures_for', return_value=[]), \
             mock.patch('cell_features.osm_ref', side_effect=lambda p: p['id']), \
             mock.patch('cell_features.property_label', side_effect=lambda p: p['name']):
            prog = cf.footprint_progress(48, -44, idx=idx, covered=covered)
        self.assertEqual(prog['built_area'], len(covered))
        self.assertGreater(prog['unbuilt_area'], 0)
        self.assertEqual(prog['unbuilt_names'], ['Empty Hall'])
        self.assertEqual(prog['unbuilt'][0]['label'], 'Empty Hall')
        self.assertEqual(prog['unbuilt'][0]['area'], prog['unbuilt_area'])


if __name__ == '__main__':
    unittest.main()
