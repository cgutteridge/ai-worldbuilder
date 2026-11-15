#!/usr/bin/env python3
"""Tests for registry next (ticket #31 / #121 / #140 seams)."""
from __future__ import annotations

import unittest
from unittest import mock

import cellmath
import registry_next as N
from cell_journal import Build, Note
from cell_readiness import ReadinessPolicy
from journal_testkit import RegistryEvidence


class NextTaskTest(unittest.TestCase):
    def setUp(self):
        from journal_testkit import JournalWorld
        self.jw = JournalWorld()
        self.jw.__enter__()
        self.addCleanup(self.jw.__exit__)
        self.root = self.jw.root
        (self.root / 'site' / 'archive').mkdir(parents=True)
        cellmath.reload_grid({'cell': {'size': 128, 'home': [48, -44]}})
        self.addCleanup(cellmath.reload_grid)
        clip = mock.patch('cell_features.clip_coverage', return_value=1.0)
        clip.start()
        self.addCleanup(clip.stop)
        self.policy = ReadinessPolicy(self.jw.reg, RegistryEvidence())
        patch = mock.patch('readiness_evidence.policy_for', lambda reg: ReadinessPolicy(reg, self.policy.evidence))
        patch.start()
        self.addCleanup(patch.stop)
        candidates = mock.patch('cell_registry.propose_feature_candidates', return_value=[])
        candidates.start()
        self.addCleanup(candidates.stop)

    def ready(self, cell=(48, -44)):
        self.policy.validate(cell)
        self.jw.reg.build_policy = self.policy.enforce


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
        self.ready()
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
        self.assertEqual(task['kind'], 'needs-prep')
        self.assertIn('terrain: focus ghost baseline required', task['reasons'])

    def test_fresh_cell_builds_features_before_ground_once(self):
        """Task 286: features first on the ghost ground, then ground once (not stale), trees, decor."""
        self.jw.seed((48, -44), ghost_ground=True)
        self.jw.reg.change(Note((48, -44), 'plan_feature', 'b', 'b'))
        self.jw.reg.change(Note((48, -44), 'plan_feature', 'a', 'a'))
        self.ready()
        task = N.next_task(self.jw.reg, root=self.root)
        self.assertEqual((task['kind'], task['feature']), ('feature', 'a'))      # by id
        self.jw.seed((48, -44), features=('a',))
        task = N.next_task(self.jw.reg, root=self.root)
        self.assertEqual((task['kind'], task['feature']), ('feature', 'b'))
        self.jw.seed((48, -44), features=('b',))
        task = N.next_task(self.jw.reg, root=self.root)
        self.assertEqual((task['kind'], task['layer']), ('layer', 'ground'))
        self.jw.seed((48, -44), ground=True)
        self.assertEqual(self.policy.check((48, -44))['ground_stale'], [])
        task = N.next_task(self.jw.reg, root=self.root)
        self.assertEqual((task['kind'], task['layer']), ('layer', 'trees'))

    def test_cell_without_features_gets_ground_first(self):
        self.jw.seed((48, -44), ghost_ground=True)
        self.ready()
        task = N.next_task(self.jw.reg, root=self.root)
        self.assertEqual((task['kind'], task['layer']), ('layer', 'ground'))

    def test_order_inside_cell_ground_features_trees_decor(self):
        self.jw.seed((48, -44), ground=True)
        self.jw.reg.change(Note((48, -44), 'plan_feature', 'a', 'a'))
        self.jw.reg.change(Note((48, -44), 'plan_feature', 'b', 'b'))
        self.ready()
        task = N.next_task(self.jw.reg, root=self.root)
        self.assertEqual((task['kind'], task['feature'], task['status']), ('feature', 'a', 'planned'))
        self.jw.seed((48, -44), features=('a', 'b'))
        task = N.next_task(self.jw.reg, root=self.root)
        self.assertEqual(task['kind'], 'ground-redo')          # features landed after the ground (task 285)
        self.jw.seed((48, -44), ground=True)
        task = N.next_task(self.jw.reg, root=self.root)
        self.assertEqual(task['layer'], 'trees')
        self.jw.seed((48, -44), trees=True)
        task = N.next_task(self.jw.reg, root=self.root)
        self.assertEqual(task['layer'], 'decor')

    def test_stale_ground_redo_comes_after_unbuilt_features_before_trees(self):
        """Task 285: one ground redo after the cell's features, offered with its cause and command."""
        self.jw.seed((48, -44), ground=True)
        self.jw.reg.change(Note((48, -44), 'plan_feature', 'a', 'a'))
        self.jw.reg.change(Note((48, -44), 'plan_feature', 'b', 'b'))
        self.ready()
        self.jw.seed((48, -44), features=('a',))
        task = N.next_task(self.jw.reg, root=self.root)
        self.assertEqual((task['kind'], task['feature']), ('feature', 'b'))   # finish features first
        self.jw.seed((48, -44), features=('b',))
        task = N.next_task(self.jw.reg, root=self.root)
        self.assertEqual((task['kind'], task['cell'], task['layer']), ('ground-redo', '48,-44', 'ground'))
        self.assertEqual(len(task['stale']), 2)
        self.assertRegex(task['stale'][0], r'^ground \S+ is older than feature a landed \S+$')
        self.assertIn('redo_layer.py --world <world> --cell 48,-44 --layers ground --apply', task['command'])
        text = N.format_next(task)
        self.assertIn('next: ground-redo in cell 48,-44 (offered, not started)', text)
        self.assertIn('  ground stale: ground ', text)
        self.assertIn('  clear: ground stale -> python3 scripts/redo_layer.py', text)

    def test_needs_prep_cell_still_says_its_ground_is_stale(self):
        self.jw.seed((48, -44), ground=True)
        self.jw.seed((48, -44), features=('a',))
        task = N.next_task(self.jw.reg, root=self.root)          # not validated: needs-prep
        self.assertEqual(task['kind'], 'needs-prep')
        self.assertIn('feature a landed', task['stale'][0])
        self.assertIn('clear: ground stale -> ', N.format_next(task))

    def test_next_for_one_cell(self):
        self.jw.seed((48, -44), ground=True)
        self.jw.seed((49, -44), ground=True, marker=True)
        self.ready()
        self.ready((49, -44))
        self.assertEqual(N.next_task(self.jw.reg, root=self.root)['cell'], '49,-44')
        task = N.next_task(self.jw.reg, root=self.root, cell=(48, -44))
        self.assertEqual((task['cell'], task['layer']), ('48,-44', 'trees'))
        self.jw.seed((48, -44), trees=True, decor=True)
        task = N.next_task(self.jw.reg, root=self.root, cell=(48, -44))
        self.assertEqual(task, {'kind': 'complete', 'cell': '48,-44'})
        self.assertIn('complete', N.format_next(task))

    def test_held_cell_skipped(self):
        self.jw.seed((48, -44), ghost_ground=True)
        self.jw.reg.change(Note((48, -44), 'hold', 'flood'))
        self.jw.seed((48, -45), ghost_ground=True)
        self.ready((48, -45))
        task = N.next_task(self.jw.reg, root=self.root)
        self.assertEqual(task['cell'], '48,-45')

    def test_idle_when_empty(self):
        self.assertIsNone(N.next_task(self.jw.reg, root=self.root))

    def test_partial_clip_blocks_trees_and_can_be_dismissed(self):
        self.jw.seed((48, -44), ground=True)
        with mock.patch('cell_features.clip_coverage', return_value=0.125), \
                mock.patch('cell_registry.propose_feature_candidates', return_value=[]):
            task = N.next_task(self.jw.reg, root=self.root)
            self.assertEqual(task['kind'], 'needs-prep')
            self.assertEqual(task['clip_coverage'], 0.125)
            self.assertIn('12.5%', N.format_next(task))
            self.jw.reg.change(Note((48, -44), 'clip_coverage-dismiss', 'checked against aerial'))
            self.ready()
            self.assertEqual(N.next_task(self.jw.reg, root=self.root)['layer'], 'trees')

    def test_partial_clip_surfaces_on_otherwise_complete_cell(self):
        self.jw.seed((48, -44), ground=True, trees=True, decor=True)
        with mock.patch('cell_features.clip_coverage', return_value=0), \
                mock.patch('cell_registry.propose_feature_candidates', return_value=[]):
            self.assertEqual(N.next_task(self.jw.reg, root=self.root)['kind'], 'needs-prep')

    def test_unplanned_footprint_blocks_trees_after_ground(self):
        """#140: ground done + one footprint and no plan_feature → needs-feature-plan, not trees."""
        self.jw.seed((48, -44), ground=True)
        footprint = [{
            'kind': 'building', 'label': 'fixture hall', 'street': None,
            'refs': ['w140fixture'], 'bbox': (6160, -5600, 6180, -5580),
            'portals': [], 'pictures': [],
        }]
        with mock.patch('cell_registry.propose_feature_candidates', return_value=footprint):
            task = N.next_task(self.jw.reg, root=self.root)
        self.assertEqual(task['kind'], 'needs-prep')
        self.assertEqual(task['cell'], '48,-44')
        self.assertIn('validation required', task['reasons'])
        with mock.patch('cell_registry.propose_feature_candidates', return_value=footprint):
            self.assertEqual(self.policy.check((48, -44))['candidates'][0]['id'], 'w140fixture')
        self.assertNotEqual(task.get('layer'), 'trees')
        self.assertIn('needs-prep', N.format_next(task))

    def test_planned_or_dismissed_footprint_allows_trees(self):
        """#140: a plan_feature or dismiss covering the candidate must not block trees."""
        self.jw.seed((48, -44), ground=True)
        footprint = [{
            'kind': 'building', 'label': 'fixture hall', 'street': None,
            'refs': ['w140fixture'], 'bbox': (6160, -5600, 6180, -5580),
            'portals': [], 'pictures': [],
        }]
        self.jw.reg.change(Note((48, -44), 'plan_feature', 'fixture hall', ref='w140fixture'))
        # Still proposed by the map, but already on the plan → next offers the feature, not trees.
        with mock.patch('cell_registry.propose_feature_candidates', return_value=footprint):
            self.ready()
            task = N.next_task(self.jw.reg, root=self.root)
        self.assertEqual((task['kind'], task['feature']), ('feature', 'w140fixture'))

        self.jw.reg.change(Note((48, -44), 'plan_feature-dismiss', 'car park', ref='w140fixture'))
        with mock.patch('cell_registry.propose_feature_candidates', return_value=footprint):
            self.ready()
            task = N.next_task(self.jw.reg, root=self.root)
        self.assertEqual(task['layer'], 'trees')


if __name__ == '__main__':
    unittest.main()
