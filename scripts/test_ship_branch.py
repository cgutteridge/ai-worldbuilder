"""Tests for ship_branch.merge using throwaway git repositories."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import ship_branch as sb


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ['git', *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=sb._git_env(),
    )
    return result.stdout.strip()


def _init_repo(root: Path) -> None:
    _git(root, 'init', '-b', 'main')
    _git(root, 'config', 'user.email', 'ship-branch-test@example.com')
    _git(root, 'config', 'user.name', 'Ship Branch Test')
    _git(root, 'config', 'commit.gpgsign', 'false')
    (root / 'README').write_text('main\n', encoding='utf-8')
    _git(root, 'add', 'README')
    _git(root, 'commit', '-m', 'initial')


def _add_branch_commit(root: Path, branch: str, filename: str, body: str) -> None:
    _git(root, 'checkout', '-b', branch)
    (root / filename).write_text(body, encoding='utf-8')
    _git(root, 'add', filename)
    _git(root, 'commit', '-m', f'work on {branch}')
    _git(root, 'checkout', 'main')


class ShipBranchMergeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / 'repo'
        self.root.mkdir()
        _init_repo(self.root)
        self.branch = 'task/61-example'
        _add_branch_commit(self.root, self.branch, 'feature.txt', 'ok\n')

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _head_subject(self) -> str:
        return _git(self.root, 'log', '-1', '--pretty=%s')

    def _branches(self) -> set[str]:
        out = _git(self.root, 'branch', '--format=%(refname:short)')
        return {line for line in out.splitlines() if line}

    def test_green_merge_deletes_branch_and_worktree(self) -> None:
        wt = Path(self._tmp.name) / 'wt-feature'
        _git(self.root, 'worktree', 'add', str(wt), self.branch)
        self.assertTrue(wt.is_dir())
        self.assertIsNotNone(sb.worktree_path_for_branch(self.root, self.branch))

        calls: list[str] = []

        def runner(repo: Path) -> int:
            calls.append(sb.current_branch(repo))
            return 0

        code = sb.merge_branch(self.root, self.branch, run_tests=runner)
        self.assertEqual(code, 0)
        self.assertEqual(calls, [self.branch, 'main'])
        self.assertEqual(sb.current_branch(self.root), 'main')
        self.assertIn(self.branch, self._head_subject())
        self.assertNotIn(self.branch, self._branches())
        self.assertFalse(wt.exists())
        # merge commit, not a fast-forward tip
        parents = _git(self.root, 'rev-list', '--parents', '-n', '1', 'HEAD').split()
        self.assertGreaterEqual(len(parents), 3)

    def test_failing_tip_aborts_without_merge(self) -> None:
        main_before = _git(self.root, 'rev-parse', 'HEAD')
        calls: list[str] = []

        def runner(repo: Path) -> int:
            calls.append(sb.current_branch(repo))
            return 1

        code = sb.merge_branch(self.root, self.branch, run_tests=runner)
        self.assertEqual(code, 1)
        self.assertEqual(calls, [self.branch])
        self.assertEqual(_git(self.root, 'rev-parse', 'HEAD'), main_before)
        self.assertIn(self.branch, self._branches())
        self.assertEqual(sb.current_branch(self.root), 'main')

    def test_failing_main_after_merge_keeps_merge_skips_cleanup(self) -> None:
        wt = Path(self._tmp.name) / 'wt-feature'
        _git(self.root, 'worktree', 'add', str(wt), self.branch)
        main_before = _git(self.root, 'rev-parse', 'main')
        calls: list[str] = []

        def runner(repo: Path) -> int:
            branch = sb.current_branch(repo)
            calls.append(branch)
            # Tip green; post-merge main red.
            return 0 if branch == self.branch else 1

        code = sb.merge_branch(self.root, self.branch, run_tests=runner)
        self.assertEqual(code, 1)
        self.assertEqual(calls, [self.branch, 'main'])
        self.assertEqual(sb.current_branch(self.root), 'main')
        self.assertNotEqual(_git(self.root, 'rev-parse', 'HEAD'), main_before)
        self.assertIn(self.branch, self._head_subject())
        # No auto-revert and no cleanup.
        self.assertIn(self.branch, self._branches())
        self.assertTrue(wt.exists())
        self.assertIsNotNone(sb.worktree_path_for_branch(self.root, self.branch))

    def test_missing_branch_reports_and_exits(self) -> None:
        code = sb.merge_branch(self.root, 'task/does-not-exist', run_tests=lambda _r: 0)
        self.assertEqual(code, 1)
        self.assertEqual(sb.current_branch(self.root), 'main')

    def test_cli_merge_missing_branch(self) -> None:
        code = sb.main(
            ['merge', 'task/does-not-exist', '--repo-root', str(self.root)],
        )
        self.assertEqual(code, 1)
        self.assertEqual(sb.current_branch(self.root), 'main')

    def test_git_env_clears_hook_overrides(self) -> None:
        """Inherited GIT_DIR must not pin throwaway-repo commands to the host."""
        poisoned = dict(os.environ)
        poisoned['GIT_DIR'] = '/nonexistent/git-dir'
        poisoned['GIT_WORK_TREE'] = '/nonexistent/work-tree'
        cleaned = sb._git_env(poisoned)
        self.assertNotIn('GIT_DIR', cleaned)
        self.assertNotIn('GIT_WORK_TREE', cleaned)


if __name__ == '__main__':
    unittest.main()
