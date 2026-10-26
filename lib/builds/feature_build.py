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
                   "edge"?: {"block": "<mat>", "where": "outer"|"all"}}}
                 Absent edge = no roof edging. Present: block is the parapet /
                 coping mat; where "outer" skips zone steps, "all" edges every
                 step. Flat-roof overhang fringe only when edge is set.
  groups         [{"nos": [labels], "mode": "each"|"group", "front": "N"|"S"|"E"|"W"}]
  wall / floor   wall and interior floor materials (defaults bricks / smooth_stone)
  floor_min      minimum floor y or null
  step_max       stoop outward look (default 4)
  ledge_stair    rising stair on 1-block ledges beyond 4 columns (default false)
  door_frame     framed doors on angled walls (default true)
  keep_edits     stoop/step cells survive earlier-build clash filter (default false)
  doors          optional per-portal door map (university register ids):
                   {"<portal id>": {"leaves": 1|2, "block": "<*_door>"}}
                 leaves 2 = mirrored hinges; dry run prints each beside the portal name.
                 Absent/empty → legacy front/rear single spruce doors.
  facades        optional per-label per-face table of window rows (photo reading):
                   {"<label>": {"N"|"S"|"E"|"W": [
                     {"storey": 1..n, "glazed_h": 1..3, "pier": >=0,
                      "wall"?: "<mat>", "rhythm"?: "WW_BB_WW"}]}}
                 Dry run prints glazed share per storey per face.
  exclude        optional per-label boxes cut out of the map footprint (outline larger
                 than the real building, or neighbours sharing the outline):
                   {"<label>": [[x0, z0, x1, z1], ...]}   inclusive block coords
  zones          optional per-label lidar roof plateaus within one footprint:
                   {"<label>": {"from": "lidar",
                     "levels": [<m AOD>, ...] | "auto",
                     "bucket"?: 4, "axis_deg"?: "auto" | <deg>}}
                 Splits the mass into flat roof zones (5×5 median DSM, snap to
                 plateau levels, mode-filter, bucket on the building's rotated
                 grid). Walls rise from local lidar ground so downhill faces gain
                 storeys. Optional ``roofs[label].edge`` places a parapet on
                 outer edges or on every zone step (default: none).
                 Prints ``footprint osm=N readable=M pad=P cover=C%``. Requires
                 ``roofs[label].kind == "flat"``. Facades/doors/roofs reuse the
                 same label keys.

Path-in-footprint dry-run: scripts/footprint_overlap.py (also printed below).
Evidence contact sheet after apply: ``python3 scripts/evidence_sheet.py <name>``
(portal-facing + top-down + aerial; records path on ``*-result.json``; handoff
``end`` refuses FEATURE without it).

Usage:  python3 scripts/feature_build.py site/builds/<name>.json [--apply]
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as _sw

from script_env import ROOT  # noqa: E402
import footprint_overlap  # noqa: E402
import osm_footprints  # noqa: E402
import portals  # noqa: E402
from site_kit import Plan, World, apply, guard_door_frames, guard_roof_jogs, preview  # noqa: E402
from world_frame import metres_aod_to_y  # noqa: E402

STOREY_H = 3
GROUND = (
    'minecraft:stone', 'minecraft:gray_terracotta', 'minecraft:polished_granite',
    'minecraft:polished_diorite', 'minecraft:light_gray_concrete', 'minecraft:grass_block',
    'minecraft:dirt', 'minecraft:coarse_dirt', 'minecraft:gravel',
)
DIRS = {'N': (0, -1), 'S': (0, 1), 'W': (-1, 0), 'E': (1, 0)}
OPP = {'N': 'S', 'S': 'N', 'W': 'E', 'E': 'W'}
N8 = [(dx, dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1) if dx or dz]
N4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
FACE = {'S': 'south', 'N': 'north', 'W': 'west', 'E': 'east'}
TOWARD = {'S': 'north', 'N': 'south', 'W': 'east', 'E': 'west'}
ROOF_KINDS = frozenset({'flat', 'pitched'})
EDGE_WHERE = frozenset({'outer', 'all'})
ZONE_FROM = frozenset({'lidar'})


