"""BlockStore contract: MemoryBlocks and LiveBlocks (over a tiny hand-built .mca) behave alike."""
from __future__ import annotations

import builtins
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

import nbt_min as N
from block_store import (BlockStoreError, LiveBlocks, MemoryBlocks, StaleRead, canonical_state,
                         same_state)

STONE = 'minecraft:stone'
STAIRS = 'minecraft:oak_stairs[facing=north,half=bottom]'


def _tag_state(state: str) -> N.Tag:
    name, _, rest = state.partition('[')
    fields = {'Name': N.Tag(N.STRING, name)}
    if rest:
        props = dict(kv.split('=') for kv in rest.rstrip(']').split(','))
        fields['Properties'] = N.Tag(N.COMPOUND, {k: N.Tag(N.STRING, v) for k, v in props.items()})
    return N.Tag(N.COMPOUND, fields)


def _section(sy: int, blocks: dict, uniform: str | None) -> N.Tag:
    """Section ``sy``: ``blocks`` maps (lx, ly, lz) to a state; the rest is air (or ``uniform``)."""
    if uniform is not None:
        palette, data = [uniform], None
    else:
        palette = ['minecraft:air'] + sorted({s for s in blocks.values()} - {'minecraft:air'})
        bits = max(4, (len(palette) - 1).bit_length())
        per = 64 // bits
        longs = [0] * (-(-4096 // per))
        for (lx, ly, lz), st in blocks.items():
            i = ly * 256 + lz * 16 + lx
            longs[i // per] |= palette.index(st) << ((i % per) * bits)
        data = [v - (1 << 64) if v >= 1 << 63 else v for v in longs]
    bs = {'palette': N.Tag(N.LIST, (N.COMPOUND, [_tag_state(s) for s in palette]))}
    if data is not None:
        bs['data'] = N.Tag(N.LONG_ARRAY, data)
    return N.Tag(N.COMPOUND, {'Y': N.Tag(N.BYTE, sy), 'block_states': N.Tag(N.COMPOUND, bs)})


def build_region(region: Path, blocks: dict, uniform: dict | None = None) -> None:
    """Write ``r.<rx>.<rz>.mca`` files for ``{(x, y, z): state}`` (uniform: ``{(cx, sy, cz): state}``)."""
    region.mkdir(parents=True, exist_ok=True)
    chunks: dict = {}
    for (x, y, z), st in blocks.items():
        chunks.setdefault((x >> 4, z >> 4), {}).setdefault(y >> 4, {})[(x & 15, y & 15, z & 15)] = st
    for (cx, sy, cz) in (uniform or {}):
        chunks.setdefault((cx, cz), {}).setdefault(sy, {})
    files: dict = {}
    for (cx, cz), secs in chunks.items():
        tags = [_section(sy, b, (uniform or {}).get((cx, sy, cz))) for sy, b in sorted(secs.items())]
        root = N.Tag(N.COMPOUND, {'sections': N.Tag(N.LIST, (N.COMPOUND, tags))})
        raw = bytes([N.COMPOUND]) + struct.pack('>H', 0) + N._write(root)
        comp = zlib.compress(raw)
        body = struct.pack('>I', len(comp) + 1) + b'\x02' + comp
        files.setdefault((cx >> 5, cz >> 5), {})[(cx & 31) + (cz & 31) * 32] = body
    for (rx, rz), entries in files.items():
        header, payload = bytearray(8192), b''
        for idx, body in sorted(entries.items()):
            sectors = -(-len(body) // 4096)
            struct.pack_into('>I', header, idx * 4, ((2 + len(payload) // 4096) << 8) | sectors)
            payload += body.ljust(sectors * 4096, b'\0')
        (region / f'r.{rx}.{rz}.mca').write_bytes(bytes(header) + payload)


def make_store(kind: str, blocks: dict, tmp: Path, **kw):
    """The same world as MemoryBlocks or as LiveBlocks over a built region."""
    if kind == 'memory':
        return MemoryBlocks(blocks)
    region = tmp / 'saves' / 'w' / 'region'
    build_region(region, blocks, kw.get('uniform'))
    return LiveBlocks(region, allowed=[region], identity_check=lambda r: None,
                      flusher=kw.get('flusher', lambda: None), settle_seconds=0)


WORLD = {
    (0, 64, 0): STONE,
    (1, 64, 0): STAIRS,
    (-1, 64, -1): 'minecraft:dirt',           # negative coordinates: chunk (-1, -1), region (-1, -1)
    (15, 70, 15): 'minecraft:glass',
    (16, 70, 16): 'minecraft:glowstone',      # next chunk over
    (511, 64, 0): 'minecraft:gravel',         # last column of region 0
    (512, 64, 0): 'minecraft:sand',           # first column of region 1
}


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


class TestLiveContract(BlockStoreContract, unittest.TestCase):
    kind = 'live'

    def test_missing_section_in_loaded_chunk_is_air(self):
        self.assertEqual(self.store.read((2, 200, 2)), 'minecraft:air')

    def test_uniform_single_palette_section_returns_palette_zero(self):
        store = make_store('live', {}, self.tmp / 'u', uniform={(0, 4, 0): 'minecraft:water'})
        self.assertEqual(store.read((3, 70, 3)), 'minecraft:water')

    def test_each_region_file_is_read_once(self):
        reads = []
        real = Path.read_bytes

        def counting(path):
            reads.append(path.name)
            return real(path)

        with mock.patch.object(Path, 'read_bytes', counting):
            store = make_store('live', WORLD, self.tmp / 'c')
            store.read_many(list(WORLD) * 3)
        self.assertEqual(sorted(reads), sorted(set(reads)))
        self.assertEqual(set(reads), {'r.0.0.mca', 'r.1.0.mca', 'r.-1.-1.mca'})

    def test_cache_is_stale_until_flush(self):
        region = self.tmp / 'saves' / 'w' / 'region'
        self.assertEqual(self.store.read((0, 64, 0)), STONE)
        build_region(region, {**WORLD, (0, 64, 0): 'minecraft:clay'})
        self.assertEqual(self.store.read((0, 64, 0)), STONE)          # cached chunk
        self.store.flush()
        self.assertEqual(self.store.read((0, 64, 0)), 'minecraft:clay')

    def test_read_after_write_refused_until_flush(self):
        self.store.mark_written()
        with self.assertRaises(StaleRead):
            self.store.read((0, 64, 0))
        with self.assertRaises(StaleRead):
            self.store.read_many([(0, 64, 0)])
        self.store.flush()
        self.assertEqual(self.store.read((0, 64, 0)), STONE)

    def test_flush_calls_flusher_then_sleeps(self):
        calls = []
        store = make_store('live', WORLD, self.tmp / 'f', flusher=lambda: calls.append('save'))
        store.flush()
        self.assertEqual(calls, ['save'])

    def test_rejects_region_that_is_not_active_or_bakeoff(self):
        region = self.tmp / 'snapshot' / 'region'
        build_region(region, WORLD)
        with self.assertRaises(BlockStoreError):
            LiveBlocks(region, allowed=[self.tmp / 'saves' / 'w' / 'region'],
                       identity_check=lambda r: None, flusher=lambda: None)

    def test_identity_check_failure_aborts_construction(self):
        region = self.tmp / 'saves' / 'w' / 'region'

        def refuse(_r):
            raise BlockStoreError('open world differs')

        with self.assertRaises(BlockStoreError):
            LiveBlocks(region, allowed=[region], identity_check=refuse, flusher=lambda: None)

    def test_identity_check_runs_at_construction(self):
        region = self.tmp / 'saves' / 'w' / 'region'
        seen = []
        LiveBlocks(region, allowed=[region], identity_check=seen.append, flusher=lambda: None)
        self.assertEqual(seen, [region])

    def test_never_opens_a_file_for_writing(self):
        real_open = builtins.open
        modes = []

        def guarded(file, mode='r', *a, **k):
            modes.append(mode)
            if any(c in mode for c in 'wax+'):
                raise AssertionError(f'write-mode open {file!r} {mode!r}')
            return real_open(file, mode, *a, **k)

        with mock.patch.object(Path, 'write_bytes', side_effect=AssertionError('write_bytes')), \
                mock.patch.object(Path, 'write_text', side_effect=AssertionError('write_text')), \
                mock.patch('io.open', guarded):
            self.store.read_many(list(WORLD))
            self.store.flush()
            self.store.read((0, 64, 0))
        self.assertTrue(modes)
        self.assertTrue(all(m == 'rb' for m in modes), modes)


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
        # the game re-derives connections from neighbours after placement (R-006: 2 panes)
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
        # gates are not connectables: facing/open/in_wall are the builder's choice
        self.assertFalse(same_state('oak_fence_gate[facing=north,open=true]',
                                    'minecraft:oak_fence_gate[facing=north,open=false]'))
        self.assertFalse(same_state('oak_fence_gate[facing=north]', 'minecraft:oak_fence_gate[facing=south]'))


if __name__ == '__main__':
    unittest.main()
