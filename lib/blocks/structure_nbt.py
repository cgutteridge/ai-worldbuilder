"""Read Java-edition structure ``.nbt`` templates (site_kit ``before_<plan>`` backups).

Gzip-compressed named compounds with ``size``, ``palette``, and ``blocks`` (relative
``pos`` + palette index). Used to compare a post-undo world against the pre-build
snapshot (#123). Read-only — never writes structure files.
"""
from __future__ import annotations

import gzip
from pathlib import Path
from typing import Mapping

from block_store import Pos, canonical_state
from nbt_read import read_named_root


def palette_state(entry: Mapping) -> str:
    """Canonical block state from a structure palette entry (dict or NBT-decoded map).

    @param entry: ``{'Name': id}`` or ``{'Name': id, 'Properties': {k: v}}``
        (values may be plain strings or ``Tag`` with ``.value``)
    @returns: canonical ``minecraft:...`` state string
    """
    name = _plain(entry['Name'])
    props = entry.get('Properties')
    if not props:
        return canonical_state(name)
    raw = props.value if hasattr(props, 'value') else props
    pairs = {str(k): str(_plain(v)) for k, v in raw.items()}
    prop_s = ','.join(f'{k}={pairs[k]}' for k in sorted(pairs))
    return canonical_state(f'{name}[{prop_s}]')


def structure_file_path(save_root: Path | str, namespaced: str) -> Path:
    """On-disk path for a namespaced structure id under a world save.

    @param save_root: Minecraft save directory (contains ``generated/``)
    @param namespaced: e.g. ``worldbuilder:before_house``
    @returns: ``…/generated/<ns>/structures/<local>.nbt``
    """
    ns, _, local = namespaced.partition(':')
    if not ns or not local:
        raise ValueError(f'expected namespace:local structure id, got {namespaced!r}')
    return Path(save_root) / 'generated' / ns / 'structures' / f'{local}.nbt'


def read_structure_file(path: Path | str) -> tuple[tuple[int, int, int], dict[Pos, str]]:
    """Decode a gzip structure template into size and relative block map.

    @param path: ``.nbt`` file (gzip-compressed)
    @returns: ``((sx, sy, sz), {(x, y, z): canonical_state})`` with positions relative
        to the structure origin
    @raises OSError: file missing or unreadable
    @raises ValueError: NBT shape is not a structure template
    """
    raw = Path(path).read_bytes()
    try:
        data = gzip.decompress(raw)
    except OSError:
        data = raw
    root = read_named_root(data).value
    try:
        size_tags = root['size'].value[1]
        size = (int(size_tags[0].value), int(size_tags[1].value), int(size_tags[2].value))
        palette = [palette_state(_tag_map(e)) for e in root['palette'].value[1]]
        blocks: dict[Pos, str] = {}
        for b in root['blocks'].value[1]:
            bm = _tag_map(b)
            pos_tags = bm['pos'].value[1] if hasattr(bm['pos'], 'value') else bm['pos']
            x, y, z = (int(_plain(pos_tags[0])), int(_plain(pos_tags[1])), int(_plain(pos_tags[2])))
            state_i = int(_plain(bm['state']))
            blocks[(x, y, z)] = palette[state_i]
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ValueError(f'not a structure template: {path}: {exc}') from exc
    return size, blocks


def absolute_blocks(relative: Mapping[Pos, str], origin: tuple[int, int, int] | list[int]) -> dict[Pos, str]:
    """Translate relative structure positions to absolute world coordinates.

    @param relative: ``{(dx, dy, dz): state}`` from :func:`read_structure_file`
    @param origin: structure save ``from`` corner ``[x, y, z]``
    @returns: ``{(x, y, z): state}`` in world space
    """
    ox, oy, oz = int(origin[0]), int(origin[1]), int(origin[2])
    return {(ox + x, oy + y, oz + z): s for (x, y, z), s in relative.items()}


def _plain(v):
    return v.value if hasattr(v, 'value') else v


def _tag_map(tag) -> dict:
    """Compound tag → plain dict (``Tag`` values kept for nested lists)."""
    raw = tag.value if hasattr(tag, 'value') else tag
    if not isinstance(raw, dict):
        raise TypeError(f'expected compound, got {type(raw)!r}')
    return raw
