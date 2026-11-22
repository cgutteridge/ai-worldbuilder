"""The one tree-species table (#378), and the one default leaf block.

Every species the tool can build is one row of ``SPECIES``: its log and leaf blocks, and (for a
species with its own silhouette) the crown radii, trunk width and trunk heights. Tree voxel
generation (``tree_voxels``), the trees pass (``trees_layer``) and land-use validation
(``land_use.validate_table`` checks ``trees.species_weights`` against :func:`mixable`) all read
this table, so a new species is one row and no other edit.
"""
from __future__ import annotations

from dataclasses import dataclass

# The persistent oak leaf block: the default leaf, and the hedge, shrub and bush block everywhere.
DEFAULT_LEAVES = 'oak_leaves[persistent=true]'
DEFAULT_SPECIES = 'oak'  # keeps the original plane/round path and plain trunk; never has a silhouette


@dataclass(frozen=True)
class Species:
    """One row: blocks, and optionally a silhouette (crown, trunk width, trunk heights).

    @param log: log block id (no namespace)
    @param leaves: persistent leaf block id
    @param crown: (horizontal, vertical) crown radii in blocks, or None for the round/plane crowns
    @param trunk_width: trunk side in blocks (2 builds a 2x2 trunk); used with ``crown``
    @param heights: trunk heights the trees pass picks from; set together with ``crown``
    """
    log: str
    leaves: str
    crown: tuple[float, float] | None = None
    trunk_width: int = 1
    heights: tuple[int, ...] | None = None

    def __post_init__(self):
        if (self.crown is None) != (self.heights is None):
            raise ValueError('a species silhouette needs both crown and heights, or neither')


SPECIES: dict[str, Species] = {
    'oak': Species('oak_log', DEFAULT_LEAVES),
    'birch': Species('birch_log', 'birch_leaves[persistent=true]', crown=(2.0, 3.0), trunk_width=1,
                     heights=(6, 7, 8)),
    'spruce': Species('spruce_log', 'spruce_leaves[persistent=true]'),
    'dark_oak': Species('dark_oak_log', 'dark_oak_leaves[persistent=true]', crown=(4.0, 2.0),
                        trunk_width=2, heights=(6, 7, 8)),
    'cherry': Species('cherry_log', 'cherry_leaves[persistent=true]'),
    'acacia': Species('acacia_log', 'acacia_leaves[persistent=true]'),
}


def has_silhouette(species: str) -> bool:
    """True when the species builds its own crown (not the round/plane oak path).

    @param species: key of ``SPECIES``
    @returns: whether the row carries crown radii and trunk heights
    """
    return SPECIES[species].crown is not None


def mixable() -> tuple[str, ...]:
    """Species the trees pass can place, in table order: oak, plus every row with a silhouette.

    Read live, so a row added to ``SPECIES`` is valid in ``trees.species_weights`` at once.

    @returns: species names land-use validation accepts
    """
    return tuple(name for name in SPECIES if name == DEFAULT_SPECIES or has_silhouette(name))
