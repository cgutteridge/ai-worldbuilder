"""Roof-material catalogue and the flat(ish) / pitched rule (#355)."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import roof_material as RM


class RoofClassTest(unittest.TestCase):
    """Seam: rise/run is the only slope rule for material choice."""

    def test_ratios_from_the_ticket(self):
        cases = (
            (0, 1, 'flatish'),
            (0.4, 1, 'flatish'),
            (0.5, 1, 'flatish'),
            (1, 2, 'flatish'),
            (0.51, 1, 'pitched'),
            (1.0, 1, 'pitched'),
            (0, 0, 'flatish'),
            (1, 0, 'pitched'),
        )
        for rise, run, expected in cases:
            with self.subTest(rise=rise, run=run):
                self.assertEqual(RM.roof_class(rise, run), expected)

    def test_feature_kind_maps_onto_the_same_classes(self):
        self.assertEqual(RM.roof_class_for_kind('flat'), 'flatish')
        self.assertEqual(RM.roof_class_for_kind('pitched'), 'pitched')
        with self.assertRaises(ValueError):
            RM.roof_class_for_kind('dome')


class RoofCatalogueTest(unittest.TestCase):
    """Seam: catalogue names, colours, and the flat(ish) / pitched gate."""

    def setUp(self):
        self.catalogue = RM.load_catalogue()
        self.ids = RM.block_id_names()

    def test_every_catalogue_name_is_in_the_block_list(self):
        for family_id, family in self.catalogue['families'].items():
            for variant in ('full', 'slab', 'stairs'):
                name = family.get(variant)
                if not name:
                    continue
                with self.subTest(family=family_id, variant=variant):
                    self.assertIn(name, self.ids)

    def test_colours_follow_the_preview_table_when_a_key_matches(self):
        for family_id, family in self.catalogue['families'].items():
            seeded = RM.preview_colour(family['full'])
            if seeded is None:
                continue
            with self.subTest(family=family_id):
                self.assertEqual(tuple(family['colour']), seeded)

    def test_brick_keeps_the_full_name_bricks(self):
        variants = RM.family_variants('brick')
        self.assertEqual(variants['full'], 'bricks')
        self.assertEqual(variants['slab'], 'brick_slab')
        self.assertEqual(variants['stairs'], 'brick_stairs')
        self.assertEqual(
            RM.family_variants('minecraft:brick_slab[type=bottom]')['full'],
            'bricks',
        )

    def test_flatish_smooth_stone_passes_and_pitched_does_not(self):
        flatish = RM.roof_blocks('smooth_stone', 'flatish')
        self.assertTrue(flatish['ok'])
        self.assertEqual(flatish['messages'], [])
        self.assertEqual(flatish['slab'], 'smooth_stone_slab')
        self.assertIsNone(flatish['stairs'])
        pitched = RM.roof_blocks('smooth_stone', 'pitched')
        self.assertFalse(pitched['ok'])
        self.assertTrue(pitched['suggestions'])
        self.assertIn('polished_andesite', pitched['suggestions'])
        message = pitched['messages'][0]
        for suggestion in pitched['suggestions']:
            self.assertIn(suggestion, message)
            self.assertTrue(self.catalogue['families'][suggestion]['triad'])

    def test_pitched_terracotta_is_rejected_with_brick_first(self):
        pitched = RM.roof_blocks('terracotta', 'pitched')
        self.assertFalse(pitched['ok'])
        self.assertEqual(pitched['suggestions'][0], 'brick')
        self.assertTrue(RM.roof_blocks('terracotta', 'flatish')['ok'])
        self.assertIsNone(RM.roof_blocks('terracotta', 'flatish')['slab'])

    def test_concrete_colours_are_flatish_only(self):
        self.assertNotIn('concrete', self.catalogue['families'])
        for name in ('black_concrete', 'gray_concrete', 'light_gray_concrete', 'white_concrete'):
            with self.subTest(name=name):
                self.assertTrue(RM.roof_blocks(name, 'flatish')['ok'])
                self.assertFalse(RM.roof_blocks(name, 'pitched')['ok'])
                self.assertIsNone(self.catalogue['families'][name].get('slab'))
                self.assertIsNone(self.catalogue['families'][name].get('stairs'))

    def test_names_outside_the_catalogue_keep_the_historical_prefix(self):
        for name in ('granite', 'stone_wall_only'):
            with self.subTest(name=name):
                self.assertNotIn(name, self.catalogue['families'])
                self.assertEqual(RM.family_variants(name), {
                    'full': name,
                    'slab': f'{name}_slab',
                    'stairs': f'{name}_stairs',
                })
                self.assertEqual(RM.validate_roof_material(name, 'pitched'), [])

    def test_warnings_list_a_pitched_smooth_stone_and_do_not_rewrite(self):
        roof = {
            'kind': 'pitched',
            'material': 'smooth_stone',
            'zone_materials': {'59': 'terracotta'},
        }
        before = dict(roof)
        messages = RM.roof_material_warnings(roof)
        self.assertEqual(roof, before)
        self.assertEqual(len(messages), 2)
        self.assertTrue(any('smooth_stone' in message for message in messages))
        self.assertTrue(any('terracotta' in message for message in messages))
        self.assertEqual(RM.roof_material_warnings({
            'kind': 'flat',
            'material': 'smooth_stone',
        }), [])
        self.assertEqual(RM.roof_material_warnings({
            'kind': 'pitched',
            'material': 'brick',
        }), [])


class RoofTypeTest(unittest.TestCase):
    """Seam: a named roof type resolves to catalogue families and blocks (#357)."""

    NAMES = (
        'red_clay_tile', 'grey_slate', 'grey_tile',
        'brown_tile', 'weathered_copper', 'flat_deck',
    )

    def setUp(self):
        self.catalogue = RM.load_catalogue()
        self.ids = RM.block_id_names()

    def test_the_ticket_types_are_all_named(self):
        self.assertEqual(set(RM.roof_type_names()), set(self.NAMES))

    def test_every_type_resolves_to_catalogue_families(self):
        for name in RM.roof_type_names():
            row = self.catalogue['types'][name]
            for family_id in [row['family'], *row['alternates']]:
                with self.subTest(type=name, family=family_id):
                    self.assertIn(family_id, self.catalogue['families'])
            self.assertTrue(1 <= len(row['alternates']) <= 3)

    def test_every_pitched_type_family_and_alternate_is_a_triad(self):
        for name in RM.roof_type_names():
            row = self.catalogue['types'][name]
            if row['slope'] != 'pitched':
                continue
            for family_id in [row['family'], *row['alternates']]:
                with self.subTest(type=name, family=family_id):
                    self.assertTrue(self.catalogue['families'][family_id]['triad'])

    def test_lookup_returns_the_triad_for_a_pitched_type(self):
        got = RM.roof_type_blocks('red_clay_tile')
        self.assertEqual(got['slope'], 'pitched')
        self.assertEqual(got['family'], 'brick')
        self.assertEqual(
            (got['full'], got['slab'], got['stairs']),
            ('bricks', 'brick_slab', 'brick_stairs'),
        )
        for name in RM.roof_type_names():
            with self.subTest(type=name):
                blocks = RM.roof_type_blocks(name)
                for entry in [blocks, *blocks['alternates']]:
                    self.assertIn(entry['full'], self.ids)
                    if blocks['slope'] == 'pitched':
                        self.assertIn(entry['slab'], self.ids)
                        self.assertIn(entry['stairs'], self.ids)

    def test_flat_deck_is_flatish_and_needs_no_stairs(self):
        got = RM.roof_type_blocks('flat_deck')
        self.assertEqual(got['slope'], 'flatish')
        self.assertEqual(got['full'], 'smooth_stone')
        self.assertIsNone(got['stairs'])
        self.assertTrue(got['alternates'])

    def test_unknown_type_names_the_known_ones(self):
        with self.assertRaises(ValueError) as caught:
            RM.roof_type_blocks('thatch')
        self.assertIn('red_clay_tile', str(caught.exception))

    def test_catalogue_refuses_a_pitched_type_with_a_non_triad_family(self):
        bad = copy.deepcopy(self.catalogue)
        bad['types']['grey_slate']['family'] = 'smooth_stone'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'roof-materials.json'
            path.write_text(json.dumps(bad), encoding='utf-8')
            with self.assertRaises(ValueError) as caught:
                RM.load_catalogue(path)
        self.assertIn('not a triad', str(caught.exception))

    def test_catalogue_refuses_a_type_with_an_unknown_family(self):
        bad = copy.deepcopy(self.catalogue)
        bad['types']['grey_tile']['alternates'] = ['no_such_family']
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'roof-materials.json'
            path.write_text(json.dumps(bad), encoding='utf-8')
            with self.assertRaises(ValueError):
                RM.load_catalogue(path)
