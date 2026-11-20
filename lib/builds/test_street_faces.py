"""Class street faces from synthetic mapped footprints, names and street lines (#330)."""
from contextlib import redirect_stdout
import io
import json
import unittest
from unittest import mock

import numpy as np

import facade_plan as FP
import feature_build as F
import ground_sources as G
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


class StraightRunTest(unittest.TestCase):
    def test_straight_front_is_unchanged(self):
        fronts = {(x, 5): 'N' for x in range(4)}
        runs = F.fit_straight_runs(fronts)
        self.assertEqual(runs, [{'face': 'N', 'line': [(0, 5), (1, 5), (2, 5), (3, 5)], 'fill': set()}])

    def test_one_block_step_fills_the_notch_on_the_street_side(self):
        fronts = {(0, 1): 'N', (1, 0): 'N'}
        runs = F.fit_straight_runs(fronts)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]['face'], 'N')
        self.assertEqual(runs[0]['line'], [(0, 0), (1, 0)])
        self.assertEqual(runs[0]['fill'], {(0, 0)})


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

    def test_class_and_scale_selects_industrial_big_box_or_shop(self):
        def rule(cls, building, columns=80, height=6):
            return L.street_face_defaults(cls, {'building': building}, columns=columns, height=height)
        self.assertEqual(rule('industrial', 'industrial')['kind'], 'industrial')
        self.assertEqual(rule('commercial', 'retail', columns=2200)['kind'], 'big_box')
        self.assertEqual(rule('commercial', 'retail', height=18)['kind'], 'big_box')
        shop = rule('commercial', 'retail')
        self.assertEqual(shop['kind'], 'shop')
        self.assertFalse(shop['awning'])
        self.assertIsNone(rule('commercial', 'office', height=30))
        self.assertIsNone(rule('residential', 'house', columns=3000))
        self.assertIsNone(rule('unknown', 'yes', columns=3000))

    def test_awning_follows_shop_kind(self):
        def rule(**tags):
            tags.setdefault('building', 'retail')
            return L.street_face_defaults('commercial', tags, columns=80, height=6)
        for kind in ('cafe', 'restaurant', 'fast_food', 'food'):
            with self.subTest(kind=kind):
                dressed = rule(shop=kind)
                self.assertEqual(dressed['shop_kind'], kind)
                self.assertTrue(dressed['awning'])
        self.assertTrue(rule(building='yes', amenity='cafe')['awning'])
        self.assertFalse(rule(shop='books')['awning'])
        self.assertFalse(rule(shop='newsagent')['awning'])
        self.assertIsNone(L.street_face_defaults(
            'commercial', {'building': 'yes'}, columns=80, height=6))
        self.assertIsNone(L.street_face_defaults(
            'commercial', {'building': 'hotel'}, columns=80, height=6))
        tall_books = L.street_face_defaults(
            'commercial', {'building': 'retail', 'shop': 'books'}, columns=80, height=18)
        self.assertEqual(tall_books['kind'], 'shop')
        self.assertEqual(L.street_face_defaults(
            'commercial', {'building': 'supermarket'}, columns=80, height=18)['kind'], 'big_box')
        self.assertEqual(L.street_face_defaults(
            'commercial', {'building': 'retail', 'shop': 'supermarket'}, columns=80, height=18)['kind'],
            'big_box')

    def test_table_refuses_misplaced_or_invalid_recipes(self):
        for cls, recipe in [('unknown', {'kind': 'industrial'}),
                            ('industrial', {'kind': 'big_box'}),
                            ('industrial', {'kind': 'industrial', 'shutter_width': True})]:
            doc = json.loads(L.TABLE_PATH.read_text())
            row = next(r for r in doc['classes'] if r['id'] == cls)
            row['street_face'] = recipe
            with self.subTest(cls=cls, recipe=recipe), self.assertRaises(L.TableError):
                L.validate_table(doc)

    def test_table_refuses_a_shop_recipe_that_is_not_keyed_by_kind(self):
        doc = json.loads(L.TABLE_PATH.read_text())
        commercial = next(r for r in doc['classes'] if r['id'] == 'commercial')
        industrial = next(r for r in doc['classes'] if r['id'] == 'industrial')
        bad_shop = {'glass_height': 2, 'door': 'oak_door', 'sign_wrap': 15,
                    'kinds': {'High Street': {'awning': True}}}
        cases = [
            ('place name', commercial, bad_shop),
            ('glass height', commercial, {**commercial['street_face']['shop'], 'glass_height': True}),
            ('industrial shop', industrial, commercial['street_face']['shop']),
        ]
        for label, row, shop in cases:
            trial = json.loads(json.dumps(doc))
            target = next(r for r in trial['classes'] if r['id'] == row['id'])
            if label == 'industrial shop':
                target['street_face'] = {**target['street_face'], 'shop': shop}
            else:
                target['street_face']['shop'] = shop
            with self.subTest(label=label), self.assertRaises(L.TableError):
                L.validate_table(trial)