# ---- lidar roof zones (pure helpers; fixture grids in tests) --------------------

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
) -> tuple[np.ndarray, np.ndarray, list, float]:
    """Full zone pipeline: square → 5×5 median → snap → mode-filter → bucket.

    Arrays share the same 2-D indexing (row/col). ``xs``/``zs`` hold world block
    coordinates for each cell (needed for rotated bucketing).

    @param dsm: DSM metres AOD
    @param outline: boolean vector-way footprint
    @param levels: plateau list or ``\"auto\"``
    @param xs: world x grid
    @param zs: world z grid
    @param bucket: rotated-grid bucket size
    @param axis_deg: degrees or ``\"auto\"`` (PCA of readable cells)
    @returns: ``(readable, zone, levels_used, axis_deg_used)``
    """
    readable = square_footprint(outline)
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
    return readable, zone, levels_used, axis_used


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
    spec.setdefault('doors', {})
    spec.setdefault('facades', {})
    spec.setdefault('zones', {})
    spec.setdefault('exclude', {})
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
    for g in spec['groups']:
        if g['front'] not in DIRS or g.get('mode', 'each') not in ('each', 'group'):
            raise SystemExit(f'{path}: bad group {g}')
        g.setdefault('mode', 'each')
        for no in g['nos']:
            if no not in labels or no not in spec['storeys'] or no not in spec['roofs']:
                raise SystemExit(f'{path}: label {no} needs a way, storeys and roof')
    _validate_doors(path, spec)
    _validate_facades(path, spec, labels)
    _validate_zones(path, spec, labels)
    _validate_exclude(path, spec, labels)
    return spec


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
        if roof.get('kind') != 'flat':
            raise SystemExit(f'{path}: zones[{lab}] requires roofs[{lab}].kind == \"flat\"')


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


def _validate_doors(path, spec: dict) -> None:
    """Validate optional per-portal door map.

    @param path: spec path (for error text)
    @param spec: feature spec being loaded
    @raises SystemExit: on invalid door entries
    """
    doors = spec.get('doors') or {}
    if not isinstance(doors, dict):
        raise SystemExit(f'{path}: doors must be an object')
    for pid, row in doors.items():
        if not isinstance(row, dict):
            raise SystemExit(f'{path}: doors[{pid}] must be an object')
        leaves = row.get('leaves')
        block = row.get('block')
        if not isinstance(leaves, int) or leaves not in (1, 2):
            raise SystemExit(f'{path}: doors[{pid}].leaves must be 1 or 2')
        if not isinstance(block, str) or not block.endswith('_door') or 'trap' in block:
            raise SystemExit(f'{path}: doors[{pid}].block must be a *_door id (not trapdoor)')


