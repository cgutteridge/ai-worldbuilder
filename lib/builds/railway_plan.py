"""Pure railway corridor planning from :class:`railway_evidence.Line`.

``plan_line(line, ground, start_cell, incoming=())`` returns JSON-able data,
never blocks or journal changes. ``ground`` has ``x0``, ``z0`` and arrays
indexed [x, z]: ``terrain`` (DTM block Y), ``h`` (surface block Y), optional
``have``, ``kind`` (4 means structure), ``supports`` (structure piers), and
``solid`` (solid material below terrain; defaults true). ``deck_y`` may give
an overhead deck's underside; otherwise h is the available height evidence.
All arrays cover the same rectangle. Missing/NaN terrain is a reported failure.

Rail tuples are [x, rail_y, z, shape, full_block_state]; columns are
[x, z, original_ground_y, formation_y, bed|slope]. ``surfaces`` gives
[x,z,surface_kind], retaining ground_sources' road vocabulary. The integrator
supplies its palette. ``planting`` is [x,y,z,'bush'] rather than a new palette.
Torch supports are explicit [x,y,z] solid-fill requirements for integration.

``tracks`` includes the continuous profiles through gaps; only ``rails`` and
the other placement lists omit gaps. A join's ``at`` is the final inside
rail column; ``next`` is the adjacent column to start the next feature,
``level`` is rail Y, and ``run`` carries step spacing across the boundary.
Pass the entire outgoing join back as ``incoming``. Gap ``ends`` describe
the two abutting rail cells, with rail Y, direction and track index.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np

import cellmath
from ground_sources import road_width
from railway_evidence import PLANNER_BOX, Line

RAIL_MIN_STEP_RUN = 100
RAIL_MAX_DEVIATION = 8
BED_RADIUS = 2.3
SLOPE_RATIO = 2.0
SLOPE_MAX = 40
RAIL_CLEARANCE = 5


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


def _shape(path, levels, i):
    p = path[i]
    before = path[i - 1] if i else (2 * p[0] - path[1][0], 2 * p[1] - path[1][1])
    after = path[i + 1] if i + 1 < len(path) else (2 * p[0] - before[0], 2 * p[1] - before[1])
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
    """One connected visit to the box, pointed along an incoming join if any."""
    if entry:
        at = tuple(entry['next'])
        if at not in path:
            return [], None, 'incoming join is not on this track'
        i = path.index(at)
        desired = cellmath.FACING[entry['direction']]
        if i + 1 < len(path) and (path[i + 1][0] - at[0], path[i + 1][1] - at[1]) == desired:
            path = path[i:]
        elif i and (path[i - 1][0] - at[0], path[i - 1][1] - at[1]) == desired:
            path = list(reversed(path[:i + 1]))
        else:
            return [], None, 'incoming join direction cannot connect'
    runs, current = [], []
    for i, p in enumerate(path):
        if _inside(p, box):
            current.append(p)
        elif current:
            runs.append((current, p))
            current = []
    if current:
        runs.append((current, None))
    if not runs:
        return [], None, 'track does not enter the planning box'
    chosen = min(runs, key=lambda r: min(math.dist(p, centre) for p in r[0])) if not entry else runs[0]
    return chosen[0], chosen[1], None


def _gaps(line, paths, roads):
    """Gap intervals in snapped path indices, with source ids for integration."""
    result = [[] for _ in paths]
    for crossing in line.crossings:
        ti = crossing.track_index
        if ti >= len(paths) or not paths[ti]:
            continue
        road = roads.get(str(crossing.osm_id))
        if road is None:
            # Width is evidence, not a guessed road class or a default design.
            result[ti].append({'error': 'crossing is missing road tags', 'where': list(crossing.at)})
            continue
        half = road_width(dict(road)) / 2 + 1
        poly = line.tracks[ti]
        intervals = []
        for p in paths[ti]:
            best = None
            along0 = 0.0
            for a, b in zip(poly, poly[1:]):
                distance, along = cellmath.segment_distance(p, a, b, along=True)
                candidate = (distance, along0 + along)
                if best is None or candidate[0] < best[0]:
                    best = candidate
                along0 += math.dist(a, b)
            intervals.append(best[1])
        included = [i for i, along in enumerate(intervals) if abs(along - crossing.along) <= half]
        if included:
            result[ti].append({'kind': 'crossing', 'source': crossing.osm_id,
                               'start': min(included), 'end': max(included)})
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


def _solve(path, targets, terrain, start, minimum, deviation, forbidden, initial_run, pair_bounds=None):
    """Least-error profile with unit steps, spacing and solid legal corners.

    Numpy advances the level/run state matrix; int32 back pointers keep the
    2048-box case bounded without retaining Python objects for every state.
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


