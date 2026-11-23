"""Terrace-house body from lidar evidence (task #333).

Many UK buildings are a pitched house-like body, often with a one-storey flat
front added over a ground floor (a shopfront, a porch range). This module holds
the pure evidence rules and column geometry for that body; it places nothing.
``house_build`` reads a per-label ``body`` spec block and calls in here.

One mechanism, driven by evidence and a class recipe (``body`` in
``config/land-use-classes.json``), with no per-street or per-side branches. A
building whose lidar shows room in the roof (ridge well above the eave) gets
dormers; one without gets a bay window on its upper storey. A street where one
side reads three storeys with a dormer top floor and the other two storeys with a
bay therefore differs by its own lidar, not by code.

Heights are metres of lidar above ground (DSM minus DTM): ``eave_m`` is the low
roofline (the 10th percentile, which ignores dormer slivers) and ``ridge_m`` the
top. One storey is three blocks. Lidar is height truth for the shell; the roof may
deviate from it for clean joins, which is why pitch is clamped.
"""
from __future__ import annotations

import math

from cellmath import DIRS, OPP

STOREY_H = 3
# house_build treats a roof_pitch of 0.5 or less as legacy slab rings; a pitched
# body needs more. One block per block is a 45 degree roof.
MIN_PITCH, MAX_PITCH = 0.6, 1.0
# Class recipe fallbacks (the land-use table row overrides them): no front strip, and a dormer
# needs a sill, two panes and a cap (four blocks) of roof above the eave.
DEFAULTS = {'front_depth': 0, 'attic_rise_m': 3.0, 'min_rise_m': 1.5, 'glazed_front': False}
# A dormer or bay is three columns wide; keep a column of wall each side.
MARGIN = 2
SPACING = 4
MAX_STOREYS = 4


