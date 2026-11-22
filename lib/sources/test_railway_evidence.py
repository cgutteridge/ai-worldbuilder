"""Railway evidence: class, chains, pairs, crossings and spans (#379).

Fixtures are a trimmed copy of a trial railway extract
(``rail_trial_256``), OpenStreetMap contributors, ODbL. See ATTRIBUTION.txt.
"""
from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import world_frame as wf
from ground_sources import line_cells, road_width

import railway_evidence as rv

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'railway'
RAIL = FIXTURES / 'railway-bbox-6650--4800-7500--3800.geojson'
POINTS = FIXTURES / 'points-bbox-6650--4800-7500--3800.geojson'
BOX = (6650, -4800, 7500, -3800)
FRAME = wf.WorldFrame({
    'crs': 'EPSG:27700',
    'offsets': {'easting': 436248, 'northing': 109608},
    'y_offset': 11,
    'cell': {'size': 128, 'home': [48, -44]},
})

# Hand chains from the trial, plus the ways that continue past the study clip.
BRANCH_U = {
    '880996761', '19071533', '236572196', '236572197', '24299041', '880996764',
}
BRANCH_V = {
    '880996762', '236572191', '236572195', '236572190', '236572194', '880996763',
}
MAIN_P = {'236574344'}
MAIN_Q = {'237029734', '510881165'}


def _way(osm_id: str, points, **tags) -> rv.Way:
    """A through or decor way. ``railway`` defaults to rail."""
    flat = {'railway': 'rail', **tags}
    return rv.Way(osm_id, rv.classify(flat), tuple(points), tuple(flat.items()))


def _line_feature(points, **tags) -> dict:
    """A WGS84 line feature that round-trips through the trial frame."""
    coords = []
    for x, z in points:
        east, north = wf.mc_to_osgb(x, z, FRAME)
        lat, lon = wf.osgb_to_wgs84(east, north)
        coords.append([lon, lat])
    props = {'railway': tags.pop('railway', 'rail'), 'osm_id': tags.pop('osm_id', '1')}
    if tags:
        props['other_tags'] = ','.join(f'"{k}"=>"{v}"' for k, v in tags.items())
    return {
        'type': 'Feature',
        'properties': props,
        'geometry': {'type': 'LineString', 'coordinates': coords},
    }


def _write(path: Path, features: list) -> None:
    path.write_text(json.dumps({'type': 'FeatureCollection', 'features': features}))