def _validate_facades(path, spec: dict, labels: set) -> None:
    """Validate optional per-face facade table rows.

    @param path: spec path (for error text)
    @param spec: feature spec being loaded
    @param labels: known way labels
    @raises SystemExit: on invalid facade structure
    """
    facades = spec.get('facades') or {}
    if not isinstance(facades, dict):
        raise SystemExit(f'{path}: facades must be an object')
    for lab, faces in facades.items():
        if lab not in labels:
            raise SystemExit(f'{path}: facades label {lab} has no way')
        if not isinstance(faces, dict):
            raise SystemExit(f'{path}: facades[{lab}] must be face→rows')
        n_storeys = spec['storeys'][lab]
        for face, rows in faces.items():
            if face not in DIRS:
                raise SystemExit(f'{path}: facades[{lab}] bad face {face}')
            if not isinstance(rows, list) or not rows:
                raise SystemExit(f'{path}: facades[{lab}][{face}] needs a non-empty row list')
            seen = set()
            for row in rows:
                if not isinstance(row, dict):
                    raise SystemExit(f'{path}: facades[{lab}][{face}] row must be an object')
                storey = row.get('storey')
                glazed_h = row.get('glazed_h')
                pier = row.get('pier')
                if not isinstance(storey, int) or storey < 1 or storey > n_storeys:
                    raise SystemExit(
                        f'{path}: facades[{lab}][{face}] storey must be int 1..{n_storeys}')
                if storey in seen:
                    raise SystemExit(f'{path}: facades[{lab}][{face}] duplicate storey {storey}')
                seen.add(storey)
                if not isinstance(glazed_h, int) or glazed_h < 1 or glazed_h > STOREY_H:
                    raise SystemExit(
                        f'{path}: facades[{lab}][{face}] glazed_h must be int 1..{STOREY_H}')
                if not isinstance(pier, int) or pier < 0:
                    raise SystemExit(f'{path}: facades[{lab}][{face}] pier must be int >= 0')
                if 'rhythm' in row and row['rhythm'] is not None:
                    if not isinstance(row['rhythm'], str) or not row['rhythm']:
                        raise SystemExit(f'{path}: facades[{lab}][{face}] rhythm must be a non-empty string')
                    bad = [c for c in row['rhythm'] if c not in 'WB_']
                    if bad:
                        raise SystemExit(
                            f'{path}: facades[{lab}][{face}] rhythm chars must be W/B/_ (got {bad!r})')
                if 'wall' in row and row['wall'] is not None and not isinstance(row['wall'], str):
                    raise SystemExit(f'{path}: facades[{lab}][{face}] wall must be a string')


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
    ):
        """
        @param spec: validated feature spec
        @param footprints: optional ``{label: set[(x,z)]}`` (else OSMIndex from ways)
        @param world: optional ``World``-like with ``block(x,y,z)`` (else live ``World()``)
        @param osm_root: optional vector-map clip root for ``OSMIndex`` (unit-test /
            fixture trees only). Production always uses this world's
            ``reference/vector_map`` via ``vector_map_clips.vector_map_clip_root``;
            do not point at another world's clips.
        @param zone_data: optional ``{label: {dsm, ground_y, x0, z0}}`` grids for tests
            (arrays indexed ``[z, x]`` / row=z; ``ground_y`` is block Y; ``dsm`` metres AOD).
            When omitted, zoned labels fetch composite lidar via ``lidar_provider.mc_heights``.
        """
        self.spec = spec
        self.name = spec['name']
        self.groups = spec['groups']
        self.wall = spec['wall']
        self.floor_mat = spec['floor']
        self.keep = set()
        self.p = Plan(self.name)
        self.live = world if world is not None else World()
        self.zone_data = zone_data or {}
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

    def set(self, x, y, z, s):
        self.p.set(x, y, z, s)

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
        """Floor level from plot-edge surface, honouring floor_min.

        @param f: feature mass
        @returns: floor y
        """
        ring = set()
        for (x, z) in f.cells:
            for dx in range(-7, 8):
                for dz in range(-7, 8):
                    c = (x + dx, z + dz)
                    if max(abs(dx), abs(dz)) >= 4 and c not in self.by_no_all_cells:
                        ring.add(c)
        near = {(x + dx, z + dz) for (x, z) in f.cells for dx in range(-3, 4) for dz in range(-3, 4)}
        cols = ring - near
        self._prefetch_live((x, y, z) for x, z in cols for y in range(75, 30, -1))
        ys = [self.surf(x, z) for x, z in cols]
        ys = [y for y in ys if y]
        if not ys:
            y = self.spec['floor_min'] if self.spec['floor_min'] is not None else 50
        else:
            y = int(statistics.median(ys))
            if self.spec['floor_min'] is not None:
                y = max(y, self.spec['floor_min'])
        return y

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
            readable, zone, _levels, _axis = assign_roof_zones(
                dsm, outline, cfg['levels'], xs, zs,
                bucket=int(cfg.get('bucket', 4)),
                axis_deg=cfg.get('axis_deg', 'auto'),
            )
            self._zone_pad_lines.append(footprint_pad_line(outline, readable))
            new_cells = mask_to_cells(readable, x0, z0)
            roof: dict = {}
            gy: dict = {}
            zl: dict = {}
            for z in range(depth):
                for x in range(width):
                    if not readable[z, x]:
                        continue
                    wx, wz = x0 + x, z0 + z
                    roof[(wx, wz)] = metres_aod_to_y(float(zone[z, x]))
                    gy[(wx, wz)] = int(ground[z, x])
                    zl[(wx, wz)] = int(round(float(zone[z, x])))
            # neighbourhood ground for expose() outside the mass
            for z in range(depth):
                for x in range(width):
                    wx, wz = x0 + x, z0 + z
                    gy.setdefault((wx, wz), int(ground[z, x]))
            f.cells = new_cells
            f.zone_roof = roof
            f.zone_ground = gy
            f.zone_level = zl
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
                need.append((x, b, z))
                need.extend((x, y, z) for y in range(b + 1, top_clear + 1))
        self._prefetch_live(need)
        for x, z, b, own in work:
            for y in range(37, b):
                self.set(x, y, z, 'stone')
            if own:
                rng = range(b, top_clear + 1)
            else:
                if (self.live.block(x, b, z) or 'minecraft:air') == 'minecraft:air':
                    self.set(x, b, z, 'stone')
                rng = range(b + 1, top_clear + 1)
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
        Optional ``f.roof['edge']`` places a parapet (default: none). Interior
        floor slabs follow the facade storey rhythm from ``f.b`` (median ground).

        @param k: feature index (ownership / wall rivalry)
        @param f: feature with ``zone_roof`` / ``zone_ground`` filled
        """
        del k  # ownership already baked into f.cells; kept for call-site parity
        roof = f.zone_roof
        gy = f.zone_ground
        rm = f.roof['material']
        edge = f.roof.get('edge')
        datum = f.b

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
            self.set(x, ry, z, rm)
            if wants_edge(x, z, ry):
                self.set(x, ry + 1, z, edge['block'])
            else:
                self.set(x, ry + 1, z, 'air')

    def framed(self, f: Feature, cand, d):
        """Door candidates with a wall cell on both sides along the wall.

        @param f: feature mass
        @param cand: candidate wall cells
        @param d: exposure direction
        @returns: filtered candidate list
        """
        if not self.spec['door_frame']:
            return cand
        lat = ((0, 1), (0, -1)) if d in 'EW' else ((1, 0), (-1, 0))
        strict = [c for c in cand
                  if all((c[0] + lx, c[1] + lz) in f.walls and self.exposed((c[0] + lx, c[1] + lz), d) for lx, lz in lat)]
        if strict:
            return strict
        return [c for c in cand if all((c[0] + lx, c[1] + lz) in f.walls for lx, lz in lat)] or cand

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
        """Place doors: portal map from the spec when present, else front/rear spruce.

        @param k: feature index
        @param f: feature mass
        """
        door_specs = self.spec.get('doors') or {}
        if door_specs:
            self.portal_doors(f, door_specs)
        else:
            self.default_doors(k, f)

    def _door_candidates(self, f: Feature, face: str) -> list:
        """Framed wall cells on ``face`` with interior behind them.

        @param f: feature mass
        @param face: ``N``/``S``/``E``/``W``
        @returns: candidate ``(x, z)`` cells
        """
        dx, dz = DIRS[face]
        cand = [c for c in f.walls if self.exposed(c, face) and (c[0] - dx, c[1] - dz) in f.inner]
        return self.framed(f, cand, face)

    def _set_door_leaf(self, cell, face: str, block: str, hinge: str, b: int):
        """Write one framed door leaf (lower+upper) and clear the stoop column.

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
        st = (cell[0] + dx, cell[1] + dz)
        self.set(st[0], b, st[1], 'smooth_stone')
        for y in range(b + 1, b + 4):
            self.set(st[0], y, st[1], 'air')

    def portal_doors(self, f: Feature, door_specs: dict) -> None:
        """Place per-portal doors from the spec; print each beside its portal name.

        @param f: feature mass
        @param door_specs: ``{portal_id: {leaves, block}}``
        """
        by_id = {p['id']: p for p in portals.load()}
        placed: set = set()
        f.door = None
        f.rear = None
        f.door_cells = set()
        reach2 = portals.DOOR_REACH ** 2
        for pid, row in sorted(door_specs.items()):
            portal = by_id.get(pid)
            if portal is None:
                print(f'door {pid}: unknown portal: skipped')
                continue
            px, pz = portal['x'], portal['z']
            leaves = row['leaves']
            block = row['block']
            # Nearest raw wall within reach first, then framing on that local set
            # only — whole-face framed() would drop nearer non-strict candidates (#114).
            best = None
            for face in DIRS:
                dx, dz = DIRS[face]
                near = []
                for c in f.walls:
                    if c in placed:
                        continue
                    if not (self.exposed(c, face) and (c[0] - dx, c[1] - dz) in f.inner):
                        continue
                    d2 = (c[0] - px) ** 2 + (c[1] - pz) ** 2
                    if d2 <= reach2:
                        near.append(c)
                for c in self.framed(f, near, face):
                    d2 = (c[0] - px) ** 2 + (c[1] - pz) ** 2
                    if best is None or d2 < best[0]:
                        best = (d2, c, face)
            if best is None:
                print(f'door {pid} "{portal["label"]}": no wall near {px},{pz}: skipped')
                continue
            _d2, c0, face = best
            lat = (1, 0) if face in 'NS' else (0, 1)
            cells = [c0]
            if leaves == 2:
                opts = [
                    (c0[0] + lat[0], c0[1] + lat[1]),
                    (c0[0] - lat[0], c0[1] - lat[1]),
                ]
                opts.sort(key=lambda c: (c[0] - px) ** 2 + (c[1] - pz) ** 2)
                dx, dz = DIRS[face]
                face_near = [
                    c for c in f.walls
                    if c not in placed
                    and self.exposed(c, face) and (c[0] - dx, c[1] - dz) in f.inner
                    and (c[0] - px) ** 2 + (c[1] - pz) ** 2 <= reach2
                ]
                face_ok = set(self.framed(f, face_near, face))
                pair = next(
                    (c for c in opts if c not in placed and c in face_ok),
                    None,
                )
                if pair is None:
                    print(f'door {pid} "{portal["label"]}": no pair cell; using 1-leaf')
                    leaves = 1
                else:
                    cells = sorted([c0, pair], key=lambda c: c[0] * lat[0] + c[1] * lat[1])
            hinges = ('left',) if leaves == 1 else ('left', 'right')
            b = self._column_floor(f, cells[0])
            for cell, hinge in zip(cells, hinges):
                self._set_door_leaf(cell, face, block, hinge, self._column_floor(f, cell))
                placed.add(cell)
                f.door_cells.add(cell)
            dx, dz = DIRS[face]
            self.step_out((cells[0][0] + dx, cells[0][1] + dz), face, b)
            if f.door is None:
                f.door = cells[0]
            print(
                f'door {pid} "{portal["label"]}" {leaves}-leaf {block} '
                f'at {cells[0][0]},{b + 1},{cells[0][1]} facing {FACE[face]}'
            )

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

    def default_doors(self, k: int, f: Feature):
        """Legacy front and rear single spruce doors when the spec has no ``doors`` map.

        @param k: feature index (unused; kept for call-site parity)
        @param f: feature mass
        """
        del k  # call-site parity with doors()
        front = f.front
        r = OPP[front]
        cand = self._door_candidates(f, front)
        if not cand:
            return
        if front == 'N':
            front_edge = min(c[1] for c in cand)
            cand = [c for c in cand if c[1] <= front_edge + 2]
            mid = sum(c[0] for c in cand) / len(cand)
            c0 = min(cand, key=lambda c: abs(c[0] - mid))
        elif front == 'S':
            front_edge = max(c[1] for c in cand)
            cand = [c for c in cand if c[1] >= front_edge - 2]
            mid = sum(c[0] for c in cand) / len(cand)
            c0 = min(cand, key=lambda c: abs(c[0] - mid))
        else:
            front_edge = min(c[0] for c in cand) if front == 'W' else max(c[0] for c in cand)
            if front == 'W':
                cand = [c for c in cand if c[0] <= front_edge + 1]
            else:
                cand = [c for c in cand if c[0] >= front_edge - 1]
            mid = sum(c[1] for c in cand) / len(cand)
            c0 = min(cand, key=lambda c: abs(c[1] - mid))
        f.door = c0
        f.door_cells = {c0}
        b0 = self._column_floor(f, c0)
        self._set_door_leaf(c0, front, 'spruce_door', 'left', b0)
        fx, fz = DIRS[front]
        self.step_out((c0[0] + fx, c0[1] + fz), front, b0)
        cand = self._door_candidates(f, r)
        if cand:
            if r in 'NS':
                e = min(c[1] for c in cand) if r == 'N' else max(c[1] for c in cand)
                cc = [c for c in cand if (c[1] <= e + 2 if r == 'N' else c[1] >= e - 2)]
                m = sum(c[0] for c in cc) / len(cc)
                c1 = min(cc, key=lambda c: abs(c[0] - m))
            else:
                e = min(c[0] for c in cand) if r == 'W' else max(c[0] for c in cand)
                cc = [c for c in cand if (c[0] <= e + 1 if r == 'W' else c[0] >= e - 1)]
                m = sum(c[1] for c in cc) / len(cc)
                c1 = min(cc, key=lambda c: abs(c[1] - m))
            f.rear = c1
            f.door_cells.add(c1)
            b1 = self._column_floor(f, c1)
            self._set_door_leaf(c1, r, 'spruce_door', 'right', b1)
            sx, sz = c1[0] + DIRS[r][0], c1[1] + DIRS[r][1]
            self.step_out((sx, sz), r, b1)
        else:
            f.rear = None

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
        """Punch openings from the facade table, or placeholder pier rhythm if absent."""
        facades = (self.spec.get('facades') or {}).get(f.no)
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
                                self.set(c[0], y, c[1], pane(axis))
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
                self.set(c[0], y, c[1], pane(axis))

    def glazed_share_report(self) -> list[str]:
        """Glazed share of each storey per face (for dry-run photo compare).

        @returns: human-readable lines ``<label> face <F> storey <n>: glazed a/b (p%)``
        """
        lines = []
        for f in self.features:
            facades = (self.spec.get('facades') or {}).get(f.no) or {}
            if not facades:
                continue
            for face in sorted(facades):
                run = self.face_run(f, face)
                if not run:
                    continue
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
                    lines.append(
                        f'{f.no} face {face} storey {s + 1}: glazed {glazed}/{total} ({pct}%)')
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
        done = {}
        for c, d in dist.items():
            y = eave + d // 2
            t = 'bottom' if d % 2 == 0 else 'top'
            key = (y, 1 if t == 'top' else 0)
            cur = done.get(c)
            if cur is None or key > cur[0]:
                done[c] = (key, t, c in self.owner)
        for (x, z), (key, t, inside) in done.items():
            y = key[0]
            if inside:
                for yy in range(eave, y):
                    self.set(x, yy, z, self.wall)
            self.set(x, y, z, f'{rm}_slab[type={t}]')

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
            if 'slab' in v[0]:
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
        for k, f in enumerate(self.features):
            self.doors(k, f)
            self.windows(f)
        self.roof()
        if self.idx is not None:
            self._prune_outside_guard_hits()
        own1 = {(x + dx, z + dz) for (x, z) in self.owner for dx in (-1, 0, 1) for dz in (-1, 0, 1)}
        leaf_q = [q for q in self.p.cells if (q[0], q[2]) not in own1]
        self._prefetch_live(leaf_q)
        for q in list(self.p.cells):
            if (q[0], q[2]) not in own1 and 'leaves' in (self.live.block(*q) or ''):
                del self.p.cells[q]
        self.p.outside_footprint_ok = 'roof-overhang fringe slabs; evidence: footprint-hits json'
        # cells of earlier real builds are theirs (ghost owners yield: the shell overwrites them)
        from cell_registry import view_registry
        owned = view_registry().owned(self.p.cells, exclude={self.name}, real_only=True)
        for q in [q for q, st in self.p.cells.items() if q in owned and owned[q][1] != st and q not in self.keep]:
            del self.p.cells[q]
        # same refusals as the live apply, so the designer sees them before hand-off (#99)
        guard_door_frames(self.p, self.live.block)
        guard_roof_jogs(self.p)
        for line in self._zone_pad_lines:
            print(line)
        for f in self.features:
            bits = [
                f.no, 'b', f.b, 'storeys', f.storeys, 'cells', len(f.cells),
                'door', getattr(f, 'door', None), 'rear', getattr(f, 'rear', None),
            ]
            if getattr(f, 'zone_roof', None):
                bits.append(f'zones={sorted(set(f.zone_level.values()))}')
            print(*bits)
        for line in self.glazed_share_report():
            print(line)
        for line in self.footprint_overlap_report():
            print(line)
        return self.p

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


