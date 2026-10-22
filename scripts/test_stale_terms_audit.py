#!/usr/bin/env python3
"""Tests for scripts/stale_terms_audit.py (task #47)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import stale_terms_audit as S


class ScanTest(unittest.TestCase):
    def test_finds_banned_term_in_rules(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'rules').mkdir()
            (root / 'rules' / 'bad.md').write_text('use QUEUE.txt for progress\n')
            hits = S.scan(root)
        self.assertTrue(any(h.term == 'QUEUE.txt' and h.path.endswith('rules/bad.md') for h in hits))

    def test_allows_adr_and_audit_history(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for rel in ('docs/adr', 'docs/audits', 'docs/plans'):
                p = root / rel
                p.mkdir(parents=True)
                (p / 'old.md').write_text('QUEUE.txt and restore-ghost and STAGES.json\n')
            hits = S.scan(root)
        self.assertEqual(hits, [])

    def test_signoff_is_banned_in_skills(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            skill = root / '.claude' / 'skills' / 'x'
            skill.mkdir(parents=True)
            (skill / 'SKILL.md').write_text('then run coverage signoff\n')
            hits = S.scan(root)
        self.assertTrue(any(h.term == 'signoff' for h in hits))


class ToolRepoCleanTest(unittest.TestCase):
    def test_tool_repo_agent_docs_are_clean(self):
        """Living agent-facing surfaces must not reintroduce banned terms."""
        hits = S.scan(S.tool_root())
        self.assertEqual(
            hits, [],
            msg='\n'.join(f'{h.path}:{h.line}: {h.term!r}' for h in hits),
        )


if __name__ == '__main__':
    unittest.main()
