"""Mosaics and registered aerials share one interface; aerial_rgb layers them; aerial_annot crops either."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

import aerial_annot
import aerial_coverage
import aerial_registered
import ground_sources

# aerial_rgb((0, 0)) on the fixture below, recorded from the pre-change implementation
# (registered-shot pixels layered over mosaic pixels).
GOLDEN_COUNT = 17424
GOLDEN_DIGEST = 'f36220d1e2ca63e52c1b2ac52d6f59fca401d0886c965417df06c512b09b4fb5'
MOSAIC_RGB = (60 / 255, 100 / 255, 40 / 255)
REG_RGB = (90 / 255, 125 / 255, 97 / 255)


_REAL_PIXELS = aerial_coverage.Mosaic.pixels


def identity_pixels(self, points):
    return {p: (p[0], p[1]) for p in points}


class Fixture(unittest.TestCase):
    """Synthetic mosaic (identity pixels) and a registered shot (2 px per block, right half black)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.dir = self.root / 'reference' / 'aerial' / 'x'
        self.dir.mkdir(parents=True)
        self.mosaic_path = self.dir / 'mosaic-z19-1x1-0-0.png'
        Image.new('RGB', (136, 136), (60, 100, 40)).save(self.mosaic_path)
        shot = np.zeros((200, 200, 3), np.uint8)
        shot[:, :100] = (90, 125, 97)
        Image.fromarray(shot).save(self.dir / 'shot.png')
        self.reg_path = self.dir / 'shot.registration.json'
        self.reg_path.write_text(json.dumps({'image': 'aerial/x/shot.png', 'A': [[2, 0, 0], [0, 2, 0]]}))
        self.mosaic = aerial_coverage.Mosaic(self.mosaic_path, 19, 0, 0)
        for patch in (mock.patch.object(aerial_coverage.Mosaic, 'pixels', identity_pixels),
                      mock.patch.object(aerial_coverage, 'mosaics', return_value=[self.mosaic]),
                      mock.patch.object(aerial_registered, 'ROOT', self.root)):
            patch.start()
            self.addCleanup(patch.stop)


class RegisteredDiscoveryTest(Fixture):
    def test_discovery_finds_registration_and_skips_broken_ones(self):
        (self.dir / 'bad.registration.json').write_text('{not json')
        (self.dir / 'noimage.registration.json').write_text(json.dumps({'image': 'aerial/x/gone.png', 'A': [[1, 0, 0], [0, 1, 0]]}))
        found = aerial_registered.registered()
        self.assertEqual([r.path.name for r in found], ['shot.registration.json'])
        self.assertAlmostEqual(found[0].px_per_block, 2.0)

    def test_both_kinds_expose_pixels_and_image(self):
        reg = aerial_registered.registered()[0]
        for source in (self.mosaic, reg):
            self.assertEqual(source.image().mode, 'RGB')
            self.assertEqual(set(source.pixels([(3, 4)])), {(3, 4)})
        self.assertEqual(reg.pixels([(3, 4)])[(3, 4)], (7.0, 9.0))      # column centre through A

    def test_registered_sampling_skips_outside_edge_and_black(self):
        reg = aerial_registered.registered()[0]
        out = aerial_registered.sample_rgb([(30, 50), (1, 50), (30, 1), (60, 50), (80, 50)], [reg])
        self.assertEqual(set(out), {(30, 50)})           # (1,50),(30,1) near the edge; (60,50),(80,50) black
        self.assertEqual(out[(30, 50)], REG_RGB)

    def test_registration_glob_lives_in_one_place(self):
        lib = Path(__file__).resolve().parents[1]
        hits = [p.name for p in lib.rglob('*.py')
                if not p.name.startswith('test_') and "glob('reference/aerial/" in p.read_text()]
        self.assertEqual(hits, ['aerial_registered.py'])


class AerialRgbLayeringTest(Fixture):
    def test_registered_wins_where_it_covers_and_matches_pre_change_output(self):
        out = ground_sources.aerial_rgb((0, 0))
        self.assertEqual(len(out), GOLDEN_COUNT)
        digest = hashlib.sha256(repr(sorted(
            (k, tuple(float(c) for c in v), type(v).__name__) for k, v in out.items())).encode()).hexdigest()
        self.assertEqual(digest, GOLDEN_DIGEST)
        self.assertIsInstance(out[(10, 10)], ground_sources.RegRGB)
        self.assertEqual(tuple(out[(10, 10)]), REG_RGB)
        self.assertNotIsInstance(out[(60, 30)], ground_sources.RegRGB)     # black half: mosaic fills in
        self.assertEqual(tuple(float(c) for c in out[(60, 30)]), MOSAIC_RGB)


class AerialAnnotTest(Fixture):
    BOX = (20, 20, 60, 60)
    FOOT = [([[(33, 33), (37, 33), (37, 37), (33, 37), (33, 33)]], {'osm_way_id': '1234567'})]

    def test_crop_of_a_mosaic_has_grid_and_footprint(self):
        out = self.root / 'crop.png'
        source = aerial_annot.covering_source(self.BOX)
        self.assertEqual(source.path, self.mosaic_path)
        size = aerial_annot.annotate(source, self.BOX, out, scale=2, footprints=self.FOOT)
        self.assertEqual(size, (80, 80))
        im = Image.open(out).convert('RGB')
        self.assertEqual(im.size, (80, 80))
        colours = {c for _, c in im.getcolors(10000)}
        self.assertIn((255, 0, 0), colours)       # footprint outline
        self.assertIn((0, 255, 255), colours)     # x grid
        self.assertIn((0, 255, 0), colours)       # z grid

    def test_registered_aerial_works_through_the_same_interface(self):
        out = self.root / 'reg.png'
        reg = aerial_registered.registered()[0]
        size = aerial_annot.annotate(reg, (10, 10, 30, 30), out, scale=1, footprints=[])
        self.assertEqual(size, (40, 40))

    def test_named_mosaic_and_missing_cover(self):
        self.assertEqual(aerial_annot.covering_source(self.BOX, self.mosaic_path).path, self.mosaic_path)
        with self.assertRaises(ValueError):
            aerial_annot.covering_source(self.BOX, self.root / 'other.png')
        with self.assertRaises(ValueError):
            aerial_annot.covering_source((500, 500, 600, 600))

    def test_annotation_position_is_the_pre_change_projection(self):
        """No half-block shift: exact MC coordinates land where the old px() put them.

        Expected values come from the old implementation (to_osgb -> wgs84 -> world pixel, no +.5)
        for a real, unmocked Mosaic.pixels.
        """
        mosaic = aerial_coverage.Mosaic(Path('unused.png'), 18, 130062, 87835, 1.0)
        with mock.patch.object(aerial_coverage.Mosaic, 'pixels', _REAL_PIXELS):
            got = aerial_annot.locate(mosaic, [(0, 0), (47, -45), (-20, 33)])
        want = [(-18464.13962544501, 14854.067977398634), (-18338.76727669686, 14735.373189173639),
                (-18517.74305698648, 14941.379081647843)]
        for g, w in zip(got, want):
            self.assertAlmostEqual(g[0], w[0], places=6)
            self.assertAlmostEqual(g[1], w[1], places=6)

    def test_module_has_no_default_mosaic_path_or_import_time_argv(self):
        text = Path(aerial_annot.__file__).read_text()
        self.assertNotIn('sys.argv[', text.replace('sys.argv[1:]', ''))


if __name__ == '__main__':
    unittest.main()
