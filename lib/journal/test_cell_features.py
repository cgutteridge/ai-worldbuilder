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

    def test_touching_retail_ways_form_one_group_distant_stays_building(self):
        """#328: same building= tag, gap <= TOUCH_GAP → one group; a distant way stays alone.

        The third shop sits inside JOIN_GAP but beyond TOUCH_GAP, so house-run clustering
        must not swallow it into the group.
        """
        # Cell 48,-44: x 6144..6271, z -5632..-5505. Three retail footprints.
        a = _square(6160, -5600, 6180, -5580)          # touches b (shared wall at x=6180)
        b = _square(6180, -5600, 6200, -5580)
        c = _square(6205, -5600, 6225, -5580)          # gap 5: > TOUCH_GAP, < JOIN_GAP
        self.assertLessEqual(cf._gap((6180, -5600, 6200, -5580), (6205, -5600, 6225, -5580)),
                             cf.JOIN_GAP)
        self.assertGreater(cf._gap((6180, -5600, 6200, -5580), (6205, -5600, 6225, -5580)),
                           cf.TOUCH_GAP)
        idx = SimpleNamespace(
            buildings=[
                (a, {'building': 'retail', 'name': 'Shop A', 'id': 'w30'}),
                (b, {'building': 'retail', 'name': 'Shop B', 'id': 'w10'}),
                (c, {'building': 'retail', 'name': 'Shop C', 'id': 'w20'}),
            ],
            streets=[],
        )
        with mock.patch('cell_features.for_footprint', return_value=[]), \
             mock.patch('cell_features.pictures_for', return_value=[]), \
             mock.patch('cell_features.osm_ref', side_effect=lambda p: p['id']), \
             mock.patch('cell_features.property_label', side_effect=lambda p: p['name']):
            unbuilt = cf.footprint_progress(48, -44, idx=idx, covered=set())['unbuilt']
        kinds = sorted((u['kind'], tuple(u['refs'])) for u in unbuilt)
        self.assertEqual(kinds, [
            ('building', ('w20',)),
            ('group', ('w10', 'w30')),
        ])
        group = next(u for u in unbuilt if u['kind'] == 'group')
        self.assertEqual(group['label'], 'retail (2 buildings)')
        self.assertEqual(group['bbox'][:2], (6160, -5600))
        self.assertEqual(group['bbox'][2:], (6200, -5580))

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

    def test_built_footprint_still_emits_uncovered_portals_as_houses(self):
        """#154: a built outline that swallows houses still proposes their portals.

        Main footprint columns are covered (over COVERED_FRAC); portal columns are not.
        Those portals must appear as an unbuilt houses row so propose-features can see them.
        """
        # Cell 49,-43 is x 6272..6399, z -5504..-5377.
        big = _square(6275, -5490, 6320, -5430)  # large outline (built)
        from coverage import scan_polygon, cell_bbox
        cb = cell_bbox(49, -43, 49, -43)
        full = set(scan_polygon(big, cb))
        # Cover most of the footprint; leave a strip around the portal columns free.
        portal_cols = {(6290, -5464), (6285, -5470), (6300, -5447)}
        covered = full - portal_cols
        self.assertGreaterEqual(len(covered) / len(full), cf.COVERED_FRAC)

        portals = [
            {'id': '1105-A', 'building': '1105', 'label': 'Main', 'x': 6290, 'z': -5464, 'images': []},
            {'id': '1106-A', 'building': '1106', 'label': 'Main', 'x': 6285, 'z': -5470, 'images': []},
            {'id': '1102-A', 'building': '1102', 'label': 'Main', 'x': 6300, 'z': -5447, 'images': []},
        ]
        idx = SimpleNamespace(
            buildings=[(big, {'building': 'yes', 'name': 'Big Hall', 'id': 'w9'})],
            streets=[('University Road', 4, [(6270.0, -5450.0), (6330.0, -5450.0)])],
        )
        with mock.patch('cell_features.for_footprint', return_value=portals), \
             mock.patch('cell_features.pictures_for', return_value=[]), \
             mock.patch('cell_features.osm_ref', side_effect=lambda p: p['id']), \
             mock.patch('cell_features.property_label', side_effect=lambda p: p['name']):
            prog = cf.footprint_progress(49, -43, idx=idx, covered=covered)
        houses = [u for u in prog['unbuilt'] if u['kind'] == 'houses']
        self.assertEqual(len(houses), 1, prog['unbuilt'])
        self.assertEqual(houses[0]['street'], 'University Road')
        self.assertEqual({p['id'] for p in houses[0]['portals']}, {'1105-A', '1106-A', '1102-A'})
        self.assertIn('University Road', houses[0]['label'])
        self.assertGreater(houses[0]['area'], 0)

    def _settled(self, covered, dismissed=(), portals=()):
        """settled_footprint_columns over a built hall (w1) and an empty hall (w2) in cell 48,-44."""
        from coverage import cell_bbox
        idx = SimpleNamespace(buildings=[
            (_square(6160, -5600, 6180, -5580), {'building': 'university', 'id': 'w1'}),
            (_square(6200, -5600, 6220, -5580), {'building': 'university', 'id': 'w2'}),
            (_square(6230, -5600, 6236, -5594), {'building': 'shed', 'id': 'w3'}),
        ], streets=[])
        with mock.patch('cell_features.for_footprint', side_effect=lambda r: list(portals) if r[0][0][0] == 6160 else []), \
                mock.patch('cell_features.osm_ref', side_effect=lambda p: p['id']):
            return cf.settled_footprint_columns(cell_bbox(48, -44, 48, -44), idx=idx, covered=covered,
                                                dismissed=dismissed)

    def test_settled_footprint_is_built_or_dismissed_buildings_only(self):
        """Task 295: a built building's outline is the ground's where no build owns it; an unbuilt one is held."""
        from coverage import scan_polygon, cell_bbox
        cb = cell_bbox(48, -44, 48, -44)
        hall = set(scan_polygon(_square(6160, -5600, 6180, -5580), cb))
        empty = set(scan_polygon(_square(6200, -5600, 6220, -5580), cb))
        shed = set(scan_polygon(_square(6230, -5600, 6236, -5594), cb))
        gap = {q for q in hall if q[1] == -5581}                  # an edge strip the feature build left out
        settled = self._settled(hall - gap)
        self.assertEqual(settled, hall)                             # the strip is not held: ownership decides
        self.assertTrue(settled.isdisjoint(empty | shed))
        self.assertEqual(self._settled(set(), dismissed={'w2'}), empty)       # dismissed: the ground's
        self.assertEqual(self._settled(set(), dismissed={'plan_w2'}), empty)  # plan ids that end in the ref
        self.assertEqual(self._settled(set()), set())

    def test_settled_footprint_holds_uncovered_portals_in_a_built_outline(self):
        """Task 295 / #154: houses swallowed by a built outline keep their columns held."""
        from coverage import scan_polygon, cell_bbox
        hall = set(scan_polygon(_square(6160, -5600, 6180, -5580), cell_bbox(48, -44, 48, -44)))
        portal = {'id': '7-A', 'building': '7', 'x': 6170, 'z': -5590, 'images': []}
        settled = self._settled(hall - {(6170, -5590)}, portals=[portal])
        box = {(6170 + a, -5590 + b) for a in range(-2, 3) for b in range(-2, 3)}
        self.assertTrue(settled.isdisjoint(box))
        self.assertEqual(settled, hall - box)

    def test_missing_water_bodies_from_columns(self):
        """#148: connected water columns become water_body candidates until covered."""
        # Cell 48,-45: place a 3×3 pond and a covered 2×2 (below MIN_WATER_COLUMNS after filter).
        from coverage import cell_bbox
        x0, z0, x1, z1 = cell_bbox(48, -45, 48, -45)
        pond = {(x0 + 10 + dx, z0 + 10 + dz) for dx in range(3) for dz in range(3)}
        covered_pond = {(x0 + 40 + dx, z0 + 40 + dz) for dx in range(3) for dz in range(3)}
        water = pond | covered_pond
        with mock.patch('cell_features.feature_columns', return_value=set(covered_pond)):
            bodies = cf.missing_water_bodies(48, -45, water_columns=water, covered=set(covered_pond))
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0]['kind'], 'water_body')
        self.assertEqual(bodies[0]['area'], 9)
        self.assertEqual(bodies[0]['cells'], [[48, -45]])

    def _two_cell_pond(self, west_cols, east_cols):
        """Pond rows straddling the x=6144 line between cells 47,-44 and 48,-44."""
        cols = {(x, z) for x in range(6144 - west_cols, 6144 + east_cols) for z in range(-5600, -5590)}
        return cols, {q: 'w147601601' for q in cols}

    def test_cross_cell_pond_is_proposed_once_whole_in_its_majority_cell(self):
        """Task 288: a pond over a cell line is one candidate, in the cell holding most of it."""
        water, refs = self._two_cell_pond(2, 5)
        home = cf.missing_water_bodies(48, -44, water_columns=water, water_refs=refs, covered=set())
        self.assertEqual(len(home), 1, home)
        self.assertEqual(home[0]['refs'], ['w147601601'])
        self.assertEqual(home[0]['area'], 70)
        self.assertEqual(len(home[0]['outline']), 70)
        self.assertEqual(home[0]['cells'], [[48, -44], [47, -44]])
        self.assertEqual(cf.missing_water_bodies(47, -44, water_columns=water, water_refs=refs,
                                                 covered=set()), [])

    def test_cross_cell_tie_goes_to_the_lowest_cell(self):
        """Task 288: an even split is planned in the lowest (tx, tz), stably."""
        water, refs = self._two_cell_pond(3, 3)
        self.assertEqual(cf.home_cell(water), (47, -44))
        self.assertEqual(len(cf.missing_water_bodies(47, -44, water_columns=water, water_refs=refs,
                                                     covered=set())), 1)
        self.assertEqual(cf.missing_water_bodies(48, -44, water_columns=water, water_refs=refs,
                                                 covered=set()), [])

    def test_cross_cell_building_is_proposed_only_in_its_home_cell(self):
        """Task 288: an unbuilt building over a cell line is a candidate of its home cell only."""
        idx = SimpleNamespace(buildings=[(_square(6140, -5600, 6160, -5580),
                                          {'building': 'university', 'name': 'Line Hall', 'id': 'w9'})],
                              streets=[])
        with mock.patch('cell_features.for_footprint', return_value=[]), \
             mock.patch('cell_features.pictures_for', return_value=[]), \
             mock.patch('cell_features.osm_ref', side_effect=lambda p: p['id']), \
             mock.patch('cell_features.property_label', side_effect=lambda p: p['name']):
            home = cf.footprint_progress(48, -44, idx=idx, covered=set())
            other = cf.footprint_progress(47, -44, idx=idx, covered=set())
        self.assertEqual(home['unbuilt_names'], ['Line Hall'])
        self.assertEqual(other['unbuilt'], [])

    def _line_hall(self, x0, x1, z1=-5580, building='university'):
        return SimpleNamespace(buildings=[(_square(x0, -5600, x1, z1),
                                           {'building': building, 'name': 'Line Hall', 'id': 'w9'})],
                               streets=[])

    def _progress(self, idx, cell):
        with mock.patch('cell_features.for_footprint', return_value=[]), \
             mock.patch('cell_features.pictures_for', return_value=[]), \
             mock.patch('cell_features.osm_ref', side_effect=lambda p: p['id']), \
             mock.patch('cell_features.property_label', side_effect=lambda p: p['name']):
            return cf.footprint_progress(*cell, idx=idx, covered=set())

    def test_cross_cell_building_tie_goes_to_the_lowest_cell(self):
        """Task 288 follow-up: a building split evenly over a cell line is planned in the lowest cell."""
        idx = self._line_hall(6134, 6154)             # 10 columns wide each side of x=6144
        self.assertEqual(self._progress(idx, (47, -44))['unbuilt_names'], ['Line Hall'])
        self.assertEqual(self._progress(idx, (48, -44))['unbuilt'], [])

    def test_huge_cross_cell_building_is_still_proposed_once(self):
        """Task 288 follow-up: ≥4000 columns in each cell must not skip the home-cell decision."""
        idx = self._line_hall(6069, 6224, z1=-5540)   # 75x60 west, 80x60 east: both over 4000
        self.assertEqual(self._progress(idx, (48, -44))['unbuilt_names'], ['Line Hall'])
        self.assertEqual(self._progress(idx, (47, -44))['unbuilt'], [])

    def test_huge_cross_cell_building_tie_goes_to_the_lowest_cell(self):
        idx = self._line_hall(6064, 6224, z1=-5540)   # 80x60 each side
        self.assertEqual(self._progress(idx, (47, -44))['unbuilt_names'], ['Line Hall'])
        self.assertEqual(self._progress(idx, (48, -44))['unbuilt'], [])

    def test_water_body_carries_way_id_not_stream(self):
        """#163: polygon water → one candidate with way id + vector_map; stream → no pond."""
        import json
        import tempfile
        from pathlib import Path

        import ground_sources as GS
        import osm_footprints as OF

        # Cell (0,0) is MC x/z 0..127. Pond way 141657072 + a long stream elsewhere.
        pond_ring = [
            [20.0, 20.0], [28.0, 20.0], [28.0, 28.0], [20.0, 28.0], [20.0, 20.0],
        ]
        stream_pts = [[80.0, 10.0], [80.0, 100.0]]
        features = [
            {
                'type': 'Feature',
                'properties': {
                    'osm_way_id': '141657072',
                    'natural': 'water',
                },
                'geometry': {'type': 'Polygon', 'coordinates': [pond_ring]},
            },
            {
                'type': 'Feature',
                'properties': {'osm_id': '999', 'waterway': 'stream'},
                'geometry': {'type': 'LineString', 'coordinates': stream_pts},
            },
        ]
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            (cache / 'campus__water.geojson').write_text(
                json.dumps({'type': 'FeatureCollection', 'features': features}))
            empty_clips = cache / 'no-clips'
            empty_clips.mkdir()
            with mock.patch.object(OF, 'CACHE', cache), \
                    mock.patch.object(OF, '_resolve_vector_map_clips', return_value=empty_clips), \
                    mock.patch('world_frame.osgb_to_mc', side_effect=lambda e, n: (e, n)):
                ground = GS.OSMGround((0, 0))

        self.assertTrue(ground.water, 'pond polygon must rasterise')
        self.assertTrue(ground.water_line, 'stream must rasterise as line water')
        self.assertFalse(ground.water & ground.water_line,
                         'polygon and line water must stay separate')
        self.assertGreaterEqual(len(ground.water_line), cf.MIN_WATER_COLUMNS,
                                'stream alone would have been a pond before #163')

        with mock.patch('cell_features.feature_columns', return_value=set()), \
                mock.patch('ground_sources.OSMGround', return_value=ground):
            bodies = cf.missing_water_bodies(0, 0, covered=set())

        self.assertEqual(len(bodies), 1, bodies)
        body = bodies[0]
        self.assertEqual(body['kind'], 'water_body')
        self.assertEqual(body['refs'], ['w141657072'])
        self.assertEqual(body['provenance']['source'], 'vector_map')
        outline = {(int(c[0]), int(c[1])) for c in body['outline']}
        self.assertTrue(outline <= set(ground.water))
        self.assertFalse(outline & set(ground.water_line))


if __name__ == '__main__':
    unittest.main()
