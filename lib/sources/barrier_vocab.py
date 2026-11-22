"""Barrier kinds for garden conventions and the decor plan.

One table of plan kinds (what a decor item may carry) and one table of street-convention
types. ``fence`` is only a convention alias: :func:`barrier_kind` turns it into
``plank_fence`` or ``spruce_fence``. It is not itself a plan kind, so a convention that
already names ``spruce_fence`` stays an unknown type and is not filled from the plan defaults.
"""
from __future__ import annotations

from tree_species import DEFAULT_LEAVES

# The hedge block is the tree table's default leaf, so a hedge and a shrub stay the same wood.
HEDGE_BLOCK = DEFAULT_LEAVES

# Kinds a resolved decor item may carry. ``fence`` is not one of them.
DEFAULT_HEIGHT = {
    'hedge': 2,
    'brick_wall': 1,
    'iron_bars': 1,
    'spruce_fence': 2,
    'plank_fence': 1,
}
DEFAULT_BLOCK = {
    'hedge': HEDGE_BLOCK,
    'brick_wall': 'brick_wall',
    'iron_bars': 'iron_bars',
    'spruce_fence': 'spruce_fence',
    'plank_fence': 'oak_planks',
}
BARRIER_KINDS = frozenset(DEFAULT_BLOCK)

# Types a street convention may name. Hedge, wall and bars match the plan defaults.
# ``fence`` defaults to a two-high spruce fence; a plank block is a one-high panel instead.
FENCE_ALIAS = 'fence'
CONVENTION_HEIGHT = {
    'hedge': 2,
    'brick_wall': 1,
    'fence': 2,
    'iron_bars': 1,
}
CONVENTION_BLOCK = {
    'hedge': HEDGE_BLOCK,
    'brick_wall': 'brick_wall',
    'fence': 'spruce_fence',
    'iron_bars': 'iron_bars',
}
PLANK_HEIGHT = DEFAULT_HEIGHT['plank_fence']

# Close-boarded timber panels: oak is the default tone; the others only where evidence shows a
# distinct tone (rules/frontage-and-evidence.md). Block names are from memory and
# not yet confirmed in-world.
PLANK_TONES = {
    'oak': ('oak_planks',),
    'dark': ('spruce_planks', 'dark_oak_planks'),
    'reddy': ('jungle_planks', 'acacia_planks', 'mangrove_planks'),
    'pale': ('birch_planks',),
    'white': ('pale_oak_planks',),   # painted / white-looking fences; near white, lighter than birch
}
PLANK_BLOCKS = frozenset(b for blocks in PLANK_TONES.values() for b in blocks)


def barrier_kind(type_: str, block: str | None = None) -> str:
    """Plan kind for a convention ``type``.

    ``fence`` with a plank block is a close-boarded panel (``plank_fence``). ``fence`` with no
    block, or with a fence-post block, stays ``spruce_fence``. Any other type passes through
    unchanged, including an unknown one, so the plan can report it rather than drop it.

    @param type_: convention type, or a plan kind already chosen
    @param block: block id from the convention (``minecraft:`` prefix allowed); None if omitted
    @returns: plan kind (``plank_fence``, ``spruce_fence``, or ``type_`` unchanged)
    """
    if type_ == FENCE_ALIAS:
        name = (block or '').removeprefix('minecraft:')
        return 'plank_fence' if name in PLANK_BLOCKS else 'spruce_fence'
    return type_
