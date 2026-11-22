"""A read-only world built from DEFRA LIDAR + OSM footprints (the composite lidar world).

Everything that reads "the lidar" does so through a world with ``block(x, y, z)`` (lidar_heightmap, campus, geo probe).
``DefraWorld`` answers those calls from shims/defra_lidar.py instead of region files, using Geocraft-style block
rules (lib/Minecraft/Projection.pm render_xz):

  ground      solid up to SEA_LEVEL + int(DTM)
  feature     int(DSM - DTM) more solid blocks above it  (same quantity as lidar_query ``diff``)
  building    a column inside an OSM building footprint: at least BUILDING_MIN of feature (Geocraft's
              feature_min_height), wall blocks, and a light-grey carpet on top
  tree        outside every footprint, FEATURE_TREE_MIN or more tall, and foliage: first-return DSM at least CANOPY_MIN
              above last-return DSM (laser reaches through leaves, not through roofs or walls). Leaf blocks run up to the
              first-return crown top. Without the first-return tile cached the test falls back to tall-and-outside, which
              also catches walls and unmapped buildings. Geocraft grew trees procedurally on nature landuse only, so it
              never matched real vegetation: this finds more crowns than the save has.
  structure   tall (FEATURE_TREE_MIN+), outside every footprint, no foliage (wall, unmapped building): solid to its top,
              kind STRUCTURE so ground estimates and tree counts skip it (only with the first-return tile cached)
  else        solid up to the top of the feature

Unfiltered DSM noise: cars/trains are not built form; street furniture is often worth keeping
(site/GEOCRAFT.txt §1a). Footprints come from the OSMIndex (reference/osm clips); pass ``idx`` for another place.
"""
from __future__ import annotations

import numpy as np

import cellmath
import defra_lidar as dl
import lidar_heightmap as lh

BUILDING_MIN = 3             # Geocraft config/blocks feature_min_height for building classes
FEATURE_TREE_MIN = 3         # feature height outside a footprint that is drawn as leaves
CANOPY_MIN = 1.5             # metres first return sits above last return: foliage. Chosen on a campus cell: roofs and
                             # lawns stay below it, crowns are well above (median 1.65 m, 90th percentile 5.5 m)
STONE, WALL, CARPET, LEAVES = 'minecraft:stone', 'minecraft:bricks', 'minecraft:light_gray_carpet', 'minecraft:oak_leaves'

_IDX = None


def shared_index():
    """The OSM footprint index, loaded once per process."""
    global _IDX
    if _IDX is None:
        from osm_footprints import OSMIndex
        _IDX = OSMIndex()
    return _IDX


def footprint_mask(cell: tuple[int, int], idx) -> np.ndarray:
    """bool [128, 128]: columns of the cell whose centre is inside an OSM building footprint."""
    x0, z0, x1, z1 = cellmath.bounds(cell)
    mask = np.zeros((cellmath.CELL, cellmath.CELL), dtype=bool)
    for (x, z) in idx.cells():
        if x0 <= x <= x1 and z0 <= z <= z1:
            mask[x - x0, z - z0] = True
    return mask


def has_first_return(lidar: dl.DefraLidar, cell: tuple[int, int]) -> bool:
    """True when the first-return DSM tile(s) for the cell are cached."""
    return not lidar.missing('DSM_FIRST', *dl.mc_box_en(*cellmath.bounds(cell)))