class StreetFaceBuildTest(unittest.TestCase):
    def setUp(self):
        self.cells = {(x, z) for x in range(10, 25) for z in range(20, 30)}

    def build(self, building='industrial', storeys=2, cells=None, streets=None, spec=None, world=None,
              tags=None, footprints=None, name='National Trade Supplies', zone_data=None):
        spec = spec or fixtures.BuildPlanTest.spec(self, storeys=storeys)
        ground = zone_data or {'A': {'ground_y': np.full((60, 60), 49), 'x0': -10, 'z0': -10}}
        body = {'building': building, 'name': name}
        if tags:
            body.update(tags)
        builder = F.Build(spec, footprints=footprints or {'A': cells or self.cells},
                          world=world or fixtures._StoneWorld(),
                          building_tags={'A': body},
                          zone_data=ground,
                          streets=[street()] if streets is None else streets)
        buf = io.StringIO()
        with redirect_stdout(buf):
            plan = builder.build()
        builder.output = buf.getvalue()
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

    def _shop(self, shop='books', **kwargs):
        tags = {'shop': shop, 'building': 'retail'}
        tags.update(kwargs.pop('tags', {}))
        kwargs.setdefault('storeys', 2)
        return self.build('retail', tags=tags, **kwargs)

    def test_shop_front_is_glass_door_fascia_and_contrasting_sign(self):
        name = next(candidate for candidate in (
            'Cyan Cafe', 'Orange Cafe', 'Blue Cafe', 'A', 'B', 'C', 'D', 'E')
            if F.fascia_colour(candidate) in F.LIGHT_FASCIA)
        _, plan = self._shop(shop='books', name=name)
        glass = {(x, y, z) for (x, y, z), state in plan.cells.items() if state == 'minecraft:glass'}
        self.assertTrue(glass)
        self.assertTrue(all(z == 20 and y in (51, 52) for x, y, z in glass))
        doors = {(x, y, z) for (x, y, z), state in plan.cells.items()
                 if state.startswith('minecraft:oak_door') and 'half=lower' in state}
        self.assertEqual(len(doors), 1)
        (dx, dy, dz), = doors
        self.assertEqual((dy, dz), (51, 20))
        self.assertNotIn((dx, 50, dz), {p for p, s in plan.cells.items() if 'door' in s})
        for jx in (dx - 1, dx + 1):
            for y in (51, 52):
                self.assertEqual(plan.cells[(jx, y, 20)], 'minecraft:bricks')
        fascia = plan.cells[(10, 54, 20)].removeprefix('minecraft:').removesuffix('_concrete')
        self.assertIn(fascia, F.LIGHT_FASCIA)
        self.assertEqual(len(plan.signs), 1)
        pos, lines, colour, glow = plan.signs[0]
        self.assertEqual(pos[1], 54)
        self.assertEqual(colour, 'black')
        self.assertFalse(glow)
        self.assertTrue(all(len(line) <= 15 for line in lines))
        self.assertIn('_concrete', plan.cells[(pos[0], pos[1], pos[2] + 1)])
        self.assertFalse(any('trapdoor' in state for state in plan.cells.values()))
        self.assertEqual(plan.cells[(24, 51, 25)], 'minecraft:bricks')
        self.assertEqual(plan.cells[(15, 51, 29)], 'minecraft:bricks')
        self.assertEqual(plan.cells[(12, 55, 20)], 'minecraft:bricks')

    def test_food_kinds_get_a_continuous_awning_on_the_street_face_only(self):
        for shop in ('cafe', 'restaurant', 'fast_food', 'food'):
            with self.subTest(shop=shop):
                _, plan = self._shop(shop=shop)
                awnings = {(x, y, z) for (x, y, z), state in plan.cells.items() if 'trapdoor' in state}
                self.assertEqual(awnings, {(x, 53, 19) for x in range(10, 25)})
                self.assertTrue(all('half=top' in plan.cells[pos] for pos in awnings))
                self.assertTrue(all(y <= 53 for x, y, z in awnings))
                for x in range(10, 25):
                    self.assertIn('_concrete', plan.cells[(x, 53, 20)])

    def test_shop_front_stays_off_party_and_alley_walls(self):
        spec = fixtures.BuildPlanTest.spec(self, storeys=2)
        spec['ways']['98'] = 'B'
        spec['storeys']['B'] = 2
        spec['roofs']['B'] = {'kind': 'flat', 'material': 'smooth_stone'}
        spec['groups'] = [{'nos': ['A', 'B'], 'front': 'S', 'mode': 'each'}]
        neighbour = {(x, 19) for x in range(10, 15)}
        streets = [street(), street(((30, 0), (30, 40)), service='alley')]
        grid = {'ground_y': np.full((60, 60), 49), 'x0': -10, 'z0': -10}
        _, plan = self._shop(
            shop='books', spec=spec, streets=streets,
            footprints={'A': self.cells, 'B': neighbour},
            zone_data={'A': grid, 'B': grid},
        )
        for x in range(10, 15):
            self.assertEqual(plan.cells[(x, 51, 20)], 'minecraft:bricks', x)
        self.assertEqual(plan.cells[(16, 51, 20)], 'minecraft:glass')
        self.assertEqual(plan.cells[(24, 51, 25)], 'minecraft:bricks')
        self.assertFalse(any('trapdoor' in state for state in plan.cells.values()))

    def test_stepped_front_squares_off_so_awning_sign_and_door_touch(self):
        cells = {(x, z) for x in range(10, 30) for z in range(10, 30) if 30 <= x + z <= 43}
        _, plan = self._shop(
            shop='cafe', cells=cells,
            streets=[street(((0, 20), (20, 0)), highway='pedestrian')])
        awnings = [(x, y, z) for (x, y, z), state in plan.cells.items() if 'trapdoor' in state]
        self.assertGreaterEqual(len(awnings), 3)
        self.assertTrue(len({x for x, y, z in awnings}) == 1 or len({z for x, y, z in awnings}) == 1)
        self.assertTrue(all(y <= 53 for x, y, z in awnings))
        solid = {'glass', 'concrete', 'bricks', 'door'}
        for x, y, z in awnings:
            self.assertTrue(any(
                any(kind in plan.cells.get((x + dx, y, z + dz), '') for kind in solid)
                for dx, dz in F.N4))
        doors = [(x, y, z, state) for (x, y, z), state in plan.cells.items()
                 if 'oak_door' in state and 'half=lower' in state]
        self.assertEqual(len(doors), 1)
        dx, dy, dz, state = doors[0]
        lateral = ((1, 0), (-1, 0)) if ('facing=north' in state or 'facing=south' in state) else ((0, 1), (0, -1))
        for sx, sz in lateral:
            self.assertIn('bricks', plan.cells.get((dx + sx, dy, dz + sz), ''))
            self.assertIn('bricks', plan.cells.get((dx + sx, dy + 1, dz + sz), ''))
        signs = [(x, y, z) for (x, y, z), state in plan.cells.items() if '_wall_sign[' in state]
        self.assertEqual(len(signs), 1)
        sx, sy, sz = signs[0]
        self.assertTrue(any('_concrete' in plan.cells.get((sx + dx, sy, sz + dz), '')
                            for dx, dz in F.N4))

    def test_explicit_facade_rows_skip_shop_dressing(self):
        spec = fixtures.BuildPlanTest.spec(self, storeys=2)
        spec['facade_plan'] = {'A': {'faces': {'N': {'glazing': [
            {'storey': 1, 'glazed_h': 1, 'pier': 2}]}}}}
        _, plan = self._shop(shop='cafe', spec=spec)
        self.assertFalse(any('trapdoor' in state or 'oak_door' in state for state in plan.cells.values()))

    def test_shop_without_a_street_keeps_the_window_grid(self):
        builder, plan = self._shop(shop='cafe', streets=[])
        self.assertIn('no mapped street front', builder.output)
        self.assertTrue(any('glass' in state for state in plan.cells.values()))
        self.assertFalse(any('trapdoor' in state or 'oak_door' in state
                             for state in plan.cells.values()))

    def test_single_storey_shop_keeps_the_window_grid(self):
        builder, plan = self._shop(shop='cafe', storeys=1)
        self.assertIn('shell too low for a fascia band', builder.output)
        self.assertTrue(any('glass' in state for state in plan.cells.values()))
        self.assertFalse(any('trapdoor' in state or 'oak_door' in state or '_wall_sign[' in state
                             for state in plan.cells.values()))


