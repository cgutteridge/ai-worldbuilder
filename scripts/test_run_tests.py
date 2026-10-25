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


class FullSuiteLockTest(unittest.TestCase):
    """Advisory flock for default full discover; --only stays unlocked."""

    def _git_repo(self) -> tempfile.TemporaryDirectory[str]:
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        import subprocess

        subprocess.run(
            ['git', 'init', '-b', 'main'],
            cwd=root,
            check=True,
            capture_output=True,
        )
        return tmp

    def test_lock_path_under_git_common_dir(self) -> None:
        with self._git_repo() as tmp:
            root = Path(tmp)
            path = run_tests.full_suite_lock_path(root)
            self.assertEqual(path.name, run_tests.FULL_SUITE_LOCK_NAME)
            common = run_tests.git_common_dir(root)
            self.assertEqual(path.parent.resolve(), common.resolve())

    def test_second_full_lock_fails_fast(self) -> None:
        with self._git_repo() as tmp:
            root = Path(tmp)
            with run_tests.full_suite_lock(root):
                with self.assertRaises(run_tests.FullSuiteLocked):
                    with run_tests.full_suite_lock(root):
                        pass

    def test_parent_holds_skips_acquire_while_outer_held(self) -> None:
        with self._git_repo() as tmp:
            root = Path(tmp)
            with run_tests.full_suite_lock(root):
                with run_tests.full_suite_lock(root, parent_holds=True):
                    pass  # must not raise

    def test_only_mode_does_not_take_lock(self) -> None:
        env = {**os.environ, run_tests.WORLD_ENV: '/tmp/fake-world'}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(run_tests, 'full_suite_lock') as lock_cm:
                with mock.patch.object(
                    run_tests,
                    'load_suite',
                    return_value=unittest.TestSuite(),
                ):
                    with mock.patch.object(
                        run_tests, 'report_result', return_value=0
                    ):
                        with mock.patch(
                            'unittest.TextTestRunner'
                        ) as runner_cls:
                            runner_cls.return_value.run.return_value = mock.Mock()
                            code = run_tests.main(['--only', 'test_run_tests'])
        self.assertEqual(code, 0)
        lock_cm.assert_not_called()

    def test_full_run_uses_lock_and_exits_2_when_busy(self) -> None:
        env = {**os.environ, run_tests.WORLD_ENV: '/tmp/fake-world'}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(
                run_tests,
                'full_suite_lock',
                side_effect=run_tests.FullSuiteLocked('busy'),
            ):
                with mock.patch('sys.stderr', new_callable=io.StringIO) as err:
                    code = run_tests.main([])
        self.assertEqual(code, 2)
        self.assertIn('busy', err.getvalue())

    def test_parent_holds_flag_and_env(self) -> None:
        args = run_tests.parse_args(['--parent-holds-lock'])
        self.assertTrue(args.parent_holds_lock)
        self.assertTrue(run_tests.parent_already_holds_lock(args, {'x': '1'}))
        args2 = run_tests.parse_args([])
        self.assertFalse(args2.parent_holds_lock)
        self.assertTrue(
            run_tests.parent_already_holds_lock(
                args2, {run_tests.PARENT_HOLDS_LOCK_ENV: '1'}
            )
        )
        self.assertFalse(
            run_tests.parent_already_holds_lock(args2, {})
        )


if __name__ == '__main__':
    unittest.main()
