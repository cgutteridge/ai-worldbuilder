#!/usr/bin/env python3
"""Layered trees pass for one 128² cell — HANDOFF-LAYERED-PASSES §1 (pass 3), §2, §3.

Crowns are read from local aerial mosaics with verified full-cell coverage by texture
plus greenness, not colour alone: sunlit crowns here are the same hue as the lawn.
Trees stand on the true ground: the trunk base is the block at lidar terrain Y (``ground_at``), never the
live top block, so a roof is not ground (#301). The block at terrain Y must be solid, unowned ground
('no ground at terrain' otherwise) and no feature-owned block may stand in the trunk's own column up to
the crown top ('feature above terrain'); the tree is never moved onto the roof. Where terrain is missing
the live surface is used as before and the report counts it.
Trees go only where the live block is air (or a grass stem / flower, as in §10), so the
mask undo is exact. A crown voxel that is not air (roof, wall, a neighbour's block) is
dropped; leaves may fill air beside or above a feature, but a feature's own blocks and margin air
(``owned``) are never used. The tree is skipped if too much of it would be dropped or the trunk is blocked.
A tree belongs to the cell its trunk is in (the trunk is always in-cell); its crown may overhang into
neighbours' columns where the block is air or a leaf of a neighbour's trees mask (crowns merge), recorded
in this cell's mask. It never overwrites any other block. Undo skips blocks another mask also lists.
Writes a trees mask (§3) only after a clean readback.

  python3 scripts/trees_layer.py --cell 48,-44            # dry run + crown review image
  SITE_OWNER=<owner> python3 scripts/trees_layer.py --cell 48,-44 --apply
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import argparse
import json
import math
import random
import re
import sys
from pathlib import Path

import numpy as np

from script_env import ROOT  # noqa: E402

from ground_layer import (plan_name as ground_plan_name, bare, ignored_builds, is_air,  # noqa: E402
                          load_masked_positions, neighbour_owned, warm_surface_probe)
from surface_probe import ground_at, ground_missing_reason, is_water, raised_over_terrain, short_id, surface_y, terrain_y  # noqa: E402
from pass_mask import build_mask, cell_bounds, clip_to_cell  # noqa: E402
from plant_cell_canopy import RNG_SEED, bush_voxels, tree_voxels  # noqa: E402
from cellmath import CELL  # noqa: E402
from site_kit import Plan, World  # noqa: E402

LAYER_PLAN = re.compile(r'^highfield_\d+_m\d+_(ground|trees|decor|foliage|stripabove|edge_grass|podium_flatten)$')
LAWN_SURFACES = frozenset({'grass_block', 'dirt', 'podzol', 'coarse_dirt', 'moss_block'})
# Detector/dense-fill material policy; explicit crowns instead use evidence-led rooting.
# Rooting never changes the surface block.
PAVED_SURFACES = frozenset({'light_gray_concrete', 'polished_granite', 'gravel'})
# Hard-surface rule (2026-10-15 dense-tree redo): trunks stand on lawn only, never on paving, gravel or tarmac.
TREES_ON_PAVING = False
KEEP_FRACTION = 0.7        # a crown that loses more than this to non-air is skipped
MIN_CROWN_R = 1.8          # below this a blob is a bush, not a tree
MAX_CROWN_R = 7.0
DENSITY_MIN = 0.55
# Dense-cover fill (I-059): in woodland the individual-crown detector finds a fraction of the crowns
# (shaded, brown-olive or dark canopy fails the lawn-green test), so area cover is read with a looser
# test and filled at a spacing that matches it. Planting still goes through plan_cell's rules.
COVER_MIN = 0.6            # local cover fraction below which nothing is filled (light canopy unchanged)
COVER_FULL = 0.9           # at or above this every fill site gets a tree
FILL_SPACING = 5           # jittered grid pitch of the fill (crowns here are 5-7 blocks across)
FILL_RADII = (2.5, 3.0, 3.0, 3.5, 4.0, 4.5)   # height variance: round 4-6 below 3.5, plane 8-9 above


class _InCell:
    """Membership test: is a position's x/z inside the cell."""

    def __init__(self, x0: int, z0: int, x1: int, z1: int):
        self.box = (x0, z0, x1, z1)

    def __contains__(self, p) -> bool:
        x0, z0, x1, z1 = self.box
        return x0 <= p[0] <= x1 and z0 <= p[2] <= z1


def may_plant(live: str | None, block: str, owner: str | None) -> bool:
    """The one placement rule: air, or a leaf onto a leaf that another cell's trees mask owns (crowns merge).

    owner is the mask owning the position ('trees 46,-44'), or None. Anything else is never overwritten.
    """
    if owner is None:
        return is_air(live)
    return owner.startswith('trees ') and 'leaves' in bare(live) and 'leaves' in block


def plan_name(tx: int, tz: int) -> str:
    """Build name, e.g. highfield_48_m44_trees."""
    return f"highfield_{tx}_{'m' if tz < 0 else ''}{abs(tz)}_trees"


