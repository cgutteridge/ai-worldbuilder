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
        b = gzip.decompress((save / 'level.dat').read_bytes())
        i = b.find(b'\x08\x00\x09LevelName') + 12
        n = struct.unpack_from('>H', b, i)[0]
        return b[i + 2:i + 2 + n].decode('utf-8')

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
