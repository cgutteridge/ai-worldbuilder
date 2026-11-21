"""Stepped integer water level along a river reach from lidar surface samples.

Pure function only (#354): the caller supplies ordered ``(distance, height)``
samples. Spec fields, builds and real lidar sampling stay in #326.
"""
from __future__ import annotations

import math
from typing import NamedTuple, Sequence


class LevelSegment(NamedTuple):
    """One constant water-surface Y along a distance interval of a reach."""

    start: float
    end: float
    level: int


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

    runs = _collapse_runs(distances, levels)
    runs = _absorb_short_drops(runs, min_step_length)
    if _runs_honour_end_and_length(runs, levels[-1], min_step_length):
        return [LevelSegment(s, e, lv) for s, e, lv in runs]
    return _stairs(
        distances[0],
        distances[-1],
        levels[0],
        levels[-1],
        min_step_length,
    )


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


def _collapse_runs(
    distances: Sequence[float],
    levels: Sequence[int],
) -> list[tuple[float, float, int]]:
    """Merge consecutive equal integer levels into ``(start, end, level)`` runs."""
    runs: list[tuple[float, float, int]] = []
    start = distances[0]
    level = levels[0]
    for i in range(1, len(levels)):
        if levels[i] != level:
            runs.append((start, distances[i], level))
            start = distances[i]
            level = levels[i]
    runs.append((start, distances[-1], level))
    return runs


def _absorb_short_drops(
    runs: Sequence[tuple[float, float, int]],
    min_step_length: float,
) -> list[tuple[float, float, int]]:
    """Keep upstream level across lower runs shorter than ``min_step_length``."""
    merged: list[list[float | int]] = [[runs[0][0], runs[0][1], runs[0][2]]]
    for start, end, level in runs[1:]:
        prev = merged[-1]
        if level > prev[2]:
            level = int(prev[2])
        if level == prev[2]:
            prev[1] = end
            continue
        if (end - start) < min_step_length:
            prev[1] = end
            continue
        merged.append([start, end, level])
    return [(float(s), float(e), int(lv)) for s, e, lv in merged]


def _runs_honour_end_and_length(
    runs: Sequence[tuple[float, float, int]],
    end_level: int,
    min_step_length: float,
) -> bool:
    """True when runs already match the observed end level and min lengths."""
    if not runs or runs[-1][2] != end_level:
        return False
    if runs[0][2] < end_level:
        return False
    for i, (start, end, _) in enumerate(runs):
        if i < len(runs) - 1 and (end - start) < min_step_length - 1e-9:
            return False
    for i in range(len(runs) - 1):
        if runs[i][2] < runs[i + 1][2]:
            return False
    return True


def _stairs(
    start_d: float,
    end_d: float,
    start_level: int,
    end_level: int,
    min_step_length: float,
) -> list[LevelSegment]:
    """Emit min-length stairs from ``start_level`` down to ``end_level``."""
    if end_level >= start_level:
        return [LevelSegment(start_d, end_d, start_level)]
    span = end_d - start_d
    drop = start_level - end_level
    if span < min_step_length:
        return [LevelSegment(start_d, end_d, start_level)]
    n_steps = min(drop, int(math.floor(span / min_step_length)))
    if n_steps <= 0:
        return [LevelSegment(start_d, end_d, start_level)]

    out: list[LevelSegment] = []
    for i in range(n_steps):
        a = start_d + i * min_step_length
        b = start_d + (i + 1) * min_step_length
        out.append(LevelSegment(a, b, start_level - i))
    last_start = start_d + n_steps * min_step_length
    if last_start < end_d - 1e-12:
        out.append(LevelSegment(last_start, end_d, end_level))
    else:
        final = out[-1]
        out[-1] = LevelSegment(final.start, end_d, end_level)
    # Collapse any accidental equal neighbours and clamp rises.
    fixed: list[LevelSegment] = []
    for seg in out:
        level = seg.level
        if fixed and level > fixed[-1].level:
            level = fixed[-1].level
        if fixed and level == fixed[-1].level:
            prev = fixed[-1]
            fixed[-1] = LevelSegment(prev.start, seg.end, prev.level)
        else:
            fixed.append(LevelSegment(seg.start, seg.end, level))
    if fixed[-1].level != end_level and len(fixed) > 1:
        fixed[-1] = LevelSegment(fixed[-1].start, fixed[-1].end, end_level)
        if fixed[-1].level > fixed[-2].level:
            fixed[-1] = LevelSegment(
                fixed[-1].start, fixed[-1].end, fixed[-2].level
            )
    return fixed