class ClassifyAndChainTests(unittest.TestCase):
    """Rules that do not need the trial extract."""

    def test_through_ignores_usage_and_service_is_decor(self):
        self.assertEqual(rv.classify({'railway': 'rail'}), rv.THROUGH)
        self.assertEqual(rv.classify({'railway': 'rail', 'usage': 'industrial'}), rv.THROUGH)
        self.assertEqual(rv.classify({'railway': 'rail', 'usage': 'main'}), rv.THROUGH)
        self.assertEqual(
            rv.classify({'railway': 'rail', 'service': 'siding', 'usage': 'main'}),
            rv.DECOR,
        )
        for railway in ('miniature', 'disused', 'turntable', 'switch'):
            self.assertEqual(rv.classify({'railway': railway}), rv.DECOR, railway)
        self.assertEqual(rv.classify({'railway': 'rail', 'service': 'crossover'}), rv.DECOR)

    def test_reversed_way_joins_end_to_end(self):
        """A way drawn backwards still chains when the ends meet."""
        lines = rv.build_lines([
            _way('1', [(0, 0), (10, 0)]),
            _way('2', [(20, 0), (10, 0)]),
        ])
        self.assertEqual(len(lines), 1)
        poly = lines[0].tracks[0]
        self.assertEqual(poly[0], (0, 0))
        self.assertEqual(poly[-1], (20, 0))
        self.assertEqual(poly.count((10, 0)), 1)
        self.assertEqual(lines[0].track_way_ids, (('1', '2'),))

    def test_way_with_no_usage_tag_still_chains(self):
        lines = rv.build_lines([
            _way('1', [(0, 0), (10, 0)]),
            _way('2', [(10, 0), (30, 0)], usage='main'),
        ])
        self.assertEqual(len(lines), 1)
        self.assertEqual(set(lines[0].track_way_ids[0]), {'1', '2'})
        self.assertEqual(lines[0].tracks[0][0], (0, 0))
        self.assertEqual(lines[0].tracks[0][-1], (30, 0))

    def test_chains_stop_where_three_through_ways_meet(self):
        lines = rv.build_lines([
            _way('1', [(0, 0), (10, 0)]),
            _way('2', [(10, 0), (20, 0)]),
            _way('3', [(10, 0), (10, 10)]),
        ])
        joined = [set().union(*line.track_way_ids) for line in lines]
        self.assertFalse(any({'1', '2'} <= ids for ids in joined))
        self.assertFalse(any({'1', '3'} <= ids for ids in joined))

    def test_switch_node_stops_a_two_way_joint(self):
        ways = [
            _way('1', [(0, 0), (10, 0)]),
            _way('2', [(10, 0), (20, 0)]),
        ]
        joined = rv.build_lines(ways)
        self.assertEqual(len(joined), 1)
        stopped = rv.build_lines(ways, [
            rv.MapPoint('s', (10, 0), (('railway', 'switch'),)),
        ])
        self.assertEqual(len(stopped), 2)
        self.assertTrue(all(len(line.tracks) == 1 for line in stopped))

    def test_tunnel_on_the_rail_is_a_gap_span(self):
        """A tunnel tag on the rail is a gap, not an overhead deck."""
        lines = rv.build_lines([
            _way('1', [(0, 0), (40, 0)], tunnel='yes', layer='-1'),
        ])
        self.assertEqual(len(lines[0].spans), 1)
        span = lines[0].spans[0]
        self.assertEqual(span.kind, 'tunnel')
        self.assertEqual(span.tunnel, 'yes')
        self.assertEqual(span.layer, '-1')
        self.assertEqual(span.tracks[0].start, (0, 0))
        self.assertEqual(span.tracks[0].end, (40, 0))
        self.assertEqual(lines[0].overhead, ())

    def test_service_siding_is_not_chained_into_the_through_line(self):
        lines = rv.build_lines([
            _way('1', [(0, 0), (10, 0)]),
            _way('9', [(10, 0), (10, 12)], service='siding'),
        ])
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].track_way_ids, (('1',),))

    def test_parallel_chains_become_one_line(self):
        lines = rv.build_lines([
            _way('1', [(0, 0), (80, 0)]),
            _way('2', [(0, 4), (80, 4)]),
        ])
        self.assertEqual(len(lines), 1)
        self.assertEqual(len(lines[0].tracks), 2)
        self.assertEqual(lines[0].id, 'line-1-2')

    def test_viaduct_over_two_tracks_is_overhead_not_a_rail_gap(self):
        """A road bridge over the rails is not a span that carries the rail."""
        tags = {'highway': 'unclassified', 'bridge': 'viaduct', 'layer': '1'}
        road = [(50, -10), (50, 14)]
        beside = [(0, 20), (100, 20)]
        lines = rv.build_lines(
            [_way('1', [(0, 0), (100, 0)]), _way('2', [(0, 4), (100, 4)])],
            overhead=[
                {'osm_id': 'v', 'tags': tags, 'points': road},
                {'osm_id': 'side', 'tags': tags, 'points': beside},
            ],
        )
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].spans, ())
        self.assertEqual(len(lines[0].overhead), 1)
        span = lines[0].overhead[0]
        self.assertEqual(span.bridge, 'viaduct')
        self.assertEqual(span.layer, '1')
        self.assertEqual(span.highway, 'unclassified')
        half = max(1.5, road_width(tags) / 2)
        self.assertEqual(span.deck, frozenset(line_cells(road, half)))
        self.assertGreater(len(span.deck), 0)
        self.assertEqual({h.track_index for h in span.crossings}, {0, 1})
        for hit in span.crossings:
            self.assertAlmostEqual(hit.along, 50, delta=0.1)
            self.assertAlmostEqual(hit.at[0], 50, delta=0.1)


