"""Synthetic typology specs: a terrace, a semi, a shop parade, and a refusal.

Each decided spec is loaded and planned by the builder it names. No world data.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import numpy as np

import feature_build as F
import feature_spec as FS
import house_build as H
import land_use
import typology_spec as T


STREET = [[(-20.0, 30.0), (80.0, 30.0)]]
PROFILE = {
    'name': 'Fixture',
    'classes': {
        'residential': {'palette': ['bricks'], 'roofs': ['gable'], 'storeys': {'min': 1, 'max': 3}},
        'commercial': {'palette': ['bricks'], 'roofs': ['flat'], 'storeys': {'min': 1, 'max': 4}},
    },
}
PINNED = {
    'name': 'Pinned',
    'classes': {
        'residential': {'palette': ['bricks'], 'roofs': ['gable'], 'storeys': {'min': 2, 'max': 2}},
        'commercial': {'palette': ['bricks'], 'roofs': ['flat'], 'storeys': {'min': 2, 'max': 2}},
    },
}


def rect(x0, z0, width, depth):
    """Filled rectangle of block columns."""
    return {(x, z) for x in range(x0, x0 + width) for z in range(z0, z0 + depth)}


def building(way, cells, tags, lidar=8.0, profile=PROFILE):
    """One synthetic footprint with the tags and lidar the rules read.

    Pass ``profile=None`` for a cell with no district profile.
    """
    return {
        'way_id': str(way),
        'cells': cells,
        'tags': tags,
        'lidar_m': lidar,
        'profile': profile,
    }


def house_row(tag, count, levels='2', lidar=8.0):
    """Houses side by side, sharing the short wall, street to the south."""
    return [building(
        i + 1, rect(i * 5, 0, 5, 8),
        {'building': tag, 'building:levels': levels}, lidar=lidar,
    ) for i in range(count)]


class _Lidar:
    """Flat DTM so a feature plan can read a floor without a lidar cache."""

    def missing(self, *_args):
        return []

    def sample(self, _product, east, _north, geocraft=True):
        return np.full(np.asarray(east).shape, 38.0)


class _StoneWorld:
    """Solid stone through y=49, air above."""

    def block(self, x, y, z):
        return 'minecraft:stone' if y <= 49 else 'minecraft:air'


_lidar = mock.patch('lidar_provider.tile_client', return_value=_Lidar())


def setUpModule():
    _lidar.start()


def tearDownModule():
    _lidar.stop()


class TypologySpecs(unittest.TestCase):
    """The three shapes the ticket names, plus a building the rules will not guess."""

    def test_terrace_row_uses_pair_roofs_and_party_wall_chimneys(self):
        for tag in ('house', 'terrace'):
            with self.subTest(tag=tag):
                report = T.generate(house_row(tag, 3), STREET)
                self.assertEqual(report['needs_designer'], [])
                item = report['specs'][0]
                spec = item['spec']
                self.assertEqual(item['builder'], 'house_build')
                self.assertEqual(set(spec['ways']), {'1', '2', '3'})
                self.assertEqual(spec['groups'], [
                    {'nos': ['a', 'b'], 'mode': 'group', 'front': 'S'},
                    {'nos': ['c'], 'mode': 'each', 'front': 'S'},
                ])
                self.assertEqual(spec['chimneys'], {
                    'a': 'party_walls', 'b': 'party_walls', 'c': 'per_house',
                })
                self.assertEqual(spec['roofs']['a'], spec['roofs']['b'])
                self.assertNotEqual(spec['roofs']['a'], spec['roofs']['c'])
                self.assertEqual(
                    spec['roof_pitch'],
                    {'a': T.PITCHED_RISE, 'b': T.PITCHED_RISE, 'c': T.PITCHED_RISE},
                )
                self.assertGreater(T.PITCHED_RISE, 0.5)
                plan = plan_house(spec, {row['way_id']: row['cells'] for row in house_row(tag, 3)})
                self.assertTrue(any('_stairs[' in state for state in plan.cells.values()))
                pots = [s for s in plan.cells.values() if 'flower_pot' in s]
                self.assertEqual(len(pots), 2)

    def test_long_terrace_alternates_pair_roofs(self):
        houses = house_row('house', 20)
        report = T.generate(houses, STREET)
        self.assertEqual(report['needs_designer'], [])
        self.assertEqual(len(report['specs']), 1)
        spec = report['specs'][0]['spec']
        self.assertEqual(len(spec['groups']), 10)
        self.assertTrue(all(g['mode'] == 'group' and len(g['nos']) == 2 for g in spec['groups']))
        self.assertTrue(all(opt == 'party_walls' for opt in spec['chimneys'].values()))
        families = land_use.terrace_roof_families('residential')
        self.assertEqual(len(families), 2)
        for i, group in enumerate(spec['groups']):
            want = families[i % 2]
            for no in group['nos']:
                self.assertEqual(spec['roofs'][no], want, (i, no))
            if i > 0:
                prev = families[(i - 1) % 2]
                self.assertNotEqual(want, prev)
        footprints = {row['way_id']: row['cells'] for row in houses}
        plan = plan_house(spec, footprints)
        pots = [s for s in plan.cells.values() if 'flower_pot' in s]
        self.assertEqual(len(pots), 10)

    def test_rotated_terrace_row_keeps_the_recipe(self):
        houses = [
            building(
                i + 1, rect(0, i * 5, 8, 5),
                {'building': 'house', 'building:levels': '2'},
            )
            for i in range(4)
        ]
        street = [[(20.0, -10.0), (20.0, 40.0)]]
        report = T.generate(houses, street)
        self.assertEqual(report['needs_designer'], [])
        spec = report['specs'][0]['spec']
        self.assertEqual(len(spec['groups']), 2)
        self.assertEqual(spec['groups'][0]['mode'], 'group')
        self.assertIn(spec['groups'][0]['front'], 'NSEW')
        plan = plan_house(spec, {row['way_id']: row['cells'] for row in houses})
        pots = [s for s in plan.cells.values() if 'flower_pot' in s]
        self.assertEqual(len(pots), 2)
    def test_semi_detached_pair_keeps_separate_roofs(self):
        for tag in ('house', 'semidetached_house'):
            with self.subTest(tag=tag):
                report = T.generate(house_row(tag, 2), STREET)
                self.assertEqual(report['needs_designer'], [])
                spec = report['specs'][0]['spec']
                self.assertEqual(report['specs'][0]['builder'], 'house_build')
                self.assertEqual(spec['groups'][0]['mode'], 'each')
                self.assertEqual(spec['groups'][0]['front'], 'S')
                self.assertEqual(len(spec['ways']), 2)
                self.assertIn('semi-detached', spec['notes'])
                plan = plan_house(spec, {row['way_id']: row['cells'] for row in house_row(tag, 2)})
                self.assertTrue(plan.cells)

    def test_detached_house_is_its_own_spec(self):
        report = T.generate(house_row('detached', 1), STREET)
        self.assertEqual(report['needs_designer'], [])
        spec = report['specs'][0]['spec']
        self.assertEqual(spec['groups'], [{'nos': ['a'], 'mode': 'each', 'front': 'S'}])
        self.assertEqual(set(spec['ways']), {'1'})
        plan = plan_house(spec, {'1': rect(0, 0, 5, 8)})
        self.assertTrue(plan.cells)

    def test_shop_parade_is_a_feature_spec_with_flats(self):
        shops = [building(
            i + 1, rect(i * 6, 0, 6, 7),
            {'building': 'retail', 'building:levels': '3'}, lidar=9.0,
        ) for i in range(3)]
        report = T.generate(shops, STREET)
        self.assertEqual(report['needs_designer'], [])
        item = report['specs'][0]
        spec = item['spec']
        self.assertEqual(item['builder'], 'feature_build')
        self.assertEqual(spec['storeys'], {'a': 3, 'b': 3, 'c': 3})
        self.assertEqual(spec['roofs']['a'], {'kind': 'flat', 'material': 'brick'})
        self.assertEqual(spec['wall'], 'bricks')
        self.assertEqual(spec['groups'][0]['mode'], 'each')
        self.assertIn('shop with flats above', spec['notes'])
        loaded = FS.load_spec(write_spec(spec))
        footprints = {spec['ways'][row['way_id']]: row['cells'] for row in shops}
        with redirect_stdout(io.StringIO()):
            plan = F.build(loaded, footprints=footprints, world=_StoneWorld())
        self.assertTrue(any('slab' in state for state in plan.cells.values()))

    def test_odd_missing_unknown_and_disagreement_are_not_guessed(self):
        odd = building('odd', rect(0, 0, 3, 3) | rect(10, 0, 3, 3), {'building': 'house', 'building:levels': '2'})
        missing = building('missing', rect(20, 0, 5, 8), {})
        unknown = building('unknown', rect(40, 0, 5, 8), {'building': 'banana', 'building:levels': '2'})
        disagreed = [building(
            f'd{i + 1}', rect(100 + i * 5, 0, 5, 8),
            {'building': 'detached', 'building:levels': '2'},
        ) for i in range(3)]
        report = T.generate([odd, missing, unknown, *disagreed], STREET)
        self.assertEqual(report['specs'], [])
        reasons = {row['way_id']: row['reason'] for row in report['needs_designer']}
        self.assertEqual(reasons['odd'], 'odd footprint')
        self.assertEqual(reasons['missing'], 'missing tags')
        self.assertEqual(reasons['unknown'], 'unknown class')
        self.assertEqual(reasons['d1'], 'tag disagrees with the footprint')
        three = building('three', rect(140, 0, 5, 8), {'building': 'house', 'building:levels': '3'}, lidar=9)
        alone = T.generate([three], STREET)
        self.assertEqual(alone['needs_designer'], [{'way_id': 'three', 'reason': 'house builder is two storeys'}])

    def test_slivers_and_multi_part_shapes_are_odd_but_rotated_ones_are_not(self):
        diagonal = {(x, z) for z in range(8) for x in range(z, z + 8)}   # a rotated (sheared) rectangle
        sliver = {(i, i) for i in range(12)}
        shop_sliver = {(i, j) for i in range(8) for j in range(3)}   # 24 cells, a small shop is fine
        house = {'building': 'house', 'building:levels': '2'}
        report = T.generate([
            building('turned', diagonal, house, profile=PINNED),
            building('sliver', {(x + 100, z) for x, z in sliver}, house, profile=PINNED),
            building('shop', {(x, z + 28) for x, z in shop_sliver},
                     {'building': 'retail', 'building:levels': '2'}, profile=PINNED),
        ], [[(-50.0, 20.0), (150.0, 20.0)]])
        reasons = {row['way_id']: row['reason'] for row in report['needs_designer']}
        self.assertEqual(reasons, {'sliver': 'odd footprint'})
        self.assertEqual(len(report['specs']), 2)

    def test_thin_strips_are_refused_in_either_direction(self):
        house = {'building': 'house', 'building:levels': '2'}
        report = T.generate([
            building('w1', rect(0, 0, 1, 12), house, profile=PINNED),
            building('w2', rect(10, 0, 2, 10), house, profile=PINNED),
            building('z2', rect(20, 0, 10, 2), house, profile=PINNED),
            building('ok', rect(40, 0, 3, 8), house, profile=PINNED),
        ], [[(-20.0, 20.0), (80.0, 20.0)]])
        reasons = {row['way_id']: row['reason'] for row in report['needs_designer']}
        self.assertEqual(reasons, {'w1': 'odd footprint', 'w2': 'odd footprint', 'z2': 'odd footprint'})
        self.assertEqual(len(report['specs']), 1)

    def test_a_street_off_the_end_of_a_row_gives_no_front(self):
        house = {'building': 'house', 'building:levels': '2'}
        row = [building(f'r{i}', rect(0, 4 * i, 6, 3), house, profile=PINNED) for i in range(3)]
        for offset in (0.0, 0.3, -0.3):   # a street beside the end of the row, off by less than a block
            report = T.generate(row, [[(3.0 + offset, 30.0), (3.0 + offset, 60.0)]])
            self.assertEqual(report['specs'], [])
            self.assertEqual({r['reason'] for r in report['needs_designer']}, {'no clear front'})

    def test_footprints_one_block_apart_form_a_row_and_wider_gaps_do_not(self):
        house = {'building': 'house', 'building:levels': '2'}
        row = [building(f'r{i}', {(x, z + 4 * i) for x in range(0, 6) for z in range(0, 3)}, house,
                        profile=PINNED) for i in range(3)]   # one empty column between neighbours
        apart = [building(f'a{i}', rect(40 + 12 * i, 0, 6, 6), house, profile=PINNED) for i in range(2)]
        report = T.generate(row + apart, [[(30.0, -20.0), (30.0, 100.0)]])
        names = sorted(tuple(sorted(item['spec']['ways'])) for item in report['specs'])
        self.assertEqual(names, [('a0',), ('a1',), ('r0', 'r1', 'r2')])

    def test_front_follows_a_diagonal_street_and_only_a_missing_street_has_none(self):
        shop = {'building': 'retail', 'building:levels': '2'}
        diagonal = [[(0.0, 0.0), (60.0, 60.0)]]
        # the street's normal is exactly diagonal, so the axes tie and the north-south one wins
        for cells, side in ((rect(40, 10, 8, 6), 'S'), (rect(10, 40, 8, 6), 'N')):
            report = T.generate([building('s', cells, shop, profile=PINNED)], diagonal)
            self.assertEqual(report['specs'][0]['spec']['groups'][0]['front'], side)
        far = T.generate([building('s', rect(200, 200, 8, 6), shop, profile=PINNED)], diagonal)
        self.assertEqual(far['needs_designer'], [{'way_id': 's', 'reason': 'no street to face'}])

    def test_refused_neighbour_blocks_a_house_row_but_not_shops(self):
        house = {'building': 'house', 'building:levels': '2'}
        shop = {'building': 'retail', 'building:levels': '2'}
        for tags, expect in ((house, 'attached building cannot be decided'), (shop, None)):
            report = T.generate([
                building('x1', rect(0, 0, 6, 6), tags, profile=PINNED),
                building('x2', rect(6, 0, 6, 6), {}, profile=PINNED),
            ], [[(-20.0, 20.0), (60.0, 20.0)]])
            reasons = {row['way_id']: row['reason'] for row in report['needs_designer']}
            self.assertEqual(reasons.get('x1'), expect)

    def test_missing_levels_use_a_pinned_profile_storey_count(self):
        house = building('h', rect(0, 0, 5, 8), {'building': 'house'}, profile=PINNED)
        shop = building('s', rect(20, 0, 6, 7), {'building': 'retail'}, profile=PINNED)
        report = T.generate([house, shop], STREET)
        self.assertEqual(report['needs_designer'], [])
        by_builder = {item['builder']: item['spec'] for item in report['specs']}
        self.assertIn('Storeys 2 from the district profile.', by_builder['house_build']['notes'])
        self.assertIn('district palette (Pinned)', by_builder['house_build']['notes'])
        self.assertNotIn('class default', by_builder['house_build']['notes'])
        self.assertEqual(by_builder['feature_build']['storeys'], {'a': 2})
        self.assertIn('Storeys 2 from the district profile.', by_builder['feature_build']['notes'])
        loaded = FS.load_spec(write_spec(by_builder['feature_build']))
        footprints = {'a': rect(20, 0, 6, 7)}
        with redirect_stdout(io.StringIO()):
            plan = F.build(loaded, footprints=footprints, world=_StoneWorld())
        self.assertTrue(plan.cells)

    def test_a_storey_range_does_not_guess_a_missing_count(self):
        house = building('h', rect(0, 0, 5, 8), {'building': 'house'})
        report = T.generate([house], STREET)
        self.assertEqual(report['specs'], [])
        self.assertEqual(
            report['needs_designer'],
            [{'way_id': 'h', 'reason': 'district profile storeys min and max differ'}],
        )
        outside = building('s', rect(20, 0, 6, 7), {'building': 'retail', 'building:levels': '5'})
        refused = T.generate([outside], STREET)
        self.assertEqual(
            refused['needs_designer'],
            [{'way_id': 's', 'reason': 'storeys outside the district range'}],
        )

    def test_lidar_height_is_a_note_and_a_missing_height_is_not_a_refusal(self):
        tall = building('tall', rect(0, 0, 5, 8), {'building': 'house', 'building:levels': '2'}, lidar=20)
        report = T.generate([tall], STREET)
        self.assertEqual(report['needs_designer'], [])
        self.assertIn('Lidar height disagrees with building:levels.', report['specs'][0]['spec']['notes'])
        plan = plan_house(report['specs'][0]['spec'], {'tall': rect(0, 0, 5, 8)})
        self.assertTrue(plan.cells)
        missing = building('h', rect(20, 0, 5, 8), {'building': 'house', 'building:levels': '2'}, lidar=None)
        quiet = T.generate([missing], STREET)
        self.assertEqual(quiet['needs_designer'], [])
        self.assertIn('Lidar height missing.', quiet['specs'][0]['spec']['notes'])

    def test_missing_profile_uses_the_class_default_and_names_it(self):
        house = building('h', rect(0, 0, 5, 8), {'building': 'detached'}, profile=None, lidar=None)
        report = T.generate([house], STREET)
        self.assertEqual(report['needs_designer'], [])
        spec = report['specs'][0]['spec']
        self.assertEqual(report['specs'][0]['builder'], 'house_build')
        notes = spec['notes']
        self.assertIn('Roof gable from the land-use class default.', notes)
        self.assertIn('Material brick from the land-use class default (residential).', notes)
        self.assertIn('Storeys 2 from the land-use class default.', notes)
        self.assertIn('Lidar height missing.', notes)
        plan = plan_house(spec, {'h': rect(0, 0, 5, 8)})
        self.assertTrue(plan.cells)
        shop = building(
            's', rect(20, 0, 6, 7),
            {'building': 'retail', 'building:levels': '4', 'roof:shape': 'gabled'},
            profile=None, lidar=12.0,
        )
        observed = T.generate([shop], STREET)
        self.assertEqual(observed['needs_designer'], [])
        shop_spec = observed['specs'][0]['spec']
        self.assertEqual(shop_spec['storeys'], {'a': 4})
        self.assertEqual(shop_spec['roofs']['a']['kind'], 'pitched')
        self.assertIn('roof:shape', shop_spec['notes'])
        self.assertIn('land-use class default (commercial)', shop_spec['notes'])
        self.assertNotIn('Storeys', shop_spec['notes'])

    def test_a_profile_that_omits_the_class_falls_back_and_names_it(self):
        profile = {'name': 'Only shops', 'classes': {
            'commercial': {'palette': ['bricks'], 'roofs': ['flat'], 'storeys': {'min': 2, 'max': 2}},
        }}
        house = building('h', rect(0, 0, 5, 8), {'building': 'house'}, profile=profile)
        report = T.generate([house], STREET)
        self.assertEqual(report['needs_designer'], [])
        notes = report['specs'][0]['spec']['notes']
        self.assertIn('land-use class default (residential)', notes)
        self.assertNotIn('Only shops', notes)

    def test_a_class_with_no_default_is_not_guessed(self):
        church = building('c', rect(0, 0, 8, 6), {'building': 'church', 'building:levels': '2'}, profile=None)
        report = T.generate([church], STREET)
        self.assertEqual(report['specs'], [])
        self.assertEqual(report['needs_designer'], [{'way_id': 'c', 'reason': 'no class default'}])

    def test_district_roof_wins_over_the_class_default(self):
        profile = {
            'name': 'Hip district',
            'classes': {
                'residential': {'palette': ['bricks'], 'roofs': ['hip'], 'storeys': {'min': 2, 'max': 2}},
            },
        }
        house = building('h', rect(0, 0, 5, 8), {'building': 'house'}, profile=profile)
        report = T.generate([house], STREET)
        self.assertEqual(report['needs_designer'], [])
        notes = report['specs'][0]['spec']['notes']
        self.assertIn('Roof hip from the district profile.', notes)
        self.assertNotIn('class default', notes)


class LoadAndWrite(unittest.TestCase):
    """The loader pulls a whole touching row; dry run prints and --write saves."""

    def index(self):
        houses = house_row('house', 3)
        far = building('9', rect(200, 0, 5, 8), {'building': 'house', 'building:levels': '2'})
        rows = houses + [far]
        index = mock.Mock()
        index.buildings = []
        cells = {}
        for i, row in enumerate(rows):
            index.buildings.append((None, {
                'osm_way_id': row['way_id'],
                **row['tags'],
            }))
            for cell in row['cells']:
                cells[cell] = i
        index.cells.return_value = cells
        index.streets = [('High Street', 'residential', [(-20, 30), (80, 30)])]
        return index

    def test_bbox_pulls_the_rest_of_a_touching_terrace(self):
        buildings, streets = T.load_buildings(
            self.index(), bbox=(0, 0, 4, 7), lidar=lambda _cells: 8.0, profile_at=lambda _cell: PROFILE)
        self.assertEqual({row['way_id'] for row in buildings}, {'1', '2', '3'})
        report = T.generate(buildings, streets)
        self.assertEqual(report['needs_designer'], [])
        self.assertEqual(set(report['specs'][0]['spec']['ways']), {'1', '2', '3'})

    def test_a_missing_way_is_reported(self):
        buildings, streets = T.load_buildings(
            self.index(), way_ids=['99'], lidar=lambda _cells: 8.0, profile_at=lambda _cell: PROFILE)
        report = T.generate(buildings, streets)
        self.assertEqual(report['specs'], [])
        self.assertEqual(report['needs_designer'], [{'way_id': '99', 'reason': 'not in the vector map'}])

    def test_dry_run_prints_and_write_refuses_to_replace(self):
        index = self.index()
        original = T.load_buildings

        def fake(_index, *, way_ids=None, bbox=None, lidar=None, profile_at=None):
            return original(index, way_ids=way_ids, bbox=bbox,
                            lidar=lambda _cells: 8.0, profile_at=lambda _cell: PROFILE)

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch('osm_footprints.OSMIndex', return_value=index), \
                mock.patch.object(T, 'load_buildings', side_effect=fake):
            printed = io.StringIO()
            with redirect_stdout(printed):
                self.assertEqual(T.main(['--ways', '1', '2', '3']), 0)
            report = json.loads(printed.getvalue())
            self.assertEqual(report['specs'][0]['builder'], 'house_build')
            self.assertNotIn('wrote', report)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(T.main(['--ways', '1', '--write', '--out', tmp]), 0)
            spec_path = Path(tmp) / 'typology_terrace_1.json'
            self.assertTrue(spec_path.is_file())
            H.load_spec(spec_path)
            with self.assertRaises(SystemExit):
                T.write_specs(report, Path(tmp))


def write_spec(spec) -> Path:
    """Spec JSON in a temp file, as the builders' loaders require a path."""
    handle = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
    json.dump(spec, handle)
    handle.close()
    return Path(handle.name)


