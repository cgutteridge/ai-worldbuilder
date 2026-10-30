#!/usr/bin/env python3
"""Tests for registry verify (ticket #32) over the cell journal."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cell_registry as CR
import registry_verify as V
from cell_journal import Build, Note
from journal_testkit import JournalWorld


_FOOTPRINT = {
    'id': 'w141hall',
    'kind': 'building',
    'label': 'Fixture Hall',
    'street': None,
    'refs': ['w141hall'],
    'bbox': (6160, -5600, 6180, -5580),
    'portals': [],
    'pictures': [],
}


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

    def test_incomplete_clip_warning_and_persistent_dismissal(self):
        self.jw.seed((48, -44), ground=True)
        with mock.patch('cell_features.clip_coverage', return_value=0.125):
            issues = V.check_clip_query_coverage(self.reg())
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0]['severity'], 'warn')
            self.assertEqual(issues[0]['clip_coverage'], 0.125)
            self.assertIn('12.5%', issues[0]['message'])
            with mock.patch.object(CR, 'activate_world_from_args'):
                self.assertEqual(CR.main(['--world', 'unit-test', '--root', str(self.root),
                                          'dismiss-clip-coverage', '48,-44',
                                          '--reason', 'surveyed aerial']), 0)
            self.assertEqual(V.check_clip_query_coverage(self.reg()), [])

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

    def test_verify_does_not_check_issue_notes(self):
        """#121: ISSUES.txt / journal issue-note drift is no longer a verify check."""
        self.assertFalse(hasattr(V, 'check_issue_notes'))
        report = V.run_verify(root=self.root, check_world=False)
        self.assertFalse(any(str(i.get('id', '')).startswith('issue-note:') for i in report['issues']))

    def test_verify_exit_nonzero_on_errors_and_clean_on_empty(self):
        self.assertEqual(V.exit_code(V.run_verify(root=self.root)), 0)
        (self.root / 'site').mkdir(exist_ok=True)
        (self.root / 'site' / 'QUEUE.txt').write_text('x\n')
        report = V.run_verify(root=self.root, check_world=False)
        self.assertGreater(report['error_count'], 0)
        self.assertNotEqual(V.exit_code(report), 0)

    def test_landed_ground_with_uncovered_footprint_is_error(self):
        """#141: landed ground whose skipped footprints lack a planned/landed feature → error."""
        self.jw.seed((48, -44), ground=True)
        with mock.patch(
            'registry_next.unplanned_footprint_candidates',
            return_value=[_FOOTPRINT],
        ):
            issues = V.check_uncovered_footprints(self.reg())
        self.assertEqual(len(issues), 1)
        issue = issues[0]
        self.assertEqual(issue['id'], 'uncovered-footprint:48,-44')
        self.assertEqual(issue['severity'], V.SEVERITY_ERROR)
        self.assertIn('Fixture Hall', issue['message'])
        self.assertIn('w141hall', issue['message'])
        self.assertEqual(issue.get('buildings'), ['Fixture Hall'])

        with mock.patch(
            'registry_next.unplanned_footprint_candidates',
            return_value=[_FOOTPRINT],
        ):
            report = V.run_verify(root=self.root, check_world=False)
        self.assertTrue(any(i['id'] == 'uncovered-footprint:48,-44' for i in report['issues']))
        self.assertGreater(report['error_count'], 0)
        self.assertNotEqual(V.exit_code(report), 0)

    def test_planned_or_landed_footprint_clears_uncovered_error(self):
        """#141: plan_feature / landed feature / dismiss means no uncovered-footprint error."""
        self.jw.seed((48, -44), ground=True)
        with mock.patch('registry_next.unplanned_footprint_candidates', return_value=[]):
            self.assertEqual(V.check_uncovered_footprints(self.reg()), [])

        # Sanity: the #140 helper itself treats a plan_feature as covering.
        footprint = [{
            'kind': 'building', 'label': 'Fixture Hall', 'street': None,
            'refs': ['w141hall'], 'bbox': (6160, -5600, 6180, -5580),
            'portals': [], 'pictures': [],
        }]
        self.jw.reg.change(Note((48, -44), 'plan_feature', 'Fixture Hall', ref='w141hall'))
        with mock.patch('cell_registry.propose_feature_candidates', return_value=footprint):
            self.assertEqual(V.check_uncovered_footprints(self.reg()), [])

    def test_no_ground_yet_skips_uncovered_footprint_check(self):
        """#141: only landed ground layers are checked — empty / ghost cells stay quiet."""
        self.jw.seed((48, -44), ghost_ground=True)
        with mock.patch(
            'registry_next.unplanned_footprint_candidates',
            return_value=[_FOOTPRINT],
        ) as propose:
            self.assertEqual(V.check_uncovered_footprints(self.reg()), [])
        propose.assert_not_called()


if __name__ == '__main__':
    unittest.main()
