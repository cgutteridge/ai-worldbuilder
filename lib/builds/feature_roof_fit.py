"""Feature roofs fitted to a designed half-block-cell volume (``roofs[label].stairs: "fit"``, #472).

The octant fit that house roofs use (:mod:`roof_octant`, #415) brought to ``feature_build`` roofs.
The roof is first designed as a continuous surface over its columns, then each block is matched to
air, a full block, a bottom slab or a bottom stair (with the corner shape its stair neighbours give
it) by the cells it gets wrong (:func:`roof_octant.match_blocks`). Stairs then appear only where
the surface really climbs within a block (a slope, or the edge of a layer) and level blocks fill
flat layers and one-block steps, without a per-case rule.

Where the surface comes from:

* a column with a fitted plane or gable (``zones[label].surface: "fit"``): that surface itself, in
  block units, so a rotated gable is one clean plane on each side of its ridge. Lidar is a guide:
  the fitted ridge is not clipped to the next zone level and ``flat_tol_m`` does not flatten it;
* a flat layer (a flat fitted surface, or a column the zone logic keeps flat): its built level;
* any other column (raw lidar heights, or a footprint hip): the built heightfield, interpolated
  between neighbouring columns of the same roof volume, so a one-block rise is a slope and a
  step between volumes stays a step;
* an unzoned roof: a hip in the footprint's own rectangle when it is close to one (as house roofs
  do), else a hip that rises from every edge of the outline (:func:`outline_hip_top`).

The fit never offers a top slab (a top slab never sits over air), the first layer of a column the
building owns is never air, and a stair may only face a way the designed surface rises across its
block (never off the roof), so a flat layer or a crisp step gets no stair at all. Pure geometry: it places nothing and reads no world.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from roof_octant import (
    BOTTOM,
    DIRS,
    EAVES,
    FEATURE_OPTIONS,
    RIM_OPTIONS,
    design_volume,
    fitted_blocks,
    hip_surface,
    match_blocks,
    missed_cells,
    stair_cells,
)

FIT_PITCH = 1.0         # an unzoned hip rises a block per block, like the default hip
BASE_OPTIONS = tuple(o for o in FEATURE_OPTIONS if o[0] != ('air',))
RISE_EPS = 0.02         # blocks: a facing whose half rises less than this across a block is level


@dataclass
class RoofFit:
    """A fitted feature roof.

    @ivar blocks: ``{(x, y, z): ('full',) | ('slab', 'bottom') | ('stair', facing, shape)}``
    @ivar tops: ``{(x, z): y}`` of each column's top block
    @ivar wanted: cells in the designed volume
    @ivar missed: cells where the blocks differ from the design
    @ivar source: what the surface came from, for the dry-run line
    @ivar settled: False when the block search stopped at its pass limit
    """
    blocks: dict
    tops: dict
    wanted: int
    missed: int
    source: str
    settled: bool = True

    def half_heights(self) -> dict:
        """``{(x, z): half-height}`` of each top block, in the heightfield's terms (``roof_block_y``).

        A full block or stair at ``y`` is ``2 * y``; a bottom slab at ``y`` is ``2 * y - 1``.
        """
        out = {}
        for c, y in self.tops.items():
            block = self.blocks[(c[0], y, c[1])]
            out[c] = 2 * y - 1 if block[0] == 'slab' else 2 * y
        return out

    def skin(self, columns) -> set:
        """Blocks under a column's top that the roof itself shows, so they take roof blocks.

        Any slab or stair below a top, and a full block beside a roof column whose block at that
        height is not full (seen through a stair's open half or over a lower step one block down).
        A full block that only faces outside the roof (a gable end) or another full block is left
        to the walls.

        @param columns: the roof's columns
        @returns: ``{(x, y, z)}`` below the tops
        """
        columns = set(columns)
        out = set()
        for (x, y, z), block in self.blocks.items():
            if y == self.tops[(x, z)]:
                continue
            if block[0] != 'full':
                out.add((x, y, z))
                continue
            for dx, dz in DIRS.values():
                n = (x + dx, z + dz)
                if n not in columns or n not in self.tops or self.tops[n] < y - 1:
                    continue
                if self.blocks.get((n[0], y, n[1]), ('air',))[0] != 'full':
                    out.add((x, y, z))
                    break
        return out

    def summary(self, label: str) -> str:
        """Dry-run line: the surface, block counts and the design cells the blocks miss."""
        tops = [self.blocks[(c[0], y, c[1])][0] for c, y in self.tops.items()]
        share = self.missed / self.wanted if self.wanted else 0.0
        return (f'{label} roof stairs fit: {self.source}; top blocks: {tops.count("stair")} stair(s), '
                f'{tops.count("slab")} bottom slab(s), {tops.count("full")} full; '
                f'{self.missed} of {self.wanted} half-block cells missed ({share:.0%})'
                + ('' if self.settled else ' (block search stopped at its pass limit)'))


def heightfield_top(half_heights: dict, level=frozenset(), volume=None):
    """Surface through a built heightfield, interpolated between columns of one roof volume.

    Each column's top is its built membrane top (``half / 2 + 1``). A point inside a column takes
    the bilinear blend of that column and its neighbours toward the point; a neighbour outside the
    roof or in another volume, and every neighbour of a level column, counts as the column's own
    top, so a step between volumes and a flat layer stay crisp.

    @param half_heights: ``{(x, z): half-height}`` (regularised)
    @param level: columns that stay level (flat layers)
    @param volume: ``{(x, z): volume id}``; None = one volume
    @returns: ``top((x, z), px, pz) -> height``
    """
    tops = {c: h / 2 + 1 for c, h in half_heights.items()}
    volume = volume or {}

    def top(c, px, pz):
        own = tops[c]
        if c in level:
            return own
        sx = 1 if px - c[0] > 0.5 else -1
        sz = 1 if pz - c[1] > 0.5 else -1

        def at(n):
            return tops[n] if n in tops and volume.get(n) == volume.get(c) else own
        tx, tz, td = at((c[0] + sx, c[1])), at((c[0], c[1] + sz)), at((c[0] + sx, c[1] + sz))
        return 0.5625 * own + 0.1875 * (tx + tz) + 0.0625 * td
    return top


def zoned_top(half_heights: dict, base: dict, level: set, volume: dict, surfaces: dict, metres_to_y: dict):
    """Design surface for a zoned roof: fitted slopes where there are any, else the heightfield.

    Where two sloped volumes meet (a rear wing running into a main roof, the wings of an L), a
    column beside the other volume takes the higher of the two surfaces, so the lower roof runs up
    into the higher one and the join is a valley, not a slot against a wall.

    @param half_heights: built ``{(x, z): half-height}``
    @param base: ``{(x, z): eave y}`` (a fitted slope never dips below half a block above it)
    @param level: flat-layer columns
    @param volume: ``{(x, z): volume id}``
    @param surfaces: ``{(x, z): roof_surface.Surface}`` for fitted columns
    @param metres_to_y: ``{(x, z): block Y minus metres}`` of the column's zone (``eave - level``)
    @returns: ``top((x, z), px, pz) -> height``
    """
    sloped = {c for c, s in surfaces.items() if s.kind != 'flat' and c in half_heights}
    field = heightfield_top(half_heights, (set(level) | set(surfaces)) - sloped, volume)
    # sloped columns of other volumes beside each sloped column: their surfaces meet it at a valley
    meets = {}
    for c in sloped:
        others = {}
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                n = (c[0] + dx, c[1] + dz)
                if n in sloped and volume.get(n) != volume.get(c):
                    others.setdefault(volume.get(n), n)
        meets[c] = [c, *others.values()]

    def top(c, px, pz):
        if c not in sloped:
            return field(c, px, pz)
        # a fitted surface is in column coordinates (column x reads the lidar at its centre, x + 0.5)
        h = max(float(surfaces[n].height(px - 0.5, pz - 0.5)) + metres_to_y[n] for n in meets[c])
        return max(h + 1, base[c] + 0.5)
    return top


def fit_roof(columns, top, base: dict, owned, rim=frozenset(), source: str = '') -> RoofFit:
    """Match blocks to the volume under ``top``.

    @param columns: ``(x, z)`` columns of the roof
    @param top: design surface (see :func:`roof_octant.design_volume`)
    @param base: ``{(x, z): first layer y}``
    @param owned: columns inside the building (their first layer is never air)
    @param rim: overhang columns with an open side: one layer of air, bottom slab or stair
    @param source: what the surface came from (dry-run text)
    @returns: the fit
    """
    columns, owned, rim = set(columns), set(owned), set(rim)
    design = design_volume(columns, top, base)
    uphill = {c: _uphill(c, top, base[c]) for c in columns}
    target = {pos: cells for pos, cells in design.items()
              if (pos[0], pos[2]) not in rim or pos[1] == base[(pos[0], pos[2])]}
    for c in owned:
        target.setdefault((c[0], base[c], c[1]), BOTTOM)

    def options_for(pos):
        c = (pos[0], pos[2])
        if c in rim:
            options = RIM_OPTIONS
        else:
            options = BASE_OPTIONS if c in owned and pos[1] == base[c] else FEATURE_OPTIONS
        # a stair faces up the designed surface, and never off the roof
        return tuple(o for o in options if o[0][0] != 'stair'
                     or (o[0][1] in uphill[c]
                         and (c[0] + DIRS[o[0][1]][0], c[1] + DIRS[o[0][1]][1]) in columns))
    grid, settled = match_blocks(target, options_for)
    _fill_grooves(grid, options_for, rim)
    return _finish(grid, design, base, source, settled)


def _quarter_tops(blocks: dict) -> dict:
    """Top of each quarter of each column's top block, in half blocks: ``{(2x + dx, 2z + dz): h}``."""
    tops: dict = {}
    for x, y, z in blocks:
        if y > tops.get((x, z), y - 1):
            tops[(x, z)] = y
    out = {}
    for (x, z), y in tops.items():
        block = blocks[(x, y, z)]
        cells = stair_cells(block[1], block[2]) if block[0] == 'stair' else None
        for dx in (0, 1):
            for dz in (0, 1):
                if block[0] == 'stair':
                    h = 2 * y + (2 if (dx, 1, dz) in cells else 1)
                else:
                    h = 2 * y + (1 if block[0] == 'slab' else 2)
                out[(2 * x + dx, 2 * z + dz)] = h
    return out


def grooves(blocks: dict) -> set:
    """Columns with a slot one or two quarters wide, lower than both sides along a line: a gap where stairs meet.

    @param blocks: fitted blocks (``RoofFit.blocks``)
    @returns: ``{(x, z)}``
    """
    q = _quarter_tops(blocks)
    out = set()
    for (a, b), h in q.items():
        for da, db in ((1, 0), (0, 1)):
            before = q.get((a - da, b - db))
            if before is None or before <= h:
                continue
            after = q.get((a + da, b + db))
            if after is not None and after > h:
                out.add((a // 2, b // 2))
            elif after == h and q.get((a + 2 * da, b + 2 * db), h) > h:
                out.update({(a // 2, b // 2), ((a + da) // 2, (b + db) // 2)})
    return out


def _fill_grooves(grid: dict, options_for, level=frozenset(), passes: int = 8) -> None:
    """Re-pick top blocks that leave a groove (:func:`grooves`), in place.

    The cell match can leave a slab between two stairs that face each other on a ridge, or two
    stairs back to back: a slot in the roof. Each such column's top block, and the top blocks
    beside it, try their other options, and a block one layer higher on a full block (raising the
    column half a block or a block); the change that leaves the fewest grooves in the whole roof is
    kept when that is fewer than now. A grooved column also takes a change that clears its own groove
    without adding one overall (the groove moves to a neighbour, which the next pass fills), so a
    slot one quarter too low along a row is filled from one end.

    @param grid: ``{pos: state}`` from :func:`roof_octant.match_blocks` (changed in place)
    @param options_for: the options the match used
    @param level: columns that never rise (the overhang rim holds one layer)
    @param passes: most repair passes
    """
    def found():
        return grooves(fitted_blocks(grid))

    for _ in range(passes):
        blocks = fitted_blocks(grid)
        bad = grooves(blocks)
        if not bad:
            return
        tops: dict = {}
        for x, y, z in blocks:
            tops[(x, z)] = max(y, tops.get((x, z), y))
        changed = False
        for c in sorted(bad | {(x + dx, z + dz) for x, z in bad for dx, dz in DIRS.values()}):
            if c not in tops:
                continue
            pos = (c[0], tops[c], c[1])
            above = (c[0], tops[c] + 1, c[1])
            saved = {pos: grid[pos], above: grid.get(above)}
            trials = [{pos: state} for state, _cells in options_for(pos)
                      if state not in (saved[pos], ('air',))]
            if c not in level:
                trials += [{pos: ('full',), above: state} for state, _cells in options_for(above)
                           if state != ('air',)]
            current = found()
            now = len(current)
            best, best_key = None, (now, c in current)
            for trial in trials:
                grid.update(trial)
                after = found()
                key = (len(after), c in after)
                if key < best_key and len(after) <= now:
                    best, best_key = trial, key
                for k, v in saved.items():
                    if v is None:
                        grid.pop(k, None)
                    else:
                        grid[k] = v
            if best is not None:
                grid.update(best)
                changed = True
        if not changed:
            return


def _uphill(c, top, floor: float) -> set:
    """Facings toward which the design surface rises across column ``c`` (a stair may face only these).

    The surface is read at the block's four quarter points; a facing qualifies when its half is
    higher on average than the opposite half by more than :data:`RISE_EPS` blocks, so a flat layer
    or a column of a crisp step allows no stair at all.
    """
    h = {}
    for dx in (0, 1):
        for dz in (0, 1):
            v = top(c, c[0] + (dx + 0.5) / 2, c[1] + (dz + 0.5) / 2)
            h[(dx, dz)] = floor if v is None else v
    east = (h[(1, 0)] + h[(1, 1)] - h[(0, 0)] - h[(0, 1)]) / 2
    south = (h[(0, 1)] + h[(1, 1)] - h[(0, 0)] - h[(1, 0)]) / 2
    out = set()
    for face, rise in (('east', east), ('west', -east), ('south', south), ('north', -south)):
        if rise > RISE_EPS:
            out.add(face)
    return out


def _finish(grid: dict, design: dict, base: dict, source: str, settled: bool) -> RoofFit:
    """Fill any air under a column's top with full blocks and collect the result."""
    blocks = fitted_blocks(grid)
    tops: dict = {}
    for x, y, z in blocks:
        tops[(x, z)] = max(y, tops.get((x, z), y))
    for (x, z), y_top in tops.items():
        for y in range(base[(x, z)], y_top):
            if (x, y, z) not in blocks:
                blocks[(x, y, z)] = ('full',)
                grid[(x, y, z)] = ('full',)
    return RoofFit(blocks, tops, sum(len(c) for c in design.values()), missed_cells(grid, design),
                   source, settled)


def outline_hip_top(columns, eave: int, pitch: float = FIT_PITCH):
    """Hip surface that follows any outline: height grows with the distance to the roof's edge.

    The distance from a point to the nearest column outside the roof (its whole square), so every
    face rises at ``pitch`` from its own edge, whatever the outline's angle or shape (an L, a
    rhombus): a straight-skeleton hip. The outer edge of the roof sits at ``eave + 0.5``.

    @param columns: every roof column (footprint plus eave ring)
    @param eave: eave layer y
    @param pitch: rise per run
    @returns: ``top((x, z), px, pz) -> height``
    """
    columns = set(columns)
    outside = {(x + dx, z + dz) for x, z in columns for dx in (-1, 0, 1) for dz in (-1, 0, 1)} - columns

    def top(_c, px, pz):
        d = min(math.hypot(max(ox - px, 0.0, px - ox - 1), max(oz - pz, 0.0, pz - oz - 1))
                for ox, oz in outside)
        return eave + 0.5 + pitch * d
    return top


def overhang_rim(own, columns) -> set:
    """Overhang columns that hold one layer at the eave: outside the footprint with an open side.

    A one-column dent in a staircase outline (open on one side, with roof columns diagonally beyond
    it on both hands) is inside the roof and is fitted freely, like the notches the house fit allows.

    @param own: the building's columns
    @param columns: every roof column (footprint plus eave ring)
    @returns: ``{(x, z)}``
    """
    own, columns = set(own), set(columns)
    out = set()
    for x, z in columns - own:
        for dx, dz in DIRS.values():
            if (x + dx, z + dz) in columns:
                continue
            px, pz = dz, dx                                  # the two sides across the open one
            dent = ((x + dx + px, z + dz + pz) in columns and (x + dx - px, z + dz - pz) in columns)
            if not dent:
                out.add((x, z))
                break
    return out


def fit_hip(own, columns, eave: int) -> RoofFit:
    """Fit an unzoned pitched roof as a hip.

    A footprint close to a rectangle takes a hip in the rectangle's own frame, as the house fit does
    (:func:`roof_octant.fit_octant_roof`): the eave overhang is tried at a few values a quarter block
    apart and the fit that misses the smallest share of cells is kept. The rectangle has to follow
    the outline: when it would lift any rim column more than a block above the eave (a rhombus or a
    bent outline inside its rectangle), the hip follows the outline instead (:func:`outline_hip_top`).

    @param own: the building's columns
    @param columns: every roof column (footprint plus eave ring)
    @param eave: eave layer y
    @returns: the fit
    """
    own, columns = set(own), set(columns)
    base = dict.fromkeys(columns, eave)
    edge = {c for c in columns if any((c[0] + dx, c[1] + dz) not in columns for dx, dz in DIRS.values())}
    rim = overhang_rim(own, columns)
    fits = []
    for pad in EAVES:
        surface, deg = hip_surface(own, FIT_PITCH, eave, pad)
        if surface is None:
            break
        if any((surface(c[0] + 0.5, c[1] + 0.5) or eave) > eave + 1.5 for c in edge):
            continue
        fits.append(fit_roof(columns, lambda _c, px, pz, s=surface: s(px, pz), base, own, rim,
                             source=f'hip on a rectangle at {deg} degrees'))
    if fits:
        return min(fits, key=lambda f: f.missed / f.wanted)
    return fit_roof(columns, outline_hip_top(columns, eave), base, own, rim,
                    source='hip following the outline (not close to a rectangle)')


def fit_states(fit: RoofFit, material_of, warnings: list | None = None) -> dict:
    """Block states for a fit, each column in its own roof material.

    A material without a stair or slab variant places its full block and adds a warning, as
    ``roof_geometry.pitched_roof_states`` does.

    @param fit: the fitted roof
    @param material_of: ``material_of((x, z)) -> material`` key for ``roof_material.family_variants``
    @param warnings: optional list that receives fallback messages
    @returns: ``{(x, y, z): state}``
    """
    from roof_material import family_variants
    out = {}
    for (x, y, z), block in fit.blocks.items():
        mat = material_of((x, z))
        names = family_variants(mat)
        want = {'full': 'full', 'slab': 'slab', 'stair': 'stairs'}[block[0]]
        if want not in names:
            if warnings is not None:
                warnings.append(f'roof material {mat!r} has no {want}; using full block {names["full"]}')
            out[(x, y, z)] = names['full']
        elif block[0] == 'full':
            out[(x, y, z)] = names['full']
        elif block[0] == 'slab':
            out[(x, y, z)] = f'{names["slab"]}[type=bottom,waterlogged=false]'
        else:
            out[(x, y, z)] = f'{names["stairs"]}[facing={block[1]},half=bottom,shape={block[2]},waterlogged=false]'
    return out
