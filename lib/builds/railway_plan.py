"""Pure railway corridor planning from :class:`railway_evidence.Line`.

``plan_line(line, ground, start_cell, incoming=())`` returns JSON-able data,
never blocks or journal changes. ``ground`` has ``x0``, ``z0`` and arrays
indexed [x, z]: ``terrain`` (DTM block Y), ``h`` (surface block Y), optional
``have``, ``kind`` (4 means structure), ``supports`` (structure piers), and
``solid`` (solid material below terrain; defaults true). ``deck_y`` may give
an overhead deck's underside; otherwise h is the available height evidence.
``structures`` (optional, ``structure_evidence.by_osm_id``) is the deck
evidence for the line's overhead spans: a measured underside replaces h as
the clearance ceiling, and a span whose deck height is unknown (or inferred
while :data:`INFERRED_DECK_USABLE` is false) becomes an ``overhead`` gap with
end records for a human, never a failure. All arrays cover the same rectangle. Missing/NaN terrain is a reported failure.

Rail tuples are [x, rail_y, z, shape, full_block_state]; columns are
[x, z, original_ground_y, formation_y, bed|slope]. ``surfaces`` gives
[x,z,surface_kind], retaining ground_sources' road vocabulary. The integrator
supplies its palette. ``planting`` is [x,y,z,'bush'] rather than a new palette.
Torch supports are explicit [x,y,z] solid-fill requirements for integration.

``tracks`` includes the continuous profiles through gaps; only ``rails`` and
the other placement lists omit gaps. A join's ``at`` is the final inside
rail column; ``next`` is the adjacent column to start the next feature,
``level`` is rail Y, ``side`` identifies the start or end cut, and ``run``
carries step spacing across the boundary. ``direction`` enters ``next``
from ``at``; the track may turn immediately at next. Pass the selected
outgoing join back as ``incoming``. Profiles retain ``before`` and ``after``
neighbours so boundary corners keep their actual shape. Gap ``ends`` describe
the two abutting rail cells, with rail Y, direction and track index.
A mapped road crossing, or any crossing with a barrier, light or bell tag,
becomes a ``crossing`` gap (:func:`crossing_is_feature`). A footpath or private
crossing with none of those tags is not a gap: the rails stay continuous and
that path's surface is laid across those track columns.

The box is :data:`railway_evidence.PLANNER_BOX` (512) square, so a longer line is a chain of
features. Gaps are found on the visit plus :data:`EDGE_MARGIN`: a gap wholly inside the box is
recorded, one wholly outside is the neighbour's, one that starts at an incoming join gets that
join's rail as its first end (``from_join``), and one that straddles the edge fails loudly with
coordinates (docs/plans/railway.md decision 17).

Where several lines of one feature share rail columns, :func:`junction_gaps`
keeps the main line's rails and names the side tracks' overlapping (and
orphaned) rail runs as ``junction`` gaps, which :func:`plan_line` then plans
through its ``junction_gaps`` argument; a person finishes the junction.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np

import cellmath
from ground_sources import PATH_WIDTH, ROAD_CLASSES, SURFACE_KIND, road_width
from railway_evidence import PLANNER_BOX, Line

RAIL_MIN_STEP_RUN = 100
RAIL_MAX_DEVIATION = 8
BED_RADIUS = 2.3
SLOPE_RATIO = 2.0
SLOPE_MAX = 40
RAIL_CLEARANCE = 5
# Gaps are looked for this far (cells along a track, blocks for a crossing) past the box edge.
EDGE_MARGIN = 64
# A rail run this short, cut off by junction gaps (or a dead end) on both sides, joins nothing.
JUNCTION_MIN_RUN = 4
# Deck-height policy for an overhead span whose lidar height is only inferred
# (too few or conflicting structure columns). False: leave a gap for a human.
# True: trust the inferred underside; if it leaves under RAIL_CLEARANCE the
# corridor still fails loudly. A measured height is always trusted, and an
# unknown height is always a gap.
INFERRED_DECK_USABLE = False


def snap_track(points: Sequence[Sequence[float]]) -> list[tuple[int, int]]:
    """Densify at 0.25 blocks, repair diagonal moves and erase loops.

    @param points: whole Minecraft polyline
    @returns: unique 4-connected columns, in track order
    @raises ValueError: for nonfinite coordinates
    """
    dense = []
    for a, b in zip(points, points[1:]):
        if not all(math.isfinite(v) for v in (*a, *b)):
            raise ValueError('nonfinite track coordinate')
        n = max(1, math.ceil(math.dist(a, b) / 0.25))
        if not dense:
            dense.append(tuple(a))
        dense.extend((a[0] + (b[0] - a[0]) * k / n,
                      a[1] + (b[1] - a[1]) * k / n) for k in range(1, n + 1))
    cells = []
    previous = None
    for x, z in dense:
        p = math.floor(x), math.floor(z)
        if p == previous:
            continue
        if previous is not None and abs(p[0] - previous[0]) == abs(p[1] - previous[1]) == 1:
            choices = ((p[0], previous[1]), (previous[0], p[1]))
            cells.append(min(choices, key=lambda c: (math.hypot(c[0] + .5 - x, c[1] + .5 - z), c)))
        cells.append(p)
        previous = p
    out, seen = [], {}
    for p in cells:
        if p in seen:
            k = seen[p]
            for dead in out[k + 1:]:
                del seen[dead]
            del out[k + 1:]
        else:
            seen[p] = len(out)
            out.append(p)
    return out


def _inside(p, box):
    return box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]


def _sample(ground, key, p, default=None):
    array = ground.get(key)
    if array is None:
        return default
    x, z = p[0] - ground['x0'], p[1] - ground['z0']
    if not (0 <= x < len(array) and 0 <= z < len(array[x])):
        return default
    value = array[x][z]
    if value is None or not np.isfinite(value):
        return default
    return value.item() if isinstance(value, np.generic) else value


def _ground(ground, p):
    if not _sample(ground, 'have', p, True):
        return None
    value = _sample(ground, 'terrain', p)
    return None if value is None else int(value)


def _direction(a, b):
    vector = b[0] - a[0], b[1] - a[1]
    return next(word for word, offset in cellmath.FACING.items() if offset == vector)


def _shape(path, levels, i, before=None, after=None):
    p = path[i]
    before = path[i - 1] if i else before or (2 * p[0] - path[1][0], 2 * p[1] - path[1][1])
    after = path[i + 1] if i + 1 < len(path) else after or (2 * p[0] - before[0], 2 * p[1] - before[1])
    directions = {_direction(p, before), _direction(p, after)}
    straight = before[0] == p[0] == after[0] or before[1] == p[1] == after[1]
    if straight:
        for j in (i - 1, i + 1):
            if 0 <= j < len(path) and levels[j] == levels[i] + 1:
                return 'ascending_' + _direction(p, path[j])
        return 'north_south' if before[0] == after[0] else 'east_west'
    return '_'.join(sorted(directions, key=lambda d: d in ('east', 'west')))


def _nearest_indices(path, reference):
    """Exact nearest reference columns; ties choose the earlier track cell."""
    points = np.asarray(reference, dtype=float)
    return [int(np.argmin(np.sum((points - p) ** 2, axis=1))) for p in path]


def _clip(path, box, entry, centre):
    """One connected visit to the box, pointed along an incoming join if any.

    @returns: ``(oriented, a, b, before, after, error)``: the whole track in travel order,
        the visit as ``oriented[a:b]``, its outside neighbours, and a failure reason (or None)
    """
    incoming_before = None
    if entry:
        at = tuple(entry['next'])
        if at not in path:
            return [], 0, 0, None, None, 'incoming join is not on this track'
        if not _inside(at, box):
            return [], 0, 0, None, None, 'incoming join is outside the planning box'
        i = path.index(at)
        desired = cellmath.FACING[entry['direction']]
        previous = tuple(entry.get('at', (at[0] - desired[0], at[1] - desired[1])))
        if (at[0] - previous[0], at[1] - previous[1]) != desired:
            return [], 0, 0, None, None, 'incoming join direction disagrees with its entry cells'
        if i and path[i - 1] == previous:
            path = path[i:]
        elif i + 1 < len(path) and path[i + 1] == previous:
            path = list(reversed(path[:i + 1]))
        elif previous not in path and i == 0:
            path = path[i:]
        elif previous not in path and i == len(path) - 1:
            path = list(reversed(path))
        else:
            return [], 0, 0, None, None, 'incoming join direction cannot connect'
        incoming_before = previous
    runs, start = [], None
    for i, p in enumerate(path):
        if _inside(p, box):
            if start is None:
                start = i
        elif start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(path)))
    if not runs:
        return [], 0, 0, None, None, 'track does not enter the planning box'
    a, b = (min(runs, key=lambda r: min(math.dist(p, centre) for p in path[r[0]:r[1]]))
            if not entry else runs[0])
    before = path[a - 1] if a else incoming_before
    after = path[b] if b < len(path) else None
    return path, a, b, before, after, None


def deck_evidence(line, structures, inferred_usable=None):
    """Clearance ceilings and unknown decks for a line's overhead spans.

    @param line: line whose ``overhead`` spans are looked up by way id
    @param structures: ``structure_evidence.by_osm_id`` mapping, or None for no evidence
    @param inferred_usable: trust an inferred height; defaults to :data:`INFERRED_DECK_USABLE`
    @returns: ``(undersides, decks)``: ``{(x, z): underside Y}`` over usable decks, and a
        JSON-able report per span with ``osm_id``, ``status``, ``underside``, ``usable`` and ``reason``
    """
    usable_inferred = INFERRED_DECK_USABLE if inferred_usable is None else inferred_usable
    undersides, report = {}, []
    if structures is None:
        return undersides, report
    for span in line.overhead:
        record = structures.get(span.osm_id)
        if record is None:
            status, under, usable, reason = 'unknown', None, False, 'no structure evidence for this deck'
        else:
            height = record.height
            status, under, reason = height.status, height.underside, height.reason
            usable = under is not None and (status == 'measured' or (status == 'inferred' and usable_inferred))
            if status == 'inferred' and not usable:
                reason = 'inferred height is not trusted (INFERRED_DECK_USABLE): ' + reason
        report.append({'osm_id': span.osm_id, 'status': status, 'underside': under,
                       'usable': usable, 'reason': reason})
        if usable:
            for p in span.deck:
                undersides[p] = min(under, undersides.get(p, under))
    return undersides, report


def _overhead_gaps(line, paths, decks):
    """Gaps under overhead decks whose height cannot be used, merged when they touch."""
    result = [[] for _ in paths]
    for deck in decks:
        if deck['usable']:
            continue
        for span in (s for s in line.overhead if s.osm_id == deck['osm_id']):
            for ti, path in enumerate(paths):
                inside = [i for i, p in enumerate(path) if p in span.deck]
                for i in inside:
                    last = result[ti][-1] if result[ti] else None
                    if last and last['source'] == deck['osm_id'] and i <= last['end'] + 2:
                        last['end'] = i
                    else:
                        result[ti].append({'kind': 'overhead', 'source': deck['osm_id'], 'start': i,
                                           'end': i, 'deck': deck['status'], 'reason': deck['reason']})
    for ti, intervals in enumerate(result):
        merged = []
        for gap in sorted(intervals, key=lambda g: (g['start'], g['end'])):
            if merged and gap['start'] <= merged[-1]['end'] + 2:
                merged[-1]['end'] = max(merged[-1]['end'], gap['end'])
                if gap['source'] not in merged[-1]['source'].split('+'):
                    merged[-1]['source'] += '+' + gap['source']
            else:
                merged.append(gap)
        result[ti] = merged
    return result


def _equipped(value) -> bool:
    """True when a crossing equipment tag asks for a piece of kit."""
    return value not in (None, '', 'no')


def crossing_is_feature(road: Mapping, crossing=None) -> bool:
    """Whether this level crossing is its own feature, and so a gap in the line.

    A mapped road (``ROAD_CLASSES``) is always its own feature. So is any
    crossing whose barrier, light or bell tag is set to something other than
    empty or ``no``, including a footpath that has those tags. A footpath or
    private way with none of them stays on the line: no gap, rails continuous,
    and the path surface laid across the rails.

    @param road: tags of the way being crossed
    @param crossing: optional :class:`railway_evidence.LevelCrossing`; its
        equipment tags win over the same keys copied onto ``road``
    @returns: True when the line must leave a gap for a crossing feature
    """
    tags = dict(road or {})
    highway = tags.get('highway') or ''
    if crossing is None:
        flags = (tags.get('crossing:barrier'), tags.get('crossing:light'), tags.get('crossing:bell'))
    else:
        flags = (crossing.barrier, crossing.light, crossing.bell)
    return highway in ROAD_CLASSES or any(_equipped(flag) for flag in flags)


def _crossing_indices(path, poly, along, half):
    """Path indices within ``half`` of a crossing's distance along its track."""
    intervals = []
    for p in path:
        best = None
        along0 = 0.0
        for a, b in zip(poly, poly[1:]):
            distance, at = cellmath.segment_distance(p, a, b, along=True)
            candidate = (distance, along0 + at)
            if best is None or candidate[0] < best[0]:
                best = candidate
            along0 += math.dist(a, b)
        intervals.append(None if best is None else best[1])
    included = [i for i, value in enumerate(intervals) if value is not None and abs(value - along) <= half]
    if not included:
        return None
    return min(included), max(included)


