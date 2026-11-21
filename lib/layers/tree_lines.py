"""Spike #323: unmapped tree lines and belts along mapped hedges and lanes.

The trees source gate (#317) keeps a crown on open land (park, rural, natural classes) only
with mapped trees, a wood polygon, or lidar standing height of at least 2 m. Hedgerow trees
and tree belts that the map does not mark as wood fail it wherever the lidar reader sees
through the canopy, which the last-return composite does (task 320 findings).

This module reads a second kind of evidence without touching ``trees_layer``:

* a **corridor** of columns within reach of a mapped hedge or lane,
* **canopy** columns in that corridor, from standing height (first-return when cached, else
  last-return) or from aerial darkness calibrated per cell against mapped wood and open grass,
* a **belt**: canopy grown outward from the corridor through connected canopy columns on gated
  land, so a belt that runs along a lane is kept while open pasture beyond it stays clear.

Everything is a pure function of explicit arrays except ``standing_height`` and ``propose``,
which load evidence for one cell. Arrays are indexed ``[x - x0, z - z0]`` like the lidar reader.
"""
from __future__ import annotations

from collections import deque

import numpy as np

# Lanes: rural carriageways and tracks whose verges carry hedgerow trees. Footpaths are left out:
# they cross open pasture as often as they follow a boundary.
LANE_HIGHWAYS = frozenset({'service', 'track', 'unclassified', 'tertiary', 'bridleway'})
CORRIDOR_REACH = 4        # blocks from a hedge column or lane edge that still count as its verge
BELT_REACH = 32           # blocks a belt may grow from the corridor through connected canopy
MIN_STANDING_M = 2.0      # same standing height as the #317 gate's lidar source
MIN_REFERENCE_COLUMNS = 200  # wood and open-grass samples needed to calibrate aerial darkness
DARK_SHARE = 0.5          # threshold sits this share of the way from the wood to the open median
CROWN_SPACING = 5         # minimum centre separation, matches trees_layer FILL_SPACING
DEFAULT_RADIUS = 3.0      # crown radius when no standing height is measured
GATED_CLASSES = frozenset({'park', 'rural', 'natural'})
# Foliage rule radius guide (rules/foliage.md): height 13 = r 6+, 7-10 = r 3.5-5, 3-5 = bush.
RADIUS_BY_HEIGHT = ((13.0, 6.0), (10.0, 5.0), (7.0, 4.0), (5.0, 3.5), (3.0, 3.0))


def corridor(hedge, lanes, bounds: tuple[int, int, int, int], reach: int = CORRIDOR_REACH,
             highways=LANE_HIGHWAYS) -> set:
    """Columns within ``reach`` of a mapped hedge column or a lane's edge, clipped to ``bounds``.

    @param hedge: iterable of world ``(x, z)`` hedge columns
    @param lanes: ``{'highway', 'points', 'half'}`` lane centrelines (``OSMGround.road_lines`` /
        ``path_lines`` rows); rows whose highway is not in ``LANE_HIGHWAYS`` are ignored
    @param bounds: inclusive ``(x0, z0, x1, z1)``
    @param reach: verge width in blocks; 0 gives the carriageway itself
    @param highways: highway values that count as lanes, or None for every row
    @returns: set of world ``(x, z)``
    """
    x0, z0, x1, z1 = bounds
    out = set()
    r2 = reach * reach
    for hx, hz in hedge:
        for dx in range(-reach, reach + 1):
            for dz in range(-reach, reach + 1):
                if dx * dx + dz * dz <= r2:
                    out.add((hx + dx, hz + dz))
    for lane in lanes:
        if highways is not None and lane.get('highway') not in highways:
            continue
        width = float(lane.get('half') or 1.0) + reach
        if reach == 0:
            width = max(width - 0.5, 0.5)     # carriageway proper, not its kerb column
        pts = [(float(a), float(b)) for a, b in lane['points']]
        for (ax, az), (bx, bz) in zip(pts, pts[1:]):
            lo_x, hi_x = int(np.floor(min(ax, bx) - width)), int(np.ceil(max(ax, bx) + width))
            lo_z, hi_z = int(np.floor(min(az, bz) - width)), int(np.ceil(max(az, bz) + width))
            lo_x, hi_x, lo_z, hi_z = max(lo_x, x0), min(hi_x, x1), max(lo_z, z0), min(hi_z, z1)
            if lo_x > hi_x or lo_z > hi_z:
                continue
            xs, zs = np.meshgrid(np.arange(lo_x, hi_x + 1), np.arange(lo_z, hi_z + 1), indexing='ij')
            vx, vz = bx - ax, bz - az
            seg2 = vx * vx + vz * vz
            t = np.zeros_like(xs, dtype=float) if seg2 == 0 else np.clip(
                ((xs - ax) * vx + (zs - az) * vz) / seg2, 0.0, 1.0)
            d2 = (xs - (ax + t * vx)) ** 2 + (zs - (az + t * vz)) ** 2
            for x, z in zip(xs[d2 <= width * width], zs[d2 <= width * width]):
                out.add((int(x), int(z)))
    return {q for q in out if x0 <= q[0] <= x1 and z0 <= q[1] <= z1}


