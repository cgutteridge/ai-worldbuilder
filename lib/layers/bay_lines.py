"""Car-park bay lines for the ground layer (#336): rows perpendicular to the mapped parking aisle.

Pure geometry, no world access. The ground layer asks :func:`mark_car_park_bays` once per cell, after
:func:`ground_layer.classify`; every column it returns is a ``mark`` column (``white_concrete``), written by
the ground pass's own plan. That plan is the ground pass, which is exempt from the #327 ground-pass gate,
so these lines do not go through ``Plan.ground_marking``.

Evidence (``OSMGround``): ``parking_areas`` (one column set per ``amenity=parking`` polygon that is not
underground or multi-storey) and ``parking_aisles`` (``highway=service`` + ``service=parking_aisle`` centrelines
with their half width). A car park with no mapped aisle gets no lines (nothing is invented), and a mapped aisle
outside any car park polygon gets none either.

Layout, from the high-street trial (docs/plans/industrial-commercial.md section 3, item 10): a one-column
line every ``BAY_PITCH`` blocks along the aisle, running ``BAY_DEPTH`` blocks out from each aisle edge, so the
bays are ``BAY_PITCH`` wide and ``BAY_DEPTH`` deep either side of the aisle. Limitation: on the outside of an
aisle bend the wedge past a segment's end gets no lines.

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


def _segments(aisles):
    """Flatten aisles into ``(a, b, half, length, ux, uz)`` per non-degenerate segment."""
    out = []
    for aisle in aisles:
        pts = aisle['points']
        for a, b in zip(pts, pts[1:]):
            length = math.dist(a, b)
            if length >= 1e-6:
                out.append((a, b, float(aisle['half']), length, (b[0] - a[0]) / length, (b[1] - a[1]) / length))
    return out


def _candidates(seg, area: set, depth: int):
    """Columns of ``area`` inside the box that bounds one segment's bays."""
    a, b, half, _, ux, uz = seg
    reach = half + depth
    xs = (a[0] - reach * abs(uz), b[0] + reach * abs(uz), a[0] + reach * abs(uz), b[0] - reach * abs(uz))
    zs = (a[1] - reach * abs(ux), b[1] + reach * abs(ux), a[1] + reach * abs(ux), b[1] - reach * abs(ux))
    for x in range(math.floor(min(xs)) - 1, math.ceil(max(xs)) + 1):
        for z in range(math.floor(min(zs)) - 1, math.ceil(max(zs)) + 1):
            if (x, z) in area:
                yield x, z


def _frame(seg, x: int, z: int):
    """``(along, off, dist)`` of a column centre against a segment: along the aisle, from its line, to the segment."""
    a, b, _, length, ux, uz = seg
    px, pz = x + .5 - a[0], z + .5 - a[1]
    along = px * ux + pz * uz
    off = abs(-px * uz + pz * ux)
    return along, off, math.hypot(max(0.0, -along, along - length), off)


def bay_line_columns(areas, aisles, pitch: int = BAY_PITCH, depth: int = BAY_DEPTH) -> set:
    """Columns of the white bay lines for every car park that has a mapped aisle.

    ``areas`` is a list of column sets (one per car park polygon); ``aisles`` is a list of
    ``{'points': [(x, z), ...], 'half': float}`` (MC coordinates, ``half`` = half the aisle width). Every column
    picks its nearest aisle segment first; only that segment's pitch phase decides whether it is a line, and the
    line runs at right angles to that segment. A bend or two neighbouring aisles therefore never cross lines.
    A column must lie inside the car park polygon, so an aisle crossing a cell edge is judged per column, not
    as a whole. Limitation: on the outside of a bend, the wedge past a segment's end has no line (it is nearer
    the corner point than any segment's side), and the inner-corner overlap goes to the nearer segment.
    """
    segs = _segments(aisles)
    out: set = set()
    for area in areas:
        for seg in segs:
            for x, z in _candidates(seg, area, depth):
                if (x, z) in out:
                    continue
                best = min(segs, key=lambda s: _frame(s, x, z)[2])
                if best is not seg:
                    continue                                # a nearer segment owns this column; it is tested there
                along, off, _ = _frame(seg, x, z)
                if not (0.0 <= along <= seg[3]) or not (seg[2] < off <= seg[2] + depth):
                    continue
                nearest = round(along / pitch)             # the line this column is closest to
                if -0.5 <= along - nearest * pitch < 0.5:  # one column wide, half-open so a tie is not doubled
                    out.add((x, z))
    return out


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
