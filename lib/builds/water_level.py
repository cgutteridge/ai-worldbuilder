"""Stepped integer water level along a river reach from lidar surface samples.

Pure functions: the caller supplies ordered ``(distance, height)`` samples.
The water-body builder can consume the resulting segments; real lidar sampling
and interpretation stay with its caller.
"""
from __future__ import annotations

import math
from typing import NamedTuple, Sequence


class LevelSegment(NamedTuple):
    """One constant water-surface Y along a distance interval of a reach."""

    start: float
    end: float
    level: int


def level_at_distance(segments: Sequence[LevelSegment], distance: float) -> int:
    """Read a validated contiguous profile, choosing downstream at a join.

    @param segments: ordered, contiguous output of :func:`water_level_profile`
        (or equivalent validated segments)
    @param distance: finite reach distance inside the profile's coverage
    @returns: local integer level; intervals are [start, end), except that the
        last includes its end (also supports a single zero-length segment)
    @raises ValueError: for an empty profile or an uncovered distance
    """
    if (not segments or not math.isfinite(distance)
            or distance < segments[0].start or distance > segments[-1].end):
        raise ValueError('distance is outside water-level profile coverage')
    for segment in reversed(segments):
        if segment.start <= distance:
            return segment.level
    raise ValueError('distance is outside water-level profile coverage')


def water_level_profile(
    samples: Sequence[tuple[float, float]],
    min_step_length: float,
) -> list[LevelSegment]:
    """Build a non-rising stepped integer water level from lidar surface samples.

    Rules (synthetic acceptance for #354):

    - levels never rise going downstream
    - height noise under one block does not create a step
    - a short dip (bridge hole, culvert) shorter than ``min_step_length`` is
      ignored rather than stepping
    - a gentle slope produces steps no shorter than ``min_step_length``
    - a flat reach returns one segment at today's single integer level

    Steps stay anchored to where the lidar drops. A short trailing drop is
    absorbed into the upstream level. On a steep slope each min-length stair
    catches up to the lidar height at that boundary (no deferred cliff).

    @param samples: ordered ``(distance_along_reach, surface_height)`` pairs;
        distances must be non-decreasing; heights are continuous elevations in
        the same units as Minecraft block Y (already offset if needed)
    @param min_step_length: minimum distance a candidate dip or emitted step
        must cover; must be ``> 0``
    @returns: contiguous ``LevelSegment``s covering the first sample distance
        through the last, with non-increasing integer levels
    """
    if not samples:
        raise ValueError('samples must not be empty')
    if min_step_length <= 0:
        raise ValueError('min_step_length must be > 0')

    distances = [float(d) for d, _ in samples]
    heights = [float(h) for _, h in samples]
    for i in range(1, len(distances)):
        if distances[i] < distances[i - 1]:
            raise ValueError('sample distances must be non-decreasing')

    cleaned = _fill_short_dips(distances, heights, min_step_length)
    monotone = [cleaned[0]]
    for height in cleaned[1:]:
        monotone.append(min(monotone[-1], height))

    levels = [_level_at(monotone[0])]
    for height in monotone[1:]:
        prev = levels[-1]
        if abs(height - prev) < 1.0:
            levels.append(prev)
        else:
            levels.append(min(prev, _level_at(height)))

    return _walk_steps(distances, levels, min_step_length)


def _level_at(height: float) -> int:
    """Integer block Y for a continuous surface height (floor toward -inf)."""
    return int(math.floor(height + 1e-9))


def _fill_short_dips(
    distances: Sequence[float],
    heights: Sequence[float],
    min_step_length: float,
) -> list[float]:
    """Raise short low runs so bridge holes and culverts do not force a step.

    A run is a short dip when its span is strictly less than ``min_step_length``
    and both the sample before and after the run sit at least one block above
    the run's maximum height.
    """
    out = list(heights)
    n = len(out)
    i = 0
    while i < n:
        j = i + 1
        while j < n and abs(out[j] - out[i]) < 1.0:
            j += 1
        if i > 0 and j < n:
            run_max = max(out[i:j])
            left = out[i - 1]
            right = out[j]
            span = distances[j - 1] - distances[i]
            if (
                span < min_step_length
                and left >= run_max + 1.0
                and right >= run_max + 1.0
            ):
                fill = min(left, right)
                for k in range(i, j):
                    out[k] = fill
        i = j
    return out


def _level_at_distance(
    distances: Sequence[float],
    levels: Sequence[int],
    at: float,
) -> int:
    """Integer level from the last sample at or before ``at`` (else the first)."""
    level = levels[0]
    for distance, sample_level in zip(distances, levels):
        if distance <= at + 1e-12:
            level = sample_level
        else:
            break
    return level


def _first_drop_distance(
    distances: Sequence[float],
    levels: Sequence[int],
    after: float,
    current: int,
) -> float | None:
    """Distance of the first sample after ``after`` whose level is below ``current``."""
    for distance, level in zip(distances, levels):
        if distance < after - 1e-12:
            continue
        if level < current:
            return distance
    return None


def _walk_steps(
    distances: Sequence[float],
    levels: Sequence[int],
    min_step_length: float,
) -> list[LevelSegment]:
    """Emit data-anchored steps that never rise and honour ``min_step_length``."""
    start_d = distances[0]
    end_d = distances[-1]
    if end_d <= start_d + 1e-12:
        return [LevelSegment(start_d, end_d, levels[0])]

    out: list[LevelSegment] = []
    pos = start_d
    level = levels[0]

    while pos < end_d - 1e-12:
        drop_at = _first_drop_distance(distances, levels, pos, level)
        if drop_at is None:
            out.append(LevelSegment(pos, end_d, level))
            break

        hold_until = pos + min_step_length
        lower_span = end_d - drop_at

        # Long hold then a short positive-length trailing dip: keep upstream.
        if (
            1e-12 < lower_span < min_step_length - 1e-12
            and (drop_at - pos) >= min_step_length - 1e-12
        ):
            out.append(LevelSegment(pos, end_d, level))
            break

        if drop_at + 1e-12 >= hold_until:
            # Lidar stays high long enough; step where it drops.
            if drop_at >= end_d - 1e-12:
                # Drop only at the endpoint sample: finish at that end level.
                out.append(LevelSegment(pos, end_d, min(level, levels[-1])))
                break
            out.append(LevelSegment(pos, drop_at, level))
            pos = drop_at
            level = _level_at_distance(distances, levels, pos)
            continue

        # Drop arrives before this step is long enough. Hold to min_step, then
        # catch up to the lidar height at that boundary (not the reach end).
        step_end = min(hold_until, end_d)
        data_level = _level_at_distance(distances, levels, step_end)
        next_level = min(level, data_level)
        out.append(LevelSegment(pos, step_end, level))
        if step_end >= end_d - 1e-12:
            break
        pos = step_end
        level = next_level

    return _merge_equal(out)


def _merge_equal(segments: Sequence[LevelSegment]) -> list[LevelSegment]:
    """Collapse adjacent segments that share a level."""
    if not segments:
        return []
    merged = [segments[0]]
    for seg in segments[1:]:
        prev = merged[-1]
        if seg.level == prev.level:
            merged[-1] = LevelSegment(prev.start, seg.end, prev.level)
        else:
            merged.append(seg)
    return list(merged)
