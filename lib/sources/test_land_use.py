"""Land-use classifier and defaults table (#253). Synthetic inputs only: no world, no map files."""
from __future__ import annotations

import copy
import json
import unittest
from collections import Counter

import coverage as C
import land_use as L
from osm_footprints import OSMIndex


def sq(x0, z0, x1, z1):
    return [[(x0, z0), (x1, z0), (x1, z1), (x0, z1), (x0, z0)]]


def index(areas=(), buildings=(), landuse_extent=(-200, -200, 400, 400)):
    """OSMIndex with no clips: polygons are given in block coordinates."""
    idx = OSMIndex.__new__(OSMIndex)
    idx.areas = [(r, dict(p, osm_way_id=str(i + 1))) for i, (r, p) in enumerate(areas)]
    idx.buildings = [(r, dict(p, osm_way_id=str(100 + i))) for i, (r, p) in enumerate(buildings)]
    idx.extents = [(-200, -200, 400, 400)]
    idx.streets, idx.street_props, idx.entrances, idx._cells = [], [], [], None
    idx.layer_extents = {'landuse': [landuse_extent] if landuse_extent else []}
    return idx


BUILDING_CASES = {
    'residential': {'building': 'house'},
    'commercial': {'building': 'retail'},
    'industrial': {'building': 'warehouse'},
    'civic': {'building': 'school'},
    'park': {'building': 'pavilion'},
    'rural': {'building': 'barn'},
    'transport': {'building': 'train_station'},
    'parking': {'building': 'parking'},
}
AREA_CASES = {
    'residential': {'landuse': 'residential'},
    'commercial': {'landuse': 'retail'},
    'industrial': {'landuse': 'industrial'},
    'civic': {'amenity': 'hospital'},
    'park': {'leisure': 'park'},
    'rural': {'landuse': 'farmland'},
    'natural': {'natural': 'wood'},
    'water': {'natural': 'water'},
    'transport': {'landuse': 'railway'},
    'parking': {'amenity': 'parking'},
}


class EachClass(unittest.TestCase):
    def test_every_class_has_a_building_or_area_case_or_is_unknown(self):
        self.assertEqual(set(BUILDING_CASES) | set(AREA_CASES) | {'unknown'}, set(L.CLASSES))

    def test_building_tag_per_class(self):
        for cls, tags in BUILDING_CASES.items():
            with self.subTest(cls):
                self.assertEqual(L.classify_building(tags)['class'], cls)

    def test_area_tag_per_class(self):
        for cls, tags in AREA_CASES.items():
            with self.subTest(cls):
                self.assertEqual(L.area_class(tags)['class'], cls)

    def test_unknown_reasons(self):
        self.assertEqual(L.classify_building({'building': 'yes'}, checked=False)['reason'], 'not_checked')
        self.assertEqual(L.classify_building({'building': 'yes'})['reason'], 'unmapped')
        self.assertEqual(L.classify_building({'building': 'moonbase'})['reason'], 'unlisted')
        self.assertEqual(L.classify_column(None, checked=False), {'class': 'unknown', 'reason': 'not_checked', 'ref': None})
        self.assertEqual(L.area_class({'landuse': 'banana'})['reason'], 'unlisted')
        self.assertEqual(L.area_class({'landuse': 'military'})['class'], 'unknown')

    def test_underground_parking_and_plain_outline_are_ignored(self):
        self.assertIsNone(L.area_class({'amenity': 'parking', 'parking': 'underground'}))
        self.assertIsNone(L.area_class({'type': 'multipolygon'}))


class BuildingRules(unittest.TestCase):
    def test_other_tags_beat_building_value(self):
        self.assertEqual(L.classify_building({'building': 'house', 'shop': 'bakery'})['class'], 'commercial')
        self.assertEqual(L.classify_building({'building': 'yes', 'amenity': 'school'})['class'], 'civic')
        self.assertEqual(L.classify_building({'building': 'yes', 'amenity': 'bus_station'})['class'], 'transport')
        self.assertEqual(L.classify_building({'building': 'yes', 'amenity': 'cafe'})['class'], 'commercial')

    def test_building_tag_wins_over_polygon(self):
        got = L.classify_building({'building': 'house'}, polygon=('commercial', 'w1'))
        self.assertEqual(got['class'], 'residential')

    def test_yes_takes_polygon_then_house_number_then_unknown(self):
        self.assertEqual(L.classify_building({'building': 'yes'}, polygon=('commercial', 'w1'))['class'], 'commercial')
        self.assertEqual(L.classify_building({'building': 'yes', 'addr:housenumber': '4'})['class'], 'residential')
        self.assertEqual(L.classify_building({'building': 'yes'})['class'], 'unknown')

    def test_outbuilding_inherits(self):
        self.assertEqual(L.classify_building({'building': 'shed'}, polygon=('park', 'w1'))['class'], 'park')


