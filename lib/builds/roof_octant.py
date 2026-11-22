"""Roof blocks fitted to a half-block-cell volume (the "octant" roof fit, #415).

A pitched roof is first designed as a solid volume made of half-block cells: every block is
2 x 2 x 2 cells, and the roof surface is a plane through the fitted rectangle of the footprint
(so a house drawn square to an angled road keeps its own axes). Each block is then matched to
the closest of air, full block, bottom or top slab, and bottom stairs in four facings and five
shapes, by counting the cells that differ. A stair's corner shape is not free: the game derives
it from same-height neighbouring stairs, so the fit evaluates every stair with the shape that
``roof_geometry.stair_shape_from_neighbours`` gives it in the current arrangement and adjusts
facings until that settles. A stair is a stair whatever its material.

The rim (eave columns outside the footprint with an open side) is held to one first-layer block that
is air, a bottom slab or a stair, so the roof edge is never a thick full-block lip (#414); columns
elsewhere, including notches of a staircase outline, are fitted freely.

Cell patterns, per block, for a bottom stair facing ``f`` (the tall side is the ``f`` side):

* bottom layer: all four cells;
* top layer: straight = the ``f`` half; outer corner = the ``f`` half and the left (or right)
  half intersected; inner corner = the ``f`` half and the left (or right) half united.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from roof_geometry import fit_rotated_rectangle, stair_shape_from_neighbours
from roof_material import family_variants

LEFT = {'east': 'north', 'west': 'south', 'south': 'east', 'north': 'west'}
RIGHT = {'east': 'south', 'west': 'north', 'south': 'west', 'north': 'east'}
DIRS = {'north': (0, -1), 'south': (0, 1), 'east': (1, 0), 'west': (-1, 0)}
SHAPES = ('straight', 'outer_left', 'outer_right', 'inner_left', 'inner_right')
MAX_PASSES = 30

BOTTOM = frozenset((dx, 0, dz) for dx in (0, 1) for dz in (0, 1))
TOP = frozenset((dx, 1, dz) for dx in (0, 1) for dz in (0, 1))
FULL = BOTTOM | TOP
OPTIONS = ((('air',), frozenset()), (('full',), FULL),
           (('slab', 'bottom'), BOTTOM), (('slab', 'top'), TOP),
           *((('stair', face), None) for face in DIRS))
STATIC_CELLS = {state: cells for state, cells in OPTIONS if cells is not None}
# The rim of the roof (its outermost overhanging columns) is one slab or stair thick with nothing under it (#414).
RIM_OPTIONS = tuple(o for o in OPTIONS if o[0][0] in ('air', 'stair') or o[0] == ('slab', 'bottom'))


def _side(face: str) -> frozenset:
    """Horizontal cells on one side of a block (``(dx, dz)`` with 0 = west/north half)."""
    return frozenset((dx, dz) for dx in (0, 1) for dz in (0, 1)
                     if (face == 'north' and dz == 0) or (face == 'south' and dz == 1)
                     or (face == 'east' and dx == 1) or (face == 'west' and dx == 0))


def stair_cells(facing: str, shape: str) -> frozenset:
    """Cells a bottom stair fills.

    @param facing: direction of the tall side
    @param shape: ``straight`` / ``outer_left`` / ``outer_right`` / ``inner_left`` / ``inner_right``
    @returns: set of ``(dx, dy, dz)`` cells, ``dx``/``dz`` 0 = west/north half, ``dy`` 0 = lower half
    """
    front = _side(facing)
    if shape == 'straight':
        top = front
    elif shape == 'outer_left':
        top = front & _side(LEFT[facing])
    elif shape == 'outer_right':
        top = front & _side(RIGHT[facing])
    elif shape == 'inner_left':
        top = front | _side(LEFT[facing])
    else:
        top = front | _side(RIGHT[facing])
    return BOTTOM | frozenset((dx, 1, dz) for dx, dz in top)


@dataclass
class OctantRoof:
    """Result of :func:`fit_octant_roof`.

    @ivar blocks: ``{(x, y, z): ('full',) | ('slab', half) | ('stair', facing, shape)}``
    @ivar wanted: cells in the designed roof volume
    @ivar missed: cells where the placed blocks differ from the design
    @ivar angle: fitted rectangle angle in degrees
    @ivar settled: whether the facing descent stopped on its own (False: it hit ``MAX_PASSES``)
    """
    blocks: dict
    wanted: int
    missed: int
    angle: int
    settled: bool = True


def _surface(own: set, pitch: float, y0: int, pad: float):
    """Roof surface function ``(x, z) -> top height or None`` through the fitted rectangle."""
    fit = fit_rotated_rectangle(own, min_angle=0)
    if fit is None:
        return None, None
    deg, u0, u1, v0, v1, _fill = fit
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))

    def height(px: float, pz: float):
        u = px * c + pz * s
        v = -px * s + pz * c
        d = min(u - (u0 - pad), (u1 + pad) - u, v - (v0 - pad), (v1 + pad) - v)
        return None if d < 0 else y0 + 0.5 + pitch * d
    return height, deg


EAVES = (1.0, 0.75, 1.25)     # eave overhangs tried; the plane sits on a quarter-block phase of the grid


def fit_octant_roof(own: set, columns: set, pitch: float, y0: int,
                    full_columns: set | None = None) -> OctantRoof | None:
    """Fit roof blocks to a half-block-cell volume over a rectangular footprint.

    The eave overhang is tried at a few values a quarter block apart and the fit that leaves the
    fewest cells wrong is kept (ties keep the first, 1 cell).

    @param own: owned ``(x, z)`` columns of the roof (the footprint)
    @param columns: every column the roof may use (footprint plus eave ring)
    @param pitch: rise per run, above 0.5
    @param y0: first roof layer; the outermost eave is a bottom slab at this layer
    @param full_columns: columns that must be solid full blocks (a chimney)
    @returns: the fitted roof, or ``None`` when the footprint is not close to a rectangle
    """
    fits = [_fit(own, columns, pitch, y0, full_columns, pad) for pad in EAVES]
    fits = [f for f in fits if f is not None]
    return min(fits, key=lambda f: f.missed / f.wanted) if fits else None


def _fit(own: set, columns: set, pitch: float, y0: int, full_columns: set | None,
         pad: float) -> OctantRoof | None:
    """One fit at a given eave overhang (see :func:`fit_octant_roof`)."""
    surface, deg = _surface(own, pitch, y0, pad)
    if surface is None:
        return None
    full_columns = full_columns or set()
    # Rim: eave columns outside the footprint with an open side. Columns in a notch of a staircase
    # outline are inside the roof and are fitted freely.
    rim = {(x, z) for x, z in columns if (x, z) not in own
           and any((x + dx, z + dz) not in columns for dx, dz in DIRS.values())}
    design: dict = {}
    for x, z in sorted(columns):
        heights = {(dx, dz): surface(x + (dx + 0.5) / 2, z + (dz + 0.5) / 2)
                   for dx in (0, 1) for dz in (0, 1)}
        y = y0
        while True:
            cells = frozenset((dx, dy, dz) for (dx, dz), h in heights.items() if h is not None
                              for dy in (0, 1) if y + (dy + 0.5) / 2 < h)
            if not cells:
                break
            design[x, y, z] = cells
            y += 1
    # The fit works to the design, except that a rim column only ever holds its first layer.
    target = {pos: cells for pos, cells in design.items() if (pos[0], pos[2]) not in rim or pos[1] == y0}
    order = sorted(target, key=lambda p: (p[1], p[2], p[0]))
    grid: dict = {}

    def effective(state, pos):
        adjacent = {}
        for face, (dx, dz) in DIRS.items():
            n = grid.get((pos[0] + dx, pos[1], pos[2] + dz))
            adjacent[face] = (n[1], 'bottom') if n is not None and n[0] == 'stair' else None
        return stair_shape_from_neighbours(state[1], 'bottom', adjacent)

    def cells_of(pos):
        state = grid.get(pos, ('air',))
        if state[0] == 'stair':
            return stair_cells(state[1], effective(state, pos))
        return STATIC_CELLS[state]

    def cost(pos):
        return len(cells_of(pos) ^ target.get(pos, frozenset()))

    for pos in order:
        if (pos[0], pos[2]) in full_columns:
            grid[pos] = ('full',)
            continue
        want = target[pos]
        options = RIM_OPTIONS if (pos[0], pos[2]) in rim else OPTIONS
        candidates = [(len(cells ^ want), i, state) for i, (state, cells) in enumerate(options)
                      if cells is not None]
        for face in DIRS:
            for shape in SHAPES:
                candidates.append((len(stair_cells(face, shape) ^ want), len(candidates), ('stair', face)))
        grid[pos] = min(candidates)[2]
    fixed = {pos for pos in order if (pos[0], pos[2]) in full_columns}
    settled = False
    for _ in range(MAX_PASSES):
        changed = False
        for pos in order:
            if pos in fixed:
                continue
            around = [(pos[0] + dx, pos[1], pos[2] + dz) for dx, dz in DIRS.values()]
            current = grid[pos]

            def local():
                return cost(pos) + sum(cost(n) for n in around if n in grid)
            best, best_cost = current, local()
            for state, _cells in (RIM_OPTIONS if (pos[0], pos[2]) in rim else OPTIONS):
                if state == current:
                    continue
                grid[pos] = state
                value = local()
                if value < best_cost:
                    best, best_cost = state, value
            grid[pos] = best
            changed |= best != current
        if not changed:
            settled = True
            break
    blocks = {}
    for pos, state in grid.items():
        if state[0] == 'air':
            continue
        blocks[pos] = ('stair', state[1], effective(state, pos)) if state[0] == 'stair' else state
    # Quality is measured against the whole design, so cells a rim column gives up count as missed.
    placed = {pos: cells_of(pos) for pos in grid}
    missed = sum(len(placed.get(pos, frozenset()) ^ design.get(pos, frozenset()))
                 for pos in set(design) | set(placed))
    return OctantRoof(blocks, sum(len(c) for c in design.values()), missed, deg, settled)


def block_states(material: str, blocks: dict) -> dict:
    """Block-state strings for fitted roof blocks (waterlogged false, explicit stair shape).

    @param material: shared roof material family
    @param blocks: ``OctantRoof.blocks``
    @returns: ``{(x, y, z): state}``
    """
    names = family_variants(material)
    out = {}
    for pos, block in blocks.items():
        if block[0] == 'full':
            out[pos] = names['full']
        elif block[0] == 'slab':
            out[pos] = f'{names["slab"]}[type={block[1]},waterlogged=false]'
        else:
            out[pos] = (f'{names["stairs"]}[facing={block[1]},half=bottom,'
                        f'shape={block[2]},waterlogged=false]')
    return out
