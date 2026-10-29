"""Read-only :class:`BlockStore` over anvil ``.mca`` region files (#111).

Strictly read-only: never write, copy, snap, restore or delete region files.
For planning and dry-run reads after ``server_save`` / ``flush`` so disk matches
the open world. Live applies and their readback stay on :class:`LiveBlocks` (MCP).

Owner-authorised exception to ADR 004 / GLOSSARY "avoid parsing region files"
(2026-10-26): disk reads after an explicit save, never as a restore path.
"""
from __future__ import annotations

from collections import OrderedDict
import math
import struct
import zlib
from pathlib import Path
from typing import Iterable

from block_store import BlockStoreError, Pos, canonical_state
from nbt_read import read_named_root

MAX_REGION_FILES = 2
MAX_REGION_CHUNKS = 256


class RegionBlocks:
    """BlockStore that parses ``r.X.Z.mca`` under a region directory (read-only).

    Missing chunks return ``None``. Present chunks with a missing section return
    ``minecraft:air`` (same as the pre-#67 ``World`` reader). ``flush()`` drops
    the file and chunk caches so a later read sees a freshly saved disk image.

    @param region: ``…/overworld/region`` directory
    """

    def __init__(self, region: Path | str) -> None:
        self.region = Path(region)
        self._files: OrderedDict[Path, bytes] = OrderedDict()
        self._chunks: OrderedDict[tuple[int, int], dict | None] = OrderedDict()

    @classmethod
    def from_world_config(cls) -> 'RegionBlocks':
        """Store for the active world's ``region_path``.

        @returns: RegionBlocks
        """
        import site_kit
        return cls(site_kit.resolve_region())

    def flush(self) -> None:
        """Drop cached region bytes and chunk palettes (re-read after a server save)."""
        self._files.clear()
        self._chunks.clear()

    def read(self, pos: Pos) -> str | None:
        """Block state at ``pos``, or None when the chunk file / slot is absent.

        @param pos: ``(x, y, z)``
        @returns: canonical state, ``minecraft:air`` for an empty section, or None
        """
        x, y, z = pos
        secs = self._chunk(x >> 4, z >> 4)
        if secs is None:
            return None
        s = secs.get(y >> 4)
        if s is None:
            return 'minecraft:air'
        pal, longs, bits, per = s
        if bits == 0 or longs is None:
            return pal[0]
        i = ((y & 15) * 256) + ((z & 15) * 16) + (x & 15)
        L = longs[i // per] & ((1 << 64) - 1)
        return pal[(L >> ((i % per) * bits)) & ((1 << bits) - 1)]

    def read_many(self, positions: Iterable[Pos]) -> dict[Pos, str | None]:
        """``{pos: read(pos)}`` for every requested position.

        @param positions: world blocks
        @returns: mapping preserving caller keys as tuples
        """
        return {tuple(p): self.read(p) for p in positions}

    def _file_bytes(self, path: Path) -> bytes:
        if path not in self._files:
            self._files[path] = path.read_bytes() if path.is_file() else b''
        self._files.move_to_end(path)
        if len(self._files) > MAX_REGION_FILES:
            self._files.popitem(last=False)
        return self._files[path]

    def _chunk(self, cx: int, cz: int) -> dict | None:
        """Parse and cache one chunk's section palettes.

        @param cx: chunk X
        @param cz: chunk Z
        @returns: ``{section_Y: (palette, longs|None, bits, per)}`` or None if missing
        """
        key = (cx, cz)
        if key in self._chunks:
            self._chunks.move_to_end(key)
            return self._chunks[key]
        rx, rz = cx >> 5, cz >> 5
        path = self.region / f'r.{rx}.{rz}.mca'
        data = self._file_bytes(path)
        secs: dict | None = None
        if data:
            try:
                secs = self._parse_chunk(data, cx, cz)
            except (OSError, struct.error, ValueError, zlib.error, KeyError, IndexError) as exc:
                raise BlockStoreError(
                    f'RegionBlocks failed to parse chunk {cx},{cz} in {path}: {exc}'
                ) from exc
        self._chunks[key] = secs
        if len(self._chunks) > MAX_REGION_CHUNKS:
            self._chunks.popitem(last=False)
        return secs

    def _parse_chunk(self, data: bytes, cx: int, cz: int) -> dict | None:
        """Decode one chunk slot from a region file payload.

        @param data: whole ``.mca`` file bytes
        @param cx: chunk X
        @param cz: chunk Z
        @returns: section map, or None when the location table entry is empty
        """
        if len(data) < 8192:
            return None
        idx = (cx & 31) + (cz & 31) * 32
        off = struct.unpack_from('>I', data, idx * 4)[0]
        if not off:
            return None
        sec = (off >> 8) * 4096
        ln = struct.unpack_from('>I', data, sec)[0]
        if data[sec + 4] != 2:
            raise BlockStoreError(f'unsupported chunk compression at {cx},{cz}')
        buf = zlib.decompress(data[sec + 5:sec + 4 + ln])
        root = read_named_root(buf).value
        sections = root.get('sections')
        if sections is None:
            return {}
        out: dict = {}
        for s in sections.value[1]:
            s = s.value
            bs = s.get('block_states')
            if not bs:
                continue
            pal = []
            for p in bs.value['palette'].value[1]:
                p = p.value
                props = p.get('Properties')
                st = p['Name'].value
                if props:
                    st += '[' + ','.join(
                        f'{k}={v.value}' for k, v in sorted(props.value.items())
                    ) + ']'
                pal.append(canonical_state(st))
            bits = max(4, math.ceil(math.log2(len(pal)))) if len(pal) > 1 else 0
            data_tag = bs.value.get('data')
            longs = data_tag.value if data_tag is not None else None
            per = (64 // bits) if bits else 0
            out[s['Y'].value] = (pal, longs, bits, per)
        return out
