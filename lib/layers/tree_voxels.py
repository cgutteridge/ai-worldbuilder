"""Tree and bush voxel generators: static log + persistent-leaf massing for the trees and decor passes.

``tree_voxels`` builds one tree (a round or plane crown, or a species silhouette from the
``tree_species`` table) and ``bush_voxels`` a low leaf clump. Both are pure: they return ``{(x, y, z): block}`` and touch no world. Placement, clearance and
planning live in ``trees_layer`` and ``decor_layer``.
"""
from __future__ import annotations

import math
import random

from tree_species import DEFAULT_LEAVES, SPECIES, has_silhouette

RNG_SEED = 48045
PLANE_SPECIES = 'oak'  # leaves; trunk is mottled oak with birch patches (see tree_voxels)


def tree_voxels(x: int, z: int, ground_y: int, *, height: int | None = None,
                species: str | None = None, style: str | None = None,
                rng: random.Random | None = None) -> dict[tuple[int, int, int], str]:
    """Parameterised tree: height, species (optional), crown style (optional).

    @param x: trunk X
    @param z: trunk Z
    @param ground_y: surface Y under the trunk
    @param height: trunk height in blocks; default picked by style
    @param species: key of ``tree_species.SPECIES`` (blocks, and the silhouette for that style); default
        by style (oak)
    @param style: crown shape, independent of species: 'round' (sphere crown), 'plane' (English
        plane: tall clean trunk, open irregular crown of overlapping lobes high up), or 'silhouette'
        (the species' own crown radii and trunk width; the species must have them); default plane
        when height >= 8, else round
    @param rng: random source for lobes
    @returns: voxel map
    """
    rnd = rng or random.Random(x * 73856093 ^ z * 19349663)
    if style not in (None, 'round', 'plane', 'silhouette'):
        raise ValueError(f'style must be round, plane or silhouette, not {style!r}')
    if style == 'silhouette' and not (species in SPECIES and has_silhouette(species)):
        raise ValueError(f'species {species!r} has no silhouette (crown and heights) in tree_species.SPECIES')
    if style is None:
        style = 'plane' if (height is None or height >= 8) else 'round'
    if height is None:
        height = rnd.choice((9, 10, 11)) if style == 'plane' else rnd.choice((4, 5, 6))
    if species is None:
        species = PLANE_SPECIES if style == 'plane' else 'oak'
    row = SPECIES[species]
    log, leaves = row.log, row.leaves
    out: dict[tuple[int, int, int], str] = {}
    for i in range(1, height + 1):
        block = log
        if style == 'plane' and species == PLANE_SPECIES:
            # plane bark is mottled: oak base with pale birch patches
            block = 'birch_log' if (i * 7 + x * 3 + z * 5) % 4 == 0 else 'oak_log'
        out[(x, ground_y + i, z)] = block
    if style == 'silhouette':
        width = row.trunk_width
        for dx in range(width):
            for dz in range(width):
                for i in range(1, height + 1):
                    out[(x + dx, ground_y + i, z + dz)] = log
        horizontal, vertical = row.crown
        reach, rise = math.ceil(horizontal), math.ceil(vertical)
        centre = (width - 1) / 2
        for dx in range(-reach, reach + width):
            for dz in range(-reach, reach + width):
                for dy in range(-rise, rise + 1):
                    if ((dx - centre) ** 2 + (dz - centre) ** 2) / horizontal ** 2 + dy ** 2 / vertical ** 2 <= 1:
                        p = (x + dx, ground_y + height + dy, z + dz)
                        if p[1] > ground_y and p not in out:
                            out[p] = leaves
        return out
    if style == 'round':
        r = 3.0 if height <= 6 else 4.0
        cy = ground_y + height
        for dx in range(-4, 5):
            for dz in range(-4, 5):
                for dy in range(-3, 3):
                    if dx * dx + dz * dz + dy * dy <= r * r + 0.5 and (dx or dz or dy >= 0):
                        p = (x + dx, cy + dy, z + dz)
                        if p[1] > ground_y and p not in out:
                            out[p] = leaves
        return out
    # plane: crown high up, several overlapping lobes (irregular, open underneath)
    top = ground_y + height
    lobes = [(0, 0, 0, 3.2)]
    for _ in range(rnd.randint(4, 6)):
        ang = rnd.uniform(0, 6.283)
        dist = rnd.uniform(1.5, 3.8)
        lobes.append((round(dist * math.cos(ang)), round(dist * math.sin(ang)), rnd.randint(-1, 1), rnd.uniform(2.0, 3.0)))
    for lx, lz, ly, lr in lobes:
        for dx in range(-5, 6):
            for dz in range(-5, 6):
                for dy in range(-2, 3):
                    if (dx - lx) ** 2 + (dz - lz) ** 2 + (dy * 1.3) ** 2 <= lr * lr:
                        p = (x + dx, top + ly + dy, z + dz)
                        if p[1] > ground_y + height // 2 and p not in out:
                            out[p] = leaves
    return out


def bush_voxels(x: int, z: int, ground_y: int) -> dict[tuple[int, int, int], str]:
    """Low leaf clump on the lawn (no trunk).

    @param x: centre X
    @param z: centre Z
    @param ground_y: lawn surface Y
    @returns: voxel map
    """
    out = {(x, ground_y + 1, z): DEFAULT_LEAVES, (x, ground_y + 2, z): DEFAULT_LEAVES}
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        out[(x + dx, ground_y + 1, z + dz)] = DEFAULT_LEAVES
    return out
