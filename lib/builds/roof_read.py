#!/usr/bin/env python3
"""Read step: a building's roof volumes from lidar and the aerial, with cross-source trust checks (#459).

The image identifies zones, lidar gives heights and flats, and one small designer-model decision per
building labels what the numbers cannot say. No single source is always right (a tree hides lidar and the
image alike), so every attribute of every volume is checked against more than one source.

1. **Profile.** Lidar (surface minus terrain) is sampled in the plot frame: ``s`` runs along the plot's
   long axis from the street end to the rear, ``t`` across it. Rows are 0.5 m apart.
2. **Segment.** Each row is mass (two or more columns at least ``MASS_M`` up), flat, a ramp along the
   plot or falling across it. Runs of rows become volumes: ``flat``, ``ridge_across`` (up then down along
   the plot: a ridge parallel to the street), ``slope_along`` and ``cross`` (falls across the plot). A
   short ramp between two volumes is the wall step between them. A volume behind the map way is kept
   only when mass joins it to the way (a bin store, a rear extension), never across an empty yard.
   Each volume is then fitted with :func:`roof_surface.fit_surface` to name its form.
3. **Decide.** One designer-model call per building (:mod:`designer_ask`): the rotated aerial crop with
   the volumes drawn and numbered, plus a short lidar table. It answers per volume: roof form, roof type
   (colour), hidden by trees or shadow, storeys if the image shows them; and bay or dormer at the front.
4. **Trust.** Per volume and attribute (height, form, colour, storeys, front), each source's reading is
   recorded as ``agree``, ``disagree`` or ``silent`` against the value chosen, and the record says which
   source the value rests on. Lidar is degraded when its roughness against a 3x3 median is above
   ``ROUGH_MAX_M`` (clean real roofs read about 0.14 m); the image when leaf green covers more than
   ``GREEN_MAX`` of the volume or it is in deep shadow; the row is the neighbours' columns at the same
   position, and speaks only when both neighbours agree. A smooth canopy passes the roughness test, so
   green over the volume plus a row disagreement also makes lidar suspect. When both lidar and the image
   are degraded, the value falls back to the row, then the land-use class default.
5. **Propose.** A ``feature_build`` spec proposal: one zone region per volume inside the way with
   ``surface: "fit"``, ``stairs: "slope"`` on a pitched roof, ``finish: "carpet"`` for a flat volume on a
   zoned roof, and ``volumes`` for a volume beyond the way whose height rests on lidar. It is written
   beside the read, never over a world spec.

Storeys use the terrace-body rules (:func:`terrace_body.storeys_from_eave`), read per volume in the plot
frame, so a rotated footprint's low front no longer pulls the body eave down. The front feature rule is
the terrace-body one: room in the roof (``attic_rise_m``) gives a dormer, else a bay on two storeys or
more.
"""
from __future__ import annotations

import argparse
import colorsys
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

import roof_surface
import terrace_body

STEP_S = 0.5            # profile row spacing along the plot, metres
MASS_M = 2.0            # a column this far above the ground is building mass (feature_volumes uses the same)
RAMP = 0.35             # metres per metre along the plot: a row on a slope along it
STEEP = 1.0             # metres per metre: steeper than 45 degrees is a wall step between volumes, not a roof
CROSS = 0.3             # metres per metre across the plot: a row falling to one side
STEP_RUN_M = 1.5        # a ramp no longer than this between two volumes is the wall step between them
HIP_JOIN_M = 1.0        # ramps up and down meeting a long top this close (row to row) are one hipped roof
MIN_RUN_M = 1.0         # a run shorter than this joins its neighbour
FRONT_PAD_M = 1.0       # profile reach in front of the way (the street end)
REAR_PAD_M = 12.0       # profile reach behind the way (a bin store, a rear extension)
ROUGH_MAX_M = 0.45      # lidar roughness above this is degraded (canopy, clutter)
MIN_ROUGH_COLUMNS = 3
MIN_WINDOW = 6
GREEN_EXG = 0.08        # excess green (2g - r - b, 0..1) above this is leaf
GREEN_MAX = 0.25        # leaf share above this degrades the image over a volume
SHADOW_RATIO = 0.6      # mean luminance below this share of the local roofs is deep shadow
FLAT_SPREAD_M = 1.5     # a flat volume whose lidar spreads more than this is not read as a roof
ROW_AGREE_M = 1.0       # the two neighbours must agree this well to speak
ROW_MATCH_M = 1.5       # a volume's top within this of the row agrees with it
MIN_REGION_COLUMNS = 3  # a proposed zone region needs this many footprint columns with lidar mass
FORMS = ('flat', 'gable_across', 'gable_along', 'mono_along', 'mono_across', 'hip')
FRONTS = ('bay', 'dormer', 'none')
PITCHED = frozenset(FORMS) - {'flat'}
# Map ``roof:shape`` tags to the form family they name (gable, hip, mono, flat).
TAG_FORMS = {'flat': 'flat', 'gabled': 'gable', 'hipped': 'hip', 'half-hipped': 'hip', 'pyramidal': 'hip',
             'skillion': 'mono', 'lean_to': 'mono'}
# A roof type from the measured aerial colour class, when the model is unsure of the type.
MEASURED_TYPES = {'pitched': {'warm': 'red_clay_tile', 'grey': 'grey_tile', 'green': 'weathered_copper'},
                  'flat': {'warm': 'flat_deck', 'grey': 'flat_deck', 'green': 'flat_deck'}}
# The profile kind's form, used when no clean surface fits (a noisy or hipped roof).
PROFILE_FORMS = {'flat': 'flat', 'ridge_across': 'gable_across', 'slope_along': 'mono_along',
                 'cross': 'mono_across', 'hip': 'hip'}
CROP_M_PER_PX = 0.25
CROP_SCALE = 4


# ---------------------------------------------------------------- plot frame


@dataclass(frozen=True)
class PlotFrame:
    """Plot coordinates: ``s`` along the long axis (street end negative), ``t`` across it.

    ``t`` is to the right when looking from the street to the rear, so a crop drawn rear-up keeps the map's
    handedness. ``s_min``.. ``t_max`` bound the footprint's column centres.
    """
    cx: float
    cz: float
    ux: float
    uz: float
    s_min: float
    s_max: float
    t_min: float
    t_max: float
    street: bool = True

    @property
    def vx(self) -> float:
        return -self.uz

    @property
    def vz(self) -> float:
        return self.ux

    def world(self, s, t):
        """World ``(x, z)`` of plot points (scalars or arrays)."""
        s = np.asarray(s, dtype=float)
        t = np.asarray(t, dtype=float)
        return self.cx + s * self.ux + t * self.vx, self.cz + s * self.uz + t * self.vz

    def plot(self, x, z):
        """Plot ``(s, t)`` of world points (scalars or arrays)."""
        dx = np.asarray(x, dtype=float) - self.cx
        dz = np.asarray(z, dtype=float) - self.cz
        return dx * self.ux + dz * self.uz, dx * self.vx + dz * self.vz

    def polygon(self, s0: float, s1: float, t0: float, t1: float) -> list[list[float]]:
        """A plot-frame rectangle as an MC ``[[x, z], ...]`` polygon (two decimals)."""
        out = []
        for s, t in ((s0, t0), (s1, t0), (s1, t1), (s0, t1)):
            x, z = self.world(s, t)
            out.append([round(float(x), 2), round(float(z), 2)])
        return out


