"""Unit tests for cell_features and handoff ring order."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

import cell_features as cf
import cellmath
import water_body as W


def _square(x0, z0, x1, z1):
    return [[(float(x0), float(z0)), (float(x1), float(z0)),
             (float(x1), float(z1)), (float(x0), float(z1)), (float(x0), float(z0))]]


class TestCellFeatures(unittest.TestCase):
    def test_layer_builds_are_not_features(self):
        for n in ('cell_49_m43_ground', 'cell_48_m44_trees_undo',
                  'cell_grounds_mc', 'cell_marker_48_m44', 'cell_48_m45_foliage'):
            self.assertFalse(cf.is_feature_build(n), n)
        for n in ('campus_b35', 'campus_engineering', 'chamberlain_rd_8_58'):
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

    def test_mixed_tag_touching_ways_stay_separate(self):
        """#328: same-tag only — industrial + retail that touch must not merge."""
        industrial = _square(6160, -5600, 6180, -5580)
        retail = _square(6180, -5600, 6200, -5580)          # shared wall; different building=
        idx = SimpleNamespace(
            buildings=[
                (industrial, {'building': 'industrial', 'name': 'Unit 7', 'id': 'w7'}),
                (retail, {'building': 'retail', 'name': 'Unit 12', 'id': 'w12'}),
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
            ('building', ('w12',)),
            ('building', ('w7',)),
        ])

    def test_cross_cell_touching_same_tag_is_planned_in_each_footprints_home(self):
        """#288 / #433: touching shops on opposite sides of a cell line stay separate.

        Each footprint is proposed once, in the cell with most of its columns.
        They are not one group filed in the lowest ref's cell.
        """
        # Cell line at x=6144: west way homes in 47,-44; east way homes in 48,-44; they touch.
        west = _square(6124, -5600, 6144, -5580)
        east = _square(6144, -5600, 6164, -5580)
        idx = SimpleNamespace(
            buildings=[
                (west, {'building': 'retail', 'name': 'West Shop', 'id': 'w10'}),
                (east, {'building': 'retail', 'name': 'East Shop', 'id': 'w30'}),
            ],
            streets=[],
        )
        with mock.patch('cell_features.for_footprint', return_value=[]), \
             mock.patch('cell_features.pictures_for', return_value=[]), \
             mock.patch('cell_features.osm_ref', side_effect=lambda p: p['id']), \
             mock.patch('cell_features.property_label', side_effect=lambda p: p['name']):
            west_cell = cf.footprint_progress(47, -44, idx=idx, covered=set())['unbuilt']
            east_cell = cf.footprint_progress(48, -44, idx=idx, covered=set())['unbuilt']
        self.assertEqual([(u['kind'], tuple(u['refs']), tuple(u['home'])) for u in west_cell],
                         [('building', ('w10',), (47, -44))])
        self.assertEqual([(u['kind'], tuple(u['refs']), tuple(u['home'])) for u in east_cell],
                         [('building', ('w30',), (48, -44))])

    def test_ring_order(self):
        self.assertEqual(cellmath.ring_cells(1), [(48, -45), (49, -45), (49, -44), (49, -43),
                                            (48, -43), (47, -43), (47, -44), (47, -45)])
        self.assertEqual(len(set(cellmath.ring_cells(2))), 16)

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
        from coverage import scan_polygon
        from cellmath import cell_bbox
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

    def test_footprint_progress_flags_landmark_candidates(self):
        """#335: church and shop=mall footprints leave footprint_progress / missing_features with landmark fields."""
        # Cell 48,-44 is x 6144..6271, z -5632..-5505.
        church = _square(6160, -5600, 6180, -5580)
        mall = _square(6200, -5600, 6220, -5580)
        ordinary = _square(6230, -5600, 6250, -5580)
        idx = SimpleNamespace(
            buildings=[
                (church, {'building': 'church', 'name': 'St Marks', 'id': 'wchurch'}),
                (mall, {'building': 'retail', 'shop': 'mall', 'name': 'West Quay', 'id': 'wmall'}),
                (ordinary, {'building': 'university', 'name': 'Lab Block', 'id': 'wlab'}),
            ],
            streets=[],
        )
        with mock.patch('cell_features.for_footprint', return_value=[]), \
             mock.patch('cell_features.pictures_for', return_value=[]), \
             mock.patch('cell_features.osm_ref', side_effect=lambda p: p['id']), \
             mock.patch('cell_features.property_label', side_effect=lambda p: p['name']):
            prog = cf.footprint_progress(48, -44, idx=idx, covered=set())
            missing = cf.missing_features(48, -44, idx=idx, covered=set(), water_columns=set())
        by_label = {u['label']: u for u in prog['unbuilt']}
        self.assertTrue(by_label['St Marks']['landmark'])
        self.assertEqual(by_label['St Marks']['landmark_class'], 'church')
        self.assertTrue(by_label['West Quay']['landmark'])
        self.assertEqual(by_label['West Quay']['landmark_class'], 'shopping_centre')
        self.assertNotIn('landmark', by_label['Lab Block'])
        miss_by_label = {u['label']: u for u in missing if u['kind'] == 'building'}
        self.assertTrue(miss_by_label['St Marks']['landmark'])
        self.assertEqual(miss_by_label['St Marks']['landmark_class'], 'church')
        self.assertTrue(miss_by_label['West Quay']['landmark'])
        self.assertEqual(miss_by_label['West Quay']['landmark_class'], 'shopping_centre')
        self.assertNotIn('landmark', miss_by_label['Lab Block'])

    def test_built_footprint_still_emits_uncovered_portals_as_houses(self):
        """#154: a built outline that swallows houses still proposes their portals.

        Main footprint columns are covered (over COVERED_FRAC); portal columns are not.
        Those portals must appear as an unbuilt houses row so propose-features can see them.
        """
        # Cell 49,-43 is x 6272..6399, z -5504..-5377.
        big = _square(6275, -5490, 6320, -5430)  # large outline (built)
        from coverage import scan_polygon
        from cellmath import cell_bbox
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
        from cellmath import cell_bbox
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
        from coverage import scan_polygon
        from cellmath import cell_bbox
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
        from coverage import scan_polygon
        from cellmath import cell_bbox
        hall = set(scan_polygon(_square(6160, -5600, 6180, -5580), cell_bbox(48, -44, 48, -44)))
        portal = {'id': '7-A', 'building': '7', 'x': 6170, 'z': -5590, 'images': []}
        settled = self._settled(hall - {(6170, -5590)}, portals=[portal])
        box = {(6170 + a, -5590 + b) for a in range(-2, 3) for b in range(-2, 3)}
        self.assertTrue(settled.isdisjoint(box))
        self.assertEqual(settled, hall - box)

    def test_missing_water_bodies_from_columns(self):
        """#148: connected water columns become water_body candidates until covered."""
        # Cell 48,-45: place a 3×3 pond and a covered 2×2 (below MIN_WATER_COLUMNS after filter).
        from cellmath import cell_bbox
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

    def test_cross_cell_pond_is_proposed_per_cell_clipped(self):
        """#321: a water body over a cell line is one clip candidate in each cell it touches."""
        water, refs = self._two_cell_pond(2, 5)
        east = cf.missing_water_bodies(48, -44, water_columns=water, water_refs=refs, covered=set())
        west = cf.missing_water_bodies(47, -44, water_columns=water, water_refs=refs, covered=set())
        self.assertEqual([b['id'] for b in east], ['w147601601_48_m44'])
        self.assertEqual([b['id'] for b in west], ['w147601601_47_m44'])
        self.assertEqual(east[0]['refs'], ['w147601601'])
        self.assertEqual((east[0]['area'], west[0]['area']), (50, 20))
        self.assertEqual(east[0]['cells'], [[48, -44]])
        self.assertEqual(east[0]['clip'], {'cell': [48, -44]})
        self.assertEqual(east[0]['bbox'], (6144, -5600, 6148, -5591))
        self.assertEqual(len(east[0]['outline']), 70)       # the shore ring sees across the line

    def test_clip_outline_reaches_only_clip_reach_beyond_its_cell(self):
        """#321: a clip carries neighbour water only as far as a bank ring can need it."""
        water, refs = self._two_cell_pond(20, 5)
        east = cf.missing_water_bodies(48, -44, water_columns=water, water_refs=refs, covered=set())
        xs = {x for x, _ in east[0]['outline']}
        self.assertEqual(min(xs), 6144 - W.CLIP_REACH)

    def test_clip_covered_in_its_own_cell_only(self):
        """#321: built water in one cell does not account for the neighbour's clip."""
        water, refs = self._two_cell_pond(2, 5)
        built_east = {q for q in water if q[0] >= 6144}
        self.assertEqual(cf.missing_water_bodies(48, -44, water_columns=water, water_refs=refs,
                                                 covered=built_east), [])
        self.assertEqual(len(cf.missing_water_bodies(47, -44, water_columns=water, water_refs=refs,
                                                     covered=built_east)), 1)

    def test_clip_candidates_build_the_whole_pond_without_a_seam(self):
        """#321: specs written from each cell's candidate join into the whole body."""
        water, refs = self._two_cell_pond(2, 5)
        common = {'water_level': 40, 'bank': {'material': 'dirt'}, 'edge': {'material': 'gravel'},
                  'provenance': {'source': 'vector_map', 'confidence': 'high'}}
        plans = []
        for cell in ((47, -44), (48, -44)):
            body = cf.missing_water_bodies(*cell, water_columns=water, water_refs=refs, covered=set())[0]
            spec = {'name': body['id'], 'outline': body['outline'], 'clip': body['clip'], **common}
            plans.append(W.build(W.load_spec_dict(spec)))
        whole = W.build(W.load_spec_dict({'name': 'whole', 'outline': sorted(water), **common}))
        self.assertEqual({**plans[0].cells, **plans[1].cells}, whole.cells)

    def test_cell_the_bank_reaches_without_water_gets_a_bank_only_clip(self):
        """#321: water along the z = -5632 line (not crossing it) still has its bank clipped below."""
        water = {(x, z) for x in range(6142, 6149) for z in range(-5632, -5629)}
        refs = {q: 'w7' for q in water}
        common = {'water_level': 40, 'bank': {'material': 'dirt'}, 'edge': {'material': 'gravel'},
                  'provenance': {'source': 'vector_map', 'confidence': 'high'}}
        below = cf.missing_water_bodies(48, -45, water_columns=water, water_refs=refs, covered=set())
        self.assertEqual([(b['id'], b['area'], b['clip']) for b in below],
                         [('w7_48_m45', 0, {'cell': [48, -45]})])
        self.assertEqual(cf.missing_water_bodies(48, -45, water_columns=water, water_refs=refs,
                                                 covered=water), below)   # built water never covers a bank
        mid = {(x, z + 3) for x, z in water}
        self.assertEqual(cf.missing_water_bodies(48, -45, water_columns=mid, water_refs={q: 'w7' for q in mid},
                                                 covered=set()), [])     # within CLIP_REACH, beyond default bank
        far = {(x, z + 9) for x, z in water}
        self.assertEqual(cf.missing_water_bodies(48, -45, water_columns=far, water_refs={q: 'w7' for q in far},
                                                 covered=set()), [])     # beyond CLIP_REACH
        whole = W.build(W.load_spec_dict({'name': 'whole', 'outline': sorted(water), **common}))
        union: dict = {}
        for cell in ((47, -44), (48, -44), (47, -45), (48, -45)):
            body = cf.missing_water_bodies(*cell, water_columns=water, water_refs=refs, covered=set())[0]
            spec = {'name': body['id'], 'outline': body['outline'], 'clip': body['clip'], **common}
            union.update(W.build(W.load_spec_dict(spec)).cells)
        self.assertEqual(union, whole.cells)
        # #353: once the bank-only clip is built, its in-cell bank columns cover it
        bank_only = below[0]
        built = W.build(W.load_spec_dict({
            'name': bank_only['id'], 'outline': bank_only['outline'], 'clip': bank_only['clip'], **common,
        }))
        bank_covered = {(x, z) for x, _y, z in built.cells}
        self.assertEqual(cf.missing_water_bodies(48, -45, water_columns=water, water_refs=refs,
                                                 covered=bank_covered), [])

    def test_single_cell_pond_keeps_its_map_ref_plan(self):
        """#321: a pond inside one cell is not clipped; its plan id stays the map ref."""
        water, refs = self._two_cell_pond(0, 5)
        bodies = cf.missing_water_bodies(48, -44, water_columns=water, water_refs=refs, covered=set())
        self.assertEqual(len(bodies), 1)
        self.assertNotIn('clip', bodies[0])
        self.assertNotIn('id', bodies[0])

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
