"""Read-only helpers for finding the ground surface in a saved column.

Shared by the ground, trees and foliage tools. Nothing here writes to the world.
"""
from __future__ import annotations

from site_kit import World

# Small plants: counted as air when finding the surface, and by the ground clear step.
# IDs are confirmed by config/minecraft-block-ids-26.2.txt (Mojang client blockstates).
# The older Mojang 1.21.11 client has single-block models for the four woodland
# additions (even tall_dry_grass); leaf_litter has facing/segment_amount states.
# This is older-version evidence ONLY: 26.2 placement states remain unverified.
# Recognition is state-agnostic and does not authorize placing those states.
# ``flower`` and ``daisy`` are legacy classification patterns, not placement IDs.
_LEGACY_PLANT_PATTERNS = (
    'short_grass', 'tall_grass', 'fern', 'large_fern', 'flower', 'poppy', 'dandelion', 'daisy',
    'cornflower', 'allium', 'azure_bluet', 'oxeye_daisy', 'red_tulip', 'white_tulip',
)
PLANTS = _LEGACY_PLANT_PATTERNS + (
    'blue_orchid', 'orange_tulip', 'pink_tulip', 'lily_of_the_valley', 'wither_rose',
    'sunflower', 'rose_bush', 'peony', 'lilac', 'pitcher_plant', 'torchflower',
    'short_dry_grass', 'tall_dry_grass', 'leaf_litter', 'bush', 'firefly_bush',
    'wildflowers', 'pink_petals', 'open_eyeblossom', 'closed_eyeblossom',
    'dead_bush', 'sweet_berry_bush', 'brown_mushroom', 'red_mushroom',
)
JUNK_ABOVE = ('_log', 'leaves', '_carpet', 'sapling')
# Cell-marker hazard tape (cell_marker.WOOL): ~30 above ground, so any height, not just high ones.
MARKER_WOOL = ('black_wool', 'yellow_wool')

# Below the lowest ground the tool builds: lidar ghost ground fills stone from Y 1 up
# (``lidar_ghost.FILL_BOTTOM``), so under Y 1 there is only the empty save (bedrock, or air down to it).
WORLD_FLOOR_Y = 0
# Metres below sea level (0 m on the lidar datum) that ground may still lie. The lowest UK land is
# about -3 m (the Fens); low tide and dock water a little more. Ground deeper than this is not expected.
LOW_GROUND_MARGIN_M = 10


def probe_floor() -> int:
    """Highest Y the surface probe never reads: ground at or below it is not looked for.

    Derived from the active world: sea level (``world-frame.json`` ``y_offset``) minus
    :data:`LOW_GROUND_MARGIN_M`, never below :data:`WORLD_FLOOR_Y`. ``surface_y``, the layer
    warm-ups (``ground_layer.probe_y_range``) and the ground layer's support and cavity searches all
    use it, so the prefetch always covers what the probe reads. Not below the ghost fill: the
    cavity search would otherwise fill the empty save's air under every column. With no world
    selected (unit tests) it is :data:`WORLD_FLOOR_Y`.

    @returns: floor Y (``surface_y`` reads down to ``probe_floor() + 1``)
    """
    import script_env
    if not script_env.world_bound():
        return WORLD_FLOOR_Y
    from world_frame import load_frame
    return max(WORLD_FLOOR_Y, load_frame().y_offset - LOW_GROUND_MARGIN_M)


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


def surface_y(w: World, x: int, z: int, top: int = 110, floor: int | None = None) -> tuple[int, str] | None:
    """Top ground block of a column, skipping air, plants, logs, leaves and marker wool (any height).

    @param floor: highest Y not read; default :func:`probe_floor`
    @returns: (y, short block id), or None for an empty column. Water counts as a surface.
    """
    if floor is None:
        floor = probe_floor()
    y = top
    while y > floor:
        b = w.block(x, y, z) or 'minecraft:air'
        if is_water(b):
            return y, short_id(b)
        if b.endswith('air'):
            y -= 1
            continue
        if short_id(b) in PLANTS or any(p in b for p in _LEGACY_PLANT_PATTERNS) or 'leaves' in b or '_log' in b:
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
        if b.endswith('air') or short_id(b) in PLANTS or any(p in b for p in _LEGACY_PLANT_PATTERNS) \
                or 'leaves' in b or '_log' in b \
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