def plot_frame(cells, street_point=None) -> PlotFrame:
    """The plot frame of a footprint: long axis by PCA, pointing away from the street.

    @param cells: footprint columns ``(x, z)``
    @param street_point: ``(x, z)`` on the street the building faces, or None (front end then unknown)
    @returns: the frame, centred on the footprint's column centres
    """
    pts = np.array([(x + 0.5, z + 0.5) for x, z in cells], dtype=float)
    centre = pts.mean(axis=0)
    rel = pts - centre
    if len(pts) >= 2:
        _vals, vecs = np.linalg.eigh(rel.T @ rel)
        ux, uz = vecs[:, -1]
    else:
        ux, uz = 1.0, 0.0
    if street_point is not None and (street_point[0] - centre[0]) * ux + (street_point[1] - centre[1]) * uz > 0:
        ux, uz = -ux, -uz
    s = rel @ np.array([ux, uz])
    t = rel @ np.array([-uz, ux])
    return PlotFrame(float(centre[0]), float(centre[1]), float(ux), float(uz),
                     float(s.min()), float(s.max()), float(t.min()), float(t.max()), street_point is not None)


# ---------------------------------------------------------------- evidence in the plot frame


@dataclass
class Evidence:
    """Everything the read uses, already in the plot frame (tests build this directly).

    @ivar s, t: profile row and column positions; ``h[i, j]`` is lidar rise above ground (NaN = no data)
    @ivar band: boolean over ``t``: the plot's own columns
    @ivar columns: native lidar columns ``(x, z) -> (s, t, rise)``
    @ivar img, img_s, img_t: aerial crop RGB 0..1 (NaN = none), its row ``s`` and column ``t`` positions
    """
    frame: PlotFrame
    s: np.ndarray
    t: np.ndarray
    h: np.ndarray
    band: np.ndarray
    columns: dict = field(default_factory=dict)
    img: np.ndarray | None = None
    img_s: np.ndarray | None = None
    img_t: np.ndarray | None = None


def profile_axes(frame: PlotFrame):
    """Row ``s`` and column ``t`` positions of the profile, and the plot band over ``t``."""
    s = np.arange(frame.s_min - FRONT_PAD_M, frame.s_max + REAR_PAD_M + 1e-9, STEP_S)
    width = max(3, int(round(frame.t_max - frame.t_min)) + 1)
    t = np.arange(math.floor(frame.t_min) - width, math.ceil(frame.t_max) + width + 1, 1.0)
    band = (t >= frame.t_min - 0.01) & (t <= frame.t_max + 0.01)
    return s, t, band


# ---------------------------------------------------------------- segmentation


@dataclass
class Volume:
    """One roof volume: rows ``i0..i1`` of the profile, ``s0..s1`` its extent along the plot."""
    id: int
    kind: str
    i0: int
    i1: int
    s0: float
    s1: float
    in_way: float = 0.0          # share of its length inside the way's extent
    form: str | None = None      # lidar form: the surface fit, else the profile kind (``form_basis``)
    form_basis: str | None = None
    fit_rms: float | None = None
    eave_m: float | None = None
    top_m: float | None = None
    rough_m: float | None = None
    rough_n: int = 0
    green: float | None = None
    lum_ratio: float | None = None
    rgb: tuple | None = None
    colour_class: str | None = None
    row: dict | None = None

    @property
    def beyond(self) -> bool:
        """True when most of the volume lies beyond the map way."""
        return self.in_way < 0.5


def row_levels(h: np.ndarray, cols: np.ndarray) -> np.ndarray:
    """Per row: median of the columns standing ``MASS_M`` or more up, NaN when fewer than two do."""
    out = np.full(h.shape[0], np.nan)
    for i in range(h.shape[0]):
        vals = h[i, cols]
        mass = vals[np.isfinite(vals) & (vals >= MASS_M)]
        if len(mass) >= 2:
            out[i] = float(np.median(mass))
    return out


def cross_slopes(h: np.ndarray, t: np.ndarray, cols: np.ndarray) -> np.ndarray:
    """Per row: least-squares slope of height across the band's mass columns, metres per metre.

    Fewer than four mass columns (a narrow store beside open ground) cannot show a cross slope: NaN.
    """
    tt = t[cols]
    out = np.full(h.shape[0], np.nan)
    for i in range(h.shape[0]):
        vals = h[i, cols]
        good = np.isfinite(vals) & (vals >= MASS_M)
        if good.sum() >= 4 and np.ptp(tt[good]) > 0:
            out[i] = float(np.polyfit(tt[good], vals[good], 1)[0])
    return out


def classify_rows(ev: Evidence) -> list[str]:
    """Row classes: ``none``, ``flat``, ``step`` (steeper than ``STEEP`` along the plot: the wall between
    two volumes, blurred by the lidar), ``cross`` (falls across the plot) or ``up`` / ``down`` (a roof slope
    along it). A row falling across the plot is ``cross`` whatever its level does along the plot short of
    a wall step: its level is the median of its mass columns, which shifts as the low side drops in and
    out of mass.
    """
    level = row_levels(ev.h, ev.band)
    cross = cross_slopes(ev.h, ev.t, ev.band)
    filled = np.where(np.isfinite(level), level, 0.0)
    grad = np.gradient(filled, STEP_S) if len(filled) > 1 else np.zeros_like(filled)
    out = []
    for i, lev in enumerate(level):
        if not np.isfinite(lev):
            out.append('none')
            continue
        both = (i == 0 or np.isfinite(level[i - 1])) and (i + 1 == len(level) or np.isfinite(level[i + 1]))
        if both and abs(grad[i]) >= STEEP:
            out.append('step')
        elif np.isfinite(cross[i]) and abs(cross[i]) >= CROSS:
            out.append('cross')
        elif both and abs(grad[i]) >= RAMP:
            out.append('up' if grad[i] > 0 else 'down')
        else:
            out.append('flat')
    return out


def _runs(classes: list[str]) -> list[list]:
    runs: list[list] = []
    for i, k in enumerate(classes):
        if runs and runs[-1][0] == k:
            runs[-1][2] = i
        else:
            runs.append([k, i, i])
    return runs


def _length(run) -> float:
    return (run[2] - run[1] + 1) * STEP_S


def _merge_same(runs):
    out = []
    for run in runs:
        if out and out[-1][0] == run[0]:
            out[-1][2] = run[2]
        else:
            out.append(list(run))
    return out


def _absorb_short(runs, level, *, into_steps: bool):
    """Runs shorter than ``MIN_RUN_M`` join the mass neighbour nearest their level.

    With ``into_steps`` False a wall step is neither absorbed nor absorbs, so a narrow flat front
    before a step survives to take its half of the step.
    """
    changed = True
    while changed:
        changed = False
        for k, run in enumerate(runs):
            if run[0] in ('none', 'step') or _length(run) >= MIN_RUN_M:
                continue
            nbrs = [j for j in (k - 1, k + 1) if 0 <= j < len(runs) and runs[j][0] != 'none'
                    and (into_steps or runs[j][0] != 'step')]
            if not nbrs:
                continue
            here = np.nanmean(level[run[1]:run[2] + 1])
            j = min(nbrs, key=lambda n: abs(np.nanmean(level[runs[n][1]:runs[n][2] + 1]) - here))
            runs[j][1], runs[j][2] = min(runs[j][1], run[1]), max(runs[j][2], run[2])
            del runs[k]
            runs = _merge_same(runs)
            changed = True
            break
    return runs


