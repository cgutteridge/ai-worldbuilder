"""Tests for ship_branch.merge and sweep using throwaway git repositories.

Pure helpers (``_git_env``, ``classify_changed_paths``, tip-plan path policy)
live in TestCase classes that create neither a Git repository nor commits.
Merge, sweep, worktree, and lock behaviour keep real temporary repositories.
"""

from __future__ import annotations

import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
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
    path = root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding='utf-8')
    _git(root, 'add', filename)
    _git(root, 'commit', '-m', f'work on {branch}')
    _git(root, 'checkout', 'main')


class ShipBranchGitEnvTest(unittest.TestCase):
    """Pure ``_git_env`` behaviour — no repository fixture."""

    def test_git_env_clears_hook_overrides(self) -> None:
        """Inherited GIT_DIR must not pin throwaway-repo commands to the host."""
        poisoned = dict(os.environ)
        poisoned['GIT_DIR'] = '/nonexistent/git-dir'
        poisoned['GIT_WORK_TREE'] = '/nonexistent/work-tree'
        cleaned = sb._git_env(poisoned)
        self.assertNotIn('GIT_DIR', cleaned)
        self.assertNotIn('GIT_WORK_TREE', cleaned)


class ClassifyChangedPathsTest(unittest.TestCase):
    """Pure path classification — no repository fixture."""

    def test_classify_docs_only_allowlist(self) -> None:
        self.assertEqual(
            sb.classify_changed_paths(['tasks/index.json', 'docs/plans/x.md']),
            'tracker/docs-only',
        )
        self.assertEqual(
            sb.classify_changed_paths(['AGENTS.md', 'CODING_STANDARDS.md']),
            'tracker/docs-only',
        )
        self.assertEqual(
            sb.classify_changed_paths(['rules/process.md']),
            'tracker/docs-only',
        )
        self.assertEqual(
            sb.classify_changed_paths(['scripts/ship_branch.py']),
            'code',
        )
        self.assertEqual(
            sb.classify_changed_paths(['tasks/index.json', 'scripts/foo.py']),
            'code',
        )


class TipTestPlanPathPolicyTest(unittest.TestCase):
    """Tip-plan kinds decided from path rules alone — no tree fixture."""

    def test_plan_skip_docs_tracker_skill_only(self) -> None:
        # repo_root unused when every path is tip-test-skip.
        plan = sb.plan_tip_tests(
            [
                'docs/a.md',
                'tasks/index.json',
                '.claude/skills/x/SKILL.md',
                'rules/process.md',
            ],
            Path('/nonexistent-repo-root'),
        )
        self.assertEqual(plan.kind, 'skip')
        self.assertEqual(plan.modules, ())

    def test_plan_full_when_runner_or_hooks_touched(self) -> None:
        for path in (
            'scripts/run_tests.py',
            'scripts/pre_commit_checks.py',
            'scripts/ship_branch.py',
            'scripts/install_git_hooks.py',
            '.githooks/pre-commit',
        ):
            plan = sb.plan_tip_tests([path], Path('/nonexistent-repo-root'))
            self.assertEqual(plan.kind, 'full', path)


