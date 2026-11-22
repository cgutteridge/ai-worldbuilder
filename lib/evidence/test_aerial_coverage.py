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

    def test_covering_mosaic_picks_the_finest_one_that_holds_every_point(self):
        import aerial_coverage as aerial
        with tempfile.TemporaryDirectory() as tmp:
            small = Path(tmp) / 'small.png'
            big = Path(tmp) / 'big.png'
            Image.new('RGB', (32, 32), (10, 10, 10)).save(small)
            Image.new('RGB', (128, 128), (10, 10, 10)).save(big)
            sources = [aerial.Mosaic(small, 19, 0, 0), aerial.Mosaic(big, 18, 0, 0)]
            with mock.patch.object(aerial.Mosaic, 'pixels',
                                   lambda self, pts: {p: (p[0], p[1]) for p in pts}):
                mosaic, px = aerial.covering_mosaic([(5, 5), (20, 20)], sources)
                self.assertEqual(mosaic.path, small)
                self.assertEqual(px[(20, 20)], (20, 20))
                mosaic, _ = aerial.covering_mosaic([(5, 5), (100, 100)], sources)
                self.assertEqual(mosaic.path, big)
                self.assertIsNone(aerial.covering_mosaic([(5, 5), (500, 500)], sources))

    def test_rgb_cache_returns_the_same_array_inside_one_run(self):
        import aerial_coverage as aerial
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'tile.png'
            Image.new('RGB', (8, 6), (10, 20, 30)).save(path)
            opens = []
            real_open = Image.open

            def counting_open(file, *args, **kwargs):
                opens.append(Path(file))
                return real_open(file, *args, **kwargs)

            with mock.patch.object(Image, 'open', counting_open):
                with aerial.rgb_cache():
                    first = aerial.load_rgb(path)
                    second = aerial.load_rgb(path)
                self.assertIs(first, second)
                self.assertEqual(opens, [path])
                again = aerial.load_rgb(path)
            self.assertIsNot(again, first)
            self.assertEqual(len(opens), 2)
            self.assertTrue((again == first).all())
            self.assertEqual(tuple(again[0, 0]), (10, 20, 30))

    def test_one_trees_run_decodes_each_mosaic_once(self):
        import aerial_coverage as aerial
        import trees_layer
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'mosaic.png'
            Image.new('RGB', (128, 128), (40, 90, 30)).save(path)
            source = aerial.Mosaic(path, 19, 0, 0)
            opens = []
            real_open = Image.open

            def counting_open(file, *args, **kwargs):
                opens.append(Path(file))
                return real_open(file, *args, **kwargs)

            with mock.patch.object(aerial, 'mosaics', return_value=[source]), \
                    mock.patch.object(aerial.Mosaic, 'pixels',
                                      lambda self, pts: {p: (p[0], p[1]) for p in pts}), \
                    mock.patch.object(Image, 'open', counting_open):
                with aerial.rgb_cache():
                    trees_layer.canopy_grid((0, 0))
                    trees_layer.cover_grid((0, 0))
            self.assertEqual(opens, [path])

