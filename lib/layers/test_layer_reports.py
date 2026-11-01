"""Observable reports retain full reasons and account for every plan position."""
import json
import tempfile
import unittest
from pathlib import Path
from layer_reports import write_report, apply_counts


class TestLayerReports(unittest.TestCase):
    def test_unchanged_positions_are_explicit_and_missing_writes_fail(self):
        plan = {(0, 60, 0): 'minecraft:stone', (1, 60, 0): 'minecraft:dirt'}
        counts = apply_counts(plan, plan, {(0, 60, 0): 'minecraft:stone'}, plan)
        self.assertEqual(counts['already_correct'], 1)
        self.assertEqual(counts['journal_write_positions'], 2)
        self.assertEqual(counts['verified_positions'], 2)
        with self.assertRaisesRegex(ValueError, 'journal writes'):
            apply_counts(plan, {(0, 60, 0): 'minecraft:stone'}, {}, plan)

    def test_full_details_survive_console_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_report('decor', {'unplaced': [{'reason': 'above lidar ground'}] * 30}, Path(tmp))
            self.assertEqual(len(json.loads(path.read_text())['unplaced']), 30)

    def test_unknown_prewrite_is_not_reported_as_already_correct_air(self):
        plan = {(0, 60, 0): 'minecraft:air'}
        counts = apply_counts(plan, plan, {(0, 60, 0): None}, plan)
        self.assertEqual(counts['already_correct'], 0)
        self.assertEqual(counts['unknown_before_positions'], 1)
        self.assertEqual(counts['positions_requiring_change'], 0)
