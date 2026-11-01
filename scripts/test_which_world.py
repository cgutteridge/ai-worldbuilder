"""Discovery command outcomes with synthetic process and checkout evidence."""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import which_world


class TestWhichWorld(unittest.TestCase):
    def test_reports_unique_checkout_without_selecting_it(self):
        import active_world
        previous = active_world.WORLD_ROOT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / 'worlds/demo/config'
            cfg.mkdir(parents=True)
            save = root / 'instance/saves/game'
            cfg.joinpath('world.json').write_text(json.dumps({'save_path': str(save.resolve())}))
            out = io.StringIO()
            with mock.patch.object(which_world, 'TOOL_ROOT', root), \
                    mock.patch('world_discovery.running_save', return_value=save), \
                    contextlib.redirect_stdout(out):
                self.assertEqual(which_world.main(['--json']), 0)
            self.assertEqual(json.loads(out.getvalue()), {'world': 'demo', 'save_path': str(save.resolve())})
        self.assertIs(active_world.WORLD_ROOT, previous)

    def test_rejects_ambiguous_unmatched_and_malformed_configs(self):
        from world_discovery import DiscoveryError, matching_world
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save = root / 'save'
            for name in ('a', 'b'):
                cfg = root / name / 'config'
                cfg.mkdir(parents=True)
                (cfg / 'world.json').write_text(json.dumps({'save_path': '../save'}))
            with self.assertRaisesRegex(DiscoveryError, '2 matching'):
                matching_world(save, root)
            with self.assertRaisesRegex(DiscoveryError, '0 matching'):
                matching_world(root / 'other', root)
            (root / 'a/config/world.json').write_text('broken')
            with self.assertRaisesRegex(DiscoveryError, 'invalid world config'):
                matching_world(save, root)

    def test_unknown_identity_returns_nonzero_without_success_output(self):
        from world_discovery import DiscoveryError
        out, err = io.StringIO(), io.StringIO()
        with mock.patch('world_discovery.running_save', side_effect=DiscoveryError('unknown identity')), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(which_world.main([]), 1)
        self.assertEqual(out.getvalue(), '')
        self.assertIn('unknown identity', err.getvalue())

    def test_stale_lock_and_server_down_are_not_save_identity(self):
        from world_discovery import DiscoveryError, running_save
        with mock.patch('world_discovery.subprocess.run', return_value=mock.Mock(
                returncode=1, stdout='', stderr='')):
            with self.assertRaisesRegex(DiscoveryError, 'unavailable'):
                running_save()
        # A listener with no open session.lock cannot select a stale on-disk file.
        with mock.patch('world_discovery.subprocess.run', side_effect=[
                mock.Mock(returncode=0, stdout='p12\n', stderr=''),
                mock.Mock(returncode=0, stdout='p12\nn/tmp/unrelated\n', stderr=''),
                mock.Mock(returncode=0, stdout='p12\n', stderr='')]):
            with self.assertRaisesRegex(DiscoveryError, 'unknown or ambiguous'):
                running_save()

    def test_listener_process_open_save_is_used_not_equal_basenames(self):
        import world_discovery as wd
        with mock.patch('world_discovery.subprocess.run', side_effect=[
                mock.Mock(returncode=0, stdout='p12\n', stderr=''),
                mock.Mock(returncode=0, stdout='p12\nn/instance-b/saves/game/session.lock\n', stderr=''),
                mock.Mock(returncode=0, stdout='p12\n', stderr='')]):
            self.assertEqual(wd.running_save(), Path('/instance-b/saves/game'))
        with mock.patch('world_discovery.subprocess.run', side_effect=[
                mock.Mock(returncode=0, stdout='p12\n', stderr=''),
                mock.Mock(returncode=0, stdout='p12\nn/a/session.lock\nn/b/session.lock\n', stderr=''),
                mock.Mock(returncode=0, stdout='p12\n', stderr='')]):
            with self.assertRaises(wd.DiscoveryError):
                wd.running_save()


class TestDiscoveryEdges(unittest.TestCase):
    def test_equal_save_basenames_in_different_instances_are_not_confused(self):
        from world_discovery import matching_world
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('a', 'b'):
                cfg = root / 'worlds' / name / 'config'
                cfg.mkdir(parents=True)
                (cfg / 'world.json').write_text(json.dumps({'save_path': f'../../instances/{name}/saves/game'}))
            selected = root / 'instances/b/saves/game'
            self.assertEqual(matching_world(selected, root / 'worlds')['world'], 'b')

    def test_inspection_errors_and_listener_changes_fail_closed(self):
        import world_discovery as wd
        failures = (FileNotFoundError('lsof absent'), subprocess.TimeoutExpired('lsof', 10))
        for failure in failures:
            with self.subTest(failure=failure), mock.patch.object(wd.subprocess, 'run', side_effect=failure):
                with self.assertRaises(wd.DiscoveryError):
                    wd.running_save()
        for first in ('', 'p12\np13\n'):
            with mock.patch.object(wd, '_lsof', return_value=first):
                with self.assertRaises(wd.DiscoveryError):
                    wd.running_save()
        with mock.patch.object(wd, '_lsof', side_effect=['p12\n', 'n/save/session.lock\n', 'p13\n']):
            with self.assertRaises(wd.DiscoveryError):
                wd.running_save()
        with mock.patch.object(wd.subprocess, 'run', return_value=mock.Mock(returncode=2, stdout='', stderr='permission denied')):
            with self.assertRaisesRegex(wd.DiscoveryError, 'permission denied'):
                wd.running_save()

    def test_fresh_import_has_no_selected_world_or_world_owned_modules(self):
        code = """
import which_world, active_world, sys
assert active_world.WORLD_ROOT is None
assert not {'portals', 'buildings', 'aerial_mosaic', 'tpo_fetch'} & sys.modules.keys()
"""
        result = subprocess.run([sys.executable, '-c', code], cwd=Path(which_world.__file__).parent,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