def storeys_from_eave(eave_m: float) -> int:
    """Wall storeys from the lidar eave height; a roofline just short of a storey rounds up.

    @param eave_m: eave height above ground, metres
    @returns: storeys of wall, at least 1 and at most ``MAX_STOREYS``
    """
    return max(1, min(MAX_STOREYS, int((eave_m + 1.0) // STOREY_H)))


def half_span_steps(short_side: int) -> int:
    """Roof distance steps from eave to ridge over a body whose short side is ``short_side`` columns.

    The eave ring adds one column each side, and the ridge is the deepest cell of the
    Chebyshev distance field, so a 7-wide body has a ridge at step 4.

    @param short_side: shorter side of the body, columns
    @returns: steps (blocks of run) from eave to ridge
    """
    return max(1, (short_side + 1) // 2)


def pitch_from_lidar(eave_m: float, ridge_m: float, short_side: int) -> float:
    """Roof rise per run that reproduces the lidar ridge, clamped to a pitched roof.

    @param eave_m: eave height above ground, metres
    @param ridge_m: ridge height above ground, metres
    @param short_side: shorter side of the body, columns
    @returns: ``roof_pitch`` in ``[MIN_PITCH, MAX_PITCH]``
    """
    rise = max(0.0, ridge_m - eave_m)
    pitch = rise / half_span_steps(short_side)
    return round(max(MIN_PITCH, min(MAX_PITCH, pitch)), 2)


def front_strip(cells: set, front: str, depth: int) -> set:
    """Columns of the single-storey front range: the front ``depth`` rows of the footprint.

    The strip is measured along the front direction from the footprint's front-most
    column, so a stepped (rotated) outline gets a strip that follows its front.
    The body keeps at least ``depth`` columns, or there is no strip.

    @param cells: footprint columns
    @param front: ``N`` ``S`` ``E`` or ``W``, the face onto the street
    @param depth: strip depth, columns (0 for none)
    @returns: strip columns, empty when the footprint is too shallow for a strip
    """
    if depth <= 0 or not cells:
        return set()
    fx, fz = DIRS[front]
    along = {c: c[0] * fx + c[1] * fz for c in cells}
    near, far = max(along.values()), min(along.values())
    if near - far + 1 < depth + 3:
        return set()
    return {c for c, v in along.items() if near - v < depth}


def front_row(body: set, front: str) -> set:
    """Body columns whose front neighbour is not body: the wall row the street sees above the front range."""
    fx, fz = DIRS[front]
    return {c for c in body if (c[0] + fx, c[1] + fz) not in body}


def lateral_runs(row: set, front: str) -> list[list[tuple[int, int]]]:
    """Straight runs of a front row, longest first, each ordered along the front.

    A run is columns on one line across the front, side by side. A stepped front
    breaks into several short runs, which is how rotated rows keep dormers and bays
    off the steps.

    @param row: front-row columns
    @param front: face direction
    @returns: runs of ``(x, z)`` columns, longest first
    """
    line = 1 if front in 'NS' else 0   # N/S fronts share a z; E/W share an x
    run_axis = 1 - line
    by_line: dict[int, list] = {}
    for c in row:
        by_line.setdefault(c[line], []).append(c)
    runs = []
    for cols in by_line.values():
        cols.sort(key=lambda c: c[run_axis])
        run = [cols[0]]
        for c in cols[1:]:
            if c[run_axis] == run[-1][run_axis] + 1:
                run.append(c)
            else:
                runs.append(run)
                run = [c]
        runs.append(run)
    return sorted(runs, key=lambda r: (-len(r), r[0]))


def centres(run: list, count: int) -> list[tuple[int, int]]:
    """Up to ``count`` three-wide positions along a run, a wall column clear at each end and between them.

    One position sits in the middle; several spread from end to end, at least ``SPACING`` columns apart so
    the three-wide dormers or bays never touch.

    @param run: columns of one front run, ordered along the front
    @param count: wanted positions
    @returns: centre columns, fewer than ``count`` when the run is too short
    """
    if count <= 0 or len(run) < 2 * MARGIN + 1:
        return []
    usable = run[MARGIN:len(run) - MARGIN]
    span = len(usable) - 1
    count = min(count, 1 + span // SPACING)
    if count == 1:
        return [usable[span // 2]]
    return [usable[round(span * i / (count - 1))] for i in range(count)]


def dormer_count(front_len: int) -> int:
    """Dormers for a front run of ``front_len`` columns: one to three, about one per five columns."""
    return max(1, min(3, round(front_len / 5)))


def reads_pitched(eave_m, ridge_m, params: dict | None = None) -> bool:
    """Whether lidar eave and ridge say a pitched body: both finite and ``min_rise_m`` apart or more."""
    params = {**DEFAULTS, **(params or {})}
    return finite(eave_m, ridge_m) and ridge_m - eave_m >= params['min_rise_m']


def recipe(eave_m: float, ridge_m: float, cells: set, front: str,
           params: dict | None = None, *, override: dict | None = None) -> dict:
    """The ``body`` spec block for one building, from lidar eave and ridge and a class recipe.

    Dormer top floor where the roof has room, a bay window where it has not. An
    ``override`` (from a hand spec or street-level evidence) replaces any value.

    @param eave_m: lidar eave height above ground, metres
    @param ridge_m: lidar ridge height above ground, metres
    @param cells: footprint columns
    @param front: face direction onto the street
    @param params: the class's ``body`` recipe (``land_use.body_recipe``); defaults when None
    @param override: keys of the block to force
    @returns: ``{storeys, front_depth, dormers, bay, glazed_front, roof_pitch}``
    """
    params = {**DEFAULTS, **(params or {})}
    strip = front_strip(cells, front, params['front_depth'])
    depth = params['front_depth'] if strip else 0
    body = cells - strip
    bx = [x for x, _ in body]
    bz = [z for _, z in body]
    short = min(max(bx) - min(bx), max(bz) - min(bz)) + 1
    attic = (ridge_m - eave_m) >= params['attic_rise_m']
    runs = lateral_runs(front_row(body, front), front)
    longest = len(runs[0]) if runs else 0
    block = {
        'storeys': storeys_from_eave(eave_m),
        'front_depth': depth,
        'dormers': dormer_count(longest) if attic else 0,
        'bay': not attic,
        'glazed_front': params['glazed_front'],
        'roof_pitch': pitch_from_lidar(eave_m, ridge_m, short),
    }
    block.update(override or {})
    return block


def bay_cells(centre: tuple[int, int], front: str) -> dict:
    """Columns of a bay window: the three outside cells in front of the wall centred on ``centre``.

    @param centre: wall column the bay is centred on
    @param front: face direction
    @returns: ``{'wall': the wall column, 'outside': [the three projecting columns]}``
    """
    fx, fz = DIRS[front]
    lx, lz = (1, 0) if front in 'NS' else (0, 1)
    wall = centre
    outside = [(centre[0] + fx + k * lx, centre[1] + fz + k * lz) for k in (-1, 0, 1)]
    return {'wall': wall, 'outside': outside}


def dormer_cells(centre: tuple[int, int], front: str) -> dict:
    """Columns of a flush wall dormer: three across the front row and the three behind them.

    @param centre: front-row column the dormer is centred on
    @param front: face direction
    @returns: ``{'front': [...three...], 'back': [...three...]}``
    """
    bx, bz = DIRS[OPP[front]]
    lx, lz = (1, 0) if front in 'NS' else (0, 1)
    row = [(centre[0] + k * lx, centre[1] + k * lz) for k in (-1, 0, 1)]
    return {'front': row, 'back': [(x + bx, z + bz) for x, z in row]}


def validate_block(label: str, block: dict) -> None:
    """Check one ``body`` spec block.

    @param label: house label (for error text)
    @param block: the block
    @raises ValueError: when a key is unknown or a value is out of range
    """
    allowed = {'storeys', 'front_depth', 'dormers', 'bay', 'bay_storey', 'glazed_front'}
    extra = set(block) - allowed
    if extra:
        raise ValueError(f'body[{label!r}]: unknown keys {sorted(extra)}')
    for key, low, high in (('storeys', 1, MAX_STOREYS), ('front_depth', 0, 8), ('dormers', 0, 3)):
        if key in block:
            value = block[key]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f'body[{label!r}].{key} must be an integer in [{low}, {high}]')
    for key in ('bay', 'glazed_front'):
        if key in block and not isinstance(block[key], bool):
            raise ValueError(f'body[{label!r}].{key} must be true or false')
    if 'bay_storey' in block:
        value = block['bay_storey']
        storeys = block.get('storeys', 2)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value < storeys:
            raise ValueError(f'body[{label!r}].bay_storey must be an upper storey (1..storeys-1)')


def finite(*values) -> bool:
    """True when every value is a finite real number (lidar heights may be missing)."""
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)
