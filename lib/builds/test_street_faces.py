"""Class street faces from synthetic mapped footprints, names and street lines (#330)."""
from contextlib import redirect_stdout
import io
import json
import unittest
from unittest import mock

import numpy as np

import facade_plan as FP
import feature_build as F
import land_use as L
import test_feature_build as fixtures
import test_facade_plan as facade_fixtures
import test_land_use as land_fixtures


def street(points=((-30, 10), (50, 10)), highway='service', **tags):
    return highway, list(points), tags


class FrontColumnsTest(unittest.TestCase):
    def setUp(self):
        self.cells = {(x, z) for x in range(10, 25) for z in range(20, 30)}

    def test_only_street_face_and_no_party_walls(self):
        front = FP.front_columns(self.cells, [street()])
        self.assertEqual(front, {(x, 20): 'N' for x in range(10, 25)})
        neighbour = {(x, 19) for x in range(10, 15)}
        self.assertEqual(set(FP.front_columns(self.cells, [street()], occupied=neighbour)),
                         {(x, 20) for x in range(15, 25)})

    def test_alleys_and_missing_streets_do_not_invent_fronts(self):
        for streets in ([], [street(service='alley')], [street(service='parking_aisle')],
                        [street(service='driveway')], [street(highway='path')]):
            with self.subTest(streets=streets):
                self.assertEqual(FP.front_columns(self.cells, streets), {})

    def test_obstructed_face_and_beyond_line_end_are_excluded(self):
        occupied = {(x, 15) for x in range(5, 30)}
        self.assertEqual(FP.front_columns(self.cells, [street()], occupied=occupied), {})
        self.assertEqual(FP.front_columns(self.cells, [street(((-20, 10), (0, 10)))]), {})

    def test_address_street_wins_over_nearer_side_road(self):
        streets = [street(name='Trade Road'), street(((5, 0), (5, 50)), name='Side Road')]
        self.assertEqual(FP.front_columns(self.cells, streets, street_name='Trade Road'),
                         {(x, 20): 'N' for x in range(10, 25)})

    def test_diagonal_front_and_pedestrian_street(self):
        cells = {(x, z) for x in range(10, 30) for z in range(10, 30)
                 if 30 <= x + z <= 43}
        front = FP.front_columns(cells, [street(((0, 20), (20, 0)), highway='pedestrian')])
        self.assertGreater(len(front), 8)
        self.assertTrue(all(x + z == 30 for x, z in front))
        self.assertTrue(set(front.values()) <= {'N', 'W'})


class StreetFaceDefaultsTest(unittest.TestCase):
    def build_context(self, tags, areas):
        idx = land_fixtures.index(
            areas=areas, buildings=[(land_fixtures.sq(10, 20, 25, 30), tags)])
        idx.buildings[0][1]['osm_way_id'] = '99'
        with mock.patch.object(F.osm_footprints, 'OSMIndex', return_value=idx):
            return F.Build(fixtures.BuildPlanTest.spec(self), world=fixtures._StoneWorld())

    def test_build_context_inherits_smallest_use_polygon_in_either_order(self):
        areas = [(land_fixtures.sq(0, 0, 40, 40), {'landuse': 'industrial'}),
                 (land_fixtures.sq(10, 15, 30, 35), {'landuse': 'retail'})]
        for ordered in (areas, list(reversed(areas))):
            with self.subTest(areas=ordered):
                builder = self.build_context({'building': 'yes'}, ordered)
                self.assertEqual(builder.land_uses['A'], 'commercial')

    def test_build_context_inherits_before_house_number_fallback(self):
        tags = {'building': 'yes', 'addr:housenumber': '4'}
        areas = [(land_fixtures.sq(0, 0, 40, 40), {'landuse': 'industrial'})]
        self.assertEqual(self.build_context(tags, areas).land_uses['A'], 'industrial')
        self.assertEqual(self.build_context(tags, []).land_uses['A'], 'residential')

    def test_build_context_own_class_tags_win_over_polygon(self):
        for tags, cls, use in [({'building': 'house'}, 'residential', 'industrial'),
                               ({'building': 'retail'}, 'commercial', 'industrial'),
                               ({'building': 'warehouse'}, 'industrial', 'retail'),
                               ({'building': 'yes', 'shop': 'bakery'}, 'commercial', 'industrial')]:
            with self.subTest(tags=tags):
                areas = [(land_fixtures.sq(0, 0, 40, 40), {'landuse': use})]
                self.assertEqual(self.build_context(tags, areas).land_uses['A'], cls)

    def test_class_and_scale_without_office_or_small_shop_defaults(self):
        def rule(cls, building, columns=80, height=6):
            return L.street_face_defaults(cls, {'building': building}, columns=columns, height=height)
        self.assertEqual(rule('industrial', 'industrial')['kind'], 'industrial')
        self.assertEqual(rule('commercial', 'retail', columns=2200)['kind'], 'big_box')
        self.assertEqual(rule('commercial', 'retail', height=18)['kind'], 'big_box')
        self.assertIsNone(rule('commercial', 'retail'))
        self.assertIsNone(rule('commercial', 'office', height=30))
        self.assertIsNone(rule('residential', 'house', columns=3000))
        self.assertIsNone(rule('unknown', 'yes', columns=3000))

    def test_table_refuses_misplaced_or_invalid_recipes(self):
        for cls, recipe in [('unknown', {'kind': 'industrial'}),
                            ('industrial', {'kind': 'big_box'}),
                            ('industrial', {'kind': 'industrial', 'shutter_width': True})]:
            doc = json.loads(L.TABLE_PATH.read_text())
            row = next(r for r in doc['classes'] if r['id'] == cls)
            row['street_face'] = recipe
            with self.subTest(cls=cls, recipe=recipe), self.assertRaises(L.TableError):
                L.validate_table(doc)


