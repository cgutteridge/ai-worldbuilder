import math
import unittest

import numpy as np

import roof_surface as RS


def grid(width, depth, x0=0, z0=0):
    zz, xx = np.mgrid[0:depth, 0:width]
    return (xx + x0).astype(float).ravel(), (zz + z0).astype(float).ravel()


def rotated(xs, zs, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return xs * c - zs * s + 50.0, xs * s + zs * c + 50.0


class ShapeTest(unittest.TestCase):
    def test_a_level_deck_is_flat(self):
        xs, zs = grid(8, 6)
        surface = RS.fit_surface(xs, zs, np.full(xs.shape, 24.5))
        self.assertEqual(surface.kind, 'flat')
        self.assertAlmostEqual(float(surface.height(3, 3)), 24.5)

    def test_a_single_slope_is_a_plane(self):
        xs, zs = grid(10, 6)
        surface = RS.fit_surface(xs, zs, 26.0 - 0.58 * zs)
        self.assertEqual(surface.kind, 'plane')
        self.assertAlmostEqual(float(surface.height(2, 4)), 26.0 - 0.58 * 4, places=2)

    def test_a_ridge_across_the_axis_is_a_gable(self):
        xs, zs = grid(14, 6)
        h = np.where(xs <= 6, 27.0 + 0.7 * (xs), 31.2 - 0.7 * (xs - 6))
        surface = RS.fit_surface(xs, zs, h, axis_deg=0.0)
        self.assertEqual(surface.kind, 'gable')
        self.assertTrue(surface.ridge_across)
        self.assertAlmostEqual(surface.params[1], 6.0, delta=0.3)
        self.assertAlmostEqual(surface.params[0], 31.2, delta=0.2)

    def test_a_ridge_along_the_axis_is_a_gable(self):
        xs, zs = grid(14, 9)
        h = 30.0 - 0.6 * np.abs(zs - 4.0)
        surface = RS.fit_surface(xs, zs, h, axis_deg=0.0)
        self.assertEqual(surface.kind, 'gable')
        self.assertFalse(surface.ridge_across)

    def test_rotated_plots_fit_in_their_own_frame(self):
        gx, gz = grid(14, 6)
        h = np.where(gx <= 6, 27.0 + 0.7 * gx, 31.2 - 0.7 * (gx - 6))
        xs, zs = rotated(gx, gz, 34.0)
        surface = RS.fit_surface(xs, zs, h, axis_deg=34.0)
        self.assertEqual(surface.kind, 'gable')
        self.assertTrue(surface.ridge_across)
        self.assertLess(surface.rms, 0.05)


class UnmodelledShapeTest(unittest.TestCase):
    def test_a_hipped_roof_is_not_flattened(self):
        gx, gz = grid(14, 10)
        h = 28.0 + 3.0 * np.clip(np.minimum(np.minimum(gz, 9 - gz), np.minimum(gx, 13 - gx)) / 4.5, 0, 1)
        surface = RS.fit_surface(gx, gz, h)
        self.assertNotEqual(getattr(surface, 'kind', None), 'flat')

    def test_a_stepped_roof_gives_no_surface_instead_of_deleting_the_high_half(self):
        xs, zs = grid(10, 8)
        self.assertIsNone(RS.fit_surface(xs, zs, np.where(xs < 6, 30.0, 33.0)))

    def test_a_pair_of_same_sign_slopes_is_not_a_gable(self):
        xs, zs = grid(14, 6)
        h = np.where(xs <= 6, 26.0 + 0.5 * xs, 29.0 + 0.8 * (xs - 6))      # one wide slope with a kink
        surface = RS.fit_surface(xs, zs, h)
        self.assertNotEqual(getattr(surface, 'kind', None), 'gable')

    def test_a_three_wide_roof_does_not_crash_the_ridge_search(self):
        xs, zs = grid(3, 8)
        RS.fit_surface(xs, zs, 30.0 - 0.6 * np.abs(xs - 1.0))


class NoiseTest(unittest.TestCase):
    def test_lidar_noise_does_not_make_a_flat_roof_pitched(self):
        rng = np.random.default_rng(3)
        xs, zs = grid(12, 8)
        surface = RS.fit_surface(xs, zs, 27.0 + rng.normal(0, 0.3, xs.shape))
        self.assertEqual(surface.kind, 'flat')

    def test_edge_cells_that_read_the_wall_or_ground_are_trimmed(self):
        xs, zs = grid(12, 6)
        h = np.full(xs.shape, 28.0)
        h[zs == 5] = 22.0                 # the eave row reads the ground below
        surface = RS.fit_surface(xs, zs, h)
        self.assertEqual(surface.kind, 'flat')
        self.assertAlmostEqual(float(surface.height(1, 1)), 28.0, places=2)
        self.assertEqual(surface.kept, int((zs != 5).sum()))

    def test_a_mild_noisy_fall_is_still_a_plane(self):
        rng = np.random.default_rng(5)
        xs, zs = grid(14, 8)
        surface = RS.fit_surface(xs, zs, 27.0 - 0.5 * zs + rng.normal(0, 0.25, xs.shape))
        self.assertEqual(surface.kind, 'plane')

    def test_nan_cells_are_ignored_and_too_few_cells_give_none(self):
        xs, zs = grid(6, 4)
        h = np.full(xs.shape, 25.0)
        h[::2] = np.nan
        self.assertEqual(RS.fit_surface(xs, zs, h).kind, 'flat')
        self.assertIsNone(RS.fit_surface(xs[:4], zs[:4], h[:4]))


if __name__ == '__main__':
    unittest.main()