def segment(ev: Evidence) -> list[Volume]:
    """Volumes along the plot from the row classes (see the module docstring).

    @param ev: evidence in the plot frame
    @returns: volumes ordered from the street end, ids from 1
    """
    runs = _runs(classify_rows(ev))
    level = row_levels(ev.h, ev.band)
    # 1. a short run beside a roof joins it; then a wall step is shared between the volumes either side
    #    of it (all of it to the only one); then any short run left joins its neighbour
    runs = _absorb_short(runs, level, into_steps=False)
    out = []
    for k, run in enumerate(runs):
        if run[0] != 'step':
            out.append(list(run))
            continue
        prev = out[-1] if out and out[-1][0] not in ('none', 'step') else None
        nxt = runs[k + 1] if k + 1 < len(runs) and runs[k + 1][0] not in ('none', 'step') else None
        if prev is None and nxt is None:
            out.append(['flat', run[1], run[2]])
            continue
        mid = (run[1] + run[2]) // 2 if prev is not None and nxt is not None else (
            run[2] if nxt is None else run[1] - 1)
        if prev is not None:
            prev[2] = mid
        if nxt is not None:
            nxt[1] = mid + 1
    runs = _absorb_short(_merge_same([r for r in out if r[2] >= r[1]]), level, into_steps=True)
    # 2. up then down is one roof: a ridge across the plot when the top between them is short, a hip when
    #    a longer top (a ridge along the plot) joins them at the ramps' height
    out = []
    k = 0
    while k < len(runs):
        run = runs[k]
        if run[0] == 'up':
            if k + 1 < len(runs) and runs[k + 1][0] == 'down':
                out.append(['ridge_across', run[1], runs[k + 1][2]])
                k += 2
                continue
            if k + 2 < len(runs) and runs[k + 1][0] == 'flat' and runs[k + 2][0] == 'down':
                top = runs[k + 1]
                if _length(top) <= STEP_RUN_M:
                    out.append(['ridge_across', run[1], runs[k + 2][2]])
                    k += 3
                    continue
                joins = (abs(level[run[2]] - level[top[1]]), abs(level[top[2]] - level[runs[k + 2][1]]))
                if all(j <= HIP_JOIN_M for j in joins):      # no wall step: the slopes reach the top
                    out.append(['hip', run[1], runs[k + 2][2]])
                    k += 3
                    continue
        out.append(list(run))
        k += 1
    # 3. a short ramp between two volumes is the wall step: split it between them
    runs = out
    out = []
    for k, run in enumerate(runs):
        if run[0] in ('up', 'down') and _length(run) <= STEP_RUN_M:
            prev = out[-1] if out and out[-1][0] != 'none' and out[-1][2] == run[1] - 1 else None
            nxt = runs[k + 1] if k + 1 < len(runs) and runs[k + 1][0] != 'none' else None
            if prev is not None or nxt is not None:
                mid = (run[1] + run[2]) // 2 if prev is not None and nxt is not None else (
                    run[2] if nxt is None else run[1] - 1)
                if prev is not None:
                    prev[2] = mid
                if nxt is not None:
                    nxt[1] = mid + 1
                continue
        out.append(list(run))
    # 4. any other ramp is a slope along the plot
    vols = []
    for kind, i0, i1 in out:
        if kind == 'none' or i1 < i0:
            continue
        kind = {'up': 'slope_along', 'down': 'slope_along'}.get(kind, kind)
        vols.append(Volume(0, kind, i0, i1, float(ev.s[i0] - STEP_S / 2), float(ev.s[i1] + STEP_S / 2)))
    return _attached(vols, ev.frame)


def _attached(vols: list[Volume], frame: PlotFrame) -> list[Volume]:
    """Keep volumes on the way and those joined to them by mass; number them from the street end."""
    lo, hi = frame.s_min - 0.5, frame.s_max + 0.5
    for vol in vols:
        overlap = max(0.0, min(vol.s1, hi) - max(vol.s0, lo))
        vol.in_way = round(overlap / max(vol.s1 - vol.s0, 1e-9), 2)
    keep = []
    for vol in vols:
        if vol.in_way > 0:
            keep.append(vol)
        elif vol.s0 >= hi and keep and abs(keep[-1].s1 - vol.s0) < 1e-6:
            keep.append(vol)          # behind the way, touching the last kept volume
    for n, vol in enumerate(keep, start=1):
        vol.id = n
    return keep


# ---------------------------------------------------------------- per-volume measurements


def _volume_cells(ev: Evidence, vol: Volume, cols: np.ndarray, mass_only: bool = True, skip=()):
    s, t, h = [], [], []
    for i in range(vol.i0, vol.i1 + 1):
        if i in skip:
            continue
        for j in np.where(cols)[0]:
            val = ev.h[i, j]
            if np.isfinite(val) and (val >= MASS_M or not mass_only):
                s.append(ev.s[i])
                t.append(ev.t[j])
                h.append(val)
    return np.array(s), np.array(t), np.array(h)


def form_of(surface) -> str | None:
    """Plot-frame form name of a fitted surface (fitted with ``s`` as x, ``t`` as z, axis 0)."""
    if surface is None:
        return None
    if surface.kind == 'flat':
        return 'flat'
    if surface.kind == 'plane':
        _a, bu, bv = surface.params
        return 'mono_along' if abs(bu) >= abs(bv) else 'mono_across'
    return 'gable_across' if surface.ridge_across else 'gable_along'


def heights(s, t, h):
    """``(form, rms, eave_m, top_m)`` of one set of plot cells: the fitted surface's low and high, or percentiles."""
    if len(h) == 0:
        return None, None, None, None
    surface = roof_surface.fit_surface(s, t, h, 0.0)
    if surface is not None:
        fitted = np.asarray(surface.height(s, t), dtype=float)
        kept = np.abs(h - fitted) <= roof_surface.TRIM_M      # the cells the fit explains, not a wall step
        fitted = fitted[kept] if kept.any() else fitted
        return form_of(surface), round(float(surface.rms), 2), float(fitted.min()), float(fitted.max())
    inner = _inner(s, t)
    low = h[inner] if inner.sum() >= roof_surface.MIN_POINTS else h
    return None, None, float(np.percentile(low, 15)), float(np.percentile(h, 95))


def _inner(s, t) -> np.ndarray:
    """Cells a metre or more inside the mass edge (neighbours along and across present): the eave is read
    there, because lidar blurs the wall top into the ground at the edge."""
    have = {(round(a * 2), round(b)) for a, b in zip(s, t)}
    return np.array([all(n in have for n in ((round(a * 2) + 2, round(b)), (round(a * 2) - 2, round(b)),
                                             (round(a * 2), round(b) + 1), (round(a * 2), round(b) - 1)))
                     for a, b in zip(s, t)], dtype=bool)


def colour_class(rgb) -> str | None:
    """``warm`` (red or brown), ``green`` or ``grey`` from a mean RGB 0..1."""
    if rgb is None or not all(np.isfinite(rgb)):
        return None
    hue, _lum, sat = colorsys.rgb_to_hls(*rgb)
    hsv_sat = colorsys.rgb_to_hsv(*rgb)[1]
    hue *= 360
    if hsv_sat >= 0.18 and (hue <= 45 or hue >= 330):
        return 'warm'
    if hsv_sat >= 0.15 and 70 <= hue <= 170:
        return 'green'
    return 'grey'


