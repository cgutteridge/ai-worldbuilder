"""Read-only Java-edition NBT (enough for anvil chunk compounds).

No write path — region files must never be mutated from this module (#111).
"""
from __future__ import annotations

import struct

END, BYTE, SHORT, INT, LONG, FLOAT, DOUBLE, BYTE_ARRAY, STRING, LIST, COMPOUND, INT_ARRAY, LONG_ARRAY = range(13)


class Tag:
    """One NBT value.

    @param kind: type id (BYTE…LONG_ARRAY)
    @param value: decoded payload
    """

    def __init__(self, kind: int, value) -> None:
        self.kind = kind
        self.value = value

    def __repr__(self) -> str:
        v = self.value
        if self.kind in (BYTE_ARRAY, INT_ARRAY, LONG_ARRAY):
            v = f'<{len(v)} items>'
        return f'Tag({self.kind}, {v!r})'


def read_tag(buf: bytes, pos: int, kind: int) -> tuple[Tag, int]:
    """Decode one tag starting at ``pos``.

    @param buf: NBT payload
    @param pos: byte offset
    @param kind: expected type id
    @returns: ``(tag, next_pos)``
    """
    if kind == BYTE:
        return Tag(kind, struct.unpack_from('>b', buf, pos)[0]), pos + 1
    if kind == SHORT:
        return Tag(kind, struct.unpack_from('>h', buf, pos)[0]), pos + 2
    if kind == INT:
        return Tag(kind, struct.unpack_from('>i', buf, pos)[0]), pos + 4
    if kind == LONG:
        return Tag(kind, struct.unpack_from('>q', buf, pos)[0]), pos + 8
    if kind == FLOAT:
        return Tag(kind, struct.unpack_from('>f', buf, pos)[0]), pos + 4
    if kind == DOUBLE:
        return Tag(kind, struct.unpack_from('>d', buf, pos)[0]), pos + 8
    if kind == BYTE_ARRAY:
        n = struct.unpack_from('>i', buf, pos)[0]
        return Tag(kind, bytes(buf[pos + 4:pos + 4 + n])), pos + 4 + n
    if kind == STRING:
        n = struct.unpack_from('>H', buf, pos)[0]
        return Tag(kind, buf[pos + 2:pos + 2 + n].decode('utf-8')), pos + 2 + n
    if kind == LIST:
        sub = buf[pos]
        n = struct.unpack_from('>i', buf, pos + 1)[0]
        pos += 5
        items = []
        for _ in range(n):
            t, pos = read_tag(buf, pos, sub)
            items.append(t)
        return Tag(kind, (sub, items)), pos
    if kind == COMPOUND:
        d = {}
        while True:
            k = buf[pos]
            pos += 1
            if k == END:
                return Tag(kind, d), pos
            n = struct.unpack_from('>H', buf, pos)[0]
            name = buf[pos + 2:pos + 2 + n].decode('utf-8')
            pos += 2 + n
            d[name], pos = read_tag(buf, pos, k)
        # unreachable
    if kind == INT_ARRAY:
        n = struct.unpack_from('>i', buf, pos)[0]
        return Tag(kind, list(struct.unpack_from(f'>{n}i', buf, pos + 4))), pos + 4 + 4 * n
    if kind == LONG_ARRAY:
        n = struct.unpack_from('>i', buf, pos)[0]
        return Tag(kind, list(struct.unpack_from(f'>{n}q', buf, pos + 4))), pos + 4 + 8 * n
    raise ValueError(f'unsupported NBT kind {kind}')


def read_named_root(buf: bytes) -> Tag:
    """Decode a named root compound (anvil chunk body after zlib).

    @param buf: decompressed chunk NBT
    @returns: root compound tag (``.value`` is the map)
    """
    kind = buf[0]
    n = struct.unpack_from('>H', buf, 1)[0]
    tag, _ = read_tag(buf, 3 + n, kind)
    return tag
