"""site_kit.World is a BlockStore facade; no .mca parse path remains."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from block_store import LiveBlocks, MemoryBlocks
from site_kit import World
from test_block_store import AIR, FakeScanMcp, STONE, make_store


class TestWorldBlockStoreFacade(unittest.TestCase):
    def test_block_delegates_to_injected_store(self):
        store = MemoryBlocks({(1, 64, 2): 'stone', (3, 70, 4): 'oak_stairs[half=bottom,facing=north]'})
        w = World(store=store)
        self.assertEqual(w.block(1, 64, 2), 'minecraft:stone')
        self.assertEqual(w.block(3, 70, 4), 'minecraft:oak_stairs[facing=north,half=bottom]')
        self.assertIsNone(w.block(0, 0, 0))

    def test_uniform_air_scans_the_section_via_store(self):
        airs = {'minecraft:air', 'minecraft:cave_air'}
        # Whole section air / unloaded → True
        empty = MemoryBlocks(default=None)
        self.assertTrue(World(store=empty).uniform_air(0, 64, 0, airs))
        # Uniform air section
        air_col = {(0, y, 0): 'minecraft:air' for y in range(64, 80)}
        self.assertTrue(World(store=MemoryBlocks(air_col)).uniform_air(0, 70, 0, airs))
        # Mixed section must not skip
        mixed = {**air_col, (0, 71, 0): 'minecraft:stone'}
        self.assertFalse(World(store=MemoryBlocks(mixed)).uniform_air(0, 70, 0, airs))

    def test_ground_uses_block_reads(self):
        col = {(0, y, 0): 'minecraft:air' for y in range(61, 71)}
        col[(0, 60, 0)] = 'minecraft:stone'
        self.assertEqual(World(store=MemoryBlocks(col)).ground(0, 0, top=70, bottom=50), 60)

    def test_lazy_default_store_is_live_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            region = Path(d) / 'region'
            region.mkdir()
            mcp = FakeScanMcp({(0, 64, 0): STONE}, loaded_chunks={(0, 0)})
            with mock.patch('site_kit.resolve_region', return_value=region), \
                 mock.patch('block_store.LiveBlocks.from_world_config') as factory:
                store = LiveBlocks(region, allowed=[region], identity_check=lambda r: None,
                                   flusher=lambda: None, settle_seconds=0, mcp=mcp)
                factory.return_value = store
                w = World()
                self.assertEqual(w.region, region)
                factory.assert_not_called()  # lazy until first read
                self.assertEqual(w.block(0, 64, 0), STONE)
                factory.assert_called_once()

    def test_no_mca_reader_methods(self):
        self.assertFalse(hasattr(World(store=MemoryBlocks()), '_chunk'))
        src = Path(__file__).resolve().parent / 'site_kit.py'
        text = src.read_text()
        # The class body must not open region files or decompress chunk NBT.
        self.assertNotIn("f'r.{rx}.{rz}.mca'", text)
        self.assertNotIn('zlib.decompress', text)

    def test_live_mcp_lists_air_for_unfiltered_far_chunks(self):
        """Pin live-server behaviour: without loaded_chunks filter, far scans list air.

        Real ``block_scan_region`` returns ``minecraft:air`` for missing chunks; FakeScanMcp
        only omits them when ``loaded_chunks`` is set (contract tests for None).
        """
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            mcp = FakeScanMcp({})  # no loaded_chunks → fill box with air
            store = make_store('live', {}, tmp, mcp=mcp)
            self.assertEqual(store.read((5000, 64, 5000)), AIR)
            w = World(store=store)
            self.assertEqual(w.block(5000, 64, 5000), AIR)


class TestWorldLightViaMcp(unittest.TestCase):
    def test_light_reads_block_get_state(self):
        class LightMcp(FakeScanMcp):
            def text(self, name, args):
                if name == 'block_get_state':
                    self.calls.append((name, args))
                    return 'lightLevel: 7\nid: "minecraft:air"'
                return super().text(name, args)

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            mcp = LightMcp({(0, 64, 0): AIR}, loaded_chunks={(0, 0)})
            store = make_store('live', {(0, 64, 0): AIR}, tmp, mcp=mcp, loaded_chunks={(0, 0)})
            w = World(store=store)
            self.assertEqual(w.light(0, 64, 0), 7)
            self.assertTrue(any(n == 'block_get_state' for n, _ in mcp.calls))

    def test_light_on_memory_store_is_none(self):
        self.assertIsNone(World(store=MemoryBlocks({(0, 64, 0): AIR})).light(0, 64, 0))


if __name__ == '__main__':
    unittest.main()
