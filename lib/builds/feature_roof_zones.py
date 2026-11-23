"""Lidar roof-zone geometry, evidence sampling and feature-zone preparation."""

from __future__ import annotations

from collections import Counter, deque
from typing import TYPE_CHECKING
import math
import statistics
import warnings

from numpy.lib.stride_tricks import sliding_window_view as _sw
import numpy as np

from cellmath import N4
from roof_geometry import regularise_roof_half_heights, roof_block_y
from roof_material import family_variants
from site_kit import nearest_preview_colour
from world_frame import metres_aod_to_y


if TYPE_CHECKING:
    from feature_build import Feature


# Minimum DSM − DTM height counted as zoned building mass.
ZONE_LIDAR_MASS_M = 3.0


# Smaller disconnected pieces would grow misleading walls and a stone cap.
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
    regions: list | None = None,
) -> tuple[np.ndarray, np.ndarray, list, float, list[dict]]:
    """Full zone pipeline: square → lidar-mass → drop small islands → median → snap → mode → bucket → regions.

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
    @param regions: optional ``[[(x, z), ...], ...]`` polygons from the aerial. Each polygon's
        readable cells become one zone whose level is read from lidar (see ``apply_regions``);
        cells outside every polygon keep the lidar zone. Overlapping polygons are refused.
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
    if regions:
        zone, levels_used = apply_regions(zone, readable, med, xs, zs, regions, levels_used)
    return readable, zone, levels_used, axis_used, dropped


REGION_EAVE_PERCENTILE = 20   # a region's level is its low lidar edge, so a hip rises from the eave


def apply_regions(
    zone: np.ndarray,
    readable: np.ndarray,
    med: np.ndarray,
    xs: np.ndarray,
    zs: np.ndarray,
    regions: list,
    levels: list,
) -> tuple[np.ndarray, list]:
    """Let aerial polygons say where the roof zones are; lidar still says how high each is.

    The image identifies a zone (a hipped front, a flat rear extension). Its level is the
    low ``REGION_EAVE_PERCENTILE`` of the median DSM inside it, rounded to whole metres
    (downstream keys zones on the integer level), so the existing ridge logic
    can rise from that eave where lidar shows a ridge and leave a flat plateau flat.

    @param zone: lidar zone grid (levels, metres AOD)
    @param readable: readable footprint mask
    @param med: median-filtered DSM, metres AOD (same shape)
    @param xs: world x per cell
    @param zs: world z per cell
    @param regions: polygons ``[(x, z), ...]`` in MC block coordinates
    @param levels: plateau levels already in use
    @returns: ``(zone, levels)`` with each region's level added to ``levels``
    @raises SystemExit: on a polygon with no readable cells, or overlapping polygons
    """
    from coverage import scan_polygon
    out = zone.copy()
    used = list(levels)
    taken = np.zeros(readable.shape, dtype=bool)
    bbox = (int(xs.min()), int(zs.min()), int(xs.max()), int(zs.max()))
    for i, polygon in enumerate(regions):
        inside = set(scan_polygon([[tuple(p) for p in polygon]], bbox))
        mask = readable & np.fromiter(
            ((int(x), int(z)) in inside for x, z in zip(xs.ravel(), zs.ravel())),
            dtype=bool, count=xs.size,
        ).reshape(xs.shape)
        if not mask.any():
            raise SystemExit(f'zones regions[{i}]: no roof columns inside polygon')
        if (mask & taken).any():
            raise SystemExit(f'zones regions[{i}]: overlaps an earlier region')
        level = float(round(np.percentile(med[mask], REGION_EAVE_PERCENTILE)))   # whole metres, like every plateau level
        out[mask] = level
        taken |= mask
        if level not in used:
            used.append(level)
    return out, sorted(used)


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


class RoofZonePlanning:
    """Lidar roof-zone geometry, evidence sampling and feature-zone preparation.

    Used by feature_build.Build; methods share its feature-plan state.
    """

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
                regions=cfg.get('regions'),
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