def build(
    spec: dict,
    footprints: dict | None = None,
    world=None,
    osm_root=None,
    zone_data: dict | None = None,
) -> Plan:
    """Build a feature plan from a validated spec.

    @param spec: validated feature spec
    @param footprints: optional label→cells map (tests / offline)
    @param world: optional World-like
    @param osm_root: optional clip root (tests / fixtures only; not cross-world)
    @param zone_data: optional lidar grids for zoned labels (tests)
    @returns: Plan
    """
    return Build(
        spec, footprints=footprints, world=world, osm_root=osm_root, zone_data=zone_data,
    ).build()


def run(
    spec_path,
    do_apply: bool = False,
    footprints: dict | None = None,
    world=None,
    osm_root=None,
    zone_data: dict | None = None,
) -> Plan:
    """Load spec, build plan, preview, and site_kit.apply (dry unless do_apply).

    @param spec_path: path to site/builds/<name>.json
    @param do_apply: when True, live apply; otherwise dry run
    @param footprints: optional label→cells map
    @param world: optional World-like
    @param osm_root: optional clip root (tests / fixtures only; not cross-world)
    @param zone_data: optional lidar grids for zoned labels (tests)
    @returns: Plan
    """
    spec = load_spec(spec_path)
    plan = build(
        spec, footprints=footprints, world=world, osm_root=osm_root, zone_data=zone_data,
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
    a = ap.parse_args()
    run(a.spec, do_apply=a.apply)
