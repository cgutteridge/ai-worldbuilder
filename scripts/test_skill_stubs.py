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

    def test_overnight_trees_budget_is_stated(self) -> None:
        """#346: overnight trees builds flag at 8 minutes, die at 12, and the run continues."""
        text = (REPO_ROOT / '.agents/skills/cell-orchestrator/SKILL.md').read_text(encoding='utf-8')
        self.assertIn('--budget-min 8', text)
        self.assertIn('--cell <tx>,<tz>', text)
        self.assertIn('exits 124', text)
        self.assertIn('Exit 124 is a killed build, not a failure.', text)
        self.assertIn('site_recover.py', text)
        self.assertIn('Do not retry it.', text)
        self.assertIn('does not count toward the two-consecutive-failures stop', text)
        self.assertIn('8 minutes or more', text)
        self.assertIn('over-budget', text)
        self.assertIn('killed', text)


if __name__ == '__main__':
    unittest.main()
