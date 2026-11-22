"""Door-stoop and natural-ground helpers shared by ``house_build`` and ``feature_build``.

Both builders step a door's outside column down or up to the floor, find the natural ground
beside it and name stair facings the same way. One copy lives here so they cannot drift (T18).
"""
from __future__ import annotations

from collections.abc import Callable, Iterable

from cellmath import DIRS
from roof_material import family_variants
from site_kit import ground_scan_range

# Ground blocks the margin clear keeps and step_out lands on. gray_/black_concrete are the lidar
# ghost's DSM-DTM tops (gray_concrete is also real tarmac): builds run on ghost ground before the
# cell's real ground (task 286), so margin clearing must not hole it.
GROUND = (
    'minecraft:stone', 'minecraft:gray_terracotta', 'minecraft:polished_granite',
    'minecraft:polished_diorite', 'minecraft:light_gray_concrete', 'minecraft:grass_block',
    'minecraft:dirt', 'minecraft:coarse_dirt', 'minecraft:gravel', 'minecraft:andesite',
    'minecraft:gray_concrete', 'minecraft:black_concrete',
)
FACE = {'S': 'south', 'N': 'north', 'W': 'west', 'E': 'east'}
TOWARD = {'S': 'north', 'N': 'south', 'W': 'east', 'E': 'west'}       # facing of a stair that rises toward the house


def stair(mat: str, facing: str, half: str = 'bottom', shape: str | None = None) -> str:
    """Stair state using shared material names; optional explicit corner shape.

    @param mat: material or family name resolved through the roof-material catalogue
    @param facing: facing direction name
    @param half: ``bottom`` or ``top``
    @param shape: explicit corner shape, or ``None`` to leave it to the game
    @returns: stair block state string
    """
    block = family_variants(mat)['stairs']
    corner = f',shape={shape}' if shape is not None else ''
    return f'{block}[facing={facing},half={half}{corner}]'


def top_solid(live, x: int, z: int, near: int) -> int | None:
    """Y of the natural ground in a column (ignores roof fringe slabs and earlier steps of this build).

    @param live: world reader with ``block(x, y, z)``
    @param x: block x
    @param z: block z
    @param near: a height the caller already knows (floor or door floor)
    @returns: ground Y, or ``None`` when the column holds none
    """
    for y in ground_scan_range(near):
        n = (live.block(x, y, z) or 'minecraft:air').split('[')[0]
        if n in GROUND and n != 'minecraft:stone_stairs':
            return y
    return None


def step_out(build, st: tuple[int, int], d: str, b: int, setter: Callable[[int, int, int, str], None],
             prefetch: Callable[[Iterable[tuple[int, int, int]]], None] | None = None,
             log: Callable[[str], None] | None = None) -> None:
    """Walk outward from a stoop to the road, levelling the ground to the door floor.

    Ground ABOVE the floor in the way (old import ledge) is lowered to the floor level; at the
    first column BELOW the floor a stair rises toward the house (a 1-block hop is not walkable
    in site_walk).

    @param build: planner with ``live``, ``owner``, ``foreign`` and ``spec`` (``step_max``, ``ledge_stair``)
    @param st: stoop cell ``(x, z)``
    @param d: outward direction key ``N``/``S``/``E``/``W``
    @param b: door floor Y
    @param setter: places one cell, e.g. ``build.stoop_set`` or ``build.kset``
    @param prefetch: optional warm-up for the live cells the walk will read
    @param log: optional sink for the ledge and step messages
    """
    dx, dz = DIRS[d]
    facing = TOWARD[d]
    step_max = build.spec['step_max']
    if prefetch is not None:
        cols = [(st[0] + dx * k, st[1] + dz * k) for k in range(1, step_max + 1)]
        prefetch((x, y, z) for x, z in cols for y in ground_scan_range(b))
    for k in range(1, step_max + 1):
        n = (st[0] + dx * k, st[1] + dz * k)
        if n in build.owner or n in build.foreign:
            continue
        g = top_solid(build.live, n[0], n[1], b)
        if g is None:
            continue
        if build.spec['ledge_stair'] and g == b + 1 and k > 4:
            # lowered ground ends at a 1-block ledge: stair rising away from the house onto it
            setter(n[0], g, n[1], stair('stone', FACE[d]))
            if log:
                log(f'ledge stair at {n}')
            return
        if g > b + 1 or (g > b and k > 4):
            return  # real slope/terrace: leave it
        if g > b:
            setter(n[0], b, n[1], 'stone')
            for y in range(b + 1, g + 1):
                setter(n[0], y, n[1], 'air')
            continue
        if g < b:
            setter(n[0], b, n[1], stair('stone', facing))
            for y in range(g + 1, b):
                setter(n[0], y, n[1], 'stone')
            for y in range(b + 1, b + 4):
                setter(n[0], y, n[1], 'air')
            if log:
                log(f'step at {n} ground {g} floor {b}')
            return
