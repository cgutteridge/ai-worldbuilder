#!/usr/bin/env python3
"""Ground layer for one 128² cell — HANDOFF-LAYERED-PASSES §1 (pass 2) and ADR 010.

Written from first principles. It knows nothing about any one place: no zones, no campus
polygon, no hand-placed rectangles, no earlier ground pass. Per column it asks three questions.

  WHAT is on the ground?  vector-map tags first (roads, paths, car parks, pedestrian areas, grass,
                          wood), then the z19 aerial for anything the map does not describe
                          (ground_sources.py). Shade and canopy hide the ground, so those
                          columns take the lawn/paving around them.
  HOW HIGH?               composite lidar ``base`` Y (rules/evidence-order.md, #167).
                          The live surface is never the height source — only a fill-from
                          and a fallback when lidar base is missing. Neighbour spike/pit
                          levelling applies only to that lidar-missing remainder (#155).
  WHAT BLOCK?             One palette, from CRAFTSMANSHIP §3a: tarmac gray_concrete (centre dashes
                          white_concrete, public-road edge smooth_stone), light paving
                          light_gray_concrete, brick paving polished_granite, grass grass_block.
                          Mapped wood and forest may replace that grass with the class woodland
                          floor (podzol, coarse dirt, moss) at the same lidar height, keeping grass
                          within the configured reach of the polygon edge (#348).

Then, per ADR 010: the top block becomes the surface block, dirt fills beneath it down to solid
ground (never just down to a live top that itself floats over air; a plan whose surface would
still float is refused, task 296: scripts/ground_float_audit.py lists them across a world), and
everything above the surface is cleared except blocks a neighbour owns or a tree/decor mask holds.

Columns this layer never touches: building footprint polygons whose feature is unsettled (a
settled building's outline columns that no build owns are surfaced like any other; task 295),
columns where a structure build owns a solid block at or above the ground's surface (a feature's air-only
margin is surfaced, below that air, task 286; a column where a feature owns only blocks buried below
the lidar surface, such as a door stoop, is the ground's above the feature's highest owned block, and
the fill never overwrites them, task 297),
water and its bank, and anything outside the cell. A mapped bridge, tunnel or layer leaves its columns
free: the ground under a bridge is the ground's at lidar terrain height (the deck is a structure's,
never ground), and the surface above a tunnel is ground as normal (task 298). Where the column has a
crust of solid blocks over unowned air (a cavity left when a stone ring was undone), the air below the
crust is filled down to the baseline floor, the crust kept (task 298). Ground-level mapped outdoor steps (highway=steps) are surfaced at lidar height as paving,
with a stair block wherever the lidar rises along the way (task 294). Trees and decor are
separate later passes that write masks; a ground plan that lands on a masked position is a
conflict and does not apply (undo that mask first: scripts/element_undo.py).

  python3 scripts/ground_layer.py --cell 48,-44                 # dry run + preview + kinds image
  SITE_OWNER=<owner> python3 scripts/ground_layer.py --cell 48,-44 --apply
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

from block_store import block_id

from ground_sources import (OSMGround, PRI, RegRGB, aerial_class, aerial_rgb,  # noqa: E402
                            line_cells, hsv, majority, MARGIN)
import cellmath  # noqa: E402
from bay_lines import mark_car_park_bays  # noqa: E402
from cell_marker import is_marker_wool  # noqa: E402
from place_config import plan_name  # noqa: E402
from site_kit import Plan, World, full  # noqa: E402
from surface_probe import is_water, probe_floor, short_id, surface_y  # noqa: E402
import layer_common as lc
import ground_dressing as gd

RAISE_FILL = 'dirt'

# Palette (CRAFTSMANSHIP §3a). One block per kind; markings and kerbs are tarmac extras.
MATERIAL = {
    'grass': 'grass_block', 'tarmac': 'gray_concrete', 'paving': 'light_gray_concrete',
    'brick': 'polished_granite', 'gravel': 'gravel', 'dirt': 'coarse_dirt',
    'mark': 'white_concrete', 'kerb': 'smooth_stone',
    'forecourt': 'andesite',                          # private forecourt, apart from public pavement (#254)
}
STITCH_DEPTH = 2        # columns from a seam that copy the finished neighbour's surface
HARD = ('tarmac', 'paving', 'brick', 'gravel', 'dirt', 'mark', 'kerb', 'forecourt')
FILL_REACH = 12         # how far shade/canopy columns look for ground around them
STRUCTURE_TOPS = ('stairs', 'slab', '_wall', '_fence', 'bars', 'planks', '_carpet')
STEP_STAIR = 'polished_andesite_stairs'   # outdoor step riser beside light_gray_concrete paving (#294)
# facing of a stair that climbs toward +axis / -axis (Minecraft: east +x, south +z)
STEP_FACING = {('x', 1): 'east', ('x', -1): 'west', ('z', 1): 'south', ('z', -1): 'north'}


# --- 1. which columns are ours -----------------------------------------------------------


def _deck(vmap) -> set:
    """Columns under a mapped bridge (or raised way); a vector map without ``deck`` says it by ``bridge``."""
    return set(vmap.deck if hasattr(vmap, 'deck') else getattr(vmap, 'bridge', ()))


def free_columns(cell: tuple[int, int], world: World, vmap: OSMGround, fp: set, owned_cols: set, details=None,
                 floors=None, heights=None):
    """Columns this layer may surface, with their live surface (y, block).

    An owned structure column stays the structure's unless ``floors`` (:func:`layer_common.owned_floors`) puts
    every owned block below the column's surface: lidar ``heights``, else the live surface (task 297).

    A mapped bridge or tunnel does not take its columns out: the ground under a bridge is the
    ground's, at lidar terrain height, and the surface above a tunnel is ground as normal (#298). The
    deck itself is a structure's; it is never ground, and is reported as unbuilt when no feature
    holds it. A mapped stream with no water built still keeps its bank, except under a bridge.

    Ground-level mapped steps (``vmap.steps``) are free unless a footprint, structure build or water
    claims them; a stairs top there is the ground layer's own step, not a structure (#294). Any steps
    column left unsurfaced has a reason that says so.

    Returns (live: {(x,z): (y, short block)}, skipped: Counter of reasons).
    """
    x0, z0, x1, z1 = cellmath.bounds(cell)
    live, skipped = {}, Counter()
    water = set(vmap.water)
    probed = {}
    lc.warm_surface_probe(world, x0 - 1, z0 - 1, x1 + 1, z1 + 1)
    for x in range(x0 - 1, x1 + 2):
        for z in range(z0 - 1, z1 + 2):
            probed[(x, z)] = surface_y(world, x, z)
            if probed[(x, z)] and (is_water(f'minecraft:{probed[(x, z)][1]}') or
                                   'waterlogged=true' in (world.block(x, probed[(x, z)][0], z) or '')):
                water.add((x, z))
    bank = lc.column_ring(water)
    # A mapped stream line with no water built keeps its bank, but not under a mapped bridge (#298).
    stream_bank = lc.column_ring(vmap.water_line)
    deck = _deck(vmap)
    steps = getattr(vmap, 'steps', {})
    for x in range(x0, x1 + 1):
        for z in range(z0, z1 + 1):
            q = (x, z)
            top = probed[q]
            reason = None
            if q in fp:
                reason = 'building footprint'
            elif q in owned_cols and not buried_under(q, top, floors, heights):
                reason = 'structure build'
            elif q in bank or (q in stream_bank and q not in deck):
                reason = 'water / bank'
            elif top is None:
                reason = 'empty column'
            elif any(s in top[1] for s in STRUCTURE_TOPS) and not (q in steps and 'stairs' in top[1]):
                reason = f'structure top {top[1]}'
            else:
                live[q] = top
            if reason and q in steps:
                reason = f'steps: {reason}'
            if reason:
                skipped[reason] += 1
                if details is not None:
                    details.append({'x': x, 'z': z, 'reason': reason})
    return live, skipped


def buried_under(q, top, floors, heights) -> bool:
    """True when every block a feature owns in column ``q`` lies below the ground's surface (task 297).

    @param top: live surface ``(y, block)`` or None
    @param floors: ``{(x, z): highest owned y}`` or None
    @param heights: ``{(x, z): lidar y}`` or None
    """
    if not floors or q not in floors:
        return False
    surface = (heights or {}).get(q) or (top[0] if top else 0)
    return surface > floors[q]


PATH_KINDS = ('paving', 'brick', 'gravel', 'dirt', 'tarmac')
BRIDGE_REACH = 30       # longest shaded stretch a path is carried across, in blocks
BRIDGE_ANGLE = 0.82     # cos of the largest bend allowed between a path's two ends (~35°)


def _path_ends(shade: set, kind: dict) -> list:
    """Where a seen path meets a shaded patch: (kind, entry point, unit direction into the shade, width).

    The direction is the principal axis of the path's visible cells near the entry, signed to point
    at the shade; the width is their spread across that axis.
    """
    ends = []
    border = {q for q in kind if kind[q] in PATH_KINDS and
              any((q[0] + a, q[1] + b) in shade for a in (-1, 0, 1) for b in (-1, 0, 1))}
    for grp in cellmath.components(border, cellmath.N8):
        k = Counter(kind[q] for q in grp).most_common(1)[0][0]
        cx = sum(q[0] for q in grp) / len(grp)
        cz = sum(q[1] for q in grp) / len(grp)
        near = [q for q in kind if kind[q] in PATH_KINDS and abs(q[0] - cx) <= 5 and abs(q[1] - cz) <= 5]
        if not near:  # curved/ring-shaped border group: centroid sits off the path
            near = list(grp)
        mx = sum(q[0] for q in near) / len(near)
        mz = sum(q[1] for q in near) / len(near)
        sxx = sum((q[0] - mx) ** 2 for q in near)
        szz = sum((q[1] - mz) ** 2 for q in near)
        sxz = sum((q[0] - mx) * (q[1] - mz) for q in near)
        ang = 0.5 * math.atan2(2 * sxz, sxx - szz)
        d = (math.cos(ang), math.sin(ang))
        sh = [q for q in shade if abs(q[0] - cx) <= 4 and abs(q[1] - cz) <= 4]
        if not sh:
            continue
        sx = sum(q[0] for q in sh) / len(sh) - cx
        sz = sum(q[1] for q in sh) / len(sh) - cz
        if d[0] * sx + d[1] * sz < 0:
            d = (-d[0], -d[1])
        perp = [(q[0] - cx) * -d[1] + (q[1] - cz) * d[0] for q in near]
        ends.append((k, (cx, cz), d, max(1.0, min(6.0, max(perp) - min(perp) + 1))))
    return ends


def bridge_paths(kind: dict, shade: set) -> dict:
    """Carry paths across shade: pair two path ends that face each other and fill the gap.

    Two ends pair when they are the same kind (or both hard), within reach, and the line between
    them stays within ~35° of both ends' direction, so a wide lawn between two unrelated
    paths is not bridged. Returns {column: kind} for the shaded columns the paths cross.
    """
    ends = _path_ends(shade, kind)
    out: dict = {}
    used: set = set()
    pairs = []
    for i, (ka, pa, da, wa) in enumerate(ends):
        for j in range(i + 1, len(ends)):
            kb, pb, db, wb = ends[j]
            v = (pb[0] - pa[0], pb[1] - pa[1])
            dist = math.hypot(*v)
            if not 2 <= dist <= BRIDGE_REACH:
                continue
            u = (v[0] / dist, v[1] / dist)
            ca = da[0] * u[0] + da[1] * u[1]
            cb = -(db[0] * u[0] + db[1] * u[1])
            if ca >= BRIDGE_ANGLE and cb >= BRIDGE_ANGLE:
                pairs.append((dist * (2 - ca - cb), i, j))
    for _score, i, j in sorted(pairs):
        if i in used or j in used:
            continue
        used |= {i, j}
        ka, pa, _da, wa = ends[i]
        kb, pb, _db, wb = ends[j]
        half = (wa + wb) / 4
        for q, (dist, along) in line_cells([pa, pb], half).items():
            if q in shade:
                out[q] = ka if along <= math.dist(pa, pb) / 2 else kb
    return out


MIN_PATCH = {'grass': 12, 'paving': 8, 'brick': 8, 'gravel': 8, 'dirt': 8, 'tarmac': 12}


def denoise(kind: dict, locked: set, how: Counter) -> dict:
    """Noise reduction: a small isolated patch of one kind takes the kind that surrounds it.

    Patches are 4-connected runs of one kind. One smaller than MIN_PATCH, that is not mapped tarmac
    (roads and car parks are real however small), is re-labelled to the most common kind on its
    border. Thin features survive because they are long: a 2-wide path is 8 columns after 4 blocks.
    Runs three times so a patch exposed by the first pass can still go.
    """
    out = dict(kind)
    for _ in range(3):
        changed = 0
        for k in MIN_PATCH:
            cols = {q for q, v in out.items() if v == k}
            for comp in cellmath.components(cols, traversal='set-pop-dfs'):
                if len(comp) >= MIN_PATCH[k] or comp & locked:
                    continue
                border = Counter(out[(q[0] + a, q[1] + b)] for q in comp for a in (-1, 0, 1) for b in (-1, 0, 1)
                                 if (q[0] + a, q[1] + b) in out and (q[0] + a, q[1] + b) not in comp)
                if border:
                    new = border.most_common(1)[0][0]
                    for q in comp:
                        out[q] = new
                    changed += len(comp)
        how['noise removed'] += changed
        if not changed:
            break
    return out


# --- 2. what is on the ground ------------------------------------------------------------
def nearest_path_kind(q: tuple[int, int], reach: int, path_kind: dict) -> str | None:
    """Surface kind of the mapped path column nearest to ``q`` within ``reach`` blocks (#325), else None.

    @param q: ``(x, z)`` column
    @param reach: whole blocks, Euclidean
    @param path_kind: ``{column: kind}`` of mapped path columns
    @returns: the kind; ties go to the lowest column so reruns agree
    """
    best = min(((i * i + j * j, (q[0] + i, q[1] + j)) for i in range(-reach, reach + 1) for j in range(-reach, reach + 1)
                if i * i + j * j <= reach * reach and (q[0] + i, q[1] + j) in path_kind), default=None)
    return path_kind[best[1]] if best else None


def classify(cell: tuple[int, int], live: dict, vmap: OSMGround, rgb: dict,
             fp: set | None = None, private_frontage: set | frozenset = frozenset(),
             frontage_route: set | frozenset = frozenset(),
             pavement: set | frozenset = frozenset(),
             guessed_out: set | None = None,
             unmapped_paving: dict | None = None,
             path_widen: dict | None = None) -> tuple[dict, Counter]:
    """Surface kind per free column: vector map first, aerial where the map is silent, fill under shade.

    ``private_frontage`` (shared residential frontage from ``garden_sources``, #240) are columns of an
    identified house's own front garden: guessed lawn there stays lawn rather than becoming the
    shopfront-forecourt paving, so private frontage reads apart from public pavement.
    ``frontage_route`` is the onward path from a house's entrance to the street: *guessed* ground on it
    becomes paving (it joins the outer end of the feature's own steps); seen lawn, gravel or paving is
    kept, since observed surface differences are retained.
    ``pavement`` (#254) is the public strip outside each front boundary (width per land-use class):
    guessed lawn there is pavement. ``private_frontage`` also holds the front extent of classes that
    keep their lawn (park, rural, natural), so guessed lawn there is never paved.
    ``guessed_out``, when supplied, receives only guessed-lawn columns still classified as grass
    after all paving conversions and denoising. The return value is unchanged.
    ``unmapped_paving`` (#316, ``land_use.unmapped_paving`` per column) is ``{column: kind}`` for columns
    of a class that sets ``surface.unmapped_paving`` (park ``dirt``, rural and natural ``grass``). Aerial
    paving there that is not on a mapped way (``vmap.pri`` below 40) is shade, undergrowth or worn grass,
    so it takes that kind, and grass inside a mapped wood or scrub polygon. Columns outside the map
    (residential, unlisted classes) keep the aerial paving.
    A mapped surface car park (``vmap.soft``, #338) stays tarmac against the blurry mosaic: its lawn-coloured,
    orange, shaded and tinted-grey columns are sun-bleached tarmac, parked cars and tree shade, not open ground.
    Only a sharp registered aerial (``RegRGB``) that reads lawn there makes grass, as in a mapped plaza; a mapped
    aisle (``PRI['service']``) through it always stays tarmac. Bay lines (#336) rely on the kept tarmac.
    ``path_widen`` (#325, ``land_use.path_widen`` per column) is ``{column: reach}`` for columns of a class
    that sets ``surface.path_widen`` (park 3). Aerial paving within ``reach`` blocks of a mapped path
    (not on it) is that path drawn wider than the map says, so it takes the nearest path's own surface
    kind, before ``unmapped_paving`` is applied. Absent or empty changes nothing."""
    free = set(live)
    path_kind = {q: vmap.kind[q] for q in vmap.feat if vmap.pri.get(q) == PRI['path'] and q in vmap.kind} \
        if path_widen else {}
    x0, z0, x1, z1 = cellmath.bounds(cell, MARGIN)
    aer = {q: aerial_class(c) for q, c in rgb.items() if x0 <= q[0] <= x1 and z0 <= q[1] <= z1}
    raw = aer
    aer = majority(aer, set(aer))
    kind, how = {}, Counter()
    soft_grass: set = set()                               # lawn guessed, not seen (orange far from paths, shade fill)
    for q in free:
        a = aer.get(q)
        ok = vmap.kind.get(q)
        if ok and vmap.pri[q] == PRI['parking'] and q in vmap.soft and a == 'lawn' and isinstance(rgb.get(q), RegRGB):
            kind[q] = 'grass'                             # a sharp registered aerial shows open ground inside the mapped car park (#338)
            how['aerial lawn inside mapped car park'] += 1
        elif ok and vmap.pri[q] == PRI['pedestrian'] and a == 'lawn' and isinstance(rgb.get(q), RegRGB):
            kind[q] = 'grass'                             # lawn strip/planter inside a mapped plaza, seen in a sharp registered aerial
            how['registered aerial lawn inside mapped plaza'] += 1
        elif ok and vmap.pri[q] >= 40:                     # a mapped road, path, car park or plaza
            kind[q] = ok
            how['vector map surface'] += 1
        elif a == 'lawn':
            kind[q] = 'grass'
            how['aerial lawn'] += 1
        elif a == 'brick':
            if any(vmap.pri.get((q[0] + i, q[1] + j), 0) >= 40 for i in range(-4, 5) for j in range(-4, 5)):
                kind[q] = 'brick'
                how['aerial brick'] += 1
            else:                                         # orange far from any mapped path: shed roof / bed, not paving
                kind[q] = 'grass'
                soft_grass.add(q)
                how['aerial brick away from paths -> lawn'] += 1
        elif a in ('light', 'dark') and path_widen and q in path_widen and (
                near := nearest_path_kind(q, path_widen[q], path_kind)):
            kind[q] = near
            how['aerial paving beside a mapped path -> the path surface'] += 1
        elif a in ('light', 'dark') and unmapped_paving and q in unmapped_paving:
            if vmap.pri.get(q) == PRI['wood']:
                kind[q] = 'grass'
                how['aerial paving in mapped wood -> grass'] += 1
            else:
                kind[q] = unmapped_paving[q]
                how[f'aerial paving off a mapped way -> {unmapped_paving[q]}'] += 1
        elif a in ('light', 'dark'):
            kind[q] = 'paving'
            how['aerial paving'] += 1
    # shade and canopy hide the ground. First carry the paths that go under it straight across,
    # with the colour of their visible ends; then what is left takes the nearest seen ground.
    shaded = {q for q in free if aer.get(q) == 'shade'}
    # Block paving vs plain paving: vote along the same mapped path (or plaza) over the columns where
    # pavement colour is actually visible. Canopy and lawn-coloured columns do not vote, so a path
    # that goes under a tree takes the colour of the path on either side of it.
    by_feat: dict = {}
    for q in free:
        if kind.get(q) == 'paving' and vmap.pri.get(q, 0) >= 40:
            by_feat.setdefault(vmap.feat.get(q), []).append(q)
    for f, cols in by_feat.items():
        votes = [(q, raw.get(q)) for q in (cols if f is not None else [])
                 if raw.get(q) in ('brick', 'light') and aer.get(q) in ('brick', 'light')]
        for q in cols:
            if f is None:
                near = sum(raw.get((q[0] + i, q[1] + j)) == 'brick' for i in (-1, 0, 1) for j in (-1, 0, 1))
                if near >= 3:
                    kind[q] = 'brick'
                continue
            near = [c for p, c in votes if abs(p[0] - q[0]) <= 8 and abs(p[1] - q[1]) <= 8]
            if near.count('brick') > near.count('light'):
                kind[q] = 'brick'
                if q in shaded or aer.get(q) not in ('brick', 'light'):
                    how['mapped path colour voted along its length'] += 1
    gap = free - set(kind)
    seen_kind = {q: k for q, k in kind.items() if q not in shaded}
    for q, k in bridge_paths(seen_kind, gap).items():
        kind[q] = k
        how['path carried across shade'] += 1
    seen = {q: k for q, k in kind.items()}
    for q in sorted(free - set(kind)):
        if vmap.kind.get(q) == 'grass':                    # mapped lawn, hidden by canopy
            kind[q] = 'grass'
            how['vector map grass (under shade)'] += 1
            continue
        best = None
        for r in range(1, FILL_REACH + 1):
            ring = [(q[0] + a, q[1] + b) for a in range(-r, r + 1) for b in range(-r, r + 1)
                    if max(abs(a), abs(b)) == r and (q[0] + a, q[1] + b) in seen]
            if ring:
                best = Counter(seen[p] for p in ring).most_common(1)[0][0]
                break
        near_path = any(vmap.pri.get((q[0] + a, q[1] + b), 0) >= 40 for a in range(-3, 4) for b in range(-3, 4))
        if best in ('paving', 'brick') and not near_path:
            best = 'grass'                                # unseen ground away from any mapped path: lawn/bed
        kind[q] = best or 'grass'
        if best in (None, 'grass'):
            soft_grass.add(q)
        how['filled under shade'] += 1
    # A guessed lawn strip squeezed between a public road and a building front is pavement (shopfront
    # forecourts, shadowed by the buildings), not garden. Only guessed lawn is converted, never seen lawn.
    if fp:
        road = {q for q in vmap.kind if vmap.kind[q] == 'tarmac' and vmap.pri.get(q, 0) >= 40}
        for q in soft_grass:
            if q in pavement and q not in private_frontage and kind.get(q) == 'grass':
                kind[q] = 'paving'
                how['guessed lawn on public pavement strip -> paving'] += 1
                continue
            if q in private_frontage:
                how['guessed lawn kept: private residential frontage'] += 1
                continue
            if kind.get(q) == 'grass' and any((q[0] + a, q[1] + b) in road for a in range(-8, 9) for b in range(-8, 9)) \
                    and any((q[0] + a, q[1] + b) in fp for a in range(-4, 5) for b in range(-4, 5)):
                kind[q] = 'paving'
                how['guessed lawn between road and building -> paving'] += 1
    route_paved: set = set()
    for q in frontage_route:
        if q in soft_grass and kind.get(q) == 'grass' and q not in (fp or ()):
            kind[q] = 'paving'
            route_paved.add(q)
            how['guessed ground on entrance route -> paving'] += 1
    barrier = vmap.hedge | vmap.wall | vmap.fence
    lawn_by_barrier = {q for q in kind if kind[q] == 'grass' and any(
        (q[0] + a, q[1] + b) in barrier for a in (-1, 0, 1) for b in (-1, 0, 1))}
    kind = denoise(kind, {q for q in kind if vmap.pri.get(q, 0) >= 40 and vmap.kind.get(q) == 'tarmac'}
                   | lawn_by_barrier | route_paved, how)   # a one-wide entrance path is not noise          # a hedge needs its strip of lawn: not noise
    # road furniture: dashed centre line, kerb along the public-road edge (dropped at crossings)
    out = dict(kind)
    for q in free:
        if q in vmap.road and kind.get(q) == 'tarmac' and q in vmap.marks:
            out[q] = 'mark'
    for q in free:
        if q in vmap.road and kind.get(q) == 'tarmac':
            nb = [kind.get((q[0] + a, q[1] + b)) for a, b in cellmath.N4]
            if any(k in ('grass', 'paving', 'brick') for k in nb) and not any(
                    (q[0] + a, q[1] + b) in vmap.kind and vmap.pri[(q[0] + a, q[1] + b)] in (60, 70)
                    for a, b in cellmath.N4):
                out[q] = 'kerb'
    for q in free & set(getattr(vmap, 'steps', ())):    # mapped outdoor steps are paving (#294)
        out[q] = 'paving'
        how['mapped outdoor steps -> paving'] += 1
    if guessed_out is not None:
        guessed_out.update(q for q in soft_grass if out.get(q) == 'grass')
    return out, how


def split_forecourt(kind: dict, forecourt: set | frozenset, fp: set | None = None) -> Counter:
    """Private forecourt reads apart from public pavement (#254): paving behind the front boundary becomes
    ``forecourt`` (another block). Only paving: seen lawn, gravel, brick and the tarmac stay as the aerial
    colour rules left them, and the surface class of the frontage profile is not evidence here."""
    how: Counter = Counter()
    for q in forecourt:
        if kind.get(q) == 'paving' and q not in (fp or ()):
            kind[q] = 'forecourt'
            how['private forecourt surface (paving behind the front boundary)'] += 1
    return how


# --- 3. how high -------------------------------------------------------------------------
PIT_DROP = 3            # min blocks below the surrounding rim before a pit is raised (#155)
PIT_MAX_RADIUS = 2      # max Chebyshev dist from a pit cell to outside the component (#160)
PIT_RIM_REACH = 4       # how far to look for rim columns around a candidate


def step_stairs(steps: dict, ys: dict) -> dict:
    """Stair block per steps column where the surface rises along the way (#294).

    A steps column whose neighbour along the way's run axis is lower, while the neighbour on the other
    side is not, is a riser: its top becomes a stair whose back faces up the slope. A walker on the
    lower neighbour steps onto the stair's low half and on to the next step, as on a built staircase.

    @param steps: ``{(x, z): 'x' | 'z'}`` ground-level steps columns and their run axis
    @param ys: target surface Y per free column (lidar height)
    @returns: ``{(x, z): facing}`` for the riser columns
    """
    out = {}
    for q, axis in steps.items():
        y = ys.get(q)
        if y is None:
            continue
        sides = {}
        for d in (1, -1):
            n = (q[0] + d, q[1]) if axis == 'x' else (q[0], q[1] + d)
            sides[d] = ys.get(n) is not None and ys[n] < y
        if sides[1] != sides[-1]:
            up = -1 if sides[1] else 1                    # climb away from the lower side
            out[q] = STEP_FACING[(axis, up)]
    return out


def hollow_ys(block, x: int, z: int, base: int | None, owned_floor: int | None = None,
              floor: int | None = None) -> list:
    """Air positions under a column's crust, down to the baseline floor (task 298).

    :func:`layer_common.support_y` stops at the first solid run under the surface, so a crust of solid blocks
    over air (ghost stone left floating when a stone ring was undone) reads as ground. Every air
    block below that run, down to ``floor``, is a hidden cavity: nothing holds it, and a player
    can fall into it where the crust is missing. Solid blocks are never listed, so the crust and
    whatever lies under the cavity are kept.

    @param block: ``callable(x, y, z) -> state | None``
    @param base: y of the support's top block (:func:`layer_common.support_y`); None means there is none
    @param owned_floor: highest y a feature owns in the column (task 297); the feature answers for
      it and everything below
    @param floor: highest y not read; default :func:`surface_probe.probe_floor`
    @returns: ascending ``[y, ...]`` of air positions (``floor < y < base``) the ground may fill
    """
    if base is None:
        return []
    if floor is None:
        floor = probe_floor()
    low = floor if owned_floor is None else max(floor, owned_floor)
    return [y for y in range(low + 1, base) if lc.is_air(block(x, y, z))]


def floating_tops(cells: dict, block, tops: dict, floors=None) -> list:
    """Planned surfaces that would stand over air once the plan lands (#296).

    A column floats when nothing below holds it up, or when air is left between its surface and
    the solid ground below (:func:`layer_common.support_y`, found in the world as it is) once the plan is read
    over the world. Ground found in the plan itself does not count: planned dirt over an air
    pocket is not ground.

    @param cells: planned ``{(x, y, z): state}``
    @param block: ``callable(x, y, z) -> state | None`` for the world as it is
    @param tops: ``{(x, z): surface y}``
    @param floors: ``{(x, z): highest feature-owned y}`` for buried-feature columns (task 297): the
      ground answers for the column above that block only, the feature for what lies below it
    @returns: ``[{'x', 'z', 'surface_y', 'support_y', 'air'}]`` sorted by column
    """
    def state(x, y, z):
        return cells.get((x, y, z)) or block(x, y, z)
    out = []
    for (x, z), gy in sorted(tops.items()):
        base = lc.support_y(block, x, gy, z)
        if floors and (x, z) in floors:
            base = floors[(x, z)] if base is None else max(base, floors[(x, z)])
        low = probe_floor() if base is None else base
        air = sum(1 for y in range(low + 1, gy) if lc.is_air(state(x, y, z)))
        if base is None or air:
            out.append({'x': x, 'z': z, 'surface_y': gy, 'support_y': base, 'air': air})
    return out


def ground_heights(live: dict, lidar_ys: dict) -> dict:
    """Target surface Y per free column: lidar bare terrain is HOW HIGH (#172 / #167).

    The live game surface may sit below a correct lidar ground (wrong ghost
    baseline, unfinished fill). Those columns rise to lidar. A real depression
    that lidar also reports stays low. Neighbour-rim pit / spike levelling is
    not applied on top of lidar — a wide wrong-baseline void still rises.

    When lidar height is missing (absent or 0), the live surface Y is kept.
    Values come from :func:`layer_common.lidar_base_map` (``terrain``, else ``base``).

    @param live: ``{(x, z): (y, block)}`` free columns
    @param lidar_ys: ``{(x, z): y}`` from :func:`layer_common.lidar_base_map`
    @returns: height map; ``_from_lidar`` counts columns that used lidar height
    """
    out = {}
    from_lidar = 0
    for q, (y, _b) in live.items():
        base = lidar_ys.get(q) or 0
        if base > 0:
            out[q] = int(base)
            from_lidar += 1
        else:
            out[q] = y
    out['_from_lidar'] = from_lidar
    return out


def _pit_chebyshev_radius(comp: set) -> int:
    """Largest Chebyshev distance from any cell in ``comp`` to a cell outside it.

    A 3-wide strip scores 1; a solid 5-wide band scores 2; a 10×10 pond scores 5.
    Used instead of axis-aligned bbox width so a long diagonal wall-side void
    still fills while ponds stay low (#160 / S-019).

    @param comp: connected set of ``(x, z)`` pit candidates
    @returns: max radius (0 for empty)
    """
    if not comp:
        return 0
    best = 0
    for x, z in comp:
        r = 0
        while True:
            r += 1
            hit_out = False
            for a in range(-r, r + 1):
                for b in range(-r, r + 1):
                    if max(abs(a), abs(b)) != r:
                        continue
                    if (x + a, z + b) not in comp:
                        hit_out = True
                        break
                if hit_out:
                    break
            if hit_out:
                best = max(best, r)
                break
            if r > 64:  # pathological solid; treat as too wide
                return r
    return best


def level_spikes(live: dict, kind: dict, fp: set) -> dict:
    """Surface Y per column. Only an isolated ±1 on a hard surface is levelled to its neighbours."""
    ys = {q: v[0] for q, v in live.items()}
    out = dict(ys)
    n = 0
    for q, y in ys.items():
        if kind[q] not in HARD or any((q[0] + a, q[1] + b) in fp for a in range(-2, 3) for b in range(-2, 3)):
            continue
        nb = [ys[(q[0] + a, q[1] + b)] for a in (-1, 0, 1) for b in (-1, 0, 1)
              if (a or b) and (q[0] + a, q[1] + b) in ys and kind.get((q[0] + a, q[1] + b)) in HARD]
        if len(nb) >= 6:
            med = sorted(nb)[len(nb) // 2]
            if abs(med - y) == 1 and sum(1 for v in nb if v == med) >= len(nb) - 1:
                out[q] = med
                n += 1
    out['_levelled'] = n
    return out


def level_pits(live: dict, kind: dict, fp: set, ys: dict | None = None) -> dict:
    """Raise narrow pits among free columns to the surrounding rim (#155 / #160).

    Fallback only when lidar ``base`` is missing for a column (#167): with lidar
    present, :func:`ground_heights` already sets HOW HIGH. A pit is a connected
    set of columns whose live surface sits at least ``PIT_DROP`` below nearby rim
    columns, and whose Chebyshev radius (distance from any pit cell to outside
    the component) is at most ``PIT_MAX_RADIUS``. Water, bank, bridge and
    structure tops are already absent from ``live``. Footprint columns are never
    raised and never used as rim (wall-side voids beside a building still fill to
    the outside grade — #160 / S-019). Wide cuttings and ponds stay at their
    live height.

    @param live: ``{(x, z): (y, block)}`` free columns
    @param kind: surface kind per column (kerb / mark columns are never raised)
    @param fp: building footprint columns (excluded from candidates and rim)
    @param ys: optional heights from :func:`level_spikes` (preserves ``_levelled``)
    @returns: height map with pit columns raised; ``_pits_filled`` counts raised columns
    """
    heights = {q: v[0] for q, v in live.items()}
    out = dict(ys) if ys is not None else dict(heights)
    for q, y in heights.items():
        out.setdefault(q, y)

    candidates: set = set()
    for q, y in heights.items():
        if q in fp or kind.get(q) in ('kerb', 'mark'):
            continue
        rim = []
        for a in range(-PIT_RIM_REACH, PIT_RIM_REACH + 1):
            for b in range(-PIT_RIM_REACH, PIT_RIM_REACH + 1):
                if a == 0 and b == 0:
                    continue
                n = (q[0] + a, q[1] + b)
                if n not in heights or n in fp:
                    continue
                if heights[n] >= y + PIT_DROP:
                    rim.append(heights[n])
        if len(rim) >= 4:
            candidates.add(q)

    filled = 0
    for comp in cellmath.components(candidates, cellmath.N8):
        if _pit_chebyshev_radius(comp) > PIT_MAX_RADIUS:
            continue
        rim_ys = []
        for q in comp:
            for a in range(-PIT_RIM_REACH, PIT_RIM_REACH + 1):
                for b in range(-PIT_RIM_REACH, PIT_RIM_REACH + 1):
                    n = (q[0] + a, q[1] + b)
                    if n in comp or n not in heights or n in fp:
                        continue
                    rim_ys.append(heights[n])
        if not rim_ys:
            continue
        target = sorted(rim_ys)[len(rim_ys) // 2]
        for q in comp:
            if heights[q] <= target - PIT_DROP:
                out[q] = target
                filled += 1
    out['_pits_filled'] = filled
    return out


# --- 4. the plan -------------------------------------------------------------------------
def ground_done(cell: tuple[int, int]) -> bool:
    """True if the cell journal shows the ground ``generated`` (ticket #31)."""
    from cell_registry import view_registry
    return view_registry().cell(tuple(cell)).ground == 'generated'


def stitch_seams(cell: tuple[int, int], kind: dict, world: World) -> int:
    """Make the outer columns continue the surface of a neighbour whose ground is already done.

    Each cell classifies its own edge, so a road or path can end at the seam. Where the next
    cell across a side has its ground done, the STITCH_DEPTH outer columns copy that neighbour's
    surface kind at the same lateral position (mark / kerb / dirt and water are left alone).
    Returns the number of columns changed.
    """
    x0, z0, x1, z1 = cellmath.bounds(cell)
    sides = (((-1, 0), x0, 'x'), ((1, 0), x1, 'x'), ((0, -1), z0, 'z'), ((0, 1), z1, 'z'))
    changed = 0
    for (dx, dz), edge, axis in sides:
        if not ground_done((cell[0] + dx, cell[1] + dz)):
            continue
        for q in [c for c in kind if (c[0] if axis == 'x' else c[1]) in
                  [edge - dx * d if axis == 'x' else edge - dz * d for d in range(STITCH_DEPTH)]]:
            if kind[q] in ('mark', 'kerb', 'dirt'):
                continue
            ref = (edge + dx, q[1]) if axis == 'x' else (q[0], edge + dz)
            top = surface_y(world, *ref)
            want = lc.BLOCK_KIND.get(top[1]) if top else None
            if want and want != kind[q]:
                kind[q] = want
                changed += 1
    return changed


def settled_footprint(cell: tuple[int, int]) -> set:
    """Building outline columns near the cell whose feature is built or dismissed (task 295).

    Between the feature and the ground every column is accounted for: an outline column no build
    owns is the ground's once its building's feature is settled. Reads the map and registry.

    @param cell: ``(tx, tz)``
    @returns: set of ``(x, z)`` within two columns of the cell
    """
    from cell_features import settled_footprint_columns
    return settled_footprint_columns(cellmath.bounds(cell, 2))


def unmapped_paving_columns(cell: tuple[int, int], idx, classes: dict | None = None) -> dict:
    """``{column: kind}`` for columns of the cell whose land-use class sets ``surface.unmapped_paving`` (#316).

    @param cell: ``(tx, tz)``
    @param idx: ``OSMIndex``
    @param classes: ``coverage.area_classes`` result when the caller already has it
    @returns: only columns whose class has a non-null setting; residential and other classes are absent
    """
    import land_use

    if classes is None:
        from coverage import area_classes
        classes = area_classes(idx, cellmath.bounds(cell, MARGIN))
    kinds = {c: land_use.unmapped_paving(c) for c in land_use.CLASSES}
    return {q: kinds[hit[0]] for q, hit in classes.items() if kinds.get(hit[0])}


def path_widen_columns(cell: tuple[int, int], idx, classes: dict | None = None) -> dict:
    """``{column: reach}`` for columns of the cell whose land-use class sets ``surface.path_widen`` (#325).

    @param cell: ``(tx, tz)``
    @param idx: ``OSMIndex``
    @param classes: ``coverage.area_classes`` result when the caller already has it
    @returns: only columns whose class has a non-null setting; residential and other classes are absent
    """
    import land_use

    if classes is None:
        from coverage import area_classes
        classes = area_classes(idx, cellmath.bounds(cell, MARGIN))
    reach = {c: land_use.path_widen(c) for c in land_use.CLASSES}
    return {q: reach[hit[0]] for q, hit in classes.items() if reach.get(hit[0])}


def plan_cell(cell: tuple[int, int], masked: dict, neighbour_owned: set, plan: Plan, world: World,
              evidence=None, *, owned_air: set = frozenset(), settled_fp: set = frozenset(),
              owned_states: dict | None = None):
    """Plan the ground for one cell. Returns (stats, conflicts, kinds, skipped).

    ``owned_air`` (owned positions whose state is air) frees air-only columns such as a
    feature's margin: their surface is capped below the lowest owned air (task 286).
    ``settled_fp`` (:func:`settled_footprint`) frees building outline columns whose feature is
    built or dismissed; ownership still decides each one (task 295).
    ``owned_states`` (``{pos: state}`` for ``neighbour_owned``) lets a column whose owned blocks all
    lie below the lidar surface be filled above them (task 297); without it any owned solid block
    keeps the whole column the structure's.
    """
    from osm_footprints import OSMIndex

    ev = evidence or {}
    vmap = ev.get('vmap') or OSMGround(cell)
    rgb = ev.get('rgb') or aerial_rgb(cell)
    x0, z0, x1, z1 = cellmath.bounds(cell, 2)
    idx = OSMIndex()
    fp = {q for q in idx.cells() if x0 <= q[0] <= x1 and z0 <= q[1] <= z1}
    owned_cols, ceilings = lc.owned_air_ceilings(neighbour_owned, owned_air)
    floors = lc.owned_floors(owned_states, owned_cols) if owned_states else {}
    floors = {q: y for q, y in floors.items() if lc.cell_contains(cell, *q)}
    floor_heights = lc.lidar_base_map(floors, ev.get('lidar_maps')) if floors else {}
    excluded = []
    live, skipped = free_columns(cell, world, vmap, fp - set(settled_fp), owned_cols, excluded,
                                 floors=floors, heights=floor_heights)
    buried = {q: floors[q] for q in live if q in owned_cols and q in floors}
    if 'private_frontage' in ev or 'frontage_route' in ev:
        frontages = ev.get('frontages') or []
    else:
        import garden_sources as gs
        frontages = gs.frontages_for_cell(cell)
    import garden_sources as gs
    private = set(ev['private_frontage']) if 'private_frontage' in ev else gs.private_frontage_columns(frontages)
    route = set(ev['frontage_route']) if 'frontage_route' in ev else gs.frontage_route_columns(frontages)
    forecourt = set(ev['forecourt']) if 'forecourt' in ev else gs.forecourt_columns(frontages)
    pavement = set(ev['pavement']) if 'pavement' in ev else gs.pavement_columns(frontages)
    guessed = set()
    classes = None
    if 'unmapped_paving' not in ev or 'path_widen' not in ev:
        from coverage import area_classes
        classes = area_classes(idx, cellmath.bounds(cell, MARGIN))
    unmapped = ev['unmapped_paving'] if 'unmapped_paving' in ev else unmapped_paving_columns(cell, idx, classes)
    widen = ev['path_widen'] if 'path_widen' in ev else path_widen_columns(cell, idx, classes)
    kind, how = classify(cell, live, vmap, rgb, fp, private, route, pavement, guessed_out=guessed,
                         unmapped_paving=unmapped, path_widen=widen)
    how.update(split_forecourt(kind, forecourt - route, fp))
    how['seam stitched to finished neighbour'] = stitch_seams(cell, kind, world)
    how['car park bay line (perpendicular to the mapped aisle)'] = mark_car_park_bays(kind, vmap)   # #336
    # #167: lidar base is HOW HIGH. Live surface only fills up to that Y (or stands
    # in when lidar is missing). Neighbour spike/pit logic must not override lidar.
    lidar_ys = lc.lidar_base_map(live, ev.get('lidar_maps'))
    ys = ground_heights(live, lidar_ys)
    from_lidar = ys.pop('_from_lidar', 0)
    missing = {q: live[q] for q in live if q not in lidar_ys}
    levelled = pits_filled = 0
    if missing:
        spike_ys = level_spikes(missing, kind, fp)
        levelled = spike_ys.pop('_levelled', 0)
        pit_ys = level_pits(missing, kind, fp, spike_ys)
        pits_filled = pit_ys.pop('_pits_filled', 0)
        for q, y in pit_ys.items():
            if q not in lidar_ys:
                ys[q] = y
    capped = 0
    for q in live:
        if q in ceilings and ys[q] >= ceilings[q]:
            ys[q] = ceilings[q] - 1
            capped += 1
        if q in buried and ys[q] <= buried[q]:          # live-surface fallback can sit lower than lidar did
            ys[q] = buried[q] + 1
    deck = _deck(vmap)
    tunnel = set(getattr(vmap, 'tunnel', ()))
    steps = {q: a for q, a in getattr(vmap, 'steps', {}).items() if q in live}
    stairs = step_stairs(steps, ys)
    stats = Counter({
        'columns': len(live),
        'from_lidar': from_lidar,
        'levelled': levelled,
        'pits_filled': pits_filled,
        'capped_under_feature_margin': capped,
        'vector_map_features': getattr(vmap, 'n_features', 0),
        'steps_surfaced': len(steps),
        'settled_footprint_surfaced': sum(1 for q in live if q in fp),
        'steps_stairs': len(stairs),
        'above_buried_feature': len(buried),
        'bridge_ground': sum(1 for q in live if q in deck),
        'tunnel_ground': sum(1 for q in live if q in tunnel and q not in deck),
    })
    stats.update({f'how: {k}': v for k, v in how.items()})
    stats['frontage_blocked'] = gs.blocked_connections(frontages)
    stats['excluded_columns'] = excluded
    retained = []
    stats['retained_positions'] = retained
    conflicts = []
    cavity_cols: set = set()
    for q in sorted(live):
        x, z = q
        old = live[q][0]
        gy = ys[q]
        k = kind[q]
        mat = MATERIAL[k]
        top = (x, gy, z)
        cover = world.block(x, gy + 1, z)
        if mat == 'grass_block' and lc.shades_grass(cover):   # #242: grass dies under an opaque block
            mat = 'dirt'
            stats['dirt_under_opaque'] += 1
        if q in stairs:
            mat = f'{STEP_STAIR}[facing={stairs[q]},half=bottom]'
        if top in masked:
            conflicts.append((top, mat, masked[top]))
        else:
            plan.set(x, gy, z, mat)
            stats['top'] += 1
        # Solid under the new top, down to the ground that holds the column up, not just down to the
        # live top: a live top can itself float over air (#296).
        base = lc.support_y(world.block, x, gy, z)
        low = old if base is None else base
        if q in buried:                                   # a buried feature block: fill from above it (#297)
            low = max(low, buried[q])
        for y in range(low + 1, gy):
            pos = (x, y, z)
            b = world.block(x, y, z)
            if pos in neighbour_owned or (y > old and not lc.is_air(b)):
                continue
            if pos in masked:
                conflicts.append((pos, RAISE_FILL, masked[pos]))
            else:
                plan.set(x, y, z, RAISE_FILL)          # under the live top: own a stale block too
                stats['raise'] += 1
                if y <= old:
                    stats['filled_under_floating_top'] += 1
        for y in hollow_ys(world.block, x, z, base, buried.get(q)):    # air under a crust of solid blocks (#298)
            pos = (x, y, z)
            if pos in neighbour_owned:
                continue
            if pos in masked:
                conflicts.append((pos, RAISE_FILL, masked[pos]))
            else:
                plan.set(x, y, z, RAISE_FILL)
                stats['cavity_filled'] += 1
                cavity_cols.add(q)
        for y in range(gy + 1, lc.SCAN_TOP + 1):             # above the surface: clear (ADR 010)
            pos = (x, y, z)
            b = world.block(x, y, z)
            if block_id(b, policy='read') in lc.AIRS:
                continue
            if is_marker_wool(b):          # hazard-tape ring stays
                retained.append({'position': list(pos), 'reason': 'registry marker'})
                continue
            if pos in neighbour_owned:
                stats['kept_neighbour'] += 1
                retained.append({'position': list(pos), 'reason': 'neighbour ownership'})
                continue
            if pos in masked:
                stats['kept_masked'] += 1
                retained.append({'position': list(pos), 'reason': 'tree/decor mask'})
                continue
            if block_id(b, policy='read') in lc.STEM_OR_FLOWER and y == gy + 1 and mat == 'grass_block':
                retained.append({'position': list(pos), 'reason': 'plant on lawn'})
                continue                                  # a flower or grass stem may stand on lawn
            plan.set(x, y, z, 'air')
            stats['clear'] += 1
    stats['cavity_columns'] = len(cavity_cols)
    plots = ev['plots'] if 'plots' in ev else gs.plots_for(cellmath.bounds(cell))
    owner, info, street, idx = plots
    segments = ev['segments'] if 'segments' in ev else gs.segments(owner, info, street, idx, cellmath.bounds(cell))
    # soft_grass survivors can include low-priority mapped surfaces; planting must exclude them.
    clear_cols = gs.reserved_route(frontages) | gs.reserved_openings(frontages)
    barriers = vmap.hedge | vmap.wall | vmap.fence
    stats['planting'] = gd.plan_planting(
        plan, world, guessed.difference(vmap.kind), ys, plots, segments,
        clear=clear_cols, masked=masked, owned=neighbour_owned, barriers=barriers)
    stats['shrubs'] = sum(row['shrubs'] for row in stats['planting'])
    stats['flowers'] = sum(row['flowers'] for row in stats['planting'])
    stats['cover'] = sum(row['cover'] for row in stats['planting'])
    # Rough grass / mottling (#319): own key (planting is null when frontage.inference is false).
    if 'ground_texture' in ev:
        texture_palettes = ev['ground_texture']
    elif not getattr(idx, 'areas', None):
        texture_palettes = {}
    else:
        if classes is None:
            from coverage import area_classes
            classes = area_classes(idx, cellmath.bounds(cell, MARGIN))
        texture_palettes = gd.rough_grass_columns(cell, idx, classes)
    if 'woodland_floor' in ev:
        floor_columns, floor_mask = ev['woodland_floor']
    elif not getattr(idx, 'areas', None):
        floor_columns, floor_mask = {}, set()
    else:
        if classes is None:
            from coverage import area_classes
            classes = area_classes(idx, cellmath.bounds(cell, MARGIN))
        floor_columns, floor_mask = gd.woodland_floor_columns(cell, idx, classes)
    # Opted-in scrub leaves the pasture palette; meadow, pasture and lawn columns are not in
    # floor_columns, so their #319 placement is unchanged.
    if floor_columns:
        texture_palettes = {q: name for q, name in texture_palettes.items() if q not in floor_columns}
    stats['ground_texture'] = gd.plan_ground_texture(
        plan, world, cell, ys, kind, rgb, texture_palettes,
        masked=masked, owned=neighbour_owned, clear=clear_cols | set(fp), barriers=barriers)
    stats['texture_tufts'] = stats['ground_texture']['tufts']
    stats['texture_tall_grass'] = stats['ground_texture']['tall_grass']
    stats['texture_bare'] = stats['ground_texture']['bare']
    stats['texture_flowers'] = stats['ground_texture']['flowers']
    stats['woodland_floor'] = gd.plan_woodland_floor(
        plan, world, cell, ys, kind, floor_columns, floor_mask,
        masked=masked, owned=neighbour_owned, clear=clear_cols | set(fp), barriers=barriers)
    stats['floating_columns'] = floating_tops(plan.cells, world.block, {q: ys[q] for q in live},  # #296 guard
                                              floors=buried)
    return stats, conflicts, kind, skipped


KIND_RGB = {'grass': (110, 180, 70), 'tarmac': (70, 70, 80), 'paving': (200, 200, 200),
            'brick': (190, 90, 60), 'gravel': (150, 140, 120), 'dirt': (120, 90, 60),
            'mark': (255, 255, 255), 'kerb': (255, 220, 60), 'forecourt': (160, 160, 150)}


def kinds_image(cell: tuple[int, int], kind: dict, out: Path, scale: int = 4) -> None:
    """Kinds map beside the aerial crop of the same cell, for the review."""
    import numpy as np
    from PIL import Image
    from aerial_coverage import cell_image

    x0, z0, x1, z1 = cellmath.bounds(cell)
    img = np.full((128, 128, 3), 35, np.uint8)
    for (x, z), k in kind.items():
        img[z - z0, x - x0] = KIND_RGB[k]
    left = Image.fromarray(img).resize((128 * scale, 128 * scale), Image.NEAREST)
    right = cell_image(cell).resize(left.size)
    sheet = Image.new('RGB', (left.width * 2 + 8, left.height), (0, 0, 0))
    sheet.paste(left, (0, 0))
    sheet.paste(right, (left.width + 8, 0))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)


def _fresh_world(strict: bool = False) -> World:
    """Flush the server's chunks to disk, then read them via :func:`fresh_planning_world` (#111).

    ``strict`` (live apply): a failed flush raises, because planning from stale region files writes
    a wrong plan.
    """
    from site_kit import fresh_planning_world
    return fresh_planning_world(strict=strict)


def run(cell: tuple[int, int], skip_preview: bool = False) -> int:
    """Dry-run one cell. Returns the number of conflicts (0 means it is safe to apply)."""
    name = plan_name(cell, 'ground')
    plan = Plan(f'{name}-dry', input_cells=[cell])
    world = _fresh_world()
    masked = lc.load_masked_positions()
    states = lc.neighbour_owned_states(cell, lc.ignored_builds(cell))
    owned, air = set(states), {p for p, b in states.items() if block_id(b, policy='read') in lc.AIRS}
    stats, conflicts, kind, skipped = plan_cell(cell, masked, owned, plan, world, owned_air=air,
                                                settled_fp=settled_footprint(cell), owned_states=states)
    from layer_reports import artifact_path, write_report
    write_report(name, {'stats': dict(stats), 'skipped': dict(skipped), 'conflicts': conflicts,
                        'planned_positions': len(plan.cells)}, dry=True)
    stats.pop('excluded_columns', None)
    stats.pop('retained_positions', None)
    floating = stats.pop('floating_columns', [])
    changes = [p for p, b in plan.cells.items() if block_id(world.block(*p), policy='read') != b]
    print(f'{name}: columns {stats["columns"]}  (levelled {stats["levelled"]})')
    print(f'  vector map: {stats["vector_map_features"]} features')
    print('  skipped: ' + ', '.join(f'{k} {v}' for k, v in skipped.most_common()))
    print('  evidence: ' + ', '.join(f'{k[5:]} {v}' for k, v in sorted(stats.items()) if k.startswith('how:')))
    print(f'  kinds: {dict(Counter(kind.values()).most_common())}')
    print(f'  top {stats["top"]}  raise {stats["raise"]}  clear {stats["clear"]}  '
          f'(kept: neighbour {stats["kept_neighbour"]}, masked {stats["kept_masked"]})')
    print(f'  planned cells {len(plan.cells)}  would change {len(changes)}')
    print(f'  bridges: {stats["bridge_ground"]} column(s) surfaced under a mapped bridge (deck is a structure\'s; '
          f'unbuilt unless a feature holds it); tunnels: {stats["tunnel_ground"]} surfaced above')
    print(f'  cavities: {stats["cavity_filled"]} air block(s) filled under a crust in {stats["cavity_columns"]} column(s)')
    print(f'  inferred shrubs: {stats["shrubs"]}, flowers: {stats["flowers"]}, cover: {stats["cover"]} '
          '(low confidence; see per-plot planting report)')
    print(f'  ground texture: tufts {stats["texture_tufts"]}, tall grass {stats["texture_tall_grass"]}, '
          f'bare {stats["texture_bare"]}, flowers {stats["texture_flowers"]} '
          '(#319; see ground_texture report)')
    floor = stats['woodland_floor']
    print(f'  woodland floor: podzol {floor["podzol"]}, coarse dirt {floor["coarse_dirt"]}, '
          f'moss {floor["moss_block"]}, grass kept {floor["grass"]}, edge {floor["edge"]} '
          '(#348; see woodland_floor report)')
    overlaps = lc.mask_plan_overlaps(plan.cells)
    print(f'  mask positions known: {len(masked)}  conflicts: {len(conflicts)}  '
          f'planned on a mask: {len(overlaps)}')
    for pos, want, why in conflicts[:10]:
        print(f'    CONFLICT {pos}: wanted {want}, masked by {why}')
    for pos, want, why in overlaps[:10]:
        print(f'    OVERLAP {pos}: planned {want}, masked by {why}')
    if conflicts or overlaps:
        print('  NOT SAFE TO APPLY: undo the listed mask first (scripts/element_undo.py; HANDOFF-LAYERED §3, ADR 010).')
    report_floating(floating)
    report_journal_floats(cell, plan)
    out = artifact_path(name, 'kinds.png', dry=True)
    kinds_image(cell, kind, out)
    print(f'  kinds image: {out}')
    if not skip_preview:
        from site_kit import preview
        pv = artifact_path(name, 'preview.png', dry=True)
        preview(plan, pv, scale=2)
        print(f'  preview: {pv}')
    return len(conflicts) + len(overlaps) + len(floating)


def report_floating(floating: list) -> None:
    """Print the floating-surface guard's columns (#296), loudly, a few at a time."""
    if not floating:
        return
    print(f'  FLOATING: {len(floating)} surface(s) would stand over air; NOT SAFE TO APPLY')
    for row in floating[:10]:
        print(f"    ({row['x']},{row['z']}) surface y{row['surface_y']}, support y{row['support_y']}, "
              f"{row['air']} air below")


def journal_floats(view, cell: tuple[int, int], planned: set) -> tuple[list, list]:
    """Floating ground tops the journal holds now, split by whether this plan surfaces them (task 297).

    @param view: the journal (``cell_registry.view_registry()``)
    @param planned: ``{(x, z)}`` columns this ground plan surfaces
    @returns: ``(settled, left)``: audit rows this plan surfaces, and rows it leaves, each of the
      latter with ``clear``, the command that settles it (a feature's reapply/redo, or a ground redo)
    """
    from ground_float_audit import audit, clear_command
    rows = audit(view, [cell])[tuple(cell)]
    settled = [r for r in rows if (r['x'], r['z']) in planned]
    left = [{**r, 'clear': clear_command(view, cell, r)} for r in rows if (r['x'], r['z']) not in planned]
    return settled, left


def report_journal_floats(cell: tuple[int, int], plan: Plan) -> None:
    """Print the journal's floating ground tops: those this plan surfaces, and the clear for the rest."""
    from cell_registry import view_registry
    from ground_float_audit import describe
    settled, left = journal_floats(view_registry(), cell, {(x, z) for x, _y, z in plan.cells})
    if settled:
        print(f'  journal floats this plan surfaces: {len(settled)}')
        for row in settled[:10]:
            print(f'    {describe(row)}')
    if left:
        print(f'  journal floats this plan leaves: {len(left)} (not ground\'s to fill)')
        for row in left[:10]:
            print(f"    {describe(row)}  [clear: {row['clear']}]")


def apply_cell(cell: tuple[int, int]) -> int:
    """Live apply: lock held, re-plan from the live world, readback.

    Returns 0 on a clean readback, otherwise 1.
    """
    import os
    from site_kit import apply
    from stage_timing import timed_stage

    if not os.environ.get('SITE_OWNER'):
        raise SystemExit('SITE_OWNER is not set; claim the lock first (scripts/site_lock.py claim <owner> ...)')
    from readiness_evidence import require_ready
    name = plan_name(cell, 'ground')
    require_ready([cell], 'ground', redoing=(name,))    # a re-apply may pass its own mismatch (#284)
    plan = Plan(name, input_cells=[cell])
    world = _fresh_world(strict=True)
    masked = lc.load_masked_positions()
    ignored = lc.ignored_builds(cell)
    states = lc.neighbour_owned_states(cell, ignored)
    owned, air = set(states), {p for p, b in states.items() if block_id(b, policy='read') in lc.AIRS}
    with timed_stage('layer planning (includes surface warm-up)'):
        stats, conflicts, kind, skipped = plan_cell(cell, masked, owned, plan, world, owned_air=air,
                                                    settled_fp=settled_footprint(cell), owned_states=states)
    from layer_reports import write_report
    write_report(name, {'stats': dict(stats), 'skipped': dict(skipped), 'conflicts': conflicts,
                        'planned_positions': len(plan.cells)})
    stats.pop('excluded_columns', None)
    stats.pop('retained_positions', None)
    floating = stats.pop('floating_columns', [])
    print('  skipped: ' + ', '.join(f'{k} {v}' for k, v in skipped.most_common()))
    overlaps = lc.mask_plan_overlaps(plan.cells)
    if conflicts or overlaps:
        print(f'REFUSED: {len(conflicts) + len(overlaps)} mask conflict(s); undo the masks first (§3, ADR 010)')
        return 1
    if floating:
        report_floating(floating)
        print(f'REFUSED: {len(floating)} ground surface(s) would float over air (#296); nothing written')
        return 1
    print(f'{name}: {len(plan.cells)} cells planned, stats {dict(stats)}')
    rb = apply(plan, modifies=ignored, stage='ground-layer', cell=cell,
               strict=True, meta={'note': 'first-principles ground (vector map + aerial); verify: patrol, visuals; ground redo preserves trees/decor completion'})
    print(f'readback: {rb["planned"]} planned, {rb["mismatches"]} mismatches')
    report_journal_floats(cell, plan)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cell', required=True, help='tx,tz e.g. 48,-44')
    ap.add_argument('--skip-preview', action='store_true')
    ap.add_argument('--apply', action='store_true', help='LIVE: write to the world (needs SITE_OWNER + claimed lock)')
    a = ap.parse_args()
    cell = tuple(int(v) for v in a.cell.split(','))
    if a.apply:
        return apply_cell(cell)
    return 1 if run(cell, skip_preview=a.skip_preview) else 0


if __name__ == '__main__':
    sys.exit(main())
