"""Car-park bay lines for the ground layer (#336, #442): rows perpendicular to the mapped parking aisle.

Pure geometry, no world access. The ground layer asks :func:`mark_car_park_bays` once per cell, after
:func:`ground_layer.classify`; every column it returns is a ``mark`` column (``white_concrete``), written by
the ground pass's own plan. That plan is the ground pass, which is exempt from the #327 ground-pass gate,
so these lines do not go through ``Plan.ground_marking``.

Evidence (``OSMGround``): ``parking_areas`` (one column set per ``amenity=parking`` polygon that is not
underground or multi-storey) and ``parking_aisles`` (``highway=service`` + ``service=parking_aisle`` centrelines
with their half width). A car park with no mapped aisle gets no lines (nothing is invented), and a mapped aisle
outside any car park polygon gets none either. An aisle marks only a car park its centreline actually enters,
so a neighbouring park inside bay depth stays blank (#442).

Layout, from the high-street trial (docs/plans/industrial-commercial.md section 3, item 10): a one-column
line every ``BAY_PITCH`` blocks along the aisle, running ``BAY_DEPTH`` blocks out from each aisle edge, so the
bays are ``BAY_PITCH`` wide and ``BAY_DEPTH`` deep either side of the aisle. The pitch is measured along the
whole aisle, not restarted at each segment, so a bend does not put two lines a block apart (#442). On the
outside of a bend the wedge past a segment's end gets no lines. A step that cannot sit on one straight row
(a gap, a column another segment owns, a one-column speck, or a cell that touches a different line) is not
drawn: that is the 50,-35 grid, where a bent aisle's restarted phases painted short rows into each other.

Dependency on the surface rule: lines draw only on columns the ground pass classified ``tarmac`` and whose
mapped surface is the car park itself (``vmap.pri == PRI['parking']``), so aerial lawn inside a polygon, an
aisle, a road or a path never gets a line. Task #338 (surface car parks paint as tarmac) widens which columns
are tarmac; this module needs nothing from it beyond reading the finished ``kind`` map.
"""
from __future__ import annotations

import math

from ground_sources import PRI

BAY_PITCH = 3        # blocks between bay lines along the aisle (trial: "every 3 blocks")
BAY_DEPTH = 5        # blocks from the aisle edge to the back of the bay (trial: "5-deep rows")
_MIN_SIDE = 2        # one column is a speck, not a row (#442)


def _segments(aisles):
    """Flatten aisles into ``(a, b, half, length, ux, uz)`` per non-degenerate segment."""
    out = []
    for aisle in aisles:
        pts = aisle['points']
        half = float(aisle['half'])
        for a, b in zip(pts, pts[1:]):
            length = math.dist(a, b)
            if length >= 1e-6:
                out.append((a, b, half, length, (b[0] - a[0]) / length, (b[1] - a[1]) / length))
    return out


def _with_distance(aisle):
    """Segments of one aisle as ``(a, b, half, length, ux, uz, cum)``; ``cum`` is the distance from its start."""
    half = float(aisle['half'])
    cum = 0.0
    out = []
    for a, b in zip(aisle['points'], aisle['points'][1:]):
        length = math.dist(a, b)
        if length < 1e-6:
            continue
        ux, uz = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        out.append((a, b, half, length, ux, uz, cum))
        cum += length
    return out


def _frame(seg, x: int, z: int):
    """``(along, off, dist)`` of a column centre against a segment: along the aisle, from its line, to the segment."""
    a, _, _, length, ux, uz = seg[:6]
    px, pz = x + .5 - a[0], z + .5 - a[1]
    along = px * ux + pz * uz
    off = abs(-px * uz + pz * ux)
    return along, off, math.hypot(max(0.0, -along, along - length), off)


def _centreline_hits(aisle, area: set) -> bool:
    """True when the aisle centreline enters ``area`` (a neighbour inside bay depth does not count)."""
    pts = aisle['points']
    for a, b in zip(pts, pts[1:]):
        steps = max(1, int(math.ceil(math.dist(a, b))))
        for i in range(steps + 1):
            t = i / steps
            if (math.floor(a[0] + (b[0] - a[0]) * t), math.floor(a[1] + (b[1] - a[1]) * t)) in area:
                return True
    return False


def _column_on_line(tx, tz, sx, sz, ux, uz):
    """The grid column whose centre lies on the perpendicular through ``(sx, sz)`` and nearest ``(tx, tz)``.

    The along-window is half-open (``-0.5 <= err < 0.5``), the same tie the straight-aisle rows already use,
    and a boundary tie takes the column further into the bay. ``None`` when no column sits on that line.
    """
    cx, cz = math.floor(tx), math.floor(tz)
    best, best_key = None, None
    for dx in (-1, 0, 1):
        for dz in (-1, 0, 1):
            qx, qz = cx + dx, cz + dz
            err = (qx + .5 - sx) * ux + (qz + .5 - sz) * uz
            if not (-0.5 <= err < 0.5):
                continue
            off = abs(-(qx + .5 - sx) * uz + (qz + .5 - sz) * ux)
            dist2 = (qx + .5 - tx) ** 2 + (qz + .5 - tz) ** 2
            key = (round(dist2, 6), -off, err)
            if best_key is None or key < best_key:
                best_key, best = key, (qx, qz)
    return best


