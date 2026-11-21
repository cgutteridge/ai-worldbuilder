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

``--write`` sets ``roofs[label].material`` and ``roofs[label].pv_arrays`` in the
spec; ``feature_build`` lays the arrays as daylight_detector rows. Look-only
aerials stay outside the repo; only the derived boxes go into the spec.
"""
from __future__ import annotations

import argparse
import json
import math
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

# Flat-roof material families (all have slabs) with approximate texture colours.
FLAT_ROOF_PALETTE: list[tuple[str, tuple[int, int, int]]] = [
    ('smooth_stone', (158, 158, 158)),
    ('stone', (125, 125, 125)),
    ('polished_andesite', (132, 135, 133)),
    ('polished_diorite', (192, 193, 194)),
    ('smooth_quartz', (235, 229, 222)),
    ('polished_deepslate', (72, 72, 73)),
    ('polished_blackstone', (53, 48, 56)),
    ('smooth_sandstone', (223, 214, 170)),
    ('brick', (150, 97, 83)),
]


def nearest_roof_material(rgb) -> str:
    """Closest flat-roof material family to an aerial colour.

    @param rgb: (r, g, b) 0..255
    @returns: material family name for ``roofs[label].material``
    """
    return min(FLAT_ROOF_PALETTE, key=lambda t: sum((a - b) ** 2 for a, b in zip(t[1], rgb)))[0]


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
    @returns: ``{px_per_block, rotation_deg, shift, roof_rgb, material, pv_share, pv_arrays}``
    @raises ValueError: on an aerial too coarse to resolve PV rows, or an empty footprint
    """
    A = np.asarray(A, float)
    rgb = np.asarray(rgb, float)
    ppb = float(math.hypot(A[0, 0], A[1, 0]))
    if ppb < MIN_PX_PER_BLOCK:
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
        if np.isnan(win).any():
            continue
        # Stripes parallel to x (rows along x) vary down the window: profile over z.
        score_rx[(x, z)], lag_rx[(x, z)] = period_score(win.mean(axis=1), lo, hi)
        score_rz[(x, z)], lag_rz[(x, z)] = period_score(win.mean(axis=0), lo, hi)

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
        arrays.append({
            'box': [x0, z0, x1, z1], 'rows': rows, 'pitch': pitch,
            'source': f'aerial PV array, rows along {rows}, row pitch {row_pitch:.1f} m, '
                      f'periodicity {score:.2f}{note}',
            'row_pitch_m': round(row_pitch, 2), 'score': round(score, 2), 'cells': len(cells),
        })
        in_arrays |= inside(box)
    arrays.sort(key=lambda a: (a['box'][1], a['box'][0]))

    roof = membrane_colour(colour[c] for c in colour if c not in in_arrays)
    roof_rgb = list(roof) if roof else None
    return {
        'px_per_block': round(ppb, 3),
        'rotation_deg': round(float(math.degrees(math.atan2(A[1, 0], A[0, 0]))), 2),
        'shift': list(shift),
        'roof_rgb': roof_rgb,
        'material': nearest_roof_material(roof_rgb) if roof_rgb else None,
        'pv_share': round(len(in_arrays) / len(footprint), 3),
        'pv_arrays': arrays,
    }


def patch_spec(path, label: str, derived: dict) -> None:
    """Write derived ``material`` and ``pv_arrays`` into ``roofs[label]``; other roof keys stay.

    @param path: feature spec JSON
    @param label: roof label
    @param derived: ``derive`` result
    @raises SystemExit: when the spec has no ``roofs[label]``
    """
    path = Path(path)
    spec = json.loads(path.read_text())
    roof = (spec.get('roofs') or {}).get(label)
    if not isinstance(roof, dict):
        raise SystemExit(f'{path}: no roofs[{label}]')
    if derived.get('material'):
        roof['material'] = derived['material']
    roof['pv_arrays'] = [
        {k: a[k] for k in ('box', 'rows', 'pitch', 'source')} for a in derived['pv_arrays']
    ]
    path.write_text(json.dumps(spec, indent=1) + '\n')


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
    d.add_argument('--sheet', help='write a review PNG with arrays and rows drawn on')
    d.add_argument('--write', action='store_true', help='patch roofs[label] in --spec')
    args = ap.parse_args(argv)
    if args.spec and not args.label:
        ap.error('--spec needs --label')
    if args.write and not args.spec:
        ap.error('--write needs --spec')
    from PIL import Image
    rgb = np.asarray(Image.open(args.image).convert('RGB'))
    foot = _footprint(args)
    if not foot:
        raise SystemExit('no roof columns (label has no mapped way in the vector map?)')
    bbox = (min(c[0] for c in foot), min(c[1] for c in foot), max(c[0] for c in foot), max(c[1] for c in foot))
    if args.registration:
        from aerial_registered import load
        _, A = load(args.registration)
    else:
        A = _mosaic_affine(args.mosaic, bbox)
    shift = tuple(int(v) for v in args.shift.split(','))
    try:
        out = derive(rgb, A, foot, shift=shift, pitch=args.pitch, threshold=args.threshold,
                     source_note=f'aerial {Path(args.image).name}')
    except ValueError as exc:
        raise SystemExit(str(exc))
    if args.sheet:
        out['sheet'] = str(review_sheet(rgb, A, foot, out, args.sheet))
    print(json.dumps(out, indent=1))
    if args.write:
        patch_spec(args.spec, args.label, out)
        print(f'wrote roofs[{args.label}] material + {len(out["pv_arrays"])} pv_arrays to {args.spec}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
