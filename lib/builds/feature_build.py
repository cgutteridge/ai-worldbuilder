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
                   "material_regions"?: [{"level": <m_AOD>, "polygon": [[x,z], ...], "material"?: "<mat>"}],
                   "flat_tol_m"?: <metres 0..3, pitched only, default 1.5>,
                   "pv_arrays"?: [{"box": [x0, z0, x1, z1], "rows": "x"|"z",
                                   "pitch"?: <int >= 1, default 2>, "source": "<evidence>"}]}}
                 Absent edge = no roof edging. Present: block is the parapet /
                 coping mat; where "outer" skips zone steps, "all" edges every
                 step. Flat-roof overhang fringe only when edge is set.
                 Optional zone_materials overrides ``material`` per lidar
                 plateau level (metres AOD, same keys as ``zones[].levels``).
                 Pitched roofs may set material_regions [{level, polygon}]
                 from photo/aerial evidence to assign a roof's material across
                 lidar-zone boundaries. Unmapped columns retain zone lookup.
                 A row with its own "material" gives just those columns that
                 material (level still names their plateau), so a region
                 needs no spare plateau of its own.
                 Material choice: flat(ish) (kind flat, rise/run <= 1 in 2) may
                 use any catalogue full block or slab; pitched needs a triad
                 (roof_material.py, config/roof-materials.json). Lidar
                 flattening is a separate step. A missing triad falls back to
                 the full block and is listed, not rewritten.
                 Flat roofs may set pv_arrays (from ``scripts/roof_aerial.py``):
                 inclusive box, rows running along x or z, one daylight_detector
                 row every ``pitch`` blocks across the rows. Detectors sit on the
                 finished deck (the slab under each becomes a full roof block),
                 only on owned columns at the array's commonest deck height, and
                 never over a detail; skipped columns are reported as clipped.
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
  Explicit windows use facade_plan[label].faces[face].glazing. Without face plans,
  accepted industrial / big-box street faces come from the land-use class table,
  mapped building tags and street lines. Other classes keep the legacy window rhythm.
  Panes need solid
  framing on both sides, above and below; missing frames are reported. The
  dry run prints glazed share per exposed face/storey and marks missing rows
  as no evidence read. Legacy top-level window tables are refused.
  details        optional per-label roof/facade greeblies (building-review):
                   {"<label>": [
                     {"kind": "<roof|facade kind>", "at": [x, y, z],
                      "size": [sx, sy, sz], "source": "<evidence note>",
                      "face"?: "N"|"S"|"E"|"W"}]}
                 Roof kinds: plant_room, vent, louvred_vent, skylight,
                 solar_panel, lift_overrun, chimney. Facade kinds: drainpipe, sign,
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
Evidence contact sheet after apply: ``python3 scripts/evidence_sheet.py --world <world> <name>``
(portal-facing + top-down + aerial; records path on ``*-result.json``; handoff
``end`` refuses FEATURE without it).

Usage:  python3 scripts/feature_build.py --world <world> site/builds/<name>.json [--apply [--overwrite]]
                                         [--landmark-override REASON]

``--apply`` refuses a plan with a landed, not-undone build in the journal (a re-apply
leaves old blocks outside the new plan); undo it first, or pass ``--overwrite``.
Begun / mismatched entries do not block (site_recover / reconcile own those).
Landmark classes (church, cathedral, chapel, cinema, shopping centre) refuse
``--apply`` until a street-level image source is recorded for the feature's cell
(``cell_sources.py --record``), unless ``--landmark-override`` gives a non-empty reason.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import textwrap
import warnings
from collections import Counter, deque
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as _sw

from block_store import state_props
from script_env import ROOT, TOOL_ROOT  # noqa: E402
import footprint_overlap  # noqa: E402
import osm_footprints  # noqa: E402
from chimney import chimney_layers, validate_chimney
from roof_geometry import (
    ROOF_DELTA, ROOF_FACE_ORDER, align_stair_shapes, edge_distance_field, pitched_roof_states,
    regularise_roof_half_heights, roof_block_y,
)
from roof_material import block_id_names, family_variants, roof_material_warnings
import facade_plan as facade_plan_mod  # noqa: E402
import land_use  # noqa: E402
import provenance as provenance_mod  # noqa: E402
from site_kit import (  # noqa: E402
    AIR_IDS, Plan, apply, bid, fresh_planning_world, ground_scan_range, guard_door_frames, guard_roof_jogs,
    interior_ground_clears, is_bottom_half_slab, nearest_preview_colour, preview,
)
from world_frame import metres_aod_to_y  # noqa: E402

STOREY_H = 3
from cellmath import DIRS, N4, N8, segment_distance  # noqa: E402
import stoop  # noqa: E402
from stoop import FACE, GROUND  # noqa: E402
FASCIA_COLOURS = ('blue', 'green', 'red', 'cyan', 'orange', 'purple')
# Light bands need black lettering; the rest stay white so the name reads against the bar.
LIGHT_FASCIA = frozenset({'cyan', 'orange', 'lime', 'yellow', 'white', 'pink'})


# Existing facade-exposure checks intentionally omit void_air; volume checks do not.
_OUTSIDE_AIR_EXCLUSIONS = frozenset({'void_air'})
_OUTSIDE_AIRS = tuple('minecraft:' + name for name in AIR_IDS if name not in _OUTSIDE_AIR_EXCLUSIONS)
_AIRS = tuple('minecraft:' + name for name in AIR_IDS)
_OPENING_EXTRAS = ('water', 'lava', 'iron_bars')


def fascia_colour(key: str) -> str:
    """Stable fascia colour from a mapped name or feature label.

    @param key: name, brand, unit or feature label
    @returns: a concrete colour name
    """
    digest = hashlib.sha256(key.encode()).digest()
    return FASCIA_COLOURS[int.from_bytes(digest[:4], 'big') % len(FASCIA_COLOURS)]


def contrasting_sign_colour(fascia: str) -> str:
    """Dye colour that reads against a fascia band.

    @param fascia: concrete colour name from :func:`fascia_colour`
    @returns: ``black`` on a light band, otherwise ``white``
    """
    return 'black' if fascia in LIGHT_FASCIA else 'white'


# A stair-step notch is filled. A deeper recess starts another run, so a long
# diagonal does not pour one wedge out to the streetward corner.
MAX_SHOP_STEP = 2


def fit_straight_runs(fronts: dict) -> list[dict]:
    """Square shop-front columns into straight runs and name the cells that fill the steps.

    One run per stretch of a connected front whose recess is at most
    :data:`MAX_SHOP_STEP` blocks. An already straight front is unchanged. Each
    stretch snaps to its own streetward edge, and every block between a recessed
    column and that edge is fill, so a later awning or sign meets wall rather
    than a gap. The owner would rather have that extra thickness than a hover.

    @param fronts: ``{(x, z): 'N'|'S'|'E'|'W'}``
    @returns: list of ``{'face', 'line', 'fill', 'sources'}``; ``line`` is ordered
        along the run, ``fill`` is the cells to build that are not already front
        columns, ``sources`` is the front columns the run came from
    """
    if not fronts:
        return []
    remaining = set(fronts)
    runs = []
    while remaining:
        start = min(remaining)
        comp = {start}
        remaining.remove(start)
        todo = [start]
        while todo:
            x, z = todo.pop()
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    if not dx and not dz:
                        continue
                    nxt = (x + dx, z + dz)
                    if nxt in remaining:
                        remaining.remove(nxt)
                        comp.add(nxt)
                        todo.append(nxt)
        runs.extend(_runs_within_step(comp, fronts))
    return runs


def _runs_within_step(cells: set, fronts: dict) -> list[dict]:
    """Split one connected front wherever the recess exceeds :data:`MAX_SHOP_STEP`.

    @param cells: front columns in one component
    @param fronts: face of every front column
    @returns: straight runs, each no deeper than the step limit
    """
    horizontal = sum(fronts[c] in 'NS' for c in cells) >= sum(fronts[c] in 'EW' for c in cells)

    def cross(cell):
        return cell[1] if horizontal else cell[0]

    def along(cell):
        return cell[0] if horizontal else cell[1]

    ordered = sorted(cells, key=lambda cell: (along(cell), cross(cell)))
    groups = []
    current = []
    for cell in ordered:
        trial = current + [cell]
        span = max(cross(c) for c in trial) - min(cross(c) for c in trial)
        if current and span > MAX_SHOP_STEP:
            groups.append(current)
            current = [cell]
        else:
            current.append(cell)
    if current:
        groups.append(current)
    return [_straight_run(set(group), fronts) for group in groups]