def _pixels(ev: Evidence, s0: float, s1: float, t0: float, t1: float) -> np.ndarray:
    if ev.img is None:
        return np.zeros((0, 3))
    rows = (ev.img_s >= s0) & (ev.img_s < s1)
    cols = (ev.img_t >= t0) & (ev.img_t <= t1)
    block = ev.img[np.ix_(rows, cols)].reshape(-1, 3)
    return block[np.all(np.isfinite(block), axis=1)]


def roof_luminance(ev: Evidence) -> float | None:
    """Median luminance of crop pixels over lidar mass: the local roofs, the shadow reference."""
    if ev.img is None:
        return None
    vals = []
    for i, s in enumerate(ev.s):
        for j, t in enumerate(ev.t):
            if np.isfinite(ev.h[i, j]) and ev.h[i, j] >= MASS_M:
                px = _pixels(ev, s - STEP_S / 2, s + STEP_S / 2, t - 0.5, t + 0.5)
                if len(px):
                    vals.append(float(px.mean()))
    return float(np.median(vals)) if vals else None


def roughness(ev: Evidence, vol: Volume) -> tuple[float | None, int]:
    """Mean lidar residual against the 3x3 median, over the volume's own mass columns.

    The median of a column's window counts only columns of this volume with mass, so a wall step, the
    eave drop or a neighbour never reads as roughness. A column needs ``MIN_WINDOW`` such columns in its
    window (an edge column of a strip two deep has six). Fewer than ``MIN_ROUGH_COLUMNS`` columns
    measured gives None.
    """
    t0, t1 = ev.frame.t_min - 0.5, ev.frame.t_max + 0.5
    mine = {c: rise for c, (s, t, rise) in ev.columns.items()
            if vol.s0 <= s < vol.s1 and t0 <= t <= t1 and np.isfinite(rise) and rise >= MASS_M}
    vals = []
    for (x, z), rise in mine.items():
        window = [mine[(x + a, z + b)] for a in (-1, 0, 1) for b in (-1, 0, 1) if (x + a, z + b) in mine]
        if len(window) >= MIN_WINDOW:
            vals.append(abs(rise - float(np.median(window))))
    if len(vals) < MIN_ROUGH_COLUMNS:
        return None, len(vals)
    return round(float(np.mean(vals)), 3), len(vals)


def row_read(ev: Evidence, vol: Volume) -> dict | None:
    """The row neighbours at the same position: both side bands read the same way.

    @returns: ``{eave_m, top_m, form, colour_class, sides}`` when both sides have mass over the volume's rows
        and their tops agree within ``ROW_AGREE_M``; ``{'silent': why}`` otherwise
    """
    width = max(3.0, ev.frame.t_max - ev.frame.t_min + 1)
    sides = {
        'left': (ev.t < ev.frame.t_min - 0.5) & (ev.t >= ev.frame.t_min - 0.5 - width),
        'right': (ev.t > ev.frame.t_max + 0.5) & (ev.t <= ev.frame.t_max + 0.5 + width),
    }
    reads = {}
    for name, cols in sides.items():
        s, t, h = _volume_cells(ev, vol, cols)
        total = (vol.i1 - vol.i0 + 1) * int(cols.sum())
        if total == 0 or len(h) < 0.5 * total:
            reads[name] = None
            continue
        form, _rms, eave, top = heights(s, t, h)
        rgb = _pixels(ev, vol.s0, vol.s1, float(ev.t[cols].min()) - 0.5, float(ev.t[cols].max()) + 0.5)
        reads[name] = {'eave_m': eave, 'top_m': top, 'form': form,
                       'colour_class': colour_class(tuple(rgb.mean(0))) if len(rgb) else None}
    left, right = reads['left'], reads['right']
    if left is None or right is None:
        return {'silent': 'no mass on ' + ' and '.join(k for k, v in reads.items() if v is None)}
    if abs(left['top_m'] - right['top_m']) > ROW_AGREE_M:
        return {'silent': f"neighbours differ (top {left['top_m']:.1f} vs {right['top_m']:.1f} m)"}
    return {
        'eave_m': round((left['eave_m'] + right['eave_m']) / 2, 2),
        'top_m': round((left['top_m'] + right['top_m']) / 2, 2),
        'form': left['form'] if left['form'] == right['form'] else None,
        'colour_class': left['colour_class'] if left['colour_class'] == right['colour_class'] else None,
    }


def measure(ev: Evidence, vols: list[Volume]) -> list[Volume]:
    """Fill each volume's lidar form and heights, roughness, image flags and row read."""
    ref = roof_luminance(ev)
    classes = classify_rows(ev)
    steps = {i for i, k in enumerate(classes) if k == 'step'}
    for vol in vols:
        s, t, h = _volume_cells(ev, vol, ev.band, skip=steps)      # the wall step blurs the roof's own shape
        if len(h) == 0:
            s, t, h = _volume_cells(ev, vol, ev.band)
        if len(h) >= roof_surface.MIN_POINTS:
            vol.form, vol.fit_rms, eave, top = heights(s, t, h)
        else:                                                    # too few cells to fit: the profile speaks
            vol.form, vol.fit_rms, eave, top = None, None, float(np.percentile(h, 15)), float(np.percentile(h, 95))
        vol.form_basis = 'fit'
        if vol.form is None:
            vol.form, vol.form_basis = PROFILE_FORMS[vol.kind], 'profile'
        vol.eave_m = round(eave, 2) if eave is not None else None
        vol.top_m = round(top, 2) if top is not None else None
        vol.rough_m, vol.rough_n = roughness(ev, vol)
        px = _pixels(ev, vol.s0, vol.s1, ev.frame.t_min - 0.5, ev.frame.t_max + 0.5)
        if len(px):
            exg = 2 * px[:, 1] - px[:, 0] - px[:, 2]
            vol.green = round(float((exg > GREEN_EXG).mean()), 3)
            vol.rgb = tuple(round(float(c), 3) for c in px.mean(0))
            vol.colour_class = colour_class(vol.rgb)
            if ref:
                vol.lum_ratio = round(float(px.mean()) / ref, 2)
        vol.row = row_read(ev, vol)
    return vols


# ---------------------------------------------------------------- the designer decision


def roof_types() -> list[str]:
    """Roof type names the model may choose from (the catalogue's ``types``)."""
    from roof_material import roof_type_names
    return roof_type_names()