class LoadingBayTest(unittest.TestCase):
    def marks(self, streets=(), occupied=(), world=None):
        cells = {(x, z) for x in range(10, 25) for z in range(20, 30)}
        builder = F.Build(fixtures.BuildPlanTest.spec(self), footprints={'A': cells},
                          world=world or fixtures._StoneWorld(), streets=streets)
        feature = builder.features[0]
        feature.b = 50
        builder.by_no_all_cells = cells | set(occupied)
        opening = [(x, 20) for x in range(15, 19)]
        builder._loading_bay(feature, opening, {c: 'N' for c in opening}, 5)
        return {p for p, state in builder.p.cells.items() if state == 'minecraft:yellow_concrete'}

    def test_shallow_forecourt_ends_before_carriageway_and_closes_far_edge(self):
        for highway, tags, centre in [('service', {'width': '1'}, 17),
                                       ('service', {}, 16),
                                       ('residential', {}, 15),
                                       ('living_street', {'width': '3 m'}, 16.5)]:
            with self.subTest(highway=highway, tags=tags):
                points = [(-30, centre), (50, centre)]
                road = G.line_cells(points, G.road_width(dict(tags, highway=highway)) / 2)
                marks = self.marks(streets=[street(points, highway, **tags)])
                self.assertEqual(marks, {(x, 49, z) for x in range(15, 19) for z in (18, 19)
                                         if x in (15, 18) or z == 18})
                self.assertFalse({(x, z) for x, y, z in marks} & road.keys())

    def test_marks_do_not_resume_behind_an_intervening_building(self):
        occupied = {(16, 18)}  # An obstruction in the unpainted interior also limits the bay.
        self.assertEqual(self.marks(occupied=occupied), {(x, 49, 19) for x in range(15, 19)})

    def test_marks_stop_at_lawn_walls_or_missing_paving(self):
        for obstacle in ('lawn', 'wall', 'gap'):
            class Obstructed(fixtures._StoneWorld):
                def block(self, x, y, z):
                    if z == 18:
                        if obstacle == 'lawn' and y == 49:
                            return 'minecraft:grass_block'
                        if obstacle == 'wall' and 49 <= y <= 53:
                            return 'minecraft:stone'
                        if obstacle == 'gap':
                            return 'minecraft:air'
                    return super().block(x, y, z)
            with self.subTest(obstacle=obstacle):
                self.assertEqual(self.marks(world=Obstructed()),
                                 {(x, 49, 19) for x in range(15, 19)})


if __name__ == '__main__':
    unittest.main()
