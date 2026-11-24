"""Hip roofs designed in the plot's own frame, for unzoned feature roofs (``roofs[label].frame: "plot"``, #463).

The default feature hip (``feature_build.Build.roof_pitched``) raises each column by its Chebyshev distance
from the outline. On a footprint drawn at an angle to the grid that distance wobbles along a diagonal
eave, and the stair facing is picked from whichever neighbour happens to be higher, so a diagonal roof
face alternates east and north stairs (outer then inner corners) and shows the wall through their open
quarters.

Here the roof is designed first and the blocks follow from the design:

1. Fit the footprint with a rectangle in its own frame (``roof_geometry.fit_rotated_rectangle``) and grow
   it by the eave overhang. The roof is a hip on that rectangle: four planes, one per side, each rising
   from its eave, meeting at straight hips and a straight ridge in the plot frame.
2. Each column belongs to the face whose eave is nearest. A face has one stair facing: the grid direction
   nearest its uphill normal. On an exact diagonal (45 degrees) the tie goes the same way round on every
   face, so the four faces still take four different facings, as on a square-on hip.
3. Heights rise half a block per block along that facing (stair, slab, stair, slab): the face plane is
   scaled so that one step along the facing is one half-block, whatever the angle.
4. Blocks: on a whole-block height, a column on the rim (a side neighbour is not a roof column) or whose
   neighbour along its face's facing is higher is a stair facing that way (the edge of the roof, or a real
   slope); otherwise it is the level block of its height (a full block; a half-block height is a bottom
   slab), so the eave is never a thick full-block lip. So a face never mixes facings, and a stair always
   climbs. Corner shapes come from same-height neighbouring stairs (the game's rule), applied by the
   caller's final shape pass.
5. Nothing outside the footprint rises above the eave: an eave-ring column (not owned) is held at the eave
   course, because no wall holds it up. This matters where the footprint leaves a notch in its rectangle (a
   ragged corner, or an L or T that still fills the rectangle well enough): the notch stays at eave height and
   the footprint columns beside it show roof blocks, not wall, down to it. An owned rim column on a
   half-block height is lowered half a block, so the rim is a stair, not a slab.

Pure geometry: no world reads, no placing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from roof_geometry import ROOF_DELTA, fit_rotated_rectangle, roof_block_y
from roof_material import family_variants

PAD = 1.0            # eave overhang in cells beyond the outermost footprint centres
CARDINALS = ('east', 'south', 'west', 'north')     # world angle 0, 90, 180, 270 degrees (z grows south)
TIE_EPS_DEG = 1e-6


@dataclass(frozen=True)
class PlotHip:
    """A hip designed in the plot frame.

    @ivar half_heights: ``{(x, z): membrane top in half-blocks}`` (see ``roof_block_y``)
    @ivar facing: ``{(x, z): stair facing of the column's face}``
    @ivar face: ``{(x, z): face index}`` (0..3: the eave side the column is nearest)
    @ivar angle: fitted rectangle angle in degrees
    @ivar fill: footprint cells over fitted rectangle area
    """
    half_heights: dict
    facing: dict
    face: dict
    angle: int
    fill: float


def face_facing(nx: float, nz: float) -> str:
    """Grid stair facing nearest an uphill direction; exact diagonals turn the same way every time.

    @param nx: uphill direction, x component
    @param nz: uphill direction, z component
    @returns: ``east`` / ``south`` / ``west`` / ``north``
    """
    ang = math.degrees(math.atan2(nz, nx))
    return CARDINALS[round((ang - TIE_EPS_DEG) / 90.0) % 4]


def design_plot_hip(own: set, columns: set, eave_y: int, pad: float = PAD) -> PlotHip | None:
    """Design a plot-frame hip over the footprint (see the module text).

    @param own: owned ``(x, z)`` columns (the footprint the rectangle is fitted to)
    @param columns: every roof column (footprint plus eave ring)
    @param eave_y: block Y of the eave course (the outermost eave column is a block at this Y)
    @param pad: eave overhang in cells beyond the outermost footprint centres
    @returns: the design, or None when the footprint is not close to a rectangle in any frame
    """
    fit = fit_rotated_rectangle(own, min_angle=0)
    if fit is None:
        return None
    deg, u0, u1, v0, v1, fill = fit
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    # (distance from that eave, uphill direction x, z) per side: u grows along (c, s), v along (-s, c)
    faces = (
        (lambda u, v: u - (u0 - pad), (c, s)),
        (lambda u, v: (u1 + pad) - u, (-c, -s)),
        (lambda u, v: v - (v0 - pad), (-s, c)),
        (lambda u, v: (v1 + pad) - v, (s, -c)),
    )
    facings = [face_facing(*up) for _d, up in faces]
    # one step along the face's facing moves |n . f| along its normal: scale so that step is one half-block
    steps = []
    for (_d, (nx, nz)), f in zip(faces, facings):
        fx, fz = ROOF_DELTA[f]
        steps.append(nx * fx + nz * fz)
    half, facing, face = {}, {}, {}
    for x, z in columns:
        u = (x + 0.5) * c + (z + 0.5) * s
        v = -(x + 0.5) * s + (z + 0.5) * c
        dists = [d(u, v) for d, _up in faces]
        k = min(range(4), key=lambda i: (dists[i], i))
        depth = max(0.0, dists[k])
        half[(x, z)] = 2 * eave_y + int(math.floor(depth / steps[k] + 0.5))
        facing[(x, z)] = facings[k]
        face[(x, z)] = k
    for (x, z), h in half.items():
        if (x, z) not in own:
            half[(x, z)] = 2 * eave_y     # the eave ring has no wall under it: never above the eave course
        elif h % 2 and any((x + dx, z + dz) not in half for dx, dz in ROOF_DELTA.values()):
            half[(x, z)] = h - 1          # an owned rim column is a stair, not a slab
    return PlotHip(half, facing, face, int(deg), float(fill))


def plot_hip_states(hip: PlotHip, material: str) -> dict:
    """Membrane block states for a plot-frame hip (see the module text, step 4).

    A stair stands only on the rim or where the design rises toward its facing; anything else is the
    level block of its height.

    Stairs are written ``straight``; the caller's same-height shape pass gives corners their shape.

    @param hip: :func:`design_plot_hip` result
    @param material: roof material family (needs stairs and slabs)
    @returns: ``{(x, z): block state}`` placed at ``roof_block_y(hip.half_heights[(x, z)])``
    """
    names = family_variants(material)
    out = {}
    for (x, z), h in hip.half_heights.items():
        f = hip.facing[(x, z)]
        dx, dz = ROOF_DELTA[f]
        ahead = hip.half_heights.get((x + dx, z + dz))
        rim = any((x + ex, z + ez) not in hip.half_heights for ex, ez in ROOF_DELTA.values())
        if h % 2 == 0 and (rim or (ahead is not None and ahead > h)):
            out[(x, z)] = f'{names["stairs"]}[facing={f},half=bottom,shape=straight,waterlogged=false]'
        elif h % 2:
            out[(x, z)] = f'{names["slab"]}[type=bottom,waterlogged=false]'
        else:
            out[(x, z)] = names['full']
    return out


def exposed_backing(hip: PlotHip) -> set:
    """Cells under the membrane that show beside a lower neighbour's open stair or slab half.

    A stair or slab's open upper half looks sideways at the column beside it; when that column's membrane
    is higher, the block it looks at is the column's fill under its membrane. Those cells take the roof's
    full block, so a face reads as roof, not as wall quarters.

    @param hip: :func:`design_plot_hip` result
    @returns: ``{(x, y, z)}`` fill cells to make roof full blocks
    """
    out = set()
    for (x, z), h in hip.half_heights.items():
        y = roof_block_y(h)
        for dx, dz in ROOF_DELTA.values():
            nh = hip.half_heights.get((x + dx, z + dz))
            if nh is None:
                continue
            ny = roof_block_y(nh)
            if ny < y:
                # the neighbour's membrane top is below this column's membrane: its fill shows from ny up
                for yy in range(ny, y):
                    out.add((x, yy, z))
    return out