def dark_canopy(lum: np.ndarray, wood: np.ndarray, open_grass: np.ndarray,
                share: float = DARK_SHARE, min_columns: int = MIN_REFERENCE_COLUMNS) -> tuple[np.ndarray | None, dict]:
    """Aerial canopy by darkness, calibrated on this cell's own mapped wood and open grass.

    Shaded tree crowns read darker than sunlit grass in a summer aerial, but absolute
    brightness changes between sheets, so the threshold is placed between the two medians.
    Without enough of either reference the read is refused rather than guessed.

    @param lum: per-column aerial luminance
    @param wood: bool mask of mapped wood/scrub/forest columns
    @param open_grass: bool mask of mapped open grass (meadow, grass, farmland, grassland)
    @param share: where the threshold sits between the wood median (0) and the open median (1)
    @param min_columns: minimum reference columns of each kind
    @returns: ``(mask or None, info)``; info has the medians, threshold or refusal reason
    """
    n_wood, n_open = int(wood.sum()), int(open_grass.sum())
    info = {'wood_columns': n_wood, 'open_columns': n_open}
    if n_wood < min_columns or n_open < min_columns:
        info['refused'] = f'too few reference columns (wood {n_wood}, open {n_open}; need {min_columns})'
        return None, info
    w, o = float(np.median(lum[wood])), float(np.median(lum[open_grass]))
    info.update(wood_median=round(w, 1), open_median=round(o, 1))
    if w >= o:
        info['refused'] = 'mapped wood is not darker than open grass in this aerial'
        return None, info
    threshold = w + share * (o - w)
    info['threshold'] = round(threshold, 1)
    return lum <= threshold, info


def belt(seeds: set, evidence: set, allowed: set, reach: int = BELT_REACH) -> dict:
    """Grow canopy outward from corridor seeds through 4-connected evidence columns.

    @param seeds: corridor columns that already hold canopy evidence
    @param evidence: every canopy-evidence column
    @param allowed: columns the belt may enter (gated classes, not water/road)
    @param reach: maximum steps from the nearest seed
    @returns: ``{(x, z): steps from the corridor}`` including the seeds at 0
    """
    dist = {q: 0 for q in seeds if q in allowed}
    queue = deque(dist)
    while queue:
        q = queue.popleft()
        d = dist[q]
        if d >= reach:
            continue
        for n in ((q[0] + 1, q[1]), (q[0] - 1, q[1]), (q[0], q[1] + 1), (q[0], q[1] - 1)):
            if n not in dist and n in evidence and n in allowed:
                dist[n] = d + 1
                queue.append(n)
    return dist


