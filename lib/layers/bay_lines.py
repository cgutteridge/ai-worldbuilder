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
bays are ``BAY_PITCH`` wide and ``BAY_DEPTH`` deep either side of the aisle.

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
AISLE_OWNER_SHARE = 0.5   # an aisle belongs to the car park polygon holding at least this share of its length


def aisle_belongs_to(area: set, points) -> bool:
    """True when at least ``AISLE_OWNER_SHARE`` of the aisle centreline (sampled each block) is inside ``area``."""
    samples = []
    for a, b in zip(points, points[1:]):
        n = max(1, math.ceil(math.dist(a, b)))
        samples += [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n)]
    samples.append(tuple(points[-1]))
    inside = sum((math.floor(x), math.floor(z)) in area for x, z in samples)
    return inside >= AISLE_OWNER_SHARE * len(samples)


def _segment_lines(a, b, half: float, area: set, pitch: int, depth: int):
    """Yield ``(column, offset)``: bay-line columns of one aisle segment and their distance from its centreline."""
    length = math.dist(a, b)
    if length < 1e-6:
        return
    ux, uz = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    reach = half + depth
    xs = (a[0] - reach * abs(uz), b[0] + reach * abs(uz), a[0] + reach * abs(uz), b[0] - reach * abs(uz))
    zs = (a[1] - reach * abs(ux), b[1] + reach * abs(ux), a[1] + reach * abs(ux), b[1] - reach * abs(ux))
    for x in range(math.floor(min(xs)) - 1, math.ceil(max(xs)) + 1):
        for z in range(math.floor(min(zs)) - 1, math.ceil(max(zs)) + 1):
            if (x, z) not in area:
                continue
            px, pz = x + .5 - a[0], z + .5 - a[1]
            along = px * ux + pz * uz                      # distance along the aisle
            off = abs(-px * uz + pz * ux)                  # distance from its centreline
            if not (0.0 <= along <= length) or not (half < off <= reach):
                continue
            nearest = round(along / pitch)                 # the line this column is closest to
            if -0.5 <= along - nearest * pitch < 0.5:      # one column wide, half-open so a tie is not doubled
                yield (x, z), off


def bay_line_columns(areas, aisles, pitch: int = BAY_PITCH, depth: int = BAY_DEPTH) -> set:
    """Columns of the white bay lines for every car park that has a mapped aisle.

    ``areas`` is a list of column sets (one per car park polygon); ``aisles`` is a list of
    ``{'points': [(x, z), ...], 'half': float}`` (MC coordinates, ``half`` = half the aisle width). Each line runs
    at right angles to the aisle segment it stands beside; where several segments or aisles reach one
    column, the nearest centreline owns it, so a bend or two neighbouring aisles never double up.
    """
    out: set = set()
    for area in areas:
        owner: dict = {}                                   # column -> smallest offset seen
        for aisle in aisles:
            pts = aisle['points']
            if len(pts) < 2 or not aisle_belongs_to(area, pts):
                continue
            for a, b in zip(pts, pts[1:]):
                for q, off in _segment_lines(a, b, float(aisle['half']), area, pitch, depth):
                    owner[q] = min(off, owner.get(q, off))
        out |= set(owner)
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