def prompt(vols: list[Volume], types: list[str]) -> str:
    """The one question per building: short, with the lidar table and the JSON answer shape."""
    lines = [
        'Aerial photo of one building plot, rotated: the street is at the BOTTOM, the rear at the TOP. '
        'The plot lies between the two white vertical lines. Yellow lines split it into volumes '
        'numbered from the street. Lidar per volume (metres above ground):',
        'id | depth m | lidar shape | eave | top',
    ]
    for vol in vols:
        shape = vol.form + (' (from the profile, no clean fit)' if vol.form_basis == 'profile' else '')
        where = ' (beyond the mapped outline)' if vol.beyond else ''
        eave = f'{vol.eave_m:.1f}' if vol.eave_m is not None else '?'
        top = f'{vol.top_m:.1f}' if vol.top_m is not None else '?'
        lines.append(f'{vol.id} | {vol.s1 - vol.s0:.1f} | {shape}{where} | {eave} | {top}')
    lines += [
        'Shapes: gable_across = ridge parallel to the street; gable_along = ridge running front to back; '
        'mono_along / mono_across = one slope falling toward the rear or front / to one side.',
        'For each volume from the IMAGE: form (' + ', '.join(FORMS) + ', unsure), roof type (one of '
        + ', '.join(types) + ', or unsure), hidden (true when trees or deep shadow hide it), storeys '
        '(only if the image shows them, else null). Also the front of the first pitched volume nearest the '
        'street: bay, dormer, none or unsure.',
        'Answer JSON only: {"volumes": [{"id": 1, "form": "...", "roof": "...", "hidden": false, '
        '"storeys": null}], "front": "...", "note": "<= 20 words"}',
    ]
    return '\n'.join(lines)


def parse_answer(text: str, vols: list[Volume], types: list[str]) -> dict:
    """The model's answer as ``{volumes: {id: {...}}, front, note}``; unknown values become ``unsure``.

    @raises ValueError: when the text holds no JSON object
    """
    match = re.search(r'\{.*\}', text or '', re.S)
    if not match:
        raise ValueError('answer holds no JSON object')
    raw = json.loads(match.group(0))
    ids = {vol.id for vol in vols}
    out = {}
    for row in raw.get('volumes') or []:
        if not isinstance(row, dict) or row.get('id') not in ids:
            continue
        storeys = row.get('storeys')
        out[row['id']] = {
            'form': row.get('form') if row.get('form') in FORMS else 'unsure',
            'roof': row.get('roof') if row.get('roof') in types else 'unsure',
            'hidden': bool(row.get('hidden')),
            'storeys': storeys if isinstance(storeys, int) and not isinstance(storeys, bool)
            and 1 <= storeys <= terrace_body.MAX_STOREYS else None,
        }
    front = raw.get('front') if raw.get('front') in FRONTS else 'unsure'
    return {'volumes': out, 'front': front, 'note': str(raw.get('note') or '')[:200]}


# ---------------------------------------------------------------- trust


def _status(reading, value, same) -> str:
    if reading is None:
        return 'silent'
    return 'agree' if same(reading, value) else 'disagree'


def _type_class(type_name: str | None) -> str | None:
    if not type_name or type_name == 'unsure':
        return None
    from roof_material import load_catalogue, roof_type_blocks
    family = roof_type_blocks(type_name)['family']
    rgb = load_catalogue()['families'][family]['colour']
    return colour_class(tuple(c / 255 for c in rgb))


def trust(vol: Volume, label: dict | None, *, defaults: dict, tag_levels: int | None = None,
          tag_shape: str | None = None, main: bool = False, front_label: str | None = None,
          body: dict | None = None) -> dict:
    """Per-attribute choice for one volume and each source's status against it.

    @param vol: a measured volume
    @param label: the model's answer for this volume, or None (not asked)
    @param defaults: class defaults ``{form, roof: {flat, pitched}, storeys}`` (roof types)
    @param tag_levels: ``building:levels`` (checked on the main volume only)
    @param tag_shape: ``roof:shape`` (checked on the main volume only, by form family)
    @param main: True for the building's main volume (tallest inside the way)
    @param front_label: the model's front answer, used on the front volume
    @param body: the class's terrace-body recipe for the front rule (``attic_rise_m``), or None
    @returns: ``{attribute: {value, rests_on, lidar, image, row, ..., why}}``
    """
    why = []
    lidar_ok = vol.top_m is not None and (vol.rough_m is None or vol.rough_m <= ROUGH_MAX_M)
    if vol.rough_m is not None and vol.rough_m > ROUGH_MAX_M:
        why.append(f'lidar rough {vol.rough_m:.2f} m')
    uneven = vol.form == 'flat' and vol.top_m is not None and vol.top_m - vol.eave_m > FLAT_SPREAD_M
    if uneven:                    # too small to test roughness, and no flat roof spreads this much
        lidar_ok = False
        why.append(f'lidar uneven for a flat ({vol.eave_m:.1f} to {vol.top_m:.1f} m)')
    leafy = vol.green is not None and vol.green > GREEN_MAX
    dark = vol.lum_ratio is not None and vol.lum_ratio < SHADOW_RATIO
    hidden = bool(label and label.get('hidden'))
    image_ok = vol.green is not None and not leafy and not dark and not hidden
    if leafy:
        why.append(f'image green {vol.green:.0%}')
    if dark:
        why.append(f'image shadow {vol.lum_ratio:.2f} of local roofs')
    if hidden:
        why.append('model: hidden')
    if vol.green is None:
        why.append('no aerial')
    row = vol.row if vol.row and 'silent' not in vol.row else None
    if row is None and vol.row:
        why.append('row silent: ' + vol.row['silent'])
    near = lambda a, b: abs(a - b) <= ROW_MATCH_M  # noqa: E731
    # canopy suspicion: a smooth crown passes the roughness test, but not green plus a row disagreement
    if lidar_ok and leafy and row is not None and not near(vol.top_m, row['top_m']):
        lidar_ok = False
        why.append('lidar suspect: green over the volume and the row disagrees')
    out = {'flags': why}

    # height: lidar, else the row, else the class default
    lidar_h = vol.top_m if lidar_ok else None
    row_h = row['top_m'] if row else None
    if lidar_h is not None:
        top, eave, rests = vol.top_m, vol.eave_m, 'lidar'
    elif row_h is not None:
        top, eave, rests = row['top_m'], row['eave_m'], 'row'
    else:
        top, eave, rests = None, None, 'class default'
    out['height'] = {
        'value': {'eave_m': eave, 'top_m': top}, 'rests_on': rests,
        'lidar': _status(lidar_h, top, near) if top is not None else 'silent',
        'row': _status(row_h, top, near) if top is not None else 'silent',
        'image': 'silent',
    }

    # form: lidar fit, else the image, else the row, else the class default
    lidar_f = vol.form if lidar_ok else None
    image_f = label['form'] if image_ok and label and label['form'] != 'unsure' else None
    row_f = row['form'] if row else None
    form, rests = next(((f, src) for f, src in ((lidar_f, 'lidar'), (image_f, 'image'), (row_f, 'row'))
                        if f is not None), (defaults['form'], 'class default'))
    same = lambda a, b: a == b  # noqa: E731
    out['form'] = {'value': form, 'rests_on': rests, 'lidar': _status(lidar_f, form, same),
                   'image': _status(image_f, form, same), 'row': _status(row_f, form, same)}
    tag_f = TAG_FORMS.get(str(tag_shape or '').strip()) if main else None
    out['form']['tag'] = _status(tag_f, form.split('_')[0], same)     # roof:shape names the family only

    # colour: the image (the model's roof type, checked against the measured colour; the measured colour
    # alone when the model is unsure), else the class default for the chosen form
    image_c = label['roof'] if image_ok and label and label['roof'] != 'unsure' else None
    slope = 'pitched' if form in PITCHED else 'flat'
    by_colour = MEASURED_TYPES[slope].get(vol.colour_class) if image_ok else None
    if image_c:
        roof, rests = image_c, 'image'
    elif by_colour:
        roof, rests = by_colour, 'image (measured colour)'
    else:
        roof, rests = defaults['roof'][slope], 'class default'
    want = _type_class(roof)
    measured = vol.colour_class if image_ok else None
    row_c = row['colour_class'] if row else None
    out['colour'] = {
        'value': roof, 'rests_on': rests, 'lidar': 'silent',
        'image': _status(image_c, roof, same),
        'measured': _status(measured, want, same) if want else 'silent',
        'row': _status(row_c, want, same) if want else 'silent',
        'aerial_rgb': vol.rgb,
    }

    # storeys: from the chosen eave (terrace-body rule), else a tag on the main volume, else the class default
    by_eave = lambda e: terrace_body.storeys_from_eave(e) if e is not None else None  # noqa: E731
    lidar_s = by_eave(vol.eave_m) if lidar_ok else None
    row_s = by_eave(row['eave_m']) if row else None
    tag_s = tag_levels if main else None
    image_s = label.get('storeys') if image_ok and label else None
    storeys, rests = next(((v, src) for v, src in ((by_eave(eave), out['height']['rests_on']), (tag_s, 'tag'))
                           if v is not None), (defaults['storeys'], 'class default'))
    out['storeys'] = {'value': storeys, 'rests_on': rests, 'lidar': _status(lidar_s, storeys, same),
                      'row': _status(row_s, storeys, same), 'tag': _status(tag_s, storeys, same),
                      'image': _status(image_s, storeys, same)}

    if front_label is not None:
        params = {**terrace_body.DEFAULTS, **(body or {})}
        rule = None
        if eave is not None and top is not None and form in PITCHED:
            rule = 'dormer' if top - eave >= params['attic_rise_m'] else 'bay' if storeys >= 2 else 'none'
        image_fr = front_label if image_ok and front_label in FRONTS else None
        value, rests = next(((v, src) for v, src in ((image_fr, 'image'), (rule, 'rule')) if v is not None),
                            ('none', 'class default'))
        out['front'] = {'value': value, 'rests_on': rests, 'image': _status(image_fr, value, same),
                        'rule': _status(rule, value, same), 'lidar': 'silent'}
    for key in ('height', 'form', 'colour', 'storeys', 'front'):
        row_ = out.get(key)
        if row_ is None:
            continue
        agreeing = sorted({'image' if src == 'measured' else src for src, v in row_.items() if v == 'agree'})
        row_['support'] = len(agreeing)
        if row_['rests_on'] == 'class default':
            why.append(f'{key} unread: class default')
        elif len(agreeing) == 1:
            why.append(f'{key} rests on {agreeing[0]} alone')
    return out