class AreaRaster(unittest.TestCase):
    def test_smallest_use_polygon_wins(self):
        idx = index(areas=[(sq(0, 0, 40, 40), {'landuse': 'residential'}),
                           (sq(10, 10, 20, 20), {'landuse': 'retail'})])
        got = C.area_classes(idx, (0, 0, 50, 50))
        self.assertEqual(got[(15, 15)][0], 'commercial')
        self.assertEqual(got[(30, 30)][0], 'residential')
        self.assertNotIn((45, 45), got)

    def test_grass_inside_university_is_civic_grass_alone_is_park(self):
        idx = index(areas=[(sq(0, 0, 40, 40), {'amenity': 'university'}),
                           (sq(5, 5, 10, 10), {'landuse': 'grass'}),
                           (sq(60, 60, 70, 70), {'landuse': 'grass'})])
        got = C.area_classes(idx, (0, 0, 80, 80))
        self.assertEqual(got[(7, 7)][0], 'civic')
        self.assertEqual(got[(65, 65)][:1], ('park',))
        self.assertEqual(got[(65, 65)][2], 'cover')

    def test_large_polygon_with_no_vertex_in_the_cell_still_classifies(self):
        idx = index(areas=[(sq(-500, -500, 500, 500), {'landuse': 'industrial'})])
        self.assertEqual(C.area_classes(idx, (0, 0, 10, 10))[(5, 5)][0], 'industrial')

    def test_unlisted_value_claims_nothing_and_is_counted(self):
        idx = index(areas=[(sq(0, 0, 10, 10), {'landuse': 'banana'})])
        report = Counter()
        self.assertEqual(C.area_classes(idx, (0, 0, 10, 10), report), {})
        self.assertEqual(report['landuse=banana'], 1)

    def test_house_in_retail_is_residential_by_tag_yes_in_retail_is_commercial(self):
        idx = index(areas=[(sq(0, 0, 40, 40), {'landuse': 'retail'})],
                    buildings=[(sq(5, 5, 9, 9), {'building': 'house'}),
                               (sq(20, 20, 24, 24), {'building': 'yes'})])
        classes = C.area_classes(idx, (0, 0, 50, 50))
        got = [C.building_land_use(idx, r, p, classes) for r, p in idx.buildings]
        self.assertEqual([g['class'] for g in got], ['residential', 'commercial'])

    def test_not_checked_versus_unmapped(self):
        for extent, reason in (((-200, -200, 400, 400), 'unmapped'), (None, 'not_checked')):
            with self.subTest(reason):
                idx = index(buildings=[(sq(5, 5, 9, 9), {'building': 'yes'})], landuse_extent=extent)
                got = C.building_land_use(idx, idx.buildings[0][0], idx.buildings[0][1], {})
                self.assertEqual((got['class'], got['reason']), ('unknown', reason))

    def test_no_neighbour_inheritance(self):
        idx = index(areas=[(sq(0, 0, 10, 10), {'landuse': 'residential'})],
                    buildings=[(sq(20, 20, 24, 24), {'building': 'yes'})])
        classes = C.area_classes(idx, (0, 0, 50, 50))
        self.assertEqual(C.building_land_use(idx, *idx.buildings[0], classes)['class'], 'unknown')


