"""Synthetic planner output captured before the #401 helper consolidation."""
import hashlib
import json
import unittest

import test_ground_layer as ground


class SharedHelperPlannerSnapshots(unittest.TestCase):
    def test_synthetic_ground_planners_are_unchanged(self):
        # Baseline includes complete cells, statistics, classifications and skips.
        # Captured from main 56c4dfe before editing the helper implementations.
        # #449 renamed the bay-line statistic; these fixtures have no car park, so the
        # plans are unchanged and only that statistic's name moved.
        cases = (
            (ground.TestFloatingSurface, 'test_rise_of_twelve_or_more_is_filled_down_to_solid',
             5376, 'd0f8b99eda6ddd431724a655ba9a6b619e4573c9a2abefe80fa9c9746a17d51e'),
            (ground.TestMappedOutdoorSteps, 'test_steps_way_is_surfaced_at_lidar_height',
             1360, '1b194b1d02950bd6e8012d2ec0bc7742742d2171401d48564939b542fb161d73'),
        )
        for cls, method, count, expected in cases:
            with self.subTest(fixture=cls.__name__):
                fixture = cls(method)
                plan, stats, kind, skipped = fixture.run_plan(fixture.Osm())
                payload = {'cells': sorted((list(k), v) for k, v in plan.cells.items()),
                           'stats': stats, 'kind': sorted((list(k), v) for k, v in kind.items()),
                           'skipped': skipped}
                text = json.dumps(payload, sort_keys=True, separators=(',', ':'))
                self.assertEqual(len(plan.cells), count)
                self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), expected)
