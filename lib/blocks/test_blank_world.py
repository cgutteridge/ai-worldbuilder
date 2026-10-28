#!/usr/bin/env python3
"""Tests for blank-world creation (the one permitted write under saves/)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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


if __name__ == '__main__':
    unittest.main()