class BuildingCentroid(unittest.TestCase):
    def test_holes_cover_and_unlisted_polygons_do_not_supply_inheritance(self):
        retail = sq(0, 0, 30, 30) + sq(10, 10, 20, 20)
        idx = index(areas=[(sq(-500000, -500000, 500000, 500000), {'landuse': 'industrial'}),
                           (retail, {'landuse': 'retail'}),
                           (sq(10, 10, 20, 20), {'landuse': 'grass'}),
                           (sq(10, 10, 20, 20), {'landuse': 'banana'})],
                    buildings=[(sq(12, 12, 18, 18), {'building': 'yes'})])
        got = C.building_land_use(idx, *idx.buildings[0])
        self.assertEqual((got['class'], got['reason'], got['ref']),
                         ('industrial', 'use_polygon', 'w1'))
        idx.areas.pop(0)
        self.assertEqual(C.building_land_use(idx, *idx.buildings[0])['class'], 'unknown')

    def test_holes_are_subtracted_when_ranking_whole_polygon_sizes(self):
        retail = sq(-10, -10, 50, 50) + sq(10, -9, 49, 49)
        idx = index(areas=[(sq(0, 0, 40, 40), {'landuse': 'industrial'}),
                           (retail, {'landuse': 'retail'})],
                    buildings=[(sq(5, 5, 9, 9), {'building': 'yes'})])
        self.assertEqual(C.building_land_use(idx, *idx.buildings[0])['class'], 'commercial')

    def test_split_reference_sizes_include_parts_outside_the_centroid(self):
        idx = index(areas=[(sq(0, 0, 20, 20), {'landuse': 'industrial'}),
                           (sq(5, 5, 15, 15), {'landuse': 'retail'}),
                           (sq(100, 100, 150, 150), {'landuse': 'retail'})],
                    buildings=[(sq(6, 6, 10, 10), {'building': 'yes'})])
        idx.areas[2][1]['osm_way_id'] = idx.areas[1][1]['osm_way_id']
        for ordered in (idx.areas, list(reversed(idx.areas))):
            with self.subTest(refs=[p['osm_way_id'] for _, p in ordered]):
                idx.areas = ordered
                self.assertEqual(C.building_land_use(idx, *idx.buildings[0])['class'], 'industrial')

    def test_repeated_polygon_does_not_inflate_its_size(self):
        idx = index(areas=[(sq(0, 0, 20, 20), {'landuse': 'industrial'}),
                           (sq(2, 2, 18, 18), {'landuse': 'retail'})],
                    buildings=[(sq(6, 6, 10, 10), {'building': 'yes'})])
        idx.areas.append(idx.areas[1])
        self.assertEqual(C.building_land_use(idx, *idx.buildings[0])['class'], 'commercial')


class CheckedExtent(unittest.TestCase):
    def test_layer_extent_comes_from_clip_name(self):
        from pathlib import Path
        import osm_footprints as F
        self.assertEqual(F._query_bbox(Path('a+landuse-cell-3-4.geojson'), 'landuse'), (3, 4, 130, 131))
        self.assertIsNone(F._query_bbox(Path('buildings+highways-cell-3-4.geojson'), 'landuse'))
        self.assertEqual(F._query_bbox(Path('buildings+highways-cell-3-4.geojson')), (3, 4, 130, 131))


