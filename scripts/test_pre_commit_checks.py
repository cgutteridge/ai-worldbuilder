"""Tests for pre-commit guardrails (tracker ownership + wave lock helpers)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pre_commit_checks as pcc


class TrackerOwnershipTest(unittest.TestCase):
    def test_worker_branch_rejects_system_tracker_paths(self) -> None:
        forbidden = pcc.forbidden_tracker_paths(
            ['task/22-provider-dual-roots'],
            ['scripts/foo.py', 'tasks/index.json', 'README.md'],
        )
        self.assertEqual(forbidden, ['tasks/index.json'])

    def test_worker_branch_rejects_done_archive(self) -> None:
        forbidden = pcc.forbidden_tracker_paths(
            ['task/17-contact-sheet'],
            ['tasks/done.json'],
        )
        self.assertEqual(forbidden, ['tasks/done.json'])

    def test_main_allows_tracker_edits(self) -> None:
        forbidden = pcc.forbidden_tracker_paths(
            ['main'],
            ['tasks/index.json', 'tasks/done.json'],
        )
        self.assertEqual(forbidden, [])

    def test_unrelated_branch_allows_tracker_edits(self) -> None:
        forbidden = pcc.forbidden_tracker_paths(
            ['feature/hooks'],
            ['tasks/index.json'],
        )
        self.assertEqual(forbidden, [])

    def test_worker_branch_allows_non_tracker_paths(self) -> None:
        forbidden = pcc.forbidden_tracker_paths(
            ['task/15-greeblies-gate'],
            ['scripts/site_handoff.py', 'scripts/test_site_handoff.py'],
        )
        self.assertEqual(forbidden, [])

    def test_detached_head_with_task_tip_still_rejects(self) -> None:
        """Detached HEAD plus a task/* tip at the commit still forbids tracker edits."""
        forbidden = pcc.forbidden_tracker_paths(
            ['task/22-provider-dual-roots'],
            ['tasks/index.json'],
        )
        self.assertEqual(forbidden, ['tasks/index.json'])


class CheckoutBranchNamesTest(unittest.TestCase):
    """``checkout_branch_names`` must not treat sibling task/* tips as the checkout."""

    def test_attached_main_ignores_task_tips_at_same_commit(self) -> None:
        """Attached main with task/* at HEAD: current branch alone (tracker allowed)."""
        root = Path('/tmp/repo')
        with mock.patch.object(pcc, 'current_branch', return_value='main'):
            with mock.patch.object(
                pcc,
                '_git',
                return_value='main\ntask/70-foo\ntask/71-bar',
            ) as git_mock:
                names = pcc.checkout_branch_names(root)
        self.assertEqual(names, ['main'])
        git_mock.assert_not_called()
        self.assertEqual(
            pcc.forbidden_tracker_paths(names, ['tasks/index.json']),
            [],
        )

    def test_detached_head_includes_task_tips_at_head(self) -> None:
        """Detached HEAD still gathers points-at tips so task/* cannot bypass."""
        root = Path('/tmp/repo')

        def fake_git(_repo: Path, *args: str) -> str:
            if args[:2] == ('branch', '--points-at'):
                return 'task/70-foo\ntask/71-bar'
            raise AssertionError(f'unexpected git args: {args!r}')

        with mock.patch.object(pcc, 'current_branch', return_value=''):
            with mock.patch.object(pcc, '_git', side_effect=fake_git):
                names = pcc.checkout_branch_names(root)
        self.assertEqual(names, ['task/70-foo', 'task/71-bar'])
        self.assertEqual(
            pcc.forbidden_tracker_paths(names, ['tasks/index.json']),
            ['tasks/index.json'],
        )

    def test_attached_task_branch_uses_current_alone(self) -> None:
        """Attached task/* checkout is still a worker branch (tracker blocked)."""
        root = Path('/tmp/repo')
        with mock.patch.object(
            pcc,
            'current_branch',
            return_value='task/79-tracker-hook-points-at-head',
        ):
            with mock.patch.object(pcc, '_git') as git_mock:
                names = pcc.checkout_branch_names(root)
        self.assertEqual(names, ['task/79-tracker-hook-points-at-head'])
        git_mock.assert_not_called()
        self.assertEqual(
            pcc.forbidden_tracker_paths(names, ['tasks/index.json']),
            ['tasks/index.json'],
        )


class WaveLockTest(unittest.TestCase):
    def test_acquire_creates_lock_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = pcc.acquire_wave_lock(root, wave_id='wave-1', holder='orch-a')
            self.assertTrue(path.is_file())
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(data['wave_id'], 'wave-1')
            self.assertEqual(data['holder'], 'orch-a')

    def test_acquire_refuses_different_holder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pcc.acquire_wave_lock(root, wave_id='wave-1', holder='orch-a')
            with self.assertRaises(pcc.WaveLockError):
                pcc.acquire_wave_lock(root, wave_id='wave-2', holder='orch-b')

    def test_acquire_same_holder_refreshes_wave_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pcc.acquire_wave_lock(root, wave_id='wave-1', holder='orch-a')
            path = pcc.acquire_wave_lock(root, wave_id='wave-2', holder='orch-a')
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(data['wave_id'], 'wave-2')

    def test_release_removes_lock_for_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pcc.acquire_wave_lock(root, wave_id='wave-1', holder='orch-a')
            pcc.release_wave_lock(root, holder='orch-a')
            self.assertFalse((root / pcc.WAVE_LOCK_NAME).exists())

    def test_release_refuses_other_holder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pcc.acquire_wave_lock(root, wave_id='wave-1', holder='orch-a')
            with self.assertRaises(pcc.WaveLockError):
                pcc.release_wave_lock(root, holder='orch-b')
            self.assertTrue((root / pcc.WAVE_LOCK_NAME).exists())


class CheckSummaryFormatTest(unittest.TestCase):
    """One-line summaries printed after each pre-commit check."""

    def test_ok_summary_without_counts(self) -> None:
        self.assertEqual(
            pcc.format_check_summary('provider-name audit', ok=True),
            'provider-name audit: OK',
        )

    def test_ok_summary_with_total(self) -> None:
        self.assertEqual(
            pcc.format_check_summary('unit tests', ok=True, failed=0, total=412),
            'unit tests: OK',
        )

    def test_failed_summary_includes_counts(self) -> None:
        self.assertEqual(
            pcc.format_check_summary('unit tests', ok=False, failed=3, total=412),
            'unit tests: FAILED 3/412',
        )

    def test_failed_audit_summary(self) -> None:
        self.assertEqual(
            pcc.format_check_summary('provider-name audit', ok=False),
            'provider-name audit: FAILED',
        )


class FailureExcerptTest(unittest.TestCase):
    """Failing test ids + capped tracebacks for quiet pre-commit output."""

    def test_caps_traceback_to_last_n_lines(self) -> None:
        traceback = '\n'.join(f'line-{i}' for i in range(1, 51))
        excerpt = pcc.format_failure_excerpt(
            'pkg.mod.TestCase.test_foo',
            traceback,
            max_lines=40,
        )
        lines = excerpt.splitlines()
        self.assertEqual(lines[0], 'FAIL pkg.mod.TestCase.test_foo')
        self.assertEqual(lines[1], 'line-11')
        self.assertEqual(lines[-1], 'line-50')
        self.assertEqual(len(lines), 41)

    def test_short_traceback_unchanged(self) -> None:
        traceback = 'Traceback (most recent call last):\n  AssertionError'
        excerpt = pcc.format_failure_excerpt('a.b.T.t', traceback, max_lines=40)
        self.assertEqual(
            excerpt,
            'FAIL a.b.T.t\nTraceback (most recent call last):\n  AssertionError',
        )

    def test_formats_multiple_failures(self) -> None:
        report = pcc.format_failure_report(
            [
                ('mod.T.test_a', 'tb-a\nend-a'),
                ('mod.T.test_b', 'tb-b\nend-b'),
            ],
            max_lines=40,
        )
        self.assertIn('FAIL mod.T.test_a', report)
        self.assertIn('FAIL mod.T.test_b', report)
        self.assertIn('end-a', report)
        self.assertIn('end-b', report)


class UnsetWorldEarlyExitTest(unittest.TestCase):
    """World-dependent checks skipped when AI_WORLDBUILDER_WORLD_ROOT is unset."""

    def test_unset_world_skips_checks_and_prints_cause_last(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != pcc.WORLD_ENV}
        printed: list[str] = []

        def capture_print(*args: object, **_kwargs: object) -> None:
            printed.append(' '.join(str(a) for a in args))

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(pcc, 'checkout_branch_names', return_value=['main']):
                with mock.patch.object(pcc, 'staged_paths', return_value=[]):
                    with mock.patch.object(
                        pcc, 'run_provider_name_audit', return_value=0
                    ) as audit:
                        with mock.patch.object(pcc, 'run_unit_tests') as tests:
                            with mock.patch('builtins.print', side_effect=capture_print):
                                code = pcc.run_pre_commit(Path('/tmp/repo'))

        self.assertEqual(code, 1)
        audit.assert_not_called()
        tests.assert_not_called()
        self.assertTrue(printed, 'expected at least the unset-world line')
        self.assertEqual(printed[-1], pcc.UNSET_WORLD_MESSAGE)

    def test_set_world_runs_unit_tests(self) -> None:
        env = {**os.environ, pcc.WORLD_ENV: '/tmp/fake-world'}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(pcc, 'checkout_branch_names', return_value=['main']):
                with mock.patch.object(pcc, 'staged_paths', return_value=[]):
                    with mock.patch.object(
                        pcc, 'run_provider_name_audit', return_value=0
                    ):
                        with mock.patch.object(
                            pcc, 'run_unit_tests', return_value=0
                        ) as tests:
                            with mock.patch('builtins.print'):
                                code = pcc.run_pre_commit(Path('/tmp/repo'))
        self.assertEqual(code, 0)
        tests.assert_called_once()


if __name__ == '__main__':
    unittest.main()
