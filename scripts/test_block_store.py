"""BlockStore contract: MemoryBlocks and MCP-backed LiveBlocks (fake scan text) behave alike."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from block_store import (BlockStoreError, LiveBlocks, MemoryBlocks, StaleRead, aabb_tile_lower_bound,
                         canonical_state, format_block_scan_text, parse_block_scan_text, same_state,
                         scan_tiles)

STONE = 'minecraft:stone'
STAIRS = 'minecraft:oak_stairs[facing=north,half=bottom]'
AIR = 'minecraft:air'

# Recorded-shape sample from a live block_scan_region (task #49 / #56 probe).
SAMPLE_STAIRS_SCAN = '''\
[1]:
  - position:
    x: 6128
    y: 80
    z: -5546
    state:
      id: "minecraft:oak_stairs"
      lightLevel: 0
      hardness: 0
      hasBlockEntity: false
      properties:
        waterlogged: "false"
        shape: straight
        facing: east
        half: top
'''

# Truncated live block_scan_region (bakeoff 2026-10-23 parity probe): two air blocks.
SAMPLE_AIR_TILE = '''\
[2]:
  - position:
    x: 6128
    y: 50
    z: -5546
    state:
      id: "minecraft:air"
      lightLevel: 0
      hardness: 0
      hasBlockEntity: false
      properties:
  - position:
    x: 6129
    y: 50
    z: -5546
    state:
      id: "minecraft:air"
      lightLevel: 0
      hardness: 0
      hasBlockEntity: false
      properties:
'''

# Hand-built fixture (not a recorded capture) — air + stone for mixed parse.
FIXTURE_AIR_AND_STONE_TILE = '''\
[2]:
  - position:
    x: 0
    y: 64
    z: 0
    state:
      id: "minecraft:air"
      lightLevel: 15
      hardness: 0
      hasBlockEntity: false
      properties:
  - position:
    x: 1
    y: 64
    z: 0
    state:
      id: "minecraft:stone"
      lightLevel: 0
      hardness: 0
      hasBlockEntity: false
      properties:
'''


WORLD = {
    (0, 64, 0): STONE,
    (1, 64, 0): STAIRS,
    (-1, 64, -1): 'minecraft:dirt',
    (15, 70, 15): 'minecraft:glass',
    (16, 70, 16): 'minecraft:glowstone',
    (511, 64, 0): 'minecraft:gravel',
    (512, 64, 0): 'minecraft:sand',
}


class FakeScanMcp:
    """MCP stand-in: ``text('block_scan_region', …)`` from an in-memory world.

    @param blocks: ``{pos: state}`` for loaded positions
    @param loaded_chunks: optional ``{(cx, cz), …}``; positions outside are omitted (→ None)
    @param max_volume: refuse scans larger than this (default 65536)
    """

    def __init__(self, blocks: dict, *, loaded_chunks: set | None = None,
                 max_volume: int = 65536) -> None:
        self.blocks = {tuple(p): canonical_state(s) for p, s in blocks.items()}
        self.loaded_chunks = loaded_chunks
        self.max_volume = max_volume
        self.calls: list[tuple[str, dict]] = []

    def text(self, name: str, args: dict) -> str:
        self.calls.append((name, args))
        if name != 'block_scan_region':
            raise AssertionError(f'unexpected tool {name}')
        box = args['box']
        x0, y0, z0 = (int(box['from'][k]) for k in 'xyz')
        x1, y1, z1 = (int(box['to'][k]) for k in 'xyz')
        vol = (x1 - x0 + 1) * (y1 - y0 + 1) * (z1 - z0 + 1)
        if vol > self.max_volume:
            raise RuntimeError(f'volume {vol} exceeds {self.max_volume}')
        limit = int(args.get('limit', 1024))
        found: dict = {}
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    if self.loaded_chunks is not None and (x >> 4, z >> 4) not in self.loaded_chunks:
                        continue
                    pos = (x, y, z)
                    found[pos] = self.blocks.get(pos, AIR)
                    if len(found) >= limit:
                        return format_block_scan_text(found)
        return format_block_scan_text(found)

    def call(self, name: str, args: dict):
        self.calls.append((name, args))
        return {'content': [{'type': 'text', 'text': 'ok'}]}


def make_store(kind: str, blocks: dict, tmp: Path, **kw):
    """MemoryBlocks or MCP LiveBlocks over the same ``blocks`` map."""
    if kind == 'memory':
        return MemoryBlocks(blocks)
    region = tmp / 'saves' / 'w' / 'region'
    region.mkdir(parents=True, exist_ok=True)
    mcp = kw.get('mcp') or FakeScanMcp(blocks, loaded_chunks=kw.get('loaded_chunks'))
    return LiveBlocks(region, allowed=[region], identity_check=lambda r: None,
                      flusher=kw.get('flusher', lambda: None), settle_seconds=0, mcp=mcp)


class BlockStoreContract:
    kind = ''

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.store = make_store(self.kind, WORLD, self.tmp)

    def test_read_returns_stored_state_with_sorted_properties(self):
        self.assertEqual(self.store.read((0, 64, 0)), STONE)
        self.assertEqual(self.store.read((1, 64, 0)), 'minecraft:oak_stairs[facing=north,half=bottom]')

    def test_unloaded_chunk_reads_none(self):
        self.assertIsNone(self.store.read((5000, 64, 5000)))

    def test_negative_coordinates_and_borders(self):
        for pos, state in WORLD.items():
            self.assertEqual(self.store.read(pos), state, pos)

    def test_read_many_equals_reads_and_keeps_keys(self):
        positions = list(WORLD) + [(5000, 64, 5000)]
        got = self.store.read_many(positions)
        self.assertEqual(set(got), set(positions))
        self.assertEqual(dict(got), {p: self.store.read(p) for p in positions})

    def test_read_many_empty(self):
        self.assertEqual(dict(self.store.read_many([])), {})

    def test_flush_is_callable_and_reads_still_work(self):
        self.store.flush()
        self.assertEqual(self.store.read((0, 64, 0)), STONE)


class TestMemoryContract(BlockStoreContract, unittest.TestCase):
    kind = 'memory'

    def test_default_state_for_unwritten_positions(self):
        store = MemoryBlocks(default='air')
        self.assertEqual(store.read((1, 2, 3)), 'minecraft:air')

    def test_write_canonicalises_and_flush_counts(self):
        store = MemoryBlocks()
        store.write((0, 0, 0), 'oak_stairs[half=bottom,facing=north]')
        self.assertEqual(store.read((0, 0, 0)), 'minecraft:oak_stairs[facing=north,half=bottom]')
        store.flush()
        store.flush()
        self.assertEqual(store.flush_count, 2)

    def test_strict_flush_refuses_reads_after_write(self):
        store = MemoryBlocks(strict_flush=True)
        store.write((0, 0, 0), STONE)
        with self.assertRaises(StaleRead):
            store.read((0, 0, 0))
        store.flush()
        self.assertEqual(store.read((0, 0, 0)), STONE)


class TestLiveMcpContract(BlockStoreContract, unittest.TestCase):
    kind = 'live'

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        # Only chunks that appear in WORLD are loaded; distant reads stay None.
        loaded = {(x >> 4, z >> 4) for x, _y, z in WORLD}
        self.mcp = FakeScanMcp(WORLD, loaded_chunks=loaded)
        self.store = make_store('live', WORLD, self.tmp, mcp=self.mcp, loaded_chunks=loaded)

    def test_air_in_loaded_chunk_is_air(self):
        self.assertEqual(self.store.read((2, 64, 2)), AIR)

    def test_read_after_write_refused_until_flush(self):
        self.store.mark_written()
        with self.assertRaises(StaleRead):
            self.store.read((0, 64, 0))
        with self.assertRaises(StaleRead):
            self.store.read_many([(0, 64, 0)])
        self.store.flush()
        self.assertEqual(self.store.read((0, 64, 0)), STONE)

    def test_flush_calls_flusher_and_clears_cache(self):
        calls = []
        mcp = FakeScanMcp(WORLD, loaded_chunks={(0, 0)})
        store = make_store('live', {(0, 64, 0): STONE}, self.tmp / 'f',
                           mcp=mcp, flusher=lambda: calls.append('save'),
                           loaded_chunks={(0, 0)})
        self.assertEqual(store.read((0, 64, 0)), STONE)
        n_before = len(mcp.calls)
        store.flush()
        self.assertEqual(calls, ['save'])
        mcp.blocks[(0, 64, 0)] = 'minecraft:clay'
        self.assertEqual(store.read((0, 64, 0)), 'minecraft:clay')
        self.assertGreater(len(mcp.calls), n_before)

    def test_rejects_region_that_is_not_active_or_bakeoff(self):
        region = self.tmp / 'snapshot' / 'region'
        region.mkdir(parents=True)
        with self.assertRaises(BlockStoreError):
            LiveBlocks(region, allowed=[self.tmp / 'saves' / 'w' / 'region'],
                       identity_check=lambda r: None, flusher=lambda: None,
                       mcp=FakeScanMcp({}))

    def test_identity_check_failure_aborts_construction(self):
        region = self.tmp / 'id_fail' / 'region'
        region.mkdir(parents=True)

        def refuse(_r):
            raise BlockStoreError('open world differs')

        with self.assertRaises(BlockStoreError):
            LiveBlocks(region, allowed=[region], identity_check=refuse, flusher=lambda: None,
                       mcp=FakeScanMcp({}))

    def test_identity_check_runs_at_construction(self):
        region = self.tmp / 'id_ok' / 'region'
        region.mkdir(parents=True)
        seen = []
        LiveBlocks(region, allowed=[region], identity_check=seen.append, flusher=lambda: None,
                   mcp=FakeScanMcp({}))
        self.assertEqual(seen, [region])

    def test_read_many_tiles_tall_volumes(self):
        """A tall AABB is split into volume≤65536 tiles; each scan raises limit to the tile size."""
        # 128×8×128 = 131072 → two 128×4×128 tiles
        blocks = {(x, y, z): STONE
                  for x in (0, 127) for y in (0, 3, 4, 7) for z in (0, 127)}
        loaded = {(0, 0)}
        mcp = FakeScanMcp(blocks, loaded_chunks=loaded)
        store = make_store('live', blocks, self.tmp / 'tile', mcp=mcp, loaded_chunks=loaded)
        positions = [(0, y, 0) for y in range(8)] + [(127, y, 127) for y in range(8)]
        got = store.read_many(positions)
        self.assertEqual(got[(0, 0, 0)], STONE)
        self.assertEqual(got[(0, 7, 0)], STONE)
        scan_calls = [a for n, a in mcp.calls if n == 'block_scan_region']
        self.assertGreaterEqual(len(scan_calls), 2)
        for args in scan_calls:
            box = args['box']
            x0, y0, z0 = (int(box['from'][k]) for k in 'xyz')
            x1, y1, z1 = (int(box['to'][k]) for k in 'xyz')
            vol = (x1 - x0 + 1) * (y1 - y0 + 1) * (z1 - z0 + 1)
            self.assertLessEqual(vol, 65536)
            self.assertEqual(args['limit'], vol)

    def test_scan_uses_raised_limit_not_default_1024(self):
        mcp = FakeScanMcp({(0, 64, 0): STONE}, loaded_chunks={(0, 0)})
        store = make_store('live', {(0, 64, 0): STONE}, self.tmp / 'lim',
                           mcp=mcp, loaded_chunks={(0, 0)})
        store.read((0, 64, 0))
        args = mcp.calls[0][1]
        # single-block tile: limit == volume (never the tool default of 1024)
        self.assertEqual(args['limit'], 1)

    def test_far_apart_read_many_falls_back_to_per_chunk(self):
        """Sparse positions whose parent AABB needs >64 tiles scan per chunk, not the AABB."""
        near = (0, 64, 0)
        far = (100_000, 64, 100_000)
        blocks = {near: STONE, far: 'minecraft:dirt'}
        loaded = {(near[0] >> 4, near[2] >> 4), (far[0] >> 4, far[2] >> 4)}
        mcp = FakeScanMcp(blocks, loaded_chunks=loaded)
        store = make_store('live', blocks, self.tmp / 'sparse', mcp=mcp, loaded_chunks=loaded)
        got = store.read_many([near, far])
        self.assertEqual(got[near], STONE)
        self.assertEqual(got[far], 'minecraft:dirt')
        scan_calls = [a for n, a in mcp.calls if n == 'block_scan_region']
        # Per-chunk fallback: two tiny scans, not millions of empty AABB tiles.
        self.assertEqual(len(scan_calls), 2)
        for args in scan_calls:
            box = args['box']
            x0, y0, z0 = (int(box['from'][k]) for k in 'xyz')
            x1, y1, z1 = (int(box['to'][k]) for k in 'xyz')
            vol = (x1 - x0 + 1) * (y1 - y0 + 1) * (z1 - z0 + 1)
            self.assertLessEqual(vol, 65536)
            self.assertLessEqual(vol, 16)  # single-block (or tiny) per chunk group


class TestParseBlockScanText(unittest.TestCase):
    def test_recorded_stairs_sample(self):
        got = parse_block_scan_text(SAMPLE_STAIRS_SCAN)
        self.assertEqual(got[(6128, 80, -5546)],
                         'minecraft:oak_stairs[facing=east,half=top,shape=straight,waterlogged=false]')

    def test_recorded_air_tile(self):
        got = parse_block_scan_text(SAMPLE_AIR_TILE)
        self.assertEqual(got[(6128, 50, -5546)], AIR)
        self.assertEqual(got[(6129, 50, -5546)], AIR)

    def test_fixture_air_and_stone_tile(self):
        got = parse_block_scan_text(FIXTURE_AIR_AND_STONE_TILE)
        self.assertEqual(got[(0, 64, 0)], AIR)
        self.assertEqual(got[(1, 64, 0)], STONE)

    def test_empty_list(self):
        self.assertEqual(parse_block_scan_text('[]'), {})

    def test_round_trip_format(self):
        blocks = {(1, 2, 3): STAIRS, (0, 0, 0): AIR}
        text = format_block_scan_text(blocks)
        self.assertEqual(parse_block_scan_text(text),
                         {p: canonical_state(s) for p, s in blocks.items()})


class TestScanTiles(unittest.TestCase):
    def test_small_box_is_one_tile(self):
        self.assertEqual(list(scan_tiles(0, 0, 0, 1, 1, 1)), [((0, 0, 0), (1, 1, 1))])

    def test_tall_cell_slices_on_y(self):
        # 128×100×128 needs 25 tiles of height 4
        tiles = list(scan_tiles(0, 0, 0, 127, 99, 127))
        self.assertEqual(len(tiles), 25)
        for (lo, hi) in tiles:
            vol = (hi[0] - lo[0] + 1) * (hi[1] - lo[1] + 1) * (hi[2] - lo[2] + 1)
            self.assertLessEqual(vol, 65536)
        self.assertEqual(tiles[0], ((0, 0, 0), (127, 3, 127)))
        self.assertEqual(tiles[-1], ((0, 96, 0), (127, 99, 127)))

    def test_dz_greater_than_cap_slices_z_without_recursion(self):
        """Regression: dz > cap used to set x_span=1 and recurse forever."""
        cap = 100
        tiles = list(scan_tiles(0, 0, 0, 0, 0, cap + 50, cap=cap))
        self.assertGreater(len(tiles), 1)
        for lo, hi in tiles:
            vol = (hi[0] - lo[0] + 1) * (hi[1] - lo[1] + 1) * (hi[2] - lo[2] + 1)
            self.assertLessEqual(vol, cap)
        # Coverage: every z from 0..cap+50 appears in some tile
        covered = set()
        for lo, hi in tiles:
            covered.update(range(lo[2], hi[2] + 1))
        self.assertEqual(covered, set(range(cap + 51)))

    def test_wide_footprint_slices_without_recursion(self):
        """Footprint ≫ cap (wide X and Z) must terminate; every tile ≤ cap."""
        # 400×1×400 = 160_000 > 65536 — needs horizontal slicing (was a crash path
        # when only one axis exceeded the cap).
        tiles = list(scan_tiles(0, 64, 0, 399, 64, 399, cap=65536))
        self.assertGreater(len(tiles), 1)
        for lo, hi in tiles:
            vol = (hi[0] - lo[0] + 1) * (hi[1] - lo[1] + 1) * (hi[2] - lo[2] + 1)
            self.assertLessEqual(vol, 65536)
        self.assertGreater(aabb_tile_lower_bound(0, 64, 0, 100_000, 64, 100_000), 64)


class TestStateHelpers(unittest.TestCase):
    def test_canonical_state(self):
        self.assertEqual(canonical_state('stone'), 'minecraft:stone')
        self.assertEqual(canonical_state('oak_stairs[half=bottom,facing=north]'),
                         'minecraft:oak_stairs[facing=north,half=bottom]')

    def test_same_state_ignores_unplanned_and_waterlogged(self):
        self.assertTrue(same_state('oak_stairs[facing=north]', 'minecraft:oak_stairs[facing=north,half=top]'))
        self.assertTrue(same_state('oak_stairs[waterlogged=true]', 'minecraft:oak_stairs[waterlogged=false]'))
        self.assertFalse(same_state('stone', None))
        self.assertFalse(same_state('stone', 'minecraft:dirt'))

    def test_same_state_ignores_game_derived_connections(self):
        self.assertTrue(same_state('glass_pane[east=true,west=true]',
                                   'minecraft:glass_pane[east=false,north=true,south=false,waterlogged=false,west=true]'))
        self.assertTrue(same_state('iron_bars[north=true]', 'minecraft:iron_bars[north=false,south=true]'))
        self.assertTrue(same_state('oak_fence[east=true]', 'minecraft:oak_fence[east=false,north=true]'))
        self.assertTrue(same_state('stone_brick_wall[up=true,north=low]',
                                   'minecraft:stone_brick_wall[up=false,north=none]'))
        self.assertTrue(same_state('copper_bars[north=true]', 'minecraft:copper_bars[north=false]'))
        self.assertFalse(same_state('glass_pane[east=true]', 'minecraft:stained_glass_pane[east=true]'))

    def test_site_kit_same_delegates(self):
        import site_kit
        self.assertTrue(site_kit.same('glass_pane[east=true,west=true]',
                                      'minecraft:glass_pane[east=false,north=true,west=true]'))
        self.assertFalse(site_kit.same('oak_fence_gate[open=true]', 'minecraft:oak_fence_gate[open=false]'))

    def test_same_state_keeps_other_properties_on_connectables(self):
        self.assertFalse(same_state('oak_fence_gate[facing=north,open=true]',
                                    'minecraft:oak_fence_gate[facing=north,open=false]'))
        self.assertFalse(same_state('oak_fence_gate[facing=north]', 'minecraft:oak_fence_gate[facing=south]'))


if __name__ == '__main__':
    unittest.main()