def _straight_run(cells: set, fronts: dict) -> dict:
    """Snap one connected front onto its streetward edge.

    @param cells: front columns in one component
    @param fronts: face of every front column
    @returns: ``{'face', 'line', 'fill', 'sources'}``
    """
    horizontal = sum(fronts[c] in 'NS' for c in cells) >= sum(fronts[c] in 'EW' for c in cells)
    if horizontal:
        face = 'N' if sum(fronts[c] == 'N' for c in cells) >= sum(fronts[c] == 'S' for c in cells) else 'S'
        street_low = face == 'N'
        alongs = sorted({c[0] for c in cells})
        outer = min(c[1] for c in cells) if street_low else max(c[1] for c in cells)
        line = [(along, outer) for along in alongs]
    else:
        face = 'W' if sum(fronts[c] == 'W' for c in cells) >= sum(fronts[c] == 'E' for c in cells) else 'E'
        street_low = face == 'W'
        alongs = sorted({c[1] for c in cells})
        outer = min(c[0] for c in cells) if street_low else max(c[0] for c in cells)
        line = [(outer, along) for along in alongs]
    step = -1 if street_low else 1
    fill = set()
    for cell in cells:
        cursor = cell[1] if horizontal else cell[0]
        along = cell[0] if horizontal else cell[1]
        while cursor != outer:
            cursor += step
            fill.add((along, cursor) if horizontal else (cursor, along))
    fill -= set(cells)
    return {'face': face, 'line': line, 'fill': fill, 'sources': set(cells)}


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


def _win_nanmedian(a: np.ndarray, k: int) -> np.ndarray:
    """k×k sliding median that skips NaN (no-data) cells.

    @param a: 2-D float array, NaN where there is no data
    @param k: odd window size
    @returns: same shape as ``a``; NaN only where the whole window is NaN
    """
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)  # all-NaN window → NaN, handled by the caller
        return _win(a, k, np.nanmedian)


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


def _cap_lone_cells(rise_m: np.ndarray, sel: np.ndarray, readable: np.ndarray) -> np.ndarray:
    """Cap each in-zone cell's rise at its highest readable 8-neighbour's rise.

    A lone one-cell spike (chimney, plant room, noise) has no neighbour as
    high, so it drops to the neighbour level; a ridge two or more cells wide
    keeps its height because neighbours match it.

    @param rise_m: metres above the zone plateau
    @param sel: boolean mask of the zone's cells
    @param readable: footprint mask; neighbours in another zone count, so a
        crest on a zone edge keeps its height when the ridge runs on across it
    @returns: capped rise (cells outside ``sel`` unchanged)
    """
    masked = np.where(readable, rise_m, -np.inf)
    padded = np.pad(masked, 1, mode='constant', constant_values=-np.inf)
    h, w = rise_m.shape
    nb = np.full(rise_m.shape, -np.inf)
    for dz in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dz == 0 and dx == 0:
                continue
            nb = np.maximum(nb, padded[1 + dz:1 + dz + h, 1 + dx:1 + dx + w])
    cap = np.where(np.isfinite(nb), nb, rise_m)
    return np.where(sel, np.minimum(rise_m, cap), rise_m)


def _roof_pitch_mask(
    rise_m: np.ndarray, mask: np.ndarray, flat_tol_m: float, flat_gradient: float,
) -> np.ndarray:
    """Keep slopes reaching the height threshold through a steep part of the roof.

    Gradient is rise/run across adjacent one-metre samples in the same roof
    zone; perpendicular components combine so rotated roofs use the same
    threshold. A steep low bump must reach a higher roof by a non-descending
    path to qualify. Walking back downhill from those higher cells preserves
    their toes, while a separate low bump beyond a saddle stays flat.

    @param rise_m: non-negative, capped metres above the zone eave
    @param mask: readable cells in this zone; holes and other zones do not join
    @param flat_tol_m: exclusive height threshold for flattening
    @param flat_gradient: exclusive gradient threshold for flattening
    @returns: boolean mask of cells belonging to a retained pitch
    """
    gx = np.zeros(rise_m.shape)
    gz = np.zeros(rise_m.shape)
    dx = np.where(mask[:, 1:] & mask[:, :-1], np.abs(np.diff(rise_m, axis=1)), 0.0)
    dz = np.where(mask[1:, :] & mask[:-1, :], np.abs(np.diff(rise_m, axis=0)), 0.0)
    gx[:, 1:] = np.maximum(gx[:, 1:], dx)
    gx[:, :-1] = np.maximum(gx[:, :-1], dx)
    gz[1:, :] = np.maximum(gz[1:, :], dz)
    gz[:-1, :] = np.maximum(gz[:-1, :], dz)

    def spread(seeds: np.ndarray, uphill: bool) -> np.ndarray:
        seen = seeds.copy()
        pending = deque(zip(*np.nonzero(seeds)))
        while pending:
            z, x = pending.popleft()
            for dz, dx in N4:
                nz, nx = z + dz, x + dx
                if not (0 <= nz < mask.shape[0] and 0 <= nx < mask.shape[1]):
                    continue
                if seen[nz, nx] or not mask[nz, nx]:
                    continue
                a, b = rise_m[z, x], rise_m[nz, nx]
                follows_slope = b >= a if uphill else b <= a
                if follows_slope:
                    seen[nz, nx] = True
                    pending.append((nz, nx))
        return seen

    steep = mask & (rise_m > 0) & (np.hypot(gx, gz) >= flat_gradient)
    uphill = spread(steep, uphill=True)
    return spread(uphill & (rise_m >= flat_tol_m), uphill=False)


FLAT_TOL_M = 1.5  # default exclusive rise (m above the zone eave) below which a roof stays flat


def zone_ridge_half_steps(
    med: np.ndarray,
    zone: np.ndarray,
    readable: np.ndarray,
    flat_tol_m: float = FLAT_TOL_M,
    levels: list | None = None,
    flat_gradient: float = 0.2,
    flat_out: np.ndarray | None = None,
) -> np.ndarray:
    """Half-block rises above each zone plateau for pitched roofs.

    A roof rise below ``flat_tol_m`` (default 1.5 m, including every rise at or
    below 1 m) stays flat. A larger rise also stays flat when its gradient is
    below ``flat_gradient`` (default 20%). Qualifying slopes keep their lower
    toes, but cannot make a separate low bump in the same zone pitched.
    Retained cells rise ``max(0, round((med - level) * 2))`` half-steps (1 m ≈ 1
    block), so a lidar ridge becomes stair / slab / full-block courses instead
    of stepped flat terraces. A lone one-cell spike is no ridge (it cannot turn
    a flat zone pitched), and each rise is clipped to the gap to the next zone
    level so a bucket-edge mismatch cannot spike past the next plateau.

    @param med: median DSM metres AOD (pitched labels pass the 3×3 median so a
        narrow ridge keeps its peak; zone snapping uses the 5×5). NaN means no
        data: that cell is dropped from ``readable`` and from the neighbour cap
        (it is never read as a zero-metre roof) and gets 0 half-steps
    @param zone: snapped plateau grid (same shape)
    @param readable: footprint mask
    @param flat_tol_m: exclusive rise threshold for a flat roof (default 1.5 m)
    @param levels: plateau levels for the next-level clip (default: the levels
        present in ``zone`` over ``readable``)
    @param flat_gradient: exclusive rise/run threshold for a flat roof (default 0.2)
    @param flat_out: optional boolean output grid; receives True for readable
        cells rejected as pitch, so block selection cannot add stairs back
    @returns: int half-steps above the zone eave (0 outside readable / flat zones)
    """
    out = np.zeros(zone.shape, dtype=int)
    if flat_out is not None:
        flat_out[:] = readable
    if not readable.any():
        return out
    med_f = med.astype(float)
    nodata = np.isnan(med_f)
    if nodata.any():
        # no-data is unreadable, never a zero-metre roof: drop it from the zone and its neighbours
        readable = readable & ~nodata
        med_f = np.where(nodata, 0.0, med_f)
        if not readable.any():
            return out
    present = sorted({float(v) for v in np.unique(zone[readable])})
    all_levels = sorted({float(v) for v in (levels if levels is not None else present)} | set(present))
    for level in present:
        sel = readable & (zone == level)
        if not sel.any():
            continue
        rise_m = np.maximum(0, _cap_lone_cells(med_f - level, sel, readable))
        above = [v for v in all_levels if v > level]
        if above:
            rise_m = np.minimum(rise_m, above[0] - level)
        pitched = _roof_pitch_mask(rise_m, sel, flat_tol_m, flat_gradient)
        if flat_out is not None:
            flat_out[pitched] = False
        half = np.rint(rise_m * 2).astype(int)
        out[pitched] = half[pitched]
    return out


