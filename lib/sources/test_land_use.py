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
