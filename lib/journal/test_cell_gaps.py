"""Shared completion gaps use synthetic journal state and evidence doubles."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cell_gaps as gaps
from cell_journal import Build, Note
from journal_testkit import JournalWorld


CELL = (1, 1)
EMPTY_FOOTPRINT = {'built_area': 0, 'unbuilt_area': 0, 'unbuilt': [], 'unbuilt_names': []}


class CellGapsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.world = JournalWorld(Path(self.tmp.name))
        self.world.__enter__()
        self.addCleanup(self.world.__exit__)
        for patch in (
            mock.patch('cell_features.footprint_progress', return_value=EMPTY_FOOTPRINT.copy()),
            mock.patch('pass_mask.trees_rule_current', return_value=True),
            mock.patch.object(gaps, 'lidar_lumps', return_value=[]),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def seed(self):
        self.world.seed(CELL, ground=True, trees=True, decor=True)

    def test_finished_layers_and_footprint_have_no_gaps(self):
        self.seed()
        result = gaps.cell_gaps(CELL)
        self.assertEqual(result['layers'], [])
        self.assertEqual(result['features'], [])
        self.assertEqual(result['in_flight'], [])
        self.assertEqual(result['unbuilt_footprint'], [])
        self.assertEqual(result['trees_over_roof'], [])
        self.assertFalse(result['trees_stale'])

    def test_ghost_ground_requires_ground_before_later_layers(self):
        self.world.seed(CELL, ghost_ground=True)
        result = gaps.cell_gaps(CELL, features=False)
        self.assertEqual(result['layers'], ['ground'])

    def test_missing_layers_can_be_checked_without_feature_evidence(self):
        self.world.seed(CELL, ground=True)
        with mock.patch('cell_features.footprint_progress', side_effect=AssertionError('no evidence scan')), \
                mock.patch.object(gaps, 'lidar_lumps', side_effect=AssertionError('no lidar scan')):
            result = gaps.cell_gaps(CELL, features=False)
        self.assertEqual(result['layers'], ['trees', 'decor'])

    def test_outdated_trees_mask_is_reported(self):
        self.seed()
        with mock.patch('pass_mask.trees_rule_current', return_value=False):
            self.assertTrue(gaps.cell_gaps(CELL, features=False)['trees_stale'])

    def test_unfinished_apply_is_listed_from_journal(self):
        self.world.seed(CELL, ground=True, trees=True)
        self.world.reg.begin(Build('interrupted_decor', 'decor', {(140, 61, 140): 'minecraft:poppy'}, cell=CELL))
        result = gaps.cell_gaps(CELL, features=False)
        self.assertEqual(result['in_flight'], ['in-flight: interrupted_decor'])
        self.assertEqual(result['layers'], ['decor'])

    def test_unbuilt_footprints_keep_their_names_and_evidence(self):
        self.seed()
        row = {'kind': 'houses', 'label': 'Example row', 'refs': ['w1']}
        footprint = {'built_area': 4, 'unbuilt_area': 8, 'unbuilt': [row], 'unbuilt_names': ['Example row']}
        with mock.patch('cell_features.footprint_progress', return_value=footprint):
            result = gaps.cell_gaps(CELL)
        self.assertEqual(result['features'], [row])
        self.assertEqual(result['unbuilt_footprint'], ['Example row'])
        self.assertEqual(result['footprint'], footprint)

    def test_failed_footprint_measurement_falls_back_to_missing_features(self):
        self.seed()
        row = {'kind': 'building', 'label': 'Example'}
        with mock.patch('cell_features.footprint_progress', side_effect=RuntimeError('unavailable')), \
                mock.patch('cell_features.missing_features', return_value=[row]) as missing, \
                mock.patch('osm_footprints.OSMIndex', return_value=object()):
            self.assertEqual(gaps.cell_gaps(CELL)['features'], [row])
        missing.assert_called_once()

    def test_feature_status_and_hold_survive_the_completion_report(self):
        view = SimpleNamespace(
            ground='generated', trees=True, decor=True, in_flight=[], notes=[], done=False,
            hold='review needed', features=[SimpleNamespace(id='railway_1_1', status='proposed'),
                                          SimpleNamespace(id='house', status='built')],
        )
        with mock.patch.object(gaps, '_view', return_value=view):
            result = gaps.cell_gaps(CELL, features=False)
        self.assertEqual(result['features'], ['railway_1_1=proposed'])
        self.assertEqual(result['hold'], 'review needed')
        self.assertFalse(result['done'])

    def test_tree_over_roof_stays_open_until_covering_review_note(self):
        self.seed()
        lump = {'bbox': [10, 10, 20, 20], 'trees_over': 12}
        with mock.patch.object(gaps, 'lidar_lumps', return_value=[lump]):
            self.assertEqual(gaps.cell_gaps(CELL)['trees_over_roof'], [lump])
            self.world.reg.change(Note(CELL, 'review', 'crown kept', 'trees-over-roof:5,5,30,30'))
            self.assertEqual(gaps.cell_gaps(CELL)['trees_over_roof'], [])
        self.assertEqual(gaps.resolved_trees_over_roof(CELL), [{'bbox': [5, 5, 30, 30], 'reason': 'crown kept'}])

    def test_tree_review_does_not_hide_an_unrelated_lump(self):
        self.seed()
        self.world.reg.change(Note(CELL, 'review', 'crown kept', 'trees-over-roof:5,5,15,15'))
        lump = {'bbox': [10, 10, 20, 20], 'trees_over': 12}
        self.assertEqual(gaps.trees_over_roof_open(CELL, lumps=[lump]), [lump])
        self.assertEqual(gaps.trees_over_roof_open(CELL, lumps=[dict(lump, trees_over=0)]), [])


if __name__ == '__main__':
    unittest.main()
