#!/usr/bin/env python3
"""Airtight check: can air get from outside into a building's interior? (user, 2026-10-16: "not airtight")

Flood-fills air (foliage counts as air; doors, glass and slabs are solid) inside a box from an OUTSIDE point and reports whether
it reaches an INSIDE point, with the cells of the breach.

  .venv/bin/python scripts/airtight.py 6184,55,-5592,6210,63,-5576 --outside 6197,57,-5577 --inside 6197,57,-5583
"""
import argparse
import collections
import sys
from pathlib import Path

_TOOL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL / 'scripts'))
import script_env  # noqa: E402

script_env.ensure_import_paths()
from site_kit import AIR_IDS, World  # noqa: E402

_AIR_EXTRAS = ('light',)


def is_air(w, p):
    """@returns: True for air and foliage (open to the weather)"""
    b = (w.block(*p) or 'minecraft:air').split('[')[0][10:]
    return b in (*AIR_IDS, *_AIR_EXTRAS) or 'leaves' in b or 'fern' in b or 'flower' in b or (
        'grass' in b and b != 'grass_block')


def leak(w, box, outside, inside):
    """Flood air from ``outside`` within ``box`` (x0,y0,z0,x1,y1,z1).

    @param w: site_kit.World
    @param box: inclusive box
    @param outside: (x, y, z) in open air
    @param inside: (x, y, z) in the room
    @returns: path of cells from inside to outside when air reaches it, else None
    """
    def inb(p):
        return all(box[i] <= p[i] <= box[i + 3] for i in range(3))
    seen, q = {outside: None}, collections.deque([outside])
    while q:
        p = q.popleft()
        for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            n = (p[0] + d[0], p[1] + d[1], p[2] + d[2])
            if n not in seen and inb(n) and is_air(w, n):
                seen[n] = p
                q.append(n)
    if inside not in seen:
        return None
    path, c = [], inside
    while c:
        path.append(c)
        c = seen[c]
    return path


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
script_env.add_world_arg(ap)
    ap.add_argument('box')
    ap.add_argument('--outside', required=True)
    ap.add_argument('--inside', required=True)
    a = ap.parse_args()
script_env.activate_world_from_args(a)
    t = lambda s: tuple(int(v) for v in s.split(','))
    path = leak(World(), t(a.box), t(a.outside), t(a.inside))
    print('AIRTIGHT' if path is None else f'LEAK: {len(path)} cells, inside -> outside: {path[:8]} ... {path[-3:]}')
    raise SystemExit(0 if path is None else 1)
