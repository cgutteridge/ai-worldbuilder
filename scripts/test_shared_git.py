"""Shared git contracts, including hook environment and subprocess options."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shared_git import git_env, git_output, git_run


class SharedGitContracts(unittest.TestCase):
    def test_scrubbing_is_explicit_when_targeting_a_second_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = (Path(directory) / name for name in ('first', 'second'))
            for repo in (first, second):
                repo.mkdir()
                git_run(repo, 'init', '-q')
            poisoned = {'GIT_DIR': str(first / '.git'), 'GIT_WORK_TREE': str(first)}
            with mock.patch.dict(os.environ, poisoned):
                self.assertEqual(Path(git_output(second, 'rev-parse', '--show-toplevel')), first.resolve())
                self.assertEqual(Path(git_output(second, 'rev-parse', '--show-toplevel', scrub_hook_env=True)),
                                 second.resolve())
                self.assertEqual(os.environ['GIT_DIR'], poisoned['GIT_DIR'])

    def test_environment_scrub_copies_only_repository_overrides(self):
        overrides = ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
                     'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES')
        original = dict.fromkeys(overrides, 'sentinel') | {'PATH': '/bin', 'GIT_AUTHOR_NAME': 'kept'}
        self.assertEqual(git_env(original), {'PATH': '/bin', 'GIT_AUTHOR_NAME': 'kept'})
        self.assertIn('GIT_INDEX_FILE', original)

    def test_subprocess_flags_and_errors_are_preserved(self):
        result = subprocess.CompletedProcess(['git'], 7, None, None)
        with mock.patch('shared_git.subprocess.run', return_value=result) as run:
            self.assertIs(git_run(Path('/tmp'), 'status', check=False, capture_output=False), result)
            run.assert_called_once_with(['git', 'status'], cwd=Path('/tmp'), check=False,
                                        capture_output=False, text=True)
        error = subprocess.CalledProcessError(128, ['git'], output='details', stderr='failure')
        with mock.patch('shared_git.subprocess.run', side_effect=error):
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                git_output(Path('/tmp'), 'status')
        self.assertIs(raised.exception, error)
        with mock.patch('shared_git.subprocess.run', return_value=subprocess.CompletedProcess(['git'], 0, ' value\n', '')):
            self.assertEqual(git_output(Path('/tmp'), 'status'), 'value')
