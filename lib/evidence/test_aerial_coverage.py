"""Mosaic coverage and sampling with tiny synthetic images."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image


class MosaicCoverageTest(unittest.TestCase):
    def test_multiple_mosaics_jointly_cover_the_cell_for_tree_sampling(self):
        import aerial_coverage as aerial
        import trees_layer
        with tempfile.TemporaryDirectory() as tmp:
            sources = []
            for offset in (0, 64):
                path = Path(tmp) / f'{offset}.png'
                Image.new('RGB', (64, 128), (60, 100, 40)).save(path)
                sources.append(aerial.Mosaic(path, 19, offset, 0))
            with mock.patch.object(aerial.Mosaic, 'pixels',
                                   lambda self, pts: {p: (p[0] - self.tile_x, p[1]) for p in pts}), \
                    mock.patch.object(aerial, 'mosaics', return_value=sources):
                self.assertEqual(aerial.coverage((0, 0), [sources[0]]), .5)
                self.assertEqual(aerial.coverage((0, 0)), 1.)
                self.assertGreater(trees_layer.cover_grid((0, 0)).min(), .9)

    def test_tree_detection_refuses_missing_aerial_instead_of_returning_zero_crowns(self):
        import aerial_coverage as aerial
        import trees_layer
        with mock.patch.object(aerial, 'mosaics', return_value=[]):
            with self.assertRaisesRegex(aerial.CoverageError, '0.0%'):
                trees_layer.canopy_grid((53, -43))

    def test_partial_cell_reports_fraction_and_refuses_silent_empty_detection(self):
        import aerial_coverage as aerial
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'mosaic-z19-1x1-0-0.png'
            Image.new('RGB', (64, 128), (60, 100, 40)).save(path)
            mosaic = aerial.Mosaic(path, 19, 0, 0)
            with mock.patch.object(aerial.Mosaic, 'pixels',
                                   lambda self, pts: {p: (p[0], p[1]) for p in pts}):
                self.assertEqual(aerial.coverage((0, 0), [mosaic]), 0.5)
                with self.assertRaisesRegex(aerial.CoverageError, '50.0%'):
                    aerial.require_coverage((0, 0), [mosaic])
                Image.new('RGB', (128, 128), (60, 100, 40)).save(path)
                self.assertEqual(aerial.coverage((0, 0), [mosaic]), 1.0)
                self.assertEqual(len(aerial.sample_rgb((0, 0), [mosaic])), 128 * 128)

