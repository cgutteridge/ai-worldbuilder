"""Clean roof surfaces fitted to noisy lidar heights (task #447, follow-up to #434).

Lidar heights over one roof zone carry noise: chimneys, edge cells that read the wall or the ground,
and canopy. Building them column by column turns every wobble into a stair or a slab. Here one roof
zone (a set of columns the image picked out) is fitted by the simplest of three shapes, and the
fitted surface stands in for the raw heights:

* ``flat``  - one level, for a deck;
* ``plane`` - one slope, for a monopitch or lean-to;
* ``gable`` - two slopes meeting at a ridge, which runs along the zone's axis or across it.

The simplest shape that explains the heights within the lidar's own noise wins. This module is
pure geometry: it places nothing and reads no world.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

MIN_POINTS = 6
TRIM_M = 1.0              # cells further than this from the fit (edge cells, spikes) are refitted without
TRIM_PASSES = 3
NOISE_M = 0.15            # a richer shape must beat the simpler one by more than this (metres RMS) ...
NOISE_FRACTION = 0.25     # ... and by this fraction of the simpler one's RMS
MIN_SLOPE = 0.15          # metres per metre: a slope shallower than this is a flat roof
RIDGE_INSET_M = 1.0       # a ridge needs this much roof on each side of it
RIDGE_STEP_M = 0.25


@dataclass(frozen=True)
class Surface:
    """A fitted roof surface over a zone.

    @ivar kind: ``flat`` / ``plane`` / ``gable``
    @ivar rms: RMS of the kept cells against the fit, metres
    @ivar kept: how many cells the fit used (cells beyond ``TRIM_M`` are dropped)
    @ivar ridge_across: for a gable, True when the ridge runs across the axis (parallel to the road
        of a deep plot), False when it runs along the axis
    @ivar params: shape parameters in the axis frame (``u`` along the axis, ``v`` across it)
    """
    kind: str
    rms: float
    kept: int
    ridge_across: bool
    params: tuple
    axis_deg: float

    def height(self, x, z):
        """Surface height in metres at world ``(x, z)`` (scalars or arrays)."""
        c, s = math.cos(math.radians(self.axis_deg)), math.sin(math.radians(self.axis_deg))
        u = np.asarray(x, dtype=float) * c + np.asarray(z, dtype=float) * s
        v = -np.asarray(x, dtype=float) * s + np.asarray(z, dtype=float) * c
        if self.kind == 'flat':
            return np.full(np.shape(u), self.params[0])
        if self.kind == 'plane':
            a, bu, bv = self.params
            return a + bu * u + bv * v
        h, r, k_low, k_high = self.params
        d = (u if self.ridge_across else v) - r
        return h + np.where(d <= 0, k_low * d, k_high * d)


def _frame(xs, zs, axis_deg: float):
    c, s = math.cos(math.radians(axis_deg)), math.sin(math.radians(axis_deg))
    return xs * c + zs * s, -xs * s + zs * c


def _fit_flat(u, v, h, keep):
    level = float(np.median(h[keep]))
    return (level,), np.full(h.shape, level)


def _fit_plane(u, v, h, keep):
    a = np.column_stack([np.ones(int(keep.sum())), u[keep], v[keep]])
    co, *_ = np.linalg.lstsq(a, h[keep], rcond=None)
    return tuple(float(c) for c in co), co[0] + co[1] * u + co[2] * v


def _fit_gable(u, v, h, keep, across: bool):
    """Two continuous slopes meeting at a ridge; the ridge position is searched, the rest solved."""
    t = (u if across else v)
    tk, hk = t[keep], h[keep]
    lo, hi = float(tk.min()) + RIDGE_INSET_M, float(tk.max()) - RIDGE_INSET_M
    best = None
    for r in np.arange(lo, hi + 1e-9, RIDGE_STEP_M):
        d = tk - r
        a = np.column_stack([np.ones(len(tk)), np.where(d <= 0, d, 0.0), np.where(d > 0, d, 0.0)])
        co, *_ = np.linalg.lstsq(a, hk, rcond=None)
        err = float(((a @ co - hk) ** 2).sum())
        if best is None or err < best[0]:
            best = (err, float(r), co)
    if best is None:
        return None, None
    _err, r, co = best
    k_low, k_high = float(co[1]), float(co[2])
    if k_low < MIN_SLOPE or k_high > -MIN_SLOPE:       # a gable rises to the ridge and falls away from it
        return None, None
    d = t - r
    return (float(co[0]), r, k_low, k_high), co[0] + np.where(d <= 0, co[1] * d, co[2] * d)


def _trimmed(fit, u, v, h, passes: int = TRIM_PASSES):
    """Fit, drop cells further than ``TRIM_M`` from the result, refit; returns (params, rms, kept mask)."""
    keep = np.ones(h.shape, dtype=bool)
    params, pred = fit(u, v, h, keep)
    if params is None:
        return None, math.inf, keep
    for _ in range(passes):
        nxt = np.abs(pred - h) <= TRIM_M
        if nxt.sum() < MIN_POINTS or (nxt == keep).all():
            break
        keep = nxt
        params, pred = fit(u, v, h, keep)
        if params is None:
            return None, math.inf, keep
    rms = float(np.sqrt(np.mean((pred[keep] - h[keep]) ** 2)))
    return params, rms, keep


def fit_surface(xs, zs, heights, axis_deg: float = 0.0) -> Surface | None:
    """Fit the simplest of flat / plane / gable to a zone's lidar heights.

    @param xs: world x of the zone's columns
    @param zs: world z of the zone's columns
    @param heights: lidar height per column, metres (NaN is ignored)
    @param axis_deg: the zone's long-axis angle, degrees (atan2 of z over x), as ``footprint_axis_deg``
    @returns: the fitted surface, or None when fewer than ``MIN_POINTS`` columns have data
    """
    xs = np.asarray(xs, dtype=float).ravel()
    zs = np.asarray(zs, dtype=float).ravel()
    h = np.asarray(heights, dtype=float).ravel()
    good = np.isfinite(h)
    if int(good.sum()) < MIN_POINTS:
        return None
    xs, zs, h = xs[good], zs[good], h[good]
    u, v = _frame(xs, zs, axis_deg)
    candidates = []
    for kind, ridge_across, fit in (
        ('flat', False, _fit_flat),
        ('plane', False, _fit_plane),
        ('gable', True, lambda *a: _fit_gable(*a, across=True)),
        ('gable', False, lambda *a: _fit_gable(*a, across=False)),
    ):
        params, rms, keep = _trimmed(fit, u, v, h)
        if params is not None:
            candidates.append(Surface(kind, rms, int(keep.sum()), ridge_across, params, float(axis_deg)))
    if not candidates:
        return None
    # Simplest wins unless a richer shape beats it by more than the lidar's own noise.
    rank = {'flat': 0, 'plane': 1, 'gable': 2}
    candidates.sort(key=lambda s: (rank[s.kind], s.rms))
    chosen = candidates[0]
    for other in candidates[1:]:
        if other.rms < chosen.rms - max(NOISE_M, NOISE_FRACTION * chosen.rms):
            chosen = other
    return chosen