class Table(unittest.TestCase):
    def setUp(self):
        self.doc = copy.deepcopy(json.loads(L.TABLE_PATH.read_text()))

    def row(self, cid):
        return next(r for r in self.doc['classes'] if r['id'] == cid)

    def test_shipped_table_valid_and_no_fence_default(self):
        rows = L.validate_table(self.doc)['classes']
        for cid, row in rows.items():
            self.assertTrue(all(v is None for v in row['frontage']['boundary'].values()), cid)
        for cid in L.NO_INFERENCE:
            self.assertFalse(rows[cid]['frontage']['inference'], cid)

    def test_residential_row_is_todays_behaviour(self):
        fr = L.defaults('residential')['frontage']
        self.assertEqual((fr['inference'], fr['requires'], fr['private_surface'], fr['opening']),
                         (True, ['identified_property', 'front_segment'], 'grass', 'door_route'))

    def test_duplicate_tag_refused(self):
        self.row('commercial')['match']['use']['landuse'].append('residential')
        with self.assertRaisesRegex(L.TableError, 'maps to'):
            L.validate_table(self.doc)

    def test_missing_column_refused(self):
        del self.row('park')['frontage']['opening']
        with self.assertRaisesRegex(L.TableError, 'opening'):
            L.validate_table(self.doc)

    def test_unknown_with_boundary_refused(self):
        self.row('unknown')['frontage']['boundary']['front'] = {'type': 'fence', 'height': 1}
        with self.assertRaisesRegex(L.TableError, 'unknown must not'):
            L.validate_table(self.doc)

    def test_no_inference_class_refused_when_inferring(self):
        self.row('rural')['frontage']['inference'] = True
        with self.assertRaisesRegex(L.TableError, 'no inference'):
            L.validate_table(self.doc)

    def test_shipped_unmapped_paving_per_class(self):
        got = {cid: L.unmapped_paving(cid) for cid in L.CLASSES}
        self.assertEqual({k: v for k, v in got.items() if v},
                         {'park': 'dirt', 'rural': 'grass', 'natural': 'grass'})
        self.assertIsNone(L.unmapped_paving('residential'))
        self.assertIsNone(L.unmapped_paving('banana'))

    def test_unmapped_paving_is_optional_and_checked(self):
        del self.row('park')['surface']
        L.validate_table(self.doc)                                  # a missing surface column infers nothing
        self.row('park')['surface'] = {'unmapped_paving': 'concrete'}
        with self.assertRaisesRegex(L.TableError, 'unmapped_paving'):
            L.validate_table(self.doc)
        self.row('park')['surface'] = {'unmapped_paving': 'dirt', 'floor': 'podzol'}
        with self.assertRaisesRegex(L.TableError, 'surface must be'):
            L.validate_table(self.doc)
        self.row('park')['surface'] = {'unmapped_paving': None}
        L.validate_table(self.doc)

    def test_shipped_path_widen_per_class(self):
        got = {cid: L.path_widen(cid) for cid in L.CLASSES}
        self.assertEqual({k: v for k, v in got.items() if v}, {'park': 3})
        self.assertIsNone(L.path_widen('residential'))
        self.assertIsNone(L.path_widen('banana'))

    def test_shipped_shade_cover_is_park_and_rural_only(self):
        got = {cid: L.tree_cover(cid) for cid in L.CLASSES}
        self.assertEqual({k: v for k, v in got.items() if v}, {'park': 'shade', 'rural': 'shade'})
        self.assertIsNone(L.tree_cover('natural'))
        self.assertIsNone(L.tree_cover('unknown'))
        self.assertIsNone(L.tree_cover('banana'))

    def test_tree_cover_is_optional_and_checked(self):
        self.row('rural')['trees'].pop('cover')
        L.validate_table(self.doc)
        for good in (None, 'shade'):
            self.row('rural')['trees']['cover'] = good
            L.validate_table(self.doc)
        for bad in (False, '', 'dark', 0.4, ['shade']):
            self.row('rural')['trees']['cover'] = bad
            with self.subTest(bad=bad), self.assertRaisesRegex(L.TableError, 'cover'):
                L.validate_table(self.doc)

    def test_shipped_tree_sources_only_gate_open_land(self):
        got = {cid: L.tree_sources(cid) for cid in L.CLASSES}
        self.assertEqual({k: v for k, v in got.items() if v is not None},
                         dict.fromkeys(('park', 'rural', 'natural'), ('mapped', 'wood', 'lidar', 'lines')))
        self.assertIsNone(L.tree_sources('banana'))
        self.assertEqual(L.classes_with_source(L.LINE_SOURCE), frozenset({'park', 'rural', 'natural'}))
        self.assertEqual(L.MIN_STANDING_M, 2.0)
        self.assertEqual(L.tree_cover('park'), 'shade')
        self.assertEqual(L.tree_cover('rural'), 'shade')
        self.assertIsNone(L.tree_cover('natural'))

    def test_tree_sources_are_optional_and_checked(self):
        self.row('park').pop('trees', None)
        L.validate_table(self.doc)
        for good in (None, {}, {'sources': None}, {'sources': []}, {'sources': ['mapped', 'lidar']},
                     {'sources': ['lines']}, {'sources': ['mapped', 'wood', 'lidar', 'lines']}):
            self.row('park')['trees'] = good
            L.validate_table(self.doc)
        for bad in (False, [], {'fill': 'dense'}, {'sources': 'mapped'},
                    {'sources': ['aerial']}, {'sources': ['darkness']}, {'sources': ['mapped', 'mapped']},
                    {'sources': [True]}):
            self.row('park')['trees'] = bad
            with self.assertRaisesRegex(L.TableError, 'trees'):
                L.validate_table(self.doc)

    def test_shipped_species_mix_is_placeholder_and_parkland_stays_oak(self):
        self.assertEqual(L.tree_species_weights('natural'), {'oak': 60, 'birch': 25, 'dark_oak': 15})
        for cid in ('park', 'rural'):
            self.assertEqual(L.tree_species_weights(cid), {'oak': 100})
        self.assertEqual(L.tree_species_weights('banana'), {'oak': 1})
        weights = L.tree_species_weights('natural')
        weights['oak'] = 0
        self.assertEqual(L.tree_species_weights('natural')['oak'], 60)

    def test_species_weights_are_optional_and_checked(self):
        for good in (None, {'oak': 1}, {'birch': 2.5, 'oak': 0}, {'oak': 60, 'birch': 25, 'dark_oak': 15}):
            self.row('natural')['trees']['species_weights'] = good
            L.validate_table(self.doc)
        self.row('natural')['trees'].pop('species_weights')
        L.validate_table(self.doc)
        for bad in (False, [], {}, {'spruce': 1}, {'oak': True}, {'oak': -1}, {'oak': 0},
                    {'oak': '60'}, {'oak': float('nan')}, {'oak': float('inf')},
                    {'oak': 1e308, 'birch': 1e308}, {'oak': 10**400}):
            self.row('natural')['trees']['species_weights'] = bad
            with self.subTest(bad=bad), self.assertRaisesRegex(L.TableError, 'species_weights'):
                L.validate_table(self.doc)

    def test_path_widen_is_optional_and_checked(self):
        self.row('park')['surface'] = {'unmapped_paving': 'dirt'}
        L.validate_table(self.doc)                                  # absent key widens nothing
        for bad in (0, 7, 2.5, '3', True):
            self.row('park')['surface'] = {'path_widen': bad}
            with self.assertRaisesRegex(L.TableError, 'path_widen'):
                L.validate_table(self.doc)
        for good in (None, 1, 6):
            self.row('park')['surface'] = {'path_widen': good}
            L.validate_table(self.doc)

    def test_unknown_row_column_refused(self):
        self.row('park')['banana'] = None
        with self.assertRaisesRegex(L.TableError, 'unknown column'):
            L.validate_table(self.doc)

    def test_shipped_ground_texture_only_on_open_land(self):
        got = {cid: L.ground_texture(cid) for cid in L.CLASSES}
        for cid in ('park', 'rural', 'natural'):
            self.assertEqual(set(got[cid]), {'pasture', 'meadow', 'wood'})
            self.assertEqual(got[cid]['pasture']['tufts'], [0.08, 0.30])
            self.assertEqual(got[cid]['meadow']['flowers']['kinds'],
                             ['oxeye_daisy', 'cornflower', 'dandelion'])
            self.assertIsNone(got[cid]['pasture']['flowers'])
            self.assertEqual(got[cid]['wood'], {
                'podzol': 0.30, 'coarse_dirt': 0.10, 'moss_block': 0.08,
                'edge': 2, 'scrub': False})
        self.assertIsNone(got['residential'])
        self.assertIsNone(L.ground_texture('banana'))

    def test_ground_texture_is_optional_and_checked(self):
        self.row('park').pop('ground_texture', None)
        L.validate_table(self.doc)
        good_flowers = {'share': 0.015, 'kinds': ['oxeye_daisy', 'dandelion']}
        good = {'pasture': {'tufts': [0.08, 0.3], 'tall_grass': [0.01, 0.07],
                            'bare': True, 'flowers': None},
                'meadow': {'tufts': [0, 1], 'tall_grass': [0, 0], 'bare': False,
                           'flowers': good_flowers}}
        wood = {'podzol': 0.30, 'coarse_dirt': 0.10, 'moss_block': 0.08, 'edge': 2, 'scrub': False}
        for value in (None, {}, {'pasture': good['pasture']}, good, {'wood': wood},
                      dict(good, wood=wood)):
            self.row('park')['ground_texture'] = value
            L.validate_table(self.doc)
        for bad in (False, [], {'woodland': good['pasture']},
                    {'pasture': dict(good['pasture'], extra=1)},
                    {'pasture': dict(good['pasture'], tufts=[0.3, 0.08])},
                    {'pasture': dict(good['pasture'], bare='yes')},
                    {'meadow': dict(good['meadow'], flowers={'share': 0.1, 'kinds': ['poppy']})},
                    {'meadow': dict(good['meadow'], flowers={'share': 0.015, 'kinds': ['stone']})},
                    {'wood': dict(wood, podzol=0.9)},
                    {'wood': dict(wood, edge=9)},
                    {'wood': dict(wood, edge=True)},
                    {'wood': dict(wood, scrub='yes')},
                    {'wood': {k: v for k, v in wood.items() if k != 'scrub'}}):
            self.row('park')['ground_texture'] = bad
            with self.assertRaisesRegex(L.TableError, 'ground_texture'):
                L.validate_table(self.doc)

    def test_shipped_planting_rules_and_null_classes(self):
        self.assertEqual(L.planting_rule('residential', 'shrubs'), {'per_columns': 6})
        self.assertEqual(L.planting_rule('residential', 'flowers'),
                         {'share': 0.08, 'kinds': ['poppy', 'dandelion']})
        for cls in set(L.CLASSES) - {'residential'} | {'unlisted'}:
            for kind in ('shrubs', 'flowers', 'cover', 'pond'):
                self.assertIsNone(L.planting_rule(cls, kind), (cls, kind))
        self.assertEqual(L.planting_rule('residential', 'cover'),
                         {'share': 0.12, 'kinds': ['short_grass', 'fern']})
        self.assertIsNone(L.planting_rule('residential', 'pond'))

    def test_bad_planting_schema_refused(self):
        valid = copy.deepcopy(self.row('residential')['planting'])
        invalid = [False, [], {}, dict(valid, unknown=None), dict(valid, pond={}),
                   dict(valid, shrubs={}), dict(valid, shrubs={'per_columns': 6, 'extra': 1}),
                   dict(valid, flowers={}), dict(valid, flowers={'share': .1, 'kinds': ['poppy'], 'extra': 1}),
                   dict(valid, cover={}), dict(valid, cover={'share': .1, 'kinds': ['fern'], 'extra': 1})]
        invalid += [dict(valid, cover={'share': n, 'kinds': ['fern']}) for n in (0, -.1, .31, True, '0.1', None)]
        invalid += [dict(valid, cover={'share': .1, 'kinds': kinds})
                    for kinds in ([], ['fern', 'fern'], ['fern'] * 4, ['stone'], 'fern', [None])]
        invalid += [dict(valid, shrubs={'per_columns': n}) for n in (0, -1, True, 1.5, '6', None)]
        invalid += [dict(valid, flowers={'share': n, 'kinds': ['poppy']})
                    for n in (0, -.1, .21, True, '0.1', None, float('nan'))]
        invalid += [dict(valid, flowers={'share': .08, 'kinds': kinds})
                    for kinds in ([], ['poppy'] * 3, ['stone'], 'poppy', [None], [{}])]
        for planting in invalid:
            with self.subTest(planting=planting), self.assertRaises(L.TableError):
                self.row('residential')['planting'] = planting
                L.validate_table(self.doc)

    def test_no_inference_classes_refuse_planting(self):
        for cls in L.NO_INFERENCE:
            doc = copy.deepcopy(self.doc)
            next(r for r in doc['classes'] if r['id'] == cls)['planting'] = self.row('residential')['planting']
            with self.subTest(cls=cls), self.assertRaisesRegex(L.TableError, 'no planting inference'):
                L.validate_table(doc)

    def test_planting_null_rules_and_limits(self):
        for rule in ({'shrubs': None, 'flowers': None, 'cover': None, 'pond': None},
                     {'shrubs': {'per_columns': 1}, 'flowers': {'share': .2, 'kinds': ['fern']},
                      'cover': {'share': .3, 'kinds': ['short_grass', 'fern', 'tall_grass']}, 'pond': None}):
            self.row('residential')['planting'] = rule
            self.assertEqual(L.validate_table(self.doc)['classes']['residential']['planting'], rule)