class ShipBranchMergeTest(unittest.TestCase):
    """Merge / worktree / lock integration against a real temporary repository."""

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

        code = sb.merge_branch(
            self.root, self.branch, run_tests=runner, push=False
        )
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

        code = sb.merge_branch(
            self.root, self.branch, run_tests=runner, push=False
        )
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

        code = sb.merge_branch(
            self.root, self.branch, run_tests=runner, push=False
        )
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
        code = sb.merge_branch(
            self.root, 'task/does-not-exist', run_tests=lambda _r: 0, push=False
        )
        self.assertEqual(code, 1)
        self.assertEqual(sb.current_branch(self.root), 'main')

    def test_cli_merge_missing_branch(self) -> None:
        code = sb.main(
            [
                'merge',
                'task/does-not-exist',
                '--repo-root',
                str(self.root),
                '--no-push',
            ],
        )
        self.assertEqual(code, 1)
        self.assertEqual(sb.current_branch(self.root), 'main')

    def test_busy_full_suite_lock_refuses_merge(self) -> None:
        import run_tests

        main_before = _git(self.root, 'rev-parse', 'HEAD')
        with run_tests.full_suite_lock(self.root):
            code = sb.merge_branch(
                self.root, self.branch, run_tests=lambda _r: 0, push=False
            )
        self.assertEqual(code, 2)
        self.assertEqual(_git(self.root, 'rev-parse', 'HEAD'), main_before)
        self.assertIn(self.branch, self._branches())

    def test_green_merge_without_remote_fails_push(self) -> None:
        """Default push=True with no origin leaves the merge but exits 1."""
        code = sb.merge_branch(
            self.root, self.branch, run_tests=lambda _r: 0
        )
        self.assertEqual(code, 1)
        self.assertEqual(sb.current_branch(self.root), 'main')
        self.assertIn(self.branch, self._head_subject())
        self.assertNotIn(self.branch, self._branches())
        self.assertIsNone(sb.resolve_push_target(self.root, 'main'))

    def test_green_merge_pushes_to_origin(self) -> None:
        bare = Path(self._tmp.name) / 'remote.git'
        _git(self.root, 'clone', '--bare', str(self.root), str(bare))
        _git(self.root, 'remote', 'add', 'origin', str(bare))
        _git(self.root, 'push', '-u', 'origin', 'main')
        remote_before = _git(bare, 'rev-parse', 'main')

        code = sb.merge_branch(
            self.root, self.branch, run_tests=lambda _r: 0
        )
        self.assertEqual(code, 0)
        self.assertNotEqual(_git(bare, 'rev-parse', 'main'), remote_before)
        self.assertEqual(
            _git(self.root, 'rev-parse', 'main'),
            _git(bare, 'rev-parse', 'main'),
        )
        self.assertEqual(
            sb.resolve_push_target(self.root, 'main'),
            ('origin', 'main'),
        )


