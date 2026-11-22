"""Compatibility contracts for shared IDs, states and domain-specific air filters."""
import importlib
import unittest

from block_store import block_id, format_block_scan_text, same_state, state_prop, state_props
from site_kit import AIR_IDS, _block_arg, bid, colour


class BlockIdContracts(unittest.TestCase):
    def test_namespace_and_saved_read_defaults_remain_distinct(self):
        for value, namespaced, read in (
            ('stone[axis=y]', 'minecraft:stone', 'stone'),
            ('minecraft:stone[axis=y]', 'minecraft:stone', 'minecraft:stone'),
            ('mod:stone[axis=y]', 'mod:stone', 'mod:stone'),
            ('', 'minecraft:', 'minecraft:air'),
        ):
            with self.subTest(value=value):
                self.assertEqual(block_id(value), namespaced)
                self.assertEqual(bid(value), namespaced)
                self.assertEqual(block_id(value, policy='read'), read)
        self.assertEqual(block_id(None, policy='read'), 'minecraft:air')
        with self.assertRaises(AttributeError):
            bid(None)
        with self.assertRaises(AttributeError):
            block_id(None)


class StateContracts(unittest.TestCase):
    def test_strict_roof_properties_keep_first_equals_and_last_duplicate(self):
        self.assertEqual(state_props('stone[a=x=y,a=z,b=]'), {'a': 'z', 'b': ''})
        self.assertEqual(state_props('stone'), {})
        self.assertEqual(state_props('stone[facing=north'), {'facing': 'nort'})
        for value in ('stone[]', 'stone[bad]', 'stone[x=y,]'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                state_props(value)

    def test_readback_still_rejects_extra_equals_and_malformed_pairs(self):
        with self.assertRaises(ValueError):
            same_state('stone[x=y=z]', 'minecraft:stone[x=y=z]')
        with self.assertRaises(ValueError):
            same_state('stone[x=y]', 'minecraft:stone[bad]')
        self.assertTrue(same_state('stone[x=y]', 'minecraft:stone[x=z,x=y]'))
        self.assertFalse(same_state('stone[x=y]', 'stone[x=y]'))

    def test_scan_text_keeps_duplicate_rows_and_missing_values(self):
        text = format_block_scan_text({(1, 2, 3): 'stone[k=z,k=a,lone]'})
        self.assertEqual(text.count('\n        k:'), 2)
        self.assertLess(text.index('k: a'), text.index('k: z'))
        self.assertIn('\n        lone: ', text)
        self.assertEqual(state_props('stone[x=1,,lone]]', syntax='scan'), {'x': '1', 'lone': ''})

    def test_block_argument_trims_and_requires_a_closing_bracket(self):
        self.assertEqual(_block_arg('stone[ x = y ,lone,]'), {
            'id': 'minecraft:stone', 'properties': {'x': 'y', 'lone': '', '': ''},
        })
        with self.assertRaises(ValueError):
            _block_arg('stone[x=y')

    def test_legacy_single_property_lookup_keeps_regex_contract(self):
        for value in ('stone[facing=north,facing=south]', 'facing=north-west', 'other_facing=north'):
            with self.subTest(value=value):
                self.assertEqual(state_prop(value, 'facing'), 'north')
        self.assertIsNone(state_prop('stone[facing=]', 'facing'))
        self.assertEqual(state_props('stairs[bad,facing=north]', syntax='filtered'), {'facing': 'north'})


class AirFilterContracts(unittest.TestCase):
    """Snapshots of former collections, including observable tuple order and types.

    Some filters intentionally omitted void_air (or cave_air). Those require base
    minus named exclusions plus named extras; union alone cannot preserve them.
    """
    def test_former_collections_equal_base_with_their_named_exceptions(self):
        snapshots = (
            ('cell_journal', 'AIR_IDS', frozenset, ('air', 'cave_air', 'void_air'), True, None, None),
            ('layer_common', 'AIRS', frozenset, ('air', 'cave_air', 'void_air'), True, None, None),
            ('road_labels', '_AIR', frozenset, ('air', 'cave_air', 'void_air'), False, None, None),
            ('cell_marker', 'AIRS', frozenset, ('air', 'cave_air', 'void_air'), True, None, None),
            ('door_audit', '_EMPTY', tuple, ('air', 'cave_air', 'void_air'), False, None, None),
            ('lidar_heightmap', '_AIRS', tuple, ('air', 'cave_air', 'void_air', 'light'), True, '_AIR_EXTRAS', None),
            ('osm_footprints', 'AIRS', tuple, ('air', 'cave_air', 'light', 'void_air', 'water'), True, '_AIR_EXTRAS', None),
            ('floaters', '_AIR', frozenset, ('air', 'cave_air', 'void_air', 'light'), True, '_AIR_EXTRAS', None),
            ('roof_lint', '_SKIP', tuple, ('air', 'light', 'cave_air'), True, '_AIR_EXTRAS', '_AIR_EXCLUSIONS'),
            ('feature_facade', '_OUTSIDE_AIRS', tuple, ('air', 'cave_air'), True, None, '_OUTSIDE_AIR_EXCLUSIONS'),
            ('feature_facade', '_AIRS', tuple, ('air', 'cave_air', 'void_air'), True, None, None),
            ('cell_journal', 'CANCEL', frozenset, ('air', 'light'), True, '_CANCEL_EXTRAS', '_CANCEL_AIR_EXCLUSIONS'),
        )
        self.assertEqual(AIR_IDS, ('air', 'cave_air', 'void_air'))
        for module_name, name, kind, old, namespace, extras_name, excluded_name in snapshots:
            with self.subTest(module=module_name, name=name):
                module = importlib.import_module(module_name)
                actual = getattr(module, name)
                expected = kind('minecraft:' + v if namespace else v for v in old)
                self.assertIs(type(actual), kind)
                self.assertEqual(actual, expected)
                base = set(AIR_IDS) - set(getattr(module, excluded_name) if excluded_name else ())
                if namespace:
                    base = {'minecraft:' + v for v in base}
                extras = set(getattr(module, extras_name)) if extras_name else set()
                self.assertEqual(set(actual), base | extras)

    def test_structural_and_substring_policies_keep_their_separate_extras(self):
        import feature_details as details
        import geo
        import site_walk
        import window_openings as windows
        self.assertEqual(windows._NON_STRUCTURAL, set(AIR_IDS) | windows._NON_STRUCTURAL_EXTRAS)
        self.assertEqual(windows._NON_STRUCTURAL, {
            'air', 'cave_air', 'void_air', 'water', 'lava', 'iron_bars',
            'snow', 'grass', 'short_grass', 'tall_grass', 'vine',
        })
        self.assertEqual((*AIR_IDS, *details._OPENING_EXTRAS),
                         ('air', 'cave_air', 'void_air', 'water', 'lava', 'iron_bars'))
        self.assertEqual(geo.SKIP_LIDAR_TOP,
                         ('air', 'leaves', '_log', 'vine', 'short_grass', 'tall_grass', 'fern', 'flower'))
        self.assertTrue(site_walk.solid('minecraft:void_air'))
        self.assertFalse(site_walk.solid('minecraft:cave_air'))
        self.assertTrue(windows.structural('minecraft:barrier'))
        self.assertFalse(windows.structural('minecraft:water'))
        for name in (*AIR_IDS, 'light'):
            self.assertIsNone(colour(name))
        self.assertIsNotNone(colour('barrier'))