class FurnitureRules(unittest.TestCase):
    """#260: the highways lamp map and the park bench section ship in the table; resolve reads them."""

    def setUp(self):
        self.doc = copy.deepcopy(json.loads(L.TABLE_PATH.read_text()))

    def test_shipped_highways_cover_the_inferred_classes_and_leave_the_rest_unlit(self):
        highways = L.load_table()['highways']
        for hw in ('residential', 'tertiary', 'secondary', 'primary', 'trunk', 'footway'):
            self.assertGreater(highways[hw], 0, hw)
        for hw in ('service', 'path', 'cycleway', 'steps', 'primary_link', 'track'):
            self.assertNotIn(hw, highways, hw)
        self.assertLess(highways['residential'], highways['tertiary'])

    def test_lamp_spacing_resolves_from_class_only_where_inference_is_allowed(self):
        r = L.resolve('residential', 'lamp_spacing', role='residential')
        self.assertEqual((r['value'], r['layer']), (30, 'class'))
        for cls in ('rural', 'natural', 'unknown'):
            r = L.resolve(cls, 'lamp_spacing', role='residential')
            self.assertEqual((r['value'], r['layer']), (None, None), cls)
        district = {'lamp_spacing': {'residential': 20}}
        r = L.resolve('residential', 'lamp_spacing', role='residential', district=district)
        self.assertEqual((r['value'], r['layer']), (20, 'district'))

    def test_only_park_ships_a_furniture_section(self):
        rows = L.load_table()['classes']
        self.assertEqual({cid for cid, row in rows.items() if row['furniture'] is not None}, {'park'})
        self.assertGreater(L.defaults('park')['furniture']['bench_per_100m'], 0)
        self.assertLessEqual(L.defaults('park')['furniture']['bench_per_100m'], 3)     # low density

    def test_bench_density_ladder(self):
        r = L.resolve('park', 'bench_per_100m')
        self.assertEqual((r['value'], r['layer']), (1.5, 'class'))
        r = L.resolve('park', 'bench_per_100m', district={'bench_per_100m': 4})
        self.assertEqual((r['value'], r['layer']), (4, 'district'))
        r = L.resolve('park', 'bench_per_100m', street={'bench_per_100m': 2}, district={'bench_per_100m': 4})
        self.assertEqual((r['value'], r['layer']), (2, 'street'))
        for cls in ('residential', 'commercial', 'civic', 'rural', 'unknown', 'banana'):
            self.assertEqual(L.resolve(cls, 'bench_per_100m')['value'], None, cls)

    def test_bad_furniture_or_highways_refused(self):
        row = next(r for r in self.doc['classes'] if r['id'] == 'park')
        for bad in ({}, {'bench_per_100m': 0}, {'bench_per_100m': 101}, {'bench_per_100m': True},
                    {'bench_per_100m': 1, 'extra': 1}, 3, []):
            with self.subTest(furniture=bad):
                row['furniture'] = bad
                with self.assertRaises(L.TableError):
                    L.validate_table(self.doc)
        row['furniture'] = {'bench_per_100m': 1.5}
        for cls in L.NO_INFERENCE:
            doc = copy.deepcopy(self.doc)
            next(r for r in doc['classes'] if r['id'] == cls)['furniture'] = {'bench_per_100m': 1}
            with self.subTest(cls=cls), self.assertRaises(L.TableError):
                L.validate_table(doc)
        for bad in (0, 1001, True, 'x'):
            doc = dict(self.doc, highways={'residential': bad})
            with self.subTest(highways=bad), self.assertRaises(L.TableError):
                L.validate_table(doc)


