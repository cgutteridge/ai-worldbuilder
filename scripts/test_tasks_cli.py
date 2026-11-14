"""Run the task CLI's synthetic Node tests through the repository test runner."""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path


class TasksCliTest(unittest.TestCase):
    def test_tasks_cli(self) -> None:
        """Exercise task reads, writes and recovery in disposable trackers."""
        result = subprocess.run(
            ['node', '--test', str(Path(__file__).with_name('tasks.test.mjs'))],
            capture_output=True,
            text=True,
            env={key: value for key, value in os.environ.items()
                 if not key.startswith('GIT_')},
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
