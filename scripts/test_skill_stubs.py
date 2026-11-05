"""Checks that every canonical skill in .agents/skills has a matching Claude stub."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK_SCRIPT = REPO_ROOT / 'scripts' / 'check-skill-stubs.mjs'


@unittest.skipUnless(shutil.which('node'), 'node is needed to run check-skill-stubs.mjs')
class SkillStubsTest(unittest.TestCase):
    def test_every_canonical_skill_has_a_matching_stub(self) -> None:
        result = subprocess.run(
            ['node', str(CHECK_SCRIPT), '--root', str(REPO_ROOT)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f'{result.stderr}\nFix with: node scripts/check-skill-stubs.mjs --write',
        )
        self.assertRegex(result.stdout, r'^(\d+) of \1 canonical skills')
        self.assertNotRegex(result.stdout, r'^0 of 0')


if __name__ == '__main__':
    unittest.main()