# ---------------------------------------------------------------- the read and the proposal


def read(ev: Evidence, answer: dict | None, *, defaults: dict, tags: dict | None = None,
         body: dict | None = None) -> dict:
    """Segment, measure and check one building; the model's ``answer`` (parsed) may be None.

    @returns: ``{frame, volumes: [...], main, front_volume}`` (JSON-ready)
    """
    vols = measure(ev, segment(ev))
    labels = (answer or {}).get('volumes', {})
    inside = [v for v in vols if not v.beyond]
    main = max(inside, key=lambda v: (v.top_m or 0, v.s1 - v.s0)) if inside else None
    levels = None
    try:
        levels = int(str((tags or {}).get('building:levels', '')).strip())
    except ValueError:
        pass
    shape = (tags or {}).get('roof:shape')
    rows = []
    front_vol = None
    for vol in vols:
        label = labels.get(vol.id)
        record = trust(vol, label, defaults=defaults, tag_levels=levels, tag_shape=shape, main=vol is main,
                       body=body)
        if front_vol is None and not vol.beyond and record['form']['value'] in PITCHED:
            front_vol = vol.id
            record = trust(vol, label, defaults=defaults, tag_levels=levels, tag_shape=shape, main=vol is main,
                           body=body, front_label=(answer or {}).get('front', 'unsure'))
        data = asdict(vol)
        data.pop('row')
        rows.append({**data, 'beyond': vol.beyond, 'row': vol.row, 'label': label, 'trust': record})
    return {'frame': asdict(ev.frame), 'volumes': rows, 'main': main.id if main else None,
            'front_volume': front_vol}


def _family(type_name: str) -> str:
    from roof_material import roof_type_blocks
    return roof_type_blocks(type_name)['family']


def proposal(result: dict, *, name: str, way_id: str, front: str | None, wall: str,
             mass_cells: set | None = None) -> dict:
    """A ``feature_build`` spec proposal from a read (one label ``a``).

    @param result: :func:`read` output
    @param name: spec name (a feature id planned in the building's cell when it is to be built)
    @param way_id: the map way
    @param front: cardinal face onto the street, or None
    @param wall: wall material
    @param mass_cells: footprint columns with lidar mass; a zone region over fewer than
        ``MIN_REGION_COLUMNS`` of them joins its neighbour (the builder refuses an empty region)
    @returns: the spec dict
    """
    frame = PlotFrame(**result['frame'])
    vols = result['volumes']
    inside = [v for v in vols if not v['beyond']]
    beyond = [v for v in vols if v['beyond']]
    main = next((v for v in vols if v['id'] == result['main']), None)
    if main is None:
        raise ValueError('no volume inside the map way')
    forms = [v['trust']['form']['value'] for v in inside]
    pitched = any(f in PITCHED for f in forms)
    roof = main['trust']['colour']['value']
    material = _family(roof)
    roofs = {'kind': 'pitched' if pitched else 'flat', 'material': material}
    spec = {
        'name': name,
        'ways': {str(way_id): 'a'},
        'storeys': {'a': main['trust']['storeys']['value']},
        'roofs': {'a': roofs},
        'groups': [{'nos': ['a'], 'mode': 'each', **({'front': front} if front else {})}],
        'wall': wall,
    }
    t0, t1 = frame.t_min - 1.0, frame.t_max + 1.0
    if pitched:
        spans = []
        for k, vol in enumerate(inside):
            s0 = frame.s_min - 1.0 if k == 0 else vol['s0']
            s1 = max(vol['s1'], frame.s_max + 1.0) if k == len(inside) - 1 else vol['s1']
            spans.append([s0, s1, {vol['trust']['form']['value']}])
        spans = _joined_spans(spans, frame, mass_cells)
        regions = [frame.polygon(s0, s1, t0, t1) for s0, s1, _forms in spans]
        spec['zones'] = {'a': {'from': 'lidar', 'levels': 'auto', 'bucket': 4, 'axis_deg': 'auto',
                               'regions': regions, 'surface': 'fit'}}
        roofs['stairs'] = 'slope'
        if any(f == {'flat'} for _s0, _s1, f in spans):    # a flat volume that kept its own region
            roofs['finish'] = 'carpet'
    extra = []
    for vol in beyond:
        tr = vol['trust']
        if tr['height']['rests_on'] != 'lidar':
            continue          # a volume beyond the way is only proposed when lidar confirms its mass
        colour = tr['colour']['value']
        kind = 'pitched' if tr['form']['value'] in PITCHED else 'flat'
        extra.append({
            'polygon': frame.polygon(vol['s0'], vol['s1'], t0, t1),
            'kind': kind, 'storeys': tr['storeys']['value'],
            'material': _family(colour) if kind == 'pitched' else material,
            'source': f"read: lidar {tr['form']['value']} {vol['top_m']} m, height rests on "
                      f"{tr['height']['rests_on']}",
        })
    if extra:
        spec['volumes'] = {'a': extra}
    spec['notes'] = note(result)
    return spec