def _owned_by(col, seg, segs, depth: int) -> bool:
    """True when ``col`` is in ``seg``'s bay band and no segment in ``segs`` is nearer."""
    x, z = col
    along, off, dist = _frame(seg, x, z)
    half, length = seg[2], seg[3]
    if not (0.0 <= along <= length + 1e-6 and half < off <= half + depth + 0.05):
        return False
    for other in segs:
        if other is not seg and _frame(other, x, z)[2] < dist - 1e-9:
            return False
    return True


def _side_row(seg, sx, sz, ux, uz, side: int, area: set, segs, depth: int) -> list:
    """One side of a bay line: up to ``depth`` columns stepping straight out from the aisle, or []."""
    half, px, pz = seg[2], -uz, ux
    cols, prev = [], None
    for i in range(depth):
        off = half + 0.5 + i
        col = _column_on_line(sx + side * px * off, sz + side * pz * off, sx, sz, ux, uz)
        if col is None or col not in area or col == prev:
            break
        c_off = abs(-(col[0] + .5 - sx) * uz + (col[1] + .5 - sz) * ux)
        if not (half < c_off <= half + depth + 0.01):
            break
        if prev is not None and max(abs(col[0] - prev[0]), abs(col[1] - prev[1])) > 1:
            break                                 # a gap: this angle cannot hold one straight row
        if not _owned_by(col, seg, segs, depth):
            break                                 # another segment owns it, or it has left this segment
        cols.append(col)
        prev = col
    return cols if len(cols) >= _MIN_SIDE else []


def _separate_stations(stations: list) -> list:
    """Drop cells that touch a different station's row, then drop a side that is no longer a row.

    Where two lines meet (the inside of a bend, or the two long sides of a narrow ring) the geometry
    cannot give two consistent rows, so those cells are left blank (#442).
    """
    occupied = {}
    for i, sides in enumerate(stations):
        for cols in sides:
            for q in cols:
                occupied.setdefault(q, []).append(i)
    kept = []
    for i, sides in enumerate(stations):
        new_sides = []
        for cols in sides:
            stay = set()
            for x, z in cols:
                clash = any(j != i for j in occupied.get((x, z), ()))
                if not clash:
                    for dx in (-1, 0, 1):
                        for dz in (-1, 0, 1):
                            if dx == 0 and dz == 0:
                                continue
                            if any(j != i for j in occupied.get((x + dx, z + dz), ())):
                                clash = True
                                break
                        if clash:
                            break
                if not clash:
                    stay.add((x, z))
            if len(stay) >= _MIN_SIDE:
                new_sides.append(stay)
        if new_sides:
            kept.append(new_sides)
    return kept


def bay_line_columns(areas, aisles, pitch: int = BAY_PITCH, depth: int = BAY_DEPTH) -> set:
    """Columns of the white bay lines for every car park that has a mapped aisle.

    ``areas`` is a list of column sets (one per car park polygon); ``aisles`` is a list of
    ``{'points': [(x, z), ...], 'half': float}`` (MC coordinates, ``half`` = half the aisle width). An aisle
    is used only for a polygon its centreline enters. Stations sit every ``pitch`` blocks along that aisle's
    whole length, and each station is one row at right angles to the segment it sits on. A bend therefore
    keeps a single spacing, the outer wedge gets nothing, and a cell two rows would both claim is left blank.
    """
    out: set = set()
    prepared = []
    for aisle in aisles:
        segs = _with_distance(aisle)
        if segs:
            prepared.append((aisle, segs))
    for area in areas:
        group = [segs for aisle, segs in prepared if _centreline_hits(aisle, area)]
        flat = [seg for segs in group for seg in segs]
        stations = []
        for segs in group:
            stations.extend(_stations_owned(segs, flat, area, pitch, depth))
        for sides in _separate_stations(stations):
            for cols in sides:
                out |= cols
    return out


def _stations_owned(segs, flat, area, pitch, depth):
    """Rows of one aisle. A column must be nearer its own segment than any segment in ``flat``."""
    if not segs:
        return []
    total = segs[-1][6] + segs[-1][3]
    stations = []
    k = 0
    while k * pitch <= total + 1e-9:
        dist = k * pitch
        seg = None
        for s in segs:
            cum, length = s[6], s[3]
            end = cum + length
            last = s is segs[-1]
            if (cum - 1e-9 <= dist < end - 1e-9) or (last and abs(dist - end) < 1e-6):
                seg = s
                break
        k += 1
        if seg is None:
            continue
        a, _, _, _, ux, uz, cum = seg
        t = dist - cum
        sx, sz = a[0] + ux * t, a[1] + uz * t
        sides = []
        for side in (1, -1):
            row = _side_row(seg, sx, sz, ux, uz, side, area, flat, depth)
            if row:
                sides.append(set(row))
        if sides:
            stations.append(sides)
    return stations


def mark_car_park_bays(kind: dict, vmap) -> int:
    """Turn bay-line columns into ``mark`` in the ground pass's ``kind`` map; returns how many.

    Only ``tarmac`` columns whose mapped surface is the car park itself are marked (see module docstring).
    Evidence a vmap does not carry (no ``parking_areas``) marks nothing.
    """
    areas = getattr(vmap, 'parking_areas', ())
    aisles = getattr(vmap, 'parking_aisles', ())
    if not areas or not aisles:
        return 0
    pri = getattr(vmap, 'pri', {})
    n = 0
    for q in bay_line_columns(areas, aisles):
        if kind.get(q) == 'tarmac' and pri.get(q) == PRI['parking']:
            kind[q] = 'mark'
            n += 1
    return n