def radius_for(height: float | None) -> float:
    """Crown radius from standing height using the foliage rule guide; default when unmeasured.

    @param height: metres, or None/NaN
    @returns: radius in blocks
    """
    if height is None or not np.isfinite(height):
        return DEFAULT_RADIUS
    for h, r in RADIUS_BY_HEIGHT:
        if height >= h:
            return r
    return DEFAULT_RADIUS


def place_crowns(columns, score: dict, height: dict | None = None, spacing: int = CROWN_SPACING) -> list:
    """Deterministic greedy crowns over ``columns``: best score first, at least ``spacing`` apart.

    @param columns: iterable of world ``(x, z)`` that may hold a crown centre
    @param score: ``{(x, z): float}`` higher is more tree-like (missing = 0)
    @param height: optional ``{(x, z): metres}`` for crown radius
    @param spacing: minimum centre separation in blocks
    @returns: ``[(x, z, r)]``
    """
    order = sorted(columns, key=lambda q: (-score.get(q, 0.0), q[0], q[1]))
    taken: list = []
    s2 = spacing * spacing
    for q in order:
        if all((q[0] - a) ** 2 + (q[1] - b) ** 2 >= s2 for a, b, _r in taken):
            taken.append((q[0], q[1], radius_for((height or {}).get(q))))
    return taken


def first_return_height(dtm: np.ndarray, dsm_first: np.ndarray) -> np.ndarray:
    """Standing height from the first-return surface: ``max(dsm_first - dtm, 0)``, NaN kept.

    Not the provider's ``canopy`` layer, which is first minus last return (task 320).
    """
    h = np.asarray(dsm_first, dtype=float) - np.asarray(dtm, dtype=float)
    return np.where(np.isfinite(h), np.maximum(h, 0.0), np.nan)


def standing_height(cell: tuple[int, int], margin: int = 0, client=None) -> tuple[np.ndarray | None, str]:
    """Standing height for a cell: first-return when every tile is cached, else last-return.

    Never downloads. The last-return fallback is what ``trees_layer.cell_ndsm`` reads today.

    @param cell: ``(tx, tz)``
    @param margin: halo columns
    @param client: optional tile client (tests); default ``lidar_provider.tile_client(download=False)``
    @returns: ``(metres array or None, 'first-return' | 'last-return' | 'none')``
    """
    from cellmath import CELL
    try:
        import lidar_provider as lp
        lidar = client or lp.tile_client(download=False)
        x0, z0 = cell[0] * CELL - margin, cell[1] * CELL - margin
        x1, z1 = x0 + CELL - 1 + 2 * margin, z0 + CELL - 1 + 2 * margin
        h = lp.mc_heights_float(lidar, x0, z0, x1, z1)
        last = np.maximum(np.asarray(h['dsm'], float) - np.asarray(h['dtm'], float), 0.0)
    except Exception:
        return None, 'none'
    if not (hasattr(lidar, 'missing') and hasattr(lidar, 'sample')):
        return last, 'last-return'
    from world_frame import load_frame, mc_grid_to_en
    frame = load_frame()
    xs, zs = np.meshgrid(np.arange(x0, x1 + 1), np.arange(z0, z1 + 1), indexing='ij')
    e, n = mc_grid_to_en(xs, zs, frame)
    try:
        if lidar.missing('DSM_FIRST', float(e.min()), float(n.min()), float(e.max()), float(n.max())):
            return last, 'last-return'
        first = first_return_height(h['dtm'], lidar.sample('DSM_FIRST', e, n, False))
    except Exception:
        return last, 'last-return'
    return first, 'first-return'