def _plain_half(road: Mapping) -> float:
    """Half-width of a crossing the line itself paints, in blocks."""
    if road.get('width'):
        return road_width(dict(road)) / 2
    highway = road.get('highway') or ''
    if highway in PATH_WIDTH:
        return PATH_WIDTH[highway] / 2
    return road_width(dict(road)) / 2


def _plain_surface(road: Mapping) -> str:
    """Surface kind a footpath or private crossing lays across the rails."""
    highway = road.get('highway') or ''
    default = 'gravel' if highway in ('track', 'service') else 'paving'
    return SURFACE_KIND.get(road.get('surface') or '', default)


def _plain_crossing_surfaces(line, paths, roads) -> dict:
    """Track columns of crossings that stay on the line, mapped to a surface kind."""
    out = {}
    for crossing in line.crossings:
        ti = crossing.track_index
        if ti >= len(paths) or not paths[ti]:
            continue
        road = roads.get(str(crossing.osm_id))
        if road is None or crossing_is_feature(road, crossing):
            continue
        span = _crossing_indices(paths[ti], line.tracks[ti], crossing.along, _plain_half(road))
        if span is None:
            continue
        kind = _plain_surface(road)
        for p in paths[ti][span[0]:span[1] + 1]:
            out[tuple(p)] = kind
    return out


