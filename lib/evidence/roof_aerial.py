#!/usr/bin/env python3
"""Flat-roof colour and PV arrays from a registered aerial crop (#337).

PV panels on a flat roof show as a regular grid of light gaps (between rows
and between panels in a row) at about 1-2 m pitch. The roof membrane, plant
(irregular, or striped one way only) and a plain blue tarp do not. So each
footprint column is scored for periodicity on both axes (correlation of a
~4-block luminance window with itself shifted by one pitch); a PV column is
periodic on both axes and blue. Columns group into 4-connected arrays reduced
to boxes; each box then regrows up to MAX_GROW lines a side over panel-coloured
columns, since the window blurs array edges. The row axis is the clearly
stronger axis, else the box's long side. The roof
colour is the dominant (modal) colour of the non-PV columns, so plant, shadow
and edge cladding do not darken it, snapped to a flat-roof material.

Needs 2.5 aerial pixels a block (0.4 m/px) or finer to resolve rows; a
coarser aerial is refused rather than guessed (spike: docs/plans/bigbox-roof-aerial.md).
Any aerial type works: a ``<image>.registration.json`` (``aerial_registered``)
or a Web Mercator mosaic named by its zoom and top-left tile.

  python3 scripts/roof_aerial.py derive --image IMG --mosaic 19,260093,175744 \\
      --spec site/builds/w465660355.json --label WS [--shift 0,0] [--sheet out.png] [--write]
  python3 scripts/roof_aerial.py derive --image IMG --registration IMG.registration.json --box x0,z0,x1,z1

``--write`` sets ``roofs[label].material`` and ``roofs[label].pv_arrays`` in a flat roof of the
spec; ``feature_build`` lays the arrays as daylight_detector rows. Look-only
aerials stay outside the repo; only the derived boxes go into the spec.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import deque
from pathlib import Path

import numpy as np

ROW_MIN_M = 1.0          # shortest PV row pitch looked for (metres = blocks)
ROW_MAX_M = 2.2          # longest
MIN_PX_PER_BLOCK = 2.5   # spike: clean to 2.65 px/block (0.38 m/px), arrays split from 2.4
WINDOW_BLOCKS = 2.0      # half-width of the periodicity window
AXIS_TIE = 0.1           # periodicity difference below which the long side picks the axis
EDGE_FILL = 0.5          # trim box edge lines with less PV than this
MAX_GROW = 3             # lines a box may regrow a side (the window erodes about WINDOW_BLOCKS)
COLOUR_BIN = 16          # membrane colour histogram bin (0..255 per channel)
MIN_COVERAGE = 0.9       # share of roof columns the aerial must cover (else the registration is wrong)



def nearest_roof_material(rgb) -> str:
    """Closest flat-roof material family to an aerial colour.

    @param rgb: (r, g, b) 0..255
    @returns: material family name for ``roofs[label].material``
    """
    from roof_material import load_catalogue
    palette = [(name, row['colour']) for name, row in load_catalogue()['families'].items()
               if row.get('slab')]
    return min(palette, key=lambda t: sum((a - b) ** 2 for a, b in zip(t[1], rgb)))[0]


def period_score(profile, lo: int, hi: int) -> tuple[float, int]:
    """Strongest correlation of a 1-D profile with itself shifted by a lag in ``lo..hi``.

    Each lag correlates only the overlapping parts (Pearson), so a clean stripe
    scores about 1 whatever its lag, rather than being capped at (n - lag) / n.

    @param profile: luminance profile (any length > hi)
    @param lo: shortest lag in samples (>= 1)
    @param hi: longest lag in samples
    @returns: ``(score, lag)``; score 0 for a flat profile, about 1 for a clean stripe
    """
    p = np.asarray(profile, float)
    if len(p) <= hi or float(np.ptp(p)) <= 1e-9:
        return 0.0, 0
    best, lag = 0.0, 0
    for k in range(lo, hi + 1):
        a, b = p[:-k] - p[:-k].mean(), p[k:] - p[k:].mean()
        d = math.sqrt(float((a * a).sum()) * float((b * b).sum()))
        r = float((a * b).sum()) / d if d > 1e-9 else 0.0
        if r > best:
            best, lag = r, k
    return best, lag


def components(cells) -> list[set]:
    """4-connected groups of (x, z) cells.

    @param cells: iterable of (x, z)
    @returns: list of sets
    """
    left = set(cells)
    out = []
    while left:
        seed = left.pop()
        comp = {seed}
        q = deque([seed])
        while q:
            x, z = q.popleft()
            for n in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
                if n in left:
                    left.remove(n)
                    comp.add(n)
                    q.append(n)
        out.append(comp)
    return out


def _trim_box(comp: set) -> tuple[int, int, int, int] | None:
    """Bounding box of a component with sparse edge lines peeled off."""
    x0, x1 = min(c[0] for c in comp), max(c[0] for c in comp)
    z0, z1 = min(c[1] for c in comp), max(c[1] for c in comp)
    while x0 <= x1 and z0 <= z1:
        fills = {
            'x0': sum((x0, z) in comp for z in range(z0, z1 + 1)) / (z1 - z0 + 1),
            'x1': sum((x1, z) in comp for z in range(z0, z1 + 1)) / (z1 - z0 + 1),
            'z0': sum((x, z0) in comp for x in range(x0, x1 + 1)) / (x1 - x0 + 1),
            'z1': sum((x, z1) in comp for x in range(x0, x1 + 1)) / (x1 - x0 + 1),
        }
        side, fill = min(fills.items(), key=lambda kv: kv[1])
        if fill >= EDGE_FILL:
            return x0, z0, x1, z1
        if side == 'x0':
            x0 += 1
        elif side == 'x1':
            x1 -= 1
        elif side == 'z0':
            z0 += 1
        else:
            z1 -= 1
    return None


def membrane_colour(colours) -> tuple[int, int, int] | None:
    """Dominant surface colour: mean of the most common coarse colour bin.

    @param colours: iterable of (r, g, b) 0..255
    @returns: (r, g, b) ints, or None when empty
    """
    cols = np.asarray(list(colours), float).reshape(-1, 3)
    if not len(cols):
        return None
    bins = np.floor(cols / COLOUR_BIN).astype(int)
    keys, inv, counts = np.unique(bins, axis=0, return_inverse=True, return_counts=True)
    top = int(np.argmax(counts))
    return tuple(int(round(v)) for v in cols[inv.reshape(-1) == top].mean(axis=0))


def grow_box(box, colour: dict, panel, roof, footprint, *, taken=frozenset(), max_grow: int = MAX_GROW):
    """Regrow a box side by side over lines that look more like panel than roof.

    A side grows one line when at least half of that line's footprint columns
    with a colour are nearer ``panel`` than ``roof`` and none is ``taken``.

    @param box: (x0, z0, x1, z1) inclusive
    @param colour: {(x, z): (r, g, b)} block colours
    @param panel: array core colour
    @param roof: membrane colour
    @param footprint: roof columns
    @param taken: columns of other arrays (never crossed)
    @param max_grow: most lines added to one side
    @returns: grown (x0, z0, x1, z1)
    """
    panel, roof = np.asarray(panel, float), np.asarray(roof, float)

    def panel_like(c):
        v = np.asarray(colour[c], float)
        return float(((v - panel) ** 2).sum()) < float(((v - roof) ** 2).sum())

    def ok(line):
        cells = [c for c in line if c in footprint and c in colour]
        if not cells or any(c in taken for c in line):
            return False
        return sum(panel_like(c) for c in cells) * 2 >= len(cells)

    x0, z0, x1, z1 = box
    grown = {'x0': 0, 'x1': 0, 'z0': 0, 'z1': 0}
    changed = True
    while changed:
        changed = False
        if grown['x0'] < max_grow and ok([(x0 - 1, z) for z in range(z0, z1 + 1)]):
            x0 -= 1; grown['x0'] += 1; changed = True
        if grown['x1'] < max_grow and ok([(x1 + 1, z) for z in range(z0, z1 + 1)]):
            x1 += 1; grown['x1'] += 1; changed = True
        if grown['z0'] < max_grow and ok([(x, z0 - 1) for x in range(x0, x1 + 1)]):
            z0 -= 1; grown['z0'] += 1; changed = True
        if grown['z1'] < max_grow and ok([(x, z1 + 1) for x in range(x0, x1 + 1)]):
            z1 += 1; grown['z1'] += 1; changed = True
    return x0, z0, x1, z1


def _sample(rgb: np.ndarray, A: np.ndarray, bbox, s: int, shift) -> np.ndarray:
    """Resample the aerial on the MC grid (bilinear): ``s`` samples a block, NaN off the image."""
    x0, z0, x1, z1 = bbox
    dx, dz = shift
    xs = x0 + dx + (np.arange((x1 - x0 + 1) * s) + 0.5) / s
    zs = z0 + dz + (np.arange((z1 - z0 + 1) * s) + 0.5) / s
    X, Z = np.meshgrid(xs, zs)
    u = A[0, 0] * X + A[0, 1] * Z + A[0, 2] - 0.5   # pixel centres at integer + 0.5
    v = A[1, 0] * X + A[1, 1] * Z + A[1, 2] - 0.5
    h, w = rgb.shape[:2]
    inside = (u >= 0) & (u <= w - 1) & (v >= 0) & (v <= h - 1)
    out = np.full(X.shape + (3,), np.nan)
    ui, vi = u[inside], v[inside]
    u0 = np.minimum(np.floor(ui).astype(int), w - 2) if w > 1 else np.zeros_like(ui, int)
    v0 = np.minimum(np.floor(vi).astype(int), h - 2) if h > 1 else np.zeros_like(vi, int)
    fu, fv = (ui - u0)[:, None], (vi - v0)[:, None]
    u1, v1 = np.minimum(u0 + 1, w - 1), np.minimum(v0 + 1, h - 1)
    img = rgb[..., :3]
    out[inside] = ((img[v0, u0] * (1 - fu) + img[v0, u1] * fu) * (1 - fv)
                   + (img[v1, u0] * (1 - fu) + img[v1, u1] * fu) * fv)
    return out


def _covered_profile(window, axis):
    """Mean finite image samples per line, omitting uncovered border lines."""
    counts = np.isfinite(window).sum(axis=axis)
    sums = np.nansum(window, axis=axis)
    return sums[counts > 0] / counts[counts > 0]


def row_boxes(cells, box, rows, pitch):
    """Encode supported detector runs as boxes without filling an angled hull."""
    x0, z0, _, _ = box
    lines = {}
    for x, z in sorted(cells):
        across, along = (z, x) if rows == 'x' else (x, z)
        if (across - (z0 if rows == 'x' else x0)) % pitch == 0:
            lines.setdefault(across, []).append(along)
    out = []
    for across, values in sorted(lines.items()):
        start = end = values[0]
        for value in values[1:] + [values[-1] + 2]:
            if value == end + 1:
                end = value
                continue
            out.append((start, across, end, across) if rows == 'x' else (across, start, across, end))
            start = end = value
    return out


def derive(rgb, A, footprint, *, shift=(0, 0), pitch: int = 2, threshold: float = 0.5,
           blue_margin: float = 30.0, min_cells: int = 30, source_note: str = '') -> dict:
    """Roof colour and PV arrays for one footprint from a registered aerial.

    @param rgb: aerial image array (h, w, 3) 0..255
    @param A: 2x3 similarity, MC (x, z, 1) -> image (u, v)
    @param footprint: set of (x, z) roof columns
    @param shift: (dx, dz) blocks the roof appears displaced in the aerial (lean)
    @param pitch: detector row pitch written to each array (blocks)
    @param threshold: periodicity a PV column needs on both axes (0..1)
    @param blue_margin: blue minus red a PV column needs (0..255)
    @param min_cells: smallest array kept (columns)
    @param source_note: evidence note appended to each array's ``source``
    @returns: ``{px_per_block, rotation_deg, shift, roof_rgb, material, coverage, pv_share, pv_arrays}``
    @raises ValueError: on an aerial too coarse to resolve PV rows, an empty footprint, or one
        the aerial covers less than ``MIN_COVERAGE`` of (a wrong registration)
    """
    A = np.asarray(A, float)
    rgb = np.asarray(rgb, float)
    if pitch < 1 or min_cells < 1 or not math.isfinite(blue_margin):
        raise ValueError('pitch and min_cells must be positive; blue_margin must be finite')
    ppb = float(math.hypot(A[0, 0], A[1, 0]))
    if ppb + 1e-9 < MIN_PX_PER_BLOCK:
        raise ValueError(f'aerial too coarse for PV rows: {ppb:.1f} px/block (needs >= {MIN_PX_PER_BLOCK:g})')
    if not footprint:
        raise ValueError('empty footprint')
    s = max(int(round(ppb)), 1)
    lo, hi = max(2, int(math.floor(ROW_MIN_M * s))), int(math.ceil(ROW_MAX_M * s))
    half = int(round(WINDOW_BLOCKS * s))
    pad = int(math.ceil(WINDOW_BLOCKS)) + 1
    fx0, fx1 = min(c[0] for c in footprint), max(c[0] for c in footprint)
    fz0, fz1 = min(c[1] for c in footprint), max(c[1] for c in footprint)
    bx0, bz0 = fx0 - pad, fz0 - pad
    grid = _sample(rgb, A, (bx0, bz0, fx1 + pad, fz1 + pad), s, shift)
    lum = grid.mean(axis=2)

    colour, score_rx, score_rz, lag_rx, lag_rz = {}, {}, {}, {}, {}
    for x, z in footprint:
        cu, cv = (x - bx0) * s + s // 2, (z - bz0) * s + s // 2
        block = grid[cv - s // 2:cv - s // 2 + s, cu - s // 2:cu - s // 2 + s]
        if np.isnan(block).any():
            continue
        colour[(x, z)] = block.reshape(-1, 3).mean(axis=0)
        win = lum[cv - half:cv + half + 1, cu - half:cu + half + 1]
        # Stripes parallel to x (rows along x) vary down the window: profile over z.
        score_rx[(x, z)], lag_rx[(x, z)] = period_score(_covered_profile(win, 1), lo, hi)
        score_rz[(x, z)], lag_rz[(x, z)] = period_score(_covered_profile(win, 0), lo, hi)

    coverage = len(colour) / len(footprint)
    if coverage < MIN_COVERAGE:
        raise ValueError(
            f'aerial covers only {coverage:.0%} of the roof columns (needs {MIN_COVERAGE:.0%}); '
            'check the registration or mosaic tiles and --shift')
    pv = {
        c for c in score_rx
        if min(score_rx[c], score_rz[c]) >= threshold and colour[c][2] - colour[c][0] >= blue_margin
    }
    cores = []
    for comp in components(pv):
        if len(comp) < min_cells:
            continue
        box = _trim_box(comp)
        if box is None:
            continue
        x0, z0, x1, z1 = box
        cells = {c for c in comp if x0 <= c[0] <= x1 and z0 <= c[1] <= z1}
        if len(cells) >= min_cells:
            cores.append((box, cells))
    membrane = membrane_colour(colour[c] for c in colour if c not in pv)

    def inside(b):
        return {c for c in footprint if b[0] <= c[0] <= b[2] and b[1] <= c[1] <= b[3]}

    arrays, in_arrays = [], set()
    boxes = [b for b, _ in cores]
    for i, (box, cells) in enumerate(cores):
        if membrane is not None:
            panel = np.median([colour[c] for c in cells], axis=0)
            taken = set().union(*(inside(b) for j, b in enumerate(boxes) if j != i)) if len(boxes) > 1 else set()
            box = grow_box(box, colour, panel, membrane, footprint, taken=taken)
            boxes[i] = box
        x0, z0, x1, z1 = box
        mx = float(np.mean([score_rx[c] for c in cells]))
        mz = float(np.mean([score_rz[c] for c in cells]))
        if abs(mx - mz) < AXIS_TIE:
            rows = 'x' if (x1 - x0) >= (z1 - z0) else 'z'
        else:
            rows = 'x' if mx > mz else 'z'
        lags = lag_rx if rows == 'x' else lag_rz
        row_pitch = float(np.median([lags[c] for c in cells])) / s
        score = mx if rows == 'x' else mz
        note = f'; {source_note}' if source_note else ''
        supported = inside(box)
        if membrane is not None:
            supported = {c for c in supported if c in colour and (
                c in cells or np.sum((colour[c] - panel) ** 2) < np.sum((colour[c] - membrane) ** 2))}
        # Keep rectangular arrays compact; irregular/angled support becomes row
        # runs using the existing box schema, never its rectangular hull.
        layout = [box] if supported == inside(box) else row_boxes(supported, box, rows, pitch)
        for bounds in layout:
            arrays.append({
                'box': list(bounds), 'rows': rows, 'pitch': pitch,
                'source': f'aerial PV array, rows along {rows}, row pitch {row_pitch:.1f} m, '
                          f'periodicity {score:.2f}{note}',
                'row_pitch_m': round(row_pitch, 2), 'score': round(score, 2), 'cells': len(cells),
            })
        in_arrays |= supported
    arrays.sort(key=lambda a: (a['box'][1], a['box'][0]))

    roof = membrane_colour(colour[c] for c in colour if c not in in_arrays)
    roof_rgb = list(roof) if roof else None
    return {
        'px_per_block': round(ppb, 3),
        'rotation_deg': round(float(math.degrees(math.atan2(A[1, 0], A[0, 0]))), 2),
        'shift': list(shift),
        'roof_rgb': roof_rgb,
        'material': nearest_roof_material(roof_rgb) if roof_rgb else None,
        'coverage': round(coverage, 3),
        'pv_share': round(len(in_arrays) / len(footprint), 3),
        'pv_arrays': arrays,
    }


def patch_spec(path, label: str, derived: dict, *, replace: bool = False) -> None:
    """Write derived ``material`` and ``pv_arrays`` into a flat ``roofs[label]``; other keys stay.

    Refuses (and leaves the file untouched) when the roof is not flat, when no
    arrays were found, or when the roof already has ``pv_arrays`` and ``replace``
    is not set, so a re-run or a blank crop never erases hand-added boxes.

    @param path: feature spec JSON
    @param label: roof label
    @param derived: ``derive`` result
    @param replace: overwrite existing ``pv_arrays``
    @raises SystemExit: on any refusal above, or when the spec has no ``roofs[label]``
    """
    path = Path(path)
    spec = json.loads(path.read_text())
    roof = (spec.get('roofs') or {}).get(label)
    if not isinstance(roof, dict):
        raise SystemExit(f'{path}: no roofs[{label}]')
    if roof.get('kind') != 'flat':
        raise SystemExit(f'{path}: roofs[{label}] is {roof.get("kind")!r}; PV arrays need a flat roof')
    if not derived.get('pv_arrays'):
        raise SystemExit(f'{path}: no PV arrays found; spec left unchanged')
    if roof.get('pv_arrays') and not replace:
        raise SystemExit(
            f'{path}: roofs[{label}] already has {len(roof["pv_arrays"])} pv_arrays; '
            'pass --replace-pv to overwrite them')
    if derived.get('material'):
        roof['material'] = derived['material']
    roof['pv_arrays'] = [
        {k: a[k] for k in ('box', 'rows', 'pitch', 'source')} for a in derived['pv_arrays']
    ]
    path.write_text(json.dumps(spec, indent=1) + '\n')


def check_registration(reg: dict, image: Path, size: tuple[int, int]) -> None:
    """Refuse a registration made for another image, or whose tie points fall off it.

    @param reg: registration JSON (``image``, ``ties``)
    @param image: aerial image path given on the command line
    @param size: (width, height) of that image
    @raises SystemExit: on a name mismatch or a tie point outside the image
    """
    named = reg.get('image')
    if named and Path(named).name != Path(image).name:
        raise SystemExit(f'registration names {named!r}, not {Path(image).name!r}')
    w, h = size
    off = [t['px'] for t in reg.get('ties') or [] if not (0 <= t['px'][0] <= w and 0 <= t['px'][1] <= h)]
    if off:
        raise SystemExit(f'registration tie points outside the {w}x{h} image: {off[:3]}')


_MOSAIC_NAME = re.compile(r'z(\d+)-(\d+)x(\d+)-(\d+)-(\d+)')


def check_mosaic(image: Path, size: tuple[int, int], spec: str) -> None:
    """Refuse a mosaic whose size is not whole 256 px tiles, or whose name disagrees with ``--mosaic``.

    @param image: mosaic path; a name like ``mosaic-z19-4x4-260093-175744.png`` is cross-checked
    @param size: (width, height) of the image
    @param spec: ``--mosaic`` value ``Z,TX0,TY0``
    @raises SystemExit: on a size or tile mismatch
    """
    w, h = size
    if w % 256 or h % 256:
        raise SystemExit(f'mosaic {w}x{h} is not whole 256 px tiles')
    m = _MOSAIC_NAME.search(Path(image).name)
    if not m:
        print("warning: mosaic filename has no tile identity; only dimensions were checked", file=sys.stderr)
        return
    zoom, nx, ny, tx0, ty0 = (int(v) for v in m.groups())
    if (zoom, tx0, ty0) != tuple(int(v) for v in spec.split(',')):
        raise SystemExit(f'--mosaic {spec} disagrees with the file name tile z{zoom} {tx0},{ty0}')
    if (w, h) != (nx * 256, ny * 256):
        raise SystemExit(f'mosaic size {w}x{h} does not match its name ({nx}x{ny} tiles)')


def review_sheet(rgb, A, footprint, derived: dict, out, scale: int = 2) -> Path:
    """Aerial crop over the footprint with each array box and its detector rows drawn on.

    @param rgb: aerial image array
    @param A: MC -> image similarity
    @param footprint: roof columns
    @param derived: ``derive`` result (uses its ``shift``)
    @param out: PNG path
    @param scale: upscale factor
    @returns: written path
    """
    from PIL import Image, ImageDraw
    dx, dz = derived.get('shift') or (0, 0)
    xs = [c[0] for c in footprint]
    zs = [c[1] for c in footprint]
    x0, z0, x1, z1 = min(xs) - 3, min(zs) - 3, max(xs) + 4, max(zs) + 4
    ppb = derived['px_per_block']
    s = max(int(round(ppb)), 1)
    grid = _sample(np.asarray(rgb, float), np.asarray(A, float), (x0, z0, x1, z1), s, (dx, dz))
    img = Image.fromarray(np.nan_to_num(grid).clip(0, 255).astype('uint8'))
    img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
    d = ImageDraw.Draw(img)
    k = s * scale

    def px(x, z):
        return (x - x0) * k, (z - z0) * k

    for a in derived['pv_arrays']:
        bx0, bz0, bx1, bz1 = a['box']
        d.rectangle([*px(bx0, bz0), px(bx1 + 1, bz1 + 1)[0] - 1, px(bx1 + 1, bz1 + 1)[1] - 1],
                    outline=(255, 40, 40), width=2)
        p = a['pitch']
        if a['rows'] == 'x':
            for z in range(bz0, bz1 + 1, p):
                d.line([px(bx0, z + 0.5), px(bx1 + 1, z + 0.5)], fill=(255, 220, 0), width=1)
        else:
            for x in range(bx0, bx1 + 1, p):
                d.line([px(x + 0.5, bz0), px(x + 0.5, bz1 + 1)], fill=(255, 220, 0), width=1)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def _mosaic_affine(spec: str, bbox) -> np.ndarray:
    """Similarity MC -> mosaic px for a Web Mercator mosaic ``Z,TX0,TY0`` (top-left tile)."""
    from aerial_registered import fit_similarity
    from world_frame import mc_batch_to_wgs84, tile_float
    zoom, tx0, ty0 = (int(v) for v in spec.split(','))
    x0, z0, x1, z1 = bbox
    pts = [(x0, z0), (x1, z0), (x0, z1), (x1, z1), ((x0 + x1) / 2, (z0 + z1) / 2)]
    ties = []
    for (x, z), (lat, lon) in zip(pts, mc_batch_to_wgs84(pts)):
        fx, fy = tile_float(lat, lon, zoom)
        ties.append([x, z, (fx - tx0) * 256, (fy - ty0) * 256])
    return fit_similarity(ties)


def _footprint(args) -> set:
    """Roof columns from ``--box`` or the spec label's mapped ways minus its ``exclude`` boxes."""
    if args.box:
        x0, z0, x1, z1 = (int(v) for v in args.box.split(','))
        return {(x, z) for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)}
    import osm_footprints
    spec = json.loads(Path(args.spec).read_text())
    ways = {w for w, lab in spec['ways'].items() if lab == args.label}
    idx = osm_footprints.OSMIndex(read_only=True)
    cells = set()
    for c, i in idx.cells().items():
        props = idx.buildings[i][1]
        if str(props.get('osm_way_id') or props.get('osm_id')) in ways:
            cells.add(c)
    for bx0, bz0, bx1, bz1 in (spec.get('exclude') or {}).get(args.label, []):
        cells -= {(x, z) for x in range(bx0, bx1 + 1) for z in range(bz0, bz1 + 1)}
    return cells


