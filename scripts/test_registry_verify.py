#!/usr/bin/env python3
"""Tests for registry verify (ticket #32) — file/registry seams only."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import cell_registry as CR
import registry_hooks as H
import registry_verify as V


class VerifyLeftoversTest(unittest.TestCase):
    def test_reports_legacy_progress_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            site = root / 'site'
            site.mkdir()
            (site / 'QUEUE.txt').write_text('x\n')
            (site / 'CURRENT-CELL.json').write_text('{}\n')
            (site / 'COVERAGE-SIGNOFFS.jsonl').write_text('\n')
            (site / 'cells').mkdir()
            (site / 'cells' / '48_-44-STAGES.json').write_text('{}\n')
            issues = V.check_leftover_progress_files(root)
        ids = {i['id'] for i in issues}
        self.assertIn('leftover:QUEUE.txt', ids)
        self.assertIn('leftover:CURRENT-CELL.json', ids)
        self.assertIn('leftover:COVERAGE-SIGNOFFS.jsonl', ids)
        self.assertIn('leftover:STAGES', ids)


class VerifySpecsTest(unittest.TestCase):
    def test_built_feature_missing_spec_is_error(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            store = CR.RegistryStore(root)
            H.on_layer_clean((48, -44), 'ground', store=store)
            store.register_feature('q001_x', name='X', cells=['48,-44'], status='built')
            (root / 'site' / 'builds').mkdir(parents=True)
            issues = V.check_feature_specs(store, root)
        self.assertTrue(any(i['id'] == 'missing-spec:q001_x' for i in issues))

    def test_spec_present_is_clean(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            store = CR.RegistryStore(root)
            store.register_feature('q001_x', name='X', cells=['48,-44'], status='built')
            builds = root / 'site' / 'builds'
            builds.mkdir(parents=True)
            (builds / 'q001_x.json').write_text('{}\n')
            issues = V.check_feature_specs(store, root)
        self.assertEqual(issues, [])


class VerifyMarkersTest(unittest.TestCase):
    def test_at_most_one_marker_up(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            store = CR.RegistryStore(root)
            store.set_marker('48,-44', 'up')
            # Force a second marker via raw write to simulate corruption
            cells = store._load_cells()
            cells['49,-44'] = dict(CR.DEFAULT_CELL)
            cells['49,-44']['marker'] = 'up'
            store._save(cells, store._load_features())
            issues = V.check_marker_invariant(store)
        self.assertTrue(any(i['id'] == 'marker:multiple' for i in issues))


class VerifyReportTest(unittest.TestCase):
    def test_verify_exit_nonzero_on_errors(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'site').mkdir()
            (root / 'site' / 'QUEUE.txt').write_text('x\n')
            CR.RegistryStore(root)  # empty registry ok
            report = V.run_verify(root=root, check_world=False)
        self.assertGreater(report['error_count'], 0)
        self.assertNotEqual(V.exit_code(report), 0)


if __name__ == '__main__':
    unittest.main()