def _structure_gaps(line, paths, roads, decks, near):
    """Overhead, crossing and bridge/tunnel gap intervals in ``paths`` indices.

    @param near: ``(x0, z0, x1, z1)`` inside which a level crossing is considered, or None for all
    """
    result = _overhead_gaps(line, paths, decks)
    for crossing in line.crossings:
        ti = crossing.track_index
        if ti >= len(paths) or not paths[ti]:
            continue
        if near is not None and not _inside(crossing.at, near):
            continue
        road = roads.get(str(crossing.osm_id))
        if road is None:
            # Width is evidence, not a guessed road class or a default design.
            result[ti].append({'error': 'crossing is missing road tags', 'where': list(crossing.at)})
            continue
        # A footpath or private crossing with no equipment stays on the line.
        if not crossing_is_feature(road, crossing):
            continue
        half = road_width(dict(road)) / 2 + 1
        span = _crossing_indices(paths[ti], line.tracks[ti], crossing.along, half)
        if span:
            result[ti].append({'kind': 'crossing', 'source': crossing.osm_id,
                               'start': span[0], 'end': span[1]})
    for span in line.spans:
        for track in span.tracks:
            ti = track.track_index
            if ti >= len(paths) or not paths[ti]:
                continue
            poly = line.tracks[ti]
            selected = []
            for i, p in enumerate(paths[ti]):
                along0, best = 0.0, None
                for a, b in zip(poly, poly[1:]):
                    d, along = cellmath.segment_distance(p, a, b, along=True)
                    if best is None or d < best[0]:
                        best = (d, along0 + along)
                    along0 += math.dist(a, b)
                if track.along0 <= best[1] <= track.along1:
                    selected.append(i)
            if selected:
                result[ti].append({'kind': span.kind, 'source': span.bridge_ref,
                                   'start': min(selected), 'end': max(selected)})
    return result


def _in_window(intervals, path, lo, hi):
    """Keep gaps wholly inside ``path[lo..hi]`` (shifted to start at 0); fail on straddlers.

    A gap wholly outside belongs to the neighbouring feature. One that crosses the
    window edge cannot be recorded with two abutting rails, so it becomes an ``error``.
    """
    kept = []
    for gap in intervals:
        if 'error' in gap:
            kept.append(gap)
        elif gap['end'] < lo or gap['start'] > hi:
            continue
        elif gap['start'] < lo or gap['end'] > hi:
            edge = lo if gap['start'] < lo else hi
            kept.append({'where': list(path[edge]), 'error':
                         f'{gap["kind"]} gap{" " + str(gap["source"]) if gap["source"] else ""} straddles the planning box edge at '
                         f'{path[edge][0]},{path[edge][1]}: choose a start cell whose box holds the whole '
                         'gap (docs/plans/railway.md decision 17)'})
        else:
            kept.append({**gap, 'start': gap['start'] - lo, 'end': gap['end'] - lo})
    return kept


