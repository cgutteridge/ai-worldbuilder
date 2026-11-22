"""Catalogue role dispatch, optional evidence and provider failure boundaries."""
import contextlib
import io
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cell_sources
import script_env
import world_provider as wp


class TestProviderRoles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.package = self.root / 'providers' / 'arbitrary-register-name'
        self.package.mkdir(parents=True)
        self.row = {'id': 'fixture-register', 'role': 'entrance_register',
                    'package': 'providers/arbitrary-register-name', 'types': ['poi_register'],
                    'home': 'world'}
        self.data = {'providers': [self.row, {'id': 'other-register',
                     'role': 'building_register', 'types': ['poi_register']}]}
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(wp, 'load_place_sources', return_value=self.data).start()
        mock.patch.object(script_env, 'ROOT', self.root).start()
        mock.patch.object(script_env, 'world_bound', return_value=True).start()
        self.addCleanup(sys.modules.pop, '_test_provider', None)

    def test_role_selects_correct_register_not_shared_evidence_type(self):
        (self.package / 'portals.py').write_text('ANSWER = 42\n')
        loaded = wp.load_world_provider('entrance_register', 'portals.py', '_test_provider')
        self.assertEqual(loaded.ANSWER, 42)
        self.assertIs(wp.load_world_provider('entrance_register', 'portals.py', '_test_provider'), loaded)

    def test_all_four_cli_shims_dispatch_by_role(self):
        shims = Path(cell_sources.__file__).resolve().parents[2] / 'shims'
        self.data['providers'] = [self.row]
        for name, role in [('portals', 'entrance_register'), ('buildings', 'building_register'),
                           ('tpo_fetch', 'tree_preservation'), ('aerial_mosaic', 'aerial_mosaic')]:
            with self.subTest(shim=name):
                self.row['role'] = role
                (self.package / f'{name}.py').write_text(f'print({role!r})\n')
                out = io.StringIO()
                with mock.patch.object(script_env, 'prepend_world_activation'), \
                        mock.patch.object(sys, 'argv', [name]), contextlib.redirect_stdout(out):
                    runpy.run_path(str(shims / f'{name}.py'), run_name='__main__')
                self.assertEqual(out.getvalue().strip(), role)

    def test_system_home_is_respected_even_when_world_package_exists(self):
        system = self.root / 'tool'
        package = system / self.row['package']
        package.mkdir(parents=True)
        (package / 'portals.py').write_text('ANSWER = "system"\n')
        (self.package / 'portals.py').write_text('ANSWER = "world"\n')
        self.row['home'] = 'system'
        with mock.patch.object(script_env, 'TOOL_ROOT', system):
            loaded = wp.load_world_provider('entrance_register', 'portals.py', '_test_provider')
        self.assertEqual(loaded.ANSWER, 'system')

    def test_optional_role_missing_returns_none_but_required_role_raises(self):
        self.data['providers'] = []
        self.assertIsNone(wp.load_world_provider('entrance_register', 'portals.py', '_test_provider', optional=True))
        with self.assertRaisesRegex(wp.MissingProviderError, 'provider role missing'):
            wp.load_world_provider('entrance_register', 'portals.py', '_test_provider')

    def test_ambiguous_role_is_an_error_even_when_optional(self):
        self.data['providers'].append(dict(self.row, id='duplicate'))
        with self.assertRaisesRegex(ValueError, 'exactly one provider'):
            wp.load_world_provider('entrance_register', 'portals.py', '_test_provider', optional=True)

    def test_configured_missing_package_and_module_are_errors_even_when_optional(self):
        for package, expected in [('providers/absent', 'package'),
                                  ('providers/arbitrary-register-name', 'module')]:
            with self.subTest(expected=expected):
                self.row['package'] = package
                with self.assertRaisesRegex(wp.MissingProviderError, f'local provider {expected} missing'):
                    wp.load_world_provider('entrance_register', 'portals.py', '_test_provider', optional=True)

    def test_existing_provider_bug_propagates_without_caching_partial_module(self):
        for error in ('ImportError', 'FileNotFoundError'):
            with self.subTest(error=error):
                (self.package / 'portals.py').write_text(f'raise {error}("provider bug")\n')
                with self.assertRaisesRegex((ImportError, FileNotFoundError), 'provider bug') as caught:
                    wp.load_world_provider('entrance_register', 'portals.py', '_test_provider', optional=True)
                self.assertNotIsInstance(caught.exception, wp.MissingProviderError)
                self.assertNotIn('_test_provider', sys.modules)

    def test_missing_provider_error_is_concise_with_world_context(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            try:
                wp.load_world_provider('entrance_register', 'portals.py', '_test_provider')
            except wp.MissingProviderError as exc:
                script_env.provider_excepthook(type(exc), exc, exc.__traceback__)
        self.assertIn('entrance_register', error.getvalue())
        self.assertIn('config/local-providers.json', error.getvalue())
        self.assertNotIn('Traceback', error.getvalue())


class TestOptionalProviderConsumers(unittest.TestCase):
    def test_cell_features_and_registry_next_work_with_no_local_providers(self):
        # A fresh interpreter avoids the provider module doubles installed by
        # the runner: this reproduces a new place, rather than mocking imports.
        code = """
import sys
import script_env
from unit_test_world import unit_test_world
with unit_test_world() as root:
    from unittest import mock
    for name in ('portals', 'buildings', 'cell_features'):
        sys.modules.pop(name, None)
    import cell_features
    assert cell_features.for_footprint([]) == []
    assert cell_features.pictures_for([]) == []
    import evidence_sheet
    assert evidence_sheet._portals_for_build('absent', (0, 0, 1, 1)) == []
    import cell_registry
    with mock.patch.object(cell_registry, 'activate_world_from_args'):
        assert cell_registry.main(['--world', root.name, 'next']) == 0
"""
        scripts = Path(cell_sources.__file__).resolve().parents[2] / 'scripts'
        result = subprocess.run([sys.executable, '-c', code], cwd=scripts,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_synthetic_registers_preserve_feature_and_photo_evidence(self):
        from unit_test_world import unit_test_world
        import cell_features
        import evidence_sheet
        with unit_test_world() as root:
            rows = []
            for role, name, record in [
                ('entrance_register', 'portals', {'id': '9-A', 'x': 10, 'z': 5}),
                ('building_register', 'buildings', {'id': '9', 'image': 'photo.jpg', 'rings': []}),
            ]:
                folder = root / 'providers' / f'fixture-{name}'
                folder.mkdir(parents=True)
                (folder / f'{name}.py').write_text(
                    f'RECORD = {record!r}\n'
                    'def for_footprint(rings): return [RECORD]\n'
                    'def in_bbox(*args, **kwargs): return [RECORD]\n'
                    'def describe(record): return "fixture " + record["id"]\n')
                rows.append({'id': f'fixture-{name}', 'role': role,
                             'package': f'providers/fixture-{name}', 'types': ['poi_register']})
            (root / 'config' / 'local-providers.json').write_text(json.dumps({'providers': rows}))
            self.assertEqual(cell_features.for_footprint([])[0]['id'], '9-A')
            self.assertEqual(cell_features.pictures_for([])[0]['id'], '9')
            self.assertEqual(cell_features.describe({'id': '9-A'}), 'fixture 9-A')
            self.assertEqual(cell_features.describe_building({'id': '9'}), 'fixture 9')
            self.assertEqual(evidence_sheet._portals_for_build('absent', (0, 0, 1, 1))[0]['id'], '9-A')
