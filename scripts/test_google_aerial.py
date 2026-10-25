"""Unit tests for google_aerial tile maths and sheet.

Markup and tile_range cases stub ``mc_to_tile`` at the module boundary with a
fictional affine (not a CRS conversion) so they exercise HTML/placement without
GDAL, pyproj, network, or a real world frame. Coordinate correctness belongs to
``world_frame`` fixtures (tasks #84-#86).
"""
from __future__ import annotations

import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'shims'))

import google_aerial as ga  # noqa: E402
import world_frame as wf  # noqa: E402

# Fictional MC→tile map for markup / tile_range only: 40 MC blocks per tile axis.
# Zoom is ignored. This is explicit test I/O, not a second conversion library.
_BLOCKS_PER_TILE = 40.0


def _fictional_mc_to_tile(
    points: list[tuple[float, float]], zoom: int,
) -> list[tuple[float, float]]:
    """Return deterministic fractional tiles for fictional MC points.

    @param points: MC (x, z) pairs
    @param zoom: zoom level (accepted for boundary parity; unused)
    @returns: one (tile_x, tile_y) per point
    """
    del zoom
    return [(x / _BLOCKS_PER_TILE, z / _BLOCKS_PER_TILE) for x, z in points]


class _ProjectionStub:
    """Records ``mc_to_tile`` calls and returns fictional tile fractions.

    @param calls: recorded (points, zoom) batches after each invocation
    """

    def __init__(self) -> None:
        self.calls: list[tuple[list[tuple[float, float]], int]] = []

    def __call__(
        self, points: list[tuple[float, float]], zoom: int,
    ) -> list[tuple[float, float]]:
        """Projection-boundary stand-in used by markup tests.

        @param points: MC (x, z) pairs from sheet/tile_range
        @param zoom: zoom level
        @returns: fictional fractional tile coordinates
        """
        pts = [(float(x), float(z)) for x, z in points]
        self.calls.append((pts, zoom))
        return _fictional_mc_to_tile(pts, zoom)

    def all_points(self) -> list[tuple[float, float]]:
        """Flatten every MC point passed to the stub.

        @returns: points in call order
        """
        return [p for pts, _z in self.calls for p in pts]


class TestTiles(unittest.TestCase):
    def test_tile_float_matches_known_oakdene_tiles(self):
        # looked up by hand 2026-10-16: lat 50.931839, lon -1.395051
        for zoom, xy in ((18, (130056, 87838)), (19, (260112, 175677)), (20, (520224, 351354))):
            fx, fy = ga.tile_float(50.931839, -1.395051, zoom)
            self.assertEqual((int(fx), int(fy)), xy)

    def test_url_is_a_plain_tile(self):
        self.assertEqual(ga.tile_url(260112, 175677, 19), 'https://mt1.google.com/vt/lyrs=s&x=260112&y=175677&z=19')

    def test_tile_range_uses_corner_projections(self):
        # Fictional bbox (0,0)–(80,80) → tiles (0,0)…(2,2) under _fictional_mc_to_tile.
        proj = _ProjectionStub()
        with mock.patch.object(ga, 'mc_to_tile', side_effect=proj), mock.patch.object(
            wf, '_gdal_transform', side_effect=AssertionError('tile_range must not call GDAL'),
        ):
            self.assertEqual(ga.tile_range((0, 0, 80, 80), 20), (0, 0, 2, 2))
        self.assertEqual(len(proj.calls), 1)
        self.assertEqual(proj.calls[0][1], 20)
        self.assertEqual(
            proj.calls[0][0],
            [(0.0, 0.0), (80.0, 0.0), (0.0, 80.0), (80.0, 80.0)],
        )

    def test_too_many_tiles_is_refused(self):
        def sprawling(points, zoom):
            del zoom
            # Span 100 tiles on each axis → over MAX_TILES.
            return [(x * (100 / 80), z * (100 / 80)) for x, z in points]

        with mock.patch.object(ga, 'mc_to_tile', side_effect=sprawling), mock.patch.object(
            wf, '_gdal_transform', side_effect=AssertionError('tile_range must not call GDAL'),
        ):
            with self.assertRaises(ValueError):
                ga.tile_range((0, 0, 80, 80), 20)


