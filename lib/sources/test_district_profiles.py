"""Synthetic district evidence only: validation, precedence and the garden/decor pipeline (#255)."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import decor_plan
import district_profiles as dp
import garden_sources as gs
import land_use


def profile():
    """Dark-hedge area, with two street-run defaults mirroring the #244 fixture structure."""
    return {'name': 'Synthetic district', 'cells': [[0, 0], {'min': [-2, -3], 'max': [-1, -1]}],
            'evidence_ids': ['synthetic-area-study'], 'classes': {'residential': {
                'frontage': {'boundary': {'front': {'type': 'hedge', 'density': 1}},
                             'materials': {'hedge': {'block': 'spruce_leaves[persistent=true]', 'height': 1}},
                             'runs': {'timber run': {'street': 'Panel Road',
                                                    'boundary': {'front': {'type': 'fence'}},
                                                    'materials': {'fence': {'block': 'oak_planks', 'height': 1}}},
                                      'pale block': {'street': 'Pale Road', 'numbers': ['2', '4'],
                                                     'boundary': {'front': {'type': 'fence'}},
                                                     'materials': {'fence': {'block': 'birch_planks', 'height': 2}}}}},
                'furniture': {'lamp_spacing': {'residential': 28, 'primary': 40, 'path': None}},
                'palette': ['bricks', 'stone'], 'roofs': ['gable'], 'storeys': {'min': 1, 'max': 3}}}}


