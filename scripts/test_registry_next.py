#!/usr/bin/env python3
"""Tests for registry next + ISSUES (ticket #31 seams)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cell_registry as R  # noqa: E402
import cellmath  # noqa: E402
import registry_hooks as H  # noqa: E402
import registry_issues as I  # noqa: E402
import registry_next as N  # noqa: E402


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


class IssuesAddCloseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'site' / 'archive').mkdir(parents=True)
        self.store = R.RegistryStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_issue_reopens_trees(self):
        H.on_layer_clean((48, -44), 'ground', store=self.store)
        H.on_layer_clean((48, -44), 'trees', store=self.store)
        issue = I.add_issue(
            reason='sparse crowns', cell=(48, -44), layer='trees',
            root=self.root,
        )
        # reopen clears trees via RegistryStore under root
        store = R.RegistryStore(self.root)
        # add_issue uses RegistryStore(root) which writes to root/registry
        self.assertFalse(store.cell('48,-44')['trees'])
        self.assertEqual(issue.id, 1)
        open_ = I.load_issues(self.root)
        self.assertEqual(len(open_), 1)

    def test_auto_close_on_layer(self):
        I.add_issue(reason='x', cell=(48, -44), layer='trees', root=self.root, reopen=False)
        closed = I.auto_close_for_target(cell=(48, -44), layer='trees', root=self.root)
        self.assertEqual(len(closed), 1)
        self.assertEqual(I.load_issues(self.root), [])
        arch = I.archive_path(self.root).read_text()
        self.assertIn('I-001', arch)
        self.assertIn('closed', arch)

    def test_whole_cell_issue_reopens_built_features(self):
        H.on_layer_clean((48, -44), 'ground', store=self.store)
        self.store.register_feature('q001_x', name='X', cells=['48,-44'], status='built')
        I.add_issue(reason='redo cell', cell=(48, -44), root=self.root)
        self.assertEqual(self.store.feature('q001_x')['status'], 'in-progress')
        self.assertEqual(self.store.cell('48,-44')['ground'], 'none')


class NextTaskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'site' / 'archive').mkdir(parents=True)
        self.store = R.RegistryStore(self.root)
        cellmath.reload_grid({'cell': {'size': 128, 'origin': [48, -44]}})

    def tearDown(self):
        cellmath.reload_grid()
        self.tmp.cleanup()

    def test_open_issue_beats_registry_work(self):
        H.on_layer_clean((48, -44), 'ground', store=self.store)
        I.add_issue(reason='fix', cell=(48, -45), layer='ground', root=self.root, reopen=False)
        task = N.next_task(self.store, root=self.root)
        self.assertEqual(task['kind'], 'issue')
        self.assertEqual(task['issue_id'], 1)

    def test_marker_cell_preferred_when_not_done(self):
        H.on_marker_raise((49, -44), store=self.store)
        # neighbour also undone
        self.store.set_ground('48,-44', 'none')
        task = N.next_task(self.store, root=self.root)
        self.assertEqual(task['cell'], '49,-44')
        self.assertEqual(task['layer'], 'ground')

    def test_order_inside_cell_ground_features_trees_decor(self):
        cid = '48,-44'
        self.store.set_ground(cid, 'generated')
        self.store.register_feature('a', name='A', cells=[cid], status='planned')
        self.store.register_feature('b', name='B', cells=[cid], status='planned')
        task = N.next_task(self.store, root=self.root)
        self.assertEqual(task['kind'], 'feature')
        self.assertEqual(task['feature'], 'a')
        self.store.set_feature_status('a', 'built')
        self.store.set_feature_status('b', 'built')
        task = N.next_task(self.store, root=self.root)
        self.assertEqual(task['layer'], 'trees')
        H.on_layer_clean((48, -44), 'trees', store=self.store)
        task = N.next_task(self.store, root=self.root)
        self.assertEqual(task['layer'], 'decor')

    def test_held_cell_skipped(self):
        self.store.set_hold('48,-44', 'flood')
        self.store.set_ground('48,-45', 'none')
        task = N.next_task(self.store, root=self.root)
        self.assertEqual(task['cell'], '48,-45')

    def test_idle_when_empty(self):
        self.assertIsNone(N.next_task(self.store, root=self.root))


if __name__ == '__main__':
    unittest.main()
