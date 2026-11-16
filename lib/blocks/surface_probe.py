"""Read-only helpers for finding the ground surface in a saved column.

Shared by the ground, trees and foliage tools. Nothing here writes to the world.
"""
from __future__ import annotations

from site_kit import World

# Small plants: counted as air when finding the surface, and by the ground clear step.
PLANTS = (
    'short_grass', 'tall_grass', 'fern', 'large_fern', 'flower', 'poppy', 'dandelion', 'daisy',
    'cornflower', 'allium', 'azure_bluet', 'oxeye_daisy', 'red_tulip', 'white_tulip',
)
JUNK_ABOVE = ('_log', 'leaves', '_carpet', 'sapling')
# Cell-marker hazard tape (cell_marker.WOOL): ~30 above ground, so any height, not just high ones.
MARKER_WOOL = ('black_wool', 'yellow_wool')


def short_id(block: str) -> str:
    """Block id without ``minecraft:`` or ``[state]``."""
    return (block or 'minecraft:air').split('[')[0].replace('minecraft:', '')


def is_water(block: str) -> bool:
    """Plain water block (waterlogged stairs and leaves are not water)."""
    return (block or '').split('[')[0] == 'minecraft:water'


def is_tree_or_plant_block(block: str) -> bool:
    """Logs, leaves, carpet, saplings and small plants: vegetation, not ground."""
    short = short_id(block)
    return 'sapling' in short or any(j in block for j in JUNK_ABOVE) or short in PLANTS


def surface_y(w: World, x: int, z: int, top: int = 110) -> tuple[int, str] | None:
    """Top ground block of a column, skipping air, plants, logs, leaves and marker wool (any height).

    @returns: (y, short block id), or None for an empty column. Water counts as a surface.
    """
    y = top
    while y > 30:
        b = w.block(x, y, z) or 'minecraft:air'
        if is_water(b):
            return y, short_id(b)
        if b.endswith('air'):
            y -= 1
            continue
        if any(p in b for p in PLANTS) or 'leaves' in b or '_log' in b:
            y -= 1
            continue
        if short_id(b) in MARKER_WOOL:
            y -= 1
            continue
        return y, short_id(b)
    return None


def terrain_y(hm, x: int, z: int) -> int | None:
    """Lidar terrain (DTM) Y of one column: the Y of the top ground block, or None where it is missing.

    @param hm: height map dict (``x0``, ``z0``, ``terrain``) or None
    @returns: Y, or None when there is no map, no ``terrain`` array, the column is outside it or holds 0
    """
    if hm is None or 'terrain' not in hm:
        return None
    terrain = hm['terrain']
    i, j = x - int(hm['x0']), z - int(hm['z0'])
    if not (0 <= i < terrain.shape[0] and 0 <= j < terrain.shape[1]):
        return None
    y = int(terrain[i, j])
    return y if y > 0 else None


def ground_at(w: World, hm, x: int, z: int, owned=(), masked=(), fallback=None) -> tuple[int, str] | None:
    """The ground block of a column, at lidar terrain Y rather than the live top block (#301).

    Over a roof ``surface_y`` returns the roof, so anything that stood on it stood on the roof. The
    terrain height is the truth for ground; the live world is only read AT that height, for the block
    id and for whether it is real ground. Material, water, carriageway and 'uneven ground' checks read
    this block, never ``surface_y``.

    Where terrain is missing for the column the live surface is used as before (``fallback``,
    default :func:`surface_y`), and the caller can tell by ``terrain_y(hm, x, z) is None``.

    @param hm: height map dict with ``terrain``, or None
    @param owned: positions a feature owns; ground there is not ground
    @param masked: positions another pass mask owns
    @param fallback: ``callable(w, x, z)`` used when terrain is missing; default :func:`surface_y`
    @returns: ``(y, short block id)`` like ``surface_y`` (water counts), or None when the column has no
        solid, unowned block at terrain Y (air, plant, log, leaves, marker wool, owned or masked)
    """
    y = terrain_y(hm, x, z)
    if y is None:
        return (fallback or surface_y)(w, x, z)
    b = w.block(x, y, z) or 'minecraft:air'
    if not is_water(b):
        if b.endswith('air') or any(p in b for p in PLANTS) or 'leaves' in b or '_log' in b \
                or short_id(b) in MARKER_WOOL:
            return None
    if (x, y, z) in owned or (x, y, z) in masked:
        return None
    return y, short_id(b)


def raised_over_terrain(w: World, x: int, z: int, y: int) -> bool:
    """True when the live surface of the column stands above terrain ``y`` (a roof or deck over the ground)."""
    live = surface_y(w, x, z)
    return bool(live and live[0] > y)


def ground_missing_reason(hm, x: int, z: int, owned=()) -> str:
    """Why :func:`ground_at` found no ground, as the reason the layers report.

    'feature above terrain' where a feature owns the block at terrain Y (it stands on, in or over the
    ground: a building's own floor or wall), 'no ground at terrain' where terrain exists but the block
    there is not solid ground (air, a plant, a log, a floating base), 'no surface' where there is no terrain
    and the live column is empty.
    """
    y = terrain_y(hm, x, z)
    if y is None:
        return 'no surface'
    return 'feature above terrain' if (x, y, z) in owned else 'no ground at terrain'
