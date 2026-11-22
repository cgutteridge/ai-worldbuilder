"""Synthetic planner output captured before the #401 helper consolidation."""
import hashlib
import json
import unittest

import test_ground_layer as ground


class SharedHelperPlannerSnapshots(unittest.TestCase):
    def test_synthetic_ground_planners_are_unchanged(self):
        # Baseline includes complete cells, statistics, classifications and skips.
        # Captured from main 56c4dfe before editing the helper implementations.
        cases = (
            (ground.TestFloatingSurface, 'test_rise_of_twelve_or_more_is_filled_down_to_solid',
             5376, 'd6562ff6d0dc1970c6298661036e78745d8770d4f0b1c3c13f04142a498a0381'),
            (ground.TestMappedOutdoorSteps, 'test_steps_way_is_surfaced_at_lidar_height',
             1360, '7bc381c68d6c0c4fb3c0d7cbe48b9c63d6aaf100032cb6fbc37f8f68b8245583'),
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
