#!/usr/bin/env python3
"""Tests for registry verify (ticket #32) over the cell journal."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import cell_registry as CR
import registry_issues as RI
import registry_verify as V
from cell_journal import Build
from journal_testkit import JournalWorld


class VerifyLeftoversTest(unittest.TestCase):
    def test_reports_legacy_progress_files_and_retired_records(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            site = root / 'site'
            site.mkdir()
            (site / 'QUEUE.txt').write_text('x\n')
            (site / 'CURRENT-CELL.json').write_text('{}\n')
            (site / 'COVERAGE-SIGNOFFS.jsonl').write_text('\n')
            (site / 'cells').mkdir()
            (site / 'cells' / '48_-44-STAGES.json').write_text('{}\n')
            (root / 'registry').mkdir()
            (root / 'registry' / 'cells.json').write_text('{}\n')
            (root / 'verification' / 'site').mkdir(parents=True)
            (root / 'verification' / 'site' / 'house-cells.json').write_text('{}\n')
            issues = {i['id']: i for i in V.check_leftover_progress_files(root)}
        for iid in ('leftover:QUEUE.txt', 'leftover:CURRENT-CELL.json', 'leftover:COVERAGE-SIGNOFFS.jsonl'):
            self.assertEqual(issues[iid]['severity'], V.SEVERITY_ERROR)
        for iid in ('leftover:STAGES', 'leftover:registry/cells.json', 'leftover:cells-json'):
            self.assertEqual(issues[iid]['severity'], V.SEVERITY_WARN)
            self.assertIn('retired record', issues[iid]['message'])


class VerifyJournalTest(unittest.TestCase):
    def setUp(self):
        self.jw = JournalWorld()
        self.jw.__enter__()
        self.addCleanup(self.jw.__exit__)
        self.root = self.jw.root

    def reg(self):
        return CR.view_registry(self.root)

    def test_built_feature_missing_spec_is_error(self):
        self.jw.seed((48, -44), ground=True, features=('q001_x',))
        (self.root / 'site' / 'builds').mkdir(parents=True)
        issues = V.check_feature_specs(self.reg(), self.root)
        self.assertEqual([(i['id'], i['severity']) for i in issues], [('missing-spec:q001_x', V.SEVERITY_ERROR)])
        (self.root / 'site' / 'builds' / 'q001_x.json').write_text('{}\n')
        self.assertEqual(V.check_feature_specs(self.reg(), self.root), [])

    def test_in_flight_entries_are_reported(self):
        self.jw.reg.begin(Build('house', 'feature', {(1, 60, 1): 'minecraft:bricks'}))
        issues = V.check_in_flight(self.reg())
        self.assertEqual(len(issues), 1)
        self.assertIn('site_recover.py status', issues[0]['message'])

    def test_issue_note_without_issues_line_is_reported(self):
        self.jw.seed((48, -44), ground=True)
        RI.add_issue(reason='x', cell=(48, -44), root=self.root)
        self.assertEqual(V.check_issue_notes(self.reg(), self.root), [])
        RI.issues_path(self.root).write_text('')                          # closed by hand
        issues = V.check_issue_notes(self.reg(), self.root)
        self.assertIn('--kind close --ref I-001', issues[0]['message'])

    def test_verify_exit_nonzero_on_errors_and_clean_on_empty(self):
        self.assertEqual(V.exit_code(V.run_verify(root=self.root)), 0)
        (self.root / 'site').mkdir(exist_ok=True)
        (self.root / 'site' / 'QUEUE.txt').write_text('x\n')
        report = V.run_verify(root=self.root, check_world=False)
        self.assertGreater(report['error_count'], 0)
        self.assertNotEqual(V.exit_code(report), 0)


if __name__ == '__main__':
    unittest.main()