def plan_line(
    line: Line, ground: Mapping, start_cell: tuple[int, int], incoming: Sequence[dict] = (), *,
    crossing_roads: Mapping[str, Mapping] | None = None,
    road_surfaces: Mapping[tuple[int, int], str] | None = None,
    min_step_run: int = RAIL_MIN_STEP_RUN, max_deviation: int = RAIL_MAX_DEVIATION,
    cell_size: int | None = None,
) -> dict:
    """Plan one box of a single/paired through line; report unbuildable tracks.

    @param line: immutable extracted through-line evidence (never junction/decor ways)
    @param ground: arrays described in the module docstring
    @param start_cell: coverage-cell coordinate around which to centre the 2048 box
    @param incoming: outgoing joins from earlier plans of this line
    @param crossing_roads: crossing node id -> actual crossing road tags; width uses road_width
    @param road_surfaces: vector-map column -> ground surface kind inside the footprint
    @param min_step_run: positive minimum number of rail cells between unit steps
    @param max_deviation: maximum formation-height departure from DTM under a track
    @param cell_size: explicit coverage-grid size, or cellmath's configured size
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
                tracks=[], gaps=[], joins=[], failures=[])

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
    paths, next_cells = [], []
    for ti, poly in enumerate(line.tracks):
        try:
            snapped = snap_track(poly)
        except ValueError as exc:
            fail(start_cell, str(exc))
            snapped = []
        path, nxt, error = _clip(snapped, box, entries.get(ti), centre)
        if error or len(path) < 2:
            fail(path[0] if path else start_cell, error or 'track needs at least two rail cells')
            path = []
        if any((b[0] - a[0], b[1] - a[1]) not in cellmath.N4 for a, b in zip(path, path[1:])):
            fail(path[0], 'path is not 4-connected after repair')
            path = []
        paths.append(path)
        next_cells.append(nxt)
    gaps = _gaps(line, paths, crossing_roads or {})
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
                ceiling = _sample(ground, 'deck_y', p, _sample(ground, 'h', p))
                if ceiling is None or ceiling - (level + 1) < RAIL_CLEARANCE:
                    blocked = (p, 'overhead deck leaves less than 5 blocks rail clearance')
                    break
            shape = _shape(path, levels, i)
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
                   'gap': gap_mask, 'initial_run': initial_run}
        plan['tracks'].append(profile)
        for i, (p, level) in enumerate(zip(path, levels)):
            if gap_mask[i]:
                continue
            shape = _shape(path, levels, i)
            powered = shape in ('east_west', 'north_south') or shape.startswith('ascending_')
            block = (f'minecraft:powered_rail[shape={shape},powered=true]' if powered
                     else f'minecraft:rail[shape={shape}]')
            plan['rails'].append([p[0], level + 1, p[1], shape, block])
            if powered:
                plan['torches'].append([p[0], level - 1, p[1]])
                plan['torch_supports'].append([p[0], level - 2, p[1]])
        for gap in gaps[ti]:
            ends = []
            for index, inward in ((gap['start'] - 1, 1), (gap['end'] + 1, -1)):
                if 0 <= index < len(path):
                    neighbour = path[index + inward]
                    ends.append({'track_index': ti, 'at': list(path[index]), 'level': levels[index] + 1,
                                 'direction': _direction(path[index], neighbour)})
            plan['gaps'].append({**gap, 'track_index': ti, 'ends': ends,
                                 'columns': [list(p) for p in path[gap['start']:gap['end'] + 1]]})
        nxt = next_cells[ti]
        if nxt is not None:
            steps = [i for i in range(1, len(path)) if levels[i] != levels[i - 1]]
            run = len(path) - 1 - steps[-1] if steps else initial_run + len(path) - 1
            record = {'track_index': ti, 'at': list(path[-1]), 'next': list(nxt),
                      'level': levels[-1] + 1, 'direction': _direction(path[-1], nxt),
                      'run': min(min_step_run, run)}
            plan['joins'].append({'line_id': line.id, 'cell': list(cellmath.cell_of(*nxt, size=size)),
                                  'level': levels[-1] + 1, 'direction': record['direction'], 'tracks': [record]})
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
    _corridor(plan, ground, deck, supports, road_surfaces or {})
    return plan


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
                    assert _shape(path, levels, low) == 'ascending_' + _direction(path[low], path[high]), 'step on a corner'
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
            shape = _shape(path, levels, i)
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
            i = track['path'].index(end['at'])
            assert end['level'] == track['levels'][i] + 1
            assert not track['gap'][i]
            neighbour = track['path'][i + (1 if i < gap['start'] else -1)]
            assert end['direction'] == _direction(end['at'], neighbour)
    for join in plan['joins']:
        for record in join['tracks']:
            track = profiles[record['track_index']]
            assert record['at'] == track['path'][-1]
            assert record['level'] == track['levels'][-1] + 1
            assert not _inside(record['next'], plan['box'])
            assert record['direction'] == _direction(record['at'], record['next'])
            assert 0 <= record['run'] <= plan['min_step_run']
    if len(plan['tracks']) > 1:
        first = plan['tracks'][0]
        for other in plan['tracks'][1:]:
            near = _nearest_indices(other['path'], first['path'])
            assert all(abs(y - first['levels'][i]) <= 1 for y, i in zip(other['levels'], near)), 'paired tracks differ by more than one level'
