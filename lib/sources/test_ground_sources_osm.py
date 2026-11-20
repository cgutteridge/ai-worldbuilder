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
    def test_projection_refreshes_same_timestamp_source_edits(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / 'clips' / 'fixture' / 'buildings.geojson'
            source.parent.mkdir(parents=True)
            source.write_text('{"type":"FeatureCollection","features":[],"revision":1}')
            cache = root / 'cache'

            def project(argv, **kwargs):
                Path(argv[-2]).write_bytes(Path(argv[-1]).read_bytes())

            with mock.patch.object(OF, 'CACHE', cache), \
                    mock.patch.object(OF.subprocess, 'run', side_effect=project):
                first = OF.cached_geojson_paths(source.parent.parent)[0]
                stamp = source.stat()
                source.write_text(source.read_text().replace('"revision":1', '"revision":2'))
                os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                refreshed = OF.cached_geojson_paths(source.parent.parent)[0]
                self.assertEqual(json.loads(refreshed.read_text())['revision'], 2)
                self.assertEqual(first, refreshed)

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


class LoadOsmTreeRowLinesTest(unittest.TestCase):
    """#157: natural=tree_row lines are kept as polylines for the trees layer."""

    def test_tree_row_line_is_stored_not_rasterised_as_surface(self):
        ground = _ground_from_features([
            {
                'type': 'Feature',
                'properties': {'osm_id': '20', 'natural': 'tree_row'},
                'geometry': {
                    'type': 'LineString',
                    'coordinates': [[10.0, 40.0], [30.0, 40.0]],
                },
            },
        ])
        self.assertEqual(len(ground.tree_rows), 1)
        self.assertEqual(ground.tree_rows[0][0], (10.0, 40.0))
        self.assertEqual(ground.tree_rows[0][-1], (30.0, 40.0))
        self.assertFalse(ground.kind, 'tree_row must not paint a ground kind')


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
        self.assertFalse(ground.water, 'stream lines are water_line, not pond water (#163)')
        self.assertTrue(ground.water_line, 'open stream should rasterise to water_line columns')
        self.assertTrue(ground.water_line <= stream_cols,
                        f'water_line {ground.water_line - stream_cols} not on the open stream')
        self.assertFalse(ground.water_line & culvert_cols,
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
        self.assertFalse(ground.water_line,
                         'covered=yes and layer=-1 streams must not rasterise as open water')
        self.assertFalse(ground.water)

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
        self.assertFalse(ground.water_line)
        self.assertFalse(any(ground.kind.get(q) == 'tarmac' for q in ground.bridge),
                         'bridge columns are not ours to surface')


class BridgeAndTunnelSplitTest(unittest.TestCase):
    """#298: a way's columns are a deck (bridge, layer>0) or a tunnel (tunnel=yes, layer<0); .bridge holds both."""

    @staticmethod
    def _way(**tags):
        return {'type': 'Feature', 'properties': {'osm_id': '7', 'highway': 'footway', **tags},
                'geometry': {'type': 'LineString', 'coordinates': [[10.0, 20.0], [30.0, 20.0]]}}

    def test_bridge_and_raised_layer_are_deck(self):
        for tags in ({'bridge': 'yes', 'layer': '1'}, {'bridge': 'viaduct'}, {'layer': '1'}):
            with self.subTest(tags=tags):
                ground = _ground_from_features([self._way(**tags)])
                self.assertIn((20, 20), ground.deck)
                self.assertFalse(ground.tunnel)
                self.assertEqual(ground.bridge, ground.deck)

    def test_tunnel_and_underground_layer_are_tunnel(self):
        for tags in ({'tunnel': 'yes'}, {'layer': '-1'}, {'tunnel': 'yes', 'layer': '-1'}):
            with self.subTest(tags=tags):
                ground = _ground_from_features([self._way(**tags)])
                self.assertIn((20, 20), ground.tunnel)
                self.assertFalse(ground.deck)
                self.assertEqual(ground.bridge, ground.tunnel)

    def test_a_bridge_wins_over_a_negative_layer(self):
        ground = _ground_from_features([self._way(bridge='yes', layer='-1')])
        self.assertIn((20, 20), ground.deck)
        self.assertFalse(ground.tunnel)


class OutdoorStepsTest(unittest.TestCase):
    """#294: ground-level highway=steps is its own set, apart from bridges."""

    @staticmethod
    def _steps(coords, **tags):
        return {'type': 'Feature', 'properties': {'osm_id': '9', 'highway': 'steps', **tags},
                'geometry': {'type': 'LineString', 'coordinates': coords}}

    def test_ground_level_steps_are_steps_not_bridge(self):
        ground = _ground_from_features([self._steps([[10.0, 20.0], [20.0, 20.0], [20.0, 40.0]])])
        self.assertTrue(ground.steps)
        self.assertFalse(ground.bridge)
        self.assertEqual(ground.steps[(15, 20)], 'x')
        self.assertEqual(ground.steps[(20, 30)], 'z')

    def test_bridge_tunnel_and_layer_steps_stay_in_bridge(self):
        for tags in ({'bridge': 'yes'}, {'tunnel': 'yes'}, {'layer': '1'}):
            with self.subTest(tags=tags):
                ground = _ground_from_features([self._steps([[10.0, 20.0], [20.0, 20.0]], **tags)])
                self.assertFalse(ground.steps)
                self.assertIn((15, 20), ground.bridge)


class WaterPolyVsLineTest(unittest.TestCase):
    """#163: polygon water keeps way refs; streams stay on water_line only."""

    def test_polygon_water_refs_and_stream_separated(self):
        ground = _ground_from_features([
            {
                'type': 'Feature',
                'properties': {'osm_way_id': '141657072', 'natural': 'water'},
                'geometry': {
                    'type': 'Polygon',
                    'coordinates': [[
                        [20.0, 20.0], [28.0, 20.0], [28.0, 28.0], [20.0, 28.0], [20.0, 20.0],
                    ]],
                },
            },
            {
                'type': 'Feature',
                'properties': {'osm_id': '999', 'waterway': 'stream'},
                'geometry': {
                    'type': 'LineString',
                    'coordinates': [[80.0, 10.0], [80.0, 100.0]],
                },
            },
        ])
        self.assertTrue(ground.water)
        self.assertTrue(ground.water_line)
        self.assertFalse(ground.water & ground.water_line)
        self.assertTrue(ground.water_refs)
        self.assertEqual(set(ground.water_refs.values()), {'w141657072'})
        self.assertEqual(set(ground.water_refs), set(ground.water))


_AREA_RING = [[
    [20.0, 20.0], [28.0, 20.0], [28.0, 28.0], [20.0, 28.0], [20.0, 20.0],
]]
_AREA_SAMPLE = (24, 24)  # column centre inside _AREA_RING


def _area_feature(osm_id: str, tags: dict) -> dict:
    """One OSGB-cache polygon Feature with the given OSM tags."""
    props = {'osm_id': osm_id, **tags}
    return {
        'type': 'Feature',
        'properties': props,
        'geometry': {'type': 'Polygon', 'coordinates': _AREA_RING},
    }


class GroundAreaTagsTest(unittest.TestCase):
    """#158: leisure / landuse / natural areas map to kind + priority; surface/sport choose floor."""

    # One row per tag (or tag+surface/sport case): (label, tags, expected_kind, expected_pri_key).
    CASES = [
        ('leisure=pitch default', {'leisure': 'pitch'}, 'grass', 'pitch'),
        ('leisure=pitch surface=asphalt', {'leisure': 'pitch', 'surface': 'asphalt'}, 'tarmac', 'pitch'),
        ('leisure=pitch surface=tartan', {'leisure': 'pitch', 'surface': 'tartan'}, 'tarmac', 'pitch'),
        ('leisure=pitch surface=grass', {'leisure': 'pitch', 'surface': 'grass'}, 'grass', 'pitch'),
        ('leisure=pitch sport=tennis', {'leisure': 'pitch', 'sport': 'tennis'}, 'tarmac', 'pitch'),
        ('leisure=pitch sport=soccer', {'leisure': 'pitch', 'sport': 'soccer'}, 'grass', 'pitch'),
        ('leisure=playground', {'leisure': 'playground'}, 'paving', 'pitch'),
        ('leisure=playground surface=asphalt',
         {'leisure': 'playground', 'surface': 'asphalt'}, 'tarmac', 'pitch'),
        ('leisure=track', {'leisure': 'track'}, 'tarmac', 'pitch'),
        ('leisure=track surface=tartan', {'leisure': 'track', 'surface': 'tartan'}, 'tarmac', 'pitch'),
        ('landuse=construction', {'landuse': 'construction'}, 'dirt', 'pitch'),
        ('landuse=allotments', {'landuse': 'allotments'}, 'dirt', 'grass'),
        ('natural=wetland', {'natural': 'wetland'}, 'grass', 'grass'),
        ('natural=grassland', {'natural': 'grassland'}, 'grass', 'grass'),
        ('natural=shrubbery', {'natural': 'shrubbery'}, 'grass', 'wood'),
    ]

    def test_each_area_tag_maps_kind_and_priority(self):
        for i, (label, tags, want_kind, pri_key) in enumerate(self.CASES):
            with self.subTest(label=label):
                ground = _ground_from_features([_area_feature(str(100 + i), tags)])
                self.assertEqual(ground.kind.get(_AREA_SAMPLE), want_kind,
                                 f'{label}: kind at {_AREA_SAMPLE}')
                self.assertEqual(ground.pri.get(_AREA_SAMPLE), GS.PRI[pri_key],
                                 f'{label}: priority at {_AREA_SAMPLE}')

    def test_built_landuse_stays_unread(self):
        """landuse=residential/retail/commercial/industrial must not paint under houses."""
        for lu in ('residential', 'retail', 'commercial', 'industrial'):
            with self.subTest(landuse=lu):
                ground = _ground_from_features([_area_feature('200', {'landuse': lu})])
                self.assertFalse(ground.kind, f'landuse={lu} must leave kind empty')
                self.assertFalse(ground.pri, f'landuse={lu} must leave pri empty')
            with self.subTest(landuse=lu, surface='asphalt'):
                ground = _ground_from_features([
                    _area_feature('201', {'landuse': lu, 'surface': 'asphalt'}),
                ])
                self.assertFalse(ground.kind,
                                 f'landuse={lu}+surface must still leave kind empty')


class SurfaceCarParkTest(unittest.TestCase):
    """#338: a surface amenity=parking polygon is tarmac at PRI['parking']; covered parking is not ground paint."""

    SQUARE = [[[10.0, 10.0], [30.0, 10.0], [30.0, 30.0], [10.0, 30.0], [10.0, 10.0]]]

    def ground(self, tags):
        return _ground_from_features([{'type': 'Feature', 'properties': dict(tags, osm_id='7'),
                                       'geometry': {'type': 'Polygon', 'coordinates': self.SQUARE}}])

    def test_surface_car_park_is_tarmac_at_parking_priority(self):
        for tags in ({'amenity': 'parking'}, {'amenity': 'parking', 'parking': 'surface'}):
            g = self.ground(tags)
            self.assertTrue(g.kind)
            self.assertEqual({g.kind[q] for q in g.kind}, {'tarmac'})
            self.assertEqual({g.pri[q] for q in g.kind}, {GS.PRI['parking']})
            self.assertEqual(g.soft, set(g.kind))
            self.assertEqual(len(g.parking_areas), 1)

    def test_multi_storey_underground_and_rooftop_parking_paint_nothing(self):
        for kind in ('multi-storey', 'underground', 'rooftop'):
            g = self.ground({'amenity': 'parking', 'parking': kind})
            self.assertFalse(g.kind, kind)
            self.assertFalse(g.soft, kind)
            self.assertFalse(g.parking_areas, kind)

    def test_car_park_through_classify_stays_tarmac_under_mosaic_lawn_colour(self):
        import ground_layer as gl
        g = self.ground({'amenity': 'parking'})
        cols = sorted(g.kind)
        live = {q: (50, 'stone') for q in cols}
        for colour in ((0.64, 0.60, 0.54), (0.84, 0.76, 0.69), (0.44, 0.39, 0.27)):
            kind, _ = gl.classify((0, 0), live, g, {q: colour for q in cols})
            self.assertEqual({kind[q] for q in cols}, {'tarmac'}, colour)


class WayLinesForFurnitureTest(unittest.TestCase):
    """#260: OSMGround keeps each mapped carriageway and path as a line for lamp and bench inference."""

    @staticmethod
    def line(osm_id, props, pts):
        return {'type': 'Feature', 'properties': dict(props, osm_id=osm_id),
                'geometry': {'type': 'LineString', 'coordinates': pts}}

    def test_roads_and_paths_are_recorded_with_class_width_and_tags(self):
        ground = _ground_from_features([
            self.line('1', {'highway': 'residential', 'lit': 'yes', 'lanes': '2'}, [[10.0, 20.0], [60.0, 20.0]]),
            self.line('2', {'highway': 'footway', 'footway': 'sidewalk'}, [[10.0, 24.0], [60.0, 24.0]]),
            self.line('3', {'highway': 'path'}, [[10.0, 40.0], [60.0, 40.0]]),
            self.line('4', {'highway': 'residential', 'bridge': 'yes'}, [[10.0, 80.0], [60.0, 80.0]]),
            self.line('5', {'highway': 'steps'}, [[10.0, 90.0], [20.0, 90.0]]),
        ])
        roads = {(w['highway'], w['lit']): w for w in ground.road_lines}
        self.assertEqual(list(roads), [('residential', 'yes')])
        self.assertEqual(roads[('residential', 'yes')]['half'], GS.LANE_WIDTH)        # two lanes
        self.assertEqual(roads[('residential', 'yes')]['points'], [(10.0, 20.0), (60.0, 20.0)])
        paths = {w['highway']: w for w in ground.path_lines}
        self.assertEqual(sorted(paths), ['footway', 'path'])
        self.assertEqual(paths['footway']['footway'], 'sidewalk')
        self.assertEqual(paths['path']['half'], GS.PATH_WIDTH['path'] / 2)
        self.assertIsNone(paths['path']['lit'])                                        # absent stays None


if __name__ == '__main__':
    unittest.main()