class ParcelMeta(unittest.TestCase):
    def build(self, areas, buildings):
        return C.ParcelBuilder('t', (0, 0, 0, 0), index(areas, buildings)).run()

    def test_parcels_carry_land_use_ids_and_digests_unchanged(self):
        # An umbrella polygon only (retail) is not a parcel, so ids and digests match a run with no classes.
        areas = [(sq(0, 0, 60, 60), {'landuse': 'retail'})]
        buildings = [(sq(10, 10, 16, 16), {'building': 'yes'})]
        got = self.build(areas, buildings)
        prop = next(p for p in got['parcels'] if p['kind'] == 'property')
        self.assertEqual(prop['meta']['land_use']['class'], 'commercial')
        base = self.build([], buildings)
        key = lambda rec: [(p['id'], p['kind'], p['n'], p['digest']) for p in rec['parcels'] if p['kind'] == 'property']
        self.assertEqual(key(got), key(base))

    def test_gap_and_open_parcels_get_a_class_roads_do_not(self):
        got = self.build([(sq(0, 0, 60, 60), {'landuse': 'retail'}), (sq(70, 70, 90, 90), {'leisure': 'park'})], [])
        kinds = {p['kind']: p['meta'].get('land_use') for p in got['parcels']}
        self.assertEqual(kinds['gap']['class'], 'commercial')
        self.assertEqual(kinds['open']['class'], 'park')