def flatten_half_step_decks(half: np.ndarray, zone: np.ndarray, readable: np.ndarray) -> np.ndarray:
    """Drop 1-half-step decks and toes to the eave so a deck is not a block of slabs.

    Zone levels are whole metres but a real deck often sits half a metre above
    its level, which the provider's float option now shows. A 1-half-step cell
    with no same-zone neighbour above 1 is that deck (or a toe), not a pitch, so
    it drops to the eave (0). A 1-half-step cell beside a 2 or more stays: it
    is the foot of a slope and plans the slab course there.

    @param half: int half-steps above the zone eave (from ``zone_ridge_half_steps``)
    @param zone: snapped plateau grid (same shape)
    @param readable: footprint mask
    @returns: new int grid of the same shape
    """
    out = half.copy()
    h, w = out.shape
    pz = np.pad(zone, 1, mode='constant', constant_values=np.nan)
    pr = np.pad(readable, 1, mode='constant', constant_values=False)
    ph = np.pad(half, 1, mode='constant', constant_values=0)
    above_one = np.zeros(out.shape, dtype=bool)
    for dz, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        sl = (slice(1 + dz, 1 + dz + h), slice(1 + dx, 1 + dx + w))
        above_one |= pr[sl] & (pz[sl] == zone) & (ph[sl] >= 2)
    out[readable & (out == 1) & ~above_one] = 0
    return out


def _lone_odd_half_heights(half_heights: dict[tuple[int, int], int]) -> list[tuple[int, int]]:
    """Columns whose half-height is odd and differs from every N4 neighbour.

    @param half_heights: ``{(x, z): half_height}``
    @returns: the lone odd columns (a slab speckle, not a slab course)
    """
    lone = []
    for (x, z), v in half_heights.items():
        if v % 2 == 0:
            continue
        nbs = [half_heights[(x + dx, z + dz)] for dx, dz in N4 if (x + dx, z + dz) in half_heights]
        if nbs and v not in nbs:
            lone.append((x, z))
    return lone


def snap_lone_odd_half_heights(
    half_heights: dict[tuple[int, int], int],
    levels: dict[tuple[int, int], int],
    passes: int = 6,
) -> dict[tuple[int, int], int]:
    """Snap each lone odd half-height to its neighbours so a slab is never a speckle.

    The provider's float option carries single-cell noise. An odd rise (a slab
    course) that differs from all of its neighbours is noise. First it snaps to
    the closest half-height among its same-zone neighbours (the lower on a tie),
    without going below its original even floor. The even floors of an already
    regularised roof also differ by at most two half-steps, so re-capping cannot
    pull any column below that floor: even crest columns keep their height and
    odd crest columns lose at most one half-step, along the entire ridge.
    A cell that still stands alone after that (a steep hip where each snap
    hands the speckle to the next cell)
    drops one half-step to the even height below; the cliff cap in
    ``regularise_roof_half_heights`` that follows respects the same floors.
    Odd steps in a clean course (a neighbour shares the height) are kept.

    @param half_heights: ``{(x, z): half_height}``, already regularised
    @param levels: semantic zone level per column (as for ``regularise_roof_half_heights``)
    @param passes: max passes of each repair
    @returns: new dict with the same keys
    """
    h = dict(half_heights)
    # Re-cap the even floor too, so raw helper inputs cannot introduce cliffs.
    even_floor = regularise_roof_half_heights({c: v - v % 2 for c, v in h.items()}, levels)
    for _ in range(passes):
        lone = _lone_odd_half_heights(h)
        nxt = dict(h)
        for (x, z) in lone:
            same = [h[(x + dx, z + dz)] for dx, dz in N4
                    if (x + dx, z + dz) in h and levels[(x + dx, z + dz)] == levels[(x, z)]]
            if same:
                nearest = min(same, key=lambda s: (abs(s - h[(x, z)]), s))
                nxt[(x, z)] = max(even_floor[(x, z)], nearest)
        if nxt == h:
            break
        h = regularise_roof_half_heights(nxt, levels)
    for _ in range(passes):
        lone = _lone_odd_half_heights(h)
        if not lone:
            break
        for c in lone:
            h[c] -= 1
        h = regularise_roof_half_heights(h, levels)
    return h


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


def _bottom_slab_or_full(mat: str) -> str:
    """Bottom slab of a roof material family, or its full block when it has no slab.

    Catalogue families such as terracotta and the concretes have no slab, so a
    spelled-out ``{mat}_slab`` would be a block that does not exist.

    @param mat: roof material family or block alias
    @returns: block state string
    """
    variants = family_variants(mat)
    if 'slab' in variants:
        return f'{variants["slab"]}[type=bottom]'
    return variants['full']


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
        _validate_flat_tol(path, lab, roof)
        _validate_zone_materials(path, lab, roof)
        _validate_pv_arrays(path, lab, roof)
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


def _validate_flat_tol(path, lab: str, roof: dict) -> None:
    """Validate optional ``roofs[label].flat_tol_m`` (absent = ``FLAT_TOL_M`` default).

    @param path: spec path (for error text)
    @param lab: roof label
    @param roof: roofs[lab] dict
    @raises SystemExit: when not a finite number in [0, 3], or set on a flat roof
    """
    if 'flat_tol_m' not in roof:
        return
    tol = roof['flat_tol_m']
    if roof.get('kind') != 'pitched':
        raise SystemExit(f'{path}: roofs[{lab}].flat_tol_m only applies to pitched roofs')
    if (isinstance(tol, bool) or not isinstance(tol, (int, float))
            or not math.isfinite(tol) or not 0 <= tol <= 3):
        raise SystemExit(f'{path}: roofs[{lab}].flat_tol_m must be a finite number of metres in [0, 3]')


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


PV_ARRAY_KEYS = frozenset({'box', 'rows', 'pitch', 'source'})
PV_DETECTOR = 'daylight_detector[inverted=false]'   # power follows daylight in game


def _validate_pv_arrays(path, lab: str, roof: dict) -> None:
    """Validate optional flat-roof ``pv_arrays`` rows; fill the default pitch.

    @param path: spec path (for error text)
    @param lab: roof label
    @param roof: roofs[lab] dict (rows gain ``pitch`` 2 when absent)
    @raises SystemExit: on a malformed row or a non-flat roof
    """
    if 'pv_arrays' not in roof:
        return
    rows = roof['pv_arrays']
    context = f'{path}: roofs[{lab}].pv_arrays'
    if not isinstance(rows, list):
        raise SystemExit(f'{context} must be a list')
    if rows and roof.get('kind') != 'flat':
        raise SystemExit(f'{context} needs a flat roof')
    for i, row in enumerate(rows):
        pref = f'{context}[{i}]'
        if not isinstance(row, dict):
            raise SystemExit(f'{pref} must be an object')
        unknown = sorted(set(row) - PV_ARRAY_KEYS)
        if unknown:
            raise SystemExit(f'{pref} unknown keys {unknown}')
        box = row.get('box')
        if (not isinstance(box, list) or len(box) != 4
                or not all(isinstance(v, int) and not isinstance(v, bool) for v in box)
                or box[0] > box[2] or box[1] > box[3]):
            raise SystemExit(f'{pref} box must be [x0, z0, x1, z1] ints with x0 <= x1, z0 <= z1')
        if row.get('rows') not in ('x', 'z'):
            raise SystemExit(f'{pref} rows must be "x" or "z"')
        pitch = row.setdefault('pitch', 2)
        if not isinstance(pitch, int) or isinstance(pitch, bool) or pitch < 1:
            raise SystemExit(f'{pref} pitch must be an int >= 1')
        source = row.get('source')
        if not isinstance(source, str) or not source.strip():
            raise SystemExit(f'{pref} source must be a non-empty string')


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
            if not isinstance(row, dict) or not {'level', 'polygon'} <= set(row) <= {'level', 'polygon', 'material'}:
                raise SystemExit(f'{prefix} needs {{level, polygon}} and optionally material')
            if 'material' in row and (not isinstance(row['material'], str) or not row['material'].strip()):
                raise SystemExit(f'{prefix}.material must be a non-empty material name')
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


REGION_MATERIAL_PREFIX = 'region:'   # material-level key for a material_regions row with its own material