def main(argv=None) -> int:
    """Derive roof materials and PV runs, optionally patching an explicit spec."""
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    d = sub.add_parser('derive', help='roof colour + PV arrays for one roof')
    d.add_argument('--image', required=True, help='aerial image (any keepable or look-only aerial)')
    reg = d.add_mutually_exclusive_group(required=True)
    reg.add_argument('--registration', help='<image>.registration.json (aerial_registered)')
    reg.add_argument('--mosaic', help='Web Mercator mosaic: Z,TX0,TY0 (zoom, top-left tile)')
    where = d.add_mutually_exclusive_group(required=True)
    where.add_argument('--spec', help='feature spec; needs --label')
    where.add_argument('--box', help='x0,z0,x1,z1 roof columns (inclusive)')
    d.add_argument('--label', help='roof label in --spec')
    d.add_argument('--shift', default='0,0', help='dx,dz blocks of aerial lean (roof drawn this far off)')
    d.add_argument('--pitch', type=int, default=2, help='detector row pitch (blocks)')
    d.add_argument('--threshold', type=float, default=0.5, help='periodicity needed on both axes')
    d.add_argument('--blue-margin', type=float, default=30.0, help='minimum blue minus red; use 0 for neutral dark arrays')
    d.add_argument('--min-cells', type=int, default=30, help='smallest connected array support')
    d.add_argument('--sheet', help='write a review PNG with arrays and rows drawn on')
    d.add_argument('--write', action='store_true', help='patch roofs[label] in --spec')
    d.add_argument('--replace-pv', action='store_true', help='with --write: overwrite existing pv_arrays')
    args = ap.parse_args(argv)
    if args.spec and not args.label:
        ap.error('--spec needs --label')
    if args.write and not args.spec:
        ap.error('--write needs --spec')
    if args.replace_pv and not args.write:
        ap.error('--replace-pv needs --write')
    if args.pitch < 1 or args.min_cells < 1 or not math.isfinite(args.blue_margin):
        ap.error('pitch/min-cells must be positive and blue-margin finite')
    from PIL import Image
    rgb = np.asarray(Image.open(args.image).convert('RGB'))
    foot = _footprint(args)
    if not foot:
        raise SystemExit('no roof columns (label has no mapped way in the vector map?)')
    bbox = (min(c[0] for c in foot), min(c[1] for c in foot), max(c[0] for c in foot), max(c[1] for c in foot))
    size = (rgb.shape[1], rgb.shape[0])
    if args.registration:
        from aerial_registered import load
        reg, A = load(args.registration)
        check_registration(reg, Path(args.image), size)
    else:
        check_mosaic(Path(args.image), size, args.mosaic)
        A = _mosaic_affine(args.mosaic, bbox)
    shift = tuple(int(v) for v in args.shift.split(','))
    try:
        out = derive(rgb, A, foot, shift=shift, pitch=args.pitch, threshold=args.threshold,
                     blue_margin=args.blue_margin, min_cells=args.min_cells,
                     source_note=f'aerial {Path(args.image).name}')
    except ValueError as exc:
        raise SystemExit(str(exc))
    if args.sheet:
        out['sheet'] = str(review_sheet(rgb, A, foot, out, args.sheet))
    print(json.dumps(out, indent=1))
    if args.write:
        patch_spec(args.spec, args.label, out, replace=args.replace_pv)
        print(f'wrote roofs[{args.label}] material + {len(out["pv_arrays"])} pv_arrays to {args.spec}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