def _box(a: np.ndarray, k: int) -> np.ndarray:
    """Mean over a k×k window (edge padded)."""
    pad = np.pad(a, k // 2, mode='edge')
    h, w = a.shape
    return sum(pad[i:i + h, j:j + w] for i in range(k) for j in range(k)) / (k * k)


def canopy_pixels(img_path: Path) -> np.ndarray:
    """Boolean canopy mask over the mosaic: texture + greenness, then a morphological closing."""
    from PIL import Image

    a = np.asarray(Image.open(img_path).convert('RGB')).astype(float) / 255
    r, g = a[..., 0], a[..., 1]
    mx, mn = a.max(2), a.min(2)
    d = mx - mn
    s = np.where(mx > 0, d / np.maximum(mx, 1e-9), 0)
    h = np.zeros_like(mx)
    m = d > 0
    b = a[..., 2]
    idx = m & (mx == r)
    h[idx] = ((g - b)[idx] / d[idx]) % 6
    idx = m & (mx == g)
    h[idx] = ((b - r)[idx] / d[idx]) + 2
    idx = m & (mx == b)
    h[idx] = ((r - g)[idx] / d[idx]) + 4
    h *= 60
    lum = a.mean(2)
    mu = _box(lum, 7)
    sd = np.sqrt(np.maximum(_box(lum ** 2, 7) - mu ** 2, 0))
    # z18-filled pixels (gap in the z19 mosaic) are upsampled and smooth: no texture bar and a teal-shifted hue range there
    fm = img_path.with_name(img_path.stem + '-mask.png')
    filled = np.asarray(Image.open(fm).convert('L')) > 0 if fm.exists() else np.zeros(sd.shape, bool)
    mask = (sd >= np.where(filled, 0.0, 0.035)) & (g >= r * 0.93) & (s >= 0.14) & (mx <= 0.85) & (h >= 38) & (h <= np.where(filled, 200, 140))
    dil = _box(mask.astype(float), 9) > 1e-6
    return _box(dil.astype(float), 9) > 0.999


def cover_pixels(img_path: Path) -> np.ndarray:
    """Boolean vegetation-cover mask: any saturated, not-bright, green-to-olive pixel (no texture test).

    Wider than canopy_pixels on purpose: shaded woodland is dark, olive or brownish (g < r) and smooth at
    this resolution, so the crown test rejects most of it. Roofs (red, g << r), paving and tarmac (low
    saturation) and bright surfaces stay out.
    """
    from PIL import Image

    a = np.asarray(Image.open(img_path).convert('RGB')).astype(float) / 255
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mx, mn = a.max(2), a.min(2)
    s = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9), 0)
    mask = (s >= 0.22) & (mx <= 0.62) & (g > b + 0.02) & (g >= r * 0.8)
    return _box(_box(mask.astype(float), 5), 5) > 0.5


def cover_grid(cell: tuple[int, int], mask: np.ndarray | None = None) -> np.ndarray:
    """128×128 [x - x0, z - z0] = fraction of cover pixels in a ~7-block window around the column."""
    return _mosaic_grid(cell, cover_pixels, 35, mask)