class CellSkipTests(unittest.TestCase):
    """A cell with no railway is false without opening the box extract."""

    def test_missing_clip_is_false_and_does_not_read_the_box(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            box = root / 'railway-bbox-0-0-2048-2048.geojson'
            box.write_text(RAIL.read_text())
            real = Path.read_text

            def spy(self, *args, **kwargs):
                if self.resolve() == box.resolve():
                    raise AssertionError('full extract was read')
                return real(self, *args, **kwargs)

            with mock.patch.object(Path, 'read_text', spy):
                self.assertFalse(rv.cell_has_railway((0, 0), root / 'no-such.geojson'))

    def test_empty_clip_is_false_and_does_not_read_the_box(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            box = root / 'railway-bbox-0-0-2048-2048.geojson'
            box.write_text('not-json-and-must-not-be-read')
            cell = root / 'cell.geojson'
            _write(cell, [{
                'type': 'Feature',
                'properties': {'building': 'yes'},
                'geometry': {'type': 'Polygon', 'coordinates': [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
            }])
            real = Path.read_text

            def spy(self, *args, **kwargs):
                if self.resolve() == box.resolve():
                    raise AssertionError('full extract was read')
                return real(self, *args, **kwargs)

            with mock.patch.object(Path, 'read_text', spy):
                self.assertFalse(rv.cell_has_railway((0, 0), cell))

    def test_no_frame_when_a_line_is_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cell.geojson'
            _write(path, [_line_feature([(6980, -4343), (7000, -4340)])])
            with self.assertRaises(ValueError):
                rv.cell_has_railway((54, -34), path)

    def test_miniature_in_the_cell_counts_and_a_far_cell_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cell.geojson'
            _write(path, [_line_feature(
                [(6980, -4343), (7000, -4340)], railway='miniature',
            )])
            self.assertTrue(rv.cell_has_railway((54, -34), path, frame=FRAME))
            self.assertFalse(rv.cell_has_railway((0, 0), path, frame=FRAME))


class TrialExtractTests(unittest.TestCase):
    """The recorded 53 ways and 26 points."""

    @classmethod
    def setUpClass(cls):
        cls.ways = rv.extract_railway_box(BOX, source=RAIL, frame=FRAME)
        cls.points = rv.read_map_points(POINTS, FRAME)
        cls.lines = rv.build_lines(cls.ways, cls.points)

    def test_box_plan_is_the_unclipped_lines_preset(self):
        plan = rv.railway_box_plan(BOX, FRAME)
        self.assertEqual(plan['gdal_layer'], 'lines')
        self.assertIn('railway', plan['where'])
        self.assertFalse(plan['clip'])
        self.assertEqual(plan['box_mc'], BOX)
        lon0, lat0, lon1, lat1 = plan['spat']
        self.assertLess(lon0, 0)
        self.assertGreater(lat0, 50)
        self.assertLess(lon0, lon1)
        self.assertLess(lat0, lat1)

    def test_classes_match_the_trial_counts(self):
        self.assertEqual(len(self.ways), 53)
        self.assertEqual(sum(w.kind == rv.THROUGH for w in self.ways), 25)
        self.assertEqual(sum(w.kind == rv.DECOR for w in self.ways), 28)
        tags = [dict(w.tags) for w in self.ways]
        self.assertEqual(
            sum(t.get('railway') == 'rail' and t.get('usage') == 'main' and not t.get('service')
                for t in tags),
            25,
        )
        self.assertTrue(all(
            w.kind == rv.THROUGH for w, t in zip(self.ways, tags)
            if t.get('railway') == 'rail' and t.get('usage') == 'main'
        ))
        self.assertEqual(sum(
            t.get('service') == 'crossover' and t.get('railway') == 'rail' for t in tags
        ), 5)
        self.assertEqual(sum(
            t.get('service') == 'siding' and t.get('railway') == 'rail' for t in tags
        ), 2)
        self.assertEqual(sum(t.get('railway') == 'miniature' for t in tags), 19)
        self.assertEqual(sum(t.get('railway') == 'disused' for t in tags), 1)
        self.assertEqual(sum(t.get('railway') == 'turntable' for t in tags), 1)
        decor = [
            w for w, t in zip(self.ways, tags)
            if t.get('railway') != 'rail' or t.get('service')
        ]
        self.assertTrue(all(w.kind == rv.DECOR for w in decor))

    def test_whole_lines_keep_vertices_outside_the_box(self):
        far = next(w for w in self.ways if w.osm_id == '880996764')
        self.assertTrue(any(x > 7500 or z > -3800 for x, z in far.points))

    def test_main_and_branch_are_paired_and_stop_at_the_junction(self):
        main = self._line_with('236574344')
        branch = self._line_with('880996761')
        self.assertEqual(
            {frozenset(ids) for ids in main.track_way_ids},
            {frozenset(MAIN_P), frozenset(MAIN_Q)},
        )
        self.assertEqual(
            {frozenset(ids) for ids in branch.track_way_ids},
            {frozenset(BRANCH_U), frozenset(BRANCH_V)},
        )
        self.assertEqual(len(main.tracks), 2)
        self.assertEqual(len(branch.tracks), 2)
        branch_ids = set().union(*branch.track_way_ids)
        self.assertNotIn('236574344', branch_ids)
        self.assertNotIn('1002086270', branch_ids)
        self.assertNotIn('237029734', branch_ids)
        for line in (main, branch):
            self.assertTrue(self._end_near(line, (6820, -4085), 40), line.id)
        self.assertTrue(any(p[0] > 7500 for poly in branch.tracks for p in poly))

    def test_level_crossing_once_per_branch_track(self):
        branch = self._line_with('880996761')
        self.assertEqual(len(branch.crossings), 2)
        self.assertEqual({c.track_index for c in branch.crossings}, {0, 1})
        self.assertEqual(len({c.osm_id for c in branch.crossings}), 2)
        for crossing in branch.crossings:
            self.assertEqual(crossing.barrier, 'double_half')
            self.assertEqual(crossing.light, 'yes')
            self.assertEqual(crossing.bell, 'yes')
            self.assertEqual(crossing.activation, 'remote')
            self.assertIn('MCB-CCTV', crossing.description or '')
            self.assertLess(math.dist(crossing.at, (6980, -4343)), 8)
        others = [
            c for line in self.lines if line.id != branch.id for c in line.crossings
        ]
        self.assertEqual(others, [])

    def test_two_branch_bridge_spans(self):
        branch = self._line_with('880996761')
        self.assertEqual(len(branch.spans), 2)
        self.assertTrue(all(s.kind == 'bridge' and s.bridge == 'yes' for s in branch.spans))
        self.assertTrue(all(s.layer == '1' for s in branch.spans))
        short = next(s for s in branch.spans if s.bridge_ref == 'E15/1')
        long = next(s for s in branch.spans if not s.bridge_ref)
        self.assertEqual(len(short.tracks), 2)
        self.assertEqual(len(long.tracks), 2)
        for piece in short.tracks:
            self.assertLess(math.dist(piece.start, (7285, -4290)), 20)
            self.assertLess(math.dist(piece.end, (7285, -4290)), 20)
        for piece in long.tracks:
            ends = (piece.start, piece.end)
            self.assertTrue(any(math.dist(p, (7415, -4107)) < 15 for p in ends))
            self.assertTrue(any(math.dist(p, (7474, -4020)) < 15 for p in ends))
        self.assertEqual(branch.overhead, ())

    def test_cell_on_the_branch_has_railway(self):
        """Cell 54,-34 holds the Adelaide crossing. Cell 0,0 does not."""
        self.assertTrue(rv.cell_has_railway((54, -34), RAIL, frame=FRAME))
        self.assertFalse(rv.cell_has_railway((0, 0), RAIL, frame=FRAME))

    def _line_with(self, osm_id: str) -> rv.Line:
        matches = [
            line for line in self.lines
            if any(osm_id in ids for ids in line.track_way_ids)
        ]
        self.assertEqual(len(matches), 1, [line.track_way_ids for line in self.lines])
        return matches[0]

    @staticmethod
    def _end_near(line: rv.Line, xz: tuple[int, int], tol: float) -> bool:
        return any(
            math.dist(poly[0], xz) <= tol or math.dist(poly[-1], xz) <= tol
            for poly in line.tracks
        )