def gated_crowns(crowns, x0: int, z0: int, height: np.ndarray | None, near: int = 2) -> dict:
    """Which local crowns the #317 lidar source would keep: standing height >= 2 m within ``near``.

    @param crowns: ``[(world x, world z, r)]``
    @param x0: world x of ``height[0, :]``
    @param z0: world z of ``height[:, 0]``
    @param height: metres or None
    @returns: ``{(x, z): bool}``
    """
    out = {}
    for x, z, _r in crowns:
        ok = False
        if height is not None:
            i, j = x - x0, z - z0
            a, b = max(i - near, 0), max(j - near, 0)
            win = height[a:i + near + 1, b:j + near + 1]
            ok = bool(win.size and np.nanmax(np.where(np.isfinite(win), win, -1)) >= MIN_STANDING_M)
        out[(x, z)] = ok
    return out


def line_evidence(bounds, hedge, lanes, lum, wood, open_grass, allowed, height=None, height_source='none',
                  covered=None):
    """Corridor, canopy evidence and belt for one area, from explicit arrays.

    @param bounds: inclusive ``(x0, z0, x1, z1)`` matching the arrays
    @param hedge: hedge columns
    @param lanes: lane centreline rows
    @param lum: aerial luminance ``[x, z]``
    @param wood: bool mapped wood ``[x, z]``
    @param open_grass: bool mapped open grass ``[x, z]``
    @param allowed: bool columns where a belt may grow ``[x, z]`` (gated class, not road/water)
    @param height: standing height metres ``[x, z]`` or None
    @param height_source: label recorded in the result
    @param covered: bool columns already covered by wood/scrub/forest polygons (they pass the gate
        today, so they are not new evidence); default ``wood``
    @returns: dict with ``corridor``, ``evidence``, ``belt`` (column -> steps), ``dark`` info, counts
    """
    x0, z0, _x1, _z1 = bounds
    dark, dark_info = dark_canopy(lum, wood, open_grass)
    canopy = np.zeros(lum.shape, dtype=bool)
    if dark is not None:
        canopy |= dark
    if height is not None:
        canopy |= np.where(np.isfinite(height), height, 0) >= MIN_STANDING_M
    canopy &= allowed
    # Mapped wood already passes the gate, so new evidence is canopy outside it. A belt may still
    # grow through covered canopy: a strip of mapped wood often separates a lane from the belt.
    cover_mask = wood if covered is None else covered
    through = {(x0 + int(i), z0 + int(j)) for i, j in np.argwhere(canopy)}
    ev = {(x0 + int(i), z0 + int(j)) for i, j in np.argwhere(canopy & ~cover_mask)}
    allow = {(x0 + int(i), z0 + int(j)) for i, j in np.argwhere(allowed)}
    cor = corridor(hedge, lanes, bounds)
    grown = {q: d for q, d in belt(cor & through, through, allow).items() if q in ev}
    return {'corridor': cor, 'evidence': ev, 'belt': grown, 'dark': dark_info,
            'height_source': height_source,
            'counts': {'corridor': len(cor), 'evidence': len(ev), 'seeds': sum(1 for d in grown.values() if d == 0),
                       'belt': len(grown)}}


