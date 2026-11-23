"""Aerial car-park bay lines (#449): copy a thin pale streak, drop specks, refuse a soft picture."""
import unittest
from types import SimpleNamespace

import numpy as np

import bay_lines as bl
from ground_sources import PRI
from test_ground_layer import SyntheticGroundBox

SCALE = 5


def canvas(x0, z0, x1, z1, colour=0.40):
    """Flat tarmac image, one block = ``SCALE`` pixels, origin at ``(x0, z0)``."""
    height = (z1 - z0 + 1) * SCALE
    width = (x1 - x0 + 1) * SCALE
    rgb = np.full((height, width, 3), colour, np.float32)
    yy, xx = np.mgrid[0:height, 0:width]
    mx = (x0 + xx // SCALE).astype(np.int32)
    mz = (z0 + yy // SCALE).astype(np.int32)
    mask = np.ones((height, width), dtype=bool)
    return rgb, mask, mx, mz


def paint_columns(rgb, x0, z0, z, xs, colour):
    """One-pixel horizontal streak through the middle of column ``z``, over ``xs``."""
    row = (z - z0) * SCALE + SCALE // 2
    for x in xs:
        start = (x - x0) * SCALE
        rgb[row, start:start + SCALE] = colour


def columns(keep, mx, mz):
    return bl.columns_of(keep, mx, mz)


def box_blur(rgb, radius):
    """Mean of a square window. Edge pixels repeat so the blur does not wrap."""
    pad = np.pad(rgb, ((radius, radius), (radius, radius), (0, 0)), mode='edge')
    out = np.zeros_like(rgb)
    window = (2 * radius + 1) ** 2
    for dy in range(2 * radius + 1):
        for dx in range(2 * radius + 1):
            out += pad[dy:dy + rgb.shape[0], dx:dx + rgb.shape[1]]
    return out / window


class TestThinPaleStreaks(unittest.TestCase):
    def frame(self, colour=0.40):
        rgb, mask, mx, mz = canvas(0, 0, 16, 8, colour)
        return rgb, mask, mx, mz

    def test_a_clean_line_marks_those_columns_and_leaves_the_gap(self):
        rgb, mask, mx, mz = self.frame()
        xs = [x for x in range(1, 15) if x != 7]
        paint_columns(rgb, 0, 0, 4, xs, 0.95)
        keep, soft = bl.thin_pale_mask(rgb, mask)
        self.assertFalse(soft)
        self.assertEqual(columns(keep, mx, mz), {(x, 4) for x in xs})

    def test_a_faint_line_still_counts_when_it_is_paler_than_the_tarmac(self):
        rgb, mask, mx, mz = self.frame(0.42)
        paint_columns(rgb, 0, 0, 4, range(1, 15), 0.55)
        keep, soft = bl.thin_pale_mask(rgb, mask)
        self.assertFalse(soft)
        self.assertEqual(columns(keep, mx, mz), {(x, 4) for x in range(1, 15)})

    def test_specks_are_removed(self):
        rgb, mask, mx, mz = self.frame()
        for n in range(6):
            rgb[6 + n * 5, 8] = 0.98
        keep, soft = bl.thin_pale_mask(rgb, mask)
        self.assertFalse(soft)
        self.assertFalse(keep.any())
        self.assertEqual(columns(keep, mx, mz), set())

    def test_a_soft_park_with_no_line_draws_nothing(self):
        rgb, mask, mx, mz = self.frame()
        rgb[12:40, 15:70] = 0.82
        rgb = box_blur(rgb, 2)
        keep, soft = bl.thin_pale_mask(rgb, mask)
        self.assertTrue(soft)
        self.assertFalse(keep.any())
        self.assertEqual(columns(keep, mx, mz), set())

    def test_a_wide_blob_is_not_a_line(self):
        rgb, mask, mx, mz = self.frame()
        rgb[10:30, 15:35] = 0.92
        keep, soft = bl.thin_pale_mask(rgb, mask)
        self.assertFalse(soft)
        self.assertFalse(keep.any())

    def test_a_short_streak_alone_draws_nothing_and_is_not_soft(self):
        rgb, mask, mx, mz = self.frame()
        paint_columns(rgb, 0, 0, 4, range(1, 4), 0.95)
        keep, soft = bl.thin_pale_mask(rgb, mask)
        self.assertFalse(soft)
        self.assertFalse(keep.any())
        self.assertEqual(columns(keep, mx, mz), set())

    def test_a_boundary_streak_is_dropped_and_the_interior_line_is_kept(self):
        rgb, mask, mx, mz = canvas(0, 0, 20, 16)
        mask[:5, :] = False
        paint_columns(rgb, 0, 0, 10, range(1, 18), 0.95)
        rgb[6, 5:90] = 0.95
        keep, soft = bl.thin_pale_mask(rgb, mask)
        self.assertFalse(soft)
        marked = columns(keep, mx, mz)
        self.assertIn((5, 10), marked)
        self.assertNotIn((5, 1), marked)


class TestMarkCarParkBays(unittest.TestCase):
    def line_frame(self):
        rgb, mask, mx, mz = canvas(0, 0, 16, 8)
        xs = list(range(1, 15))
        paint_columns(rgb, 0, 0, 4, xs, 0.95)
        return bl.AerialFrame(rgb, mask, mx, mz, '0,0..16,8')

    def test_only_tarmac_inside_the_car_park_is_marked(self):
        area = {(x, z) for x in range(17) for z in range(9)}
        kind = dict.fromkeys(area, 'tarmac')
        kind[(3, 4)] = 'grass'
        kind[(4, 4)] = 'tarmac'
        outside = (20, 4)
        kind[outside] = 'tarmac'
        pri = dict.fromkeys(area, PRI['parking'])
        pri[(5, 4)] = PRI['service']
        vmap = SimpleNamespace(parking_areas=[area], bay_frames=[self.line_frame()], pri=pri)
        result = bl.mark_car_park_bays(kind, vmap)
        marked = {q for q, k in kind.items() if k == 'mark'}
        self.assertEqual(result.n, len(marked))
        self.assertNotIn((3, 4), marked)
        self.assertNotIn((5, 4), marked)
        self.assertNotIn(outside, marked)
        self.assertIn((4, 4), marked)
        self.assertEqual(result.soft, ())

    def test_a_soft_park_is_named_and_not_painted(self):
        rgb, mask, mx, mz = canvas(0, 0, 16, 8)
        rgb[12:40, 15:70] = 0.82
        rgb = box_blur(rgb, 2)
        area = {(x, z) for x in range(17) for z in range(9)}
        kind = dict.fromkeys(area, 'tarmac')
        frame = bl.AerialFrame(rgb, mask, mx, mz, '0,0..16,8')
        pri = dict.fromkeys(area, PRI['parking'])
        result = bl.mark_car_park_bays(
            kind, SimpleNamespace(parking_areas=[area], bay_frames=[frame], pri=pri))
        self.assertEqual(result.n, 0)
        self.assertEqual(result.soft, ('0,0..16,8',))
        self.assertFalse(any(k == 'mark' for k in kind.values()))

    def test_evidence_without_car_parks_marks_nothing(self):
        kind = {(0, 0): 'tarmac'}
        self.assertEqual(bl.mark_car_park_bays(kind, SimpleNamespace(pri={})), bl.BayLineResult(0, ()))
        self.assertEqual(kind, {(0, 0): 'tarmac'})


class TestGroundPassBayLines(SyntheticGroundBox, unittest.TestCase):
    """The ground pass writes the aerial lines itself, as white_concrete on tarmac."""

    def car_park(self, frame):
        osm = self.Osm()
        x0, z0, x1, z1 = self.BOX
        area = {(x, z) for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)}
        osm.kind, osm.pri = dict.fromkeys(area, 'tarmac'), dict.fromkeys(area, PRI['parking'])
        osm.parking_areas = [area]
        osm.bay_frames = [frame]
        return osm

    def test_a_painted_line_becomes_white_concrete_and_the_gap_stays_tarmac(self):
        rgb, mask, mx, mz = canvas(*self.BOX)
        # The synthetic box is only 8 columns in z (0..7). The line runs along x at z=6.
        paint_columns(rgb, self.BOX[0], self.BOX[1], 6, [x for x in range(1, 14) if x != 8], 0.95)
        frame = bl.AerialFrame(rgb, mask, mx, mz, '0,0..15,7')
        plan, stats, kind, _ = self.run_plan(self.car_park(frame))
        marks = {q for q, k in kind.items() if k == 'mark'}
        self.assertEqual(stats['how: car park bay line (from the aerial)'], len(marks))
        self.assertEqual(marks, {(x, 6) for x in range(1, 14) if x != 8})
        self.assertNotIn('aerial too soft', ' '.join(stats))
        for x, z in marks:
            self.assertEqual(plan.cells[(x, self.lidar(x, z), z)], 'minecraft:white_concrete')
        self.assertEqual(plan.cells[(8, self.lidar(8, 6), 6)], 'minecraft:gray_concrete')
        self.assertEqual(plan.cells[(3, self.lidar(3, 5), 5)], 'minecraft:gray_concrete')
        self.assertFalse(plan.ground_layer)

    def test_a_soft_car_park_is_listed_and_stays_tarmac(self):
        rgb, mask, mx, mz = canvas(*self.BOX)
        rgb[8:32, 10:60] = 0.82
        rgb = box_blur(rgb, 2)
        frame = bl.AerialFrame(rgb, mask, mx, mz, '0,0..15,7')
        plan, stats, kind, _ = self.run_plan(self.car_park(frame))
        self.assertNotIn('mark', kind.values())
        self.assertEqual(stats['how: aerial too soft 0,0..15,7'], 1)
        self.assertNotIn('minecraft:white_concrete', plan.cells.values())


if __name__ == '__main__':
    unittest.main()