class StreetFaceBuildTest(unittest.TestCase):
    def setUp(self):
        self.cells = {(x, z) for x in range(10, 25) for z in range(20, 30)}

    def build(self, building='industrial', storeys=2, cells=None, streets=None, spec=None, world=None):
        spec = spec or fixtures.BuildPlanTest.spec(self, storeys=storeys)
        ground = {'A': {'ground_y': np.full((60, 60), 49), 'x0': -10, 'z0': -10}}
        builder = F.Build(spec, footprints={'A': cells or self.cells}, world=world or fixtures._StoneWorld(),
                          building_tags={'A': {'building': building, 'name': 'National Trade Supplies'}},
                          zone_data=ground,
                          streets=[street()] if streets is None else streets)
        with redirect_stdout(io.StringIO()):
            plan = builder.build()
        return builder, plan

    def test_industrial_shutter_fascia_and_bay_without_window_grid(self):
        _, plan = self.build()
        self.assertFalse(any('glass' in state for state in plan.cells.values()))
        shutters = {p for p, s in plan.cells.items() if s == 'minecraft:gray_concrete'}
        self.assertEqual(len(shutters), 16)
        self.assertEqual({p[2] for p in shutters}, {20})
        self.assertEqual({p[1] for p in shutters}, {51, 52, 53, 54})
        fascia = [plan.cells[(x, 55, 20)] for x in range(10, 25)]
        self.assertEqual(len(set(fascia)), 1)
        self.assertTrue(fascia[0].endswith('_concrete'))
        marks = {p for p, s in plan.cells.items() if s == 'minecraft:yellow_concrete'}
        self.assertEqual(len(marks), 12)
        self.assertTrue(all(y == 49 and z < 20 for x, y, z in marks))
        self.assertTrue(marks <= plan.keep)

    def test_big_box_base_and_attached_name_sign_without_roof_changes(self):
        _, plan = self.build('retail', storeys=6)
        glass = {p for p, s in plan.cells.items() if 'glass' in s}
        self.assertEqual(glass, {(x, y, 20) for x in range(10, 25) for y in range(51, 56)})
        self.assertEqual(len(plan.signs), 1)
        pos, lines, colour, glow = plan.signs[0]
        self.assertIn('National Trade Supplies', ' '.join(lines))
        self.assertEqual((pos[1], pos[2]), (56, 19))
        self.assertIn('_concrete', plan.cells[(pos[0], pos[1], pos[2] + 1)])
        self.assertEqual((colour, glow), ('white', True))
        self.assertTrue(all(len(line) <= 15 for line in lines))
        _, baseline = self.build('office', storeys=6)
        self.assertEqual({p: s for p, s in plan.cells.items() if p[1] >= 68},
                         {p: s for p, s in baseline.cells.items() if p[1] >= 68})

    def test_no_street_means_no_dressing_and_industrial_still_has_no_grid(self):
        _, plan = self.build(streets=[])
        self.assertFalse(any('glass' in s or s == 'minecraft:yellow_concrete' for s in plan.cells.values()))
        self.assertEqual(plan.cells[(15, 52, 20)], 'minecraft:bricks')

    def test_explicit_facade_rows_take_precedence(self):
        spec = fixtures.BuildPlanTest.spec(self, storeys=6)
        spec['facade_plan'] = {'A': {'faces': {'N': {'glazing': [
            {'storey': 1, 'glazed_h': 1, 'pier': 2}]}}}}
        _, plan = self.build('retail', storeys=6, spec=spec)
        self.assertEqual(plan.signs, [])
        self.assertEqual(plan.cells[(15, 56, 20)], 'minecraft:bricks')
        self.assertTrue(any('glass' in s for s in plan.cells.values()))

    def test_doors_and_jambs_survive_class_glazing(self):
        spec = fixtures.BuildPlanTest.spec(self, storeys=6)
        spec['facade_plan'] = {'A': {'entrances': [facade_fixtures._entrance(
            face='N', at=[17, 20], floor_offset=1)]}}
        _, plan = self.build('retail', storeys=6, spec=spec)
        self.assertIn('iron_door', plan.cells[(17, 51, 20)])
        for x in (16, 18):
            for y in (51, 52):
                self.assertEqual(plan.cells[(x, y, 20)], 'minecraft:bricks')

    def test_loading_marks_survive_existing_ground_ownership(self):
        from journal_testkit import JournalWorld
        with JournalWorld() as world:
            paving = {(x, 49, z): 'minecraft:gray_concrete'
                      for x in range(10, 25) for z in range(15, 20)}
            world.build('old_yard', 'ground', paving)
            _, plan = self.build()
            marks = {p for p, s in plan.cells.items() if s == 'minecraft:yellow_concrete'}
            self.assertEqual(len(marks), 12)
            world.build(plan.name, 'feature', plan.cells, payload=plan)
            self.assertTrue(all(world.blocks.read(p) == 'minecraft:yellow_concrete' for p in marks))

    def test_low_big_box_keeps_fascia_and_sign_below_roof(self):
        cells = {(x, z) for x in range(10, 61) for z in range(20, 61)}
        ground = {'A': {'ground_y': np.full((80, 80), 49), 'x0': 0, 'z0': 0}}
        builder = F.Build(fixtures.BuildPlanTest.spec(self, storeys=1), footprints={'A': cells},
                          world=fixtures._StoneWorld(), building_tags={'A': {
                              'building': 'retail', 'name': 'Big Store'}},
                          streets=[street(((0, 10), (80, 10)))], zone_data=ground)
        with redirect_stdout(io.StringIO()):
            plan = builder.build()
        self.assertEqual(len(plan.signs), 1)
        self.assertEqual(plan.signs[0][0][1], 52)
        self.assertTrue(all('slab' in s for (x, y, z), s in plan.cells.items()
                            if y == 53 and (x, z) in cells))

    def test_diagonal_big_box_glass_and_sign_land_on_wall(self):
        cells = {(x, z) for x in range(10, 30) for z in range(10, 30) if 30 <= x + z <= 43}
        builder, plan = self.build('retail', storeys=6, cells=cells,
                                   streets=[street(((0, 20), (20, 0)), highway='pedestrian')])
        front = FP.front_columns(cells, builder.streets)
        self.assertGreater(sum('glass' in s for s in plan.cells.values()), 40)
        self.assertEqual(len(plan.signs), 1)
        for (x, y, z), state in plan.cells.items():
            if '_wall_sign[' in state:
                self.assertTrue(any((x - dx, z - dz) in front and
                                    '_concrete' in plan.cells.get((x - dx, y, z - dz), '')
                                    for dx, dz in F.N4))

    def test_loading_marks_do_not_paint_lawn_or_foreign_buildings(self):
        class Lawn(fixtures._StoneWorld):
            def block(self, x, y, z):
                return 'minecraft:grass_block' if y == 49 else super().block(x, y, z)
        _, plan = self.build(world=Lawn())
        self.assertNotIn('minecraft:yellow_concrete', plan.cells.values())


if __name__ == '__main__':
    unittest.main()