class TipTestPlanMappingTest(unittest.TestCase):
    """Map changed paths to --only modules; needs a scripts/ tree, not Git."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        scripts = self.root / 'scripts'
        scripts.mkdir()
        (scripts / 'test_foo.py').write_text(
            'import unittest\n', encoding='utf-8'
        )
        (scripts / 'foo.py').write_text('x = 1\n', encoding='utf-8')
        (scripts / 'bar.py').write_text('x = 2\n', encoding='utf-8')
        # bar has no test_bar.py
        shims = self.root / 'shims'
        shims.mkdir()
        (shims / 'foo.py').write_text('shim\n', encoding='utf-8')

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_map_test_module_and_impl_and_shim(self) -> None:
        modules = sb.map_changed_paths_to_modules(
            [
                'scripts/test_foo.py',
                'scripts/foo.py',
                'shims/foo.py',
                'scripts/bar.py',
                'docs/plan.md',
                'tasks/index.json',
                'rules/process.md',
                '.claude/skills/ship-branch/SKILL.md',
                'providers/x/mod.py',
            ],
            self.root,
        )
        self.assertEqual(modules, ['test_foo'])

    def test_plan_only_when_mapping_nonempty(self) -> None:
        plan = sb.plan_tip_tests(['scripts/foo.py'], self.root)
        self.assertEqual(plan.kind, 'only')
        self.assertEqual(plan.modules, ('test_foo',))

    def test_plan_full_when_code_but_no_mapped_tests(self) -> None:
        plan = sb.plan_tip_tests(['scripts/bar.py'], self.root)
        self.assertEqual(plan.kind, 'full')


class ShipBranchSweepTest(unittest.TestCase):
    """Sweep / stray-branch integration against a real temporary repository."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / 'repo'
        self.root.mkdir()
        _init_repo(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _branches(self) -> set[str]:
        out = _git(self.root, 'branch', '--format=%(refname:short)')
        return {line for line in out.splitlines() if line}

    def test_detects_unmerged_and_labels_code_vs_docs(self) -> None:
        _add_branch_commit(
            self.root, 'task/docs-stray', 'tasks/notes.txt', 'tracker\n'
        )
        _add_branch_commit(
            self.root, 'task/code-stray', 'scripts/tool.py', 'print(1)\n'
        )
        # Second docs commit so count > 1 is exercised.
        _git(self.root, 'checkout', 'task/docs-stray')
        (self.root / 'docs').mkdir(exist_ok=True)
        (self.root / 'docs' / 'extra.md').write_text('more\n', encoding='utf-8')
        _git(self.root, 'add', 'docs/extra.md')
        _git(self.root, 'commit', '-m', 'more docs')
        _git(self.root, 'checkout', 'main')

        strays = {s.name: s for s in sb.collect_stray_branches(self.root)}
        self.assertIn('task/docs-stray', strays)
        self.assertIn('task/code-stray', strays)
        self.assertEqual(strays['task/docs-stray'].kind, 'tracker/docs-only')
        self.assertEqual(strays['task/docs-stray'].commits, 2)
        self.assertEqual(strays['task/code-stray'].kind, 'code')
        self.assertEqual(strays['task/code-stray'].commits, 1)

    def test_reports_leftover_worktree(self) -> None:
        branch = 'task/wt-stray'
        _add_branch_commit(self.root, branch, 'feature.txt', 'x\n')
        wt = Path(self._tmp.name) / 'wt-stray'
        _git(self.root, 'worktree', 'add', str(wt), branch)

        leftovers = sb.list_leftover_worktrees(self.root)
        self.assertEqual(len(leftovers), 1)
        self.assertEqual(leftovers[0].path.resolve(), wt.resolve())
        self.assertEqual(leftovers[0].branch, branch)

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = sb.sweep(
                self.root,
                prompt=lambda _q: False,
                run_tests=lambda _r: 0,
                push=False,
            )
        self.assertEqual(code, 0)
        out = buf.getvalue()
        self.assertIn(str(wt.resolve()), out)
        self.assertIn(branch, out)

    def test_merge_docs_only_flag_merges_docs_not_code(self) -> None:
        _add_branch_commit(
            self.root, 'task/docs-only', 'docs/plan.md', 'plan\n'
        )
        _add_branch_commit(
            self.root, 'task/code-only', 'scripts/tool.py', 'print(1)\n'
        )
        main_before = _git(self.root, 'rev-parse', 'main')

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = sb.sweep(
                self.root,
                merge_docs_only=True,
                run_tests=lambda _r: 0,
                push=False,
            )
        self.assertEqual(code, 0)
        out = buf.getvalue()
        self.assertIn('task/docs-only', out)
        self.assertIn('task/code-only', out)
        self.assertIn('code strays', out.lower())

        branches = self._branches()
        self.assertNotIn('task/docs-only', branches)
        self.assertIn('task/code-only', branches)
        self.assertNotEqual(_git(self.root, 'rev-parse', 'main'), main_before)
        # Docs content landed; code file did not.
        self.assertTrue((self.root / 'docs' / 'plan.md').is_file())
        self.assertFalse((self.root / 'scripts' / 'tool.py').exists())

    def test_cli_merge_docs_only(self) -> None:
        _add_branch_commit(
            self.root, 'task/cli-docs', 'AGENTS.md', 'agents\n'
        )
        _add_branch_commit(
            self.root, 'task/cli-code', 'providers/x.py', 'x\n'
        )

        # CLI path has no injected runner; stub run_unit_tests via attribute.
        original = sb.run_unit_tests

        def _stub_tests(_repo: Path, **_kwargs: object) -> int:
            return 0

        sb.run_unit_tests = _stub_tests  # type: ignore[assignment]
        try:
            code = sb.main(
                [
                    'sweep',
                    '--repo-root',
                    str(self.root),
                    '--merge-docs-only',
                    '--no-push',
                ],
            )
        finally:
            sb.run_unit_tests = original  # type: ignore[assignment]

        self.assertEqual(code, 0)
        branches = self._branches()
        self.assertNotIn('task/cli-docs', branches)
        self.assertIn('task/cli-code', branches)

    def test_code_stray_not_merged_without_flag_or_prompt_yes(self) -> None:
        _add_branch_commit(
            self.root, 'task/code-stay', 'scripts/tool.py', 'print(1)\n'
        )
        main_before = _git(self.root, 'rev-parse', 'main')
        code = sb.sweep(
            self.root,
            merge_docs_only=True,
            run_tests=lambda _r: 0,
            push=False,
        )
        self.assertEqual(code, 0)
        self.assertEqual(_git(self.root, 'rev-parse', 'main'), main_before)
        self.assertIn('task/code-stay', self._branches())


if __name__ == '__main__':
    unittest.main()