class TestGrid(unittest.TestCase):
    def test_lines_every_ten_blocks_inside_the_box(self):
        lines = ga.grid_lines((6354, -5302, 6404, -5252))
        xs = [ln['value'] for ln in lines if ln['axis'] == 'x']
        zs = [ln['value'] for ln in lines if ln['axis'] == 'z']
        self.assertEqual(xs, [6360, 6370, 6380, 6390, 6400])
        self.assertEqual(zs, [-5300, -5290, -5280, -5270, -5260])

    def test_osm_outlines_keep_buildings_with_a_vertex_in_the_box(self):
        inside = ([[(10, 10), (20, 10), (20, 20), (10, 10)]], {'osm_way_id': 'w123456'})
        outside = ([[(900, 900), (910, 900), (910, 910), (900, 900)]], {'osm_way_id': 'w999'})
        fake = type('I', (), {'buildings': [inside, outside]})
        with mock.patch('osm_footprints.OSMIndex', return_value=fake):
            out = ga.osm_outlines((0, 0, 50, 50))
        self.assertEqual([o['label'] for o in out], ['456'])


class MarkupSheetTest(unittest.TestCase):
    """sheet_html against a stubbed ``mc_to_tile`` (no real CRS / GDAL)."""

    # Worked example: bbox (0,0,80,80), zoom 20, default scale 2 → s=512, tx0=ty0=0.
    # Under _fictional_mc_to_tile, MC (x,z) → pixel ((x/40)*512, (z/40)*512).

    BBOX = (0, 0, 80, 80)
    ZOOM = 20

    def setUp(self) -> None:
        self.proj = _ProjectionStub()
        p_mc = mock.patch.object(ga, 'mc_to_tile', side_effect=self.proj)
        p_gdal = mock.patch.object(
            wf, '_gdal_transform',
            side_effect=AssertionError('markup must not call GDAL'),
        )
        p_batch = mock.patch.object(
            wf, 'mc_batch_to_wgs84',
            side_effect=AssertionError('markup must not call mc_batch_to_wgs84'),
        )
        for p in (p_mc, p_gdal, p_batch):
            p.start()
            self.addCleanup(p.stop)

    def test_sheet_has_every_tile_and_the_outline(self):
        page = ga.sheet_html(self.BBOX, self.ZOOM, 't')
        self.assertEqual(page.count('<img '), 9)
        self.assertIn('x40', page)
        self.assertIn('<polygon', page)
        for corner in ((0.0, 0.0), (80.0, 0.0), (0.0, 80.0), (80.0, 80.0)):
            self.assertIn(corner, self.proj.all_points())
        # Hand-worked: MC (80,80) → tile (2,2) → pixel (1024, 1024) on the bbox polygon.
        self.assertIn('1024.0,1024.0', page)
        self.assertTrue(all(z == self.ZOOM for _pts, z in self.proj.calls))

    def test_outlines_are_drawn_with_their_labels(self):
        ring = [(40, 40), (80, 40), (80, 80), (40, 80), (40, 40)]
        page = ga.sheet_html(
            self.BBOX, self.ZOOM, 't', outlines=[{'ring': ring, 'label': '441'}],
        )
        self.assertEqual(page.count('class="osm"'), 1)
        self.assertIn('>441</text>', page)
        for x, z in ring:
            self.assertIn((float(x), float(z)), self.proj.all_points())
        # Hand-worked ring pixels: 40→512, 80→1024.
        self.assertIn(
            '512.0,512.0 1024.0,512.0 1024.0,1024.0 512.0,1024.0 512.0,512.0',
            page,
        )
        self.assertNotIn('class="osm"', ga.sheet_html(self.BBOX, self.ZOOM, 't'))

    def test_marks_are_drawn_per_cell(self):
        page = ga.sheet_html(
            self.BBOX, self.ZOOM, 't',
            marks=[(40, 40, '#f0f'), (41, 40, '#0ff')],
        )
        self.assertEqual(page.count('class="mark"'), 2)
        for p in ((40.0, 40.0), (41.0, 40.0), (41.0, 41.0), (40.0, 41.0)):
            self.assertIn(p, self.proj.all_points())
        # Hand-worked cell (40,40): corners → 512 / 524.8 (41/40 * 512).
        self.assertIn('512.0,512.0 524.8,512.0 524.8,524.8 512.0,524.8', page)
        self.assertNotIn('class="mark"', ga.sheet_html(self.BBOX, self.ZOOM, 't'))


if __name__ == '__main__':
    unittest.main()