class ProfileValidation(unittest.TestCase):
    def test_cells_pairs_and_inclusive_negative_rectangles(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / 'synthetic.json').write_text(json.dumps(profile()))
            for cell in ((0, 0), (-2, -3), (-1, -1), (-2, -2)):
                self.assertEqual(gs.load_district(cell, path)['name'], 'Synthetic district')
            self.assertIsNone(gs.load_district((1, 1), path))
            self.assertIsNone(gs.load_district((0, 0), path / 'missing'))

    def test_refuses_overlap_even_outside_the_requested_cell(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            for name, cells in (('a', [{'min': [2, -4], 'max': [5, -1]}]), ('b', [[5, -1]])):
                doc = profile()
                doc['cells'] = cells
                (path / f'{name}.json').write_text(json.dumps(doc))
            with self.assertRaisesRegex(dp.ProfileError, 'overlapping districts'):
                gs.load_district((0, 0), path)

    def test_malformed_profiles_and_reserved_sections_fail(self):
        bad_sections = [{'palette': []}, {'palette': ['not a block']}, {'roofs': ['triangle']},
                        {'storeys': {'min': 3, 'max': 1}}, {'storeys': {'min': True, 'max': 2}},
                        {'furniture': {'lamp_spacing': {'residential': 0}}},
                        {'furniture': {'lamp_spacing': {'made_up': 20}}},
                        {'frontage': {'boundary': {'front': {'type': 'hedge', 'density': 1.2}}}},
                        {'frontage': {'boundary': {'front': {'type': 'fence', 'height': False}}}},
                        {'frontage': {'boundary': {'front': {'type': 'hedge', 'provenance': {'source': 'observed'}}}}},
                        {'planting': {'shrubs': {'per_columns': 0}, 'flowers': None, 'pond': None}}]
        for section in bad_sections:
            doc = profile()
            doc['classes']['residential'] = section
            with self.subTest(section=section), self.assertRaises(dp.ProfileError):
                dp.validate_profile(doc)
        for cells in ([], [[True, 0]], [{'min': [2, 0], 'max': [1, 0]}], [{'min': [0, 0]}]):
            doc = profile()
            doc['cells'] = cells
            with self.subTest(cells=cells), self.assertRaises(dp.ProfileError):
                dp.validate_profile(doc)
        doc = profile()
        doc['evidence_ids'] = []
        with self.assertRaises(dp.ProfileError):
            dp.validate_profile(doc)

    def test_unknown_ids_and_no_inference_classes_cannot_infer(self):
        for cid in ('banana', *land_use.NO_INFERENCE):
            doc = profile()
            doc['classes'][cid] = {'frontage': {'boundary': {'front': {'type': 'fence'}}}}
            with self.subTest(cid=cid), self.assertRaises(dp.ProfileError):
                dp.validate_profile(doc)
        self.assertIsNone(dp.boundary_layer(profile(), 'unknown', 'Test Road'))
        self.assertIsNone(dp.boundary_layer(profile(), 'banana', 'Test Road'))

    def test_valid_reserved_sections_are_stored_and_have_no_effect(self):
        doc = profile()
        self.assertIs(dp.validate_profile(doc), doc)
        a = dp.boundary_layer(doc, 'residential', 'Test Road', '7')
        doc['classes']['residential'].update(palette=['gold_block'], roofs=['flat'], storeys={'min': 9, 'max': 10})
        self.assertEqual(dp.boundary_layer(dp.validate_profile(doc), 'residential', 'Test Road', '7'), a)


class ProfileResolution(unittest.TestCase):
    def layer(self, street='Test Road', number='7', doc=None):
        return dp.boundary_layer(doc or profile(), 'residential', street, number)

    def test_missing_section_and_absent_class_fall_to_class_default(self):
        doc = profile()
        del doc['classes']['residential']['frontage']
        self.assertIsNone(self.layer(doc=doc))
        self.assertIsNone(gs.role_spec(None, '7', 'front', district=self.layer(doc=doc)))
        row = copy.deepcopy(land_use.defaults('commercial'))
        row['frontage']['boundary']['front'] = {'type': 'iron_bars', 'height': 1}
        district = dp.boundary_layer(doc, 'commercial', 'Test Road')
        result = gs.role_resolution(None, '7', 'front', 'commercial', row, district)
        self.assertEqual((result['layer'], result['value']['type']), ('class', 'iron_bars'))

    def test_property_street_district_precedence_and_silent_roles(self):
        district = self.layer()
        self.assertEqual(gs.role_spec(None, '7', 'front', district=district)['block'], 'spruce_leaves[persistent=true]')
        conv = {'rear': {'type': 'fence'}, 'front': {'type': 'brick_wall', 'height': 2},
                'overrides': {'7': {'front': {'type': 'fence', 'block': 'birch_planks', 'height': 2}}}}
        self.assertEqual(gs.role_spec(conv, '7', 'front', district=district)['block'], 'birch_planks')
        self.assertEqual(gs.role_resolution(conv, '8', 'front', district=district)['layer'], 'street')
        self.assertEqual(gs.role_spec(conv, '8', 'front', district=district)['type'], 'brick_wall')
        self.assertEqual(gs.role_resolution({'rear': conv['rear']}, '8', 'front', district=district)['layer'], 'district')
        self.assertIsNone(gs.role_spec(None, '7', 'rear', district=district))
        self.assertIsNone(gs.role_spec({'front': {'type': 'none'}}, '7', 'front', district=district))

    def test_material_fills_same_kind_only_without_changing_exact_overrides(self):
        district = self.layer()
        conv = {'front': {'type': 'hedge'}, 'overrides': {'7': {
            'front': {'type': 'hedge', 'block': 'birch_leaves[persistent=true]', 'height': 2}}}}
        self.assertEqual(gs.role_spec(conv, '8', 'front', district=district)['block'], 'spruce_leaves[persistent=true]')
        self.assertEqual(gs.role_spec(conv, '7', 'front', district=district)['block'], 'birch_leaves[persistent=true]')

    def test_materials_are_selected_per_street_run_or_numbered_frontage_block(self):
        timber = gs.role_spec(None, '7', 'front', district=self.layer('Panel Road'))
        pale = gs.role_spec(None, '2', 'front', district=self.layer('Pale Road', '2'))
        hedge = gs.role_spec(None, '6', 'front', district=self.layer('Pale Road', '6'))
        self.assertEqual((timber['block'], timber['height']), ('oak_planks', 1))
        self.assertEqual((pale['block'], pale['height']), ('birch_planks', 2))
        self.assertEqual(hedge['type'], 'hedge')
        doc = profile()
        runs = doc['classes']['residential']['frontage']['runs']
        runs['duplicate'] = copy.deepcopy(runs['timber run'])
        with self.assertRaisesRegex(dp.ProfileError, 'overlapping street runs'):
            self.layer('Panel Road', doc=doc)

    def test_density_is_stable_per_whole_frontage_and_ignores_higher_layers(self):
        district = self.layer()
        district['front']['density'] = .5
        values = [bool(gs.role_spec(None, str(n), 'front', district=district)) for n in range(100)]
        self.assertGreater(sum(values), 30)
        self.assertLess(sum(values), 70)
        self.assertEqual(values, [bool(gs.role_spec(None, str(n), 'front', district=district)) for n in range(100)])
        district['front']['density'] = 0
        self.assertIsNone(gs.role_spec(None, '7', 'front', district=district))
        self.assertFalse(gs.role_resolution(None, '7', 'front', district=district)['open'])
        self.assertIsNotNone(gs.role_spec({'front': {'type': 'fence'}}, '7', 'front', district=district))

    def test_lamp_spacing_uses_the_same_ladder_and_null_stops_inference(self):
        doc = profile()
        self.assertEqual(gs.lamp_spacing((0, 0), 'residential', 'primary', profile=doc)['value'], 40)
        self.assertIsNone(gs.lamp_spacing((0, 0), 'residential', 'path', profile=doc)['value'])
        self.assertIsNone(gs.lamp_spacing((0, 0), 'unknown', 'residential', profile=doc)['value'])
        conv = {'furniture': {'lamp_spacing': {'residential': 35}}, 'overrides': {
            '7': {'furniture': {'lamp_spacing': {'residential': 12}}}}}
        result = gs.lamp_spacing((0, 0), 'residential', 'residential', conv, '7', profile=doc)
        self.assertEqual((result['value'], result['layer']), (12, 'property'))
        self.assertEqual(gs.lamp_spacing((0, 0), 'residential', 'residential', conv, '8', profile=doc)['value'], 35)
        result = gs.lamp_spacing((0, 0), 'residential', 'residential', profile=doc)
        self.assertEqual((result['value'], result['layer'], result['provenance']['source']), (28, 'district', 'inference'))
        self.assertEqual(result['provenance']['evidence_ids'], ['synthetic-area-study'])
        table = {**land_use.load_table(), 'highways': {'secondary': 50}}
        with mock.patch.object(land_use, 'load_table', return_value=table):
            self.assertEqual(gs.lamp_spacing((0, 0), 'residential', 'secondary', profile=doc)['value'], 50)
            self.assertIsNone(gs.lamp_spacing((0, 0), 'unknown', 'secondary', profile=doc)['value'])


class ProfilePipeline(unittest.TestCase):
    def garden(self, *, conv=None, doc=None, share=0, cls='residential', identified=True):
        house = {(x, z) for x in range(2, 8) for z in range(8, 13)}
        plot = {(x, z) for x in range(10) for z in range(3, 16)} - house
        front = [(x, 3) for x in range(10)]
        meta = {'street': 'Test Road', 'number': '7' if identified else '', 'centre': (4.5, 11.5),
                'foot': (4, 0), 'map_ref': None, 'land_use': cls}
        idx = mock.Mock()
        idx.cells.return_value = {q: 1 for q in house}
        report = {}
        with mock.patch.object(gs, 'plots_for', return_value=({q: 1 for q in plot}, {1: meta}, {}, idx)), \
                mock.patch.object(gs, 'segments', return_value={(1, 'front'): front}), \
                mock.patch.object(gs, 'load_convention', return_value=conv), \
                mock.patch.object(gs, 'load_district', return_value=doc if doc is not None else profile()), \
                mock.patch.object(gs, 'hedge_share', return_value=share), \
                mock.patch.object(gs, 'tree_canopy_columns', return_value=set()), \
                mock.patch.object(gs, 'landed_approaches', return_value=[]):
            items = gs.garden_barriers((0, 0), rgb={}, report=report)
        return items, report

    def test_dark_hedge_and_exact_light_fence_keep_material_and_provenance(self):
        items, report = self.garden()
        self.assertEqual(items[0]['block'], 'spruce_leaves[persistent=true]')
        self.assertEqual(items[0]['scope']['layer'], 'district')
        self.assertEqual(items[0]['provenance']['source'], 'inference')
        self.assertEqual(items[0]['provenance']['evidence_ids'], ['synthetic-area-study'])
        self.assertEqual(report['frontages'][0]['scope']['layer'], 'district')
        self.assertTrue(set(map(tuple, items[0]['cells'])).isdisjoint(gs.reserved_openings(report['frontages'])))
        override = {'overrides': {'7': {'front': {'type': 'fence', 'block': 'birch_planks', 'height': 2,
                    'provenance': {'source': 'photo_read', 'confidence': 'high', 'evidence_ids': ['synthetic-house']}}}}}
        items, _ = self.garden(conv=override)
        self.assertEqual((items[0]['kind'], items[0]['block'], items[0]['height']), ('plank_fence', 'birch_planks', 2))
        self.assertEqual(items[0]['provenance']['evidence_ids'], ['synthetic-house'])

    def test_observed_map_and_aerial_win_over_district(self):
        items, _ = self.garden()
        cols = set(map(tuple, items[0]['cells']))
        plan = decor_plan.build_decor_plan((0, 0), map_barriers={'wall': cols}, garden=items,
                                           rgb={}, canopy=set(), footprint=set())
        self.assertEqual({i['kind'] for i in plan['items']}, {'brick_wall'})
        self.assertEqual(plan['items'][0]['provenance']['source'], 'vector_map')
        items, _ = self.garden(share=1)
        self.assertEqual((items[0]['scope']['layer'], items[0]['provenance']['source']), ('aerial', 'aerial'))
        for spec in ({'type': 'none'}, {'type': 'hedge', 'density': 0}):
            doc = profile()
            doc['classes']['residential']['frontage']['boundary']['front'] = spec
            items, report = self.garden(doc=doc, share=1)
            self.assertEqual(items[0]['provenance']['source'], 'aerial')
            self.assertFalse(report['frontages'][0]['open_frontage'])

    def test_observed_property_presence_labels_district_material_separately(self):
        conv = {'overrides': {'7': {'front': {'type': 'hedge', 'provenance': {
            'source': 'photo_read', 'confidence': 'high', 'evidence_ids': ['synthetic-house']}}}}}
        items, _ = self.garden(conv=conv)
        item = items[0]
        self.assertEqual(item['provenance']['source'], 'photo_read')
        self.assertEqual(item['scope']['material_layers'], {'block': 'district', 'height': 'district'})
        self.assertEqual(item['scope']['material_provenance']['source'], 'inference')
        self.assertEqual(item['scope']['material_provenance']['evidence_ids'], ['synthetic-area-study'])

    def test_decor_observations_precede_area_inference_even_without_garden_aerial_promotion(self):
        cells = [[3, z] for z in range(4, 9)]
        garden = [{'id': 'area', 'role': 'barrier', 'kind': 'hedge', 'cells': cells,
                   'block': 'spruce_leaves[persistent=true]', 'scope': {'layer': 'district'},
                   'provenance': {'source': 'inference', 'confidence': 'low', 'evidence_ids': []}}]
        with mock.patch('ground_sources.aerial_class', return_value='shade'):
            plan = decor_plan.build_decor_plan((0, 0), map_barriers={}, garden=garden,
                        rgb={tuple(c): (0.1, 0.4, 0.1) for c in cells}, canopy=set(),
                        footprint={(x, z) for x in range(4, 7) for z in range(4, 9)})
        self.assertEqual(len(plan['items']), 1)
        self.assertEqual(plan['items'][0]['provenance']['source'], 'aerial')
        for evidence in ({'designer_items': [{'kind': 'brick_wall', 'cells': cells}]},
                         {'photo_observations': [{'kind': 'brick_wall', 'cells': cells}]},
                         {'map_furniture': [(3, 4, 'bench')]}):
            plan = decor_plan.build_decor_plan((0, 0), map_barriers={}, garden=garden,
                        rgb={}, canopy=set(), footprint=set(), **evidence)
            area = [i for i in plan['items'] if i.get('id') == 'area']
            if 'map_furniture' in evidence:
                self.assertNotIn([3, 4], area[0]['cells'])
            else:
                self.assertEqual(area, [])

    def test_profile_inference_needs_class_permission_and_identified_frontage(self):
        for cls, identified in (('unknown', True), ('residential', False), ('natural', True)):
            items, _ = self.garden(cls=cls, identified=identified)
            self.assertEqual(items, [])


if __name__ == '__main__':
    unittest.main()
