#!/usr/bin/env python3
"""Scan-call bounds for #93 live-read prefetch sweeps (no Minecraft).

Without prefetch, each ``World.block()`` becomes one MCP ``block_scan_region``.
These tests warm known position sets first and assert scan counts stay tiled.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from block_store import LiveBlocks
from site_kit import World, warm_reads
from test_block_store import AIR, FakeScanMcp, STONE


def _live(blocks: dict) -> tuple[World, FakeScanMcp, tempfile.TemporaryDirectory]:
    """World over LiveBlocks + FakeScanMcp; caller must keep ``tmp`` alive."""
    tmp = tempfile.TemporaryDirectory()
    region = Path(tmp.name) / 'saves' / 'w' / 'region'
    region.mkdir(parents=True)
    mcp = FakeScanMcp(blocks)
    store = LiveBlocks(
        region, allowed=[region], identity_check=lambda r: None,
        flusher=lambda: None, settle_seconds=0, mcp=mcp,
    )
    return World(store=store), mcp, tmp


def _scans(mcp: FakeScanMcp) -> int:
    return sum(1 for name, _ in mcp.calls if name == 'block_scan_region')


def _column(x: int, z: int, ground: int = 50, y0: int = 0, y1: int = 130) -> dict:
    return {
        (x, y, z): (STONE if y <= ground else AIR)
        for y in range(y0, y1 + 1)
    }


class WarmReadsTest(unittest.TestCase):
    def test_stub_without_prefetch_is_noop(self):
        class Stub:
            def block(self, x, y, z):
                return AIR

        warm_reads(Stub(), [(0, 64, 0)])  # must not raise


class GeoPrefetchTest(unittest.TestCase):
    def test_lidar_top_column_bounds_scans(self):
        import geo
        world, mcp, tmp = _live(_column(0, 0))
        self.addCleanup(tmp.cleanup)
        self.assertEqual(geo.lidar_top(world, 0, 0), 50)
        self.assertLessEqual(_scans(mcp), 4, f'too many scans: {_scans(mcp)}')

    def test_probe_bbox_bounds_scans(self):
        import geo
        blocks = {}
        for x in range(0, 8):
            for z in range(0, 8):
                blocks.update(_column(x, z))
        world, mcp, tmp = _live(blocks)
        self.addCleanup(tmp.cleanup)
        store = world._blocks()
        strip_cap = 8 * 201  # one X × Z-width 8 × Y 0..200
        prefetch_sizes: list[int] = []
        cache_peaks: list[int] = []
        real_prefetch = world.prefetch

        def spy_prefetch(positions):
            pos_list = list(positions)
            prefetch_sizes.append(len(pos_list))
            real_prefetch(pos_list)
            cache_peaks.append(len(store._cache))

        world.prefetch = spy_prefetch
        rec = geo.probe_bbox(world, 0, 0, 7, 7)
        self.assertEqual(rec['columns'], 64)
        self.assertEqual(rec['lidar_top_y']['median'], 50)
        # Eight X-strips, each tiled — not one MCP scan per Y walk.
        self.assertLessEqual(_scans(mcp), 32, f'too many scans: {_scans(mcp)}')
        self.assertTrue(prefetch_sizes, 'expected strip warm_reads calls')
        self.assertTrue(
            all(n <= strip_cap for n in prefetch_sizes),
            f'prefetch exceeded strip cap {strip_cap}: {prefetch_sizes}',
        )
        self.assertTrue(
            all(n <= strip_cap for n in cache_peaks),
            f'cache peak exceeded strip cap {strip_cap}: {cache_peaks}',
        )
        self.assertEqual(len(store._cache), 0, 'strips should be forgotten after use')


class SiteWalkPrefetchTest(unittest.TestCase):
    def test_walk_box_bounds_scans(self):
        import site_walk
        blocks = {}
        for x in range(0, 6):
            for z in range(0, 6):
                for y in range(40, 56):
                    if y == 49:
                        blocks[(x, y, z)] = STONE
                    else:
                        blocks[(x, y, z)] = AIR
        world, mcp, tmp = _live(blocks)
        self.addCleanup(tmp.cleanup)
        path = site_walk.walk(world, (1, 50, 1), (4, 50, 4), (0, 0, 5, 5))
        self.assertIsNotNone(path)
        self.assertLessEqual(_scans(mcp), 16, f'too many scans: {_scans(mcp)}')


class MaskUndoPrefetchTest(unittest.TestCase):
    def test_conflict_check_bounds_scans(self):
        import mask_undo
        import pass_mask as pm

        placements = {(100 + i, 60, 200): 'minecraft:oak_leaves' for i in range(40)}
        blocks = {p: st for p, st in placements.items()}
        world, mcp, tmp = _live(blocks)
        self.addCleanup(tmp.cleanup)
        mask = pm.build_mask('trees', (0, 0), 'tp', placements, lambda p: True)
        path = Path(tmp.name) / 'trees-0_0.mask.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        pm.write_mask(mask, path.parent)
        # Point mask_path at our temp file; skip MCP flush; stop after conflict loop via dry.
        with mock.patch.object(mask_undo, 'mask_path', lambda *a, **k: path), \
             mock.patch.object(mask_undo, 'World', lambda: world), \
             mock.patch.object(mask_undo, 'shared_positions', lambda m: set()), \
             mock.patch('mc_paced.Paced') as paced:
            paced.return_value.call.return_value = None
            # Dry run with matching live blocks → plans air (apply dry).
            with mock.patch.object(mask_undo, 'apply', lambda *a, **k: None):
                self.assertEqual(mask_undo.run((0, 0), 'trees', live=False), 0)
        self.assertLessEqual(_scans(mcp), 16, f'too many scans: {_scans(mcp)}')


class CellMarkerPrefetchTest(unittest.TestCase):
    def test_perimeter_band_clear_positions_bounds_scans(self):
        import cell_marker as cm

        tx, tz = 0, 0
        x0, z0, x1, z1 = cm.cell_bounds(tx, tz)
        base = 50
        y_ring = base + cm.HEIGHT_ABOVE
        blocks = {}
        # Ground samples (step 16) + perimeter band.
        for x in range(x0, x1 + 1, cm.GROUND_SAMPLE_STEP):
            for z in range(z0, z1 + 1, cm.GROUND_SAMPLE_STEP):
                blocks.update(_column(x, z, ground=base, y0=40, y1=90))
        for x, z in cm.perimeter_columns(tx, tz):
            for y in cm.marker_y_band(base):
                blocks[(x, y, z)] = AIR
            blocks[(x, y_ring, z)] = 'minecraft:black_wool'
        world, mcp, tmp = _live(blocks)
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(cm, 'reference_ground_y', return_value=base):
            got = cm._clear_positions(tx, tz, world, {}, extra_y=None)
        self.assertIn((x0, y_ring, z0), got)
        # 508 perimeter × ~6 Y without prefetch ≈ 3000 scans; tiled AABB stays small.
        self.assertLessEqual(_scans(mcp), 32, f'too many scans: {_scans(mcp)}')


if __name__ == '__main__':
    unittest.main()
