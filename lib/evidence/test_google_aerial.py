"""Unit tests for google_aerial tile maths and sheet.

Markup and tile_range cases stub ``mc_to_tile`` at the module boundary with a
fictional affine (not a CRS conversion) so they exercise placement without
GDAL, pyproj, network, or a real world frame. Coordinate correctness belongs to
``world_frame`` fixtures (tasks #84-#86). ``fetch_tile`` is stubbed to solid colours.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import google_aerial as ga
import world_frame as wf

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


def _solid_tile(x: int, y: int, zoom: int, *, download_authorized: bool = False) -> Image.Image:
    """Deterministic RGB tile for offline sheet tests.

    @param x: tile x
    @param y: tile y
    @param zoom: zoom level (unused)
    @param download_authorized: authorization forwarded by the renderer (unused)
    @returns: 256×256 solid-colour image keyed by tile indices
    """
    del zoom, download_authorized
    return Image.new('RGB', (ga.TILE, ga.TILE), (x % 256, y % 256, 80))


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
            wf, 'transform_epsg', side_effect=AssertionError('tile_range must not call CRS'),
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
            wf, 'transform_epsg', side_effect=AssertionError('tile_range must not call CRS'),
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


class DownloadAuthorizationTest(unittest.TestCase):
    """Exercise the CLI and shared network boundary without fetching imagery."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.sheets = Path(temp.name) / 'sheets'
        for patch in (
            mock.patch.object(ga, 'SHEETS', self.sheets),
            mock.patch.object(ga, 'mc_to_tile', side_effect=_fictional_mc_to_tile),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_direct_fetch_without_authorization_never_opens_network(self):
        with mock.patch.object(ga.urllib.request, 'urlopen') as network:
            with self.assertRaisesRegex(PermissionError, '--download-authorized'):
                ga.fetch_tile(0, 0, 20)
        network.assert_not_called()

    def test_authorized_fetch_decodes_the_response(self):
        raw = io.BytesIO()
        Image.new('RGB', (256, 256), 'blue').save(raw, format='PNG')
        with mock.patch.object(ga.urllib.request, 'urlopen', return_value=io.BytesIO(raw.getvalue())) as network:
            im = ga.fetch_tile(0, 1, 20, download_authorized=True)
        self.assertEqual(im.getpixel((0, 0)), (0, 0, 255))
        self.assertEqual(network.call_args.args[0].full_url, ga.tile_url(0, 1, 20))

    def test_render_and_write_refuse_before_fetching_or_creating_output(self):
        with mock.patch.object(ga, 'fetch_tile') as fetch:
            with self.assertRaises(PermissionError):
                ga.sheet_image((0, 0, 80, 80), 20)
            with self.assertRaises(PermissionError):
                ga.write_sheet('test', (0, 0, 80, 80), 20, 1.0)
        fetch.assert_not_called()
        self.assertFalse(self.sheets.exists())

    def test_cli_requires_authorization_before_reading_overlays_or_downloading(self):
        with mock.patch.object(ga.urllib.request, 'urlopen') as network, mock.patch.object(
            ga, 'osm_outlines', side_effect=AssertionError('must refuse before reading outlines'),
        ), mock.patch('sys.stdout', new_callable=io.StringIO), mock.patch('sys.stderr', new_callable=io.StringIO) as err:
            code = ga.main(['sheet', '--at', '40,40', '--outlines', '--marks', 'missing.json'])
        self.assertEqual(code, 2)
        self.assertIn('--download-authorized', err.getvalue())
        network.assert_not_called()
        self.assertFalse(self.sheets.exists())

    def test_dry_run_describes_download_without_writes_even_with_authorization(self):
        for flags in ([], ['--download-authorized']):
            with self.subTest(flags=flags), mock.patch.object(ga.urllib.request, 'urlopen') as network, mock.patch(
                'sys.stdout', new_callable=io.StringIO,
            ) as out:
                code = ga.main(['sheet', '--at', '40,40', '--dry-run', '--outlines', '--marks', 'missing.json', *flags])
            self.assertEqual(code, 0)
            plan = json.loads(out.getvalue())
            self.assertEqual(plan['source'], ga.BASE)
            self.assertEqual(plan['output'], str(self.sheets / 'at_40_40_osm_marks.png'))
            self.assertEqual(plan['tile_count'], 4)
            self.assertEqual(plan['estimated_bytes'], 4 * 256 * 256 * 3)
            self.assertIn('compressed', plan['size_note'])
            network.assert_not_called()
            self.assertFalse(self.sheets.exists())

    def test_url_remains_available_without_authorization(self):
        with mock.patch.object(ga.urllib.request, 'urlopen') as network, mock.patch(
            'sys.stdout', new_callable=io.StringIO,
        ) as out:
            code = ga.main(['url', '--at', '40,40'])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().strip(), ga.tile_url(1, 1, 20))
        network.assert_not_called()

    def test_authorized_cli_forwards_authorization_to_every_tile(self):
        with mock.patch.object(ga, 'fetch_tile', side_effect=_solid_tile) as fetch, mock.patch(
            'sys.stdout', new_callable=io.StringIO,
        ):
            self.assertEqual(ga.main(['sheet', '--at', '40,40', '--download-authorized']), 0)
        self.assertEqual(fetch.call_count, 4)
        self.assertTrue(all(call.kwargs['download_authorized'] for call in fetch.call_args_list))
        self.assertTrue((self.sheets / 'at_40_40.png').is_file())