def plan_house(spec, footprints):
    """Dry-run house_build against the same columns the generator was given."""
    order = list(spec['ways'])
    index = mock.Mock()
    index.buildings = [(None, {'osm_way_id': way}) for way in order]
    cells = {}
    for i, way in enumerate(order):
        for cell in footprints[way]:
            cells[cell] = i
    index.cells.return_value = cells
    live = mock.Mock()
    live.block.side_effect = lambda x, y, z: 'minecraft:stone' if y <= 40 else 'minecraft:air'
    path = write_spec(spec)
    try:
        with mock.patch.object(H.osm_footprints, 'OSMIndex', return_value=index), \
                mock.patch.object(H.osm_footprints, 'guard', return_value={'outside': {}}), \
                mock.patch.object(H, 'fresh_planning_world', return_value=live), \
                mock.patch('floor_reference.floor_reference', return_value=40), \
                redirect_stdout(io.StringIO()):
            return H.build(H.load_spec(path))
    finally:
        path.unlink(missing_ok=True)


class RealFootprints(unittest.TestCase):
    """Footprints and street lines cut from the Southampton map (Begonia, Northcote, Portswood Road)."""

    DATA = json.loads((Path(__file__).parent / 'fixtures' / 'typology_real_footprints.json').read_text())

    def run_street(self, key):
        street = self.DATA[key]
        buildings = [building(row['way_id'], {tuple(c) for c in row['cells']},
                              {k: v for k, v in row['tags'].items() if k != 'osm_way_id'}, profile=PINNED)
                     for row in street['buildings']]
        return T.generate(buildings, [[tuple(p) for p in line] for line in street['streets']])

    def test_rotated_streets_produce_specs_with_a_front(self):
        for key in ('begonia', 'northcote', 'portswood'):
            report = self.run_street(key)
            self.assertNotIn('odd footprint', [row['reason'] for row in report['needs_designer']], key)
            self.assertTrue(report['specs'], key)
            for item in report['specs']:
                self.assertIn(item['spec']['groups'][0]['front'], 'NSEW', key)

    def test_northcote_houses_make_terrace_rows(self):
        report = self.run_street('northcote')
        self.assertEqual(report['needs_designer'], [])
        self.assertEqual(sorted(len(item['spec']['ways']) for item in report['specs']), [1, 3, 4])

    def test_portswood_shops_are_feature_specs_and_one_is_undecided(self):
        report = self.run_street('portswood')
        self.assertEqual({item['builder'] for item in report['specs']}, {'feature_build'})
        self.assertEqual(report['needs_designer'], [{'way_id': '149814015', 'reason': 'missing tags'}])