def heightmap(cell: tuple[int, int], lidar: dl.DefraLidar, idx) -> dict:
    """lidar_heightmap-compatible map of one cell: cell, x0, z0, h, base, terrain, roof, kind.

    ``terrain`` is the bare-terrain (DTM) ground height. ``base`` remains the
    solid surface top (DSM-derived except under trees). Free ground uses
    ``terrain`` (#172); footprint / roof work keeps ``base``.

    Trees use the first-return DSM when it is cached (see module doc), else any tall column outside a footprint.
    """
    x0, z0, x1, z1 = cellmath.bounds(cell)
    m = dl.mc_heights(lidar, x0, z0, x1, z1)
    bld = footprint_mask(cell, idx)
    ground = m['ground_y']
    feature = np.where(bld, np.maximum(m['feature'], BUILDING_MIN), m['feature'])
    if has_first_return(lidar, cell):
        xs, zs = np.meshgrid(np.arange(x0, x1 + 1), np.arange(z0, z1 + 1), indexing='ij')
        from world_frame import mc_grid_to_en
        e, n = mc_grid_to_en(xs, zs)
        first_f = lidar.sample('DSM_FIRST', e, n, geocraft=False)
        canopy = np.nan_to_num(first_f - lidar.sample('DSM', e, n, geocraft=False)) >= CANOPY_MIN
        crown = np.maximum(np.trunc(lidar.sample('DSM_FIRST', e, n) - m['dtm']).astype(np.int64), feature)
        tree = ~bld & canopy & (crown >= FEATURE_TREE_MIN)
        structure = ~bld & ~tree & (feature >= FEATURE_TREE_MIN)
        feature = np.where(tree, crown, feature)
    else:
        tree = ~bld & (feature >= FEATURE_TREE_MIN)
        structure = np.zeros_like(tree)
    top = ground + feature
    h = top.astype(np.int16)
    base = np.where(tree, ground, top).astype(np.int16)
    terrain = ground.astype(np.int16)
    roof = np.where(bld, top + 1, 0).astype(np.int16)
    kind = np.full(h.shape, lh.GROUND, dtype=np.uint8)
    kind[bld] = lh.ROOF
    kind[structure] = lh.STRUCTURE
    kind[tree] = lh.TREE
    return {'cell': cell, 'x0': x0, 'z0': z0, 'h': h, 'base': base, 'terrain': terrain,
            'roof': roof, 'kind': kind}


class DefraWorld:
    """``block`` / ``uniform_air`` / ``ground`` over DEFRA heights, for the cells whose tiles are cached.

    @param lidar: DefraLidar (default: the shared cache, no downloads)
    @param idx: OSM footprint index (default: shared_index())
    """

    def __init__(self, lidar: dl.DefraLidar | None = None, idx=None):
        self.lidar = lidar or dl.DefraLidar(download=False)
        self._idx = idx
        self._maps: dict[tuple[int, int], dict] = {}
        self._covered: dict[tuple[int, int], bool] = {}

    @property
    def idx(self):
        if self._idx is None:
            self._idx = shared_index()
        return self._idx

    def trees_from_first_return(self, cell: tuple[int, int]) -> bool:
        """True when this cell's trees come from first-return LIDAR (else the wider tall-and-outside fallback)."""
        return has_first_return(self.lidar, cell)

    def covers(self, cell: tuple[int, int]) -> bool:
        """True when DTM and DSM tiles for the cell (and its bilinear edge) are cached."""
        if cell not in self._covered:
            box = dl.mc_box_en(*cellmath.bounds(cell))
            self._covered[cell] = not (self.lidar.missing('DTM', *box) or self.lidar.missing('DSM', *box))
        return self._covered[cell]

    def heightmap(self, cell: tuple[int, int]) -> dict:
        if cell not in self._maps:
            if not self.covers(cell):
                raise LookupError(f'DEFRA tiles for cell {cell[0]},{cell[1]} are not cached: '
                                  f'python3 shims/defra_lidar.py fetch --cell {cell[0]},{cell[1]}')
            self._maps[cell] = heightmap(cell, self.lidar, self.idx)
        return self._maps[cell]

    def _col(self, x: int, z: int) -> tuple[int, int, int, int]:
        hm = self.heightmap(cellmath.cell_of(x, z))
        i, j = x - hm['x0'], z - hm['z0']
        return int(hm['h'][i, j]), int(hm['base'][i, j]), int(hm['roof'][i, j]), int(hm['kind'][i, j])

    def block(self, x: int, y: int, z: int) -> str:
        h, base, roof, kind = self._col(x, z)
        if y < 0:
            return STONE
        if y <= base:
            return WALL if kind == lh.ROOF and y > base - BUILDING_MIN else STONE
        if roof and y == roof:
            return CARPET
        if kind == lh.TREE and y <= h:
            return LEAVES
        return 'minecraft:air'

    def uniform_air(self, x: int, y: int, z: int, airs) -> bool:
        h, _base, roof, _kind = self._col(x, z)
        return (y >> 4) << 4 > max(h, roof)

    def ground(self, x: int, z: int, top: int = 140, bottom: int = 40) -> int:
        return max(bottom, min(top, self._col(x, z)[1]))


# Type-facing names for lib/sources/lidar_provider.py (catalogue modules.world).
LidarWorld = DefraWorld
