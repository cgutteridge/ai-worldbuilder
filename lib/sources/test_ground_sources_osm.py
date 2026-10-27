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


if __name__ == '__main__':
    unittest.main()