def fill_dense(cover: np.ndarray, crowns: list[tuple[int, int, float]], seed: int = 0) -> list[tuple[int, int, float]]:
    """Extra crowns (dx, dz, r) so dense cover gets trees at a density that follows the cover fraction.

    A jittered grid at FILL_SPACING; a site is used with probability rising from 0 at COVER_MIN to 1 at
    COVER_FULL, and not within an existing crown. Deterministic for a given cell.
    """
    rnd = random.Random(RNG_SEED + seed)
    ii, jj = np.mgrid[0:128, 0:128]
    taken = np.zeros((128, 128), bool)
    for ci, cj, cr in crowns:
        taken |= (ii - ci) ** 2 + (jj - cj) ** 2 <= (cr * 0.75 + 1.5) ** 2
    extra = []
    for gi in range(0, 128, FILL_SPACING):
        for gj in range(0, 128, FILL_SPACING):
            i = min(127, max(0, gi + rnd.randint(-1, 1) + FILL_SPACING // 2))
            j = min(127, max(0, gj + rnd.randint(-1, 1) + FILL_SPACING // 2))
            p = (cover[i, j] - COVER_MIN) / (COVER_FULL - COVER_MIN)
            if rnd.random() >= p or taken[i, j]:    # rnd drawn first so sites do not shift with neighbours
                continue
            r = rnd.choice(FILL_RADII)
            extra.append((i, j, r))
            taken |= (ii - i) ** 2 + (jj - j) ** 2 <= (r * 0.75) ** 2
    return extra


def canopy_grid(cell: tuple[int, int]) -> np.ndarray:
    """128×128 array [x - x0, z - z0] = fraction of canopy pixels around that column."""
    return _mosaic_grid(cell, canopy_pixels, 5)


def _mosaic_grid(cell, detector, window, mask=None):
    from aerial_coverage import cell_columns, require_coverage, samples
    sources = require_coverage(cell)
    points = cell_columns(cell)
    selected = samples(points, sources)
    x0, z0, x1, z1 = cell_bounds(*cell)
    grid = np.zeros((x1 - x0 + 1, z1 - z0 + 1))
    for source in sources:
        positions = {p: sample for p, sample in selected.items() if sample[0] == source}
        if not positions:
            continue
        pixels = detector(source.path) if mask is None else mask
        density = _box(pixels.astype(float), window)
        for (x, z), (_source, px, py, _rgb) in positions.items():
            grid[x - x0, z - z0] = density[int(py), int(px)]
    return grid


def find_crowns(grid: np.ndarray) -> list[tuple[int, int, float]]:
    """Greedy crown packing: (dx, dz, radius) in cell-local columns, densest first."""
    smooth = _box(grid, 5)
    order = sorted(((smooth[i, j], i, j) for i in range(128) for j in range(128)
                    if smooth[i, j] >= DENSITY_MIN and grid[i, j] >= 0.5), reverse=True)
    used = np.zeros((128, 128), bool)
    ii, jj = np.mgrid[0:128, 0:128]
    crowns = []
    for _v, i, j in order:
        if used[i, j]:
            continue
        r = 1.0
        while r < MAX_CROWN_R:
            disc = (ii - i) ** 2 + (jj - j) ** 2 <= (r + 0.5) ** 2
            if grid[disc].mean() < 0.6:
                break
            r += 0.5
        r = min(MAX_CROWN_R, r)
        if r < 1.5:
            continue
        crowns.append((i, j, r))
        used |= (ii - i) ** 2 + (jj - j) ** 2 <= (r * 0.75) ** 2
    return crowns


EXTRA_CROWNS = ROOT / 'reference/trees-extra-crowns.json'


def extra_crowns(cell: tuple[int, int], path: Path | None = None) -> list[tuple[int, int, float]]:
    """Crowns the z19 mosaic cannot show, from reference/trees-extra-crowns.json: {"tx,tz": [{"x","z","r","evidence"}]}.

    Each entry is a tree read from other imagery (registered user aerial + LIDAR dome) where the mosaic shows no
    canopy. They are cell-local (dx, dz, r) like detected crowns and go through exactly the same planting rules
    (evidenced rooting, trunk in cell, air only, mask); only the vector-map footprint 'feature zone' skip is waived, because
    these sit where a mapped roof way was found to be a tree (I-055).
    """
    path = path or EXTRA_CROWNS
    if not path.exists():
        return []
    x0, z0 = cell[0] * CELL, cell[1] * CELL
    out = []
    for e in json.loads(path.read_text()).get(f'{cell[0]},{cell[1]}', []):
        i, j = int(e['x']) - x0, int(e['z']) - z0
        if 0 <= i < 128 and 0 <= j < 128:
            out.append((i, j, float(e['r'])))
    return out


GOOGLE_CROWNS = ROOT / 'reference/trees-google-crowns.json'
# Hand-read aerial crowns often sit a few blocks off the real canopy (S-017 / I-011). Before planting,
# snap each to the highest nDSM column in this Chebyshev window; no peak or a peak on a building → leave
# and mark unconfirmed. No road rule — positions come from lidar evidence, not a "keep off carriageway" filter.
CROWN_SNAP_RADIUS = 6
CROWN_SNAP_MIN_NDSM_M = 3.0
# Mapped vector-map trees (#157): default crown radius; young trees (no lidar peak) stay
# as small crowns — same keep/unconfirmed choice as #129 snap, not a hard reject.
MAPPED_TREE_R = 3.0
MAPPED_YOUNG_TREE_R = 2.0
TREE_ROW_SPACING = 4          # blocks between crowns sampled along natural=tree_row


def google_crowns(cell: tuple[int, int], path: Path | None = None):
    """Crowns read by eye from the aerial PNG sheet, from reference/trees-google-crowns.json.

    {"tx,tz": {"evidence": "...", "zones": [[x0, z0, x1, z1]], "crowns": [{"x", "z", "r", "note"}]}}. Inside a zone the
    detector's crowns (mosaic detection, dense fill, extra crowns) are DROPPED and these replace them: the aerial sheet
    is the position source there (the z19 mosaic and the lidar both put crowns blocks off, and the lidar holds ~1/3 of them).
    Outside the zones the detector is unchanged. Aerial-sheet crowns go first (they win overlaps), are exempt from the
    vector-map feature-zone skip (a courtyard tree is real) and may root on hard ground (a plaza tree has a soil pit);
    every other rule (trunk in cell, air only, mask) is the normal one. Positions are then snapped to nearby lidar
    nDSM peaks (see ``snap_google_crowns``).

    @returns: (zones, [(dx, dz, r) cell-local]) or None when the cell has no entry
    """
    path = path or GOOGLE_CROWNS
    if not path.exists():
        return None
    e = json.loads(path.read_text()).get(f'{cell[0]},{cell[1]}')
    if not e:
        return None
    x0, z0 = cell[0] * CELL, cell[1] * CELL
    out = [(int(c['x']) - x0, int(c['z']) - z0, float(c['r'])) for c in e['crowns']]
    return [tuple(z) for z in e.get('zones', [])], [c for c in out if 0 <= c[0] < 128 and 0 <= c[1] < 128]


def snap_google_crowns(
    crowns: list[tuple[int, int, float]],
    ndsm: np.ndarray,
    footprints: set[tuple[int, int]],
    radius: int = CROWN_SNAP_RADIUS,
    min_ndsm: float = CROWN_SNAP_MIN_NDSM_M,
) -> tuple[list[tuple[int, int, float]], list[dict]]:
    """Move each hand-read crown to the highest nearby lidar canopy column (nDSM ≥ ``min_ndsm``).

    Search is a Chebyshev window of ``radius`` blocks. The highest qualifying column wins; ties prefer
    the closer column, then smaller (dx, dz). When no column qualifies, or the winner sits on a building
    footprint, the crown stays put and the report status is ``unconfirmed``.

    @param crowns: cell-local ``(dx, dz, r)``
    @param ndsm: nDSM metres, shape ``(128, 128)`` indexed ``[dx, dz]``
    @param footprints: building footprint columns in cell-local ``(dx, dz)``
    @param radius: Chebyshev search radius in blocks
    @param min_ndsm: minimum nDSM (metres) to count as canopy
    @returns: ``(snapped crowns, per-crown reports)``; each report has ``from``, ``to``, ``shift``,
              ``status`` (``moved`` | ``unconfirmed``), and ``ndsm`` at the chosen peak
    """
    h, w = ndsm.shape
    out: list[tuple[int, int, float]] = []
    report: list[dict] = []
    for i, j, r in crowns:
        best = None  # (-height, dist2, di, dj)
        for di in range(max(0, i - radius), min(h, i + radius + 1)):
            for dj in range(max(0, j - radius), min(w, j + radius + 1)):
                if max(abs(di - i), abs(dj - j)) > radius:
                    continue
                height = float(ndsm[di, dj])
                if height < min_ndsm:
                    continue
                dist2 = (di - i) ** 2 + (dj - j) ** 2
                key = (-height, dist2, di, dj)
                if best is None or key < best:
                    best = key
        here = float(ndsm[i, j]) if 0 <= i < h and 0 <= j < w else 0.0
        if best is None or (best[2], best[3]) in footprints:
            peak_h = here if best is None else float(ndsm[best[2], best[3]])
            out.append((i, j, r))
            report.append({'from': (i, j), 'to': (i, j), 'shift': (0, 0),
                           'status': 'unconfirmed', 'ndsm': peak_h})
            continue
        _neg_h, _d2, pi, pj = best
        shift = (pi - i, pj - j)
        out.append((pi, pj, r))
        report.append({'from': (i, j), 'to': (pi, pj), 'shift': shift,
                       'status': 'moved', 'ndsm': float(ndsm[pi, pj])})
    return out, report


def cell_ndsm(cell: tuple[int, int]) -> np.ndarray | None:
    """nDSM (DSM − DTM) metres for one cell as ``[dx, dz]``, or None when lidar is unavailable.

    @param cell: ``(tx, tz)``
    @returns: float array shape ``(128, 128)``, or None
    """
    try:
        import lidar_provider as lp
        lidar = lp.tile_client(download=False)
        x0, z0 = cell[0] * CELL, cell[1] * CELL
        h = lp.mc_heights(lidar, x0, z0, x0 + CELL - 1, z0 + CELL - 1)
        return np.asarray(h['diff'], dtype=float)
    except Exception:
        return None


def cell_terrain(cell: tuple[int, int]) -> dict | None:
    """Lidar height map of one cell (``terrain`` = DTM ground Y), or None when lidar is unavailable.

    @param cell: ``(tx, tz)``
    @returns: the ``reference_world().heightmap(cell)`` dict, or None
    """
    try:
        from lidar_heightmap import reference_world
        return reference_world().heightmap(cell)
    except Exception as exc:
        # every tree then stands on the live surface, roofs included: say so, do not revert silently
        print(f'  warning: no lidar terrain for cell {cell} ({type(exc).__name__}: {exc}); '
              f'trees use the live surface', file=sys.stderr)
        return None


def terrain_summary(report: dict) -> str:
    """One console line for ``report['terrain']`` (the JSON report has the same counts).

    @param report: ``plan_cell`` report
    @returns: the summary line
    """
    t = report['terrain']
    return (f"  terrain ground: {t['used']} used, {t['moved']} moved under a roof, "
            f"{t['fallback']} fell back to the live surface")


def tree_row_sample_points(
    pts: list[tuple[float, float]],
    spacing: int = TREE_ROW_SPACING,
) -> list[tuple[int, int]]:
    """Sample integer MC columns along a tree_row polyline at ``spacing`` block intervals.

    Always includes the first vertex; then walks arc length and emits a column each
    time ``spacing`` is crossed. The final vertex is included when it is at least
    half a spacing past the last sample (avoids a near-duplicate at the tip).

    @param pts: polyline vertices in MC ``(x, z)``
    @param spacing: distance in blocks between samples
    @returns: ordered ``(x, z)`` floor columns
    """
    if not pts:
        return []
    out: list[tuple[int, int]] = [(int(math.floor(pts[0][0])), int(math.floor(pts[0][1])))]
    if len(pts) == 1:
        return out
    acc = 0.0
    next_at = float(spacing)
    for a, b in zip(pts, pts[1:]):
        seg = math.dist(a, b)
        if seg < 1e-9:
            continue
        while next_at <= acc + seg:
            t = (next_at - acc) / seg
            x = a[0] + t * (b[0] - a[0])
            z = a[1] + t * (b[1] - a[1])
            q = (int(math.floor(x)), int(math.floor(z)))
            if q != out[-1]:
                out.append(q)
            next_at += spacing
        acc += seg
    tip = (int(math.floor(pts[-1][0])), int(math.floor(pts[-1][1])))
    if tip != out[-1] and math.dist(out[-1], tip) >= spacing * 0.5:
        out.append(tip)
    return out


def resolve_mapped_tree_crowns(
    cell: tuple[int, int],
    tree_points: list[tuple[int, int]] | None = None,
    tree_rows: list[list[tuple[float, float]]] | None = None,
    ndsm: np.ndarray | None = None,
    footprints: set[tuple[int, int]] | None = None,
) -> tuple[list[tuple[int, int, float]], list[dict], list[dict]]:
    """Turn mapped tree points and tree_row lines into cell-local crown candidates (#157).

    Each in-cell point is snapped to the highest nearby lidar canopy peak (same
    window and nDSM floor as #129 / ``snap_google_crowns``). Young trees with no
    peak (or a peak on a building) are **kept** as small crowns with snap status
    ``unconfirmed`` and reason ``no lidar canopy peak`` / ``peak on building`` —
    matching #129, not rejected. Points outside the 128² cell are rejected with
    reason ``outside cell``.

    @param cell: ``(tx, tz)``
    @param tree_points: world ``(x, z)`` from ``OSMGround.trees``
    @param tree_rows: polylines from ``OSMGround.tree_rows``
    @param ndsm: cell-local nDSM metres, or None to skip snap (all kept unconfirmed)
    @param footprints: building columns in cell-local ``(dx, dz)``
    @returns: ``(crowns, rejections, snap_reports)``; crowns are cell-local
              ``(dx, dz, r)``; each rejection has ``x``, ``z``, ``reason``
    """
    x0, z0 = cell[0] * CELL, cell[1] * CELL
    footprints = footprints or set()
    raw: list[tuple[int, int]] = list(tree_points or [])
    for row in tree_rows or []:
        raw.extend(tree_row_sample_points(row))
    candidates: list[tuple[int, int, float]] = []
    rejections: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for x, z in raw:
        dx, dz = x - x0, z - z0
        if not (0 <= dx < CELL and 0 <= dz < CELL):
            rejections.append({'x': x, 'z': z, 'reason': 'outside cell'})
            continue
        key = (dx, dz)
        if key in seen:
            rejections.append({'x': x, 'z': z, 'reason': 'duplicate column'})
            continue
        seen.add(key)
        candidates.append((dx, dz, MAPPED_TREE_R))
    if not candidates:
        return [], rejections, []
    if ndsm is None:
        # No lidar: keep mapped positions as young/unconfirmed crowns (#129 leave-in-place).
        crowns = [(i, j, MAPPED_YOUNG_TREE_R) for i, j, _r in candidates]
        snaps = [{'from': (i, j), 'to': (i, j), 'shift': (0, 0),
                  'status': 'unconfirmed', 'ndsm': 0.0,
                  'reason': 'no lidar canopy peak'} for i, j, _r in candidates]
        return crowns, rejections, snaps
    snapped, snaps = snap_google_crowns(candidates, ndsm, footprints)
    crowns: list[tuple[int, int, float]] = []
    enriched: list[dict] = []
    for (i, j, _r), snap in zip(snapped, snaps):
        if snap['status'] == 'unconfirmed':
            reason = ('peak on building' if snap['ndsm'] >= CROWN_SNAP_MIN_NDSM_M
                      else 'no lidar canopy peak')
            snap = {**snap, 'reason': reason}
            crowns.append((i, j, MAPPED_YOUNG_TREE_R))
        else:
            crowns.append((i, j, MAPPED_TREE_R))
        enriched.append(snap)
    return crowns, rejections, enriched


def voxels_for(x: int, z: int, gy: int, r: float) -> dict:
    """Voxels for one crown: bush for small ones, plane/round tree otherwise."""
    rnd = random.Random(RNG_SEED + x * 31 + z)
    if r < MIN_CROWN_R + 0.2:
        return bush_voxels(x, z, gy)
    if r >= 6:
        h, style = rnd.choice((12, 13, 14)), 'plane'
    elif r >= 3.5:
        h, style = rnd.choice((8, 9)), 'plane'
    else:
        h, style = rnd.choice((4, 5, 6)), 'round'
    vox = tree_voxels(x, z, gy, height=h, style=style, rng=random.Random(RNG_SEED + x * 31 + z))
    # tree_voxels mottles plane trunks with birch patches; here trunks are plain oak (user, 2026-10-14)
    return {p: ('oak_log' if b == 'birch_log' else b) for p, b in vox.items()}


@dataclass
class TreeInputs:
    """Evidence and exclusions for one cell; shared by previews, live plans and redo plans."""
    crowns: list
    detected: int
    extra: set
    aerial_sheet: set
    owned: set
    ground_cols: set | None
    masked: dict
    footprints: set
    crown_snaps: list | None = None  # google-crown lidar snap report, when snapped
    mapped: set | None = None         # world (x, z) of mapped vector-map crowns (#157)
    mapped_rejections: list | None = None  # logged rejects for mapped points/rows
    mapped_snaps: list | None = None  # per-mapped-crown lidar snap report

    road: set = field(default_factory=set)
    water: set = field(default_factory=set)
    crown_origins: list | None = None  # world (x, z), aligned with retained crowns before lidar snapping
    terrain: Any = None                # lidar height map of the cell (``terrain`` = ground Y), None when unavailable (#301)

    def after_undo(self, remaining_masks):
        """A real undo clears only unshared positions; other masks retain ownership."""
        return replace(self, masked=dict(remaining_masks))


def planning_inputs(cell, osm_ground=None):
    """Load evidence once, keeping filesystem access out of placement rules.

    @param cell: ``(tx, tz)``
    @param osm_ground: optional ``OSMGround`` (or stand-in with ``.trees`` / ``.tree_rows``)
                       for tests; default loads the live vector map
    """
    tx, tz = cell
    x0, z0 = tx * CELL, tz * CELL
    grid = canopy_grid(cell)
    crowns = find_crowns(grid)
    n_detected = len(crowns)
    crowns = crowns + fill_dense(cover_grid(cell), crowns, seed=tx * 1000 + tz)
    extra = {(x0 + i, z0 + j) for i, j, _r in extra_crowns(cell)}
    crowns = crowns + [c for c in extra_crowns(cell) if not any((c[0] - ci) ** 2 + (c[1] - cj) ** 2 <= (cr * 0.75) ** 2
                                                                for ci, cj, cr in crowns)]
    crown_origins = [(x0 + i, z0 + j) for i, j, _r in crowns]
    aerial_sheet = set()
    crown_snaps = None
    from osm_footprints import OSMIndex
    fp = {q for q in OSMIndex().cells() if x0 - 2 <= q[0] <= x0 + 129 and z0 - 2 <= q[1] <= z0 + 129}
    ndsm = cell_ndsm(cell)
    fp_local = {(x - x0, z - z0) for x, z in fp
                if 0 <= x - x0 < CELL and 0 <= z - z0 < CELL}
    g = google_crowns(cell)
    if g:
        zones, gc = g
        google_origins = [(x0 + i, z0 + j) for i, j, _r in gc]
        if ndsm is not None:
            gc, crown_snaps = snap_google_crowns(gc, ndsm, fp_local)
        retained = [(c, origin) for c, origin in zip(crowns, crown_origins)
                    if not any(a <= x0 + c[0] <= cx and b <= z0 + c[1] <= cz for a, b, cx, cz in zones)]
        crowns = gc + [c for c, _origin in retained]
        crown_origins = google_origins + [origin for _c, origin in retained]
        aerial_sheet = {(x0 + i, z0 + j) for i, j, _r in gc}
    # Mapped natural=tree points + tree_row crown sequences (#157).
    if osm_ground is None:
        from ground_sources import OSMGround
        osm_ground = OSMGround(cell)
    mapped_crowns, mapped_rejections, mapped_snaps = resolve_mapped_tree_crowns(
        cell,
        tree_points=list(osm_ground.trees),
        tree_rows=list(osm_ground.tree_rows),
        ndsm=ndsm,
        footprints=fp_local,
    )
    mapped = {(x0 + i, z0 + j) for i, j, _r in mapped_crowns}
    # Mapped crowns win overlaps (surveyed position); skip detector crowns too close.
    retained = [
        (c, origin) for c, origin in zip(crowns, crown_origins)
        if not any((c[0] - mi) ** 2 + (c[1] - mj) ** 2 <= (mr * 0.75) ** 2
                   for mi, mj, mr in mapped_crowns)
    ]
    crowns = mapped_crowns + [c for c, _origin in retained]
    crown_origins = [(x0 + snap['from'][0], z0 + snap['from'][1]) for snap in mapped_snaps] + [
        origin for _c, origin in retained]
    owned = neighbour_owned(cell, ignored_builds(cell))
    from cell_registry import view_registry
    ground = view_registry().recorded(ground_plan_name(tx, tz, 'ground'))
    ground_cols = {(x, z) for x, _y, z in ground} if ground is not None else None
    return TreeInputs(crowns, n_detected, extra, aerial_sheet, owned, ground_cols, load_masked_positions(), fp,
                      crown_snaps, mapped, mapped_rejections, mapped_snaps,
                      set(getattr(osm_ground, 'road', ())),
                      set(getattr(osm_ground, 'water', ())) | set(getattr(osm_ground, 'water_line', ())),
                      crown_origins=crown_origins, terrain=cell_terrain(cell))


CROWN_MARGIN = 12      # columns past the cell edge warmed for crown overhang reads


def plan_cell(cell: tuple[int, int], plan: Plan, world: World, inputs: TreeInputs | None = None):
    """Plan trees against one coherent world/evidence view. Returns (placements, report)."""
    tx, tz = cell
    x0, z0 = tx * CELL, tz * CELL
    cell_xz = _InCell(*cell_bounds(tx, tz))
    inputs = planning_inputs(cell) if inputs is None else inputs
    crowns, extra, aerial_sheet = inputs.crowns, inputs.extra, inputs.aerial_sheet
    mapped = inputs.mapped or set()
    owned, ground_cols, masked, fp = inputs.owned, inputs.ground_cols, inputs.masked, inputs.footprints
    placed: dict[tuple[int, int, int], str] = {}
    report = {'candidates': len(crowns), 'detected': inputs.detected, 'trees': [], 'skipped': [],
              'crown_snaps': inputs.crown_snaps,
              'mapped_rejections': inputs.mapped_rejections or [],
              'mapped_snaps': inputs.mapped_snaps or [], 'rooting': [],
              'terrain': {'used': 0, 'moved': 0, 'fallback': 0}}
    warm_surface_probe(world, x0 - CROWN_MARGIN, z0 - CROWN_MARGIN,
                       x0 + CELL - 1 + CROWN_MARGIN, z0 + CELL - 1 + CROWN_MARGIN)   # batch reads (#110)
    # Explicit aerial, surveyed and extra crowns are evidence; detector/dense fill
    # retain their existing surface and feature-zone policy.
    evidenced = aerial_sheet | mapped | extra

    # Lidar snapping is a separate, already-completed stage. Use its original
    # observed crown for distance ties, never expand the shunt search from it.
    crown_origins = inputs.crown_origins
    if crown_origins is not None and len(crown_origins) != len(crowns):
        raise ValueError('crown_origins must align with crowns')

    base_kind: dict[tuple[int, int], tuple[str, int]] = {}   # (x, z) -> ('terrain' | 'live', base Y) of the last accepted base

    def ground(x, z):
        """Ground block at lidar terrain Y; the live surface only where terrain is missing (#301)."""
        # fallback=surface_y is passed explicitly so tests can patch this module's surface_y
        return ground_at(world, inputs.terrain, x, z, owned=owned, masked=masked, fallback=surface_y)

    def no_ground(x, z):
        return ground_missing_reason(inputs.terrain, x, z, owned)

    def candidate(x, z, r, observed):
        if (x, 0, z) not in cell_xz:
            return {}, 'trunk outside cell'
        surf = ground(x, z)
        if not surf:
            return {}, no_ground(x, z)
        if not observed and ((x, z) in fp or (ground_cols is not None and (x, z) not in ground_cols)):
            return {}, 'feature zone'
        if not observed and surf[1] not in (LAWN_SURFACES | PAVED_SURFACES if TREES_ON_PAVING else LAWN_SURFACES):
            return {}, f'surface {surf[1]}'
        gy = surf[0]
        vox = voxels_for(x, z, gy, r)
        trunk = [p for p, b in vox.items() if 'log' in b]
        roots = {(p[0], p[2]) for p in trunk if p[1] == gy + 1} or {(x, z)}
        for q in sorted(roots):
            top = ground(*q)
            if not top:
                return {}, no_ground(*q)
            live = world.block(q[0], top[0], q[1])
            if live is None:
                return {}, 'unknown water status'
            if (q[0], 0, q[1]) not in cell_xz:
                return {}, 'trunk outside cell'
            if q in inputs.water or is_water(live) or is_water(f'minecraft:{top[1]}') or 'waterlogged=true' in live:
                return {}, 'water'
            if q in inputs.road:
                return {}, 'carriageway'
            if observed and top[0] != gy:
                return {}, 'uneven trunk ground'
        # a feature's own block anywhere in the trunk column up to the crown top (a low roof) leaves no room (#301)
        crown_top = max(p[1] for p in vox)
        if any((q[0], y, q[1]) in owned for q in roots for y in range(gy + 1, crown_top + 1)):
            return {}, 'feature above terrain'
        kept = {p: b for p, b in vox.items()
                if may_plant(world.block(*p), b, masked.get(p)) and p not in owned and p not in placed}
        if any(p not in kept or p not in cell_xz for p in trunk):
            return {}, 'feature above terrain' if any(p in owned for p in trunk) else 'trunk blocked'
        if len(kept) < KEEP_FRACTION * len(vox):
            return {}, f'{len(vox) - len(kept)} of {len(vox)} voxels blocked'
        base_kind[(x, z)] = ('terrain' if terrain_y(inputs.terrain, x, z) is not None else 'live', gy)
        return kept, None

    for index, (i, j, r) in enumerate(crowns):
        origin = (x0 + i, z0 + j)
        observed = origin in evidenced
        x, z = origin
        kept, reason = candidate(x, z, r, observed)
        original_reason = reason
        observed_centre = crown_origins[index] if crown_origins is not None else origin
        attempts = [{'at': list(origin), 'reason': reason or 'accepted'}]
        # One orthogonal adjustment for road/water edges only, never a search
        # outward until a blocked tree fits. Coordinates provide a stable tie-break.
        if observed and reason in ('carriageway', 'water'):
            neighbours = ((x - 1, z), (x, z - 1), (x, z + 1), (x + 1, z))
            for nx, nz in sorted(neighbours, key=lambda q: ((q[0] - observed_centre[0]) ** 2 + (q[1] - observed_centre[1]) ** 2, q)):
                kept, reason = candidate(nx, nz, r, observed)
                attempts.append({'at': [nx, nz], 'reason': reason or 'accepted'})
                if kept:
                    x, z = nx, nz
                    break
            if not kept:
                reason = f'{original_reason}: no legal one-block rooting position'
        report['rooting'].append({'from': list(origin), 'to': [x, z] if kept else None,
                                  'reason': original_reason if (x, z) != origin else reason or 'original position',
                                  'evidenced': observed, 'observed_crown': list(observed_centre), 'attempts': attempts})
        if not kept:
            report['skipped'].append((*origin, r, reason))
            continue
        placed.update(kept)
        report['trees'].append((x, z, r, 'bush' if r < MIN_CROWN_R + 0.2 else 'tree'))
        kind, gy = base_kind[(x, z)]
        if kind == 'terrain':
            report['terrain']['used'] += 1
            if raised_over_terrain(world, x, z, gy):
                report['terrain']['moved'] += 1      # the live surface here is higher (a roof): the tree stands below it
        else:
            report['terrain']['fallback'] += 1       # no lidar terrain for this column: live surface, as before
    for p, b in placed.items():
        plan.set(*p, b)
    report['overhang'] = sum(1 for p in placed if p not in cell_xz)
    return placed, report


def review_image(cell: tuple[int, int], report: dict, out: Path) -> None:
    """Planned crowns drawn over the aerial crop, for the visual check."""
    from PIL import Image, ImageDraw

    tx, tz = cell
    x0, z0 = tx * CELL, tz * CELL
    from aerial_coverage import cell_image
    scale = 2
    img = cell_image(cell).resize((CELL * scale, CELL * scale))
    d = ImageDraw.Draw(img)
    for x, z, r, kind in report['trees']:
        cx, cy = (x - x0) * scale, (z - z0) * scale
        rr = r * scale
        d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=(255, 0, 255) if kind == 'tree' else (0, 255, 255), width=3)
    for x, z, r, why in report['skipped']:
        cx, cy = (x - x0) * scale, (z - z0) * scale
        d.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), outline=(255, 0, 0), width=2)
    for row in report.get('rooting', []):
        if row['to'] and row['from'] != row['to']:
            a, b = row['from'], row['to']
            d.line(((a[0] - x0) * scale, (a[1] - z0) * scale,
                    (b[0] - x0) * scale, (b[1] - z0) * scale), fill=(255, 255, 0), width=2)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)


