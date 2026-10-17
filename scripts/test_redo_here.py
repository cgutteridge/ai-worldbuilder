"""Unit tests for scripts/redo_here.py (no Minecraft, no world writes)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import redo_here as rh  # noqa: E402


class TestScriptFor(unittest.TestCase):
    def test_direct_build_name_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            path = d / 'build_highfield_demo.py'
            path.write_text("NAME = 'highfield_demo'\n", encoding='utf-8')
            self.assertEqual(rh.script_for('highfield_demo', d), path)

    def test_name_match_when_filename_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            path = d / 'build_something_else.py'
            path.write_text("NAME = 'highfield_odd'\n", encoding='utf-8')
            self.assertEqual(rh.script_for('highfield_odd', d), path)

    def test_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(rh.script_for('nope', Path(tmp)))


class TestPrimaryFeature(unittest.TestCase):
    def test_none(self):
        with mock.patch.object(rh, 'features_covering', return_value=[]):
            self.assertIsNone(rh.primary_feature(1, 2))

    def test_single(self):
        with mock.patch.object(rh, 'features_covering', return_value=['a']):
            self.assertEqual(rh.primary_feature(1, 2), 'a')

    def test_many_without_y_is_ambiguous(self):
        with mock.patch.object(rh, 'features_covering', return_value=['a', 'b']):
            self.assertIsNone(rh.primary_feature(1, 2))

    def test_many_with_y_picks_owner(self):
        with mock.patch.object(rh, 'features_covering', return_value=['a', 'b']), \
             mock.patch.object(rh, 'owned_in', return_value={(1, 50, 2): ('b', 'minecraft:stone')}):
            self.assertEqual(rh.primary_feature(1, 2, y=50), 'b')

    def test_many_with_y_picks_nearest_in_column(self):
        owned = {
            (1, 48, 2): ('a', 'minecraft:stone'),
            (1, 60, 2): ('b', 'minecraft:bricks'),
        }
        with mock.patch.object(rh, 'features_covering', return_value=['a', 'b']), \
             mock.patch.object(rh, 'owned_in', return_value=owned):
            self.assertEqual(rh.primary_feature(1, 2, y=58), 'b')


if __name__ == '__main__':
    unittest.main()
