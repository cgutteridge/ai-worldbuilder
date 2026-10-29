"""Unit tests for scripts/redo_here.py (no Minecraft, no world writes)."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import script_env
script_env.ensure_import_paths()

import redo_here as rh  # noqa: E402


class TestWorldActivation(unittest.TestCase):
    def test_fresh_process_import_then_cli_with_disposable_world(self):
        code = """
import redo_here
from unittest import mock
from unit_test_world import unit_test_world
with unit_test_world(), mock.patch.object(redo_here, 'activate_world_from_args'):
    redo_here.main(['--world', 'unit-test', '--at', '1,2', '--json'])
"""
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT / 'scripts',
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"primary": null', result.stdout)


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
        from journal_testkit import JournalWorld
        with JournalWorld() as jw, mock.patch.object(rh, 'features_covering', return_value=['a', 'b']):
            jw.build('b', 'feature', {(1, 50, 2): 'minecraft:stone'})
            self.assertEqual(rh.primary_feature(1, 2, y=50), 'b')

    def test_many_with_y_picks_nearest_in_column(self):
        from journal_testkit import JournalWorld
        with JournalWorld() as jw, mock.patch.object(rh, 'features_covering', return_value=['a', 'b']):
            jw.build('a', 'feature', {(1, 48, 2): 'minecraft:stone'})
            jw.build('b', 'feature', {(1, 60, 2): 'minecraft:bricks'})
            self.assertEqual(rh.primary_feature(1, 2, y=58), 'b')


if __name__ == '__main__':
    unittest.main()
