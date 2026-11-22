"""#123: parse Java structure ``.nbt`` templates (before_<plan> backups)."""
from __future__ import annotations

import gzip
import struct
import tempfile
import unittest
from pathlib import Path

from structure_nbt import absolute_blocks, palette_state, read_structure_file, structure_file_path

# Minimal NBT writer for fixtures (production nbt_read has no write path).
_END, _BYTE, _SHORT, _INT, _LONG, _FLOAT, _DOUBLE, _BYTE_ARRAY, _STRING, _LIST, _COMPOUND = range(11)


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
    raise ValueError(k)


def _named_root(compound: _T, name: str = '') -> bytes:
    nb = name.encode('utf-8')
    return bytes([compound.kind]) + struct.pack('>H', len(nb)) + nb + _w(compound)


def _write_structure(path: Path, *, size, palette, blocks) -> None:
    """Write a gzip structure ``.nbt`` with relative ``blocks`` and a ``palette``."""
    pal_tags = []
    for entry in palette:
        if isinstance(entry, str):
            pal_tags.append(_T(_COMPOUND, {'Name': _T(_STRING, entry)}))
        else:
            name, props = entry
            prop_map = {k: _T(_STRING, v) for k, v in props.items()}
            pal_tags.append(_T(_COMPOUND, {
                'Name': _T(_STRING, name),
                'Properties': _T(_COMPOUND, prop_map),
            }))
    block_tags = []
    for (x, y, z), state_i in blocks.items():
        block_tags.append(_T(_COMPOUND, {
            'pos': _T(_LIST, (_INT, [_T(_INT, x), _T(_INT, y), _T(_INT, z)])),
            'state': _T(_INT, state_i),
        }))
    root = _T(_COMPOUND, {
        'size': _T(_LIST, (_INT, [_T(_INT, c) for c in size])),
        'palette': _T(_LIST, (_COMPOUND, pal_tags)),
        'blocks': _T(_LIST, (_COMPOUND, block_tags)),
        'entities': _T(_LIST, (_COMPOUND, [])),
        'DataVersion': _T(_INT, 4903),
    })
    path.write_bytes(gzip.compress(_named_root(root)))


class TestPaletteState(unittest.TestCase):
    def test_name_only(self):
        self.assertEqual(palette_state({'Name': 'minecraft:stone'}), 'minecraft:stone')

    def test_sorted_properties(self):
        self.assertEqual(
            palette_state({
                'Name': 'minecraft:oak_stairs',
                'Properties': {'half': 'bottom', 'facing': 'east'},
            }),
            'minecraft:oak_stairs[facing=east,half=bottom]',
        )


class TestReadStructureFile(unittest.TestCase):
    def test_relative_blocks_and_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'before_demo.nbt'
            _write_structure(
                path,
                size=(2, 2, 1),
                palette=['minecraft:air', 'minecraft:dirt',
                         ('minecraft:oak_stairs', {'facing': 'north', 'half': 'bottom'})],
                blocks={(0, 0, 0): 1, (1, 0, 0): 2, (0, 1, 0): 0},
            )
            size, rel = read_structure_file(path)
            self.assertEqual(size, (2, 2, 1))
            self.assertEqual(rel[(0, 0, 0)], 'minecraft:dirt')
            self.assertEqual(rel[(1, 0, 0)], 'minecraft:oak_stairs[facing=north,half=bottom]')
            self.assertEqual(rel[(0, 1, 0)], 'minecraft:air')

    def test_absolute_blocks_add_origin(self):
        rel = {(0, 0, 0): 'minecraft:dirt', (1, 2, 3): 'minecraft:stone'}
        abs_ = absolute_blocks(rel, (100, 50, -20))
        self.assertEqual(abs_[(100, 50, -20)], 'minecraft:dirt')
        self.assertEqual(abs_[(101, 52, -17)], 'minecraft:stone')

    def test_structure_file_path(self):
        root = Path('/save')
        self.assertEqual(
            structure_file_path(root, 'worldbuilder:before_house'),
            root / 'generated' / 'worldbuilder' / 'structures' / 'before_house.nbt',
        )


if __name__ == '__main__':
    unittest.main()