def roof_material_for_zone(roof: dict, level: int | float | str | None) -> str:
    """Resolve slab/full material for one roof column.

    A ``region:<i>`` key (from :func:`roof_material_levels`) gives that
    ``material_regions`` row's own material. Otherwise prefers
    ``zone_materials[level]`` (int or str key) when present, then the
    label-wide ``material``.

    @param roof: roofs[label] dict
    @param level: plateau metres AOD (from ``zone_level``), a ``region:<i>`` key, or None
    @returns: material name without ``minecraft:``
    """
    default = roof['material']
    if isinstance(level, str) and level.startswith(REGION_MATERIAL_PREFIX):
        return roof['material_regions'][int(level[len(REGION_MATERIAL_PREFIX):])]['material']
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
        key = f'{REGION_MATERIAL_PREFIX}{i}' if row.get('material') else level
        owners.update({c: key for c in cells})
        assigned.update(cells)
        if reports is not None:
            reports.append(f'{label} material region {i}: level {level:g} columns {len(cells)}'
                           + (f' material {row["material"]}' if row.get('material') else ''))
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


class AerialSourceMissing(RuntimeError):
    """No local aerial mosaic is available, so roof colours cannot be sampled."""


def sample_columns_rgb(pts: list[tuple[int, int]]) -> dict[tuple[int, int], tuple[float, float, float]]:
    """Aerial 0..1 RGB per column from the local aerial mosaics.

    Columns no mosaic covers (or that fall on blank pixels) are left out of the
    result. A missing aerial source is a different matter and raises, so a
    broken source can never look like "no colours found".

    @param pts: MC columns to sample
    @returns: ``{(x, z): (r, g, b)}`` for columns that yielded a colour
    @raises AerialSourceMissing: when no local aerial mosaic can be found
    """
    if not pts:
        return {}
    import aerial_coverage
    sources = aerial_coverage.mosaics()
    if not sources:
        raise AerialSourceMissing(
            'no local aerial mosaic under reference/aerial; roof colour cannot be sampled')
    found = aerial_coverage.samples(list(pts), sources, radius=2)
    return {p: (float(c[0]), float(c[1]), float(c[2])) for p, (_, _, _, c) in found.items()}


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
        try:
            rgb_map = aerial_rgb if aerial_rgb is not None else sample_columns_rgb(need)
        except AerialSourceMissing as exc:
            lines.append(f'roof colour suggestions skipped: {exc}')
            return lines
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
        building_tags: dict | None = None,
        streets: list | None = None,
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
        @param zone_data: optional ``{label: {dsm, ground_y, x0, z0}}`` grids for tests (optional ``dsm_fine``: float DSM for pitched half-steps, NaN = no data)
            (arrays indexed ``[z, x]`` / row=z; ``ground_y`` is block Y; ``dsm`` metres AOD).
            Unzoned labels may inject ground_y without dsm. When omitted, zoned labels fetch composite lidar via ``lidar_provider.mc_heights``.
        @param aerial_rgb: optional ``{(x, z): (r, g, b) 0..1}`` for roof-colour
            dry-run lines (tests). When omitted, samples the place aerial mosaic.
        @param building_tags: optional flattened tags by label (synthetic fixtures)
        @param streets: optional ``(highway, points, tags)`` rows (synthetic fixtures)
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
        self.building_tags = dict(building_tags or {})
        self.land_uses = {}
        if self.idx is not None:
            for rings, props in self.idx.buildings:
                label = spec['ways'].get(str(props.get('osm_way_id') or props.get('osm_id')))
                if label is None:
                    continue
                tags = osm_footprints.osm_tags(props)
                self.building_tags.setdefault(label, tags)
                if getattr(self.idx, 'areas', None):
                    from coverage import building_land_use
                    cls = building_land_use(self.idx, rings, props)['class']
                else:
                    cls = land_use.classify_building(tags)['class']
                self.land_uses[label] = cls
        self.streets = (streets if streets is not None else
                        footprint_overlap.streets_from_index(self.idx) if self.idx is not None and
                        hasattr(self.idx, 'streets') else [])
        self.streets = [(highway, pts, osm_footprints.osm_tags(tags))
                        for highway, pts, tags in self.streets]
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
        self._window_lines: list[str] = []
        self._detail_clip_lines: list[str] = []
        self._pv_lines: list[str] = []
        self._entrance_covers: list[tuple] = []
        # Stamp against every cell the footprint covers so neighbour prepares elsewhere
        # do not invalidate this plan, while cross-cell geometry still counts (task 232).
        import cellmath
        self.p.capture_inputs({cellmath.cell_of(x, z) for cols in by_no.values() for x, z in cols})

    def set(self, x, y, z, s):
        self.p.set(x, y, z, s)

    def _set_roof(self, x, y, z, s):
        """Set a roof-membrane cell.

        Bottom-half slabs are exempted from the apply rewrite and get a wall
        block under them when the cell below is air (#209).

        @param x: block x
        @param y: block y
        @param z: block z
        @param s: roof block state
        """
        if is_bottom_half_slab(s) and not self._is_solid(x, y - 1, z):
            # a bottom-half slab never floats: wall under it when the cell below is air
            self.p.set(x, y - 1, z, self.wall)
        self.p.set(x, y, z, s)
        if is_bottom_half_slab(s):
            self.p.keep_slab_cells.add((x, y, z))

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
        return bool(st) and bid(st)[10:] not in AIR_IDS

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

    def floor_y(self, f: Feature) -> int:
        """Median lidar bare-terrain floor under the footprint, with floor_min override.

        @param f: feature whose footprint samples the DTM
        @returns: median ground block Y, raised by floor_min when specified
        """
        from floor_reference import floor_reference
        return floor_reference(self.name, f.no, f.cells, self.spec['floor_min'], self.zone_data.get(f.no))

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
        # keeps ``dsm`` (and everything built on it) exactly as before the float option existed
        h = lp.mc_heights(lidar, x0, z0, x1, z1)
        # The whole-metre grid above rounds every DSM sample, so a roof that rises half
        # a block never shows. ``dsm_fine`` is the provider's float option (NaN = no data;
        # whole metres when the provider has none) and only feeds the pitched-roof
        # half-steps; snapping, footprints and ground heights stay on the whole-metre grid.
        # (a second sample of the same bbox; the cost is accepted to leave ``dsm`` untouched)
        fine = lp.mc_heights_float(lidar, x0, z0, x1, z1)
        # mc_heights is [x - x0, z - z0]; convert to [z, x] for zone helpers
        return {
            'x0': x0,
            'z0': z0,
            'dsm': np.asarray(h['dsm']).T,
            'dsm_fine': np.asarray(fine['dsm'], dtype=float).T,
            'ground_y': np.asarray(h['ground_y']).T,
        }

    def prepare_zones(self) -> None:
        """Square footprints and assign lidar roof zones for zoned labels.

        Expands ``f.cells`` to the readable mask, fills ``zone_roof`` /
        ``zone_eave`` / ``zone_ground`` / ``zone_level``, refreshes ``owner``,
        and records the pad line for dry-run print.
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
            half_grid = None
            flat_grid = np.zeros(readable.shape, dtype=bool)
            if pitched:
                # pitched labels read the float DSM (when given) so a half-block rise survives
                fine = grids.get('dsm_fine')
                fine = dsm.astype(float) if fine is None else np.asarray(fine, dtype=float)
                half_grid = flatten_half_step_decks(
                    zone_ridge_half_steps(
                        _win_nanmedian(fine, 3), zone, readable, levels=_levels, flat_out=flat_grid,
                        flat_tol_m=float(f.roof.get('flat_tol_m', FLAT_TOL_M)),
                    ),
                    zone, readable,
                )
            roof: dict = {}
            eaves: dict = {}
            half_h: dict = {}
            flat_cells = set()
            gy: dict = {}
            zl: dict = {}
            for z in range(depth):
                for x in range(width):
                    if not readable[z, x]:
                        continue
                    wx, wz = x0 + x, z0 + z
                    eave_y = metres_aod_to_y(float(zone[z, x]))
                    eaves[(wx, wz)] = eave_y
                    if half_grid is not None:
                        h = int(half_grid[z, x])
                        half_h[(wx, wz)] = 2 * eave_y + h
                        roof[(wx, wz)] = roof_block_y(half_h[(wx, wz)])
                        if flat_grid[z, x]:
                            flat_cells.add((wx, wz))
                    else:
                        roof[(wx, wz)] = eave_y
                    gy[(wx, wz)] = int(ground[z, x])
                    zl[(wx, wz)] = int(round(float(zone[z, x])))
            if pitched:
                half_h = snap_lone_odd_half_heights(regularise_roof_half_heights(half_h, zl), zl)
                roof = {c: roof_block_y(h) for c, h in half_h.items()}
            # neighbourhood ground for expose() outside the mass
            for z in range(depth):
                for x in range(width):
                    wx, wz = x0 + x, z0 + z
                    gy.setdefault((wx, wz), int(ground[z, x]))
            f.cells = new_cells
            f.zone_roof = roof
            f.zone_eave = eaves
            f.zone_roof_half = half_h if pitched else None
            f.zone_flat_cells = flat_cells
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
        (median ground), strictly below each column's eave.

        @param k: feature index (ownership / wall rivalry)
        @param f: feature with ``zone_roof`` / ``zone_eave`` / ``zone_ground`` filled
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
                flat_cells=getattr(f, 'zone_flat_cells', None),
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
                elif (y - datum) % STOREY_H == 0 and y < f.zone_eave[(x, z)]:
                    self.set(x, y, z, self.floor_mat)
                else:
                    self.set(x, y, z, 'air')
            if pitched_states is not None:
                self._set_roof(x, ry, z, pitched_states[(x, z)])
            else:
                self.set(x, ry, z, family_variants(rm)['full'])
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
        upper half opens into that air. Back only those openings with wall.
        A storey floor block sitting at the stair's Y in the taller column is
        also swapped for wall: left in place it shows as a light-grey square
        through the stair's open half (#310, I-022 S-087). Other blocks and
        exterior air are left alone.

        @param roof: membrane block Y per owned roof column
        @param states: membrane states (after same-Y stair shape derivation)
        """
        for (x, z), state in states.items():
            if '_stairs[' not in state:
                continue
            props = state_props(state)
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
                closed.add(ROOF_FACE_ORDER[(ROOF_FACE_ORDER.index(facing) + turn) % 4])
            y = roof[(x, z)]
            for face, (dx, dz) in ROOF_DELTA.items():
                neighbour = (x + dx, z + dz)
                if face in closed or roof.get(neighbour, y) <= y:
                    continue
                pos = (neighbour[0], y, neighbour[1])
                if self.p.cells.get(pos) in ('minecraft:air', f'minecraft:{self.floor_mat}'):
                    self.set(*pos, self.wall)

    def top_solid(self, x, z, near):
        return stoop.top_solid(self.live, x, z, near)

    def step_out(self, st, d, b):
        stoop.step_out(self, st, d, b, self.kset, prefetch=self._prefetch_live)

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
        self._prefetch_live((st[0], y, st[1]) for y in ground_scan_range(outside))
        live_y = self.top_solid(*st, outside)
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
        A catalogue material with no slab (terracotta, concrete) places its
        full block instead and records a warning. It does not raise.

        @returns: None
        """
        for f, eid, cover, face, cells, roof_y in self._entrance_covers:
            depth = cover.get('depth') or 1
            kind = cover['kind']
            dx, dz = DIRS[face]
            lx, lz = (1, 0) if face in 'NS' else (0, 1)
            start = cells[0]
            width = len(cells)
            raw = f.roof.get('material') or 'smooth_stone'
            material = family_variants(raw)
            if 'slab' in material:
                cover_state = f"{material['slab']}[type=bottom]"
            else:
                cover_state = material['full']
                self._roof_warnings.append(
                    f"roof material {raw!r} has no slab; using full block {material['full']}"
                )
            for lateral in range(-1, width + 1):
                for outward in range(1, depth + 1):
                    x = start[0] + lx * lateral + dx * outward
                    z = start[1] + lz * lateral + dz * outward
                    if (x, z) in self.by_no_all_cells:
                        continue
                    self.set(x, roof_y, z, cover_state)
                    if kind == 'porch_roof' and outward == depth and lateral in (-1, width):
                        ground_y = self.top_solid(x, z, roof_y)
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
        """Resolve intended glazing as complete bounded openings before writing glass."""
        from floor_reference import outside_ground
        from window_openings import Window, choose_windows, report_lines
        facades = facade_plan_mod.glazing_by_face(self.spec.get('facade_plan'), f.no)
        if not facades and self._suppress_window_grid(f):
            return  # a dressed class street face replaces the unevidenced all-face storey grid
        door = self._door_keepout(f)
        candidates = []
        # Preserve actual door leaves and their lateral framing, including jambs
        # that were already solid before the door builder ran.
        protected = set(self.keep)
        for pos, state in self.p.cells.items():
            if '_door[' in state:
                x, y, z = pos
                protected.add(pos)
                for dx, dz in DIRS.values():
                    protected.add((x + dx, y, z + dz))
        for face in sorted(facades or DIRS):
            runs = self.face_runs(f, face)
            edge = 1 if face in 'NS' else 0
            stepped = len({c[edge] for run in runs for c in run}) > 1
            rows = {r['storey']: r for r in facades.get(face, [])}
            for storey in range(f.storeys):
                row = rows.get(storey + 1)
                if facades and not row:
                    continue
                base = f.b + storey * STOREY_H
                for run in runs:
                    roles = self.facade_roles(row, len(run)) if row else [
                        'W' if (c[0] if face in 'NS' else c[1]) % 2 == 0 else '_' for c in run]
                    corner_cells = {tuple(v['at']) for v in (row or {}).get('corner_glazing', [])}
                    for c, role in zip(run, roles):
                        if c in door:
                            continue
                        if not row and any(abs(c[0] - q[0]) + abs(c[1] - q[1]) < 3 for q in door):
                            continue
                        ry = self._column_roof_y(f, c)
                        for dy in range(STOREY_H):
                            y = base + dy
                            if ry is not None and y >= ry:
                                continue
                            pos = (c[0], y, c[1])
                            if row and row.get('wall') and pos not in protected and c not in door:
                                self.set(*pos, row['wall'])
                            wanted = role == 'W' and (dy >= STOREY_H - row['glazed_h'] if row else dy == 2)
                            if wanted:
                                candidates.append(Window(pos, face, stepped, c in corner_cells))
        positions = {(w.pos[0] + DIRS[w.face][0], w.pos[2] + DIRS[w.face][1])
                     for w in candidates}
        ground = outside_ground(positions, self.zone_data.get(f.no))
        def block(x, y, z):
            return self.p.cells.get((x, y, z), 'minecraft:air')
        writes, report = choose_windows(candidates, block=block, walls=f.walls,
            exposed=self.exposed, outside_ground=ground, protected=protected)
        for pos, state in writes.items():
            self.set(*pos, state)
        self._window_lines.extend(report_lines(f.no, report))

    def _street_face_rule(self, f: Feature) -> dict | None:
        tags = self.building_tags.get(f.no, {})
        cls = self.land_uses.get(f.no, land_use.classify_building(tags)['class'])
        roof = getattr(f, 'zone_roof', None)
        height = max(roof[c] - self._column_floor(f, c) for c in f.cells) if roof else f.storeys * STOREY_H
        return land_use.street_face_defaults(cls, tags, columns=len(f.cells), height=height)

    def _fronts_for(self, f: Feature) -> dict:
        """Street-face columns for this feature, using the mapped address street when set.

        @param f: feature mass
        @returns: ``{(x, z): face}`` from :func:`facade_plan.front_columns`
        """
        tags = self.building_tags.get(f.no, {})
        return facade_plan_mod.front_columns(
            f.cells, self.streets, occupied=self.by_no_all_cells,
            street_name=tags.get('addr:street'))

    def _shop_band_fits(self, f: Feature, cell: tuple[int, int], glass_height: int) -> bool:
        """True when a column has room for glass, the awning row and the sign row under the roof.

        @param f: feature mass
        @param cell: ``(x, z)``
        @param glass_height: opening height in blocks from the shop recipe
        @returns: whether the fascia band fits below the roof
        """
        floor = self._column_floor(f, cell)
        roof_y = self._column_roof_y(f, cell)
        roof_y = roof_y if roof_y is not None else f.b + f.storeys * STOREY_H
        return floor + glass_height + 2 < roof_y

    def _shop_front_fits(self, f: Feature, fronts: dict, rule: dict) -> bool:
        """True when at least one front column can take the shop fascia band.

        @param f: feature mass
        @param fronts: street-face columns
        @param rule: shop recipe
        @returns: whether the shell is tall enough to dress
        """
        return any(self._shop_band_fits(f, cell, rule['glass_height']) for cell in fronts)

    def _suppress_window_grid(self, f: Feature) -> bool:
        """Whether the unevidenced storey grid should stay off this shell.

        Industrial and big-box rules always replace that grid. A shop replaces it
        only when a street front is actually dressed; a shop with no mapped street,
        or a shell too low for the fascia, keeps the grid.

        @param f: feature mass
        @returns: True when the grid must not run
        """
        rule = self._street_face_rule(f)
        if not rule:
            return False
        if rule['kind'] != 'shop':
            return True
        if (self.spec.get('facade_plan') or {}).get(f.no, {}).get('faces'):
            return False
        fronts = self._fronts_for(f)
        return bool(fronts) and self._shop_front_fits(f, fronts, rule)

    def _shop_column_ok(self, f: Feature, cell: tuple[int, int]) -> bool:
        """False when a squared-front cell belongs to another building or the room inside.

        @param f: feature mass
        @param cell: ``(x, z)``
        @returns: whether the shop front may build here
        """
        if cell in getattr(f, 'inner', ()):
            return False
        if cell in f.cells:
            return True
        return cell not in self.by_no_all_cells

    def _placed_door(self, cell: tuple[int, int]):
        """Block id, floor y and face of a door leaf already written on ``cell``.

        @param cell: wall ``(x, z)``
        @returns: ``(block, floor_y, face)`` or ``None``
        """
        facing = {'north': 'N', 'south': 'S', 'east': 'E', 'west': 'W'}
        for (x, y, z), state in self.p.cells.items():
            if (x, z) != cell or '_door[' not in state or 'half=lower' not in state:
                continue
            block = state.split('[', 1)[0].removeprefix('minecraft:')
            face = next((letter for name, letter in facing.items() if f'facing={name}' in state), 'N')
            return block, y - 1, face
        return None

    def _release_recessed_door(self, cell: tuple[int, int], face: str, floor: int) -> None:
        """Take a door off a recessed column so the straight run can hold the one door.

        Drops the keep marks on the leaf, its stoop and its inward clear, and turns
        the leaf back into wall. The stoop is often the run line, and a kept air
        block there would stop the fascia.

        @param cell: recessed door ``(x, z)``
        @param face: door facing letter
        @param floor: floor y under the leaf
        """
        dx, dz = DIRS[face]
        for y in range(floor, floor + 4):
            for ox, oz in ((0, 0), (dx, dz), (-dx, -dz)):
                pos = (cell[0] + ox, y, cell[1] + oz)
                self.keep.discard(pos)
                if '_door[' in self.p.cells.get(pos, ''):
                    self.p.cells[pos] = self.wall

    def _shop_line_for_door(self, runs: list, door: tuple[int, int]):
        """Line cell in front of a door that sits on a squared run.

        @param runs: dressed runs with ``sources`` and ``line``
        @param door: registered door ``(x, z)``
        @returns: ``(line_cell, face)`` or ``None`` when the door is not on this front
        """
        for run in runs:
            owned = set(run['sources']) | set(run['line']) | set(run['fill'])
            if door not in owned:
                continue
            horizontal = run['face'] in 'NS'
            along = door[0] if horizontal else door[1]
            for cell in run['line']:
                if (cell[0] if horizontal else cell[1]) == along:
                    return cell, run['face']
        return None

    def _dress_shop_front(self, f: Feature, fronts: dict, rule: dict, name: str) -> None:
        """Glass, a pavement-level door, fascia, a contrasting sign, and a continuous awning.

        Stepped fronts are squared onto straight runs first (:func:`fit_straight_runs`).
        Glass, the door, the fascia, the sign and the awning sit on that line. The
        stair-step between a recessed column and the line is filled solid, so the
        awning and the sign touch the wall. Side, alley and party walls are not in
        ``fronts`` and stay shell. The door stands on the column floor, and its jambs
        are the line's neighbours, which a straight run always has.

        @param f: feature mass
        @param fronts: ``{(x, z): 'N'|'S'|'E'|'W'}`` from :func:`facade_plan.front_columns`
        @param rule: shop recipe from :func:`land_use.street_face_defaults`
        @param name: mapped name; empty skips the sign
        """
        runs = []
        for run in fit_straight_runs(fronts):
            line = [cell for cell in run['line'] if self._shop_column_ok(f, cell)]
            if not line:
                continue
            runs.append({
                'face': run['face'],
                'line': line,
                'fill': {cell for cell in run['fill'] if self._shop_column_ok(f, cell)},
                'sources': set(run['sources']),
            })
        if not runs:
            return
        face_of = {cell: run['face'] for run in runs for cell in run['line']}
        line_cells = set(face_of)
        longest = max(runs, key=lambda run: (len(run['line']), run['line']))
        door_line = []
        for door in list(getattr(f, 'door_cells', ()) or ()):
            hit = self._shop_line_for_door(runs, door)
            if hit is None:
                continue
            line_cell, face = hit
            if line_cell != door:
                placed = self._placed_door(door)
                block = placed[0] if placed else rule['door']
                old_floor = placed[1] if placed else self._column_floor(f, door)
                old_face = placed[2] if placed else fronts.get(door, face)
                self._release_recessed_door(door, old_face, old_floor)
                f.door_cells.discard(door)
                if getattr(f, 'door', None) == door:
                    f.door = line_cell
                self._set_door_leaf(line_cell, face, block, 'left', self._column_floor(f, line_cell))
                f.door_cells.add(line_cell)
            door_line.append(line_cell)
        place_centre = not door_line
        door_cell = longest['line'][len(longest['line']) // 2] if place_centre else None
        if door_cell is not None:
            door_line.append(door_cell)
        jambs = set()
        for cell in door_line:
            face = face_of[cell]
            lateral = (1, 0) if face in 'NS' else (0, 1)
            jambs.update(
                (cell[0] + lateral[0] * side, cell[1] + lateral[1] * side) for side in (1, -1)
            )
        colour = fascia_colour(name or f.no)
        fascia = f'{colour}_concrete'
        glass_h = rule['glass_height']
        rows = {}
        for cell in line_cells:
            if not self._shop_band_fits(f, cell, glass_h):
                continue
            floor = self._column_floor(f, cell)
            awning_y = floor + glass_h + 1
            rows[cell] = (floor, awning_y, awning_y + 1)
        if door_cell not in rows:
            door_cell = None
        doors = self._door_keepout(f)
        protected = set(self.keep)
        for (x, y, z), state in self.p.cells.items():
            if '_door[' in state:
                protected.update((x + dx, y, z + dz) for dx, dz in ((0, 0), *N4))

        def write(cell, y, state):
            roof_y = self._column_roof_y(f, cell)
            roof_y = roof_y if roof_y is not None else f.b + f.storeys * STOREY_H
            pos = (cell[0], y, cell[1])
            if y < roof_y and pos not in protected and cell not in doors:
                self.set(*pos, state)

        for run in runs:
            sample = next((cell for cell in run['line'] if cell in rows), None)
            if sample is None:
                continue
            floor, _awning_y, sign_y = rows[sample]
            for cell in run['fill']:
                if cell in line_cells:
                    write(cell, floor, self.wall)
                    continue
                for y in range(floor, sign_y + 1):
                    write(cell, y, self.wall)
        for cell, (floor, awning_y, _sign_y) in rows.items():
            if cell != door_cell and cell not in jambs:
                for y in range(floor + 1, awning_y):
                    write(cell, y, 'glass')
        if place_centre and door_cell is not None:
            floor = rows[door_cell][0]
            self._set_door_leaf(door_cell, face_of[door_cell], rule['door'], 'left', floor)
            f.door_cells.add(door_cell)
            if getattr(f, 'door', None) is None:
                f.door = door_cell
        for cell, (_floor, awning_y, sign_y) in rows.items():
            write(cell, awning_y, fascia)
            write(cell, sign_y, fascia)
        if name and rows:
            cx = statistics.mean(x for x, _z in rows)
            cz = statistics.mean(z for _x, z in rows)
            for cell in sorted(rows, key=lambda c: (abs(c[0] - cx) + abs(c[1] - cz), c)):
                _floor, _awning_y, sign_y = rows[cell]
                dx, dz = DIRS[face_of[cell]]
                pos = (cell[0] + dx, sign_y, cell[1] + dz)
                outside = self.p.cells.get(pos)
                if outside is None:
                    outside = self.live.block(*pos)
                wall = self.p.cells.get((cell[0], sign_y, cell[1]))
                if wall == bid(fascia) and outside in _OUTSIDE_AIRS:
                    self.set(*pos, f'oak_wall_sign[facing={FACE[face_of[cell]]}]')
                    self.p.sign(
                        pos, textwrap.wrap(name, width=rule['sign_wrap'])[:4],
                        colour=contrasting_sign_colour(colour), glow=False,
                    )
                    break
        if rule['awning']:
            hinge_for = {'N': 'south', 'S': 'north', 'E': 'west', 'W': 'east'}
            for cell, (_floor, awning_y, _sign_y) in rows.items():
                dx, dz = DIRS[face_of[cell]]
                outside_col = (cell[0] + dx, cell[1] + dz)
                if outside_col in self.by_no_all_cells:
                    continue
                pos = (outside_col[0], awning_y, outside_col[1])
                outside = self.p.cells.get(pos)
                if outside is None:
                    outside = self.live.block(*pos)
                if outside not in _OUTSIDE_AIRS:
                    continue
                hinge = hinge_for[face_of[cell]]
                self.set(*pos, f'oak_trapdoor[facing={hinge},half=top,open=false]')

    def street_face(self, f: Feature) -> None:
        """Dress an inferred industrial, big-box or shop street face.

        Evidenced facade rows win. Uses mapped names, street lines and the class table
        only. Shop kinds select the awning. Full blocks support diagonal frontages; writes
        stay below each column's roof. Loading marks replace yard surfaces, never buildings
        or an unevidenced lawn.
        """
        rule = self._street_face_rule(f)
        if not rule or (self.spec.get('facade_plan') or {}).get(f.no, {}).get('faces'):
            return
        tags = self.building_tags.get(f.no, {})
        fronts = self._fronts_for(f)
        if not fronts:
            print(f'{f.no} street face {rule["kind"]}: no mapped street front')
            return
        name = tags.get('name') or tags.get('brand') or tags.get('addr:unit') or ''
        if rule['kind'] == 'shop':
            if not self._shop_front_fits(f, fronts, rule):
                print(f'{f.no} street face shop: shell too low for a fascia band; left the window grid')
                return
            self._dress_shop_front(f, fronts, rule, name)
            print(
                f'{f.no} street face shop: {len(fronts)} columns; '
                f'kind={rule.get("shop_kind")}; awning={rule["awning"]}; name={name}'
            )
            return
        colour = fascia_colour(name or f.no)
        fascia = f'{colour}_concrete'
        doors = self._door_keepout(f)
        protected = set(self.keep)
        for (x, y, z), state in self.p.cells.items():
            if '_door[' in state:
                protected.update((x + dx, y, z + dz) for dx, dz in ((0, 0), *N4))

        def write(c, y, state):
            roof_y = self._column_roof_y(f, c)
            roof_y = roof_y if roof_y is not None else f.b + f.storeys * STOREY_H
            pos = (c[0], y, c[1])
            if y < roof_y and pos not in protected and c not in doors:
                self.set(*pos, state)

        fascia_h = (rule['shutter_height'] + 1 if rule['kind'] == 'industrial' else
                    rule['glazed_storeys'] * STOREY_H)
        fascia_y = {}
        for c in fronts:
            floor = self._column_floor(f, c)
            roof_y = self._column_roof_y(f, c)
            roof_y = roof_y if roof_y is not None else f.b + f.storeys * STOREY_H
            fascia_y[c] = min(floor + fascia_h, roof_y - 1)
            if rule['kind'] == 'big_box':
                for y in range(floor + 1, fascia_y[c]):
                    write(c, y, 'light_blue_stained_glass')
            write(c, fascia_y[c], fascia)
        if rule['kind'] == 'industrial':
            # The longest connected frontage gives each mapped unit one central shutter.
            remaining = set(fronts) - doors
            runs = []
            while remaining:
                run = {remaining.pop()}
                todo = list(run)
                while todo:
                    x, z = todo.pop()
                    for dx, dz in N8:
                        c = (x + dx, z + dz)
                        if c in remaining:
                            remaining.remove(c)
                            run.add(c)
                            todo.append(c)
                runs.append(run)
            if runs:
                run = sorted(max(runs, key=lambda r: (len(r), sorted(r))))
                if max(z for x, z in run) - min(z for x, z in run) > max(x for x, z in run) - min(x for x, z in run):
                    run.sort(key=lambda c: (c[1], c[0]))
                width = min(rule['shutter_width'], max(0, len(run) - 2))
                start = (len(run) - width) // 2
                opening = run[start:start + width]
                for c in opening:
                    for y in range(self._column_floor(f, c) + 1, fascia_y[c]):
                        write(c, y, 'gray_concrete')
                if opening:
                    self._loading_bay(f, opening, fronts, rule['bay_depth'])
        elif name:
            # A wall sign is attached to a real fascia cell, including on stepped frontages.
            cx = statistics.mean(x for x, z in fronts)
            cz = statistics.mean(z for x, z in fronts)
            for c in sorted(fronts, key=lambda c: (abs(c[0] - cx) + abs(c[1] - cz), c)):
                y = fascia_y[c]
                dx, dz = DIRS[fronts[c]]
                pos = (c[0] + dx, y, c[1] + dz)
                outside = self.p.cells.get(pos)
                if outside is None:
                    outside = self.live.block(*pos)
                if (self.p.cells.get((c[0], y, c[1])) == bid(fascia) and
                        outside in _OUTSIDE_AIRS):
                    self.set(*pos, f'oak_wall_sign[facing={FACE[fronts[c]]}]')
                    self.p.sign(pos, textwrap.wrap(name, width=15)[:4], colour='white', glow=True)
                    break
        print(f'{f.no} street face {rule["kind"]}: {len(fronts)} columns; name={name}')

    def _loading_bay(self, f: Feature, opening: list, fronts: dict, depth: int) -> None:
        """Paint a bay on continuous forecourt, stopping before roads and obstacles."""
        from ground_sources import ROAD_CLASSES, road_width
        paving = {'gray_concrete', 'black_concrete', 'light_gray_concrete', 'stone',
                  'smooth_stone', 'andesite', 'gravel', 'gray_terracotta'}
        rays = []
        for c in opening:
            dx, dz = DIRS[fronts[c]]
            rays.append([(c[0] + dx * step, c[1] + dz * step) for step in range(1, depth + 1)])
        if not rays:
            return
        cells = {c for ray in rays for c in ray}
        x0, x1 = min(x for x, z in cells), max(x for x, z in cells)
        z0, z1 = min(z for x, z in cells), max(z for x, z in cells)
        roads = []
        for highway, points, tags in self.streets:
            if highway.removesuffix('_link') not in ROAD_CLASSES and highway not in (
                    'service', 'living_street', 'track', 'road'):
                continue
            half = road_width(dict(tags, highway=highway)) / 2
            for a, b in zip(points, points[1:]):
                if (max(a[0], b[0]) + half >= x0 + 0.5 and min(a[0], b[0]) - half <= x1 + 0.5 and
                        max(a[1], b[1]) + half >= z0 + 0.5 and min(a[1], b[1]) - half <= z1 + 0.5):
                    roads.append((a, b, half))
        surfaces = []
        for c, ray in zip(opening, rays):
            available = []
            for x, z in ray:
                if ((x, z) in self.by_no_all_cells or any(
                        segment_distance((x + 0.5, z + 0.5), a, b)[0] <= half
                        for a, b, half in roads)):
                    break
                # Ground is beneath the opening; use local surface height on slopes.
                floor = self._column_floor(f, c)
                surface = None
                for y in range(floor + 1, floor - 5, -1):
                    state = self.p.cells.get((x, y, z))
                    if state is None:
                        state = self.live.block(x, y, z)
                    state = state or 'minecraft:air'
                    if state not in _AIRS:
                        if (bid(state)[10:] in paving and
                                not any(self._is_solid(x, y + dy, z) for dy in (1, 2))):
                            surface = (x, y, z)
                        break
                if surface is None:
                    break
                available.append(surface)
            surfaces.append(available)
        # One short ray limits the whole bay, including its far edge and unpainted interior.
        available_depth = min(map(len, surfaces))
        for i, ray in enumerate(surfaces):
            for step, pos in enumerate(ray[:available_depth], 1):
                if i in (0, len(opening) - 1) or step == available_depth:
                    self.set(*pos, 'yellow_concrete')
                    self.keep.add(pos)

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
                    rule = self._street_face_rule(f)
                    suffix = (f' — {rule["kind"]} class default' if rule and not facades else
                              ' — no evidence read' if not has_rows else '')
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
            self.set(x, eave, z, _bottom_slab_or_full(rm))

    def roof_pitched(self, features: list[Feature], own: set):
        """Hip roof from the footprint edge, with slab or opposed-stair ridge caps.

        Chebyshev distance from the roof outline sets a one-block rise per step
        (45°). ``pitched_roof_states`` picks stair facing/shape, slabs and full
        blocks for the roof material family.

        @param features: group masses sharing this roof
        @param own: owned (x, z) columns for the roof deck
        """
        rm = features[0].roof['material']
        b = features[0].b
        eave = b + features[0].storeys * STOREY_H
        dist = edge_distance_field(own, set(self.owner) | self.foreign | self.shared)
        half_heights = regularise_roof_half_heights(
            {c: 2 * (eave + d) for c, d in dist.items()},
        )
        materials = {c: rm for c in dist}
        states = pitched_roof_states(
            half_heights, materials, warnings=self._roof_warnings,
        )
        for (x, z), st in states.items():
            y = roof_block_y(half_heights[(x, z)])
            if (x, z) in self.owner:
                for yy in range(eave, y):
                    self.set(x, yy, z, self.wall)
            self._set_roof(x, y, z, st)

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
        self._window_lines = []
        for k, f in enumerate(self.features):
            self.doors(k, f)
        # Roof before windows so the eave slab can lintel the top glazed row
        # (opening checks need the finished lintel above the top glazed row).
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
        self.keep.update(interior_ground_clears(
            self.p, {c: self._column_floor(f, c) for f in self.features for c in f.inner}))
        self.p.keep = set(self.keep)
        self._preserve_real_owners()
        # same refusals as the live apply, so the designer sees them before hand-off (#99)
        guard_door_frames(self.p, self.live.block)
        guard_roof_jogs(self.p)
        # Greeblies after the outside-slab prune so awning_like/ledge overhangs survive.
        self.details()
        self.pv_rows()
        self.entrance_covers()
        for f in self.features:
            self.street_face(f)
        # Shop doors are placed with the street face, after the earlier frame check.
        guard_door_frames(self.p, self.live.block)
        self.p.keep = set(self.keep)
        self._preserve_real_owners()
        self.p.signs = [row for row in self.p.signs if '_wall_sign[' in self.p.cells.get(row[0], '')]
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
        for line in self._window_lines:
            print(line)
        for line in self.glazed_share_report():
            print(line)
        for line in roof_zone_colour_lines(self.features, aerial_rgb=self.aerial_rgb):
            print(line)
        for line in self.detail_report():
            print(line)
        for line in self._pv_lines:
            print(line)
        for line in self.footprint_overlap_report():
            print(line)
        # Material warnings are listed here, beside the unknown-id check.
        # The spec is not rewritten; a pitched roof with no triad still
        # places the full block (pitched_roof_states).
        for f in self.features:
            for line in roof_material_warnings(f.roof):
                print(line)
        valid_ids = block_id_names()
        counts = Counter(state.split('[', 1)[0] for state in self.p.cells.values())
        for name, count in sorted(counts.items()):
            if name.removeprefix('minecraft:') not in valid_ids:
                print(f'unknown block id {name} x{count} (source: vendored Minecraft 26.2 client blockstates; config/minecraft-block-ids-26.2.txt)')
        # Re-stamp from the finished geometry so roof overhangs into neighbours are
        # part of the plan identity apply will check (task 232).
        self.p.capture_inputs()
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
        @param mat: roof material family or full-block material for the pad
        @param supported: roof-supported horizontal cells of this detail
        """
        ax, ay, az = at
        sx, _sy, sz = size
        mat = family_variants(mat)['full']
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
                self._solidify_roof_under((x, ay, z), (1, 1, 1), rm, {(x, z)})
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
                place(x + dx, ay, z + dz, _bottom_slab_or_full(rm))
        elif kind == 'ledge':
            for x, y, z in self._detail_box(at, (sx, 1, sz)):
                place(x, y, z, _bottom_slab_or_full(rm))
        elif kind == 'fascia_band':
            for x, y, z in self._detail_box(at, size):
                place(x, y, z, 'terracotta')

    def pv_rows(self) -> None:
        """Place ``roofs[label].pv_arrays`` as daylight_detector rows on the finished deck.

        A column carries a detector when it is owned by the label, lies on a row
        (every ``pitch`` blocks across the rows from the box edge), its top block
        is the label's roof material (slab or full) at the array's commonest deck
        height. Details applied afterwards win. The slab under a
        detector becomes the full roof block so the detector does not hover.

        @returns: None
        """
        self._pv_lines = []
        for f in self.features:
            rows = f.roof.get('pv_arrays') or []
            if not rows:
                continue
            mats = [f.roof.get('material') or 'smooth_stone']
            mats.extend((f.roof.get('zone_materials') or {}).values())
            full_of = {}
            for mat in mats:
                variants = family_variants(mat)
                for name in variants.values():
                    full_of[name] = variants['full']
            tops: dict[tuple[int, int], int] = {}
            for (x, y, z), state in self.p.cells.items():
                if ((x, z) in f.cells and y > tops.get((x, z), -10 ** 9)
                        and bid(state)[10:] not in AIR_IDS):
                    tops[(x, z)] = y
            for row in rows:
                x0, z0, x1, z1 = row['box']
                pitch = row.get('pitch', 2)
                along_x = row['rows'] == 'x'
                want = [
                    (x, z) for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)
                    if ((z - z0) if along_x else (x - x0)) % pitch == 0
                ]
                ok: dict[tuple[int, int], int] = {}
                for c in want:
                    if c not in tops:
                        continue
                    y = tops[c]
                    name = bid(self.p.cells[(c[0], y, c[1])])[10:]
                    if name in full_of:
                        ok[c] = y
                deck = Counter(ok.values()).most_common(1)[0][0] if ok else None
                placed = 0
                for (x, z), y in sorted(ok.items()):
                    if y != deck:
                        continue
                    name = bid(self.p.cells[(x, y, z)])[10:]
                    self.set(x, y, z, full_of[name])
                    self.set(x, y + 1, z, PV_DETECTOR)
                    placed += 1
                self._pv_lines.append(
                    f'pv_array {f.no} box ({x0},{z0})..({x1},{z1}) rows={row["rows"]} pitch={pitch} '
                    f'detectors={placed} clipped={len(want) - placed} source={row["source"]}'
                )

    def details(self) -> None:
        """Place every ``details`` row after the shell and roof.

        A vent pad, chimney or skylight can replace a roof stair. Corner shapes
        are chosen again afterwards, against the stairs that remain (#390).

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
                support_names.update(family_variants(material).values())
            edge = f.roof.get('edge') or {}
            block = bid(edge.get('block') or 'air')[10:]
            if not block.endswith(('_carpet', '_trapdoor', '_wall', '_fence', '_pane')):
                if block not in (*AIR_IDS, *_OPENING_EXTRAS):
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
        # Details replace roof stairs, so choose corner states against the final skin.
        for (x, y, z), state in align_stair_shapes(self.p.cells).items():
            if self.p.cells.get((x, y, z)) != state:
                self.set(x, y, z, state)


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
    landmark_override: str = '',
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
    @param landmark_override: non-empty reason skips the landmark street-level image gate
    @returns: Plan
    """
    from builder_provenance import FEATURE_BUILDER, resolve_builder_path, builder_record, provenance
    spec_path = resolve_builder_path(spec_path)
    spec = load_spec(spec_path)
    if do_apply and not overwrite:
        seqs = landed_build_seqs(spec['name'])
        if seqs:
            raise SystemExit(
                f'{spec["name"]}: already built (journal seq {seqs[-1]}). A re-apply writes only '
                'the new plan, so old blocks outside it survive. Undo first: '
                f'element_undo.py --world <world> {spec["name"]} --apply; '
                'or pass --overwrite to apply over it anyway.')
    if do_apply:
        from readiness_evidence import require_feature_ready
        from landmark_gate import feature_cells_for_plan, require_landmark_street_check
        require_feature_ready(spec['name'])
        require_landmark_street_check(
            spec,
            override_reason=landmark_override,
            cells=feature_cells_for_plan(spec['name']),
        )
    plan = build(
        spec, footprints=footprints, world=world, osm_root=osm_root,
        zone_data=zone_data, aerial_rgb=aerial_rgb,
    )
    out = ROOT / f'verification/site/{spec["name"]}-preview.png'
    preview(plan, out, scale=4)
    print(json.dumps(plan.summary())[:600])
    apply(plan, dry=not do_apply, modifies=tuple(spec['modifies']),
          meta={'builder': builder_record('spec', spec_path), **provenance(FEATURE_BUILDER, spec_path)})      # ghost owners yield in the journal
    return plan


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('spec', help='site/builds/<name>.json')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--overwrite', action='store_true',
                    help='apply over a plan that already has a landed build (leaves stale blocks)')
    ap.add_argument(
        '--landmark-override',
        default=None,
        metavar='REASON',
        help='build a landmark without a recorded street-level image source (non-empty reason required)',
    )
    a = ap.parse_args()
    override = ''
    if a.landmark_override is not None:
        override = a.landmark_override.strip()
        if not override:
            raise SystemExit('--landmark-override needs a non-empty reason')
    run(a.spec, do_apply=a.apply, overwrite=a.overwrite, landmark_override=override)
