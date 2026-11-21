"""Stepped water-level profile from synthetic lidar samples (#354)."""
from __future__ import annotations

import unittest

import water_level as WL


def segs(samples, min_step_length=10.0):
    return WL.water_level_profile(samples, min_step_length)


class WaterLevelProfileTest(unittest.TestCase):
    """Fixture rules from task #354 notes."""

    def test_flat_reach_one_segment(self):
        samples = [(0.0, 40.0), (50.0, 40.0), (100.0, 40.2)]
        out = segs(samples, min_step_length=20.0)
        self.assertEqual(out, [WL.LevelSegment(0.0, 100.0, 40)])

    def test_levels_never_rise_downstream(self):
        # Rising lidar (bridge decks / noise) must not raise the water level.
        samples = [(0.0, 40.0), (30.0, 42.0), (60.0, 45.0), (90.0, 41.0)]
        out = segs(samples, min_step_length=10.0)
        levels = [s.level for s in out]
        self.assertEqual(levels, sorted(levels, reverse=True))
        self.assertTrue(all(s.level <= 40 for s in out))

    def test_noise_under_one_block_does_not_step(self):
        samples = [
            (0.0, 40.0),
            (20.0, 40.4),
            (40.0, 39.6),
            (60.0, 40.2),
            (80.0, 39.7),
        ]
        out = segs(samples, min_step_length=10.0)
        self.assertEqual(out, [WL.LevelSegment(0.0, 80.0, 40)])

    def test_short_dip_bridge_or_culvert_ignored(self):
        # Sustained 42, brief drop below a full block for less than min_step, then back.
        samples = [
            (0.0, 42.0),
            (40.0, 42.0),
            (45.0, 39.0),  # short culvert / pier hole
            (48.0, 39.0),
            (50.0, 42.0),
            (100.0, 42.0),
        ]
        out = segs(samples, min_step_length=10.0)
        self.assertEqual(out, [WL.LevelSegment(0.0, 100.0, 42)])

    def test_sustained_drop_creates_step(self):
        samples = [
            (0.0, 42.0),
            (40.0, 42.0),
            (50.0, 40.0),
            (100.0, 40.0),
        ]
        out = segs(samples, min_step_length=10.0)
        self.assertEqual(
            out,
            [
                WL.LevelSegment(0.0, 50.0, 42),
                WL.LevelSegment(50.0, 100.0, 40),
            ],
        )

    def test_gentle_slope_steps_no_shorter_than_minimum(self):
        # Drop 4 blocks over 100 units; min step 25 → four segments of length 25.
        samples = [(float(d), 44.0 - 0.04 * d) for d in range(0, 101, 5)]
        out = segs(samples, min_step_length=25.0)
        self.assertGreaterEqual(len(out), 2)
        for i, seg in enumerate(out):
            length = seg.end - seg.start
            if i < len(out) - 1:
                self.assertGreaterEqual(length, 25.0 - 1e-9, seg)
            if i:
                self.assertLessEqual(seg.level, out[i - 1].level)
        self.assertEqual(out[0].start, 0.0)
        self.assertEqual(out[-1].end, 100.0)
        self.assertEqual(out[0].level, 44)
        self.assertEqual(out[-1].level, 40)

    def test_steep_slope_quantised_to_min_step_stairs(self):
        # One block every 5 units would be too short; stairs of length 25.
        samples = [(float(d), 44.0 - 0.2 * d) for d in range(0, 101, 5)]
        out = segs(samples, min_step_length=25.0)
        self.assertEqual(out[0].level, 44)
        self.assertEqual(out[-1].level, 24)
        for i, seg in enumerate(out[:-1]):
            self.assertGreaterEqual(seg.end - seg.start, 25.0 - 1e-9, seg)
            if i:
                self.assertLess(seg.level, out[i - 1].level)

    def test_empty_samples_rejected(self):
        with self.assertRaises(ValueError):
            segs([], min_step_length=10.0)

    def test_non_positive_min_step_rejected(self):
        with self.assertRaises(ValueError):
            segs([(0.0, 40.0), (10.0, 40.0)], min_step_length=0.0)

    def test_distances_must_be_non_decreasing(self):
        with self.assertRaises(ValueError):
            segs([(0.0, 40.0), (5.0, 40.0), (4.0, 39.0)], min_step_length=1.0)

    def test_single_sample_zero_length_segment(self):
        out = segs([(12.0, 41.7)], min_step_length=5.0)
        self.assertEqual(out, [WL.LevelSegment(12.0, 12.0, 41)])


if __name__ == '__main__':
    unittest.main()
