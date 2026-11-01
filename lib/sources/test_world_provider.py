"""Provider CLI errors distinguish absent packages from bugs inside providers."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import world_provider as wp


class TestProviderErrors(unittest.TestCase):
    def test_missing_package_is_concise_cli_error_with_world_context(self):
        import script_env
        error = io.StringIO()
        with mock.patch.object(wp, 'resolve_provider_package', side_effect=FileNotFoundError('missing')), \
                contextlib.redirect_stderr(error):
            try:
                wp.load_world_provider('providers/missing', 'demo.py', 'demo')
            except Exception as exc:
                script_env.provider_excepthook(type(exc), exc, exc.__traceback__)
        self.assertIn('providers/missing', error.getvalue())
        self.assertIn('local-providers.json', error.getvalue())
        self.assertNotIn('Traceback', error.getvalue())

    def test_existing_provider_bug_is_not_converted_to_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            path = package / 'demo.py'
            path.write_text("raise ImportError('bug inside provider')\n")
            with mock.patch.object(wp, 'resolve_provider_package', return_value=package):
                with self.assertRaisesRegex(ImportError, 'bug inside provider') as caught:
                    wp.load_world_provider('providers/demo', 'demo.py', '_test_provider')
            self.assertNotIsInstance(caught.exception, wp.MissingProviderError)
            path.write_text('ANSWER = 42\n')
            import sys
            sys.modules.pop('_test_provider', None)
            with mock.patch.object(wp, 'resolve_provider_package', return_value=package):
                self.assertEqual(wp.load_world_provider('providers/demo', 'demo.py', '_test_provider').ANSWER, 42)
            sys.modules.pop('_test_provider', None)

    def test_missing_module_is_known_absence(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(wp, 'resolve_provider_package', return_value=Path(tmp)):
            with self.assertRaises(wp.MissingProviderError):
                wp.load_world_provider('providers/demo', 'absent.py', '_absent_provider')