def propose(cell: tuple[int, int]) -> dict:
    """Load one cell's evidence and propose tree-line crowns for the designer to review.

    @param cell: ``(tx, tz)``
    @returns: dict with ``crowns`` ``[{x, z, r, kind, evidence}]``, ``evidence`` (from ``line_evidence``),
        per-crown last/first-return gate support and calibration info
    """
    from aerial_coverage import cell_image
    from cellmath import CELL
    from coverage import area_classes, scan_polygon
    from ground_sources import OSMGround, cell_box
    from osm_footprints import OSMIndex, osm_tags

    tx, tz = cell
    x0, z0 = tx * CELL, tz * CELL
    bounds = (x0, z0, x0 + CELL - 1, z0 + CELL - 1)
    g = OSMGround(cell)
    idx = OSMIndex()
    classes = area_classes(idx, cell_box(cell, 0))
    wood = np.zeros((CELL, CELL), bool)        # darkness reference: wood and forest
    covered = np.zeros((CELL, CELL), bool)     # already passes the gate: wood, scrub, forest
    open_grass = np.zeros((CELL, CELL), bool)  # brightness reference
    # Gated classes, plus columns no land-use polygon claims (verges beside lanes and hedges,
    # where the gate does not apply but nothing detects the trees either).
    allowed = np.ones((CELL, CELL), bool)
    for (x, z), (cls, _ref, _kind) in classes.items():
        if x0 <= x < x0 + CELL and z0 <= z < z0 + CELL and cls not in GATED_CLASSES:
            allowed[x - x0, z - z0] = False
    for rings, props in idx.areas:
        t = osm_tags(props)
        cols = [(x - x0, z - z0) for x, z in scan_polygon(rings, bounds)]
        if t.get('natural') == 'wood' or t.get('landuse') == 'forest':
            targets = (wood, covered)
        elif t.get('natural') == 'scrub':
            targets = (covered,)
        elif t.get('landuse') in ('meadow', 'grass', 'farmland') or t.get('natural') == 'grassland':
            targets = (open_grass,)
        else:
            continue
        for i, j in cols:
            for target in targets:
                target[i, j] = True
    footprints = {q for q in idx.cells() if x0 <= q[0] < x0 + CELL and z0 <= q[1] < z0 + CELL}
    carriageway = corridor((), list(g.road_lines), bounds, reach=0, highways=None)
    for x, z in set(g.road) | set(g.water) | set(g.water_line) | footprints | carriageway:
        if x0 <= x < x0 + CELL and z0 <= z < z0 + CELL:
            allowed[x - x0, z - z0] = False
    rgb = np.asarray(cell_image(cell).convert('RGB').resize((CELL, CELL)), dtype=float)
    lum = rgb.mean(axis=2).T            # image rows are z, columns x -> [x, z]
    first, source = standing_height(cell)
    last = None
    if source == 'first-return':
        import trees_layer
        last = trees_layer.cell_ndsm(cell)
    else:
        last = first
        first = None
    height = first if first is not None else last
    lanes = list(g.road_lines) + list(g.path_lines)
    ev = line_evidence(bounds, g.hedge, lanes, lum, wood, open_grass & ~covered, allowed, height, source,
                       covered=covered)
    score = {}
    hmap = {}
    thr = ev['dark'].get('threshold')
    for q, steps in ev['belt'].items():
        i, j = q[0] - x0, q[1] - z0
        h = float(height[i, j]) if height is not None and np.isfinite(height[i, j]) else None
        dark = (thr - lum[i, j]) if thr is not None else 0.0
        score[q] = (h or 0.0) * 10.0 + dark - 0.01 * steps
        if h is not None and h >= MIN_STANDING_M:
            hmap[q] = h
    crowns = place_crowns(ev['belt'], score, hmap)
    hedge_cells = set(g.hedge)
    rows = []
    last_ok = gated_crowns(crowns, x0, z0, last)
    first_ok = gated_crowns(crowns, x0, z0, first) if first is not None else {}
    for x, z, r in crowns:
        steps = ev['belt'][(x, z)]
        near_hedge = any((x + dx, z + dz) in hedge_cells for dx in range(-CORRIDOR_REACH, CORRIDOR_REACH + 1)
                         for dz in range(-CORRIDOR_REACH, CORRIDOR_REACH + 1))
        kind = 'hedge line' if near_hedge and steps == 0 else ('lane line' if steps == 0 else 'belt')
        rows.append({'x': x, 'z': z, 'r': r, 'kind': kind, 'steps_from_corridor': steps,
                     'last_return_gate': last_ok.get((x, z)), 'first_return_gate': first_ok.get((x, z)),
                     'evidence': f'trial323 {kind}: aerial dark canopy / standing height ({source}) '
                                 f'{steps} blocks from mapped hedge or lane'})
    return {'cell': f'{tx},{tz}', 'height_source': source, 'dark': ev['dark'], 'counts': ev['counts'],
            'crowns': rows, '_evidence': ev, '_lum': lum, '_bounds': bounds}
