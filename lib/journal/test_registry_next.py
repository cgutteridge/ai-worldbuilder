#!/usr/bin/env python3
"""Tests for registry next (ticket #31 / #121 seams)."""
from __future__ import annotations

import unittest

import cellmath
import registry_next as N
from cell_journal import Build, Note


class NextTaskTest(unittest.TestCase):
    def setUp(self):
        from journal_testkit import JournalWorld
        self.jw = JournalWorld()
        self.jw.__enter__()
        self.addCleanup(self.jw.__exit__)
        self.root = self.jw.root
        (self.root / 'site' / 'archive').mkdir(parents=True)
        cellmath.reload_grid({'cell': {'size': 128, 'origin': [48, -44]}})
        self.addCleanup(cellmath.reload_grid)

    def in_progress(self, fid, cell=(48, -44)):
        """A feature whose apply read back with mismatches (in-progress, not in flight)."""
        x0, z0 = cell[0] * 128, cell[1] * 128
        self.jw.reg.change(
            Build(fid, 'feature', {(x0 + 9, 70, z0 + 9): 'minecraft:bricks'}),
            lambda w: None,
        )

    def test_issues_txt_does_not_steer_next(self):
        """#121: open I-nnn lines in site/ISSUES.txt must not beat registry work."""
        self.jw.seed((48, -44), ground=True)
        (self.root / 'site' / 'ISSUES.txt').write_text(
            'I-001  cell 48,-45 ground  leftover text file\n', encoding='utf-8',
        )
        task = N.next_task(self.jw.reg, root=self.root)
        self.assertIsNotNone(task)
        self.assertNotEqual(task.get('kind'), 'issue')
        self.assertEqual(task['cell'], '48,-44')
        self.assertEqual(task['layer'], 'trees')

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
