#!/usr/bin/env python3
"""Tests for blank-world creation (the one permitted write under saves/)."""
from __future__ import annotations

import gzip
import struct
import tempfile
import unittest
from pathlib import Path

import nbt_read as N

import blank_world as BW


class BlankWorldTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.saves = Path(self.tmp.name) / 'saves'
        self.saves.mkdir()

    def test_creates_from_template(self):
        out = BW.create_blank_world(self.saves / 'New')
        self.assertTrue((out / 'level.dat').is_file())
        self.assertFalse((out / 'session.lock').exists())

    def test_level_name_defaults_to_folder_name(self):
        out = BW.create_blank_world(self.saves / 'ventnor-build')
        self.assertEqual(self._level_name(out), 'ventnor-build')
        self.assertNotIn(b'SouthamptonAI', gzip.decompress((out / 'level.dat').read_bytes()))

    def test_level_name_can_be_given_and_is_unicode_safe(self):
        out = BW.create_blank_world(self.saves / 'v', name='Ventnor \u2013 seafront')
        self.assertEqual(self._level_name(out), 'Ventnor \u2013 seafront')

    def _level_name(self, save):
        root = N.read_named_root(gzip.decompress((save / 'level.dat').read_bytes()))
        return root.value['Data'].value['LevelName'].value

    def test_non_bmp_and_nul_use_java_modified_utf8(self):
        self.assertEqual(BW._modified_utf8('a\x00b'), b'a\xc0\x80b')
        self.assertEqual(BW._modified_utf8('\U0001F600'), b'\xed\xa0\xbd\xed\xb8\x80')   # surrogate pair, 3 bytes each
        self.assertEqual(BW._modified_utf8('\ud800'), b'\xed\xa0\x80')                    # lone surrogate
        self.assertEqual(BW._modified_utf8('Ventnor \u2013'), 'Ventnor \u2013'.encode('utf-8'))

    def test_non_bmp_name_is_written_as_modified_utf8(self):
        # nbt_read decodes strict UTF-8, so check the raw bytes (Java reads the 3-byte surrogate form)
        out = BW.create_blank_world(self.saves / 'x', name='V \U0001F600')
        raw = gzip.decompress((out / 'level.dat').read_bytes())
        self.assertIn(b'\x00\x08V \xed\xa0\xbd\xed\xb8\x80', raw)

    def test_too_long_name_is_a_value_error(self):
        with self.assertRaises(ValueError):
            BW.create_blank_world(self.saves / 'long', name='x' * 70000)
        self.assertFalse((self.saves / 'long').exists())

    def test_level_dat_without_level_name_raises(self):
        f = self.saves / 'level.dat'
        f.write_bytes(gzip.compress(b'\x0a\x00\x00\x00'))
        with self.assertRaises(RuntimeError):
            BW.set_level_name(f, 'x')

    def test_refuses_existing_save(self):
        target = self.saves / 'Existing'
        target.mkdir()
        (target / 'keep.txt').write_text('x')
        with self.assertRaises(FileExistsError):
            BW.create_blank_world(target)
        self.assertEqual((target / 'keep.txt').read_text(), 'x')

    def test_refuses_missing_instance(self):
        with self.assertRaises(FileNotFoundError):
            BW.create_blank_world(Path(self.tmp.name) / 'nope' / 'saves' / 'W')


class TemplateFlatLayersTest(unittest.TestCase):
    def test_single_bedrock_layer_at_world_floor(self):
        """Flat layers run from the world floor (Y -64); only bedrock, one block thick."""
        path = BW.TEMPLATE / 'data' / 'minecraft' / 'world_gen_settings.dat'
        b = gzip.open(path).read()
        nl = struct.unpack_from('>H', b, 1)[0]
        root, _ = N.read_tag(b, 3 + nl, N.COMPOUND)
        gen = root.value['data'].value['dimensions'].value['minecraft:overworld'].value['generator']
        layers = gen.value['settings'].value['layers'].value[1]
        self.assertEqual(len(layers), 1)
        self.assertEqual(layers[0].value['block'].value, 'minecraft:bedrock')
        self.assertEqual(layers[0].value['height'].value, 1)


if __name__ == '__main__':
    unittest.main()