def _joined_spans(spans: list, frame: PlotFrame, mass_cells: set | None) -> list:
    """Region spans ``[s0, s1, forms]`` along the plot, each joined to its neighbour until it holds enough
    mass columns (the joined span carries both volumes' forms)."""
    if not mass_cells:
        return spans
    along = [float(frame.plot(x + 0.5, z + 0.5)[0]) for x, z in mass_cells]
    count = lambda sp: sum(1 for v in along if sp[0] <= v < sp[1])  # noqa: E731
    spans = [[sp[0], sp[1], set(sp[2])] for sp in spans]
    k = 0
    while len(spans) > 1 and k < len(spans):
        if count(spans[k]) >= MIN_REGION_COLUMNS:
            k += 1
            continue
        j = k + 1 if k + 1 < len(spans) else k - 1
        spans[j][0], spans[j][1] = min(spans[j][0], spans[k][0]), max(spans[j][1], spans[k][1])
        spans[j][2] = spans[j][2] | spans[k][2]
        del spans[k]
        k = 0
    return spans


def note(result: dict) -> str:
    """One sentence per volume for the spec ``notes``: form, height and what each rests on."""
    parts = []
    for vol in result['volumes']:
        tr = vol['trust']
        where = 'beyond the way' if vol['beyond'] else 'in the way'
        parts.append(
            f"v{vol['id']} {where}: {tr['form']['value']} ({tr['form']['rests_on']}), top "
            f"{tr['height']['value']['top_m']} m ({tr['height']['rests_on']}), {tr['storeys']['value']} storey(s), "
            f"roof {tr['colour']['value']} ({tr['colour']['rests_on']})"
            + (f"; flags: {', '.join(tr['flags'])}" if tr['flags'] else ''))
    return 'Read step proposal (#459). ' + '. '.join(parts) + '.'


# ---------------------------------------------------------------- world evidence (CLI only)


def load(way_id: str, loaded=None) -> dict:
    """Footprint, tags, street, lidar and aerial for one map way, as :class:`Evidence` plus context.

    @param loaded: ``(buildings, streets)`` as :func:`typology_spec.load_buildings` returns them, when the
        caller already has them (a row read loads the map once, not once per building)
    """
    import aerial_coverage
    import lidar_provider as lp
    import osm_footprints
    import typology_spec
    buildings, streets = loaded or typology_spec.load_buildings(
        osm_footprints.OSMIndex(), way_ids=[way_id],
        lidar=lambda _c: None, profile_at=lambda _c: None, rises=lambda _c: {})
    me = next((b for b in buildings if b['way_id'] == str(way_id) and not b.get('absent')), None)
    if me is None:
        raise SystemExit(f'way {way_id}: not in the vector map')
    cells = me['cells']
    centroid = typology_spec._centroid(cells)
    hit = typology_spec._facing(centroid, streets)
    street = (hit[1], hit[2]) if hit and hit[0] <= typology_spec.FRONT_REACH else None
    front, _why = typology_spec._front(cells, streets, None)
    frame = plot_frame(cells, street)
    s, t, band = profile_axes(frame)
    cs = [s[0] - 1, s[-1] + 1]
    ts = [t[0] - 1, t[-1] + 1]
    xs, zs = frame.world(np.array([a for a in cs for _b in ts]), np.array([b for _a in cs for b in ts]))
    x0, z0 = int(math.floor(xs.min())) - 1, int(math.floor(zs.min())) - 1
    x1, z1 = int(math.ceil(xs.max())) + 1, int(math.ceil(zs.max())) + 1
    heights_ = lp.mc_heights_float(lp.tile_client(download=False), x0, z0, x1, z1)
    rise = np.asarray(heights_['dsm'], dtype=float) - np.asarray(heights_['dtm'], dtype=float)

    def bilinear(wx, wz):
        fx, fz = np.asarray(wx) - 0.5 - x0, np.asarray(wz) - 0.5 - z0
        ix, iz = np.floor(fx).astype(int), np.floor(fz).astype(int)
        ok = (ix >= 0) & (iz >= 0) & (ix + 1 < rise.shape[0]) & (iz + 1 < rise.shape[1])
        ix, iz = np.clip(ix, 0, rise.shape[0] - 2), np.clip(iz, 0, rise.shape[1] - 2)
        ax, az = fx - ix, fz - iz
        val = (rise[ix, iz] * (1 - ax) * (1 - az) + rise[ix + 1, iz] * ax * (1 - az)
               + rise[ix, iz + 1] * (1 - ax) * az + rise[ix + 1, iz + 1] * ax * az)
        return np.where(ok, val, np.nan)

    ss, tt = np.meshgrid(s, t, indexing='ij')
    h = bilinear(*frame.world(ss, tt))
    columns = {}
    for i in range(rise.shape[0]):
        for j in range(rise.shape[1]):
            cs_, ct_ = frame.plot(x0 + i + 0.5, z0 + j + 0.5)
            columns[(x0 + i, z0 + j)] = (float(cs_), float(ct_), float(rise[i, j]))
    img_s = np.arange(s[0], s[-1] + 1e-9, CROP_M_PER_PX)[::-1]          # rear at the top
    img_t = np.arange(t[0] - 0.5, t[-1] + 0.5 + 1e-9, CROP_M_PER_PX)
    gs, gt = np.meshgrid(img_s, img_t, indexing='ij')
    wx, wz = frame.world(gs, gt)
    img = np.full(gs.shape + (3,), np.nan)
    flat_pts = list(zip((wx - 0.5).ravel().tolist(), (wz - 0.5).ravel().tolist()))
    for mosaic in aerial_coverage.mosaics():
        todo = np.isnan(img[..., 0]).ravel()
        if not todo.any():
            break
        rgb = aerial_coverage.load_rgb(mosaic.path)
        pix = mosaic.pixels([p for p, need in zip(flat_pts, todo) if need])
        flat = img.reshape(-1, 3)
        for k, p in zip(np.where(todo)[0], [p for p, need in zip(flat_pts, todo) if need]):
            px, py = pix[p]
            if 0 <= px < rgb.shape[1] and 0 <= py < rgb.shape[0]:
                c = rgb[int(py), int(px)]
                if c.any():
                    flat[k] = c / 255.0
        img = flat.reshape(img.shape)
    ev = Evidence(frame, s, t, h, band, columns, img, img_s, img_t)
    return {'evidence': ev, 'tags': me['tags'], 'front': front, 'cells': cells, 'street': street}


def roof_columns(cells: set, columns: dict, mass_m: float = MASS_M) -> set:
    """The footprint columns the builder treats as roof: squared footprint with lidar mass, small pieces
    dropped (the same helpers ``feature_build`` zones use).

    @param mass_m: rise a column needs to count (``feature_build`` zones use 3 m)
    """
    import feature_roof_zones as FZ
    xs_ = [x for x, _z in cells]
    zs_ = [z for _x, z in cells]
    x0, z0 = min(xs_) - 2, min(zs_) - 2
    width, depth = max(xs_) - x0 + 3, max(zs_) - z0 + 3
    mask = FZ.square_footprint(FZ.cells_to_mask(cells, x0, z0, width, depth))
    for z in range(depth):
        for x in range(width):
            col = columns.get((x0 + x, z0 + z))
            if mask[z, x] and not (col is not None and np.isfinite(col[2]) and col[2] >= mass_m):
                mask[z, x] = False
    gz, gx = np.mgrid[z0:z0 + depth, x0:x0 + width]
    mask, _dropped = FZ.drop_small_footprint_pieces(mask, gx, gz)
    return FZ.mask_to_cells(mask, x0, z0)


