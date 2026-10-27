"""Regression: ground/decor load the osm_footprints OSGB cache (#113, #127)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ground_sources as GS
import osm_footprints as OF


def _write_cache(cache: Path, name: str, features: list) -> None:
    (cache / name).write_text(json.dumps({'type': 'FeatureCollection', 'features': features}))


class LoadOsmCacheNamesTest(unittest.TestCase):
    def test_underscore_cache_names_yield_roads_and_hedges(self):
        """A world with only ``dir__clip.geojson`` cache files still loads roads/hedges."""
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            _write_cache(cache, 'campus__highways.geojson', [
                {
                    'type': 'Feature',
                    'properties': {
                        'osm_id': '1',
                        'highway': 'residential',
                        'surface': 'asphalt',
                        'name': 'Test Road',
                    },
                    'geometry': {
                        'type': 'LineString',
                        'coordinates': [[10.0, 20.0], [40.0, 20.0]],
                    },
                },
                {
                    'type': 'Feature',
                    'properties': {
                        'osm_id': '2',
                        'barrier': 'hedge',
                    },
                    'geometry': {
                        'type': 'LineString',
                        'coordinates': [[12.0, 30.0], [12.0, 50.0]],
                    },
                },
            ])
            empty_clips = cache / 'no-clips'
            empty_clips.mkdir()
            with mock.patch.object(OF, 'CACHE', cache), \
                    mock.patch.object(OF, '_resolve_vector_map_clips', return_value=empty_clips), \
                    mock.patch('world_frame.osgb_to_mc', side_effect=lambda e, n: (e, n)):
                paths = OF.cached_geojson_paths()
                self.assertEqual([p.name for p in paths], ['campus__highways.geojson'])
                ground = GS.OSMGround((0, 0))
        self.assertEqual(ground.n_features, 2)
        self.assertTrue(any(ground.kind.get(q) == 'tarmac' for q in ground.kind),
                        f'expected tarmac road columns, got kinds={set(ground.kind.values())}')
        self.assertTrue(ground.hedge, 'expected hedge columns from barrier=hedge')


class LoadOsmStreetFurniturePointsTest(unittest.TestCase):
    """Part (1): Point features in the OSGB cache become OSMGround.furniture."""

    def test_point_features_are_classified_into_furniture(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            _write_cache(cache, 'campus__furniture.geojson', [
                {
                    'type': 'Feature',
                    'properties': {'osm_id': '10', 'amenity': 'bench'},
                    'geometry': {'type': 'Point', 'coordinates': [25.2, 40.8]},
                },
                {
                    'type': 'Feature',
                    'properties': {'osm_id': '11', 'highway': 'street_lamp'},
                    'geometry': {'type': 'Point', 'coordinates': [26.1, 41.2]},
                },
                {
                    'type': 'Feature',
                    'properties': {'osm_id': '12', 'natural': 'tree'},
                    'geometry': {'type': 'Point', 'coordinates': [27.0, 42.0]},
                },
            ])
            empty_clips = cache / 'no-clips'
            empty_clips.mkdir()
            with mock.patch.object(OF, 'CACHE', cache), \
                    mock.patch.object(OF, '_resolve_vector_map_clips', return_value=empty_clips), \
                    mock.patch('world_frame.osgb_to_mc', side_effect=lambda e, n: (e, n)):
                ground = GS.OSMGround((0, 0))
        self.assertEqual(sorted(ground.furniture), [
            (25, 40, 'bench'),
            (26, 41, 'street_lamp'),
        ])
        self.assertEqual(ground.trees, [(27, 42)])


def _ground_from_features(features: list) -> GS.OSMGround:
    """Build OSMGround((0, 0)) from a synthetic OSGB-cache Feature list."""
    with tempfile.TemporaryDirectory() as td:
        cache = Path(td)
        _write_cache(cache, 'campus__water.geojson', features)
        empty_clips = cache / 'no-clips'
        empty_clips.mkdir()
        with mock.patch.object(OF, 'CACHE', cache), \
                mock.patch.object(OF, '_resolve_vector_map_clips', return_value=empty_clips), \
                mock.patch('world_frame.osgb_to_mc', side_effect=lambda e, n: (e, n)):
            return GS.OSMGround((0, 0))


class UndergroundWaterwayTest(unittest.TestCase):
    """#162: culverts / covered / below-grade waterways are not open-water columns."""

    def test_culvert_beside_stream_gives_only_stream_columns(self):
        stream_pts = [[10.0, 20.0], [40.0, 20.0]]
        culvert_pts = [[10.0, 50.0], [40.0, 50.0]]
        ground = _ground_from_features([
            {
                'type': 'Feature',
                'properties': {'osm_id': '1', 'waterway': 'stream'},
                'geometry': {'type': 'LineString', 'coordinates': stream_pts},
            },
            {
                'type': 'Feature',
                'properties': {
                    'osm_id': '2',
                    'waterway': 'stream',
                    'tunnel': 'culvert',
                    'layer': '-1',
                },
                'geometry': {'type': 'LineString', 'coordinates': culvert_pts},
            },
        ])
        stream_cols = set(GS.line_cells([(10.0, 20.0), (40.0, 20.0)], 0.8))
        culvert_cols = set(GS.line_cells([(10.0, 50.0), (40.0, 50.0)], 0.8))
        self.assertTrue(ground.water, 'open stream should rasterise to water columns')
        self.assertTrue(ground.water <= stream_cols,
                        f'water {ground.water - stream_cols} not on the open stream')
        self.assertFalse(ground.water & culvert_cols,
                         'culvert (tunnel=culvert, layer=-1) must not become open water')

    def test_covered_and_negative_layer_streams_give_no_water(self):
        ground = _ground_from_features([
            {
                'type': 'Feature',
                'properties': {
                    'osm_id': '3',
                    'waterway': 'stream',
                    'covered': 'yes',
                },
                'geometry': {
                    'type': 'LineString',
                    'coordinates': [[10.0, 20.0], [40.0, 20.0]],
                },
            },
            {
                'type': 'Feature',
                'properties': {
                    'osm_id': '4',
                    'waterway': 'stream',
                    'layer': '-1',
                },
                'geometry': {
                    'type': 'LineString',
                    'coordinates': [[10.0, 50.0], [40.0, 50.0]],
                },
            },
        ])
        self.assertFalse(ground.water,
                         'covered=yes and layer=-1 streams must not rasterise as open water')

    def test_building_passage_highway_still_lands_in_bridge(self):
        """Highway bridge/tunnel/layer columns stay in .bridge (unchanged by #162)."""
        # Non-zero layer is the existing path that puts a highway in .bridge; include
        # tunnel=building_passage so the waterway exception cannot swallow it.
        ground = _ground_from_features([
            {
                'type': 'Feature',
                'properties': {
                    'osm_id': '5',
                    'highway': 'residential',
                    'surface': 'asphalt',
                    'tunnel': 'building_passage',
                    'layer': '1',
                },
                'geometry': {
                    'type': 'LineString',
                    'coordinates': [[10.0, 20.0], [40.0, 20.0]],
                },
            },
        ])
        self.assertTrue(ground.bridge, 'expected building_passage+layer highway in .bridge')
        self.assertFalse(ground.water)
        self.assertFalse(any(ground.kind.get(q) == 'tarmac' for q in ground.bridge),
                         'bridge columns are not ours to surface')


if __name__ == '__main__':
    unittest.main()
