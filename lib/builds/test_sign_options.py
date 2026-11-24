"""Sign wood, fascia override and board-contrast text colour (#437)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import feature_spec as FS
import sign_options as SO


class SignCatalogueTest(unittest.TestCase):
    """Seam: catalogue woods and dyes match the block list."""

    def setUp(self):
        self.catalogue = SO.load_catalogue()
        self.ids = {
            line.removeprefix('minecraft:')
            for line in SO.BLOCK_IDS_PATH.read_text(encoding='utf-8').splitlines()
            if line.startswith('minecraft:')
        }

    def test_every_wood_has_wall_and_hanging_sign_families(self):
        for wood in self.catalogue['woods']:
            blocks = SO.sign_blocks(wood)
            for name in blocks.values():
                with self.subTest(wood=wood, block=name):
                    self.assertIn(name, self.ids)

    def test_every_fascia_colour_has_a_concrete_block(self):
        for colour in self.catalogue['fascia_colours']:
            with self.subTest(colour=colour):
                self.assertIn(f'{colour}_concrete', self.ids)

    def test_default_wood_is_oak(self):
        self.assertEqual(SO.default_wood(), 'oak')
        self.assertEqual(SO.sign_blocks('oak')['wall_sign'], 'oak_wall_sign')


class SignContrastTest(unittest.TestCase):
    """Seam: text colour is judged against the board map colour."""

    def test_gray_on_pale_oak_is_refused(self):
        """Tidal vape case: grey lettering on a pale oak board."""
        self.assertFalse(SO.contrast_ok('oak', 'gray'))
        with self.assertRaises(ValueError) as raised:
            SO.resolve_text_colour('oak', 'gray')
        self.assertIn('low contrast', str(raised.exception))
        self.assertIn('oak', str(raised.exception))

    def test_auto_text_on_oak_is_black_or_white_and_passes(self):
        dye = SO.resolve_text_colour('oak', None)
        self.assertIn(dye, ('black', 'white'))
        self.assertTrue(SO.contrast_ok('oak', dye))

    def test_explicit_black_on_dark_oak_is_refused_or_white_wins_auto(self):
        auto = SO.resolve_text_colour('dark_oak', None)
        self.assertEqual(auto, 'white')
        self.assertTrue(SO.contrast_ok('dark_oak', 'white'))

    def test_fascia_override_keeps_hash_when_absent(self):
        hashed = SO.resolve_fascia_colour('Cyan Cafe', None, hash_colour=lambda _k: 'cyan')
        self.assertEqual(hashed, 'cyan')
        self.assertEqual(
            SO.resolve_fascia_colour('Cyan Cafe', 'black', hash_colour=lambda _k: 'cyan'),
            'black',
        )

    def test_unknown_wood_and_fascia_are_refused(self):
        with self.assertRaises(ValueError):
            SO.sign_blocks('teak')
        with self.assertRaises(ValueError):
            SO.resolve_fascia_colour('A', 'teal')


class SignSpecValidationTest(unittest.TestCase):
    """Seam: feature specs validate signs keys and emit refusals."""

    def _write(self, signs=None, details=None):
        spec = {
            'name': 'sign_options_fixture',
            'ways': {'1': 'A'},
            'storeys': {'A': 2},
            'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
            'groups': [{'nos': ['A'], 'front': 'N', 'mode': 'each'}],
        }
        if signs is not None:
            spec['signs'] = signs
        if details is not None:
            spec['details'] = details
        path = Path(self._dir.name) / 'spec.json'
        path.write_text(__import__('json').dumps(spec), encoding='utf-8')
        return path

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._dir.cleanup()

    def test_valid_signs_row_loads(self):
        path = self._write(signs={'A': {
            'wood': 'dark_oak', 'fascia_colour': 'black', 'text_colour': 'white',
        }})
        loaded = FS.load_spec(path)
        self.assertEqual(loaded['signs']['A']['wood'], 'dark_oak')
        self.assertEqual(loaded['signs']['A']['fascia_colour'], 'black')

    def test_tidal_vape_gray_on_oak_is_refused_at_load(self):
        path = self._write(signs={'A': {'wood': 'oak', 'text_colour': 'gray'}})
        with self.assertRaises(SystemExit) as raised:
            FS.load_spec(path)
        self.assertIn('low contrast', str(raised.exception))

    def test_shop_door_key_loads_and_refuses_a_non_door_or_unknown_block(self):
        path = self._write(signs={'A': {'door': 'oxidized_copper_door'}})
        self.assertEqual(FS.load_spec(path)['signs']['A']['door'], 'oxidized_copper_door')
        for bad in ('oak_trapdoor', 'oak_plank', 'teak_door', 7):
            with self.subTest(door=bad), self.assertRaises(SystemExit) as raised:
                FS.load_spec(self._write(signs={'A': {'door': bad}}))
            self.assertIn('door', str(raised.exception))

    def test_label_sign_carries_the_door_or_none(self):
        self.assertIsNone(SO.resolve_label_sign({}, 'A', hash_key='A', hash_colour=lambda _k: 'cyan')['door'])
        spec = {'signs': {'A': {'door': 'warped_door'}}}
        self.assertEqual(
            SO.resolve_label_sign(spec, 'A', hash_key='A', hash_colour=lambda _k: 'cyan')['door'], 'warped_door')

    def test_detail_sign_wood_is_validated(self):
        path = self._write(details={'A': [{
            'kind': 'sign', 'at': [0, 50, 0], 'size': [1, 1, 1],
            'source': 'street photo', 'face': 'N', 'wood': 'teak',
        }]})
        with self.assertRaises(SystemExit) as raised:
            FS.load_spec(path)
        self.assertIn('teak', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