def crop_png(ev: Evidence, vols: list[Volume], path: Path) -> None:
    """The rotated aerial crop the model sees: rear up, the plot band and numbered volume lines drawn."""
    from PIL import Image, ImageDraw
    img = np.nan_to_num(ev.img if ev.img is not None else np.zeros((10, 10, 3)), nan=0.0)
    im = Image.fromarray((img * 255).astype(np.uint8)).resize(
        (img.shape[1] * CROP_SCALE, img.shape[0] * CROP_SCALE), Image.NEAREST)
    draw = ImageDraw.Draw(im)

    def col(t):
        return int(round((t - ev.img_t[0]) / CROP_M_PER_PX * CROP_SCALE))

    def row(s):
        return int(round((ev.img_s[0] - s) / CROP_M_PER_PX * CROP_SCALE))

    for t in (ev.frame.t_min - 0.5, ev.frame.t_max + 0.5):
        draw.line([(col(t), 0), (col(t), im.height)], fill=(255, 255, 255), width=1)
    left, right = col(ev.frame.t_min - 0.5), col(ev.frame.t_max + 0.5)
    for vol in vols:
        for s in (vol.s0, vol.s1):
            draw.line([(left - 12, row(s)), (right + 12, row(s))], fill=(255, 230, 0), width=1)
        mid = row((vol.s0 + vol.s1) / 2)
        draw.rectangle([right + 14, mid - 7, right + 26, mid + 7], fill=(0, 0, 0))
        draw.text((right + 17, mid - 6), str(vol.id), fill=(255, 230, 0))
    im.save(path)


def _defaults(tags: dict) -> tuple[dict, str, dict | None]:
    """Class defaults for the fallback (form, roof type, storeys), the wall and the body recipe."""
    import land_use
    cls, _why = land_use.building_class(tags)
    section = land_use.typology_defaults(cls) if cls else None
    roofs = (section or {}).get('roofs') or ['gable']
    storeys = ((section or {}).get('storeys') or {}).get('min') or 2
    palette = (section or {}).get('palette') or ['bricks']
    form = 'flat' if roofs[0] == 'flat' else 'gable_across'
    pitched = (land_use.defaults(cls).get('terrace_roofs') or ['grey_slate'])[0] if cls else 'grey_slate'
    roof = {'flat': 'flat_deck', 'pitched': pitched}
    return ({'form': form, 'roof': roof, 'storeys': storeys}, palette[0],
            land_use.body_recipe(cls) if cls else None)


def main(argv=None) -> int:
    """Read one building; write the crop, prompt, read and spec proposal under ``--out``. Never places blocks.

    @param argv: arguments after ``--world`` has been taken off
    @returns: process status
    """
    parser = argparse.ArgumentParser(description='Read roof volumes for one map way (lidar + aerial + one '
                                     'designer-model decision) and write a feature_build spec proposal.')
    parser.add_argument('--way', required=True, help='map way id')
    parser.add_argument('--out', required=True, type=Path, help='directory for the read (never site/builds)')
    parser.add_argument('--name', help='spec name for the proposal (default read_<way>)')
    how = parser.add_mutually_exclusive_group()
    how.add_argument('--ask', action='store_true', help='ask the designer model (config/build-roles.json)')
    how.add_argument('--answer', type=Path, help='a stored answer JSON (model text, or {"text", "usage"})')
    args = parser.parse_args(argv)
    from script_env import ROOT
    out = args.out.resolve()
    if (Path(ROOT) / 'site' / 'builds').resolve() in (out, *out.parents):
        parser.error('--out must not be the world site/builds: the read writes a proposal, not a spec')
    out.mkdir(parents=True, exist_ok=True)
    loaded = load(args.way)
    ev = loaded['evidence']
    vols = measure(ev, segment(ev))
    types = roof_types()
    question = prompt(vols, types)
    stem = f'read-{args.way}'
    crop = out / f'{stem}-crop.png'
    crop_png(ev, vols, crop)
    (out / f'{stem}-prompt.txt').write_text(question + '\n')
    call = None
    if args.ask:
        import designer_ask
        from build_roles import resolve_roles
        call = designer_ask.ask(crop, question, role_id=resolve_roles().designer)
    elif args.answer:
        stored = args.answer.read_text()
        try:
            data = json.loads(stored)
        except ValueError:
            data = None
        call = data if isinstance(data, dict) and 'text' in data else {'text': stored, 'usage': None,
                                                                        'measured': 'not measured'}
    answer = parse_answer(call['text'], vols, types) if call else None
    defaults, wall, body = _defaults(loaded['tags'])
    result = read(ev, answer, defaults=defaults, tags=loaded['tags'], body=body)
    result.update(way=str(args.way), decision=call, answer=answer, defaults=defaults)
    (out / f'{stem}.json').write_text(json.dumps(result, indent=2, default=float) + '\n')
    mass = roof_columns(loaded['cells'], ev.columns)
    spec = proposal(result, name=args.name or f'read_{args.way}', way_id=args.way,
                    front=loaded['front'], wall=wall, mass_cells=mass)
    (out / f'{stem}-proposal.json').write_text(json.dumps(spec, indent=2) + '\n')
    print(summary(result))
    print(f'wrote {crop.name}, {stem}-prompt.txt, {stem}.json, {stem}-proposal.json under {out}')
    return 0


def summary(result: dict) -> str:
    """Printable lines: one per volume with each attribute's value, basis and source statuses."""
    lines = [f"way {result.get('way')}: {len(result['volumes'])} volume(s), main v{result['main']}"]
    call = result.get('decision')
    if call and call.get('usage'):
        lines.append(f"decision {call.get('model')} {call.get('effort')}: {call.get('total_tokens')} tokens "
                     f"({call['usage']}), {call.get('measured')}")
    for vol in result['volumes']:
        tr = vol['trust']
        lines.append(f"v{vol['id']} s {vol['s0']:.1f}..{vol['s1']:.1f}{' beyond the way' if vol['beyond'] else ''}: "
                     f"lidar {vol['kind']}/{vol['form']} ({vol['form_basis']}) eave {vol['eave_m']} top {vol['top_m']} rough "
                     f"{vol['rough_m']} green {vol['green']} lum {vol['lum_ratio']}")
        for key in ('height', 'form', 'colour', 'storeys', 'front'):
            if key in tr:
                row = tr[key]
                srcs = ', '.join(f'{k} {v}' for k, v in row.items()
                                 if k not in ('value', 'rests_on', 'aerial_rgb', 'support'))
                lines.append(f"    {key}: {row['value']} (rests on {row['rests_on']}; {srcs})")
        if tr['flags']:
            lines.append('    flags: ' + '; '.join(tr['flags']))
    return '\n'.join(lines)


if __name__ == '__main__':
    raise SystemExit(main())