def run(cell: tuple[int, int]) -> int:
    """Dry run: plan, run the kit's guards, write the review image. Never writes the world."""
    from site_kit import apply, fresh_planning_world, preview
    from layer_planning import plan_after_undo
    from layer_reports import artifact_path, reason_summary, write_report
    from aerial_coverage import require_coverage
    require_coverage(cell)

    tx, tz = cell
    name = plan_name(tx, tz)
    plan = Plan(f'{name}-dry')
    world = fresh_planning_world()
    undo = plan_after_undo('trees', cell, world)
    if undo.conflicts:
        print(f'  {len(undo.conflicts)} trees undo conflicts; planning against the unmodified world')
    placed, rep = plan_cell(cell, plan, undo.world, planning_inputs(cell).after_undo(undo.masked))
    print(f'{name}: crowns found {rep["detected"]} + dense fill {rep["candidates"] - rep["detected"]}  planted {len(rep["trees"])} '
          f'({sum(1 for t in rep["trees"] if t[3] == "tree")} trees, '
          f'{sum(1 for t in rep["trees"] if t[3] == "bush")} bushes)  skipped {len(rep["skipped"])}  '
          f'blocks {len(placed)}  overhang past cell edge {rep["overhang"]}')
    snaps = rep.get('crown_snaps') or []
    if snaps:
        x0, z0 = tx * CELL, tz * CELL
        n_moved = sum(1 for s in snaps if s['shift'] != (0, 0))
        print(f'  google-crown lidar snap: {n_moved}/{len(snaps)} moved '
              f'({100.0 * n_moved / len(snaps):.0f}%), radius {CROWN_SNAP_RADIUS}, '
              f'nDSM≥{CROWN_SNAP_MIN_NDSM_M:g} m')
    mapped_snaps = rep.get('mapped_snaps') or []
    if mapped_snaps:
        x0, z0 = tx * CELL, tz * CELL
        n_moved = sum(1 for s in mapped_snaps if s['status'] == 'moved')
        print(f'  mapped-tree lidar snap: {n_moved}/{len(mapped_snaps)} moved; '
              f'young/unconfirmed kept as small crowns (#129)')
    print(terrain_summary(rep))
    print(f"  skipped by reason: {reason_summary(w for *_pos, w in rep['skipped'])}")
    print(f"  mapped exclusions by reason: {reason_summary(r['reason'] for r in rep.get('mapped_rejections') or [])}")
    shunts = [r for r in rep['rooting'] if r['to'] and r['from'] != r['to']]
    print(f"  root adjustments: {len(shunts)}; by reason: {reason_summary(r['reason'] for r in shunts)}")
    write_report(name, {**rep, 'placement_positions': len(placed)}, dry=True)
    img = artifact_path(name, 'crowns.png', dry=True)
    review_image(cell, rep, img)
    print(f'  review image: {img}  (magenta tree, cyan bush, red skipped, yellow root adjustment)')
    artifact_path(name, 'plan.json', dry=True).write_text(json.dumps(
        {'trees': rep['trees'], 'skipped': rep['skipped'], 'blocks': len(placed),
         'rooting': rep['rooting'], 'crown_snaps': rep['crown_snaps'], 'mapped_snaps': rep['mapped_snaps']}, indent=1))
    if placed:
        preview(plan, artifact_path(name, 'preview.png', dry=True), scale=2)
        # the simulated undo reads this pass's own earlier build, so its name must be in `modifies`
        apply(plan, dry=True, modifies=(*ignored_builds(cell), name))
    else:
        print('  no preview written: empty trees plan')
    return 0


