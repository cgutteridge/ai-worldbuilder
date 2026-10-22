#!/usr/bin/env python3
"""Tests for registry next + ISSUES (ticket #31 seams)."""
from __future__ import annotations

import unittest

import cellmath
import registry_issues as I
import registry_next as N
from cell_journal import Build, Note


class IssuesParseTest(unittest.TestCase):
    def test_parse_cell_layer_and_feature(self):
        text = '\n'.join([
            '# comment',
            'I-014  cell 48,-44 trees  crown wrong',
            'I-015  feature q001_road  missing door',
            'I-016  cell 47,-45  redo whole cell',
        ])
        issues = I.parse_issues(text)
        self.assertEqual(len(issues), 3)
        self.assertEqual(issues[0].layer, 'trees')
        self.assertEqual(issues[0].cell, (48, -44))
        self.assertEqual(issues[1].feature_id, 'q001_road')
        self.assertIsNone(issues[2].layer)


class _Journal(unittest.TestCase):
    def setUp(self):
        from journal_testkit import JournalWorld
        self.jw = JournalWorld()
        self.jw.__enter__()
        self.addCleanup(self.jw.__exit__)
        self.root = self.jw.root
        (self.root / 'site' / 'archive').mkdir(parents=True)

    def view(self, cell=(48, -44)):
        return self.jw.reg.cell(cell)

    def in_progress(self, fid, cell=(48, -44)):
        """A feature whose apply read back with mismatches (in-progress, not in flight)."""
        x0, z0 = cell[0] * 128, cell[1] * 128
        self.jw.reg.change(Build(fid, 'feature', {(x0 + 9, 70, z0 + 9): 'minecraft:bricks'}), lambda w: None)


class IssuesAddCloseTest(_Journal):
    def test_add_issue_notes_the_cell_and_keeps_it_from_done(self):
        self.jw.seed((48, -44), ground=True, trees=True, decor=True)
        self.assertTrue(self.view().done)
        issue = I.add_issue(reason='sparse crowns', cell=(48, -44), layer='trees', root=self.root)
        self.assertEqual(issue.id, 1)
        self.assertEqual(len(I.load_issues(self.root)), 1)
        self.assertFalse(self.view().done)
        self.assertIn('issue I-001', self.view().left)

    def test_auto_close_on_layer_archives_and_journals_close(self):
        self.jw.seed((48, -44), ground=True, trees=True, decor=True)
        I.add_issue(reason='x', cell=(48, -44), layer='trees', root=self.root)
        closed = I.auto_close_for_target(cell=(48, -44), layer='trees', root=self.root)
        self.assertEqual(len(closed), 1)
        self.assertEqual(I.load_issues(self.root), [])
        arch = I.archive_path(self.root).read_text()
        self.assertIn('I-001', arch)
        self.assertIn('closed', arch)
        self.assertTrue(self.view().done)

    def test_feature_issue_is_noted_on_every_cell_of_the_feature(self):
        self.jw.build('q001_x', 'feature', {(48 * 128, 60, -44 * 128): 'minecraft:bricks',
                                            (49 * 128, 60, -44 * 128): 'minecraft:bricks'})
        I.add_issue(reason='roof', feature_id='q001_x', root=self.root)
        self.assertIn('issue I-001', self.view((48, -44)).left)
        self.assertIn('issue I-001', self.view((49, -44)).left)


class NextTaskTest(_Journal):
    def setUp(self):
        super().setUp()
        cellmath.reload_grid({'cell': {'size': 128, 'origin': [48, -44]}})
        self.addCleanup(cellmath.reload_grid)

    def test_open_issue_beats_registry_work(self):
        self.jw.seed((48, -44), ground=True)
        I.add_issue(reason='fix', cell=(48, -45), layer='ground', root=self.root, reopen=False)
        task = N.next_task(self.jw.reg, root=self.root)
        self.assertEqual(task['kind'], 'issue')
        self.assertEqual(task['issue_id'], 1)

    def test_marker_cell_preferred_when_not_done(self):
        self.jw.seed((49, -44), marker=True)
        self.jw.seed((48, -44), ghost_ground=True)
        task = N.next_task(self.jw.reg, root=self.root)
        self.assertEqual(task['cell'], '49,-44')
        self.assertEqual(task['layer'], 'ground')

    def test_order_inside_cell_ground_features_trees_decor(self):
        self.jw.seed((48, -44), ground=True)
        self.in_progress('a')
        self.in_progress('b')
        task = N.next_task(self.jw.reg, root=self.root)
        self.assertEqual((task['kind'], task['feature'], task['status']), ('feature', 'a', 'in-progress'))
        self.jw.seed((48, -44), features=('a', 'b'))
        task = N.next_task(self.jw.reg, root=self.root)
        self.assertEqual(task['layer'], 'trees')
        self.jw.seed((48, -44), trees=True)
        task = N.next_task(self.jw.reg, root=self.root)
        self.assertEqual(task['layer'], 'decor')

    def test_held_cell_skipped(self):
        self.jw.seed((48, -44), ghost_ground=True)
        self.jw.reg.change(Note((48, -44), 'hold', 'flood'))
        self.jw.seed((48, -45), ghost_ground=True)
        task = N.next_task(self.jw.reg, root=self.root)
        self.assertEqual(task['cell'], '48,-45')

    def test_idle_when_empty(self):
        self.assertIsNone(N.next_task(self.jw.reg, root=self.root))

if __name__ == '__main__':
    unittest.main()
