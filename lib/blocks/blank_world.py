#!/usr/bin/env python3
"""Create a new blank Minecraft save from ``templates/empty-world``.

The save belongs to the standalone Minecraft instance outside this project. This
is the **only** code that writes under an instance's ``saves/`` folder, and it
only ever creates a new folder: an existing target is refused, never overwritten.
"""
from __future__ import annotations

import gzip
import shutil
import struct
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE = TOOL_ROOT / 'templates' / 'empty-world'

# Never carried into a new save (the template has none, but copies may).
_SKIP = shutil.ignore_patterns('session.lock', 'players', '*.dat_old', '*.mca', 'README.txt')


def _modified_utf8(text: str) -> bytes:
    """Java modified UTF-8, the encoding of NBT strings: NUL as C0 80, non-BMP as surrogate pairs of 3 bytes each."""
    out = bytearray()
    for unit in struct.unpack(f'>{len(text.encode("utf-16-be")) // 2}H', text.encode('utf-16-be')):
        if unit == 0:
            out += b'\xc0\x80'
        else:
            out += chr(unit).encode('utf-8', 'surrogatepass')
    return bytes(out)


def set_level_name(level_dat: Path, name: str) -> None:
    """Rewrite the ``LevelName`` string in a gzipped ``level.dat`` (the in-game world name).

    NBT strings carry only their own length, so the one tag is patched in the bytes.

    @param level_dat: path to the save's level.dat
    @param name: new in-game name
    @raises RuntimeError: when the file has no single ``LevelName`` string tag
    @raises ValueError: when the name is longer than an NBT string allows (65535 bytes)
    """
    raw = gzip.decompress(Path(level_dat).read_bytes())
    key = b'\x08\x00\x09LevelName'
    i = raw.find(key)
    if i < 0 or raw.count(key) != 1:
        raise RuntimeError(f'{level_dat}: expected exactly one LevelName tag')
    at = i + len(key)
    old = struct.unpack_from('>H', raw, at)[0]
    new = _modified_utf8(name)
    if len(new) > 0xFFFF:
        raise ValueError(f'world name too long: {len(new)} bytes')
    Path(level_dat).write_bytes(gzip.compress(
        raw[:at] + struct.pack('>H', len(new)) + new + raw[at + 2 + old:]))


def create_blank_world(save: Path, template: Path = TEMPLATE, name: str | None = None) -> Path:
    """Copy the empty-world template to a new save folder and name it.

    @param save: new save folder (its parent, the instance's ``saves/``, must exist)
    @param template: template folder (tests)
    @param name: in-game world name (default: the folder name)
    @returns: the created save path
    @raises FileExistsError: when ``save`` already exists
    @raises FileNotFoundError: when the parent folder or template is missing
    """
    save = Path(save)
    if save.exists():
        raise FileExistsError(f'refusing to overwrite existing save: {save}')
    if not save.parent.is_dir():
        raise FileNotFoundError(
            f'{save.parent} does not exist: create the Minecraft instance first '
            '(docs/minecraft-instance-setup.md)')
    if not template.is_dir():
        raise FileNotFoundError(f'template missing: {template}')
    shutil.copytree(template, save, ignore=_SKIP)
    set_level_name(save / 'level.dat', name or save.name)
    return save
