"""Tests for quiet unit-test reporting used by pre-commit."""

from __future__ import annotations

import io
import unittest
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


if __name__ == '__main__':
    unittest.main()
