"""#111: RegionBlocks reads block state from on-disk .mca files (read-only)."""
from __future__ import annotations

import math
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from region_blocks import RegionBlocks

# Minimal NBT writer for fixtures only (production nbt_read has no write path).
_END, _BYTE, _SHORT, _INT, _LONG, _FLOAT, _DOUBLE, _BYTE_ARRAY, _STRING, _LIST, _COMPOUND, _INT_ARRAY, _LONG_ARRAY = range(13)


class _T:
    def __init__(self, kind, value):
        self.kind, self.value = kind, value


def _w(t: _T) -> bytes:
    k, v = t.kind, t.value
    if k == _BYTE:
        return struct.pack('>b', v)
    if k == _SHORT:
        return struct.pack('>h', v)
    if k == _INT:
        return struct.pack('>i', v)
    if k == _LONG:
        return struct.pack('>q', v)
    if k == _STRING:
        b = v.encode('utf-8')
        return struct.pack('>H', len(b)) + b
    if k == _LIST:
        sub, items = v
        return bytes([sub]) + struct.pack('>i', len(items)) + b''.join(_w(i) for i in items)
    if k == _COMPOUND:
        out = b''
        for name, child in v.items():
            nb = name.encode('utf-8')
            out += bytes([child.kind]) + struct.pack('>H', len(nb)) + nb + _w(child)
        return out + b'\x00'
    if k == _LONG_ARRAY:
        return struct.pack('>i', len(v)) + struct.pack(f'>{len(v)}q', *v)
    raise ValueError(k)


def _named_root(compound: _T, name: str = '') -> bytes:
    nb = name.encode('utf-8')
    return bytes([compound.kind]) + struct.pack('>H', len(nb)) + nb + _w(compound)


def _write_region(region: Path, cx: int, cz: int, sections: dict[int, list[str]]) -> None:
    """Write one chunk into ``r.{rx}.{rz}.mca`` with single-block (uniform) sections.

    @param region: region directory
    @param cx: chunk X
    @param cz: chunk Z
    @param sections: ``{section_Y: [palette states…]}``; length-1 → uniform section
    """
    region.mkdir(parents=True, exist_ok=True)
    sec_tags = []
    for sy, pal in sorted(sections.items()):
        palette = []
        for st in pal:
            name, _, props = st.partition('[')
            if not name.startswith('minecraft:'):
                name = 'minecraft:' + name
            entry = {'Name': _T(_STRING, name)}
            if props:
                prop_map = {}
                for kv in props.rstrip(']').split(','):
                    pk, pv = kv.split('=', 1)
                    prop_map[pk] = _T(_STRING, pv)
                entry['Properties'] = _T(_COMPOUND, prop_map)
            palette.append(_T(_COMPOUND, entry))
        bs = {'palette': _T(_LIST, (_COMPOUND, palette))}
        if len(pal) > 1:
            bits = max(4, math.ceil(math.log2(len(pal))))
            per = 64 // bits
            # All index 0 for simplicity when testing multi-palette presence.
            n_longs = (4096 + per - 1) // per
            bs['data'] = _T(_LONG_ARRAY, [0] * n_longs)
        sec_tags.append(_T(_COMPOUND, {
            'Y': _T(_BYTE, sy if -128 <= sy <= 127 else sy),
            'block_states': _T(_COMPOUND, bs),
        }))
    root = _T(_COMPOUND, {
        'DataVersion': _T(_INT, 4903),
        'xPos': _T(_INT, cx),
        'zPos': _T(_INT, cz),
        'sections': _T(_LIST, (_COMPOUND, sec_tags)),
    })
    payload = zlib.compress(_named_root(root))
    body = struct.pack('>I', len(payload) + 1) + bytes([2]) + payload
    # Pad to 4096-byte sectors after the 8 KiB header.
    sector_bytes = body + b'\x00' * ((4096 - (len(body) % 4096)) % 4096)
    n_sectors = len(sector_bytes) // 4096
    header = bytearray(8192)
    idx = (cx & 31) + (cz & 31) * 32
    # Offset in sectors from start of file; first data sector is 2.
    struct.pack_into('>I', header, idx * 4, (2 << 8) | n_sectors)
    rx, rz = cx >> 5, cz >> 5
    (region / f'r.{rx}.{rz}.mca').write_bytes(bytes(header) + sector_bytes)


class RegionBlocksReadTest(unittest.TestCase):
    def test_reads_uniform_section_from_mca(self):
        """#111: after a save, planning reads stone from the region file without MCP."""
        with tempfile.TemporaryDirectory() as td:
            region = Path(td) / 'region'
            # Section Y=4 → block Y 64..79; uniform stone.
            _write_region(region, 0, 0, {4: ['minecraft:stone']})
            store = RegionBlocks(region)
            self.assertEqual(store.read((3, 70, 5)), 'minecraft:stone')
            many = store.read_many([(3, 70, 5), (0, 64, 0), (1000, 64, 1000)])
            self.assertEqual(many[(3, 70, 5)], 'minecraft:stone')
            self.assertEqual(many[(0, 64, 0)], 'minecraft:stone')
            self.assertIsNone(many[(1000, 64, 1000)])  # missing chunk
            # Empty section in an existing chunk → air.
            self.assertEqual(store.read((0, 16, 0)), 'minecraft:air')
            # Read-only: no write API.
            self.assertFalse(hasattr(store, 'write'))
            self.assertFalse(hasattr(store, 'write_many'))


if __name__ == '__main__':
    unittest.main()