def _gaps(line, paths, roads, decks=(), junctions=None, windows=None, near=None):
    """Gap intervals in snapped path indices, with source ids for integration.

    @param paths: per-track columns; with ``windows`` they run past the planned visit
    @param windows: per-track inclusive ``(lo, hi)`` of the planned visit inside ``paths``;
        returned gaps are numbered from ``lo`` and ``junctions`` use the same numbering
    @param near: box inside which a level crossing counts; others are another feature's
    """
    windows = windows or [(0, len(p) - 1) for p in paths]
    wide = _structure_gaps(line, paths, roads, decks, near)
    result = [_in_window(wide[ti], paths[ti], *windows[ti]) if paths[ti] else wide[ti]
              for ti in range(len(paths))]
    cores = [p[lo:hi + 1] for p, (lo, hi) in zip(paths, windows)]
    for ti, intervals in (junctions or {}).items():
        if ti >= len(paths) or not paths[ti]:
            continue
        for gap in intervals:
            if not 0 <= gap['start'] <= gap['end'] < len(cores[ti]):
                raise ValueError(f'junction gap {gap["start"]}..{gap["end"]} is outside track {ti}')
            result[ti].append({**gap, 'kind': 'junction'})
    for ti, intervals in enumerate(result):
        ordered = sorted((g for g in intervals if 'error' not in g), key=lambda g: (g['start'], g['end']))
        merged = []
        for gap in ordered:
            last = merged[-1] if merged else None
            if last and gap['start'] <= last['end'] + 1:
                if 'junction' not in (gap['kind'], last['kind']):
                    result[ti] = [{'where': list(cores[ti][gap['start']]),
                                   'error': 'overlapping or adjacent gap spans need a combined structure: '
                                            f'{last["source"] or last["kind"]}, {gap["source"] or gap["kind"]}'}]
                    break
                # A junction touching a structure gap is one stretch for a person to finish.
                structure = gap if gap['kind'] != 'junction' else last
                last['also'] = sorted({*last.get('also', ()), *gap.get('also', ()),
                                       *([structure['kind']] if structure['kind'] != 'junction' else ())})
                last['source'] = '+'.join(dict.fromkeys(
                    [*last['source'].split('+'), *gap['source'].split('+')]))
                last['kind'] = 'junction'
                last['start'], last['end'] = min(last['start'], gap['start']), max(last['end'], gap['end'])
            else:
                merged.append(dict(gap))
        else:
            result[ti] = merged + [g for g in intervals if 'error' in g]
    return result


