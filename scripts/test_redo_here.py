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


class TestProviderCliBoundary(unittest.TestCase):
    def run_cli(self, mode, entry='redo'):
        # Fresh process exercises redo_here -> cell_features -> provider shim,
        # rather than calling the error renderer directly.
        code = r"""
import sys
from pathlib import Path
from unittest import mock
import script_env
import active_world
import world_provider
import redo_here
from unit_test_world import unit_test_world
mode, entry = sys.argv[1:]
with unit_test_world() as root:
    for module in ('portals', 'buildings', 'cell_features'):
        sys.modules.pop(module, None)
    source = 'def describe(*a, **k): return {}\ndef for_footprint(*a, **k): return []\n'
    for package, filename in (('uos-portals', 'portals.py'), ('uos-buildings', 'buildings.py')):
        folder = root / 'providers' / package
        if package == 'uos-portals' and mode == 'package':
            continue
        folder.mkdir(parents=True)
        if package == 'uos-portals' and mode == 'module':
            continue
        body = source
        if package == 'uos-portals' and mode in ('ImportError', 'FileNotFoundError'):
            body = f'raise {mode}("provider bug sentinel")\n'
        (folder / filename).write_text(body)
    with mock.patch.object(active_world, 'set_world', return_value=root):
        if entry == 'redo':
            redo_here.main(['--world', root.name, '--at', '1,2', '--json'])
        else:
            script_env.prepend_world_activation(['--world', root.name])
            world_provider.run_world_provider('providers/uos-portals', 'portals.py')
"""
        return subprocess.run([sys.executable, '-c', code, mode, entry], cwd=ROOT / 'scripts',
                              text=True, capture_output=True)

    def test_redo_missing_package_and_module_are_concise(self):
        for mode in ('package', 'module'):
            with self.subTest(mode=mode):
                result = self.run_cli(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f'local provider {mode} missing', result.stderr)
                self.assertIn('uos-portals', result.stderr)
                self.assertIn('worldbuilder-unit-', result.stderr)
                self.assertIn('config/local-providers.json', result.stderr)
                self.assertNotIn('Traceback', result.stderr)

    def test_valid_providers_allow_redo_cli_to_complete(self):
        result = self.run_cli('valid')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"primary": null', result.stdout)

    def test_internal_provider_errors_keep_their_tracebacks(self):
        for error in ('ImportError', 'FileNotFoundError'):
            for entry in ('redo', 'provider'):
                with self.subTest(error=error, entry=entry):
                    result = self.run_cli(error, entry)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('Traceback', result.stderr)
                    self.assertIn(f'{error}: provider bug sentinel', result.stderr)
                    self.assertNotIn('local provider package missing', result.stderr)

    def test_direct_provider_cli_names_the_selected_world_and_configuration(self):
        for mode in ('package', 'module'):
            result = self.run_cli(mode, 'provider')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(f'local provider {mode} missing', result.stderr)
            self.assertIn('worldbuilder-unit-', result.stderr)
            self.assertIn('config/local-providers.json', result.stderr)
            self.assertNotIn('Traceback', result.stderr)


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
