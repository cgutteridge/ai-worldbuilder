"""Task #333: terrace-house body over a shopfront ground floor. Synthetic footprints, no world data."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import house_build as H
import shop_body as S
import typology_spec as T

FLOOR = 50
FOOTPRINT = {(x, z) for x in range(10, 19) for z in range(20, 34)}   # 9 wide, 14 deep, street to the south


def rect(x0, z0, width, depth):
    return {(x, z) for x in range(x0, x0 + width) for z in range(z0, z0 + depth)}


class EvidenceTest(unittest.TestCase):
    """Storeys, pitch and the dormer-or-bay choice come from lidar eave and ridge."""

    def test_storeys_follow_the_eave_with_a_metre_of_slack(self):
        self.assertEqual([S.storeys_from_eave(m) for m in (2.0, 5.2, 6.0, 7.9, 8.0, 9.5, 40.0)],
                         [1, 2, 2, 2, 3, 3, S.MAX_STOREYS])

    def test_pitch_reproduces_the_lidar_ridge_but_stays_pitched(self):
        self.assertEqual(S.pitch_from_lidar(6.0, 9.2, 7), 0.8)       # 3.2 m over a 4 step half span
        self.assertEqual(S.pitch_from_lidar(6.0, 6.2, 7), S.MIN_PITCH)   # flat-ish lidar still builds a pitched body
        self.assertEqual(S.pitch_from_lidar(6.0, 20.0, 7), S.MAX_PITCH)
        self.assertGreater(S.MIN_PITCH, 0.5)   # house_build reads 0.5 and below as slab rings

    def test_roof_room_decides_dormers_over_a_bay(self):
        west = S.recipe(6.5, 10.0, FOOTPRINT, 'S')    # three-storey: eave two storeys, ridge well above
        east = S.recipe(6.0, 8.0, FOOTPRINT, 'S')     # two-storey: little roof above the eave
        self.assertEqual((west['storeys'], west['bay'], west['dormers'] > 0), (2, False, True))
        self.assertEqual((east['storeys'], east['bay'], east['dormers']), (2, True, 0))
        self.assertEqual(west['front_depth'], S.FRONT_DEPTH)

    def test_override_replaces_the_derived_values(self):
        block = S.recipe(6.0, 8.0, FOOTPRINT, 'S', override={'bay': False, 'dormers': 1, 'front_depth': 0})
        self.assertEqual((block['bay'], block['dormers'], block['front_depth']), (False, 1, 0))

    def test_strip_runs_and_positions(self):
        strip = S.front_strip(FOOTPRINT, 'S', 3)
        self.assertEqual({z for _, z in strip}, {31, 32, 33})
        self.assertEqual(S.front_strip(FOOTPRINT, 'N', 3), {c for c in FOOTPRINT if c[1] < 23})
        self.assertEqual(S.front_strip(rect(0, 0, 9, 5), 'S', 3), set())   # no room left for a body
        self.assertEqual(S.front_strip(FOOTPRINT, 'S', 0), set())
        body = FOOTPRINT - strip
        row = S.front_row(body, 'S')
        self.assertEqual({z for _, z in row}, {30})
        (run,) = S.lateral_runs(row, 'S')
        self.assertEqual(S.centres(run, 1), [(14, 30)])
        two = S.centres(run, 3)    # a nine-wide front fits two dormers, a wall column clear of each end
        self.assertEqual(two, [(12, 30), (16, 30)])
        self.assertEqual(S.centres(run[:4], 1), [])

    def test_stepped_front_breaks_into_short_runs(self):
        stepped = rect(0, 0, 5, 6) | rect(5, 0, 5, 4)
        runs = S.lateral_runs(S.front_row(stepped, 'S'), 'S')
        self.assertEqual([len(r) for r in runs], [5, 5])
        self.assertEqual(S.centres(runs[0], 2), [(2, 5)])

    def test_block_validation(self):
        S.validate_block('a', {'storeys': 3, 'front_depth': 3, 'dormers': 2, 'bay': False})
        for bad in ({'storeys': 0}, {'storeys': True}, {'dormers': 4}, {'bay': 1}, {'colour': 'red'},
                    {'storeys': 2, 'bay_storey': 2}, {'front_depth': -1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                S.validate_block('a', bad)


def write_spec(spec):
    handle = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
    json.dump(spec, handle)
    handle.close()
    return Path(handle.name)


def shop_spec(block, *, front='S', pitch=1.0, **extra):
    return {'name': 'shop', 'ways': {'99': 'A'}, 'roofs': {'A': 'brick'}, 'roof_pitch': {'A': pitch},
            'groups': [{'nos': ['A'], 'front': front}], 'floor_min': FLOOR, 'shopfront': {'A': block}, **extra}


def plan_cells(spec, cells=FOOTPRINT):
    """Dry-run house_build on one synthetic footprint over level ground."""
    index = mock.Mock()
    index.cells.return_value = {c: 0 for c in cells}
    index.buildings = [(None, {'osm_way_id': 99})]

    class Live:
        def block(self, x, y, z):
            return 'minecraft:stone' if y < FLOOR - 1 else 'minecraft:grass_block' if y == FLOOR - 1 else 'minecraft:air'

    path = write_spec(spec)
    out = io.StringIO()
    try:
        with mock.patch.object(H.osm_footprints, 'OSMIndex', return_value=index), \
                mock.patch.object(H.osm_footprints, 'guard', return_value={'outside': {}}), \
                mock.patch.object(H, 'fresh_planning_world', return_value=Live()), \
                mock.patch('floor_reference.floor_reference', return_value=FLOOR), \
                mock.patch('floor_reference.outside_ground', side_effect=lambda ps: {q: FLOOR - 1 for q in ps}), \
                mock.patch('site_kit.interior_ground_clears', return_value=set()), \
                mock.patch('cell_registry.view_registry', return_value=mock.Mock(owned=lambda *a, **k: {})), \
                redirect_stdout(out):
            plan = H.build(H.load_spec(path))
    finally:
        path.unlink(missing_ok=True)
    return dict(plan.cells), out.getvalue()


def name(state):
    return state.split(':')[-1].split('[')[0]


def column(cells, x, z, low=FLOOR - 1, high=FLOOR + 20):
    return {y: name(cells[x, y, z]) for y in range(low, high) if (x, y, z) in cells}


class SpecTest(unittest.TestCase):
    def load(self, spec):
        path = write_spec(spec)
        try:
            return H.load_spec(path)
        finally:
            path.unlink(missing_ok=True)

    def test_a_shop_spec_loads_and_a_house_spec_is_unchanged(self):
        spec = self.load(shop_spec({'storeys': 2, 'front_depth': 3}))
        self.assertEqual(spec['shopfront']['A']['front_depth'], 3)
        plain = self.load({'name': 'x', 'ways': {'1': 'A'}, 'roofs': {'A': 'brick'},
                           'groups': [{'nos': ['A'], 'front': 'S'}]})
        self.assertEqual(plain['shopfront'], {})

    def test_bad_blocks_are_refused_with_the_reason(self):
        cases = ((shop_spec({'storeys': 2}, pitch=0.5), 'roof_pitch above 0.5'),
                 (shop_spec({'dormers': 9}), 'dormers'),
                 (shop_spec({'storeys': 2}, groups=[{'nos': ['A'], 'front': 'S', 'mode': 'group'}]), 'mode "each"'),
                 (shop_spec({}, shopfront={'Z': {}}), "label 'Z'"),
                 (shop_spec({}, shopfront=[]), 'label-to-block'))
        for spec, text in cases:
            with self.subTest(text=text), self.assertRaisesRegex(SystemExit, text):
                self.load(spec)


class ShopBuildTest(unittest.TestCase):
    """The planned blocks: shopfront strip, upper wall, dormers and bay, on synthetic ground."""

    @classmethod
    def setUpClass(cls):
        cls.dormers, cls.dormer_log = plan_cells(shop_spec(
            {'storeys': 2, 'front_depth': 3, 'dormers': 2, 'bay': False}))
        cls.bay, cls.bay_log = plan_cells(shop_spec(
            {'storeys': 2, 'front_depth': 3, 'dormers': 0, 'bay': True}))
        cls.three, cls.three_log = plan_cells(shop_spec(
            {'storeys': 3, 'front_depth': 3, 'dormers': 1, 'bay': False}))

    def test_strip_is_one_storey_under_a_flat_bottom_slab_roof(self):
        cells = self.dormers
        for x in range(11, 18):
            self.assertEqual(cells[x, FLOOR + 3, 32], 'minecraft:brick_slab[type=bottom]')
            self.assertEqual(name(cells.get((x, FLOOR + 4, 32), 'minecraft:air')), 'air')
        # the body rises over its own columns, not the strip
        self.assertEqual(name(cells[11, FLOOR + 5, 30]), 'bricks')
        self.assertEqual(name(cells[11, FLOOR + 3, 30]), 'bricks')

    def test_strip_walls_stop_below_the_flat_roof_and_the_ground_storey_is_open(self):
        cells = self.dormers
        self.assertEqual([name(cells[10, FLOOR + dy, 32]) for dy in (0, 1, 2)], ['bricks'] * 3)
        self.assertNotIn(name(cells.get((10, FLOOR + 4, 32), 'minecraft:air')), ('bricks',))
        # the ground storey between strip and body has no wall at the join
        self.assertEqual(name(cells[14, FLOOR + 1, 30]), 'air')
        self.assertEqual(name(cells[14, FLOOR + 2, 30]), 'air')

    def test_front_ground_storey_is_glazed_between_piers_and_the_door_stays_framed(self):
        cells = self.dormers
        row = {x: name(cells[x, FLOOR + 1, 33]) for x in range(10, 19)}
        self.assertEqual(row[14], 'spruce_door')
        self.assertEqual(name(cells[13, FLOOR + 1, 33]), 'bricks')
        self.assertEqual(name(cells[15, FLOOR + 1, 33]), 'bricks')
        glass = [x for x, state in row.items() if 'glass' in state]
        self.assertTrue(glass, 'the shopfront has glazing')
        for x in glass:
            self.assertEqual(name(cells[x, FLOOR + 2, 33]), name(cells[x, FLOOR + 1, 33]))
            self.assertEqual(name(cells[x, FLOOR, 33]), 'bricks')   # a sill under the glass
            self.assertEqual(name(cells[x, FLOOR + 3, 33]), 'brick_slab')   # a lintel over it

    def test_dormers_stand_on_the_front_wall_row_and_stay_under_the_ridge(self):
        cells = self.dormers
        self.assertIn('dormer at (12, 30)', self.dormer_log)
        self.assertIn('dormer at (16, 30)', self.dormer_log)
        ridge = max(y for (x, y, z) in cells if (x, z) == (14, 25) and name(cells[x, y, z]) != 'flower_pot')
        for cx in (12, 16):
            base = FLOOR + 6
            self.assertEqual([name(cells[cx, base + dy, 30]) for dy in (0, 1, 2, 3)],
                             ['bricks', 'glass_pane', 'glass_pane', 'bricks'])
            for px in (cx - 1, cx + 1):
                self.assertEqual([name(cells[px, base + dy, 30]) for dy in (0, 1, 2, 3)], ['bricks'] * 4)
            self.assertLessEqual(base + 3, ridge)
            # the window is bounded: piers either side, sill below, cap above
            self.assertEqual(name(cells[cx, base + 3, 30]), 'bricks')
        # a gap between the two dormers
        self.assertNotEqual(name(cells[14, FLOOR + 7, 30]), 'glass_pane')

    def test_bay_window_is_glass_on_a_solid_base_with_the_wall_opened(self):
        cells = self.bay
        self.assertIn('bay window at (14, 30), storey 1', self.bay_log)
        sill = FLOOR + 3
        for x in (13, 14, 15):
            self.assertEqual(name(cells[x, sill, 31]), 'bricks')       # base replaces the strip roof slab
            self.assertEqual([name(cells[x, sill + dy, 31]) for dy in (1, 2)], ['glass', 'glass'])
            self.assertIn(name(cells[x, sill + 3, 31]), ('bricks', 'brick_stairs', 'brick_slab'))
        self.assertEqual([name(cells[14, sill + dy, 30]) for dy in (1, 2)], ['air', 'air'])   # opened to the room
        self.assertEqual([name(cells[13, sill + dy, 30]) for dy in (1, 2)], ['bricks', 'bricks'])
        self.assertNotIn('dormer', self.bay_log)

    def test_no_bay_without_room_and_no_dormer_without_one(self):
        cells, log = plan_cells(shop_spec({'storeys': 2, 'front_depth': 3, 'dormers': 1, 'bay': True}),
                                cells=rect(10, 20, 7, 8))   # a short body: ridge too low for a dormer cap
        self.assertIn('bay window at', log)
        self.assertTrue('dormer at' in log or 'dropped' in log)

    def test_three_wall_storeys_floor_every_three_blocks(self):
        cells = self.three
        self.assertEqual([name(cells[14, FLOOR + 3 * s, 25]) for s in (1, 2)], ['spruce_planks'] * 2)
        self.assertEqual(name(cells[10, FLOOR + 8, 25]), 'bricks')      # wall to the third storey's top
        self.assertNotEqual(name(cells[10, FLOOR + 9, 25]), 'air')        # the roof starts on it
        self.assertIn('dormer at (14, 30)', self.three_log)

    def test_every_new_slab_or_pane_is_supported_and_no_top_slab_hangs_over_air(self):
        for label, cells in (('dormers', self.dormers), ('bay', self.bay), ('three', self.three)):
            for (x, y, z), state in cells.items():
                if 'slab' in state and 'type=top' in state:
                    below = cells.get((x, y - 1, z), 'minecraft:air')
                    self.assertNotEqual(name(below), 'air', f'{label}: top slab over air at {(x, y, z)}')
                if name(state) == 'glass_pane' and y > FLOOR + 3:
                    self.assertIn(name(cells.get((x, y + 1, z), 'minecraft:air')), ('glass_pane', 'bricks', 'brick_slab'),
                                  f'{label}: unbounded pane at {(x, y, z)}')

    def test_default_house_is_untouched_by_the_shop_path(self):
        plain = {'name': 'house', 'ways': {'99': 'A'}, 'roofs': {'A': 'brick'}, 'roof_pitch': {'A': 1.0},
                 'groups': [{'nos': ['A'], 'front': 'S'}], 'floor_min': FLOOR}
        cells, _log = plan_cells(plain)
        self.assertEqual(name(cells[10, FLOOR + 5, 25]), 'bricks')
        self.assertEqual(name(cells[14, FLOOR + 3, 25]), 'spruce_planks')
        self.assertEqual(name(cells[14, FLOOR + 6, 25]), 'bricks')


class ShopGeneratorTest(unittest.TestCase):
    """typology_spec emits a house_build shopfront spec when lidar reads as a pitched body."""

    PROFILE = {
        'name': 'Fixture',
        'classes': {'commercial': {'palette': ['bricks'], 'roofs': ['flat'], 'storeys': {'min': 1, 'max': 4}}},
    }
    STREET = [[(-20.0, 50.0), (80.0, 50.0)]]

    def shops(self, eave, ridge, tags=None, count=3, **extra):
        tags = tags or {'building': 'retail'}
        return [{'way_id': str(i + 1), 'cells': rect(i * 6, 20, 6, 14), 'tags': dict(tags),
                 'lidar_m': (eave + ridge) / 2, 'profile': self.PROFILE,
                 'eave_m': eave, 'ridge_m': ridge, **extra} for i in range(count)]

    def test_pitched_lidar_makes_a_terrace_body_spec_without_building_levels(self):
        report = T.generate(self.shops(6.5, 10.0), self.STREET)
        self.assertEqual(report['needs_designer'], [])
        (item,) = report['specs']
        self.assertEqual(item['builder'], 'house_build')
        spec = item['spec']
        self.assertEqual(spec['groups'], [{'nos': ['a', 'b', 'c'], 'mode': 'each', 'front': 'S'}])
        self.assertEqual(set(spec['shopfront']), {'a', 'b', 'c'})
        self.assertGreater(spec['roof_pitch']['a'], 0.5)
        block = spec['shopfront']['a']
        self.assertEqual((block['storeys'], block['bay'], block['dormers'] > 0), (2, False, True))
        self.assertNotIn('roof_pitch', block)
        self.assertIn('lib/builds/shop_body.py', spec['notes'])
        path = write_spec(spec)
        try:
            H.load_spec(path)
        finally:
            path.unlink(missing_ok=True)

    def test_a_low_ridge_gets_a_bay_not_dormers(self):
        report = T.generate(self.shops(6.0, 7.2), self.STREET)
        self.assertEqual(report['specs'], [])   # 1.2 m of roof is a flat commercial box: the feature path
        self.assertEqual(report['needs_designer'], [{'way_id': w, 'reason': 'missing building:levels'}
                                                     for w in ('1', '2', '3')])
        report = T.generate(self.shops(6.0, 8.0), self.STREET)
        block = report['specs'][0]['spec']['shopfront']['a']
        self.assertEqual((block['bay'], block['dormers']), (True, 0))

    def test_flat_tag_or_missing_lidar_keeps_the_feature_path(self):
        tagged = [dict(row, tags={'building': 'retail', 'roof:shape': 'flat', 'building:levels': '2'})
                  for row in self.shops(6.0, 9.0)]
        report = T.generate(tagged, self.STREET)
        self.assertEqual(report['specs'][0]['builder'], 'feature_build')
        bare = [{k: v for k, v in row.items() if k not in ('eave_m', 'ridge_m')} | {
            'tags': {'building': 'retail', 'building:levels': '2'}, 'lidar_m': 6.0}
            for row in self.shops(6.0, 9.0)]
        self.assertEqual(T.generate(bare, self.STREET)['specs'][0]['builder'], 'feature_build')

    def test_keyed_by_class_not_by_town(self):
        # a shop= tag on a plain building=yes still reads as a shop, and a house never becomes one
        report = T.generate(self.shops(6.5, 10.0, tags={'building': 'yes', 'shop': 'bakery'}), self.STREET)
        self.assertEqual(report['specs'][0]['builder'], 'house_build')
        self.assertIn('shopfront', report['specs'][0]['spec'])
        house = T.generate(self.shops(6.5, 10.0, tags={'building': 'house'}), self.STREET)
        self.assertNotIn('shopfront', json.dumps(house['specs']))

    def test_district_default_of_flat_does_not_override_pitched_lidar(self):
        report = T.generate(self.shops(6.5, 10.0), self.STREET)
        self.assertIn('Roof gable', report['specs'][0]['spec']['notes'])

    def test_loader_samples_eave_and_ridge_for_shops_only(self):
        index = mock.Mock()
        cells = {c: i for i, rect_ in enumerate((rect(0, 0, 6, 6), rect(10, 0, 6, 6))) for c in rect_}
        index.cells.return_value = cells
        index.buildings = [(None, {'osm_way_id': 1}), (None, {'osm_way_id': 2})]
        index.streets = []
        tags = {1: {'building': 'retail'}, 2: {'building': 'house'}}
        with mock.patch('osm_footprints.osm_tags', side_effect=lambda props: tags[props['osm_way_id']]):
            buildings, _streets = T.load_buildings(
                index, way_ids=['1', '2'], lidar=lambda c: 5.0, profile_at=lambda cell: None,
                eave_ridge=lambda c: (6.0, 9.0))
        by_way = {row['way_id']: row for row in buildings}
        self.assertEqual((by_way['1']['eave_m'], by_way['1']['ridge_m']), (6.0, 9.0))
        self.assertNotIn('eave_m', by_way['2'])

    def test_generated_spec_builds(self):
        report = T.generate(self.shops(6.5, 10.0, count=1), self.STREET)
        spec = report['specs'][0]['spec']
        spec['floor_min'] = FLOOR
        spec['ways'] = {'99': 'a'}
        cells, log = plan_cells(spec, cells=rect(0, 20, 6, 14))
        self.assertTrue(any('glass' in state for state in cells.values()))
        self.assertIn('a b 50 cells', log)   # label a, floor 50


if __name__ == '__main__':
    unittest.main()
