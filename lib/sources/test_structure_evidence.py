"""Structure evidence: bridges, footbridges, culverts and tunnels with deck heights (#450).

The footbridge fixture is a recorded window of the railway trial extracts,
OpenStreetMap contributors, ODbL. Everything else is synthetic.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import structure_evidence as se
import structure_fixtures as fixtures
import world_frame as wf

FRAME = wf.WorldFrame({
    'crs': 'EPSG:27700',
    'offsets': {'easting': 436248, 'northing': 109608},
    'y_offset': 11,
    'cell': {'size': 128, 'home': [48, -44]},
})


def way(osm_id, points, **tags):
    return {'osm_id': str(osm_id), 'tags': tags, 'points': [tuple(p) for p in points]}


def ground(width=200, depth=200, terrain=10):
    t = np.full((width, depth), terrain, dtype=np.int16)
    return {'x0': 0, 'z0': 0, 'terrain': t, 'h': t.copy(),
            'kind': np.zeros(t.shape, dtype=np.int16), 'have': np.ones(t.shape, dtype=bool)}


def raise_deck(g, columns, top, kind=4):
    for x, z in columns:
        g['h'][x, z] = top
        g['kind'][x, z] = kind


def only(structures, osm_id):
    return next(s for s in structures if s.osm_id == str(osm_id))


class HeightTests(unittest.TestCase):
    def deck_ground(self, top=16, spread=0):
        g = ground()
        probe = se.build_structures([way(1, [(20, 50), (80, 50)], highway='primary', bridge='yes',
                                         layer='1')])[0]
        for n, p in enumerate(sorted(probe.deck)):
            raise_deck(g, [p], top + (n % (spread + 1)))
        return g

    def test_road_over_rail_is_measured_and_records_the_crossing(self):
        ways = [way(1, [(20, 50), (80, 50)], highway='primary', bridge='yes', layer='1'),
                way(2, [(50, 10), (50, 90)], railway='rail')]
        s = only(se.build_structures(ways, self.deck_ground()), 1)
        self.assertEqual((s.kind, s.form, s.carrier, s.layer, s.layer_tagged),
                         ('bridge', 'road_bridge', 'road', 1, True))
        self.assertEqual(s.height.status, se.MEASURED)
        self.assertEqual((s.height.top, s.height.underside, s.height.uncertainty), (16, 16, 0))
        self.assertGreaterEqual(s.height.columns, se.MEASURED_MIN_COLUMNS)
        self.assertEqual([(c.carrier, c.osm_id, c.layer) for c in s.crosses], [('rail', '2', 0)])
        self.assertAlmostEqual(s.crosses[0].at[0], 50)
        self.assertIn('osm:way/2', s.refs)

    def test_underside_uses_the_lowest_column_and_the_deck_thickness(self):
        g = self.deck_ground(top=16, spread=1)
        ways = [way(1, [(20, 50), (80, 50)], highway='primary', bridge='yes')]
        h = only(se.build_structures(ways, g), 1).height
        self.assertEqual((h.status, h.top, h.underside, h.uncertainty), (se.MEASURED, 16, 16, 1))
        self.assertEqual(h.underside, 16 - se.DECK_THICKNESS + 1)

    def test_rail_over_water(self):
        ways = [way(5, [(20, 50), (80, 50)], railway='rail', bridge='viaduct', layer='1'),
                way(6, [(50, 10), (50, 90)], waterway='river')]
        g = ground()
        probe = se.build_structures(ways)[0]
        raise_deck(g, probe.deck, 14)
        s = only(se.build_structures(ways, g), 5)
        self.assertEqual((s.form, s.carrier, s.style), ('rail_bridge', 'rail', 'viaduct'))
        self.assertEqual([(c.carrier, c.osm_id) for c in s.crosses], [('water', '6')])
        self.assertEqual(s.height.status, se.MEASURED)

    def test_footbridge_form_and_cells(self):
        ways = [way(7, [(100, 100), (160, 100)], highway='footway', bridge='yes', layer='2')]
        s = se.build_structures(ways, cell_size=128)[0]
        self.assertEqual((s.form, s.carrier, s.layer), ('footbridge', 'path', 2))
        self.assertEqual(s.cells, ((0, 0), (1, 0)))
        self.assertEqual((s.start, s.end), ((100, 100), (160, 100)))

    def test_culvert_and_tunnel_have_unknown_bore_height(self):
        ways = [way(1, [(10, 10), (40, 10)], waterway='stream', tunnel='culvert'),
                way(2, [(10, 50), (90, 50)], railway='rail', tunnel='yes', layer='-2'),
                way(3, [(50, 10), (50, 90)], highway='residential')]
        out = se.build_structures(ways, ground())
        culvert, tunnel = only(out, 1), only(out, 2)
        self.assertEqual((culvert.kind, culvert.form), ('tunnel', 'culvert'))
        self.assertEqual((tunnel.kind, tunnel.form, tunnel.layer), ('tunnel', 'rail_tunnel', -2))
        for s in (culvert, tunnel):
            self.assertEqual((s.height.status, s.height.top, s.height.underside),
                             (se.UNKNOWN, None, None))
            self.assertIn('bore', s.height.reason)
        self.assertEqual([(c.carrier, c.osm_id) for c in tunnel.crosses], [('road', '3')])
        self.assertEqual((tunnel.start, tunnel.end), ((10, 50), (90, 50)))

    def test_untagged_layer_defaults_and_is_flagged(self):
        out = se.build_structures([way(1, [(0, 0), (30, 0)], highway='service', bridge='yes'),
                                   way(2, [(0, 9), (30, 9)], highway='service', tunnel='yes')])
        self.assertEqual([(s.layer, s.layer_tagged) for s in out], [(1, False), (-1, False)])

    def test_same_layer_way_is_a_junction_not_a_crossing(self):
        ways = [way(1, [(20, 50), (80, 50)], highway='primary', bridge='yes', layer='1'),
                way(2, [(50, 10), (50, 90)], highway='primary', layer='1')]
        self.assertEqual(se.build_structures(ways)[0].crosses, ())

    def test_overlapping_decks_separate_by_layer(self):
        ways = [way(1, [(20, 50), (80, 50)], highway='primary', bridge='yes', layer='1'),
                way(2, [(50, 20), (50, 80)], railway='rail', bridge='yes', layer='2')]
        probe = se.build_structures(ways)
        low, high = probe[0], probe[1]
        shared = low.deck & high.deck
        self.assertTrue(shared)
        g = ground()
        raise_deck(g, high.deck, 20)          # the upper deck is what lidar sees
        raise_deck(g, low.deck - shared, 14)
        out = se.build_structures(ways, g)
        low, high = only(out, 1), only(out, 2)
        self.assertEqual((low.height.status, low.height.top), (se.MEASURED, 14))
        self.assertEqual((high.height.status, high.height.top), (se.MEASURED, 20))
        self.assertEqual([(c.osm_id, c.structure_id) for c in low.crosses], [])
        self.assertEqual([(c.osm_id, c.structure_id) for c in high.crosses], [('1', 'structure-1')])
        self.assertIn('shared with a higher deck', low.height.reason)

    def test_lower_deck_with_only_the_upper_deck_visible_is_unknown(self):
        ways = [way(1, [(20, 50), (80, 50)], highway='primary', bridge='yes', layer='1'),
                way(2, [(50, 20), (50, 80)], railway='rail', bridge='yes', layer='2')]
        probe = se.build_structures(ways)
        g = ground()
        raise_deck(g, probe[1].deck, 20)
        low = only(se.build_structures(ways, g), 1)
        self.assertEqual((low.height.status, low.height.top), (se.UNKNOWN, None))

    def test_missing_lidar_and_no_structure_columns_stay_unknown(self):
        ways = [way(1, [(20, 50), (80, 50)], highway='primary', bridge='yes')]
        self.assertEqual(se.build_structures(ways)[0].height.status, se.UNKNOWN)
        self.assertEqual(se.build_structures(ways, ground())[0].height.status, se.UNKNOWN)
        g = ground()
        g['have'][:] = False
        self.assertEqual(se.build_structures(ways, g)[0].height.status, se.UNKNOWN)

    def test_tree_and_roof_columns_are_never_a_deck(self):
        ways = [way(1, [(20, 50), (80, 50)], highway='primary', bridge='yes')]
        probe = se.build_structures(ways)[0]
        for kind in (1, 2, 3):
            g = ground()
            raise_deck(g, probe.deck, 20, kind=kind)
            self.assertEqual(se.build_structures(ways, g)[0].height.status, se.UNKNOWN, kind)

    def test_low_structure_columns_are_not_a_deck(self):
        ways = [way(1, [(20, 50), (80, 50)], highway='primary', bridge='yes')]
        probe = se.build_structures(ways)[0]
        g = ground()
        raise_deck(g, probe.deck, 11)          # one block over the DTM
        self.assertEqual(se.build_structures(ways, g)[0].height.status, se.UNKNOWN)

    def test_sparse_structure_columns_are_inferred_at_the_lowest(self):
        ways = [way(1, [(20, 50), (80, 50)], highway='primary', bridge='yes')]
        probe = se.build_structures(ways)[0]
        cols = sorted(probe.deck)
        g = ground()
        raise_deck(g, cols[:3], 17)
        raise_deck(g, cols[3:4], 16)
        h = se.build_structures(ways, g)[0].height
        self.assertEqual((h.status, h.top, h.columns, h.uncertainty), (se.INFERRED, 16, 4, 1))
        self.assertIn('only 4', h.reason)

    def test_conflicting_heights_are_inferred_not_measured(self):
        ways = [way(1, [(20, 50), (80, 50)], highway='primary', bridge='yes')]
        probe = se.build_structures(ways)[0]
        cols = sorted(probe.deck)
        g = ground()
        raise_deck(g, cols[::2], 16)
        raise_deck(g, cols[1::2], 22)
        h = se.build_structures(ways, g)[0].height
        self.assertEqual((h.status, h.top, h.uncertainty), (se.INFERRED, 16, 6))
        self.assertIn('disagree', h.reason)

    def test_single_structure_column_is_unknown(self):
        ways = [way(1, [(20, 50), (80, 50)], highway='primary', bridge='yes')]
        probe = se.build_structures(ways)[0]
        g = ground()
        raise_deck(g, [sorted(probe.deck)[0]], 16)
        self.assertEqual(se.build_structures(ways, g)[0].height.status, se.UNKNOWN)

    def test_non_structures_and_unrelated_ways_are_ignored(self):
        ways = [way(1, [(0, 0), (30, 0)], highway='service'),
                way(2, [(0, 5), (30, 5)], highway='service', bridge='no'),
                way(3, [(0, 9), (30, 9)], building='yes', bridge='yes')]
        self.assertEqual(se.build_structures(ways), ())

    def test_output_is_deterministic_and_json_able(self):
        ways = [way(9, [(20, 50), (80, 50)], highway='primary', bridge='yes'),
                way(2, [(50, 10), (50, 90)], railway='rail', tunnel='yes')]
        g = self.deck_ground()
        a, b = se.build_structures(ways, g), se.build_structures(list(reversed(ways)), g)
        self.assertEqual(a, b)
        self.assertEqual([s.osm_id for s in a], ['2', '9'])
        text = json.dumps([se.to_dict(s) for s in a])
        self.assertEqual(json.loads(text)[1]['height']['status'], se.MEASURED)
        self.assertEqual(se.by_osm_id(a)['9'].id, 'structure-9')


class ReaderTests(unittest.TestCase):
    def test_read_map_ways_converts_and_splits_multi_part_ways(self):
        def coords(points):
            out = []
            for x, z in points:
                east, north = wf.mc_to_osgb(x, z, FRAME)
                lat, lon = wf.osgb_to_wgs84(east, north)
                out.append([lon, lat])
            return out
        feats = [
            {'type': 'Feature', 'properties': {'osm_id': '5', 'highway': 'footway',
                                               'other_tags': '"bridge"=>"yes","layer"=>"1"'},
             'geometry': {'type': 'LineString', 'coordinates': coords([(0, 0), (30, 0)])}},
            {'type': 'Feature', 'properties': {'osm_id': '6', 'waterway': 'stream'},
             'geometry': {'type': 'MultiLineString',
                          'coordinates': [coords([(0, 9), (9, 9)]), coords([(20, 9), (30, 9)])]}},
            {'type': 'Feature', 'properties': {'osm_id': '7', 'building': 'yes'},
             'geometry': {'type': 'LineString', 'coordinates': coords([(0, 0), (1, 1)])}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ways.geojson'
            path.write_text(json.dumps({'type': 'FeatureCollection', 'features': feats}))
            got = se.read_map_ways(path, FRAME)
            path.write_text('[]')
            with self.assertRaises(ValueError):
                se.read_map_ways(path, FRAME)
        self.assertEqual([w['osm_id'] for w in got], ['5', '6#0', '6#1'])
        self.assertEqual(got[0]['points'], [(0, 0), (30, 0)])
        self.assertEqual(got[0]['tags']['bridge'], 'yes')


class RecordedFootbridgeTests(unittest.TestCase):
    """OSM way 28019377: lidar shows no deck, so the height must not be measured."""

    def setUp(self):
        self.data = fixtures.load_footbridge()

    def structures(self):
        ways = list(self.data['roads']) + list(self.data['rail_ways'])
        return se.build_structures(ways, self.data['ground'])

    def test_fixture_matches_the_diagnosed_columns(self):
        g = self.data['ground']
        for (x, z), (terrain, h, kind) in {(6872, -4194): (15, 16, 0), (6875, -4193): (15, 19, 2),
                                           (6866, -4196): (15, 22, 2)}.items():
            i, j = x - g['x0'], z - g['z0']
            self.assertEqual((g['terrain'][i, j], g['h'][i, j], g['kind'][i, j]), (terrain, h, kind))
        self.assertEqual(self.data['window'], (6820, -4240, 6930, -4150))

    def test_footbridge_is_not_a_measured_16(self):
        s = only(self.structures(), fixtures.FOOTBRIDGE_ID)
        self.assertEqual((s.kind, s.form, s.layer), ('bridge', 'footbridge', 1))
        self.assertIn(s.height.status, (se.UNKNOWN, se.INFERRED))
        self.assertNotEqual(s.height.status, se.MEASURED)
        self.assertIsNone(s.height.top)
        self.assertIsNone(s.height.underside)
        self.assertEqual(s.height.columns, 0)
        self.assertEqual(s.cells, ((53, -33),))
        self.assertEqual({c.carrier for c in s.crosses}, {'rail'})
        self.assertEqual(len(s.crosses), 4)
        self.assertGreaterEqual(len(s.deck), 90)

    def test_other_bridges_and_passages_in_the_window_are_listed(self):
        out = {s.osm_id: s for s in self.structures()}
        self.assertEqual(out['178471313'].kind, 'tunnel')
        self.assertEqual(out['997542160'].kind, 'bridge')
        self.assertTrue(all(s.height.status != se.MEASURED for s in out.values()
                            if s.kind == 'tunnel'))


if __name__ == '__main__':
    unittest.main()