def _solve(path, targets, terrain, start, minimum, deviation, forbidden, initial_run, pair_bounds=None):
    """Least-error profile with unit steps, spacing and solid legal corners.

    Numpy advances the level/run state matrix; int32 back pointers keep the
    planning-box case bounded without retaining Python objects for every state.
    """
    low = math.floor(min(min(targets), min(terrain))) - deviation
    high = math.ceil(max(max(targets), max(terrain))) + deviation
    levels = np.arange(low, high + 1)
    width = minimum + 1
    cost = np.full((len(levels), width), np.inf)
    if not low <= start <= high or abs(start - terrain[0]) > deviation:
        return None
    if pair_bounds is not None and abs(start - pair_bounds[0]) > 1:
        return None
    cost[start - low, min(minimum, initial_run)] = abs(start - targets[0])
    back = []
    states = np.arange(cost.size, dtype=np.int32).reshape(cost.shape)
    for i in range(1, len(path)):
        new = np.full_like(cost, np.inf)
        parent = np.full(cost.shape, -1, dtype=np.int32)
        new[:, 1:] = cost[:, :-1]
        parent[:, 1:] = states[:, :-1]
        better = cost[:, -1] < new[:, -1]
        new[better, -1] = cost[better, -1]
        parent[better, -1] = states[better, -1]
        # Both low and high cells need a straight axis: a step cannot meet a corner.
        eligible = (1 < i < len(path) - 1 and i not in forbidden and (
                    path[i - 2][0] == path[i - 1][0] == path[i][0] == path[i + 1][0]
                    or path[i - 2][1] == path[i - 1][1] == path[i][1] == path[i + 1][1]))
        if eligible:
            for shift in (-1, 1):
                src = slice(0, -1) if shift == 1 else slice(1, None)
                dst = slice(1, None) if shift == 1 else slice(0, -1)
                step = cost[src, -1] + 3.0
                take = step < new[dst, 0]
                new[dst, 0] = np.where(take, step, new[dst, 0])
                parent[dst, 0] = np.where(take, states[src, -1], parent[dst, 0])
        new += np.abs(levels - targets[i])[:, None]
        invalid = np.abs(levels - terrain[i]) > deviation
        if pair_bounds is not None:
            invalid |= np.abs(levels - pair_bounds[i]) > 1
        new[invalid] = np.inf
        cost = new
        back.append(parent)
    state = int(np.argmin(cost))
    if not np.isfinite(cost.flat[state]):
        return None
    result = [0] * len(path)
    for i in range(len(path) - 1, -1, -1):
        result[i] = int(levels[state // width])
        if i:
            state = int(back[i - 1].flat[state])
    return result


def _corridor(plan, ground, deck, supports, road_surfaces):
    """Raster bed, exact nearest-bed side slopes, and deterministic low bushes."""
    # Retain gap cells in the distance calculation. Their nearest-bed territory
    # belongs to the later structure, including shoulders and side slopes.
    bed = {}
    for track in plan['tracks']:
        for p, level, gap in zip(track['path'], track['levels'], track['gap']):
            for dx in range(-3, 4):
                for dz in range(-3, 4):
                    distance = math.hypot(dx, dz)
                    q = p[0] + dx, p[1] + dz
                    if distance > BED_RADIUS or not _inside(q, plan['box']):
                        continue
                    old = bed.get(q)
                    if old is None or (distance, -level, not gap) < (old[1], -old[0], not old[2]):
                        bed[q] = (level, distance, gap)
    if not bed:
        return
    x0 = max(plan['box'][0], min(p[0] for p in bed) - SLOPE_MAX)
    z0 = max(plan['box'][1], min(p[1] for p in bed) - SLOPE_MAX)
    x1 = min(plan['box'][2], max(p[0] for p in bed) + SLOPE_MAX)
    z1 = min(plan['box'][3], max(p[1] for p in bed) + SLOPE_MAX)
    distances = np.full((x1 - x0 + 1, z1 - z0 + 1), np.inf)
    levels = np.zeros(distances.shape, dtype=np.int32)
    gap_owned = np.zeros(distances.shape, dtype=bool)
    for (x, z), (level, _, gap) in sorted(bed.items()):
        a, b = max(x0, x - SLOPE_MAX), min(x1, x + SLOPE_MAX)
        c, d = max(z0, z - SLOPE_MAX), min(z1, z + SLOPE_MAX)
        ds = np.hypot(np.arange(a, b + 1)[:, None] - x, np.arange(c, d + 1)[None, :] - z)
        view = distances[a - x0:b - x0 + 1, c - z0:d - z0 + 1]
        lv = levels[a - x0:b - x0 + 1, c - z0:d - z0 + 1]
        gaps = gap_owned[a - x0:b - x0 + 1, c - z0:d - z0 + 1]
        take = (ds < view) | ((ds == view) & ((level > lv) | ((level == lv) & gap)))
        view[take], lv[take] = ds[take], level
        gaps[take] = gap
    for i, j in np.argwhere(distances <= SLOPE_MAX):
        p = int(i) + x0, int(j) + z0
        if gap_owned[i, j] or p in supports:
            continue
        g = _ground(ground, p)
        if g is None:
            continue
        kind = 'bed' if p in bed else 'slope'
        if p in deck and kind == 'slope':
            continue
        level = bed[p][0] if kind == 'bed' else int(levels[i, j])
        rise = int(distances[i, j] / SLOPE_RATIO)
        surface = level if kind == 'bed' else min(g, level + rise) if g > level else max(g, level - rise)
        if kind == 'slope' and surface == g:
            continue
        plan['columns'].append([*p, g, surface, kind])
        palette = road_surfaces.get(p, 'gravel' if kind == 'bed' else 'grass')
        plan['surfaces'].append([*p, palette])
        h = _sample(ground, 'h', p, g)
        if (kind == 'slope' and palette == 'grass' and h > g and p not in deck
                and (p[0] * 73856093 ^ p[1] * 19349663) % 5 == 0):
            plan['planting'].append([p[0], surface + 1, p[1], 'bush'])


def rail_rows(path, formation_levels, *, before=None, after=None, omit=()):
    """Rail, torch and support rows shared by the line and a crossing.

    ``formation_levels`` is the block under each rail. Straight and ascending
    cells are powered, with a torch two blocks below the rail and a solid
    support under the torch. Corners stay plain rail. ``omit`` lists path
    indices left empty (a structure gap).

    @param path: (x, z) columns in order
    @param formation_levels: formation Y for each column
    @param before: neighbour before the first column, when it is outside ``path``
    @param after: neighbour after the last column, when it is outside ``path``
    @param omit: path indices that stay empty
    @returns: (rails, torches, supports) in the planner's row shape
    """
    skip = set(omit)
    rails, torches, supports = [], [], []
    for i, (p, level) in enumerate(zip(path, formation_levels)):
        if i in skip:
            continue
        shape = _shape(path, formation_levels, i, before, after)
        powered = shape in ('east_west', 'north_south') or shape.startswith('ascending_')
        block = (f'minecraft:powered_rail[shape={shape},powered=true]' if powered
                 else f'minecraft:rail[shape={shape}]')
        rails.append([p[0], level + 1, p[1], shape, block])
        if powered:
            torches.append([p[0], level - 1, p[1]])
            supports.append([p[0], level - 2, p[1]])
    return rails, torches, supports


def plan_line(
    line: Line, ground: Mapping, start_cell: tuple[int, int], incoming: Sequence[dict] = (), *,
    crossing_roads: Mapping[str, Mapping] | None = None,
    road_surfaces: Mapping[tuple[int, int], str] | None = None,
    min_step_run: int = RAIL_MIN_STEP_RUN, max_deviation: int = RAIL_MAX_DEVIATION,
    cell_size: int | None = None,
    structures: Mapping | None = None, inferred_deck_usable: bool | None = None,
    junction_gaps: Mapping[int, Sequence[Mapping]] | None = None,
) -> dict:
    """Plan one box of a single/paired through line; report unbuildable tracks.

    @param line: immutable extracted through-line evidence (never junction/decor ways)
    @param ground: arrays described in the module docstring
    @param start_cell: coverage-cell coordinate around which to centre the planning box (:data:`PLANNER_BOX` on a side)
    @param incoming: outgoing joins from earlier plans of this line
    @param crossing_roads: crossing node id -> actual crossing road tags; width uses road_width
    @param road_surfaces: vector-map column -> ground surface kind inside the footprint
    @param min_step_run: positive minimum number of rail cells between unit steps
    @param max_deviation: maximum formation-height departure from DTM under a track
    @param cell_size: explicit coverage-grid size, or cellmath's configured size
    @param structures: deck evidence by way id (see :func:`deck_evidence`); None keeps the lidar surface as ceiling
    @param inferred_deck_usable: trust an inferred deck height; defaults to :data:`INFERRED_DECK_USABLE`
    @param junction_gaps: track index -> ``junction`` gap intervals from :func:`junction_gaps`
    @returns: JSON-able placement lists, profiles, gaps, joins and explicit failures
    """
    if (not isinstance(min_step_run, int) or isinstance(min_step_run, bool) or min_step_run < 1
            or not isinstance(max_deviation, int) or isinstance(max_deviation, bool) or max_deviation < 0):
        raise ValueError('min_step_run must be a positive integer and max_deviation a nonnegative integer')
    size = cellmath.cell_size() if cell_size is None else cell_size
    bx, bz, ex, ez = cellmath.bounds(start_cell, size=size)
    centre = (bx + ex + 1) // 2, (bz + ez + 1) // 2
    box = [centre[0] - PLANNER_BOX // 2, centre[1] - PLANNER_BOX // 2,
           centre[0] + PLANNER_BOX // 2 - 1, centre[1] + PLANNER_BOX // 2 - 1]
    plan = dict(line_id=line.id, box=box, min_step_run=min_step_run, max_deviation=max_deviation,
                rails=[], torches=[], torch_supports=[], columns=[], surfaces=[], planting=[],
                tracks=[], gaps=[], joins=[], failures=[], decks=[])

    def fail(where, reason):
        plan['failures'].append({'where': list(where), 'reason': reason})

    entries = {}
    for join in incoming:
        if join.get('line_id', line.id) != line.id:
            fail(start_cell, 'incoming join belongs to another line')
            return plan
        for track in join.get('tracks', ()):
            ti = track['track_index']
            if ti in entries and entries[ti] != track:
                fail(start_cell, 'conflicting incoming join records')
                return plan
            entries[ti] = track
    paths, previous_cells, next_cells, wide, windows = [], [], [], [], []
    for ti, poly in enumerate(line.tracks):
        try:
            snapped = snap_track(poly)
        except ValueError as exc:
            fail(start_cell, str(exc))
            snapped = []
        full, a, b, before, nxt, error = _clip(snapped, box, entries.get(ti), centre)
        path = full[a:b]
        if error or len(path) < 2:
            fail(path[0] if path else start_cell, error or 'track needs at least two rail cells')
            path = []
        if any((q[0] - p[0], q[1] - p[1]) not in cellmath.N4 for p, q in zip(path, path[1:])):
            fail(path[0], 'path is not 4-connected after repair')
            path = []
        paths.append(path)
        previous_cells.append(before)
        next_cells.append(nxt)
        # Gaps are found on the visit plus a margin, so one that crosses the box edge is seen.
        lo = max(0, a - EDGE_MARGIN)
        wide.append(full[lo:b + EDGE_MARGIN] if path else [])
        windows.append((a - lo, b - 1 - lo))
    under, plan['decks'] = deck_evidence(line, structures, inferred_deck_usable)
    near = (box[0] - EDGE_MARGIN, box[1] - EDGE_MARGIN, box[2] + EDGE_MARGIN, box[3] + EDGE_MARGIN)
    gaps = _gaps(line, wide, crossing_roads or {}, plan['decks'], junction_gaps, windows, near)
    painted = _plain_crossing_surfaces(line, paths, crossing_roads or {})
    if road_surfaces:
        painted.update(road_surfaces)
    heights = []
    for ti, path in enumerate(paths):
        samples = [_ground(ground, p) for p in path]
        missing = [p for p, g in zip(path, samples) if g is None]
        if missing:
            fail(missing[0], f'missing ground under track {ti} ({len(missing)} columns)')
            paths[ti] = []
            samples = []
        heights.append(samples)
    valid = [ti for ti, p in enumerate(paths) if p]
    if not valid:
        return plan
    shared_start = round(sum(heights[ti][0] for ti in valid) / len(valid))
    first_profile, first_path = None, None
    deck = {tuple(p) for span in line.overhead for p in span.deck}
    supports = set()
    # Structure evidence outside mapped deck polygons is still excluded.
    for ti in valid:
        path = paths[ti]
        before, after = previous_cells[ti], next_cells[ti]
        targets = np.asarray(heights[ti], dtype=float)
        for other in valid:
            if other != ti:
                near = _nearest_indices(path, paths[other])
                targets += np.asarray([heights[other][i] for i in near])
        targets /= len(valid)
        targets = [float(np.median(targets[max(0, i - 4):i + 5])) for i in range(len(targets))]
        forbidden, gap_mask = set(), [False] * len(path)
        bad_gap = False
        for gap in gaps[ti]:
            if 'error' in gap:
                fail(gap['where'], gap['error'])
                bad_gap = True
                continue
            for i in range(gap['start'], gap['end'] + 1):
                gap_mask[i] = True
            forbidden.update(range(max(1, gap['start']), min(len(path), gap['end'] + 2)))
        if bad_gap:
            continue
        entry = entries.get(ti)
        start = int(entry['level']) - 1 if entry else shared_start
        initial_run = int(entry.get('run', 0)) if entry else min_step_run
        pair_bounds = None
        if first_profile is not None:
            pair_bounds = [first_profile[i] for i in _nearest_indices(path, first_path)]
        levels = _solve(path, targets, heights[ti], start, min_step_run, max_deviation,
                        forbidden, initial_run, pair_bounds)
        if levels is None:
            fail(path[0], f'unsolvable join/profile on track {ti} within RAIL_MAX_DEVIATION={max_deviation}')
            continue
        bad = next((p for p, g, y in zip(path, heights[ti], levels) if abs(y - g) > max_deviation), None)
        if bad:
            fail(bad, f'track {ti} exceeds RAIL_MAX_DEVIATION={max_deviation}')
            continue
        blocked = None
        for i, (p, level, gap) in enumerate(zip(path, levels, gap_mask)):
            if gap:
                continue
            if _sample(ground, 'supports', p, False):
                blocked = (p, 'rail intersects a structure support')
                break
            structure = _sample(ground, 'kind', p, 0) == 4
            if p in deck or structure:
                deck.add(p)
                ceiling = under[p] if p in under else _sample(ground, 'deck_y', p, _sample(ground, 'h', p))
                if ceiling is None or ceiling - (level + 1) < RAIL_CLEARANCE:
                    blocked = (p, 'overhead deck leaves less than 5 blocks rail clearance')
                    break
            shape = _shape(path, levels, i, before, after)
            powered = shape in ('east_west', 'north_south') or shape.startswith('ascending_')
            if powered and not _sample(ground, 'solid', p, True):
                blocked = (p, 'torch support below the formation is not solid')
                break
        if blocked:
            fail(*blocked)
            continue
        if first_profile is None:
            first_profile, first_path = levels, path
        profile = {'track_index': ti, 'path': [list(p) for p in path], 'levels': levels,
                   'gap': gap_mask, 'initial_run': initial_run,
                   'before': list(before) if before is not None else None,
                   'after': list(after) if after is not None else None,
                   'join_in': bool(entry)}
        plan['tracks'].append(profile)
        rail_list, torch_list, support_list = rail_rows(
            path, levels, before=before, after=after,
            omit=[i for i, gap in enumerate(gap_mask) if gap])
        plan['rails'].extend(rail_list)
        plan['torches'].extend(torch_list)
        plan['torch_supports'].extend(support_list)
        for gap in gaps[ti]:
            ends = []
            for index, inward in ((gap['start'] - 1, 1), (gap['end'] + 1, -1)):
                if 0 <= index < len(path):
                    neighbour = path[index + inward]
                    ends.append({'track_index': ti, 'at': list(path[index]), 'level': levels[index] + 1,
                                 'direction': _direction(path[index], neighbour)})
                elif index == -1 and entry:
                    # The gap starts at the cut: its abutting rail is the earlier feature's last one.
                    ends.append({'track_index': ti, 'at': list(before), 'level': levels[0] + 1,
                                 'direction': _direction(before, path[0]), 'from_join': True})
            plan['gaps'].append({**gap, 'track_index': ti, 'ends': ends,
                                 'where': list(path[gap['start']]),
                                 'columns': [list(p) for p in path[gap['start']:gap['end'] + 1]]})
        steps = [i for i in range(1, len(path)) if levels[i] != levels[i - 1]]
        for side, index, nxt in (('end', len(path) - 1, after), ('start', 0, before)):
            if nxt is None or _inside(nxt, box) or side == 'start' and entry:
                continue
            if gap_mask[index]:
                fail(path[index], 'planning box cuts a structure gap without an abutting rail at '
                     f'{path[index][0]},{path[index][1]}: choose a start cell whose box holds the whole gap '
                     'and a rail on each side (docs/plans/railway.md decision 17)')
                continue
            if side == 'end':
                run = len(path) - 1 - steps[-1] if steps else initial_run + len(path) - 1
            else:
                run = steps[0] - 1 if steps else min_step_run
            record = {'track_index': ti, 'side': side, 'at': list(path[index]), 'next': list(nxt),
                      'level': levels[index] + 1, 'direction': _direction(path[index], nxt),
                      'run': min(min_step_run, run)}
            plan['joins'].append({'line_id': line.id, 'cell': list(cellmath.cell_of(*nxt, size=size)),
                                  'side': side, 'level': levels[index] + 1,
                                  'direction': record['direction'], 'tracks': [record]})
    # Reject overlapping paired tracks: a last-writer-wins crossing is not a junction solution.
    positions = [(x, z) for x, _y, z, _s, _b in plan['rails']]
    if len(set(positions)) != len(positions):
        fail(start_cell, 'track paths overlap; junctions need a human')
        for key in ('rails', 'torches', 'torch_supports', 'tracks', 'gaps', 'joins'):
            plan[key] = []
        return plan
    for key in ('supports', 'kind'):
        array = ground.get(key)
        if array is not None:
            for i, j in np.argwhere(np.asarray(array) if key == 'supports' else np.asarray(array) == 4):
                p = int(i) + ground['x0'], int(j) + ground['z0']
                if key == 'supports':
                    supports.add(p)
                else:
                    deck.add(p)
    _corridor(plan, ground, deck, supports, painted)
    return plan


def _line_rank(plan):
    """Main line first: more tracks, then more rail columns, then lowest line id."""
    return -len(plan['tracks']), -len(plan['rails']), plan['line_id']


def junction_gaps(plans: Sequence[Mapping]) -> dict:
    """Choose the main line where plans share rail columns; gap the side tracks.

    The line with the best rank (:func:`_line_rank`) keeps every shared rail column.
    Each other claimant's overlapping rail columns, plus any rail run that the
    overlap leaves shorter than :data:`JUNCTION_MIN_RUN` with nothing to connect to
    (junction gaps or a dead end on both sides, no box join and no structure gap),
    become ``junction`` gaps whose ``source`` is the winning line id.

    @param plans: first-pass :func:`plan_line` results of one feature
    @returns: ``{plan_index: {track_index: [gap, ...]}}`` for the side plans only; pass
        each value back to :func:`plan_line` as ``junction_gaps``
    @raises ValueError: with coordinates, when a join end would be gapped or a line vanishes
    """
    ids = [p['line_id'] for p in plans]
    if len(set(ids)) != len(ids):
        raise ValueError('railway plans need distinct line ids to choose a main line')
    rank = {i: _line_rank(p) for i, p in enumerate(plans)}
    claims = {}
    for i, p in enumerate(plans):
        for r in p['rails']:
            claims.setdefault((r[0], r[2]), []).append(i)
    lost = {}
    for q, claimants in claims.items():
        if len(claimants) > 1:
            winner = min(claimants, key=rank.get)
            for i in claimants:
                if i != winner:
                    lost.setdefault(i, {})[q] = winner
    result = {}
    for i, columns in lost.items():
        plan, by_track, kept = plans[i], {}, 0
        for track in plan['tracks']:
            path, g0, n = [tuple(q) for q in track['path']], track['gap'], len(track['path'])
            mask = [q in columns for q in path]
            # A junction that begins on an incoming join is fine: the earlier feature's last
            # rail abuts it (the gap's first end record). Any other cut inside one is refused.
            for index, joined in ((0, None if track.get('join_in') else track['before']), (n - 1, track['after'])):
                if mask[index] and joined is not None:
                    raise ValueError(f'junction with {ids[columns[path[index]]]} covers the box join of '
                                     f'{ids[i]} at {list(path[index])}: choose a start cell whose box holds '
                                     'the whole junction (docs/plans/railway.md decision 17)')
            full = list(mask)
            start = 0
            while start < n:
                if mask[start] or g0[start]:
                    start += 1
                    continue
                end = start
                while end + 1 < n and not mask[end + 1] and not g0[end + 1]:
                    end += 1
                anchored = ((track['before'] is not None if start == 0 else bool(g0[start - 1]))
                            or (track['after'] is not None if end == n - 1 else bool(g0[end + 1])))
                if any(mask) and end - start + 1 < JUNCTION_MIN_RUN and not anchored:
                    full[start:end + 1] = [True] * (end - start + 1)
                start = end + 1
            kept += sum(1 for k in range(n) if not full[k] and not g0[k])
            intervals, index = [], 0
            while index < n:
                if not full[index]:
                    index += 1
                    continue
                end = index
                while end + 1 < n and full[end + 1]:
                    end += 1
                sources = []
                for q in path[index:end + 1]:
                    if q in columns and ids[columns[q]] not in sources:
                        sources.append(ids[columns[q]])
                intervals.append({'kind': 'junction', 'source': '+'.join(sources),
                                  'start': index, 'end': end})
                index = end + 1
            if intervals:
                by_track[track['track_index']] = intervals
        if kept <= 0:
            first = min(columns)
            raise ValueError(f'{ids[i]} lies entirely inside rails of other lines, e.g. at {list(first)}')
        result[i] = by_track
    return result


def share_footprints(plans: Sequence[dict]) -> None:
    """Give each column that corridors claim differently to one owner, in place.

    Columns that plans write identically, with no rail among them, stay as they
    are. For any other shared column the plan holding a rail there keeps it; otherwise the best
    ranked (main) line wins. Every other plan drops that column's ground, surface
    and planting, so a junction's side track neither rewrites the main line's
    formation nor buries its own rails under it.
    """
    rails = [{(r[0], r[2]) for r in p['rails']} for p in plans]
    rows = []
    for p in plans:
        surfaces = {(x, z): kind for x, z, kind in p['surfaces']}
        planting = {}
        for x, y, z, kind in p['planting']:
            planting.setdefault((x, z), []).append((y, kind))
        rows.append({(c[0], c[1]): (tuple(c), surfaces.get((c[0], c[1])), tuple(sorted(planting.get((c[0], c[1]), ()))))
                     for c in p['columns']})
    claims = {}
    for i, mine in enumerate(rows):
        for q in mine:
            claims.setdefault(q, []).append(i)
    drops = [set() for _ in plans]
    for q, claimants in claims.items():
        if len(claimants) < 2:
            continue
        holders = [i for i in claimants if q in rails[i]]
        if not holders and len({rows[i][q] for i in claimants}) == 1:
            continue
        holders = holders or claimants
        owner = min(holders, key=lambda i: _line_rank(plans[i]))
        for i in claimants:
            if i != owner:
                drops[i].add(q)
    for p, owned in zip(plans, drops):
        if owned:
            for key, (a, b) in (('columns', (0, 1)), ('surfaces', (0, 1)), ('planting', (0, 2))):
                p[key] = [row for row in p[key] if (row[a], row[b]) not in owned]


def validate_plan(plan: Mapping) -> None:
    """Assert path, gradient, power, spacing, gap and placement invariants.

    @param plan: output of plan_line, or its JSON round trip
    @raises AssertionError: when a placement or profile violates rail legality
    """
    rails = {(r[0], r[2]): r for r in plan['rails']}
    assert len(rails) == len(plan['rails']), 'overlapping rails'
    torches = {tuple(p) for p in plan['torches']}
    supports = {tuple(p) for p in plan['torch_supports']}
    expected, expected_torches = set(), set()
    for track in plan['tracks']:
        path, levels, gaps = track['path'], track['levels'], track['gap']
        before, after = track['before'], track['after']
        if before is not None:
            assert (path[0][0] - before[0], path[0][1] - before[1]) in cellmath.N4
        if after is not None:
            assert (after[0] - path[-1][0], after[1] - path[-1][1]) in cellmath.N4
        assert len(path) == len(levels) == len(gaps)
        assert len({tuple(p) for p in path}) == len(path), 'revisited rail column'
        previous_step = None
        for i, p in enumerate(path):
            if i:
                assert (p[0] - path[i - 1][0], p[1] - path[i - 1][1]) in cellmath.N4, 'disconnected path'
                delta = levels[i] - levels[i - 1]
                assert abs(delta) <= 1, 'non-unit rail step'
                if delta:
                    assert not gaps[i - 1] and not gaps[i], 'step inside gap'
                    low, high = (i - 1, i) if delta > 0 else (i, i - 1)
                    assert _shape(path, levels, low, before, after) == 'ascending_' + _direction(path[low], path[high]), 'step on a corner'
                    if previous_step is not None:
                        assert i - previous_step >= plan['min_step_run'], 'steps too close'
                    else:
                        assert i + track['initial_run'] >= plan['min_step_run'], 'step too close to incoming join'
                    previous_step = i
            if gaps[i]:
                assert tuple(p) not in rails, 'rail placed in structure gap'
                continue
            expected.add(tuple(p))
            rail = rails[tuple(p)]
            assert rail[1] == levels[i] + 1
            shape = _shape(path, levels, i, before, after)
            assert rail[3] == shape, 'shape does not connect neighbours'
            if shape in ('north_south', 'east_west') or shape.startswith('ascending_'):
                assert rail[4] == f'minecraft:powered_rail[shape={shape},powered=true]'
                torch = p[0], rail[1] - 2, p[1]
                expected_torches.add(torch)
                assert torch in torches and (p[0], rail[1] - 3, p[1]) in supports
                if shape.startswith('ascending_'):
                    direction = cellmath.FACING[shape.removeprefix('ascending_')]
                    up = next((j for j in (i - 1, i + 1) if 0 <= j < len(path)
                               and (path[j][0] - p[0], path[j][1] - p[1]) == direction), None)
                    assert up is not None and levels[up] == levels[i] + 1
                    assert 0 < i < len(path) - 1, 'ascending endpoint'
                    assert path[i - 1][0] == p[0] == path[i + 1][0] or path[i - 1][1] == p[1] == path[i + 1][1]
            else:
                assert rail[4] == f'minecraft:rail[shape={shape}]', 'powered corner'
    assert expected == set(rails), 'unprofiled rail placement'
    assert expected_torches == torches, 'extra torch placement'
    assert supports == {(x, y - 1, z) for x, y, z in torches}, 'extra torch support'
    columns = {(r[0], r[1]) for r in plan['columns']}
    assert len(columns) == len(plan['columns']), 'duplicate corridor columns'
    gap_columns = {tuple(p) for gap in plan['gaps'] for p in gap['columns']}
    assert not columns & gap_columns, 'ground placed inside a structure gap'
    assert not {(p[0], p[2]) for p in plan['planting']} & gap_columns
    assert all(_inside((r[0], r[2]), plan['box']) for r in plan['rails'])
    assert all(_inside((r[0], r[1]), plan['box']) for r in plan['columns'])
    profiles = {t['track_index']: t for t in plan['tracks']}
    for gap in plan['gaps']:
        track = profiles[gap['track_index']]
        assert gap['columns'] == track['path'][gap['start']:gap['end'] + 1]
        assert len({e['level'] for e in gap['ends']}) <= 1, 'gap levels are not continuous'
        for end in gap['ends']:
            if end.get('from_join'):
                assert gap['start'] == 0 and end['at'] == track['before'], 'join end is not at the cut'
                assert end['level'] == track['levels'][0] + 1
                assert end['direction'] == _direction(end['at'], track['path'][0])
                continue
            i = track['path'].index(end['at'])
            assert end['level'] == track['levels'][i] + 1
            assert not track['gap'][i]
            neighbour = track['path'][i + (1 if i < gap['start'] else -1)]
            assert end['direction'] == _direction(end['at'], neighbour)
    for join in plan['joins']:
        for record in join['tracks']:
            track = profiles[record['track_index']]
            index = 0 if record['side'] == 'start' else len(track['path']) - 1
            assert record['at'] == track['path'][index]
            assert record['level'] == track['levels'][index] + 1
            assert tuple(record['at']) in rails, 'join has no abutting rail'
            assert record['next'] == track['before' if record['side'] == 'start' else 'after']
            assert not _inside(record['next'], plan['box'])
            assert record['direction'] == _direction(record['at'], record['next'])
            assert 0 <= record['run'] <= plan['min_step_run']
    if len(plan['tracks']) > 1:
        first = plan['tracks'][0]
        for other in plan['tracks'][1:]:
            near = _nearest_indices(other['path'], first['path'])
            assert all(abs(y - first['levels'][i]) <= 1 for y, i in zip(other['levels'], near)), 'paired tracks differ by more than one level'
