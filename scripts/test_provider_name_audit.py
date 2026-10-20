#!/usr/bin/env python3
"""Tests for provider_name_audit (no live catalogue paths required)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import provider_name_audit as pna


class LoadTokens(unittest.TestCase):
    """Catalogue ids, aliases, and legacy shim names become searchable tokens."""

    def test_load_tokens_from_fixture(self):
        cat = {
            'providers': [{'id': 'ukdefra-lidar'}, {'id': 'google-satellite'}],
            'aliases': {'google': 'google-satellite', 'ukdefra-dtm': 'ukdefra-lidar'},
        }
        tokens = pna.load_tokens(cat)
        self.assertIn('ukdefra-lidar', tokens)
        self.assertIn('ukdefra_lidar', tokens)
        self.assertIn('google', tokens)
        self.assertIn('defra_lidar', tokens)


class ScanFile(unittest.TestCase):
    """Violations are reported with path and line number."""

    def test_finds_provider_id_outside_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rules = root / 'rules'
            rules.mkdir()
            path = rules / 'process.md'
            path.write_text('Always run ukdefra-lidar fetch first.\n')
            allow = pna.default_allow_prefixes(root)
            hits = pna.scan_tree(root, tokens={'ukdefra-lidar'}, allow_prefixes=allow,
                                 scan_roots=[('rules', ('.md',))])
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]['path'], 'rules/process.md')
            self.assertEqual(hits[0]['line'], 1)

    def test_skips_shims_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shims = root / 'shims'
            shims.mkdir()
            (shims / 'defra_lidar.py').write_text('ukdefra-lidar\n')
            allow = pna.default_allow_prefixes(root)
            hits = pna.scan_tree(root, tokens={'ukdefra-lidar'}, allow_prefixes=allow,
                                 scan_roots=[('shims', ('.py',))])
            self.assertEqual(hits, [])


class Baseline(unittest.TestCase):
    """Baseline compare catches new violations only."""

    def test_new_violation_not_in_baseline(self):
        hit = {'path': 'rules/x.md', 'line': 2, 'token': 'defra', 'text': 'use DEFRA'}
        baseline = {pna.violation_key({'path': 'rules/x.md', 'line': 1, 'token': 'other'})}
        new = pna.violations_not_in_baseline([hit], baseline)
        self.assertEqual(new, [hit])

    def test_known_violation_suppressed_same_line_different_token(self):
        hit = {'path': 'rules/x.md', 'line': 2, 'token': 'kartaview', 'text': 'x'}
        baseline = {pna.violation_key({'path': 'rules/x.md', 'line': 2, 'token': 'rightmove'})}
        self.assertEqual(pna.violations_not_in_baseline([hit], baseline), [])


class BaselineFile(unittest.TestCase):
    """Repo baseline JSON loads and matches current scan shape."""

    def test_tool_repo_baseline_covers_scan(self):
        root = pna.tool_root()
        baseline_path = root / 'config' / 'provider_name_audit_baseline.json'
        if not baseline_path.is_file():
            self.skipTest('baseline not generated yet')
        catalogue = pna.load_catalogue(root)
        tokens = pna.load_tokens(catalogue)
        allow = pna.default_allow_prefixes(root)
        hits = pna.scan_repo(root, tokens=tokens, allow_prefixes=allow)
        baseline = pna.load_baseline(baseline_path)
        new = pna.violations_not_in_baseline(hits, baseline)
        self.assertEqual(
            new,
            [],
            'new provider-name violations (fix wording or update baseline via '
            'check_provider_names.py --update-baseline):\n'
            + '\n'.join(f"  {h['path']}:{h['line']} {h['token']!r}" for h in new[:20]),
        )


if __name__ == '__main__':
    unittest.main()
