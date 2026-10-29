#!/usr/bin/env python3
"""Generic vector-map footprint builder for non-house building features.

Use for odd masses (SU, halls, sheds, campus lumps) — not for houses/terraces
(those stay on ``house_build.py``). Shares ``site_kit`` preview/apply.

A run is a JSON spec in ``site/builds/<name>.json``:

  name           plan name (also the cell journal plan / feature id)
  notes          provenance (free text, never read by code)
  modifies       earlier builds this one may overwrite (default [])
  ways           {"<map way id>": "<label>"} footprints to build
  storeys        {"<label>": <int >= 1>} clear storeys (each STOREY_H blocks high)
  roofs          {"<label>": {"kind": "flat"|"pitched", "material": "<slab mat>",
                   "edge"?: {"block": "<mat>", "where": "outer"|"all"},
                   "zone_materials"?: {"<level_m_AOD>": "<mat>", ...},
                   "material_regions"?: [{"level": <m_AOD>, "polygon": [[x,z], ...]}]}}
                 Absent edge = no roof edging. Present: block is the parapet /
                 coping mat; where "outer" skips zone steps, "all" edges every
                 step. Flat-roof overhang fringe only when edge is set.
                 Optional zone_materials overrides ``material`` per lidar
                 plateau level (metres AOD, same keys as ``zones[].levels``).
                 Pitched roofs may set material_regions [{level, polygon}]
                 from photo/aerial evidence to assign a roof's material across
                 lidar-zone boundaries. Unmapped columns retain zone lookup.
  groups         [{"nos": [labels], "mode": "each"|"group", "front": "N"|"S"|"E"|"W"}]
  wall / floor   wall and interior floor materials (defaults bricks / smooth_stone)
  floor_min      minimum floor y or null
  step_max       stoop outward look (default 4)
  ledge_stair    rising stair on 1-block ledges beyond 4 columns (default false)
  door_frame     doors need a wall cell each side (default true)
  keep_edits     stoop/step cells survive earlier-build clash filter (default false)
  facade_plan    optional per-label per-face plan (entrances + glazing slots):
                   {"<label>": {
                     "faces": {"N"|"S"|"E"|"W": {
                       "glazing"?: [{storey, glazed_h, pier, wall?, rhythm?}],
                       "roof_edge"?: null|note, "detail"?: []}},
                     "entrances": [{
                       "id": "<stable>", "portal_id"?: "<register id>",
                       "face"?: "N"|"S"|"E"|"W", "leaves"?: 1|2,
                       "block"?: "<*_door>", "at"?: [x, z],
                       "awning_like"?: {"kind": "none"|"awning"|"covered_area"|
                                        "porch_roof", "depth"?: >=0},
                       "steps"?: >=0, "floor_offset"?: int,
                       "cell"?: [tx, tz],
                       "provenance": {source, confidence, evidence_ids,
                                      unresolved_reason}}]}}
                 Portals are inputs, not a stage: every register portal on the
                 footprint must be placed or declared unresolved (non-empty
                 ``provenance.unresolved_reason``). Provenance is
                 ``lib/site/provenance.py`` (#151). Worlds with no portal
                 register still list photo-read entrances. Build places doors
                 from placed entrances only (unresolved are listed, not built);
                 door floor y is outside ground + ``floor_offset`` and never
                 below outside ground (I-017). The old per-portal ``doors`` map
                 and front/rear spruce default are removed (#146).
                 Glazing may live under ``faces[].glazing`` (#130); window
                 frames are build-time (#131); roof/facade greeblies stay in
                 ``details`` / ``roofs.edge`` (#132) — entrance awning_like is
                 here (not a tree canopy; legacy entrance key ``canopy`` is
                 refused).
  Windows use facade_plan[label].faces[face].glazing only. Panes need solid
  framing on both sides, above and below; missing frames are reported. The
  dry run prints glazed share per exposed face/storey and marks missing rows
  as no evidence read. Legacy top-level window tables are refused.
  details        optional per-label roof/facade greeblies (building-review):
                   {"<label>": [
                     {"kind": "<roof|facade kind>", "at": [x, y, z],
                      "size": [sx, sy, sz], "source": "<evidence note>",
                      "face"?: "N"|"S"|"E"|"W"}]}
                 Roof kinds: plant_room, vent, louvred_vent, skylight,
                 solar_panel, lift_overrun. Facade kinds: drainpipe, sign,
                 awning_like, ledge, fascia_band (``face`` required). Entrance
                 awning_like / covered area belongs on ``facade_plan``
                 entrances, not here. Legacy details kind ``canopy`` is
                 refused. Placed after the shell and roof; dry run prints
                 each detail and its source. Roof detail columns without solid
                 roof below are clipped and reported. Positions from aerial (roofs) /
                 street photos (facades); large plant heights from lidar.
  exclude        optional per-label boxes cut out of the map footprint (outline larger
                 than the real building, or neighbours sharing the outline):
                   {"<label>": [[x0, z0, x1, z1], ...]}   inclusive block coords
  zones          optional per-label lidar roof plateaus within one footprint:
                   {"<label>": {"from": "lidar",
                     "levels": [<m AOD>, ...] | "auto",
                     "bucket"?: 4, "axis_deg"?: "auto" | <deg>}}
                 Splits the mass into roof zones (5×5 median DSM, snap to
                 plateau levels, mode-filter, bucket on the building's rotated
                 grid). Walls rise from local lidar ground so downhill faces gain
                 storeys.                  ``roofs[label].kind == "flat"``: each zone is a flat
                 deck at its plateau. ``kind == "pitched"``: within each zone,
                 where the DSM rises above the plateau (a ridge), stair / slab /
                 full-block slopes run from the zone eave up to the ridge
                 (stairs face upslope; shape from same-Y stair neighbours;
                 flat plateaus stay flat). Optional ``roofs[label].edge`` places a parapet on
                 outer edges or on every zone step (default: none).
                 Optional ``roofs[label].zone_materials`` sets a different mat
                 per plateau. Dry run samples the aerial mosaic over each zone
                 and prints mean colour + nearest ``site_kit`` preview colour
                 (suggestion only). After the lidar-mass intersect, disconnected
                 pieces smaller than ``ZONE_MIN_FOOTPRINT_CELLS`` are dropped
                 (#153); dry run lists each (cell count and position). Prints
                 ``footprint osm=N readable=M pad=P cover=C%``. Facades/doors/
                 roofs reuse the same label keys.

Path-in-footprint dry-run: scripts/footprint_overlap.py (also printed below).
Evidence contact sheet after apply: ``python3 scripts/evidence_sheet.py <name>``
(portal-facing + top-down + aerial; records path on ``*-result.json``; handoff
``end`` refuses FEATURE without it).

Usage:  python3 scripts/feature_build.py site/builds/<name>.json [--apply [--overwrite]]

``--apply`` refuses a plan with a landed, not-undone build in the journal (a re-apply
leaves old blocks outside the new plan); undo it first, or pass ``--overwrite``.
Begun / mismatched entries do not block (site_recover / reconcile own those).
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, deque
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as _sw

from script_env import ROOT, TOOL_ROOT  # noqa: E402
import footprint_overlap  # noqa: E402
import osm_footprints  # noqa: E402
from chimney import chimney_layers, validate_chimney
import facade_plan as facade_plan_mod  # noqa: E402
import provenance as provenance_mod  # noqa: E402
from site_kit import (  # noqa: E402
    Plan, apply, bid, fresh_planning_world, guard_door_frames, guard_roof_jogs,
    nearest_preview_colour, preview,
)
from world_frame import metres_aod_to_y  # noqa: E402

STOREY_H = 3
GROUND = (
    'minecraft:stone', 'minecraft:gray_terracotta', 'minecraft:polished_granite',
    'minecraft:polished_diorite', 'minecraft:light_gray_concrete', 'minecraft:grass_block',
    'minecraft:dirt', 'minecraft:coarse_dirt', 'minecraft:gravel',
)
DIRS = {'N': (0, -1), 'S': (0, 1), 'W': (-1, 0), 'E': (1, 0)}
N8 = [(dx, dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1) if dx or dz]
N4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
FACE = {'S': 'south', 'N': 'north', 'W': 'west', 'E': 'east'}
TOWARD = {'S': 'north', 'N': 'south', 'W': 'east', 'E': 'west'}
ROOF_KINDS = frozenset({'flat', 'pitched'})
EDGE_WHERE = frozenset({'outer', 'all'})
ZONE_FROM = frozenset({'lidar'})
ROOF_DETAIL_KINDS = frozenset({
    'plant_room', 'vent', 'louvred_vent', 'skylight', 'solar_panel', 'lift_overrun', 'chimney',
})
FACADE_DETAIL_KINDS = frozenset({
    'drainpipe', 'sign', 'awning_like', 'ledge', 'fascia_band',
})
DETAIL_KINDS = ROOF_DETAIL_KINDS | FACADE_DETAIL_KINDS


# ---- lidar roof zones (pure helpers; fixture grids in tests) --------------------

# nDSM (DSM − DTM) at or above this (metres) counts as building mass for zoned
# footprints. Bare ground inside the vector outline is dropped (#116).
ZONE_LIDAR_MASS_M = 3.0

# Minimum 4-connected cells to keep a disconnected footprint piece after the
# lidar-mass intersect. Smaller islands (1–2 cells) otherwise grow walls and a
# stone cap (#153). Raise this constant if a real small structure must be kept.
ZONE_MIN_FOOTPRINT_CELLS = 3


def _win(a: np.ndarray, k: int, fn) -> np.ndarray:
    """Apply ``fn`` over a k×k sliding window with edge padding.

    @param a: 2-D array
    @param k: odd window size
    @param fn: reducer over the last two axes (e.g. ``np.median``, ``np.max``)
    @returns: same shape as ``a``
    """
    pad = k // 2
    return fn(_sw(np.pad(a, pad, mode='edge'), (k, k)), axis=(2, 3))


def dilate_mask(mask: np.ndarray, k: int = 3) -> np.ndarray:
    """Boolean dilate (max filter).

    Uses constant-0 padding so a mass that touches the array rim does not
    grow when the window hangs off the edge (edge-pad would treat outside as
    solid and turn close/open into a halo).

    @param mask: boolean footprint
    @param k: odd window size
    @returns: dilated boolean mask
    """
    pad = k // 2
    a = np.pad(mask.astype(np.uint8), pad, constant_values=0)
    return _sw(a, (k, k)).max(axis=(2, 3)).astype(bool)


def erode_mask(mask: np.ndarray, k: int = 3) -> np.ndarray:
    """Boolean erode (min filter).

    Constant-0 padding (see ``dilate_mask``).

    @param mask: boolean footprint
    @param k: odd window size
    @returns: eroded boolean mask
    """
    pad = k // 2
    a = np.pad(mask.astype(np.uint8), pad, constant_values=0)
    return _sw(a, (k, k)).min(axis=(2, 3)).astype(bool)


def square_footprint(outline: np.ndarray) -> np.ndarray:
    """Kill 1-block stairs: morphological close then open (no fixed halo).

    Works on a temporary False-padded copy so masses that touch the array
    rim are not eaten by constant-0 erode (and not grown into a halo).

    @param outline: boolean vector-way footprint mask
    @returns: readable massing mask (same shape as ``outline``)
    """
    pad = 2
    m = np.pad(outline.astype(bool), pad, constant_values=False)
    readable = erode_mask(dilate_mask(m))
    readable = dilate_mask(erode_mask(readable))
    return readable[pad:-pad, pad:-pad]


def drop_small_footprint_pieces(
    mask: np.ndarray,
    xs: np.ndarray,
    zs: np.ndarray,
    min_cells: int = ZONE_MIN_FOOTPRINT_CELLS,
) -> tuple[np.ndarray, list[dict]]:
    """Keep 4-connected footprint components with at least ``min_cells`` cells.

    Small disconnected pieces after the lidar-mass test are dropped so they do
    not grow walls. A spur that touches the main mass is the same component and
    is kept.

    @param mask: boolean footprint (``[z, x]``)
    @param xs: world x per cell (same shape)
    @param zs: world z per cell (same shape)
    @param min_cells: keep components of this size or larger
    @returns: ``(kept_mask, dropped)`` where each dropped dict has ``n`` (cell
        count) and ``at`` ``(x, z)`` of the lexicographically first world cell
    """
    kept = np.zeros_like(mask, dtype=bool)
    dropped: list[dict] = []
    seen = np.zeros_like(mask, dtype=bool)
    depth, width = mask.shape
    for r in range(depth):
        for c in range(width):
            if not mask[r, c] or seen[r, c]:
                continue
            comp: list[tuple[int, int]] = []
            q = deque([(r, c)])
            seen[r, c] = True
            while q:
                zr, xc = q.popleft()
                comp.append((zr, xc))
                for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    nr, nc = zr + dr, xc + dc
                    if (
                        0 <= nr < depth and 0 <= nc < width
                        and mask[nr, nc] and not seen[nr, nc]
                    ):
                        seen[nr, nc] = True
                        q.append((nr, nc))
            if len(comp) >= min_cells:
                for zr, xc in comp:
                    kept[zr, xc] = True
            else:
                cells_xz = [(int(xs[zr, xc]), int(zs[zr, xc])) for zr, xc in comp]
                dropped.append({'n': len(comp), 'at': min(cells_xz)})
    return kept, dropped


def dropped_footprint_line(label: str, piece: dict) -> str:
    """Dry-run line for one dropped disconnected footprint piece.

    @param label: feature label
    @param piece: dict with ``n`` and ``at`` ``(x, z)``
    @returns: ``dropped disconnected footprint <label> n=N at (x,z)``
    """
    x, z = piece['at']
    return f'dropped disconnected footprint {label} n={piece["n"]} at ({x},{z})'


def snap_to_levels(heights: np.ndarray, levels: list[int] | list[float]) -> np.ndarray:
    """Snap each cell to the nearest plateau level.

    @param heights: height grid (e.g. 5×5 median DSM, metres AOD)
    @param levels: plateau levels in the same units
    @returns: integer (or float) level per cell, same shape as ``heights``
    """
    lv = np.asarray(levels, dtype=float)
    if lv.size == 0:
        raise ValueError('levels must be non-empty')
    idx = np.abs(heights[..., None] - lv).argmin(-1)
    return lv[idx]


def mode_filter(zone: np.ndarray, mask: np.ndarray, k: int = 5) -> np.ndarray:
    """Replace each masked cell with the mode of its k×k neighbourhood.

    @param zone: discrete level grid
    @param mask: cells to filter (outside left unchanged)
    @param k: odd window size
    @returns: mode-filtered copy of ``zone``
    """
    pad = k // 2
    zp = np.pad(np.where(mask, zone, np.nan), pad, constant_values=np.nan)
    out = zone.copy()
    rows, cols = zone.shape
    for r in range(rows):
        for c in range(cols):
            if not mask[r, c]:
                continue
            w = zp[r:r + k, c:c + k].ravel()
            w = w[~np.isnan(w)]
            if w.size:
                out[r, c] = Counter(w.tolist()).most_common(1)[0][0]
    return out


def auto_plateau_levels(med: np.ndarray, mask: np.ndarray, min_cells: int = 8) -> list[int]:
    """Derive plateau levels from rounded median DSM inside the mask.

    @param med: 5×5 median DSM (metres AOD)
    @param mask: readable footprint
    @param min_cells: drop sparse metre bins
    @returns: sorted integer metres AOD
    """
    if not mask.any():
        return []
    vals = np.round(med[mask]).astype(int)
    counts = Counter(vals.tolist())
    levels = sorted(v for v, n in counts.items() if n >= min_cells)
    return levels or sorted(counts.keys())


def footprint_axis_deg(xs: np.ndarray, zs: np.ndarray) -> float:
    """Long-axis angle of a footprint from PCA (degrees, atan2 of z over x).

    @param xs: block x of footprint cells
    @param zs: block z of footprint cells
    @returns: degrees
    """
    pts = np.column_stack([xs.astype(float), zs.astype(float)])
    pts -= pts.mean(axis=0)
    if len(pts) < 2:
        return 0.0
    cov = pts.T @ pts / len(pts)
    _evals, evecs = np.linalg.eigh(cov)
    vx, vz = evecs[:, -1]
    return float(math.degrees(math.atan2(vz, vx)))


def bucket_zones(
    zone: np.ndarray,
    mask: np.ndarray,
    xs: np.ndarray,
    zs: np.ndarray,
    bucket: int = 4,
    axis_deg: float = 0.0,
) -> np.ndarray:
    """One level per bucket cell on the building's rotated grid (straight zone edges).

    @param zone: snapped level grid (same indexing as ``mask``)
    @param mask: readable footprint
    @param xs: world x per cell (same shape as ``zone``)
    @param zs: world z per cell (same shape as ``zone``)
    @param bucket: bucket size in blocks along the rotated axes
    @param axis_deg: building long-axis degrees
    @returns: bucketed zone grid
    """
    if bucket < 1:
        raise ValueError('bucket must be >= 1')
    ca, sa = math.cos(math.radians(axis_deg)), math.sin(math.radians(axis_deg))
    u = xs * ca + zs * sa
    v = -xs * sa + zs * ca
    key = np.floor(u / bucket).astype(int) * 100_000 + np.floor(v / bucket).astype(int)
    out = zone.copy()
    for k in np.unique(key[mask]):
        sel = (key == k) & mask
        out[sel] = Counter(zone[sel].tolist()).most_common(1)[0][0]
    return out


def assign_roof_zones(
    dsm: np.ndarray,
    outline: np.ndarray,
    levels: list[int] | list[float] | str,
    xs: np.ndarray,
    zs: np.ndarray,
    bucket: int = 4,
    axis_deg: float | str = 'auto',
    ground_y: np.ndarray | None = None,
    mass_m: float = ZONE_LIDAR_MASS_M,
) -> tuple[np.ndarray, np.ndarray, list, float, list[dict]]:
    """Full zone pipeline: square → lidar-mass → drop small islands → median → snap → mode → bucket.

    Arrays share the same 2-D indexing (row/col). ``xs``/``zs`` hold world block
    coordinates for each cell (needed for rotated bucketing). When ``ground_y`` is
    set, readable cells also need nDSM (DSM − DTM) ≥ ``mass_m`` so bare ground
    inside the vector outline is dropped (#116). After that intersect, 4-connected
    pieces smaller than ``ZONE_MIN_FOOTPRINT_CELLS`` are dropped (#153).
    ``exclude`` boxes stay a separate override applied before this pipeline.

    @param dsm: DSM metres AOD
    @param outline: boolean vector-way footprint
    @param levels: plateau list or ``\"auto\"``
    @param xs: world x grid
    @param zs: world z grid
    @param bucket: rotated-grid bucket size
    @param axis_deg: degrees or ``\"auto\"`` (PCA of readable cells)
    @param ground_y: optional block-Y DTM grid (same shape as ``dsm``)
    @param mass_m: nDSM threshold in metres (default ``ZONE_LIDAR_MASS_M``)
    @returns: ``(readable, zone, levels_used, axis_deg_used, dropped)`` where
        ``dropped`` lists small islands removed (see ``drop_small_footprint_pieces``)
    """
    from world_frame import y_to_metres_aod
    readable = square_footprint(outline)
    if ground_y is not None:
        gy = np.asarray(ground_y)
        ground_m = np.vectorize(y_to_metres_aod, otypes=[float])(gy.astype(int))
        readable = readable & ((dsm.astype(float) - ground_m) >= mass_m)
    readable, dropped = drop_small_footprint_pieces(readable, xs, zs)
    if not readable.any():
        return readable, np.zeros_like(dsm, dtype=float), ([] if levels == 'auto' else list(levels)), 0.0, dropped
    med = _win(dsm.astype(float), 5, np.median)
    if levels == 'auto':
        levels_used: list = auto_plateau_levels(med, readable)
    else:
        levels_used = list(levels)
    if not levels_used:
        raise ValueError('no plateau levels (empty list / auto found none)')
    zone = snap_to_levels(med, levels_used)
    zone = mode_filter(zone, readable)
    if axis_deg == 'auto':
        axis_used = footprint_axis_deg(xs[readable], zs[readable]) if readable.any() else 0.0
    else:
        axis_used = float(axis_deg)
    zone = bucket_zones(zone, readable, xs, zs, bucket=bucket, axis_deg=axis_used)
    return readable, zone, levels_used, axis_used, dropped


def zone_ridge_half_steps(
    med: np.ndarray,
    zone: np.ndarray,
    readable: np.ndarray,
    flat_tol_m: float = 1.0,
) -> np.ndarray:
    """Half-block rises above each zone plateau for pitched roofs.

    Per snapped level, if the median DSM stays within ``flat_tol_m`` of the
    plateau, every cell in that zone stays at the eave (flat deck). Otherwise
    each cell rises ``max(0, round((med - level) * 2))`` half-steps (1 m ≈ 1
    block), so a lidar ridge becomes stair / slab / full-block courses instead
    of stepped flat terraces.

    @param med: 5×5 median DSM metres AOD
    @param zone: snapped plateau grid (same shape)
    @param readable: footprint mask
    @param flat_tol_m: max rise that still counts as a flat plateau
    @returns: int half-steps above the zone eave (0 outside readable / flat zones)
    """
    out = np.zeros(zone.shape, dtype=int)
    if not readable.any():
        return out
    med_f = med.astype(float)
    for level in np.unique(zone[readable]):
        sel = readable & (zone == level)
        if not sel.any():
            continue
        rise_m = med_f - float(level)
        if float(np.nanmax(rise_m[sel])) < flat_tol_m:
            continue
        half = np.maximum(0, np.rint(rise_m * 2).astype(int))
        out[sel] = half[sel]
    return out


def edge_wall_expose(roof: dict, ground: dict, cell: tuple[int, int]) -> int:
    """Lowest neighbour top (roof if built-on, else ground); wall faces above this.

    Matches the R-013 column rule: walls run from local ground up, but only the
    band above the shortest neighbouring top is an exterior face.

    @param roof: ``{(x, z): roof_y}`` built columns
    @param ground: ``{(x, z): ground_y}`` (at least neighbourhood of ``cell``)
    @param cell: column to evaluate
    @returns: expose y — wall blocks with ``y > expose`` face outside / a step
    """
    def top_of(q: tuple[int, int]) -> int:
        if q in roof:
            return roof[q]
        return ground.get(q, -999)

    x, z = cell
    return min(top_of((x + dx, z + dz)) for dx, dz in N4)


def footprint_pad_line(outline: np.ndarray, readable: np.ndarray) -> str:
    """Format the bake-off footprint pad line.

    @param outline: vector-way boolean mask
    @param readable: squared readable mask
    @returns: ``footprint osm=N readable=M pad=P cover=C%`` (ticket-mandated wording)
    """
    n_outline = int(outline.sum())
    n_read = int(readable.sum())
    pad = int((readable & ~outline).sum())
    cover = 100.0 * (readable & outline).sum() / n_outline if n_outline else 0.0
    return f'footprint osm={n_outline} readable={n_read} pad={pad} cover={cover:.2f}%'


def cells_to_mask(cells: set, x0: int, z0: int, width: int, depth: int) -> np.ndarray:
    """Rasterise ``(x, z)`` cells onto a ``[z, x]`` boolean grid (row = z).

    @param cells: footprint columns
    @param x0: min x of grid
    @param z0: min z of grid
    @param width: size in x
    @param depth: size in z
    @returns: boolean array shape ``(depth, width)``
    """
    mask = np.zeros((depth, width), dtype=bool)
    for x, z in cells:
        mask[z - z0, x - x0] = True
    return mask


def mask_to_cells(mask: np.ndarray, x0: int, z0: int) -> set:
    """Inverse of ``cells_to_mask``.

    @param mask: ``[z, x]`` boolean grid
    @param x0: min x
    @param z0: min z
    @returns: set of ``(x, z)``
    """
    zs, xs = np.nonzero(mask)
    return {(int(x0 + x), int(z0 + z)) for z, x in zip(zs, xs)}


def pane(axis: str) -> str:
    """Glass pane block state spanning the given horizontal axis.

    @param axis: ``'x'`` or ``'z'``
    @returns: pane block state string
    """
    return 'glass_pane[east=true,west=true]' if axis == 'x' else 'glass_pane[north=true,south=true]'


def stair(mat: str, facing: str, half: str = 'bottom') -> str:
    """Stair block state.

    @param mat: stair material prefix (e.g. ``stone``, ``spruce``)
    @param facing: facing direction name
    @param half: ``bottom`` or ``top``
    @returns: stair block state string
    """
    return f'{mat}_stairs[facing={facing},half={half}]'


# Cardinal deltas for pitched-roof upslope detection.
_ROOF_DELTA = {'east': (1, 0), 'west': (-1, 0), 'south': (0, 1), 'north': (0, -1)}
# Roof materials with no stair / slab block in vanilla 1.21 (never invent another mat).
_ROOF_NO_STAIRS = frozenset({'smooth_stone'})
_ROOF_NO_SLAB = frozenset()
_ROOF_FULL_NAME = {
    'brick': 'bricks',
    'mud_brick': 'mud_bricks',
    'deepslate_tile': 'deepslate_tiles',
    'stone_brick': 'stone_bricks',
}


def block_family_variants(mat: str) -> dict[str, str]:
    """Full / slab / stairs short names for a roof material family.

    Always includes ``full``. ``slab`` and ``stairs`` are present only when that
    variant exists for ``mat`` (vanilla roof set; never substitutes another
    material).

    @param mat: roof material prefix (e.g. ``brick``, ``smooth_stone``)
    @returns: ``{'full': ...}`` plus optional ``slab`` / ``stairs``
    """
    mat = mat.replace('minecraft:', '').split('[', 1)[0]
    if mat.endswith('_slab'):
        mat = mat[: -len('_slab')]
    if mat.endswith('_stairs'):
        mat = mat[: -len('_stairs')]
    full = _ROOF_FULL_NAME.get(mat, mat)
    out = {'full': full}
    if mat not in _ROOF_NO_SLAB:
        out['slab'] = f'{mat}_slab'
    if mat not in _ROOF_NO_STAIRS:
        out['stairs'] = f'{mat}_stairs'
    return out


def _roof_stair(mat: str, facing: str, shape: str = 'straight', half: str = 'bottom') -> str:
    """Pitched-roof stair state (bottom half, waterlogged false).

    @param mat: stair material prefix
    @param facing: upslope facing
    @param shape: ``straight`` / ``inner_*`` / ``outer_*``
    @param half: ``bottom`` or ``top``
    @returns: block state string
    """
    return (
        f'{mat}_stairs[facing={facing},half={half},shape={shape},waterlogged=false]'
    )


def _roof_slab(mat: str, typ: str) -> str:
    """Pitched-roof slab state (waterlogged false).

    @param mat: slab material prefix
    @param typ: ``bottom`` or ``top``
    @returns: block state string
    """
    return f'{mat}_slab[type={typ},waterlogged=false]'


_ROOF_FACE_ORDER = ('north', 'east', 'south', 'west')


def regularise_roof_half_heights(
    half_heights: dict[tuple[int, int], int],
    levels: dict[tuple[int, int], int] | None = None,
) -> dict[tuple[int, int], int]:
    """Lower cliffs so neighbouring columns differ by at most 2 half-steps.

    Lidar is only a guide for roofs: a Minecraft stair skin cannot bridge a
    taller jump in one column, so peaks drop toward their neighbours until
    every N4 step within a roof zone is 0, 1 or 2 half-blocks. Distinct zoned
    plateaus keep their intentional wall-backed height changes.

    @param half_heights: ``{(x, z): half_height}`` raw membrane tops
    @param levels: optional semantic zone level per column; only equal levels join
    @returns: new dict with the same keys, cliffs pulled down
    """
    h = dict(half_heights)
    pending = deque(h)
    queued = set(h)
    while pending:
        x, z = pending.popleft()
        queued.remove((x, z))
        for dx, dz in N4:
            c = (x + dx, z + dz)
            if (c in h and h[c] > h[(x, z)] + 2
                    and (levels is None or levels[c] == levels[(x, z)])):
                h[c] = h[(x, z)] + 2
                if c not in queued:
                    pending.append(c)
                    queued.add(c)
    return h


def pitched_roof_states(
    half_heights: dict[tuple[int, int], int],
    materials: dict[tuple[int, int], str],
    warnings: list[str] | None = None,
    levels: dict[tuple[int, int], int] | None = None,
) -> dict[tuple[int, int], str]:
    """Choose stair / slab / full block states for a pitched-roof heightfield.

    ``half_heights`` is the membrane top in half-blocks (``2 * y`` for a full
    block or bottom-slab course at ``y``, ``2 * y + 1`` for a top slab). Heights
    are regularised first (N4 steps at most 2 within a zone). A rise of 2
    half-steps toward a neighbour is a stair facing upslope; hip eaves with only a diagonal rise
    also get a stair. Stair ``shape`` comes from stairs at the **same block Y**
    (vanilla neighbour rule via ``roof_lint.stair_shape_from_neighbours``), not
    from height differences on the diagonal. Flat / ridge cells use a full
    block (even) or top slab (odd). Missing stair/slab variants fall back to
    the family's full block and append a warning.

    @param half_heights: ``{(x, z): half_height}``
    @param materials: ``{(x, z): material prefix}`` (must cover every column)
    @param warnings: optional list that receives fallback messages
    @param levels: optional semantic zone levels preserving intentional plateau steps
    @returns: ``{(x, z): block state}`` (place at ``y = half_height // 2`` after
        regularisation)
    """
    from roof_lint import stair_shape_from_neighbours

    half_heights = regularise_roof_half_heights(half_heights, levels)
    warn = warnings if warnings is not None else []
    fam_cache: dict[str, dict[str, str]] = {}
    out: dict[tuple[int, int], str] = {}

    def fam(mat: str) -> dict[str, str]:
        if mat not in fam_cache:
            fam_cache[mat] = block_family_variants(mat)
        return fam_cache[mat]

    def fallback(mat: str, want: str) -> str:
        f = fam(mat)
        warn.append(f'roof material {mat!r} has no {want}; using full block {f["full"]}')
        return f['full']

    def h_at(x: int, z: int) -> int | None:
        return half_heights.get((x, z))

    def ups_at(x: int, z: int, h: int) -> list[str]:
        return [
            face for face in _ROOF_FACE_ORDER
            if (nh := h_at(x + _ROOF_DELTA[face][0], z + _ROOF_DELTA[face][1])) is not None
            and nh >= h + 2
        ]

    def ortho_pair_of(ups: list[str]) -> tuple[str, str] | None:
        for i, a in enumerate(ups):
            for b in ups[i + 1:]:
                ax, az = _ROOF_DELTA[a]
                bx, bz = _ROOF_DELTA[b]
                if ax * bx + az * bz == 0:
                    return (a, b)
        return None

    # Pass 1: facing only (shape is game-owned from same-Y neighbours).
    facings: dict[tuple[int, int], str] = {}
    for (x, z), h in half_heights.items():
        ups = ups_at(x, z, h)
        pair = ortho_pair_of(ups)
        if len(ups) == 1:
            facings[(x, z)] = ups[0]
        elif pair is not None:
            facings[(x, z)] = pair[0]
        elif not ups:
            diag_pair = None
            for a in _ROOF_FACE_ORDER:
                for b in _ROOF_FACE_ORDER:
                    if a >= b:
                        continue
                    ax, az = _ROOF_DELTA[a]
                    bx, bz = _ROOF_DELTA[b]
                    if ax * bx + az * bz != 0:
                        continue
                    nh = h_at(x + ax + bx, z + az + bz)
                    if nh is not None and nh >= h + 2:
                        diag_pair = (a, b)
                        break
                if diag_pair:
                    break
            if diag_pair:
                facings[(x, z)] = diag_pair[0]

    # Pass 2: shape from same-Y stair neighbours; slabs / fulls elsewhere.
    for (x, z), h in half_heights.items():
        mat = materials[(x, z)]
        f = fam(mat)
        if (x, z) in facings:
            facing = facings[(x, z)]
            y = h // 2
            adjacent: dict[str, tuple[str, str] | None] = {}
            for face, (dx, dz) in _ROOF_DELTA.items():
                npos = (x + dx, z + dz)
                if (npos not in facings or half_heights[npos] // 2 != y
                        or 'stairs' not in fam(materials[npos])):
                    adjacent[face] = None
                else:
                    adjacent[face] = (facings[npos], 'bottom')
            shape = stair_shape_from_neighbours(facing, 'bottom', adjacent)
            if 'stairs' in f:
                out[(x, z)] = _roof_stair(mat, facing, shape=shape)
            else:
                out[(x, z)] = fallback(mat, 'stairs')
            continue

        if h % 2 == 1:
            if 'slab' in f:
                out[(x, z)] = _roof_slab(mat, 'top')
            else:
                out[(x, z)] = fallback(mat, 'slab')
        else:
            out[(x, z)] = f['full']

    return out


def load_spec(path) -> dict:
    """Read and validate a feature build spec; fill defaults.

    @param path: spec JSON path
    @returns: spec dict
    @raises SystemExit: on missing/invalid keys
    """
    spec = json.loads(Path(path).read_text())
    for k in ('name', 'ways', 'storeys', 'roofs', 'groups'):
        if k not in spec:
            raise SystemExit(f'{path}: missing "{k}"')
    spec.setdefault('modifies', [])
    spec.setdefault('floor_min', None)
    spec.setdefault('step_max', 4)
    spec.setdefault('ledge_stair', False)
    spec.setdefault('keep_edits', False)
    spec.setdefault('door_frame', True)
    spec.setdefault('wall', 'bricks')
    spec.setdefault('floor', 'smooth_stone')
    spec.setdefault('facade_plan', {})
    spec.setdefault('details', {})
    spec.setdefault('zones', {})
    spec.setdefault('exclude', {})
    if 'facades' in spec:
        raise SystemExit(f'{path}: "facades" was removed; use facade_plan[label].faces[face].glazing')
    if 'doors' in spec:
        raise SystemExit(
            f'{path}: "doors" was removed; place entrances on facade_plan '
            f'(lib/builds/facade_plan.py)'
        )
    labels = set(spec['ways'].values())
    for lab, n in spec['storeys'].items():
        if lab not in labels:
            raise SystemExit(f'{path}: storeys label {lab} has no way')
        if not isinstance(n, int) or n < 1:
            raise SystemExit(f'{path}: storeys[{lab}] must be int >= 1')
    for lab, roof in spec['roofs'].items():
        if lab not in labels:
            raise SystemExit(f'{path}: roofs label {lab} has no way')
        if not isinstance(roof, dict) or roof.get('kind') not in ROOF_KINDS or not roof.get('material'):
            raise SystemExit(f'{path}: roofs[{lab}] needs {{kind: flat|pitched, material: ...}}')
        _validate_roof_edge(path, lab, roof)
        _validate_zone_materials(path, lab, roof)
    for g in spec['groups']:
        if g['front'] not in DIRS or g.get('mode', 'each') not in ('each', 'group'):
            raise SystemExit(f'{path}: bad group {g}')
        g.setdefault('mode', 'each')
        for no in g['nos']:
            if no not in labels or no not in spec['storeys'] or no not in spec['roofs']:
                raise SystemExit(f'{path}: label {no} needs a way, storeys and roof')
    _validate_facade_plan(path, spec, labels)
    _validate_details(path, spec, labels)
    _validate_zones(path, spec, labels)
    _validate_exclude(path, spec, labels)
    _validate_material_regions(path, spec)
    return spec


def _validate_facade_plan(path, spec: dict, labels: set) -> None:
    """Validate optional ``facade_plan`` via ``lib/builds/facade_plan.py``.

    @param path: spec path (for error text)
    @param spec: feature spec being loaded
    @param labels: labels declared in ``ways``
    @raises SystemExit: on invalid facade_plan
    """
    try:
        facade_plan_mod.validate_facade_plan(
            spec.get('facade_plan') or {},
            labels=labels,
            storeys=spec.get('storeys') or {},
            portal_ids=None,
            path=f'{path}: facade_plan',
        )
    except facade_plan_mod.FacadePlanError as exc:
        raise SystemExit(str(exc)) from exc


def _validate_roof_edge(path, lab: str, roof: dict) -> None:
    """Validate optional ``roofs[label].edge`` (absent = no edging).

    When present must be ``{block: <mat>, where: outer|all}``. ``outer`` skips
    zone steps; ``all`` edges every step including zone drops.

    @param path: spec path (for error text)
    @param lab: roof label
    @param roof: roofs[lab] dict
    @raises SystemExit: on invalid edge entries
    """
    edge = roof.get('edge')
    if edge is None:
        return
    if not isinstance(edge, dict):
        raise SystemExit(f'{path}: roofs[{lab}].edge must be an object')
    block = edge.get('block')
    where = edge.get('where')
    if not isinstance(block, str) or not block.strip():
        raise SystemExit(f'{path}: roofs[{lab}].edge.block must be a non-empty material name')
    if where not in EDGE_WHERE:
        raise SystemExit(
            f'{path}: roofs[{lab}].edge.where must be one of {sorted(EDGE_WHERE)}')


def _validate_zone_materials(path, lab: str, roof: dict) -> None:
    """Validate optional ``roofs[label].zone_materials`` (per-plateau mat override).

    Keys are plateau levels (metres AOD, int or numeric string); values are
    non-empty material names. Absent = every zone uses ``roofs[label].material``.

    @param path: spec path (for error text)
    @param lab: roof label
    @param roof: roofs[lab] dict
    @raises SystemExit: on invalid zone_materials entries
    """
    zm = roof.get('zone_materials')
    if zm is None:
        return
    if not isinstance(zm, dict):
        raise SystemExit(f'{path}: roofs[{lab}].zone_materials must be an object')
    for key, mat in zm.items():
        try:
            float(key)
        except (TypeError, ValueError):
            raise SystemExit(
                f'{path}: roofs[{lab}].zone_materials key {key!r} must be a plateau level'
            ) from None
        if not isinstance(mat, str) or not mat.strip():
            raise SystemExit(
                f'{path}: roofs[{lab}].zone_materials[{key!r}] must be a non-empty material name'
            )


def _validate_material_regions(path, spec: dict) -> None:
    """Validate explicit pitched-roof membership; raster checks need the footprint.

    @param path: spec path used in diagnostics
    @param spec: validated roof and lidar-zone configuration
    @raises SystemExit: on malformed region, polygon or unknown plateau
    """
    for lab, roof in spec['roofs'].items():
        if 'material_regions' not in roof:
            continue
        rows = roof['material_regions']
        context = f'{path}: roofs[{lab}].material_regions'
        if not isinstance(rows, list):
            raise SystemExit(f'{context} must be a list')
        if roof['kind'] != 'pitched' or lab not in spec['zones']:
            raise SystemExit(f'{context} requires a pitched roof with zones')
        configured = spec['zones'][lab]['levels']
        for i, row in enumerate(rows):
            prefix = f'{context}[{i}]'
            if not isinstance(row, dict) or set(row) != {'level', 'polygon'}:
                raise SystemExit(f'{prefix} needs {{level, polygon}}')
            level = row['level']
            if isinstance(level, bool) or not isinstance(level, (int, float)) or not math.isfinite(level):
                raise SystemExit(f'{prefix}.level must be a finite plateau number')
            if configured != 'auto' and level not in configured:
                raise SystemExit(f'{prefix}: unknown plateau {level}')
            polygon = row['polygon']
            if not isinstance(polygon, list) or len(polygon) < 3:
                raise SystemExit(f'{prefix}.polygon needs at least three vertices')
            for point in polygon:
                if (not isinstance(point, list) or len(point) != 2
                        or any(isinstance(v, bool) or not isinstance(v, (int, float))
                               or not math.isfinite(v) for v in point)):
                    raise SystemExit(f'{prefix}.polygon vertices must be finite [x, z] numbers')
            if len({tuple(p) for p in polygon}) < 3:
                raise SystemExit(f'{prefix}.polygon needs three distinct vertices')


def roof_material_for_zone(roof: dict, level: int | float | None) -> str:
    """Resolve slab/full material for one roof column.

    Prefers ``zone_materials[level]`` (int or str key) when present; otherwise
    the label-wide ``material``.

    @param roof: roofs[label] dict
    @param level: plateau metres AOD (from ``zone_level``), or None
    @returns: material name without ``minecraft:``
    """
    default = roof['material']
    zm = roof.get('zone_materials') or {}
    if level is None or not zm:
        return default
    if level in zm:
        return zm[level]
    key = str(int(level)) if float(level) == int(level) else str(level)
    if key in zm:
        return zm[key]
    # JSON may keep "45.0"; accept that too
    if str(level) in zm:
        return zm[str(level)]
    return default


def roof_material_levels(
    roof: dict, levels: dict, *, label: str = 'roof',
    known_levels: list | None = None, reports: list[str] | None = None,
) -> dict:
    """Apply designer roof membership to material lookup keys, preserving zones.

    Polygons use the footprint rasterizer's block-centre convention and are
    clipped to the prepared roof columns. Unmapped columns retain their lidar
    zone. Overlapping regions are rejected rather than made order-dependent.

    @param roof: roofs[label] entry with optional material_regions
    @param levels: prepared column to lidar plateau map
    @param label: label used in diagnostics
    @param known_levels: declared or auto-discovered plateaus; otherwise observed zones
    @param reports: optional dry-run diagnostic lines to append
    @returns: independent column to material plateau map
    @raises SystemExit: on empty, overlapping or unknown-level regions
    """
    owners = dict(levels)
    assigned: set = set()
    known = set(known_levels if known_levels is not None else levels.values())
    if not roof.get('material_regions'):
        return owners
    if not levels:
        raise SystemExit(f'{label} material_regions: no prepared roof columns')
    from coverage import scan_polygon

    xs, zs = zip(*levels)
    bbox = (min(xs), min(zs), max(xs), max(zs))
    for i, row in enumerate(roof.get('material_regions') or []):
        level = float(row['level'])
        if level not in known:
            raise SystemExit(f'{label} material_regions[{i}]: unknown plateau {level:g}')
        polygon = row['polygon']
        cells = set(scan_polygon([polygon], bbox)) & levels.keys()
        if not cells:
            raise SystemExit(f'{label} material_regions[{i}]: no roof columns inside polygon')
        if cells & assigned:
            raise SystemExit(f'{label} material_regions[{i}]: overlapping roof columns')
        owners.update({c: level for c in cells})
        assigned.update(cells)
        if reports is not None:
            reports.append(f'{label} material region {i}: level {level:g} columns {len(cells)}')
    return owners


def mean_rgb_255(samples: list[tuple[float, float, float]]) -> tuple[int, int, int] | None:
    """Mean of 0..1 RGB samples as an 0..255 int triple.

    @param samples: list of (r, g, b) in 0..1
    @returns: (R, G, B) or None when empty
    """
    if not samples:
        return None
    n = len(samples)
    r = sum(s[0] for s in samples) / n
    g = sum(s[1] for s in samples) / n
    b = sum(s[2] for s in samples) / n
    return (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))


def sample_columns_rgb(pts: list[tuple[int, int]]) -> dict[tuple[int, int], tuple[float, float, float]]:
    """Aerial 0..1 RGB per column from the place mosaic / registered sources.

    Soft-fails to ``{}`` when the mosaic or CRS helpers are unavailable (dry-run
    suggestion only — never a gate).

    @param pts: MC columns to sample
    @returns: ``{(x, z): (r, g, b)}`` for columns that yielded a colour
    """
    if not pts:
        return {}
    try:
        from ground_sources import mosaic_px, registered_rgb, MOSAIC, NO_DATA_LUM
        from PIL import Image
    except ImportError:
        return {}
    try:
        im = np.asarray(Image.open(MOSAIC).convert('RGB')).astype(float) / 255
    except OSError:
        return {}
    out: dict[tuple[int, int], tuple[float, float, float]] = {}
    try:
        for p, (X, Y) in mosaic_px(list(pts)).items():
            X, Y = int(X), int(Y)
            if 2 <= X < im.shape[1] - 2 and 2 <= Y < im.shape[0] - 2:
                win = tuple(im[Y - 2:Y + 3, X - 2:X + 3].reshape(-1, 3).mean(0))
                if sum(win) / 3 < NO_DATA_LUM:
                    continue
                out[p] = (float(win[0]), float(win[1]), float(win[2]))
        out.update({p: (float(c[0]), float(c[1]), float(c[2]))
                    for p, c in registered_rgb(list(pts)).items()})
    except Exception:
        return out
    return out


def roof_zone_colour_lines(
    features: list,
    aerial_rgb: dict[tuple[int, int], tuple[float, float, float]] | None = None,
) -> list[str]:
    """Dry-run lines: mean aerial colour + nearest site_kit preview block per zone.

    Suggestion only — missing aerial / empty zone skips that zone silently.

    @param features: Feature objects; zoned ones need ``zone_level``
    @param aerial_rgb: optional injected ``{(x, z): (r, g, b) 0..1}`` (tests)
    @returns: printable report lines
    """
    lines: list[str] = []
    for f in features:
        zl = getattr(f, 'zone_level', None)
        if not zl:
            continue
        by_level: dict[int, list[tuple[int, int]]] = {}
        for c, level in zl.items():
            if c not in f.cells:
                continue
            by_level.setdefault(int(level), []).append(c)
        need = [c for cols in by_level.values() for c in cols]
        rgb_map = aerial_rgb if aerial_rgb is not None else sample_columns_rgb(need)
        for level in sorted(by_level):
            samples = [rgb_map[c] for c in by_level[level] if c in rgb_map]
            mean = mean_rgb_255(samples)
            if mean is None:
                continue
            name, _ = nearest_preview_colour(mean)
            lines.append(
                f'{f.no} zone {level}: roof colour mean rgb={mean} suggest {name}'
            )
    return lines


def _validate_zones(path, spec: dict, labels: set) -> None:
    """Validate optional per-label lidar roof-zone config.

    @param path: spec path (for error text)
    @param spec: feature spec being loaded
    @param labels: known way labels
    @raises SystemExit: on invalid zone entries
    """
    zones = spec.get('zones') or {}
    if not isinstance(zones, dict):
        raise SystemExit(f'{path}: zones must be an object')
    for lab, row in zones.items():
        if lab not in labels:
            raise SystemExit(f'{path}: zones label {lab} has no way')
        if not isinstance(row, dict):
            raise SystemExit(f'{path}: zones[{lab}] must be an object')
        if row.get('from') not in ZONE_FROM:
            raise SystemExit(f'{path}: zones[{lab}].from must be one of {sorted(ZONE_FROM)}')
        levels = row.get('levels')
        if levels != 'auto':
            if not isinstance(levels, list) or not levels:
                raise SystemExit(f'{path}: zones[{lab}].levels must be a non-empty list or \"auto\"')
            for lv in levels:
                if not isinstance(lv, (int, float)):
                    raise SystemExit(f'{path}: zones[{lab}].levels entries must be numbers')
        bucket = row.get('bucket', 4)
        if not isinstance(bucket, int) or bucket < 1:
            raise SystemExit(f'{path}: zones[{lab}].bucket must be int >= 1')
        row.setdefault('bucket', 4)
        axis = row.get('axis_deg', 'auto')
        if axis != 'auto' and not isinstance(axis, (int, float)):
            raise SystemExit(f'{path}: zones[{lab}].axis_deg must be \"auto\" or a number')
        row.setdefault('axis_deg', 'auto')
        roof = spec['roofs'].get(lab) or {}
        if roof.get('kind') not in ROOF_KINDS:
            raise SystemExit(
                f'{path}: zones[{lab}] needs roofs[{lab}].kind flat|pitched '
                f'(got {roof.get("kind")!r})'
            )


def _validate_exclude(path, spec: dict, labels: set) -> None:
    """Validate optional per-label footprint exclusion boxes.

    @param path: spec path (for error text)
    @param spec: feature spec being loaded
    @param labels: labels declared in ``ways``
    @raises SystemExit: on invalid boxes
    """
    exclude = spec.get('exclude') or {}
    if not isinstance(exclude, dict):
        raise SystemExit(f'{path}: exclude must be an object')
    for lab, boxes in exclude.items():
        if lab not in labels:
            raise SystemExit(f'{path}: exclude label {lab} has no way')
        if not isinstance(boxes, list):
            raise SystemExit(f'{path}: exclude[{lab}] must be a list of [x0, z0, x1, z1]')
        for box in boxes:
            ok = (isinstance(box, list) and len(box) == 4
                  and all(isinstance(v, int) and not isinstance(v, bool) for v in box)
                  and box[0] <= box[2] and box[1] <= box[3])
            if not ok:
                raise SystemExit(f'{path}: exclude[{lab}] box {box} must be [x0, z0, x1, z1] ints, min <= max')


def _validate_details(path, spec: dict, labels: set) -> None:
    """Validate optional per-label roof/facade detail (greeblie) rows.

    Each row needs ``kind``, ``at`` ``[x,y,z]``, ``size`` ``[sx,sy,sz]`` (>=1),
    and a non-empty ``source``. Facade kinds also need ``face`` in N/S/E/W.

    @param path: spec path (for error text)
    @param spec: feature spec being loaded
    @param labels: known way labels
    @raises SystemExit: on invalid details structure
    """
    details = spec.get('details') or {}
    if not isinstance(details, dict):
        raise SystemExit(f'{path}: details must be an object')
    for lab, rows in details.items():
        if lab not in labels:
            raise SystemExit(f'{path}: details label {lab} has no way')
        if not isinstance(rows, list):
            raise SystemExit(f'{path}: details[{lab}] must be a list')
        for i, row in enumerate(rows):
            pref = f'{path}: details[{lab}][{i}]'
            if not isinstance(row, dict):
                raise SystemExit(f'{pref} must be an object')
            kind = row.get('kind')
            if kind == 'canopy':
                raise SystemExit(
                    f'{pref} kind \'canopy\' is renamed to \'awning_like\' '
                    f'(allowed: {sorted(DETAIL_KINDS)})'
                )
            if kind not in DETAIL_KINDS:
                raise SystemExit(
                    f'{pref} kind must be one of {sorted(DETAIL_KINDS)}')
            at = row.get('at')
            if (
                not isinstance(at, (list, tuple)) or len(at) != 3
                or not all(isinstance(v, int) and not isinstance(v, bool) for v in at)
            ):
                raise SystemExit(f'{pref} at must be [x, y, z] ints')
            if kind == 'chimney':
                validate_chimney(row, pref)
            size = row.get('size')
            if (
                not isinstance(size, (list, tuple)) or len(size) != 3
                or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in size)
            ):
                raise SystemExit(f'{pref} size must be [sx, sy, sz] ints >= 1')
            source = row.get('source')
            if not isinstance(source, str) or not source.strip():
                raise SystemExit(f'{pref} source must be a non-empty string')
            if kind in FACADE_DETAIL_KINDS:
                face = row.get('face')
                if face not in DIRS:
                    raise SystemExit(f'{pref} face must be one of N/S/E/W')


class Feature:
    """One labelled footprint mass inside a feature build."""

    def __init__(self, no, cells, group, storeys, roof):
        self.no = no
        self.cells = cells
        self.group = group
        self.storeys = storeys
        self.roof = roof
        self.front = group['front']


class Build:
    """Plan a non-house feature from a validated spec."""

    def __init__(
        self,
        spec: dict,
        footprints: dict | None = None,
        world=None,
        osm_root=None,
        zone_data: dict | None = None,
        aerial_rgb: dict | None = None,
    ):
        """
        @param spec: validated feature spec
        @param footprints: optional ``{label: set[(x,z)]}`` (else OSMIndex from ways)
        @param world: optional ``World``-like with ``block(x,y,z)`` (else
            :func:`site_kit.fresh_planning_world` — save then on-disk region files; #111)
        @param osm_root: optional vector-map clip root for ``OSMIndex`` (unit-test /
            fixture trees only). Production always uses this world's
            ``reference/vector_map`` via ``vector_map_clips.vector_map_clip_root``;
            do not point at another world's clips.
        @param zone_data: optional ``{label: {dsm, ground_y, x0, z0}}`` grids for tests
            (arrays indexed ``[z, x]`` / row=z; ``ground_y`` is block Y; ``dsm`` metres AOD).
            Unzoned labels may inject ground_y without dsm. When omitted, zoned labels fetch composite lidar via ``lidar_provider.mc_heights``.
        @param aerial_rgb: optional ``{(x, z): (r, g, b) 0..1}`` for roof-colour
            dry-run lines (tests). When omitted, samples the place aerial mosaic.
        """
        self.spec = spec
        self.name = spec['name']
        self.groups = spec['groups']
        self.wall = spec['wall']
        self.floor_mat = spec['floor']
        self.keep = set()
        self.p = Plan(self.name)
        self.live = world if world is not None else fresh_planning_world()
        self.zone_data = zone_data or {}
        self.aerial_rgb = aerial_rgb
        by_no = {k: set(v) for k, v in (footprints or {}).items()}
        if footprints is None:
            idx = osm_footprints.OSMIndex(root=osm_root) if osm_root is not None else osm_footprints.OSMIndex()
            for (x, z), i in idx.cells().items():
                bp = idx.buildings[i][1]
                n = self.spec['ways'].get(str(bp.get('osm_way_id') or bp.get('osm_id')))
                if n:
                    by_no.setdefault(n, set()).add((x, z))
            self.idx = idx
        else:
            self.idx = None
        self.excluded: set = set()
        for lab, boxes in (spec.get('exclude') or {}).items():
            if lab in by_no:
                kept = {c for c in by_no[lab]
                        if not any(b[0] <= c[0] <= b[2] and b[1] <= c[1] <= b[3] for b in boxes)}
                self.excluded |= by_no[lab] - kept
                by_no[lab] = kept
        missing = [lab for lab in spec['ways'].values() if lab not in by_no or not by_no[lab]]
        if missing:
            raise SystemExit(f'{self.name}: no footprint cells for {missing}')
        self.features = []
        for g in self.groups:
            for no in g['nos']:
                self.features.append(Feature(no, by_no[no], g, spec['storeys'][no], spec['roofs'][no]))
        self.owner = {}
        for k, f in enumerate(self.features):
            for c in f.cells:
                self.owner[c] = k
        self.by_no_all = by_no
        self._zone_pad_lines: list[str] = []
        self._roof_warnings: list[str] = []
        self._pane_skips: dict[tuple[str, str], int] = {}
        self._detail_clip_lines: list[str] = []
        self._entrance_covers: list[tuple] = []

    def set(self, x, y, z, s):
        self.p.set(x, y, z, s)

    def _is_solid(self, x: int, y: int, z: int) -> bool:
        """True when the finished plan (or live world) has a non-air block here.

        @param x: block x
        @param y: block y
        @param z: block z
        @returns: whether the cell is a framing solid (not air)
        """
        st = self.p.cells.get((x, y, z))
        if st is None:
            st = self.live.block(x, y, z)
        return bool(st) and bid(st)[10:] not in ('air', 'cave_air', 'void_air')

    def _pane_framed(self, x: int, y: int, z: int, face: str) -> bool:
        """True when a pane at ``(x,y,z)`` has solid both sides along the wall and above/below.

        Wall-line sides follow the face: E/W for N/S faces, N/S for E/W faces.
        Neighbour glass (or other non-air) counts as solid so paired panes stay framed.

        @param x: pane x
        @param y: pane y
        @param z: pane z
        @param face: ``N``/``S``/``E``/``W`` the pane sits on
        @returns: whether the opening is framed
        """
        lat = ((1, 0), (-1, 0)) if face in 'NS' else ((0, 1), (0, -1))
        if not all(self._is_solid(x + dx, y, z + dz) for dx, dz in lat):
            return False
        return self._is_solid(x, y - 1, z) and self._is_solid(x, y + 1, z)

    def _note_pane_skip(self, label: str, face: str) -> None:
        """Count one unframed pane skipped on ``face`` of ``label``.

        @param label: feature label
        @param face: ``N``/``S``/``E``/``W``
        """
        key = (label, face)
        self._pane_skips[key] = self._pane_skips.get(key, 0) + 1

    def pane_frame_skip_report(self) -> list[str]:
        """Dry-run lines for panes left as wall because they lacked a solid frame.

        @returns: human-readable lines ``<label> face <F>: <n> panes skipped (no frame)``
        """
        return [
            f'{no} face {face}: {n} panes skipped (no frame)'
            for (no, face), n in sorted(self._pane_skips.items())
            if n
        ]

    def kset(self, x, y, z, st):
        """Set a stoop/step cell; with keep_edits it survives the earlier-build clash filter."""
        if self.spec['keep_edits']:
            self.keep.add((x, y, z))
        self.set(x, y, z, st)

    def _prefetch_live(self, positions) -> None:
        """Warm ``self.live`` for a known set of positions when it supports prefetch.

        Skips positions an overlay world (``PlannedWorld``) already serves from ``.cells``.
        No-op for stubs without ``prefetch``. Call only during plan construction — after
        ``mark_written`` live stores raise ``StaleRead``.

        @param positions: iterable of ``(x, y, z)``
        """
        warm = getattr(self.live, 'prefetch', None)
        if warm is None:
            return
        overlay = getattr(self.live, 'cells', None)
        if overlay is not None:
            positions = (p for p in positions if p not in overlay)
        warm(positions)

    def surf(self, x, z):
        for y in range(75, 30, -1):
            n = (self.live.block(x, y, z) or 'minecraft:air').split('[')[0]
            if n in ('minecraft:stone', 'minecraft:grass_block', 'minecraft:dirt', 'minecraft:coarse_dirt', 'minecraft:granite'):
                return y
            if n in GROUND:
                return None
        return None

    def floor_y(self, f: Feature) -> int:
        """Median lidar bare-terrain floor under the footprint, with floor_min override.

        @param f: feature whose footprint samples the DTM
        @returns: median ground block Y, raised by floor_min when specified
        """
        if f.no in self.zone_data:
            grid = self.zone_data[f.no]
            ground = np.asarray(grid['ground_y'], dtype=float)
            x0, z0 = int(grid['x0']), int(grid['z0'])
        else:
            import lidar_provider as lp
            from world_frame import load_frame, mc_grid_to_en
            xs, zs = zip(*f.cells)
            x0, z0, x1, z1 = min(xs), min(zs), max(xs), max(zs)
            lidar = lp.tile_client(download=False)
            if lidar.missing('DTM', *lp.mc_box_en(x0, z0, x1, z1)):
                raise SystemExit(f'{self.name} {f.no}: lidar DTM cache missing for floor')
            xx, zz = np.meshgrid(np.arange(x0, x1 + 1), np.arange(z0, z1 + 1))
            frame = load_frame()
            east, north = mc_grid_to_en(xx, zz, frame)
            ground = np.trunc(lidar.sample('DTM', east, north, True)) + frame.y_offset
        values = [float(ground[z - z0, x - x0]) for x, z in f.cells]
        if not all(math.isfinite(y) for y in values):
            raise SystemExit(f'{self.name} {f.no}: lidar DTM has no valid ground under footprint')
        y = int(statistics.median(values))
        return max(y, self.spec['floor_min']) if self.spec['floor_min'] is not None else y

    def _feature_has_zones(self, f: Feature) -> bool:
        """True when this label has a zones entry in the spec."""
        return f.no in (self.spec.get('zones') or {})

    def _load_zone_grids(self, f: Feature) -> dict:
        """DSM + ground_y grids for a zoned feature (injected or composite lidar).

        @param f: feature mass (uses current ``f.cells`` bbox)
        @returns: ``{dsm, ground_y, x0, z0}`` with ``[z, x]`` arrays
        """
        if f.no in self.zone_data:
            return self.zone_data[f.no]
        xs = [c[0] for c in f.cells]
        zs = [c[1] for c in f.cells]
        x0, x1 = min(xs) - 1, max(xs) + 1
        z0, z1 = min(zs) - 1, max(zs) + 1
        import lidar_provider as lp
        lidar = lp.tile_client(download=False)
        e0, n0, e1, n1 = lp.mc_box_en(x0, z0, x1, z1)
        if lidar.missing('DTM', e0, n0, e1, n1) or lidar.missing('DSM', e0, n0, e1, n1):
            raise SystemExit(
                f'{self.name}: lidar DTM/DSM cache missing for zones on {f.no} '
                f'bbox=({x0},{z0})-({x1},{z1})'
            )
        h = lp.mc_heights(lidar, x0, z0, x1, z1)
        # mc_heights is [x - x0, z - z0]; convert to [z, x] for zone helpers
        return {
            'x0': x0,
            'z0': z0,
            'dsm': np.asarray(h['dsm']).T,
            'ground_y': np.asarray(h['ground_y']).T,
        }

    def prepare_zones(self) -> None:
        """Square footprints and assign lidar roof zones for zoned labels.

        Expands ``f.cells`` to the readable mask, fills ``zone_roof`` /
        ``zone_ground`` / ``zone_level``, refreshes ``owner``, and records the
        pad line for dry-run print.
        """
        zones_spec = self.spec.get('zones') or {}
        if not zones_spec:
            return
        for f in self.features:
            if f.no not in zones_spec:
                continue
            cfg = zones_spec[f.no]
            grids = self._load_zone_grids(f)
            x0, z0 = int(grids['x0']), int(grids['z0'])
            dsm = np.asarray(grids['dsm'], dtype=float)
            ground = np.asarray(grids['ground_y'])
            depth, width = dsm.shape
            outline = cells_to_mask(f.cells, x0, z0, width, depth)
            zz, xx = np.mgrid[0:depth, 0:width]
            xs = xx + x0
            zs = zz + z0
            readable, zone, _levels, _axis, dropped = assign_roof_zones(
                dsm, outline, cfg['levels'], xs, zs,
                bucket=int(cfg.get('bucket', 4)),
                axis_deg=cfg.get('axis_deg', 'auto'),
                ground_y=ground,
            )
            self._zone_pad_lines.append(footprint_pad_line(outline, readable))
            for piece in dropped:
                self._zone_pad_lines.append(dropped_footprint_line(f.no, piece))
            if not readable.any():
                raise SystemExit(
                    f'{self.name} {f.no}: no zone cells survived; '
                    f'{ZONE_MIN_FOOTPRINT_CELLS}-cell minimum, mass threshold {ZONE_LIDAR_MASS_M:g} m (DSM - DTM)'
                )
            new_cells = mask_to_cells(readable, x0, z0)
            pitched = f.roof.get('kind') == 'pitched'
            half_grid = (
                zone_ridge_half_steps(_win(dsm.astype(float), 5, np.median), zone, readable)
                if pitched else None
            )
            roof: dict = {}
            half_h: dict = {}
            gy: dict = {}
            zl: dict = {}
            for z in range(depth):
                for x in range(width):
                    if not readable[z, x]:
                        continue
                    wx, wz = x0 + x, z0 + z
                    eave_y = metres_aod_to_y(float(zone[z, x]))
                    if half_grid is not None:
                        h = int(half_grid[z, x])
                        roof[(wx, wz)] = eave_y + h // 2
                        half_h[(wx, wz)] = 2 * eave_y + h
                    else:
                        roof[(wx, wz)] = eave_y
                    gy[(wx, wz)] = int(ground[z, x])
                    zl[(wx, wz)] = int(round(float(zone[z, x])))
            if pitched:
                half_h = regularise_roof_half_heights(half_h, zl)
                roof = {c: h // 2 for c, h in half_h.items()}
            # neighbourhood ground for expose() outside the mass
            for z in range(depth):
                for x in range(width):
                    wx, wz = x0 + x, z0 + z
                    gy.setdefault((wx, wz), int(ground[z, x]))
            f.cells = new_cells
            f.zone_roof = roof
            f.zone_roof_half = half_h if pitched else None
            f.zone_ground = gy
            f.zone_level = zl
            f.roof_material_level = (
                roof_material_levels(
                    f.roof, zl, label=f.no, known_levels=_levels,
                    reports=self._zone_pad_lines,
                ) if pitched else zl
            )
            if roof:
                f.b = int(statistics.median(gy[c] for c in roof))
        # rebuild ownership after readable expansion
        self.owner = {}
        for k, f in enumerate(self.features):
            for c in f.cells:
                self.owner[c] = k
        for f in self.features:
            self.by_no_all[f.no] = set(f.cells)

    def clear(self):
        cols = set()
        for (x, z) in self.owner:
            cols.update((x + dx, z + dz) for dx in (-2, -1, 0, 1, 2) for dz in (-2, -1, 0, 1, 2))
        self.cols = cols
        tops = []
        for f in self.features:
            if getattr(f, 'zone_roof', None):
                tops.append(max(f.zone_roof.values()) + 4)
            else:
                tops.append(f.b + f.storeys * STOREY_H + 8)
        top_clear = max(tops)
        work = []
        for x, z in cols:
            nearest = min(self.features, key=lambda f: min(max(abs(x - c[0]), abs(z - c[1])) for c in f.cells))
            if (x, z) in self.owner and getattr(nearest, 'zone_roof', None):
                continue  # shell_zoned owns these columns
            if (x, z) in self.foreign or (x, z) in self.shared:
                continue
            work.append((x, z, nearest.b, (x, z) in self.owner))
        need = []
        for x, z, b, own in work:
            if not own:
                need.extend((x, y, z) for y in range(b, top_clear + 1))
        self._prefetch_live(need)
        for x, z, b, own in work:
            if own:
                for y in range(37, b):
                    self.set(x, y, z, 'stone')
                rng = range(b, top_clear + 1)
            else:
                # Margin: clear only — no stone shelf at floor level (#115).
                # Door stoops are placed later by step_out / _set_door_leaf.
                rng = range(b, top_clear + 1)
            for y in rng:
                if own or (self.live.block(x, y, z) or 'minecraft:air').split('[')[0] not in GROUND:
                    self.set(x, y, z, 'air')

    def exposed(self, c, d) -> bool:
        dx, dz = DIRS[d]
        return (c[0] + dx, c[1] + dz) not in self.owner

    def shell(self, k: int, f: Feature):
        b = f.b
        own = f.cells
        height = f.storeys * STOREY_H
        f.walls = set()
        for c in own:
            for dx, dz in N8:
                n = (c[0] + dx, c[1] + dz)
                ko = self.owner.get(n)
                if ko is None or (ko != k and k < ko):
                    f.walls.add(c)
                    break
        f.inner = own - f.walls
        for x, z in own:
            for y in range(b - 4, b):
                self.set(x, y, z, 'stone')
        for x, z in f.walls:
            for y in range(b, b + height):
                self.set(x, y, z, self.wall)
        for x, z in f.inner:
            self.set(x, b, z, self.floor_mat)
            for y in range(b + 1, b + height):
                self.set(x, y, z, 'air')
            for s in range(1, f.storeys):
                self.set(x, b + s * STOREY_H, z, self.floor_mat)

    def shell_zoned(self, k: int, f: Feature) -> None:
        """Per-column shell from local lidar ground up to snapped roof zones.

        Walls only where a neighbour top is lower (exterior or zone step).
        Flat zones place a full-block deck at the plateau Y; pitched zones place
        stair / slab / full-block membrane (``zone_roof_half`` via
        ``pitched_roof_states``) rising from the eave toward a lidar ridge.
        Optional ``f.roof['edge']`` places a parapet (default: none).
        Interior floor slabs follow the facade storey rhythm from ``f.b``
        (median ground).

        @param k: feature index (ownership / wall rivalry)
        @param f: feature with ``zone_roof`` / ``zone_ground`` filled
        """
        del k  # ownership already baked into f.cells; kept for call-site parity
        roof = f.zone_roof
        halfs = getattr(f, 'zone_roof_half', None)
        gy = f.zone_ground
        zl = getattr(f, 'zone_level', None) or {}
        material_levels = getattr(f, 'roof_material_level', zl)
        edge = f.roof.get('edge')
        datum = f.b
        pitched_states: dict[tuple[int, int], str] | None = None
        if halfs is not None:
            mats = {
                c: roof_material_for_zone(f.roof, material_levels.get(c)) for c in halfs
            }
            pitched_states = pitched_roof_states(
                halfs, mats, warnings=self._roof_warnings, levels=zl,
            )

        def top_of(q: tuple[int, int]) -> int:
            return roof[q] if q in roof else gy.get(q, -999)

        def wants_edge(x: int, z: int, ry: int) -> bool:
            if not edge:
                return False
            if edge['where'] == 'all':
                return any(top_of((x + dx, z + dz)) < ry for dx, dz in N4)
            # outer: only columns that touch outside the roof mass (skip zone steps)
            return any((x + dx, z + dz) not in roof for dx, dz in N4)

        f.walls = set()
        for c, ry in roof.items():
            if any(top_of((c[0] + dx, c[1] + dz)) < ry for dx, dz in N4):
                f.walls.add(c)
            # also treat N8 ownership edge as wall for doors/facades
            for dx, dz in N8:
                if (c[0] + dx, c[1] + dz) not in roof:
                    f.walls.add(c)
                    break
        f.inner = set(roof) - f.walls
        for (x, z), ry in roof.items():
            g = gy[(x, z)]
            expose = edge_wall_expose(roof, gy, (x, z))
            rm = roof_material_for_zone(f.roof, material_levels.get((x, z)))
            for y in range(g - 4, g):
                self.set(x, y, z, 'stone')
            self.set(x, g, z, self.floor_mat)
            for y in range(g + 1, ry):
                if y > expose:
                    self.set(x, y, z, self.wall)
                elif (y - datum) % STOREY_H == 0 and y > g:
                    self.set(x, y, z, self.floor_mat)
                else:
                    self.set(x, y, z, 'air')
            if pitched_states is not None:
                self.set(x, ry, z, pitched_states[(x, z)])
            else:
                self.set(x, ry, z, block_family_variants(rm)['full'])
            if wants_edge(x, z, ry):
                self.set(x, ry + 1, z, edge['block'])
            else:
                self.set(x, ry + 1, z, 'air')
        if pitched_states is not None:
            self._back_roof_stair_edges(roof, pitched_states)

    def _back_roof_stair_edges(self, roof: dict, states: dict) -> None:
        """Close horizontal slits beside lower stairs inside a zoned roof mass.

        ``edge_wall_expose`` compares whole-block tops. At the lowest adjacent
        roof Y it can leave air below a taller column, even when a lower stair's
        upper half opens into that air. Back only those openings with wall;
        existing floors and exterior air are left alone.

        @param roof: membrane block Y per owned roof column
        @param states: membrane states (after same-Y stair shape derivation)
        """
        for (x, z), state in states.items():
            if '_stairs[' not in state:
                continue
            props = dict(kv.split('=', 1) for kv in state.split('[', 1)[1][:-1].split(','))
            facing = props['facing']
            shape = props.get('shape', 'straight')
            # A straight stair's upper half fills its facing side. Inner
            # corners also fill their left/right side; outer corners fill
            # only a quadrant, so every side has an open upper part.
            closed = {facing}
            if shape.startswith('outer_'):
                closed = set()
            elif shape.startswith('inner_'):
                turn = -1 if shape.endswith('_left') else 1
                closed.add(_ROOF_FACE_ORDER[(_ROOF_FACE_ORDER.index(facing) + turn) % 4])
            y = roof[(x, z)]
            for face, (dx, dz) in _ROOF_DELTA.items():
                neighbour = (x + dx, z + dz)
                if face in closed or roof.get(neighbour, y) <= y:
                    continue
                pos = (neighbour[0], y, neighbour[1])
                if self.p.cells.get(pos) == 'minecraft:air':
                    self.set(*pos, self.wall)

    def top_solid(self, x, z):
        for y in range(70, 40, -1):
            n = (self.live.block(x, y, z) or 'minecraft:air').split('[')[0]
            if n in GROUND and n != 'minecraft:stone_stairs':
                return y
        return None

    def step_out(self, st, d, b):
        dx, dz = DIRS[d]
        facing = TOWARD[d]
        cols = [(st[0] + dx * k, st[1] + dz * k) for k in range(1, self.spec['step_max'] + 1)]
        self._prefetch_live((x, y, z) for x, z in cols for y in range(70, 40, -1))
        for k in range(1, self.spec['step_max'] + 1):
            n = (st[0] + dx * k, st[1] + dz * k)
            if n in self.owner or n in self.foreign:
                continue
            g = self.top_solid(*n)
            if g is None:
                continue
            if self.spec['ledge_stair'] and g == b + 1 and k > 4:
                self.kset(n[0], g, n[1], stair('stone', FACE[d]))
                return
            if g > b + 1 or (g > b and k > 4):
                return
            if g > b:
                self.kset(n[0], b, n[1], 'stone')
                for y in range(b + 1, g + 1):
                    self.kset(n[0], y, n[1], 'air')
                continue
            if g < b:
                self.kset(n[0], b, n[1], stair('stone', facing))
                for y in range(g + 1, b):
                    self.kset(n[0], y, n[1], 'stone')
                for y in range(b + 1, b + 4):
                    self.kset(n[0], y, n[1], 'air')
                return

    def doors(self, k: int, f: Feature):
        """Place doors from ``facade_plan`` entrances for this feature's label.

        Unresolved entrances are printed and skipped. No plan entrances means
        no doors (legacy spruce / ``doors`` map removed in #146).

        @param k: feature index (unused; call-site parity)
        @param f: feature mass
        """
        del k
        self.plan_doors(f)

    def _door_jambs_solid(self, cell, face: str, b: int) -> bool:
        """True when both wall-line sides are solid at door height (guard rule).

        Uses block solidity like ``door_frame_issues`` / ``guard_door_frames``,
        not footprint ``f.walls`` membership (#164).

        @param cell: door wall ``(x, z)``
        @param face: ``N``/``S``/``E``/``W``
        @param b: floor y under the door
        @returns: whether both jamb columns are solid at ``b+1`` and ``b+2``
        """
        lat = ((1, 0), (-1, 0)) if face in 'NS' else ((0, 1), (0, -1))
        return all(
            self._is_solid(cell[0] + lx, b + 1, cell[1] + lz)
            and self._is_solid(cell[0] + lx, b + 2, cell[1] + lz)
            for lx, lz in lat
        )

    def _ensure_door_jambs(self, cell, face: str, b: int) -> None:
        """Build missing jamb solids at ``b+1``/``b+2`` and keep them (#164).

        On stepped / rotated faces a coplanar wall neighbour may be absent; the
        door stays on the register cell and the jambs are written into the plan.

        @param cell: door wall ``(x, z)``
        @param face: ``N``/``S``/``E``/``W``
        @param b: floor y under the door
        """
        lat = ((1, 0), (-1, 0)) if face in 'NS' else ((0, 1), (0, -1))
        for lx, lz in lat:
            jx, jz = cell[0] + lx, cell[1] + lz
            for y in (b + 1, b + 2):
                if not self._is_solid(jx, y, jz):
                    self.keep.add((jx, y, jz))
                    self.set(jx, y, jz, self.wall)

    def _entrance_wall_candidates(self, f: Feature, face: str, placed: set) -> list:
        """Wall cells on ``face`` with interior behind.

        Jamb framing is ensured at place time via ``_ensure_door_jambs`` (#164);
        do not drop nearer cells when a framed cell exists elsewhere on the face
        (that jumped doors ~14 blocks on stepped rotated walls).

        @param f: feature mass
        @param face: ``N``/``S``/``E``/``W``
        @param placed: wall cells already used by a door
        @returns: candidate ``(x, z)`` cells
        """
        dx, dz = DIRS[face]
        return [
            c for c in f.walls
            if c not in placed
            and self.exposed(c, face)
            and (c[0] - dx, c[1] - dz) in f.inner
        ]

    def _pick_entrance_wall(
        self, f: Feature, face: str, at, placed: set,
    ) -> tuple[int, int] | None:
        """Nearest wall cell on ``face`` to ``at`` (or mid-face when ``at`` absent).

        When choosing among equals by distance, prefer cells that already have
        solid jambs (same solidity rule as the door-frame guard).

        @param f: feature mass
        @param face: entrance face
        @param at: optional ``[x, z]`` from the plan
        @param placed: already used wall cells
        @returns: wall ``(x, z)`` or ``None``
        """
        cand = self._entrance_wall_candidates(f, face, placed)
        if not cand:
            return None

        def jamb_rank(c: tuple[int, int]) -> int:
            if not self.spec['door_frame']:
                return 0
            return 0 if self._door_jambs_solid(c, face, self._column_floor(f, c)) else 1

        if at is not None and isinstance(at, (list, tuple)) and len(at) == 2:
            ax, az = int(at[0]), int(at[1])
            return min(
                cand,
                key=lambda c: (
                    (c[0] - ax) ** 2 + (c[1] - az) ** 2,
                    jamb_rank(c),
                ),
            )
        if face in 'NS':
            mid = sum(c[0] for c in cand) / len(cand)
            return min(cand, key=lambda c: (abs(c[0] - mid), jamb_rank(c)))
        mid = sum(c[1] for c in cand) / len(cand)
        return min(cand, key=lambda c: (abs(c[1] - mid), jamb_rank(c)))

    def _outside_door_floor(
        self, f: Feature, cell, face: str, floor_offset: int,
    ) -> int:
        """Door floor from outside lidar ground; refuse stale live approaches (#204).

        @param f: feature mass
        @param cell: wall ``(x, z)``
        @param face: facing letter
        @param floor_offset: blocks relative to outside ground (0 = level)
        @returns: floor block y under the door
        """
        dx, dz = DIRS[face]
        st = (cell[0] + dx, cell[1] + dz)
        if f.no in self.zone_data:
            grid = self.zone_data[f.no]
            ground = np.asarray(grid['ground_y'])
            ix, iz = st[0] - grid['x0'], st[1] - grid['z0']
            if not (0 <= iz < ground.shape[0] and 0 <= ix < ground.shape[1]):
                raise ValueError(f'lidar ground unavailable at {st[0]},{st[1]}')
            outside = float(ground[iz, ix])
        else:
            import lidar_provider as lp
            from world_frame import load_frame, mc_grid_to_en
            lidar = lp.tile_client(download=False)
            if lidar.missing('DTM', *lp.mc_box_en(*st, *st)):
                raise ValueError(f'lidar DTM cache missing at {st[0]},{st[1]}')
            frame = load_frame()
            east, north = mc_grid_to_en(np.array([st[0]]), np.array([st[1]]), frame)
            outside = float(np.trunc(lidar.sample('DTM', east, north, True))[0] + frame.y_offset)
        if not math.isfinite(outside):
            raise ValueError(f'lidar ground unavailable at {st[0]},{st[1]}')
        outside = int(outside)
        self._prefetch_live((st[0], y, st[1]) for y in range(70, 40, -1))
        live_y = self.top_solid(*st)
        if live_y is None or abs(live_y - outside) > 3:
            raise ValueError(
                f'live y={live_y}, lidar y={outside} at {st[0]},{st[1]} '
                '(maximum difference 3 blocks)'
            )
        floor = self._column_floor(f, cell)
        top = (getattr(f, 'zone_roof', None) or {}).get(cell, f.b + f.storeys * STOREY_H)
        # Keep the opening below its roof/lintel.
        return min(max(outside + int(floor_offset), outside), max(floor, top - 3))

    def _column_floor(self, f: Feature, cell: tuple[int, int]) -> int:
        """Floor y at a column (local lidar ground when zoned, else ``f.b``).

        @param f: feature mass
        @param cell: ``(x, z)``
        @returns: floor block y
        """
        gy = getattr(f, 'zone_ground', None)
        if gy is not None and cell in gy:
            return int(gy[cell])
        return f.b

    def _set_door_leaf(self, cell, face: str, block: str, hinge: str, b: int):
        """Write one door leaf (lower+upper); clear outside stoop and inward vestibule.

        The inward clear (#143) stops ``door_to_wall`` when a storey floor sits at the
        door opening: air through the leaf height, floor underfoot left alone. Opening
        air/stoop cells are always added to ``keep`` so the earlier-build clash filter
        cannot drop them when ground already owns the column (live jubilee/west fail).
        Missing jamb solids at ``b+1``/``b+2`` are built and kept (#164) so stepped
        rotated walls stay on the register cell instead of jumping to a framed cell.

        @param cell: wall ``(x, z)``
        @param face: facing letter
        @param block: door block id (e.g. ``iron_door``)
        @param hinge: ``left`` or ``right``
        @param b: floor y
        """
        dx, dz = DIRS[face]
        face_name = FACE[face]
        for y, part in ((b + 1, 'lower'), (b + 2, 'upper')):
            self.set(cell[0], y, cell[1],
                     f'{block}[facing={face_name},half={part},hinge={hinge},open=false]')
        # Lintel on the wall column (shell may have left air at door height on stepped faces).
        self.keep.add((cell[0], b + 3, cell[1]))
        self.set(cell[0], b + 3, cell[1], self.wall)
        self._ensure_door_jambs(cell, face, b)
        st = (cell[0] + dx, cell[1] + dz)
        self.keep.add((st[0], b, st[1]))
        self.set(st[0], b, st[1], 'smooth_stone')
        for y in range(b + 1, b + 4):
            self.keep.add((st[0], y, st[1]))
            self.set(st[0], y, st[1], 'air')
        inn = (cell[0] - dx, cell[1] - dz)
        for y in range(b + 1, b + 4):
            self.keep.add((inn[0], y, inn[1]))
            self.set(inn[0], y, inn[1], 'air')

    def _pair_door_leaf(
        self, f: Feature, face: str, c0: tuple[int, int], placed: set, at,
    ) -> tuple[int, int] | None:
        """Lateral coplanar wall neighbour for a second leaf (#165).

        Only same-plane offsets (east/west on an N/S face, north/south on an E/W
        face). Diagonal cells across a step are never considered — they put the
        leaves in different planes and fail the frame guard. Step-corner walls
        that lack an ``inner`` cell behind still count: they are wall + exposed
        to ``face``. Jamb / step solids at ``b+1``/``b+2`` are built when the
        leaf is placed (``_ensure_door_jambs``).

        @param f: feature mass
        @param face: entrance face
        @param c0: primary leaf wall cell
        @param placed: wall cells already used by a door
        @param at: optional register ``[x, z]`` (tie-break toward it)
        @returns: pair ``(x, z)`` or ``None``
        """
        lat = (1, 0) if face in 'NS' else (0, 1)
        ax = int(at[0]) if isinstance(at, (list, tuple)) and len(at) == 2 else c0[0]
        az = int(at[1]) if isinstance(at, (list, tuple)) and len(at) == 2 else c0[1]
        opts = [
            (c0[0] + lat[0], c0[1] + lat[1]),
            (c0[0] - lat[0], c0[1] - lat[1]),
        ]
        opts.sort(key=lambda c: (c[0] - ax) ** 2 + (c[1] - az) ** 2)
        for c in opts:
            if c in placed or c not in f.walls:
                continue
            if not self.exposed(c, face):
                continue
            return c
        return None

    def plan_doors(self, f: Feature) -> None:
        """Place doors and queue entrance covers; skip unresolved entrances.

        @param f: feature mass
        """
        body = (self.spec.get('facade_plan') or {}).get(f.no) or {}
        rows = body.get('entrances') or []
        placed: set = set()
        f.door = None
        f.rear = None
        f.door_cells = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            eid = str(row.get('id') or '?')
            if provenance_mod.is_unresolved(row.get(provenance_mod.PROVENANCE_KEY)):
                reason = (row.get(provenance_mod.PROVENANCE_KEY) or {}).get(
                    'unresolved_reason',
                ) or ''
                print(f'door {eid}: unresolved ({reason})')
                continue
            face = row.get('face')
            leaves = row.get('leaves')
            block = row.get('block')
            if face not in DIRS or leaves not in (1, 2) or not isinstance(block, str):
                print(f'door {eid}: incomplete placed entrance: skipped')
                continue
            at = row.get('at')
            c0 = self._pick_entrance_wall(f, face, at, placed)
            if c0 is None:
                print(f'door {eid}: no wall on face {face}: skipped')
                continue
            if at is not None and isinstance(at, (list, tuple)) and len(at) == 2:
                ax, az = int(at[0]), int(at[1])
                moved = abs(c0[0] - ax) + abs(c0[1] - az)
                if moved:
                    print(
                        f'door {eid}: moved {moved} blocks from register '
                        f'{ax},{az} to {c0[0]},{c0[1]}'
                    )
            lat = (1, 0) if face in 'NS' else (0, 1)
            cells = [c0]
            if leaves == 2:
                pair = self._pair_door_leaf(f, face, c0, placed, at)
                if pair is None:
                    print(f'door {eid}: no pair cell; using 1-leaf')
                    leaves = 1
                else:
                    cells = sorted([c0, pair], key=lambda c: c[0] * lat[0] + c[1] * lat[1])
            offset = row.get('floor_offset')
            if not isinstance(offset, int) or isinstance(offset, bool):
                offset = 0
            try:
                floors = [self._outside_door_floor(f, c, face, offset) for c in cells]
            except ValueError as exc:
                print(f'door {eid}: refused ({exc})')
                continue
            b = floors[0]
            cover_y = max(floors) + 3
            hinges = ('left',) if leaves == 1 else ('left', 'right')
            for cell, hinge, leaf_b in zip(cells, hinges, floors):
                self._set_door_leaf(cell, face, block, hinge, leaf_b)
                placed.add(cell)
                f.door_cells.add(cell)
            cover = row.get('awning_like') or {}
            if cover.get('kind') in ('awning', 'covered_area', 'porch_roof'):
                self._entrance_covers.append((f, eid, cover, face, cells, cover_y))
            dx, dz = DIRS[face]
            self.step_out((cells[0][0] + dx, cells[0][1] + dz), face, b)
            if f.door is None:
                f.door = cells[0]
            print(
                f'door {eid} {leaves}-leaf {block} '
                f'at {cells[0][0]},{b + 1},{cells[0][1]} facing {FACE[face]}'
            )

    def entrance_covers(self) -> None:
        """Place covers queued by ``plan_doors`` after roof and detail passes.

        Slab roofs extend one block beyond each leaf edge. Porch posts stand
        at the two outer front corners, leaving the door approach walkable.

        @returns: None
        """
        for f, eid, cover, face, cells, roof_y in self._entrance_covers:
            depth = cover.get('depth') or 1
            kind = cover['kind']
            dx, dz = DIRS[face]
            lx, lz = (1, 0) if face in 'NS' else (0, 1)
            start = cells[0]
            width = len(cells)
            material = block_family_variants(f.roof.get('material') or 'smooth_stone')
            for lateral in range(-1, width + 1):
                for outward in range(1, depth + 1):
                    x = start[0] + lx * lateral + dx * outward
                    z = start[1] + lz * lateral + dz * outward
                    if (x, z) in self.by_no_all_cells:
                        continue
                    self.set(x, roof_y, z, f"{material['slab']}[type=bottom]")
                    if kind == 'porch_roof' and outward == depth and lateral in (-1, width):
                        ground_y = self.top_solid(x, z)
                        if ground_y is not None:
                            for y in range(ground_y + 1, roof_y):
                                self.set(x, y, z, self.wall)
            print(f'door {eid} cover {kind} depth {depth}')

    def face_runs(self, f: Feature, face: str) -> list[list]:
        """Contiguous straight wall runs exposed to ``face``, each sorted along the face.

        An L-shaped or notched mass yields several runs (one per constant edge
        coordinate), so pier rhythm applies per straight stretch — not across a
        courtyard notch. Corner cells stay in their run (craftsmanship end piers).

        @param f: feature mass
        @param face: ``N``/``S``/``E``/``W``
        @returns: list of ordered ``(x, z)`` runs
        """
        cand = [c for c in f.walls if self.exposed(c, face)]
        if not cand:
            return []
        by_edge: dict = {}
        if face in 'NS':
            for c in cand:
                by_edge.setdefault(c[1], []).append(c)
            along = 0
        else:
            for c in cand:
                by_edge.setdefault(c[0], []).append(c)
            along = 1
        runs = []
        for _edge, cells in sorted(by_edge.items()):
            cells = sorted(cells, key=lambda c: c[along])
            run = [cells[0]]
            for c in cells[1:]:
                prev = run[-1]
                if c[along] == prev[along] + 1:
                    run.append(c)
                else:
                    runs.append(run)
                    run = [c]
            runs.append(run)
        return runs

    def face_run(self, f: Feature, face: str) -> list:
        """All wall cells exposed to ``face`` (concat of ``face_runs``), for share totals.

        @param f: feature mass
        @param face: ``N``/``S``/``E``/``W``
        @returns: ordered ``(x, z)`` cells
        """
        out = []
        for run in self.face_runs(f, face):
            out.extend(run)
        return out

    @staticmethod
    def facade_roles(row: dict, n: int) -> list[str]:
        """Expand a facade row into per-cell roles (``W`` glaze, ``B`` balcony, ``_`` pier).

        Without ``rhythm``, uses 2-wide windows and ``pier`` solids; end cells are piers.

        @param row: facade table row
        @param n: face length in cells
        @returns: list of length ``n``
        """
        if n <= 0:
            return []
        rhythm = row.get('rhythm')
        if rhythm:
            chars = list(rhythm)
            roles = (chars * ((n // len(chars)) + 1))[:n]
        else:
            pier = row['pier']
            win_w = 2
            period = win_w + pier
            roles = []
            for i in range(n):
                if period > 0 and (i % period) < win_w:
                    roles.append('W')
                else:
                    roles.append('_')
            roles[0] = '_'
            if n > 1:
                roles[-1] = '_'
        return roles

    def _door_keepout(self, f: Feature) -> set:
        """Wall cells that must not become glass (every placed door leaf).

        @param f: feature mass
        @returns: set of ``(x, z)``
        """
        cells = set(getattr(f, 'door_cells', ()) or ())
        for q in (getattr(f, 'door', None), getattr(f, 'rear', None)):
            if q:
                cells.add(q)
        return cells

    def _column_roof_y(self, f: Feature, cell: tuple[int, int]) -> int | None:
        """Roof y for a column when zoned; ``None`` means uniform-storey shell.

        @param f: feature mass
        @param cell: ``(x, z)``
        @returns: roof block y or ``None``
        """
        roof = getattr(f, 'zone_roof', None)
        if roof is None:
            return None
        return roof.get(cell)

    def windows(self, f: Feature):
        """Punch openings from the facade table, or placeholder pier rhythm if absent.

        Panes need a solid frame (both wall-line sides plus above and below); otherwise
        the wall block is kept and the skip is counted for the dry-run report.
        """
        facades = facade_plan_mod.glazing_by_face(self.spec.get('facade_plan'), f.no)
        if not facades:
            self._windows_placeholder(f)
            return
        door = self._door_keepout(f)
        for face, rows in facades.items():
            runs = self.face_runs(f, face)
            if not runs:
                continue
            axis = 'z' if face in 'EW' else 'x'
            by_storey = {r['storey']: r for r in rows}
            for s in range(f.storeys):
                row = by_storey.get(s + 1)
                if not row:
                    continue
                wall_mat = row.get('wall') or self.wall
                glazed_h = row['glazed_h']
                base = f.b + s * STOREY_H
                for run in runs:
                    roles = self.facade_roles(row, len(run))
                    for c, role in zip(run, roles):
                        if c in door:
                            continue
                        ry = self._column_roof_y(f, c)
                        for dy in range(STOREY_H):
                            y = base + dy
                            if ry is not None and y >= ry:
                                continue
                            if role == 'W' and dy >= STOREY_H - glazed_h:
                                if self._pane_framed(c[0], y, c[1], face):
                                    self.set(c[0], y, c[1], pane(axis))
                                else:
                                    self._note_pane_skip(f.no, face)
                                    if wall_mat != self.wall:
                                        self.set(c[0], y, c[1], wall_mat)
                            elif wall_mat != self.wall:
                                self.set(c[0], y, c[1], wall_mat)

    def _windows_placeholder(self, f: Feature):
        """Legacy pier rhythm when the spec has no facade table for this label."""
        b = f.b
        door = self._door_keepout(f)
        for c in f.walls:
            exp = [d for d in 'NSEW' if self.exposed(c, d)]
            if not exp:
                continue
            d = exp[0]
            diag = len(exp) > 1
            lat = [(0, 1), (0, -1)] if d in 'EW' else [(1, 0), (-1, 0)]
            ok = True
            for lx, lz in ([] if diag else lat):
                n = (c[0] + lx, c[1] + lz)
                if n not in f.walls or not self.exposed(n, d):
                    ok = False
            if not ok:
                continue
            if c in door or any(abs(c[0] - q[0]) + abs(c[1] - q[1]) < 3 for q in door):
                continue
            par = (c[0] + c[1]) if diag else (c[0] if d in 'NS' else c[1])
            if par % (3 if diag else 2):
                continue
            axis = 'z' if d in 'EW' else 'x'
            ry = self._column_roof_y(f, c)
            for s in range(f.storeys):
                y = b + s * STOREY_H + 2
                if ry is not None and y >= ry:
                    continue
                if self._pane_framed(c[0], y, c[1], d):
                    self.set(c[0], y, c[1], pane(axis))
                else:
                    self._note_pane_skip(f.no, d)

    def glazed_share_report(self) -> list[str]:
        """Glazed share of each storey on every exposed face (dry-run photo compare).

        Lists every face with wall cells exposed that way, including faces
        with no facade table rows. Those are marked ``no evidence read``
        (report only — not a gate).

        @returns: human-readable lines
            ``<label> face <F> storey <n>: glazed a/b (p%)``
            or the same with `` — no evidence read`` when that face has no
            facade rows in the spec
        """
        lines = []
        for f in self.features:
            facades = facade_plan_mod.glazing_by_face(self.spec.get('facade_plan'), f.no) or {}
            for face in sorted('NSEW'):
                run = self.face_run(f, face)
                if not run:
                    continue
                has_rows = bool(facades.get(face))
                for s in range(f.storeys):
                    base = f.b + s * STOREY_H
                    total = glazed = 0
                    for c in run:
                        for dy in range(STOREY_H):
                            st = self.p.cells.get((c[0], base + dy, c[1]), '')
                            name = st.split('[')[0]
                            if name.endswith('_door') or name == 'air':
                                continue
                            total += 1
                            if 'glass' in name:
                                glazed += 1
                    if total == 0:
                        continue
                    pct = 100 * glazed // total
                    suffix = ' — no evidence read' if not has_rows else ''
                    lines.append(
                        f'{f.no} face {face} storey {s + 1}: '
                        f'glazed {glazed}/{total} ({pct}%){suffix}')
        return lines

    def roof_flat(self, features: list[Feature], own: set):
        """Flat roof membrane; optional overhang fringe when ``edge`` is set.

        Membrane uses ``roofs[].material`` slabs on owned columns. The one-block
        N8 overhang fringe is built only when ``roofs[].edge`` is present
        (default none). Pitched overhang is unchanged in ``roof_pitched``.

        @param features: group masses sharing this roof
        @param own: owned (x, z) columns for the roof deck
        """
        rm = features[0].roof['material']
        eave = features[0].b + features[0].storeys * STOREY_H
        rs = set(own)
        if features[0].roof.get('edge'):
            for (x, z) in own:
                for dx, dz in N8:
                    n = (x + dx, z + dz)
                    if n not in self.owner and n not in self.foreign and n not in self.shared:
                        rs.add(n)
        for x, z in rs:
            self.set(x, eave, z, f'{rm}_slab[type=bottom]')

    def roof_pitched(self, features: list[Feature], own: set):
        """Hip roof from the footprint edge: stairs upslope, full block at the ridge.

        Chebyshev distance from the roof outline sets a one-block rise per step
        (45°). ``pitched_roof_states`` picks stair facing/shape, slabs and full
        blocks for the roof material family.

        @param features: group masses sharing this roof
        @param own: owned (x, z) columns for the roof deck
        """
        rm = features[0].roof['material']
        b = features[0].b
        eave = b + features[0].storeys * STOREY_H
        rs = set(own)
        for (x, z) in own:
            for dx, dz in N8:
                n = (x + dx, z + dz)
                if n not in self.owner and n not in self.foreign and n not in self.shared:
                    rs.add(n)
        dist = {}
        front = [c for c in rs if any((c[0] + dx, c[1] + dz) not in rs for dx, dz in N8)]
        for c in front:
            dist[c] = 0
        q = list(front)
        while q:
            nq = []
            for c in q:
                for dx, dz in N8:
                    n = (c[0] + dx, c[1] + dz)
                    if n in rs and n not in dist:
                        dist[n] = dist[c] + 1
                        nq.append(n)
            q = nq
        half_heights = regularise_roof_half_heights(
            {c: 2 * (eave + d) for c, d in dist.items()},
        )
        materials = {c: rm for c in dist}
        states = pitched_roof_states(
            half_heights, materials, warnings=self._roof_warnings,
        )
        for (x, z), st in states.items():
            y = half_heights[(x, z)] // 2
            if (x, z) in self.owner:
                for yy in range(eave, y):
                    self.set(x, yy, z, self.wall)
            self.set(x, y, z, st)

    def roof(self):
        todo = []
        for g in self.groups:
            hs = [f for f in self.features if f.group is g and not getattr(f, 'zone_roof', None)]
            if not hs:
                continue
            if g['mode'] == 'group':
                todo.append((hs, set().union(*[f.cells for f in hs])))
            else:
                todo.extend(([f], f.cells) for f in hs)
        for hs, own in todo:
            kind = hs[0].roof['kind']
            if kind == 'flat':
                self.roof_flat(hs, own)
            else:
                self.roof_pitched(hs, own)

    def _prune_outside_guard_hits(self) -> None:
        """Drop outside fringe the vector-map guard flags, without wiping door stoops.

        Roof-overhang slabs are always removed. When a label is zoned, non-slab
        hits on columns owned by those zoned features are also removed (expanded
        readable mass the clip index may not cover). Stoops sit one step outside
        the mass, so they are kept — zoning must not delete every outside block.

        @returns: None
        """
        zoned_cols = {
            c for f in self.features
            if getattr(f, 'zone_roof', None)
            for c in f.cells
        }
        for q, v in osm_footprints.guard(self.p.cells)['outside'].items():
            name = v[0]
            if 'slab' in name or '_stairs' in name:
                del self.p.cells[q]
            elif (q[0], q[2]) in zoned_cols:
                del self.p.cells[q]

    def _foreign_cells(self) -> set:
        """Map cells of other buildings (not this build's, not clipped off by ``exclude``).

        @returns: cells to treat as someone else's footprint; empty without a map index
        """
        if self.idx is None:
            return set()
        return {c for c in self.idx.cells() if c not in self.owner and c not in self.excluded}

    def build(self) -> Plan:
        """Produce the offline plan for this feature build.

        @returns: ``site_kit.Plan``
        """
        self.foreign = self._foreign_cells()
        self.by_no_all_cells = set(self.owner) | self.foreign
        for f in self.features:
            if not self._feature_has_zones(f):
                f.b = self.floor_y(f)
        self.prepare_zones()
        self.by_no_all_cells = set(self.owner) | self.foreign
        self.shared = {(x + dx, z + dz) for (x, z) in self.foreign for dx, dz in N8} - self.foreign - set(self.owner)
        for g in self.groups:
            hs = [f for f in self.features if f.group is g and not getattr(f, 'zone_roof', None)]
            if g['mode'] == 'group' and hs:
                bb = min(f.b for f in hs)
                for f in hs:
                    f.b = bb
        self.clear()
        for k, f in enumerate(self.features):
            if getattr(f, 'zone_roof', None):
                self.shell_zoned(k, f)
            else:
                self.shell(k, f)
        self._pane_skips = {}
        for k, f in enumerate(self.features):
            self.doors(k, f)
        # Roof before windows so the eave slab can lintel the top glazed row
        # (_pane_framed needs solid above; wall shell stops at b+height-1).
        self.roof()
        for f in self.features:
            self.windows(f)
        if self.idx is not None:
            self._prune_outside_guard_hits()
        own1 = {(x + dx, z + dz) for (x, z) in self.owner for dx in (-1, 0, 1) for dz in (-1, 0, 1)}
        leaf_q = [q for q in self.p.cells if (q[0], q[2]) not in own1]
        self._prefetch_live(leaf_q)
        for q in list(self.p.cells):
            if (q[0], q[2]) not in own1 and 'leaves' in (self.live.block(*q) or ''):
                del self.p.cells[q]
        self.p.outside_footprint_ok = 'roof-overhang fringe slabs; evidence: footprint-hits json'
        # Door opening clears (#143) and keep_edits stoops: survive both this filter and apply clash.
        self.p.keep = set(self.keep)
        self._preserve_real_owners()
        # same refusals as the live apply, so the designer sees them before hand-off (#99)
        guard_door_frames(self.p, self.live.block)
        guard_roof_jogs(self.p)
        # Greeblies after the outside-slab prune so awning_like/ledge overhangs survive.
        self.details()
        self.entrance_covers()
        self._preserve_real_owners()
        for line in self._zone_pad_lines:
            print(line)
        for line in self._roof_warnings:
            print(line)
        for f in self.features:
            bits = [
                f.no, 'b', f.b, 'storeys', f.storeys, 'cells', len(f.cells),
                'door', getattr(f, 'door', None), 'rear', getattr(f, 'rear', None),
            ]
            if getattr(f, 'zone_roof', None):
                bits.append(f'zones={sorted(set(f.zone_level.values()))}')
            print(*bits)
        for line in self.pane_frame_skip_report():
            print(line)
        for line in self.glazed_share_report():
            print(line)
        for line in roof_zone_colour_lines(self.features, aerial_rgb=self.aerial_rgb):
            print(line)
        for line in self.detail_report():
            print(line)
        for line in self.footprint_overlap_report():
            print(line)
        valid_ids = {
            line for line in (TOOL_ROOT / 'config/minecraft-block-ids-26.2.txt').read_text().splitlines()
            if line and not line.startswith('#')
        }
        counts = Counter(state.split('[', 1)[0] for state in self.p.cells.values())
        for name, count in sorted(counts.items()):
            if name not in valid_ids:
                print(f'unknown block id {name} x{count} (source: vendored Minecraft 26.2 client blockstates; config/minecraft-block-ids-26.2.txt)')
        return self.p

    def _preserve_real_owners(self) -> None:
        """Keep real-build cells intact after shell and late detail placement."""
        # cells of earlier real builds are theirs (ghost owners yield: the shell overwrites them)
        from cell_registry import view_registry
        owned = view_registry().owned(self.p.cells, exclude={self.name}, real_only=True)
        for q in [q for q, st in self.p.cells.items() if q in owned and owned[q][1] != st and q not in self.keep]:
            del self.p.cells[q]

    def footprint_overlap_report(self) -> list[str]:
        """Path / open-lidar / aerial overlap lines for each feature mass (dry-run).

        Uses the live vector-map index when present. Synthetic ``footprints=`` builds (unit
        tests) skip unless streets were injected via ``self._overlap_streets``.

        @returns: printable report lines (may be empty)
        """
        streets = getattr(self, '_overlap_streets', None)
        if streets is None and self.idx is not None:
            streets = footprint_overlap.streets_from_index(self.idx)
        if streets is None:
            return []
        lines: list[str] = []
        for f in self.features:
            lines.extend(footprint_overlap.report_for_cells(
                set(f.cells), streets=streets, label=str(f.no),
            ))
        return lines

    def detail_report(self) -> list[str]:
        """Dry-run lines for each planned roof/facade detail and its source.

        @returns: printable report lines (may be empty)
        """
        lines: list[str] = []
        for f in self.features:
            for row in (self.spec.get('details') or {}).get(f.no) or []:
                ax, ay, az = row['at']
                sx, sy, sz = row['size']
                face = row.get('face')
                face_bit = f' face={face}' if face else ''
                lines.append(
                    f"detail {row['kind']} {f.no} at ({ax},{ay},{az}) "
                    f"size {sx}x{sy}x{sz}{face_bit} source={row['source']}"
                )
        return lines + self._detail_clip_lines

    def _detail_box(self, at: list | tuple, size: list | tuple):
        """Yield ``(x, y, z)`` cells in an inclusive min-corner box.

        @param at: ``[x, y, z]`` min corner
        @param size: ``[sx, sy, sz]`` extents (>=1)
        @returns: iterator of block coords
        """
        ax, ay, az = at
        sx, sy, sz = size
        for dx in range(sx):
            for dy in range(sy):
                for dz in range(sz):
                    yield (ax + dx, ay + dy, az + dz)

    def _solidify_roof_under(
        self, at: list | tuple, size: list | tuple, mat: str, supported: set[tuple[int, int]],
    ) -> None:
        """Replace slab/stair membrane under a roof detail so it does not hover.

        @param at: detail min corner
        @param size: detail size
        @param mat: full-block material for the pad
        @param supported: roof-supported horizontal cells of this detail
        """
        ax, ay, az = at
        sx, _sy, sz = size
        for dx in range(sx):
            for dz in range(sz):
                x, z = ax + dx, az + dz
                if (x, z) not in supported:
                    continue
                below = (x, ay - 1, z)
                st = self.p.cells.get(below, '')
                name = st.split('[')[0] if st else ''
                if any(tok in name for tok in ('_slab', '_stairs')):
                    self.set(x, ay - 1, z, mat)

    def _place_detail(self, f: Feature, row: dict, roof_cells: set[tuple[int, int, int]]) -> None:
        """Place one validated detail recipe after the shell and roof.

        @param f: feature mass owning the label
        @param row: details table row
        @param roof_cells: finished shell/roof snapshot, before any details
        """
        kind = row['kind']
        at = row['at']
        size = row['size']
        face = row.get('face')
        ax, ay, az = at
        sx, sy, sz = size
        rm = f.roof.get('material') or 'smooth_stone'

        if kind == 'chimney':
            layers = chimney_layers(row)
            base = layers[0][1] | layers[0][2]
            off = sorted((x, az0) for x, az0 in base if (x, ay - 1, az0) not in roof_cells)
            if off:
                self._detail_clip_lines.append(f'detail chimney {f.no} at ({ax},{ay},{az}) cells off roof={off}; not built')
                return
            for x, z in base:
                self._solidify_roof_under((x, ay, z), (1, 1, 1), block_family_variants(rm)['full'], {(x, z)})
            for y, ring, interior in layers:
                material = row.get('material', 'white_concrete')
                band = row.get('band') or {}
                if band.get('offset') == y - ay:
                    material = band['block']
                if y == ay + row['height'] - 1 and row.get('rim'):
                    material = row['rim']
                for x, z in ring:
                    self.set(x, y, z, material)
                for x, z in interior:
                    self.set(x, y, z, 'air')
            lidar_top = 'unavailable'
            if f.no in self.zone_data and 'dsm' in self.zone_data[f.no]:
                grid = self.zone_data[f.no]
                lidar_top = metres_aod_to_y(float(grid['dsm'][az - grid['z0'], ax - grid['x0']]))
            else:
                import lidar_provider as lp
                client = lp.tile_client(download=False)
                if not any(client.missing(product, *lp.mc_box_en(ax, az, ax, az)) for product in ('DTM', 'DSM')):
                    lidar_top = int(lp.mc_heights(client, ax, az, ax, az)['top_y'][0, 0])
            self._detail_clip_lines.append(
                f'chimney {f.no} at ({ax},{ay},{az}) planned height={row["height"]} '
                f'diameter={row["base_diameter"]}->{row["top_diameter"]} '
                f'top={ay + row["height"] - 1}; lidar top={lidar_top} (rough check only; photo/spec dimensions win)'
            )
            return

        if kind == 'skylight':
            off = [(ax + dx, ay, az + dz) for dx in range(sx) for dz in range(sz)
                   if (ax + dx, ay, az + dz) not in roof_cells
                   or any(x == ax + dx and z == az + dz and y > ay for x, y, z in roof_cells)]
            if off:
                self._detail_clip_lines.append(
                    f'detail skylight {f.no} at ({ax},{ay},{az}) cells off roof={off}; not built'
                )
                return
            for dx in range(sx):
                for dz in range(sz):
                    self.set(ax + dx, ay, az + dz, 'glass')
            return

        supported = None
        if kind in ROOF_DETAIL_KINDS:
            # Only the shell/roof can support a detail: an earlier recipe must
            # not turn an unsupported column into support for a later one.
            supported = set()
            for dx in range(sx):
                for dz in range(sz):
                    x, z = ax + dx, az + dz
                    if (x, ay - 1, z) in roof_cells:
                        supported.add((x, z))
            clipped = sx * sz - len(supported)
            if clipped:
                self._detail_clip_lines.append(
                    f'detail {kind} {f.no} at ({ax},{ay},{az}) '
                    f'unsupported cells={clipped}; clipped'
                )
            self._solidify_roof_under(at, size, rm, supported)

        def place(x, y, z, state):
            if supported is None or (x, z) in supported:
                self.set(x, y, z, state)

        if kind in ('plant_room', 'vent'):
            for x, y, z in self._detail_box(at, size):
                place(x, y, z, 'gray_concrete')
            for dx in range(sx):
                for dz in range(sz):
                    place(ax + dx, ay + sy, az + dz, 'iron_trapdoor[half=bottom,open=false]')
        elif kind == 'louvred_vent':
            for x, y, z in self._detail_box(at, size):
                place(x, y, z, 'iron_trapdoor[half=bottom,open=false]')
        elif kind == 'solar_panel':
            for x, y, z in self._detail_box(at, (sx, 1, sz)):
                place(x, y, z, 'black_stained_glass')
        elif kind == 'lift_overrun':
            for x, y, z in self._detail_box(at, size):
                place(x, y, z, 'smooth_stone')
        elif kind == 'drainpipe':
            for dy in range(sy):
                place(ax, ay + dy, az, 'iron_bars')
        elif kind == 'sign':
            dx, dz = DIRS[face]
            place(ax, ay, az, 'smooth_stone')
            place(
                ax + dx, ay, az + dz,
                f'oak_wall_sign[facing={FACE[face]}]',
            )
        elif kind == 'awning_like':
            dx, dz = DIRS[face]
            for i in range(max(sx, sz)):
                if face in 'NS':
                    x, z = ax + i, az
                else:
                    x, z = ax, az + i
                place(x + dx, ay, z + dz, f'{rm}_slab[type=bottom]')
        elif kind == 'ledge':
            for x, y, z in self._detail_box(at, (sx, 1, sz)):
                place(x, y, z, f'{rm}_slab[type=bottom]')
        elif kind == 'fascia_band':
            for x, y, z in self._detail_box(at, size):
                place(x, y, z, 'terracotta')

    def details(self) -> None:
        """Place every ``details`` row after the shell and roof.

        @returns: None
        """
        self._detail_clip_lines = []
        # These are the full/slab/stair families used by the built structure;
        # thin roof edging (carpets/trapdoors) is deliberately not support.
        support_names = set()
        for f in self.features:
            materials = [f.roof.get('material') or 'smooth_stone', self.wall, self.floor_mat]
            materials.extend((f.roof.get('zone_materials') or {}).values())
            for material in materials:
                support_names.update(block_family_variants(material).values())
            edge = f.roof.get('edge') or {}
            block = bid(edge.get('block') or 'air')[10:]
            if not block.endswith(('_carpet', '_trapdoor', '_wall', '_fence', '_pane')):
                if block not in ('air', 'cave_air', 'void_air', 'water', 'lava', 'iron_bars'):
                    support_names.add(block)
        roof_cells = {
            pos for pos, state in self.p.cells.items()
            if bid(state)[10:] in support_names or bid(state)[10:].endswith(('_slab', '_stairs'))
        }
        by_no = {f.no: f for f in self.features}
        for lab, rows in (self.spec.get('details') or {}).items():
            f = by_no.get(lab)
            if f is None:
                continue
            for row in rows:
                self._place_detail(f, row, roof_cells)


def build(
    spec: dict,
    footprints: dict | None = None,
    world=None,
    osm_root=None,
    zone_data: dict | None = None,
    aerial_rgb: dict | None = None,
) -> Plan:
    """Build a feature plan from a validated spec.

    @param spec: validated feature spec
    @param footprints: optional label→cells map (tests / offline)
    @param world: optional World-like
    @param osm_root: optional clip root (tests / fixtures only; not cross-world)
    @param zone_data: optional lidar grids for zoned labels (tests)
    @param aerial_rgb: optional column RGB map for roof-colour dry-run lines (tests)
    @returns: Plan
    """
    return Build(
        spec, footprints=footprints, world=world, osm_root=osm_root,
        zone_data=zone_data, aerial_rgb=aerial_rgb,
    ).build()


def landed_build_seqs(name: str) -> list[int]:
    """Journal seqs of live (landed, not undone) builds recorded under plan ``name``.

    @param name: plan name
    @returns: seq numbers, empty when the plan has no live build
    """
    import cell_registry
    return [e.seq for e in cell_registry.view_registry().entries(plan=name)
            if e.kind == 'build' and e.status == 'landed' and not e.undone]


def run(
    spec_path,
    do_apply: bool = False,
    overwrite: bool = False,
    footprints: dict | None = None,
    world=None,
    osm_root=None,
    zone_data: dict | None = None,
    aerial_rgb: dict | None = None,
) -> Plan:
    """Load spec, build plan, preview, and site_kit.apply (dry unless do_apply).

    @param spec_path: path to site/builds/<name>.json
    @param do_apply: when True, live apply; otherwise dry run
    @param overwrite: apply over a plan that already has a landed build (default: refuse)
    @param footprints: optional label→cells map
    @param world: optional World-like
    @param osm_root: optional clip root (tests / fixtures only; not cross-world)
    @param zone_data: optional lidar grids for zoned labels (tests)
    @param aerial_rgb: optional column RGB map for roof-colour dry-run lines (tests)
    @returns: Plan
    """
    spec = load_spec(spec_path)
    if do_apply and not overwrite:
        seqs = landed_build_seqs(spec['name'])
        if seqs:
            raise SystemExit(
                f'{spec["name"]}: already built (journal seq {seqs[-1]}). A re-apply writes only '
                'the new plan, so old blocks outside it survive. Undo first: '
                f'element_undo.py --world <world> {spec["name"]} --apply; '
                'or pass --overwrite to apply over it anyway.')
    plan = build(
        spec, footprints=footprints, world=world, osm_root=osm_root,
        zone_data=zone_data, aerial_rgb=aerial_rgb,
    )
    out = ROOT / f'verification/site/{spec["name"]}-preview.png'
    preview(plan, out, scale=4)
    print(json.dumps(plan.summary())[:600])
    apply(plan, dry=not do_apply, modifies=tuple(spec['modifies']))      # ghost owners yield in the journal
    return plan


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('spec', help='site/builds/<name>.json')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--overwrite', action='store_true',
                    help='apply over a plan that already has a landed build (leaves stale blocks)')
    a = ap.parse_args()
    run(a.spec, do_apply=a.apply, overwrite=a.overwrite)
