"""Unit tests for feature_selection_check grading helpers."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import feature_selection_check as fsc


class BboxMathTests(unittest.TestCase):
    """IoU and outer-pad maths."""

    def test_iou_identical(self):
        b = (0, 0, 9, 9)
        self.assertEqual(fsc.bbox_iou(b, b), 1.0)

    def test_iou_disjoint(self):
        self.assertEqual(fsc.bbox_iou((0, 0, 1, 1), (5, 5, 6, 6)), 0.0)

    def test_outer_pad_detects_fringe(self):
        gold = (10, 10, 20, 20)
        claimed = (8, 8, 22, 22)  # 2-block pad all sides
        self.assertEqual(fsc.chebyshev_outer_pad(claimed, gold), 2)

    def test_outer_pad_zero_when_inside(self):
        gold = (10, 10, 20, 20)
        claimed = (11, 11, 19, 19)
        self.assertEqual(fsc.chebyshev_outer_pad(claimed, gold), 0)


class GradeTests(unittest.TestCase):
    """End-to-end grade_answer against synthetic gold."""

    def test_pass_exact(self):
        key = {
            'osm_way_id': '33830305',
            'require_reject': ['33830236', '34391673'],
            'min_bbox_iou': 0.85,
            'max_pad': 0,
        }
        gold = (6142, -5582, 6226, -5492)
        answer = fsc.parse_answer({
            'osm_way_id': 'w33830305',
            'reject_ways': ['33830236', '34391673', '93729745'],
            'bbox_mc': list(gold),
            'dilated': False,
        })
        report = fsc.grade_answer(answer, key, gold)
        self.assertEqual(report['overall'], 'pass')

    def test_fail_dilated_fringe(self):
        key = {
            'osm_way_id': '33830305',
            'require_reject': ['33830236'],
            'min_bbox_iou': 0.5,
            'max_pad': 0,
        }
        gold = (6142, -5582, 6226, -5492)
        claimed = (6140, -5584, 6228, -5490)  # 2-block pad
        answer = fsc.parse_answer({
            'osm_way_id': '33830305',
            'reject_ways': ['33830236'],
            'bbox_mc': list(claimed),
            'dilated': False,
        })
        report = fsc.grade_answer(answer, key, gold)
        self.assertEqual(report['overall'], 'fail')
        self.assertFalse(report['checks']['bbox']['pass'])
        self.assertEqual(report['checks']['bbox']['outer_pad'], 2)

    def test_fail_wrong_way(self):
        key = {
            'osm_way_id': '33830305',
            'require_reject': ['33830236'],
            'min_bbox_iou': 0.85,
            'max_pad': 0,
        }
        gold = (0, 0, 10, 10)
        answer = fsc.parse_answer({
            'osm_way_id': '33830236',
            'reject_ways': ['33830236'],
            'bbox_mc': list(gold),
            'dilated': False,
        })
        report = fsc.grade_answer(answer, key, gold)
        self.assertEqual(report['overall'], 'fail')
        self.assertFalse(report['checks']['way_id']['pass'])


class FixtureTests(unittest.TestCase):
    """Repo scenarios and briefs load."""

    def test_list_three_scenarios(self):
        rows = fsc.list_scenarios()
        ids = {r['id'] for r in rows}
        self.assertTrue({'FS-001', 'FS-002', 'FS-003', 'FS-004'} <= ids)

    def test_brief_mentions_no_dilate(self):
        brief = fsc.format_brief(fsc.load_scenario('FS-001'))
        self.assertIn('FS-001', brief)
        self.assertIn('dilated', brief)

    def test_grade_cli_with_temp_answer(self):
        key = fsc.load_key('FS-001')
        # Use a fake gold bbox so we do not need live OSM in this assert path…
        # Actually cmd_grade calls osm_way_bbox. Use grade_answer directly with
        # a temp answer that matches key + synthetic bbox, already covered above.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'answer.json'
            path.write_text(json.dumps({
                'osm_way_id': key['osm_way_id'],
                'reject_ways': key['require_reject'],
                'bbox_mc': [1, 2, 3, 4],
                'dilated': False,
            }), encoding='utf-8')
            answer = fsc.load_answer_file(path)
            report = fsc.grade_answer(answer, key, (1, 2, 3, 4))
            self.assertEqual(report['overall'], 'pass')


if __name__ == '__main__':
    unittest.main()
