#!/usr/bin/env python3
"""R-015 hybrid hand-off: SUSU (B42) + SU Retail Centre (B57), cell (48,-44).

Designer: claude-opus-5-5-low. Builder: apply only, do not redesign massing.
Evidence table: r015_susu_evidence.md (same folder). Massing is fully determined
by the rules below + OSM ways + DEFRA lidar; no free parameters for the builder.

Why a script, not a feature_build JSON spec: feature_build.py imports portals.py,
which needs the world provider providers/uos-portals; the bakeoff world has no
such package, so feature_build cannot even import here.

Usage (repo root; world already selected via ``--world bakeoff`` on the caller):
  python3 worlds/bakeoff/site/builds/r015_susu.py          # dry run + preview
  python3 worlds/bakeoff/site/builds/r015_susu.py --apply  # live

Massing rules (designer decisions):
  M1 footprint = OSM way 33830305 (SUSU) and 33830236 (Retail), squared only to
     kill 1-block notches/spurs (no halo). Pad line printed.
  M2 roof Y per column = lidar top_y (5x5 median) snapped to plateau levels,
     then 5x5 mode filter (x2). SUSU levels Y {52,56,57,59,61}; Retail {60}.
     Level-61 islands < 16 cells drop to 59 (noise, not plant rooms).
  M3 walls rise from local lidar ground (7x7 median DTM) so downhill (NW)
     faces gain height; parapet one block above every roof edge incl. steps.
  M4 storey datum: floors at Y = 0 mod 4 (4-block storeys).
Facade rules:
  F1 upper walls bricks; window = 2-high band at storey offsets 1-2, columns
     where (x+z) % 3 != 0 (reads as paired windows on the rotated walls).
  F2 SUSU walls above Y59 (plant rooms) light_gray_concrete with iron_bars louvre band.
  F3 Plaza shopfront: storey 1 on faces E/S of SUSU x>=6185 and all Retail faces
     except N: full-height glass (3 high) with black_concrete mullion where
     (x+z)%4==0, black_concrete fascia at ground+4.
  F4 Retail upper storey: white_concrete with a gray glass band.
Roof materials: SUSU level 57 (south wing) gray_concrete (dark in aerial),
  other SUSU/Retail roofs light_gray_concrete; parapet smooth_stone.
Greeblies: see GREEBLIES below (rooflights, AC units, plaza umbrellas+tables,
  entrance canopy, bins).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / 'scripts'))

from script_env import ROOT  # noqa: E402
import osm_footprints  # noqa: E402
from site_kit import Plan, apply, preview  # noqa: E402

NAME = 'r015_susu'
WAYS = {'33830305': 'susu', '33830236': 'retail'}
LEVELS = {'susu': [52, 56, 57, 59, 61], 'retail': [60]}
BBOX = (6136, -5592, 6264, -5486)       # x0, z0, x1, z1 inclusive (covers both + margin)
DATUM = 48
N4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
FACE_OF = {(1, 0): 'E', (-1, 0): 'W', (0, 1): 'S', (0, -1): 'N'}
FACING = {'E': 'east', 'W': 'west', 'S': 'south', 'N': 'north'}

DOOR_TARGETS = [  # (label, near x, near z, preferred outward face) -> double door
    ('susu', 6196, -5566, 'E'),   # SUSU main plaza entrance (faces the square, SV g11)
    ('susu', 6214, -5552, 'E'),   # SUSU second door on the east face
    ('retail', 6221, -5566, 'W'),  # Retail centre west door onto plaza
    ('retail', 6238, -5552, 'S'),  # Retail south door
]


def lidar_grids():
    out = ROOT / f'verification/site/{NAME}-lidar.npz'
    out.parent.mkdir(parents=True, exist_ok=True)
    x0, z0, x1, z1 = BBOX
    subprocess.run([sys.executable, str(REPO / 'shims/lidar_query.py'),
                    '--bbox-mc', f'{x0},{z0},{x1},{z1}', '--layers', 'top_y,ground_y',
                    '--format', 'npz', '--step', '1', '--max-cells', '200000',
                    '--out', str(out)], check=True, stdout=subprocess.DEVNULL)
    d = np.load(out, allow_pickle=True)
    assert int(d['step']) == 1 and int(d['x0']) == x0 and int(d['z0']) == z0
    return d['top_y'].astype(float), d['ground_y'].astype(float)


def footprints():
    idx = osm_footprints.OSMIndex()
    want = {i: WAYS[str(p.get('osm_way_id'))] for i, (_r, p) in enumerate(idx.buildings)
            if str(p.get('osm_way_id')) in WAYS}
    cells = {}
    for c, i in idx.cells().items():
        if i in want:
            cells[c] = want[i]
    return cells


def square(cells: set) -> set:
    """Fill 1-block notches, drop 1-block spurs (M1). No dilation halo."""
    s = set(cells)
    for _ in range(2):
        add = set()
        for x in range(min(c[0] for c in s) - 1, max(c[0] for c in s) + 2):
            for z in range(min(c[1] for c in s) - 1, max(c[1] for c in s) + 2):
                if (x, z) in s:
                    continue
                n = [(x + dx, z + dz) in s for dx, dz in N4]
                if (n[0] and n[1]) or (n[2] and n[3]) or sum(n) >= 3:
                    add.add((x, z))
        s |= add
        s -= {c for c in s if sum((c[0] + dx, c[1] + dz) in s for dx, dz in N4) <= 1}
    return s


def med(a, x, z, k):
    x0, z0 = BBOX[0], BBOX[1]
    i, j = z - z0, x - x0
    w = a[max(0, i - k):i + k + 1, max(0, j - k):j + k + 1]
    w = w[np.isfinite(w)]
    return float(np.median(w)) if w.size else float('nan')


def roof_levels(mask: dict, top):
    roof = {}
    for c, lab in mask.items():
        h = med(top, c[0], c[1], 2)
        lv = LEVELS[lab]
        roof[c] = min(lv, key=lambda L: abs(L - h)) if np.isfinite(h) else lv[0]
    for _ in range(2):
        nxt = {}
        for (x, z), lab in mask.items():
            votes = Counter(roof[(x + dx, z + dz)] for dx in range(-2, 3) for dz in range(-2, 3)
                            if mask.get((x + dx, z + dz)) == lab)
            nxt[(x, z)] = votes.most_common(1)[0][0]
        roof = nxt
    # drop small level-61 islands (M2)
    seen = set()
    for c in [c for c, r in roof.items() if r == 61]:
        if c in seen:
            continue
        comp, stack = [], [c]
        seen.add(c)
        while stack:
            q = stack.pop(); comp.append(q)
            for dx, dz in N4:
                n = (q[0] + dx, q[1] + dz)
                if roof.get(n) == 61 and n not in seen:
                    seen.add(n); stack.append(n)
        if len(comp) < 16:
            for q in comp:
                roof[q] = 59
    return roof


def build():
    top, gnd = lidar_grids()
    osm = footprints()
    mask = {}
    for lab in ('susu', 'retail'):
        raw = {c for c, l in osm.items() if l == lab}
        sq = square(raw)
        pad = len(sq - raw)
        cover = 100 * len(sq & raw) / max(1, len(raw))
        print(f'{lab}: footprint osm={len(raw)} readable={len(sq)} pad={pad} cover={cover:.0f}%')
        for c in sq:
            mask.setdefault(c, lab)
    roof = roof_levels(mask, top)
    ground = {}
    for c in mask:
        g = med(gnd, c[0], c[1], 3)
        ground[c] = int(round(g)) if np.isfinite(g) else 48
    for c in mask:                      # never let roof sit below ground + 4
        roof[c] = max(roof[c], ground[c] + 4)

    def outside_h(n):
        if n in mask:
            return roof[n]
        g = med(gnd, n[0], n[1], 1)
        return int(round(g)) - 1 if np.isfinite(g) else 40

    p = Plan(NAME)
    for c, lab in mask.items():
        x, z = c
        R, G = roof[c], ground[c]
        clear_top = max(R + 4, int(np.nanmax([med(top, x, z, 0), R])) + 1)
        nb = {d: outside_h((x + d[0], z + d[1])) for d in N4}
        parapet = any(h < R for h in nb.values())
        for y in range(G - 2, clear_top + 1):
            exposed = [FACE_OF[d] for d, h in nb.items() if h < y <= R]
            if y < G:
                st = 'stone'
            elif y == G:
                st = 'smooth_stone' if not exposed else 'stone_bricks'
            elif y == R:
                st = 'gray_concrete' if (lab == 'susu' and R == 57) else 'light_gray_concrete'
            elif y == R + 1 and parapet:
                st = 'smooth_stone'
            elif y > R:
                st = 'air'
            elif exposed:
                st = wall_block(lab, x, y, z, G, exposed)
            else:
                st = 'smooth_stone' if ((y - DATUM) % 4 == 0 and G + 2 <= y <= R - 2) else 'air'
            p.set(x, y, z, st)
    placed = doors(p, mask, ground, roof)
    greeblies(p, mask, roof, ground, gnd, placed)
    p.outside_footprint_ok = ('plaza furniture (umbrellas, picnic tables, bins, canopy posts) '
                              'from Street View g11 yaw 225/270 and aerial terrace; see r015_susu_evidence.md')
    return p


def wall_block(lab, x, y, z, G, faces):
    s = (y - DATUM) % 4
    shop = (lab == 'retail' and any(f != 'N' for f in faces)) or \
           (lab == 'susu' and x >= 6185 and any(f in ('E', 'S') for f in faces))
    if shop and G < y <= G + 4:
        if y == G + 4:
            return 'black_concrete'                  # fascia (F3)
        return 'black_concrete' if (x + z) % 4 == 0 else 'glass'
    if lab == 'retail':
        return 'gray_stained_glass' if s == 2 else 'white_concrete'   # F4
    if y > 59:
        return 'iron_bars' if y == 60 and (x + z) % 2 == 0 else 'light_gray_concrete'  # F2
    if s in (1, 2) and (x + z) % 3 != 0:
        return 'gray_stained_glass'
    return 'bricks'


def doors(p, mask, ground, roof):
    placed = []
    for lab, tx, tz, face in DOOR_TARGETS:
        d = next(k for k, v in FACE_OF.items() if v == face)
        cands = []
        for (x, z), l in mask.items():
            if l != lab or (x + d[0], z + d[1]) in mask or (x - d[0], z - d[1]) not in mask:
                continue
            side = (d[1], d[0])
            mate = (x + side[0], z + side[1])
            if mate not in mask or (mate[0] + d[0], mate[1] + d[1]) in mask:
                continue
            if ground[(x, z)] != ground[mate]:
                continue
            cands.append(((x - tx) ** 2 + (z - tz) ** 2, (x, z), mate))
        if not cands:
            print(f'WARN no door slot for {lab} near {tx},{tz} {face}')
            continue
        _, a, b = min(cands)
        G = ground[a]
        hinge = ('left', 'right')
        for k, (cx, cz) in enumerate((a, b)):
            for half, dy in (('lower', 1), ('upper', 2)):
                p.set(cx, G + dy, cz, f'dark_oak_door[facing={FACING[face]},half={half},hinge={hinge[k]}]')
            p.set(cx, G + 3, cz, 'black_concrete')          # lintel
            # clear the doorstep outside and the cell behind
            p.set(cx + d[0], G + 1, cz + d[1], 'air'); p.set(cx + d[0], G + 2, cz + d[1], 'air')
            p.set(cx - d[0], G + 1, cz - d[1], 'air'); p.set(cx - d[0], G + 2, cz - d[1], 'air')
        # builder fix (guard refusal): outer jambs where the wall line steps/notches away from the door pair
        sd = (d[1], d[0])
        for (ox, oz) in ((a[0] - sd[0], a[1] - sd[1]), (b[0] + sd[0], b[1] + sd[1])):
            for dy in (1, 2):
                cur = p.cells.get((ox, G + dy, oz))
                if cur is None or cur.split('[')[0].endswith('air'):
                    p.set(ox, G + dy, oz, 'black_concrete')
        print(f'door {lab} {face} at {a},{b} y={G + 1}')
        placed.append((lab, a, b, d, G))
    return placed


def put(p, x, y, z, s):
    p.set(x, y, z, s)


def greeblies(p, mask, roof, ground, gnd, placed):
    """Street-scale greeblies (evidence rows G1-G6)."""
    # G1 rooflights: three round domes on the middle SUSU block (aerial: green circles)
    for cx, cz in ((6182, -5547), (6186, -5541), (6190, -5535)):
        if mask.get((cx, cz)) != 'susu':
            continue
        R = roof[(cx, cz)]
        for dx in (0, 1):
            for dz in (0, 1):
                if roof.get((cx + dx, cz + dz)) == R:
                    put(p, cx + dx, R + 1, cz + dz, 'lime_stained_glass')
    # G2 AC / plant units on the 59 roofs: 2x1x1 iron block + trapdoor grille, sparse grid
    for (x, z), lab in mask.items():
        R = roof[(x, z)]
        if lab != 'susu' or R != 59 or x % 9 or z % 7:
            continue
        if all(roof.get((x + dx, z + dz)) == R for dx in (-1, 0, 1, 2) for dz in (-1, 0, 1)):
            put(p, x, R + 1, z, 'iron_block'); put(p, x + 1, R + 1, z, 'iron_block')
            put(p, x, R + 2, z, 'iron_trapdoor[half=bottom,open=false]')
            put(p, x + 1, R + 2, z, 'iron_trapdoor[half=bottom,open=false]')
    # G3 plaza umbrellas (red) + picnic tables, east of SUSU on the square
    def g(x, z):
        v = med(gnd, x, z, 0)
        return int(round(v)) if np.isfinite(v) else 54
    for ux, uz in ((6203, -5573), (6210, -5568), (6200, -5579)):
        if any((ux + dx, uz + dz) in mask for dx in range(-2, 3) for dz in range(-2, 3)):
            continue
        G = g(ux, uz)
        for dy in (1, 2, 3):
            put(p, ux, G + dy, uz, 'spruce_fence')
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                put(p, ux + dx, G + 4, uz + dz, 'red_wool')
        for dx in (-1, 1):
            put(p, ux + dx, G + 1, uz, 'spruce_stairs[facing=east,half=top]' if dx < 0 else
                'spruce_stairs[facing=west,half=top]')
    # G4 courtyard terrace tables (aerial terrace in the SUSU notch west of the middle block)
    for tx, tz in ((6168, -5550), (6171, -5546)):
        if (tx, tz) in mask:
            continue
        G = g(tx, tz)
        put(p, tx, G + 1, tz, 'spruce_fence'); put(p, tx, G + 2, tz, 'spruce_pressure_plate')
    # G5 entrance canopy over SUSU main door: smooth_stone 3-deep on two black posts
    # (Street View g11 yaw 225: flat canopy on steel posts at the plaza edge)
    if placed:
        _lab, a, b, d, G = placed[0]
        side = (b[0] - a[0], b[1] - a[1])
        for k in range(-1, 3):                      # along the wall: door pair +1 each side
            for out in (1, 2, 3):
                cx = a[0] + side[0] * k + d[0] * out
                cz = a[1] + side[1] * k + d[1] * out
                if (cx, cz) not in mask:
                    put(p, cx, G + 4, cz, 'smooth_stone')
        for k in (-1, 2):
            cx, cz = a[0] + side[0] * k + d[0] * 3, a[1] + side[1] * k + d[1] * 3
            if (cx, cz) not in mask:
                for dy in (1, 2, 3):
                    put(p, cx, G + dy, cz, 'polished_blackstone_wall')
    # G6 bins: two on the plaza by the brick wall
    for bx, bz in ((6214, -5568), (6212, -5531)):
        if (bx, bz) not in mask:
            put(p, bx, g(bx, bz) + 1, bz, 'composter')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    plan = build()
    preview(plan, ROOT / f'verification/site/{NAME}-preview.png', scale=4)
    print(plan.summary() if hasattr(plan, 'summary') else len(plan.cells))
    apply(plan, dry=not a.apply)


if __name__ == '__main__':
    main()
