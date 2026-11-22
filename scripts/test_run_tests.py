"""Tests for quiet unit-test reporting used by pre-commit."""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
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


def _write_test_module(directory: Path, stem: str) -> None:
    """Write a one-test module named ``stem`` under ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f'{stem}.py').write_text(
        'import unittest\n'
        'class T(unittest.TestCase):\n'
        '    def test_ok(self):\n'
        '        self.assertTrue(True)\n',
        encoding='utf-8',
    )


def _case_ids(suite: unittest.TestSuite) -> set[str]:
    """Module stems loaded in ``suite`` (discover nests a suite per module)."""
    found: set[str] = set()
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            found.update(_case_ids(item))
            continue
        found.add(item.id().split('.')[0])
    return found


class BakeoffDiscoveryTest(unittest.TestCase):
    """Default discovery skips the closed bake-off; ``--bakeoff`` collects it."""

    def tearDown(self) -> None:
        stale = [
            name
            for name in list(sys.modules)
            if name.startswith('test_bake_disc_')
        ]
        for name in stale:
            sys.modules.pop(name, None)

    def test_default_suite_skips_bakeoff_until_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / 'scripts'
            bakeoff = root / 'bakeoff'
            token = f'{os.getpid()}_{id(root)}'
            scripts_stem = f'test_bake_disc_scripts_{token}'
            bake_stem = f'test_bake_disc_bake_{token}'
            _write_test_module(scripts, scripts_stem)
            _write_test_module(bakeoff, bake_stem)

            default_extras = run_tests.discover_extra_dirs(
                root, include_bakeoff=False
            )
            self.assertFalse(any(path.name == 'bakeoff' for path in default_extras))
            default_suite = run_tests.load_suite(
                scripts, only=None, extra_dirs=tuple(default_extras)
            )
            self.assertEqual(_case_ids(default_suite), {scripts_stem})

            with self.assertRaises(run_tests.UnknownTestModule):
                run_tests.load_suite(
                    scripts,
                    only=[bake_stem],
                    extra_dirs=tuple(default_extras),
                )

            opted_extras = run_tests.discover_extra_dirs(
                root, include_bakeoff=True
            )
            self.assertTrue(
                any(path.resolve() == bakeoff.resolve() for path in opted_extras)
            )
            opted_suite = run_tests.load_suite(
                scripts, only=None, extra_dirs=tuple(opted_extras)
            )
            self.assertEqual(_case_ids(opted_suite), {scripts_stem, bake_stem})
            only_bake = run_tests.load_suite(
                scripts, only=[bake_stem], extra_dirs=tuple(opted_extras)
            )
            self.assertEqual(_case_ids(only_bake), {bake_stem})

    def test_real_repo_default_extras_omit_bakeoff_dir(self) -> None:
        root = Path(__file__).resolve().parents[1]
        default_extras = run_tests.discover_extra_dirs(
            root, include_bakeoff=False
        )
        opted_extras = run_tests.discover_extra_dirs(
            root, include_bakeoff=True
        )
        self.assertFalse(any(path.name == 'bakeoff' for path in default_extras))
        self.assertTrue(any(path.name == 'bakeoff' for path in opted_extras))

    def test_cli_flag_is_the_opt_in(self) -> None:
        self.assertFalse(run_tests.parse_args([]).bakeoff)
        self.assertTrue(run_tests.parse_args(['--bakeoff']).bakeoff)

        captured: list[tuple[Path, ...]] = []

        def _capture(*_args: object, **kwargs: object) -> unittest.TestSuite:
            extra = kwargs.get('extra_dirs')
            self.assertIsInstance(extra, tuple)
            captured.append(tuple(extra))
            return unittest.TestSuite()

        @contextlib.contextmanager
        def _cm(*_args: object, **_kwargs: object):
            yield None

        with mock.patch.object(run_tests, 'unit_test_world', _cm):
            with mock.patch.object(run_tests, 'full_suite_lock', _cm):
                with mock.patch.object(run_tests, '_run_suite', return_value=0):
                    with mock.patch.object(
                        run_tests, 'load_suite', side_effect=_capture
                    ):
                        self.assertEqual(run_tests.main([]), 0)
                        self.assertEqual(run_tests.main(['--bakeoff']), 0)
        self.assertEqual(len(captured), 2)
        self.assertFalse(any(path.name == 'bakeoff' for path in captured[0]))
        self.assertTrue(any(path.name == 'bakeoff' for path in captured[1]))


class WorldIsolationTest(unittest.TestCase):
    """The runner must work without a checkout and ignore selected-world data."""

    def test_fixture_clears_and_restores_hook_git_environment(self) -> None:
        from unit_test_world import unit_test_world

        poisoned = {'GIT_DIR': '/invalid/repo', 'GIT_INDEX_FILE': '/invalid/index'}
        with mock.patch.dict(os.environ, poisoned):
            with unit_test_world():
                self.assertNotIn('GIT_DIR', os.environ)
                self.assertNotIn('GIT_INDEX_FILE', os.environ)
            self.assertEqual(os.environ['GIT_DIR'], poisoned['GIT_DIR'])
            self.assertEqual(os.environ['GIT_INDEX_FILE'], poisoned['GIT_INDEX_FILE'])

    def test_runner_uses_fixture_with_unset_or_invalid_world(self) -> None:
        runner = Path(__file__).with_name('run_tests.py')
        with tempfile.TemporaryDirectory() as tmp:
            selected = Path(tmp) / 'selected-world'
            selected.mkdir()
            (selected / 'sentinel').write_text('untouched')
            for world in (None, str(selected)):
                with self.subTest(world=world):
                    env = dict(os.environ)
                    # Runner always uses unit_test_world(); any prior world
                    # selection in the parent env must not touch ``selected``.
                    env.pop('AI_WORLDBUILDER_REGISTRY_ROOT', None)
                    env['AI_WORLDBUILDER_REGISTRY_ROOT'] = str(selected)
                    result = subprocess.run(
                        [sys.executable, str(runner), '--only', 'test_cellmath'],
                        env=env, capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([p.name for p in selected.iterdir()], ['sentinel'])
            self.assertEqual((selected / 'sentinel').read_text(), 'untouched')


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
        with mock.patch.dict(os.environ, dict(os.environ), clear=True):
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
        with mock.patch.dict(os.environ, dict(os.environ), clear=True):
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