def apply_cell(cell: tuple[int, int]) -> int:
    """Live apply: lock first, re-plan from the saved world, readback, then the mask.

    Planning reads the region files right after a strict server save (``fresh_planning_world``), as
    the ground apply does (#111, ADR 004); the readback stays on MCP. Planning from the live store
    instead hung for tens of minutes: the cell warm-up (152 x 152 columns by the probe's Y range) is
    over 30 times the live store's cache cap, so it evicted itself and every later read was its own
    paced section scan.
    """
    import os
    from site_kit import apply, fresh_planning_world
    from stage_timing import timed_stage

    if not os.environ.get('SITE_OWNER'):
        raise SystemExit('SITE_OWNER is not set; claim the lock first (scripts/site_lock.py claim <owner> ...)')
    from readiness_evidence import require_ready
    tx, tz = cell
    name = plan_name(tx, tz)
    require_ready([cell], 'trees', redoing=(name,))    # a re-apply may pass its own mismatch (#284)
    plan = Plan(name)
    world = fresh_planning_world(strict=True)
    with timed_stage('layer planning (includes surface warm-up)'):
        placed, rep = plan_cell(cell, plan, world)
    from layer_reports import reason_summary, write_report
    print(terrain_summary(rep))
    print(f"  skipped by reason: {reason_summary(w for *_pos, w in rep['skipped'])}")
    print(f"  mapped exclusions by reason: {reason_summary(r['reason'] for r in rep.get('mapped_rejections') or [])}")
    shunts = [r for r in rep['rooting'] if r['to'] and r['from'] != r['to']]
    print(f"  root adjustments: {len(shunts)}; by reason: {reason_summary(r['reason'] for r in shunts)}")
    write_report(name, {**rep, 'placement_positions': len(placed)})
    if not placed:
        raise SystemExit('nothing to plant; check the dry run')
    # air-only is asserted now, from the same world the plan was read from, and again here
    mask = build_mask('trees', cell, name, placed,
                      lambda p: is_air(world.block(*p)) or 'leaves' in bare(world.block(*p)))   # leaf-on-leaf merge
    print(f'{name}: {len(placed)} blocks, {len(rep["trees"])} crowns')
    rb = apply(plan, modifies=ignored_builds(cell), stage='trees', cell=cell,
               strict=True, meta={'crowns': len(rep['trees']), 'rooting': rep['rooting']}, mask=mask)
    print(f'readback: {rb["planned"]} planned, {rb["mismatches"]} mismatches')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cell', required=True, help='tx,tz e.g. 48,-44')
    ap.add_argument('--apply', action='store_true', help='LIVE: needs SITE_OWNER + claimed lock')
    a = ap.parse_args()
    cell = tuple(int(v) for v in a.cell.split(','))
    from aerial_coverage import CoverageError
    try:
        return apply_cell(cell) if a.apply else run(cell)
    except CoverageError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