class MarkupSheetTest(unittest.TestCase):
    """sheet_image against stubbed ``mc_to_tile`` / ``fetch_tile`` (no network, no CRS)."""

    # Worked example: bbox (0,0,80,80), zoom 20, scale 2 → s=512, tx0=ty0=0.
    # Under _fictional_mc_to_tile, MC (x,z) → pixel ((x/40)*512, (z/40)*512).

    BBOX = (0, 0, 80, 80)
    ZOOM = 20
    SCALE = 2.0

    def setUp(self) -> None:
        self.proj = _ProjectionStub()
        patches = (
            mock.patch.object(ga, 'mc_to_tile', side_effect=self.proj),
            mock.patch.object(ga, 'fetch_tile', side_effect=_solid_tile),
            mock.patch.object(
                wf, 'transform_epsg',
                side_effect=AssertionError('markup must not call CRS'),
            ),
            mock.patch.object(
                wf, 'mc_batch_to_wgs84',
                side_effect=AssertionError('markup must not call mc_batch_to_wgs84'),
            ),
        )
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_sheet_size_covers_every_tile(self):
        im = ga.sheet_image(self.BBOX, self.ZOOM, scale=self.SCALE, download_authorized=True)
        # 3×3 tiles at scale 2 → 1536×1536
        self.assertEqual(im.size, (1536, 1536))
        # Interior of tile (0,0) / (2,2) keep stub colour (avoid bbox outline at corners).
        self.assertEqual(im.getpixel((40, 40)), (0, 0, 80))
        self.assertEqual(im.getpixel((1100, 1100)), (2, 2, 80))
        for corner in ((0.0, 0.0), (80.0, 0.0), (0.0, 80.0), (80.0, 80.0)):
            self.assertIn(corner, self.proj.all_points())
        self.assertTrue(all(z == self.ZOOM for _pts, z in self.proj.calls))

    def test_outlines_project_ring_corners(self):
        ring = [(40, 40), (80, 40), (80, 80), (40, 80), (40, 40)]
        im = ga.sheet_image(
            self.BBOX, self.ZOOM, scale=self.SCALE,
            outlines=[{'ring': ring, 'label': '441'}],
            download_authorized=True,
        )
        self.assertEqual(im.size, (1536, 1536))
        for x, z in ring:
            self.assertIn((float(x), float(z)), self.proj.all_points())
        # Without outlines, fewer projection batches (no ring points).
        bare = _ProjectionStub()
        with mock.patch.object(ga, 'mc_to_tile', side_effect=bare):
            ga.sheet_image(self.BBOX, self.ZOOM, scale=self.SCALE, download_authorized=True)
        self.assertGreater(len(self.proj.all_points()), len(bare.all_points()))

    def test_marks_project_cell_corners(self):
        im = ga.sheet_image(
            self.BBOX, self.ZOOM, scale=self.SCALE,
            marks=[(40, 40, '#f0f'), (41, 40, '#0ff')],
            download_authorized=True,
        )
        self.assertEqual(im.size, (1536, 1536))
        for p in ((40.0, 40.0), (41.0, 40.0), (41.0, 41.0), (40.0, 41.0)):
            self.assertIn(p, self.proj.all_points())
        bare = _ProjectionStub()
        with mock.patch.object(ga, 'mc_to_tile', side_effect=bare):
            ga.sheet_image(self.BBOX, self.ZOOM, scale=self.SCALE, download_authorized=True)
        self.assertGreater(len(self.proj.all_points()), len(bare.all_points()))

    def test_marks_are_translucent_over_tiles(self):
        # Cell (40,40) maps to pixel ~512 under scale 2; stub tile there is (1,1,80).
        # Magenta mark #ff00ff at alpha 115 must blend, not overwrite.
        im = ga.sheet_image(
            self.BBOX, self.ZOOM, scale=self.SCALE,
            marks=[(40, 40, '#ff00ff')],
            download_authorized=True,
        )
        # Interior of the marked cell (away from polygon edges / grid).
        px = im.getpixel((520, 520))
        self.assertNotEqual(px, (255, 0, 255))
        self.assertNotEqual(px, (1, 1, 80))
        self.assertGreater(px[0], 80)
        self.assertGreater(px[2], 80)


if __name__ == '__main__':
    unittest.main()
