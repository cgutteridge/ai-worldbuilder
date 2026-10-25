"""Tests for quiet unit-test reporting used by pre-commit."""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run_tests


class QuietReportTest(unittest.TestCase):
    """Failure ids + capped tracebacks; one-line suite summary."""

    def test_report_result_prints_failures_then_summary(self) -> None:
        result = mock.Mock()
        result.wasSuccessful.return_value = False
        result.testsRun = 10
        fail_test = mock.Mock()
        fail_test.id.return_value = 'mod.T.test_fail'
        err_test = mock.Mock()
        err_test.id.return_value = 'mod.T.test_err'
        result.failures = [(fail_test, 'line-1\n' + '\n'.join(f'f{i}' for i in range(50)))]
        result.errors = [(err_test, 'e0\ne1\ne2')]
        stream = io.StringIO()
        code = run_tests.report_result(result, stream=stream, max_traceback_lines=40)
        self.assertEqual(code, 1)
        text = stream.getvalue()
        self.assertIn('FAIL mod.T.test_fail', text)
        self.assertIn('ERROR mod.T.test_err', text)
        self.assertIn('unit tests: FAILED 2/10', text)
        # Cap: first failure traceback keeps last 40 lines only (plus header)
        fail_block = text.split('ERROR mod.T.test_err')[0]
        self.assertNotIn('f0', fail_block)
        self.assertIn('f49', fail_block)
        self.assertTrue(text.rstrip().endswith('unit tests: FAILED 2/10'))

    def test_report_result_ok_prints_one_line(self) -> None:
        result = mock.Mock()
        result.wasSuccessful.return_value = True
        result.testsRun = 412
        result.failures = []
        result.errors = []
        stream = io.StringIO()
        code = run_tests.report_result(result, stream=stream)
        self.assertEqual(code, 0)
        self.assertEqual(stream.getvalue().strip(), 'unit tests: OK')


class ModuleSelectionTest(unittest.TestCase):
    """``--only`` selects unittest module stems under ``scripts/``."""

    def test_parse_args_collects_repeatable_only_names(self) -> None:
        args = run_tests.parse_args(
            ['--only', 'test_run_tests', '--only', 'test_ship_branch']
        )
        self.assertEqual(args.only, ['test_run_tests', 'test_ship_branch'])

    def test_parse_args_default_only_is_none(self) -> None:
        args = run_tests.parse_args([])
        self.assertIsNone(args.only)

    def _write_pair(self, scripts: Path) -> tuple[str, str]:
        """Write two tiny test modules; return their unique stems."""
        # Unique stems avoid colliding with other temp-dir loads in sys.modules.
        a = f'test_sel_a_{os.getpid()}_{id(scripts)}'
        b = f'test_sel_b_{os.getpid()}_{id(scripts)}'
        for stem in (a, b):
            (scripts / f'{stem}.py').write_text(
                'import unittest\n'
                'class T(unittest.TestCase):\n'
                '    def test_ok(self):\n'
                '        pass\n',
                encoding='utf-8',
            )
        return a, b

    def tearDown(self) -> None:
        # Drop temp stems so discover in another dir is not poisoned.
        stale = [
            name
            for name in list(sys.modules)
            if name.startswith('test_sel_')
        ]
        for name in stale:
            sys.modules.pop(name, None)

    def test_load_suite_with_only_loads_named_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scripts = Path(tmp)
            a, b = self._write_pair(scripts)
            suite = run_tests.load_suite(scripts, only=[a])
            self.assertEqual(suite.countTestCases(), 1)
            suite_both = run_tests.load_suite(scripts, only=[a, b])
            self.assertEqual(suite_both.countTestCases(), 2)

    def test_load_suite_unknown_module_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scripts = Path(tmp)
            self._write_pair(scripts)
            with self.assertRaises(run_tests.UnknownTestModule) as ctx:
                run_tests.load_suite(scripts, only=['test_does_not_exist_xyz'])
            self.assertIn('test_does_not_exist_xyz', str(ctx.exception))

    def test_load_suite_without_only_discovers_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scripts = Path(tmp)
            self._write_pair(scripts)
            suite = run_tests.load_suite(scripts, only=None)
            self.assertEqual(suite.countTestCases(), 2)


if __name__ == '__main__':
    unittest.main()
