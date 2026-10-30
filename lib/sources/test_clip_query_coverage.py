"""Query coverage comes from extract bounds, independent of returned features."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cellmath
import osm_footprints as OF


class ClipQueryCoverageTest(unittest.TestCase):
    def setUp(self):
        cellmath.reload_grid({'cell': {'size': 128, 'home': [0, 0]}})
        self.addCleanup(cellmath.reload_grid)

    def test_empty_overlapping_clips_count_query_union_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            place = root / 'survey'
            place.mkdir()
            for name in ('buildings-bbox-0-0-63-127', 'buildings-bbox-32-0-95-127'):
                (place / f'{name}.geojson').write_text(json.dumps({'features': []}))
            with mock.patch.object(OF, '_projected', side_effect=lambda p: p):
                idx = OF.OSMIndex(root)
            self.assertEqual(idx.clip_coverage((0, 0)), 0.75)
            self.assertTrue(idx.covered(0, 127))
            self.assertFalse(idx.covered(96, 0))

    def test_radius_negative_coordinates_and_folder_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            place = root / 'r10m-at--64--64'
            place.mkdir()
            (place / 'buildings.geojson').write_text('{"features": []}')
            with mock.patch.object(OF, '_projected', side_effect=lambda p: p):
                idx = OF.OSMIndex(root)
            self.assertEqual(idx.clip_coverage((-1, -1)), 441 / 16384)
            self.assertTrue(idx.covered(-74, -54))
            self.assertFalse(idx.covered(-75, -64))

    def test_building_extent_does_not_change_query_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            place = root / 'survey'
            place.mkdir()
            ring = [[-10, -10], [80, -10], [80, 30], [-10, 30], [-10, -10]]
            (place / 'buildings-bbox-0-0-63-127.geojson').write_text(json.dumps({
                'features': [{'geometry': {'type': 'Polygon', 'coordinates': [ring]},
                              'properties': {'building': 'yes', 'osm_id': '1'}}],
            }))
            with mock.patch.object(OF, '_projected', side_effect=lambda p: p), \
                    mock.patch('world_frame.osgb_to_mc', side_effect=lambda x, z: (x, z)):
                idx = OF.OSMIndex(root)
            self.assertEqual(idx.clip_coverage((0, 0)), 0.5)
            self.assertTrue(idx.covered(10, 100))
            self.assertFalse(idx.covered(70, 10))

    def test_cell_extract_and_unknown_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            place = root / 'survey'
            place.mkdir()
            for name in ('buildings-cell-0--128', 'unknown'):
                (place / f'{name}.geojson').write_text('{"features": []}')
            # A custom campus-boundary query did not query buildings, even though
            # it used the multipolygons layer and has a much larger bbox.
            (place / 'multipolygons-r600m-at-0-0.geojson').write_text('{"features": []}')
            with mock.patch.object(OF, '_projected', side_effect=lambda p: p):
                idx = OF.OSMIndex(root)
            self.assertEqual(idx.clip_coverage((0, -1)), 1)
            self.assertEqual(idx.clip_coverage((0, 0)), 0)
