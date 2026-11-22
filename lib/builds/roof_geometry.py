"""Pitched-roof geometry shared by the house and feature builders.

Half-block roof heightfields, the stair / slab / full-block state choice over
them, and the vanilla stair-corner rule the roof audit also checks against. Pure
functions over ``{(x, z): ...}`` dicts; no world, plan or builder state.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from cellmath import FACING, N4, N8
from roof_material import family_variants

# Cardinal deltas for pitched-roof upslope detection.
# Stair rise direction, as distinct from an outward facade or attachment direction.
ROOF_DELTA = dict(FACING)
ROOF_FACE_ORDER = ('north', 'east', 'south', 'west')
_STAIR_LEFT = {'east': 'north', 'west': 'south', 'south': 'east', 'north': 'west'}
_STAIR_RIGHT = {'east': 'south', 'west': 'north', 'south': 'west', 'north': 'east'}
_STAIR_OPP = {'east': 'west', 'west': 'east', 'south': 'north', 'north': 'south'}


def stair_shape_from_neighbours(
    facing: str,
    half: str,
    adjacent: dict[str, tuple[str, str] | None],
) -> str:
    """Vanilla stair ``shape`` from same-Y cardinal stair neighbours.

    Matches ``StairBlock.getStairsShape`` / ``canTakeShape``: only neighbours that
    are stairs with the same ``half`` participate. Outer corners look at the
    neighbour in the facing direction; inner corners look behind. A conflicting
    stair on the check side forces ``straight``.

    @param facing: this stair's facing
    @param half: ``bottom`` or ``top``
    @param adjacent: ``{north|south|east|west: (facing, half) | None}`` for the
        four same-Y neighbours (``None`` = missing, non-stair, or other half)
    @returns: ``straight`` / ``outer_left`` / ``outer_right`` / ``inner_left`` /
        ``inner_right``
    """
    front = adjacent.get(facing)
    if front is not None and front[1] == half:
        ff = front[0]
        if ff in (_STAIR_LEFT[facing], _STAIR_RIGHT[facing]):
            other = adjacent.get(_STAIR_OPP[ff])
            if not (other is not None and other[0] == facing and other[1] == half):
                return 'outer_left' if ff == _STAIR_LEFT[facing] else 'outer_right'
    behind = adjacent.get(_STAIR_OPP[facing])
    if behind is not None and behind[1] == half:
        bf = behind[0]
        if bf in (_STAIR_LEFT[facing], _STAIR_RIGHT[facing]):
            other = adjacent.get(bf)
            if not (other is not None and other[0] == facing and other[1] == half):
                return 'inner_left' if bf == _STAIR_LEFT[facing] else 'inner_right'
    return 'straight'


def _stair_facing_half(state: str) -> tuple[str, str, str] | None:
    """Facing, half and shape of a stair state, or None when ``state`` is not one.

    @param state: block state string
    @returns: ``(facing, half, shape)`` or None
    """
    if '_stairs' not in state or '[' not in state or not state.endswith(']'):
        return None
    props = dict(kv.split('=', 1) for kv in state[state.index('[') + 1:-1].split(',') if '=' in kv)
    facing = props.get('facing')
    half = props.get('half', 'bottom')
    if facing not in ROOF_DELTA or half not in ('bottom', 'top'):
        return None
    return facing, half, props.get('shape', 'straight')


def _with_stair_shape(state: str, old: str, new: str) -> str:
    """Replace the stair ``shape`` token, inserting one when the state omitted it.

    @param state: stair block state
    @param old: shape currently parsed from ``state``
    @param new: shape to write
    @returns: block state string
    """
    token = f'shape={old}'
    if token in state:
        return state.replace(token, f'shape={new}', 1)
    return state[:-1] + f',shape={new}]'


def align_stair_shapes(cells: dict[tuple[int, int, int], str], outside=None,
                       prefetch=None) -> dict[tuple[int, int, int], str]:
    """Rewrite stair shapes so each one matches its same-Y neighbours, in the plan and the world.

    :func:`pitched_roof_states` chooses shapes from the roof membrane. A later
    pass can replace a stair with a full block (a vent pad or a chimney) or
    with glass (a skylight). The stairs that remain have to be shaped against
    that final skin, or the game retargets them on the next neighbour update
    (#169, #390). Neighbour facing and half are what the rule reads, so
    rewriting shape does not change what a neighbour expects.

    A stair's shape depends on every stair beside it, whatever its material, so a stair that
    meets an earlier build's stair (a roof beside a roof, a stoop beside a step) is shaped
    against that too: ``outside`` reads the world for neighbours the plan does not write. A
    cell the plan writes always wins over the world.

    @param cells: ``{(x, y, z): block state}``
    @param outside: optional ``outside(x, y, z)`` returning a block state string or ``None``
    @param prefetch: optional ``prefetch(positions)`` that warms ``outside`` for the cells it will read
    @returns: new dict with the same keys; non-stairs and already-matching stairs unchanged
    """
    stairs: dict[tuple[int, int, int], tuple[str, str, str]] = {}
    for pos, state in cells.items():
        parsed = _stair_facing_half(state)
        if parsed is not None:
            stairs[pos] = parsed
    world: dict[tuple[int, int, int], tuple[str, str, str] | None] = {}
    if outside is not None and stairs:
        wanted = {(x + dx, y, z + dz) for (x, y, z) in stairs for dx, dz in ROOF_DELTA.values()} - set(cells)
        if prefetch is not None:
            prefetch(sorted(wanted))
        for pos in wanted:
            state = outside(*pos)
            world[pos] = _stair_facing_half(state) if state else None
    out = dict(cells)
    for (x, y, z), (facing, half, planned) in stairs.items():
        adjacent: dict[str, tuple[str, str] | None] = {}
        for face, (dx, dz) in ROOF_DELTA.items():
            pos = (x + dx, y, z + dz)
            neighbour = stairs.get(pos) if pos in cells else world.get(pos)
            adjacent[face] = None if neighbour is None else (neighbour[0], neighbour[1])
        shape = stair_shape_from_neighbours(facing, half, adjacent)
        if shape != planned:
            out[(x, y, z)] = _with_stair_shape(cells[(x, y, z)], planned, shape)
    return out


def outside_stairs_reshaped(cells: dict[tuple[int, int, int], str], outside) -> list[dict]:
    """Stairs outside the plan whose shape the plan's stairs will change (the game updates them).

    A stair placed beside an earlier build's stair retargets that stair's shape. The plan cannot
    fix it (the stair is not the plan's to write); this reports it so the owner can see which
    earlier stairs will change. Only world stairs beside a stair the plan writes are examined
    (a plan block that replaces a stair beside a world stair is not), and a world stair that was
    already out of step with its neighbours is reported as changing even though the plan is not
    the cause. Each world cell is read once.

    @param cells: ``{(x, y, z): block state}`` the plan will write
    @param outside: ``outside(x, y, z)`` returning the world's block state string or ``None``
    @returns: list of ``{pos, facing, now, then}`` for each world stair whose shape would change
    """
    plan_stairs = {pos: p for pos, s in cells.items() if (p := _stair_facing_half(s)) is not None}
    memo: dict[tuple[int, int, int], tuple[str, str, str] | None] = {}

    def world(pos):
        if pos not in memo:
            state = outside(*pos)
            memo[pos] = _stair_facing_half(state) if state else None
        return memo[pos]

    candidates = {(x + dx, y, z + dz) for (x, y, z) in plan_stairs for dx, dz in ROOF_DELTA.values()} - set(cells)
    out = []
    for pos in sorted(candidates):
        parsed = world(pos)
        if parsed is None:
            continue
        facing, half, now = parsed
        adjacent: dict[str, tuple[str, str] | None] = {}
        for face, (dx, dz) in ROOF_DELTA.items():
            n = (pos[0] + dx, pos[1], pos[2] + dz)
            neighbour = plan_stairs.get(n) if n in cells else world(n)
            adjacent[face] = None if neighbour is None else (neighbour[0], neighbour[1])
        then = stair_shape_from_neighbours(facing, half, adjacent)
        if then != now:
            out.append({'pos': pos, 'facing': facing, 'now': now, 'then': then})
    return out


def stair_effect_lines(cells: dict[tuple[int, int, int], str], outside, limit: int = 6) -> list[str]:
    """Dry-run lines naming earlier stairs whose shape this plan's stairs will change.

    @param cells: ``{(x, y, z): block state}`` the plan will write
    @param outside: ``outside(x, y, z)`` world reader
    @param limit: most example stairs to name
    @returns: empty when nothing outside the plan changes, else a count line and examples
    """
    moved = outside_stairs_reshaped(cells, outside)
    if not moved:
        return []
    lines = [f'stairs: {len(moved)} stair(s) already in the world will change shape beside this plan\'s stairs']
    for m in moved[:limit]:
        x, y, z = m['pos']
        lines.append(f"  ({x},{y},{z}) facing {m['facing']}: {m['now']} -> {m['then']}")
    return lines


def _roof_stair(mat: str, facing: str, shape: str = 'straight', half: str = 'bottom') -> str:
    """Pitched-roof stair state (bottom half, waterlogged false).

    @param mat: shared roof material family or block alias
    @param facing: upslope facing
    @param shape: ``straight`` / ``inner_*`` / ``outer_*``
    @param half: ``bottom`` or ``top``
    @returns: block state string
    """
    return (
        f'{family_variants(mat)["stairs"]}[facing={facing},half={half},shape={shape},waterlogged=false]'
    )


def _roof_slab(mat: str, typ: str) -> str:
    """Pitched-roof slab state (waterlogged false).

    @param mat: shared roof material family or block alias
    @param typ: ``bottom`` or ``top``
    @returns: block state string
    """
    return f'{family_variants(mat)["slab"]}[type={typ},waterlogged=false]'


def roof_block_y(half_height: int) -> int:
    """Block Y where a roof membrane cell of ``half_height`` sits.

    Even ``h`` is a full block / stair at ``h // 2``. Odd ``h`` is a bottom-half
    slab one block up, ``(h + 1) // 2``: its surface (y + 0.5) then lies half a
    block above the full block for ``h - 1`` at ``y - 1``, so slope surfaces
    rise steadily (7, 7.5, 8, 8.5 ...) instead of sawtoothing (#209). Every
    half-height to Y mapping goes through here so stairs (same-Y shape rule,
    #169) and slabs agree.

    @param half_height: membrane top in half-blocks
    @returns: block Y
    """
    return (half_height + 1) // 2


def regularise_roof_half_heights(
    half_heights: dict[tuple[int, int], int],
    levels: dict[tuple[int, int], int] | None = None,
) -> dict[tuple[int, int], int]:
    """Lower cliffs so neighbouring columns differ by at most 2 half-steps.

    Lidar is only a guide for roofs: a Minecraft stair skin cannot bridge a
    taller jump in one column, so peaks drop toward their neighbours until
    every N4 step within a roof zone is 0, 1 or 2 half-blocks. Distinct zoned
    plateaus keep their intentional wall-backed height changes.

    @param half_heights: ``{(x, z): half_height}`` raw membrane tops
    @param levels: optional semantic zone level per column; only equal levels join
    @returns: new dict with the same keys, cliffs pulled down
    """
    h = dict(half_heights)
    pending = deque(h)
    queued = set(h)
    while pending:
        x, z = pending.popleft()
        queued.remove((x, z))
        for dx, dz in N4:
            c = (x + dx, z + dz)
            if (c in h and h[c] > h[(x, z)] + 2
                    and (levels is None or levels[c] == levels[(x, z)])):
                h[c] = h[(x, z)] + 2
                if c not in queued:
                    pending.append(c)
                    queued.add(c)
    return h


@dataclass(frozen=True)
class RoofRidgeRun:
    """One level maximum; width/axis are None for an unbounded or irregular plateau."""
    cells: frozenset[tuple[int, int]]
    width: int | None
    axis: str | None


def roof_ridge_runs(
    half_heights: dict[tuple[int, int], int],
    levels: dict[tuple[int, int], int] | None = None,
) -> list[RoofRidgeRun]:
    """Find equal-height N4 maxima in a regularised pitched-roof heightfield.

    A cap is rectangular, bounded across its width by lower roof cells in the
    same zone. Missing cells and zone boundaries are not lower roof. A 2x2
    square or an irregular plateau has no ridge axis. Existing slope stairs
    (including diagonal rises) are excluded before classifying the whole run.
    Wider and unqualified maxima are returned too, for diagnostic counts.

    @param half_heights: regularised ``{(x, z): half_height}``
    @param levels: optional semantic zone levels; boundaries cannot bound a cap
    @returns: runs with their width and cross-slope axis (None if unqualified)
    """
    seen = set()
    runs = []

    def same_zone(a, b):
        return levels is None or levels[a] == levels[b]

    for start in sorted(half_heights):
        if start in seen:
            continue
        h = half_heights[start]
        cells = {start}
        seen.add(start)
        pending = [start]
        while pending:
            x, z = pending.pop()
            for dx, dz in N4:
                n = (x + dx, z + dz)
                if n not in seen and half_heights.get(n) == h and same_zone(start, n):
                    seen.add(n)
                    cells.add(n)
                    pending.append(n)
        if any(half_heights.get((x + dx, z + dz), h) > h
               for x, z in cells for dx, dz in N4):
            continue
        if any(half_heights.get((x + dx, z + dz), h) >= h + 2
               for x, z in cells for dx in (-1, 1) for dz in (-1, 1)):
            continue

        xs, zs = zip(*cells)
        sx, sz = max(xs) - min(xs) + 1, max(zs) - min(zs) + 1
        width = axis = None
        if len(cells) == sx * sz and (sx, sz) != (2, 2):
            # Prefer the narrower axis; a one-row gable can still have a 2-wide cap. A 1x1
            # apex has no narrow axis, so it must be bounded on both.
            axes = sorted(((sx, 'x', (1, 0)), (sz, 'z', (0, 1))))
            if len(cells) == 1:
                axes = [(1, 'x', (1, 0)), (1, 'z', (0, 1))]
            fits = []
            for span, candidate, (step_x, step_z) in axes:
                flanks = [(c, (c[0] + sign * step_x, c[1] + sign * step_z))
                          for c in cells for sign in (-1, 1)
                          if (c[0] + sign * step_x, c[1] + sign * step_z) not in cells]
                fits.append((span, candidate,
                             all(n in half_heights and same_zone(c, n) and half_heights[n] < h
                                 for c, n in flanks)))
            if len(cells) == 1:
                if all(ok for _span, _axis, ok in fits):
                    width, axis = 1, 'x'
            else:
                for span, candidate, ok in fits:
                    if ok:
                        width, axis = span, candidate
                        break
        runs.append(RoofRidgeRun(frozenset(cells), width, axis))
    return runs


def pitched_roof_states(
    half_heights: dict[tuple[int, int], int],
    materials: dict[tuple[int, int], str],
    warnings: list[str] | None = None,
    levels: dict[tuple[int, int], int] | None = None,
    flat_cells: set[tuple[int, int]] | None = None,
) -> dict[tuple[int, int], str]:
    """Choose stair / slab / full block states for a pitched-roof heightfield.

    ``half_heights`` is the membrane top in half-blocks (``2 * y`` for a full
    block at ``y``, odd values a bottom-half slab one block up; see
    :func:`roof_block_y`). Heights
    are regularised first (N4 steps at most 2 within a zone). A rise of 2
    half-steps toward a neighbour is a stair facing upslope; hip eaves with only a diagonal rise
    also get a stair.     Stair ``shape`` comes from stairs at the **same block Y**
    (vanilla neighbour rule via :func:`stair_shape_from_neighbours`), not
    from height differences on the diagonal. A later pass that removes a
    stair from the skin must call :func:`align_stair_shapes` so the shapes
    match the stairs that remain. One-wide ridges and hip apexes
    use bottom-half slabs; two-wide ridges use stairs facing each other.
    At even terrace edges with no higher neighbour, a one-block drop backed by
    a level roof cell gets a stair facing away from the drop. Half-block drops
    and corners with two perpendicular drops get bottom-half slabs. Opposing
    drops, missing backing cells and zone steps do not trim a terrace.
    Plateau interiors use a full block (even) or bottom-half slab
    (odd; see :func:`roof_block_y`: the slab
    sits one block above the full block of ``h - 1``, so surfaces rise by half
    a block per step; a top-half slab there would match a full block, I-019 / #207).
    Missing stair/slab variants fall back to
    the family's full block and append a warning.

    @param half_heights: ``{(x, z): half_height}``
    @param materials: ``{(x, z): material prefix}`` (must cover every column)
    @param warnings: optional list that receives fallback messages
    @param levels: optional semantic zone levels preserving intentional plateau steps
    @param flat_cells: optional cells classified as flat; keep full decks here,
        allowing only orthogonal stairs toward a higher, different roof zone
    @returns: ``{(x, z): block state}`` (place at ``roof_block_y(half_height)`` after
        regularisation)
    """
    half_heights = regularise_roof_half_heights(half_heights, levels)
    flat_cells = flat_cells or set()
    warn = warnings if warnings is not None else []
    fam_cache: dict[str, dict[str, str]] = {}
    out: dict[tuple[int, int], str] = {}

    def fam(mat: str) -> dict[str, str]:
        if mat not in fam_cache:
            fam_cache[mat] = family_variants(mat)
        return fam_cache[mat]

    def fallback(mat: str, want: str) -> str:
        f = fam(mat)
        warn.append(f'roof material {mat!r} has no {want}; using full block {f["full"]}')
        return f['full']

    def h_at(x: int, z: int) -> int | None:
        return half_heights.get((x, z))

    def ups_at(x: int, z: int, h: int) -> list[str]:
        ups = []
        for face in ROOF_FACE_ORDER:
            dx, dz = ROOF_DELTA[face]
            n = (x + dx, z + dz)
            nh = half_heights.get(n)
            if nh is None or nh < h + 2:
                continue
            if (x, z) in flat_cells and (levels is None or levels[n] == levels[(x, z)]):
                continue
            ups.append(face)
        return ups

    def diag_rises(x: int, z: int, h: int, dx: int, dz: int) -> bool:
        # A hip eave corner rises diagonally within its own zone. A taller zone
        # across a boundary is a wall-backed step, so a flat deck beside it
        # must not grow a corner stair that climbs nowhere (#311).
        nh = h_at(x + dx, z + dz)
        if nh is None or nh < h + 2:
            return False
        return levels is None or levels.get((x + dx, z + dz)) == levels.get((x, z))

    def ortho_pair_of(ups: list[str]) -> tuple[str, str] | None:
        for i, a in enumerate(ups):
            for b in ups[i + 1:]:
                ax, az = ROOF_DELTA[a]
                bx, bz = ROOF_DELTA[b]
                if ax * bx + az * bz == 0:
                    return (a, b)
        return None

    # Pass 1: facing only (shape is game-owned from same-Y neighbours).
    facings: dict[tuple[int, int], str] = {}
    for (x, z), h in half_heights.items():
        ups = ups_at(x, z, h)
        pair = ortho_pair_of(ups)
        if len(ups) == 1:
            facings[(x, z)] = ups[0]
        elif pair is not None:
            facings[(x, z)] = pair[0]
        elif not ups and (x, z) not in flat_cells:
            diag_pair = None
            for a in ROOF_FACE_ORDER:
                for b in ROOF_FACE_ORDER:
                    if a >= b:
                        continue
                    ax, az = ROOF_DELTA[a]
                    bx, bz = ROOF_DELTA[b]
                    if ax * bx + az * bz != 0:
                        continue
                    if diag_rises(x, z, h, ax + bx, az + bz):
                        diag_pair = (a, b)
                        break
                if diag_pair:
                    break
            if diag_pair:
                facings[(x, z)] = diag_pair[0]

    cap_slabs = set()
    for run in roof_ridge_runs(half_heights, levels):
        if run.width == 1:
            cap_slabs.update(run.cells - flat_cells)
        elif run.width == 2:
            across = 0 if run.axis == 'x' else 1
            low = min(c[across] for c in run.cells)
            for c in run.cells:
                if c in flat_cells:
                    continue
                facings[c] = (('east' if c[across] == low else 'west') if across == 0 else
                              ('south' if c[across] == low else 'north'))

    # Terrace edges supplement slope stairs and ridge caps before shapes are chosen.
    opposite = {'north': 'south', 'east': 'west', 'south': 'north', 'west': 'east'}
    for (x, z), h in half_heights.items():
        c = (x, z)
        if h % 2 or c in facings or c in cap_slabs or c in flat_cells:
            continue
        neighbours = {face: (x + dx, z + dz) for face, (dx, dz) in ROOF_DELTA.items()}
        if any(half_heights.get(n, h) > h for n in neighbours.values()):
            continue
        drops = [face for face in ROOF_FACE_ORDER
                 if (n := neighbours[face]) in half_heights
                 and (levels is None or levels[n] == levels[c])
                 and 0 < h - half_heights[n] <= 2]
        if not (len(drops) == 1 or (len(drops) == 2 and ortho_pair_of(drops))):
            continue
        if any(half_heights.get(n := neighbours[opposite[face]]) != h
               or (levels is not None and levels[n] != levels[c]) for face in drops):
            continue
        if len(drops) == 1 and h - half_heights[neighbours[drops[0]]] == 2:
            facings[c] = opposite[drops[0]]
        else:
            cap_slabs.add(c)

    # Pass 2: shape from same-Y stair neighbours, including ridge and terrace stairs.
    for (x, z), h in half_heights.items():
        mat = materials[(x, z)]
        f = fam(mat)
        if (x, z) in facings:
            facing = facings[(x, z)]
            y = roof_block_y(h)
            adjacent: dict[str, tuple[str, str] | None] = {}
            for face, (dx, dz) in ROOF_DELTA.items():
                npos = (x + dx, z + dz)
                if (npos not in facings or roof_block_y(half_heights[npos]) != y
                        or 'stairs' not in fam(materials[npos])):
                    adjacent[face] = None
                else:
                    adjacent[face] = (facings[npos], 'bottom')
            shape = stair_shape_from_neighbours(facing, 'bottom', adjacent)
            if 'stairs' in f:
                out[(x, z)] = _roof_stair(mat, facing, shape=shape)
            else:
                out[(x, z)] = fallback(mat, 'stairs')
            continue

        if (x, z) in flat_cells:
            out[(x, z)] = f['full']
        elif h % 2 == 1 or (x, z) in cap_slabs:
            if 'slab' in f:
                out[(x, z)] = _roof_slab(mat, 'bottom')
            else:
                out[(x, z)] = fallback(mat, 'slab')
        else:
            out[(x, z)] = f['full']

    return out


def edge_distance_field(own: set, blocked: set) -> dict[tuple[int, int], int]:
    """Chebyshev distance from the roof outline, over the footprint plus its eave ring.

    The roof outline is ``own`` grown by one ring of 8-neighbours that are not
    ``blocked`` (columns owned by another build, which keep their own roof). The
    outer ring of that area is 0 and each step inward adds 1.

    @param own: owned ``(x, z)`` columns of the roof
    @param blocked: columns the eave ring must not grow into
    @returns: ``{(x, z): distance}`` over the grown area
    """
    rs = set(own)
    for (x, z) in own:
        for dx, dz in N8:
            n = (x + dx, z + dz)
            if n not in blocked:
                rs.add(n)
    return chebyshev_depth(rs)


def chebyshev_depth(rs: set) -> dict[tuple[int, int], int]:
    """Chebyshev distance of each column of ``rs`` to the outside of ``rs``.

    @param rs: ``(x, z)`` columns of the area
    @returns: ``{(x, z): steps}``; columns with an outside 8-neighbour are 0
    """
    dist = {}
    front = [c for c in rs if any((c[0] + dx, c[1] + dz) not in rs for dx, dz in N8)]
    for c in front:
        dist[c] = 0
    q = list(front)
    while q:
        nq = []
        for c in q:
            for dx, dz in N8:
                n = (c[0] + dx, c[1] + dz)
                if n in rs and n not in dist:
                    dist[n] = dist[c] + 1
                    nq.append(n)
        q = nq
    return dist


ROTATED_MIN_FILL = 0.75     # footprint cells / fitted rectangle area below which no rectangle is fitted
ROTATED_ALIGNED_FILL = 0.9    # fill an axis-aligned footprint needs to count as a rectangle (an L fills 0.75)
ROTATED_MIN_ANGLE = 3       # degrees from a cardinal axis below which the footprint counts as square-on


def fit_rotated_rectangle(own: set, min_angle: int = ROTATED_MIN_ANGLE) -> tuple[int, float, float, float, float, float] | None:
    """Minimum-area rectangle around footprint cell centres, when the footprint is a rotated rectangle.

    A house drawn square to an angled road rasterises to a staircase outline whose Chebyshev
    edge distance wobbles, so a pitch laid on it comes out patchy. Fitting the footprint's own
    axes gives straight ridge and hip planes instead, quantised to half-block
    heights. Footprints that are square to the grid, or
    that do not fill a rectangle well (L shapes, groups), return ``None`` and keep the grid field.

    @param own: owned ``(x, z)`` columns of the roof
    @param min_angle: degrees from a grid axis below which ``None`` is returned (0 accepts any angle)
    @returns: ``(angle_deg, u_min, u_max, v_min, v_max, fill)`` in the rotated frame
        (``u`` along ``(cos a, sin a)``, ``v`` across), or ``None``
    """
    if not own:
        return None
    pts = [(x + 0.5, z + 0.5) for x, z in own]
    best = None
    for deg in range(90):
        a = math.radians(deg)
        c, s = math.cos(a), math.sin(a)
        us = [x * c + z * s for x, z in pts]
        vs = [-x * s + z * c for x, z in pts]
        area = (max(us) - min(us) + 1) * (max(vs) - min(vs) + 1)
        if best is None or area < best[0] - 1e-9:
            best = (area, deg, min(us), max(us), min(vs), max(vs))
    area, deg, u0, u1, v0, v1 = best
    fill = len(own) / area
    if fill < ROTATED_MIN_FILL or min(deg, 90 - deg) < min_angle:
        return None
    if min(deg, 90 - deg) < ROTATED_MIN_ANGLE and fill < ROTATED_ALIGNED_FILL:
        return None
    return deg, u0, u1, v0, v1, fill


def rotated_hip_depth(own: set, roof: set, pad: float = 1.0) -> dict[tuple[int, int], float] | None:
    """Distance of each roof column inside the fitted rectangle's eave outline, for a rotated hip.

    The outline is the rectangle from :func:`fit_rotated_rectangle` grown by ``pad`` cells, so the
    outermost eave ring sits at 0 and depth rises toward a straight ridge along the long axis
    with hips at the ends. Heights taken from it are planes, so the ridge has no jogs.

    @param own: owned ``(x, z)`` columns of the roof
    @param roof: every roof column (footprint plus eave ring)
    @param pad: eave overhang in cells beyond the outermost footprint centres
    @returns: ``{(x, z): depth}`` over ``roof`` (float, never negative), or ``None`` when the
        footprint is not a rotated rectangle
    """
    fit = fit_rotated_rectangle(own)
    if fit is None:
        return None
    deg, u0, u1, v0, v1, _fill = fit
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    out = {}
    for x, z in roof:
        u = (x + 0.5) * c + (z + 0.5) * s
        v = -(x + 0.5) * s + (z + 0.5) * c
        out[(x, z)] = max(0.0, min(u - (u0 - pad), (u1 + pad) - u, v - (v0 - pad), (v1 + pad) - v))
    return out
