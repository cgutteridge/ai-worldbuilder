"""Y-offset planning uses synthetic heights, without reading any lidar tiles."""
import io
import unittest
from unittest import mock

import numpy as np
import lidar_extremes as le


class YOffsetFitTest(unittest.TestCase):
    def test_tall_coastal_relief_caps_offset(self):
        fit = le.suggest_y_offset(0, 275)
        self.assertEqual(fit['suggested_y_offset'], 40)
        self.assertEqual(fit['offset_min'], -50)
        self.assertEqual(fit['offset_max'], 40)
        self.assertIn('-50..40', fit['reason'])
        self.assertLessEqual(275 + fit['suggested_y_offset'], 315)

    def test_high_altitude_uses_negative_offset(self):
        fit = le.suggest_y_offset(1500.25, 1700.75)
        offset = fit['suggested_y_offset']
        self.assertGreaterEqual(1500.25 + offset, -50)
        self.assertLessEqual(1700.75 + offset, 315)
        self.assertIn(f"{fit['offset_min']}..{fit['offset_max']}", fit['reason'])

    def test_low_ground_respects_foundation_margin(self):
        fit = le.suggest_y_offset(-200.75, 100.25)
        offset = fit['suggested_y_offset']
        self.assertGreaterEqual(-200.75 + offset, -50)
        self.assertLessEqual(100.25 + offset, 315)

    def test_impossible_relief_explained(self):
        fit = le.suggest_y_offset(0, 400)
        self.assertIsNone(fit['suggested_y_offset'])
        self.assertIn('No integer y_offset fits', fit['reason'])
        self.assertIn('-50..-85', fit['reason'])

    def test_fractional_bounds_can_leave_no_integer_offset(self):
        fit = le.suggest_y_offset(0.1, 365.1)
        self.assertIsNone(fit['suggested_y_offset'])

    def test_extremes_reports_safe_suggestion(self):
        heights = {'dtm': np.array([0., 243.]), 'dsm': np.array([10., 275.])}
        with mock.patch.object(le, 'DefraLidar'), mock.patch.object(le, 'mc_heights', return_value=heights):
            result = le.extremes(0, 0, 1, 0)
        self.assertEqual(result['suggested_y_offset'], 40)
        self.assertEqual(result['y_offset_fit']['top_ceiling'], 315)
        self.assertEqual(result['y_offset_fit']['offset_min'], -50)
        self.assertEqual(result['y_offset_fit']['offset_max'], 40)
        self.assertIn('-50..40', result['note'])
        self.assertNotIn('headroom', result['note'])

    def test_cli_prints_fitting_range_with_suggestion(self):
        heights = {'dtm': np.array([0., 243.]), 'dsm': np.array([10., 275.])}
        buf = io.StringIO()
        with mock.patch.object(le, 'DefraLidar'), mock.patch.object(le, 'mc_heights', return_value=heights):
            with mock.patch('sys.stdout', buf):
                code = le.main(['--bbox-mc', '0,0,1,0'])
        self.assertEqual(code, 0)
        text = buf.getvalue()
        self.assertIn('suggested y_offset: 40  (fitting range -50..40)', text)
        self.assertNotIn('headroom', text)


if __name__ == '__main__':
    unittest.main()