if __name__ == '__main__':
    unittest.main()


class TestResolve(unittest.TestCase):
    """One precedence function (#254): property > street > district > class; none speaks and stops."""

    FENCE = {'type': 'fence', 'height': 2}

    def row(self, cls='commercial', **frontage):
        row = copy.deepcopy(L.defaults(cls))
        row['frontage'].update(frontage)
        return row

    def with_front(self, cls='commercial'):
        row = self.row(cls)
        row['frontage']['boundary']['front'] = self.FENCE
        return row

    def test_layers_in_order_and_winner_is_named(self):
        row = self.with_front()
        prop, street, district = {'front': {'type': 'hedge'}}, {'front': {'type': 'brick_wall'}}, {'front': self.FENCE}
        r = L.resolve('commercial', 'boundary', role='front', property=prop, street=street, district=district, row=row)
        self.assertEqual((r['value']['type'], r['layer']), ('hedge', 'property'))
        r = L.resolve('commercial', 'boundary', role='front', street=street, district=district, row=row)
        self.assertEqual((r['value']['type'], r['layer']), ('brick_wall', 'street'))
        r = L.resolve('commercial', 'boundary', role='front', district={'front': {'type': 'hedge'}}, row=row)
        self.assertEqual((r['value']['type'], r['layer']), ('hedge', 'district'))
        r = L.resolve('commercial', 'boundary', role='front', row=row)
        self.assertEqual((r['value']['type'], r['layer']), ('fence', 'class'))

    def test_none_is_explicit_and_stops_the_search(self):
        row = self.with_front()
        for layer in ('property', 'street', 'district'):
            r = L.resolve('commercial', 'boundary', role='front', row=row, **{layer: {'front': {'type': 'none'}}})
            self.assertEqual((r['value'], r['layer'], r['open']), (None, layer, True))
        r = L.resolve('commercial', 'boundary', role='front', property={'front': None}, street={'front': self.FENCE}, row=row)
        self.assertTrue(r['open'])                                  # null and empty speak too
        self.assertEqual(r['layer'], 'property')

    def test_silent_layers_and_null_class_default_infer_nothing(self):
        r = L.resolve('residential', 'boundary', role='front', street={'side_back': self.FENCE})
        self.assertEqual((r['value'], r['layer'], r['open']), (None, None, False))

    def test_class_default_boundary_needs_inference_and_a_known_class(self):
        for cls in ('unknown', 'rural', 'natural', 'water'):
            row = self.with_front(cls)
            r = L.resolve(cls, 'boundary', role='front', row=row)
            self.assertEqual((r['value'], r['layer']), (None, None), cls)
        self.assertEqual(L.resolve('banana', 'boundary', role='front', row=self.with_front())['layer'], None)

    def test_scalar_fields_always_have_a_class_default(self):
        self.assertEqual(L.resolve('park', 'private_surface')['value'], 'keep')
        self.assertEqual(L.resolve('commercial', 'private_surface')['value'], 'paving')
        self.assertEqual(L.resolve('commercial', 'opening')['value'], 'street_gate')
        r = L.resolve('commercial', 'private_surface', street={'private_surface': 'grass'})
        self.assertEqual((r['value'], r['layer']), ('grass', 'street'))

    def test_pavement_width_is_a_class_value(self):
        self.assertEqual(L.pavement_width('residential'), 2.5)
        self.assertEqual(L.pavement_width(None), L.pavement_width('unknown'))
        self.assertEqual(L.pavement_width(row=self.row('residential', pavement=3.5)), 3.5)
        for bad in (0, -1, 9, 'x', True):
            doc = {'classes': [copy.deepcopy(r) for r in L.load_table()['classes'].values()]}
            doc['classes'][0]['frontage']['pavement'] = bad
            with self.assertRaises(L.TableError, msg=bad):
                L.validate_table(doc)
