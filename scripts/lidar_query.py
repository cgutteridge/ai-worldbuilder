#!/usr/bin/env python3
"""Ask for DEFRA LIDAR heights (DTM = ground, DSM = surface, or their difference) over a cell or box.

One command, one answer. Pick the area, the layers, and how much text you want back:

  python3 scripts/lidar_query.py --cell 48,-44                          # summary (default): stats, no big arrays
  python3 scripts/lidar_query.py --cell 48,-44 --format grid            # readable map of dtm + dsm, north at the top
  python3 scripts/lidar_query.py --cell 48,-44 --layers dtm,dsm,diff    # bare earth, surface, standing height (walls etc.)
  python3 scripts/lidar_query.py --bbox-mc 6140,-5640,6180,-5600 --layers diff --format grid --step 1
  python3 scripts/lidar_query.py --bbox-mc 6140,-5640,6180,-5600 --format json --layers dtm,dsm
  python3 scripts/lidar_query.py --bbox-osgb 442300,115200,442400,115300 --format json   # any place, no Minecraft frame
  python3 scripts/lidar_query.py --cell 48,-44 --format png             # side-by-side colour maps (file path printed)
  python3 scripts/lidar_query.py --cell 48,-44 --format npz --out /tmp/c.npz   # numpy arrays + metadata
  python3 scripts/lidar_query.py --cell 48,-44 --fetch                  # download missing tiles first (~75 MB a tile and model)

Area (one of): --cell tx,tz | --bbox-mc x0,z0,x1,z1 (inclusive blocks) | --bbox-osgb e0,n0,e1,n1 (metres, EPSG:27700).
  --margin N grows a --cell by N blocks.

Layers (comma list; default dtm,dsm):
  dtm        ground height, metres above sea level (bare earth, buildings and trees removed)
  dsm        surface height, metres (roofs, treetops; last-return composite)
  dsm_first  surface height from first returns (treetops and roofs, no see-through gaps)
  canopy     first-return DSM minus last-return DSM, at least 0: how far the laser reached through the top surface.
             ~0 on roofs, walls, bare ground; 1.5 m or more is foliage (defra_world uses that to find trees)
  diff       DSM − DTM, at least 0: standing height (buildings, walls, hedges, trees). Prefer this name when hunting
             walls and short solid objects. Alias: feature (Geocraft's name for the same quantity).
  feature    same as diff (kept for older scripts)
  ground_y   Minecraft block Y of the ground = SEA_LEVEL + int(dtm)           (--bbox-mc / --cell only)
  top_y      Minecraft block Y of the top  = ground_y + diff                 (--bbox-mc / --cell only)

Unfiltered DEFRA DSM is noisier than Geocraft's filtered heightmap: ignore cars/trains; keep street furniture
where practical (rules/geocraft-accuracy.md, site/GEOCRAFT.txt §1a).

Formats:
  summary    JSON: area in both frames, per-layer min/median/mean/max, no-data count, tile status. Small. Start here.
  grid       text map, one row per Z (north at top, since north is -Z) and one column per X (east to the right), headed
             by the X and Z of each axis. Downsampled to fit; pass --step 1 for every metre.
  json       {"x0","z0"|"e0","n0", "rows","cols","step","layers":{name: [[row of values]...]}}  rows[r][c]:
             r = z (or northing, descending) index, c = x (or easting) index, both counted from the area's origin corner.
  npz        numpy file: one [row, col] array per layer plus x0, z0 (or e0, n0), step; orientation as json.
  png        colour map per layer side by side (low = blue, high = red, scaled per layer).

Values: Geocraft whole-metre rounding by default, so ground_y/top_y equal what the Southampton2023 world holds; --float
gives true float heights and null for no data. Text output is capped at --max-cells values per layer (default 4096) by
choosing a step; the answer always says which step it used.

Python:  import lidar_query as q;  r = q.query(area=('cell', (48, -44)), layers=('dtm', 'dsm'))   # r['layers'][name] is [row, col]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

import cellmath
import defra_lidar as dl
from script_env import ROOT, make_parser  # noqa: E402

LAYERS = ('dtm', 'dsm', 'dsm_first', 'canopy', 'diff', 'feature', 'ground_y', 'top_y')
# diff and feature are the same array (DSM − DTM); both names stay so old scripts and wall-hunting docs work.
STANDING = frozenset({'diff', 'feature'})
MC_ONLY = ('ground_y', 'top_y')
UNITS = {'dtm': 'm', 'dsm': 'm', 'dsm_first': 'm', 'canopy': 'm', 'diff': 'm', 'feature': 'm',
         'ground_y': 'block Y', 'top_y': 'block Y'}
OUT_DIR = ROOT / 'verification' / 'site' / 'defra'
MAX_CELLS = 4096


def resolve_area(area: tuple[str, object], margin: int = 0) -> dict:
    """Normalise ('cell', (tx, tz)) | ('mc', (x0, z0, x1, z1)) | ('osgb', (e0, n0, e1, n1)) to both frames.

    @returns: {'frame': 'mc'|'osgb', 'mc': [x0, z0, x1, z1] | None, 'osgb': [e0, n0, e1, n1]} (inclusive, south-west to north-east)
    """
    kind, v = area
    if kind == 'cell':
        kind, v = 'mc', cellmath.bounds(tuple(v), margin)
    if kind == 'mc':
        x0, z0, x1, z1 = (int(a) for a in v)
        x0, x1, z0, z1 = min(x0, x1), max(x0, x1), min(z0, z1), max(z0, z1)
        e0, n0, e1, n1 = dl.mc_box_en(x0, z0, x1, z1)
        return {'frame': 'mc', 'mc': [x0, z0, x1, z1], 'osgb': [int(e0), int(n0), int(e1), int(n1)]}
    if kind == 'osgb':
        e0, n0, e1, n1 = (int(a) for a in v)
        return {'frame': 'osgb', 'mc': None,
                'osgb': [min(e0, e1), min(n0, n1), max(e0, e1), max(n0, n1)]}
    raise ValueError(f'unknown area kind {kind!r}')


def _grids(lidar: dl.DefraLidar, a: dict, layers: tuple[str, ...], geocraft: bool) -> dict:
    """Arrays [row, col] per layer. Rows run north to south; columns west to east."""
    out = {}
    want_standing = bool(STANDING & set(layers))
    if a['frame'] == 'mc':
        x0, z0, x1, z1 = a['mc']
        h = dl.mc_heights(lidar, x0, z0, x1, z1, geocraft=geocraft)
        base = {k: np.asarray(v).T for k, v in h.items()}      # [x, z] -> [row=z, col=x]; z ascending = north first
        if want_standing:
            base['diff'] = base['feature']
        if {'dsm_first', 'canopy'} & set(layers):
            xs, zs = np.meshgrid(np.arange(x0, x1 + 1), np.arange(z0, z1 + 1))
            from world_frame import mc_grid_to_en
            e, n = mc_grid_to_en(xs, zs)
            base['dsm_first'] = lidar.sample('DSM_FIRST', e, n, geocraft)
            base['canopy'] = np.maximum(base['dsm_first'] - base['dsm'], 0)
    else:
        e0, n0, e1, n1 = a['osgb']
        es, ns = np.meshgrid(np.arange(e0, e1 + 1), np.arange(n1, n0 - 1, -1))
        base = {'dtm': lidar.sample('DTM', es, ns, geocraft), 'dsm': lidar.sample('DSM', es, ns, geocraft)}
        if {'dsm_first', 'canopy'} & set(layers):
            base['dsm_first'] = lidar.sample('DSM_FIRST', es, ns, geocraft)
            base['canopy'] = np.maximum(base['dsm_first'] - base['dsm'], 0)
        if want_standing:
            standing = np.maximum(np.trunc(base['dsm'] - base['dtm']), 0)
            base['feature'] = standing
            base['diff'] = standing
    for name in layers:
        out[name] = np.asarray(base[name])
    return out


def _models(layers: tuple[str, ...]) -> tuple[str, ...]:
    need = {'DTM'}
    if set(layers) - {'dtm'}:
        need.add('DSM')
    if {'dsm_first', 'canopy'} & set(layers):
        need.update(('DSM', 'DSM_FIRST'))
    return tuple(sorted(need))


def _stats(a: np.ndarray) -> dict:
    ok = a[np.isfinite(a)]
    if not ok.size:
        return {'min': None, 'median': None, 'mean': None, 'max': None}
    return {'min': _num(ok.min()), 'median': _num(np.median(ok)), 'mean': round(float(ok.mean()), 2), 'max': _num(ok.max())}


def _num(v):
    v = float(v)
    return int(v) if v == int(v) else round(v, 2)


def _auto_step(rows: int, cols: int, max_cells: int) -> int:
    step = 1
    while max_cells and -(-rows // step) * -(-cols // step) > max_cells:
        step += 1
    return step


def query(area: tuple[str, object], layers: tuple[str, ...] = ('dtm', 'dsm'), margin: int = 0, geocraft: bool = True,
          fetch: bool = False, step: int | None = None, max_cells: int = MAX_CELLS,
          lidar: dl.DefraLidar | None = None) -> dict:
    """Heights for an area as arrays plus a description.

    @param area: ('cell', (tx, tz)) | ('mc', (x0, z0, x1, z1)) | ('osgb', (e0, n0, e1, n1))
    @param layers: names from LAYERS
    @param geocraft: Geocraft whole-metre rounding (matches the Southampton2023 world); False = float, NaN for no data
    @param fetch: download missing tiles (else LookupError names what to fetch)
    @param step: keep every Nth row and column (None = automatic so each layer has at most ``max_cells`` values; 1 = all)
    @returns: {'area', 'layers': {name: ndarray[row, col]}, 'step', 'rows', 'cols', 'units', 'no_data_columns', 'notes'}
    """
    layers = tuple(layers)
    bad = [n for n in layers if n not in LAYERS]
    if bad:
        raise ValueError(f'unknown layer(s) {bad}; choose from {list(LAYERS)}')
    a = resolve_area(area, margin)
    if a['frame'] == 'osgb' and set(layers) & set(MC_ONLY):
        raise ValueError(f'{sorted(set(layers) & set(MC_ONLY))} need the Minecraft frame: use --cell or --bbox-mc')
    lidar = lidar or dl.DefraLidar(download=fetch)
    models = _models(layers)
    absent = lidar.ensure(*a['osgb'], models=models)                # LookupError when not cached and fetch is off
    full = _grids(lidar, a, layers, geocraft)
    raw = _grids(lidar, a, ('dtm',), False)['dtm'] if geocraft else full.get('dtm')
    if raw is None:
        raw = _grids(lidar, a, ('dtm',), False)['dtm']
    nodata = int(np.isnan(raw).sum())
    rows, cols = next(iter(full.values())).shape
    st = step or _auto_step(rows, cols, max_cells)
    notes = []
    if st > 1:
        notes.append(f'every {st}th row and column kept ({rows}x{cols} -> {-(-rows // st)}x{-(-cols // st)}); pass --step 1 for all')
    if nodata:
        notes.append(f'{nodata} of {raw.size} columns have no LIDAR (sea, or outside England); '
                     + ('they read 0 in Geocraft mode' if geocraft else 'they are null'))
    if absent:
        notes.append(f'DEFRA publishes no data for: {absent}')
    return {'area': a, 'layers': full, 'step': st, 'rows': rows, 'cols': cols,
            'units': {n: UNITS[n] for n in layers}, 'no_data_columns': nodata, 'geocraft_rounding': geocraft,
            'crs': dl.DEFRA_CRS, 'notes': notes}


def _origin(r: dict) -> dict:
    a = r['area']
    return {'x0': a['mc'][0], 'z0': a['mc'][1]} if a['frame'] == 'mc' else {'e0': a['osgb'][0], 'n0': a['osgb'][3]}


def _thin(arr: np.ndarray, step: int) -> np.ndarray:
    return arr[::step, ::step]


def _cell_json(v):
    return None if not np.isfinite(v) else _num(v)


def format_summary(r: dict) -> dict:
    a = r['area']
    out = {'area': a, 'size': {'rows': r['rows'], 'cols': r['cols'], 'metres_per_value': 1},
           'crs': r['crs'], 'units': r['units'], 'geocraft_rounding': r['geocraft_rounding'],
           'no_data_columns': r['no_data_columns'],
           'layers': {n: _stats(v) for n, v in r['layers'].items()}, 'notes': r['notes']}
    standing = r['layers'].get('diff', r['layers'].get('feature'))
    if standing is not None:
        out['diff_share'] = {f'>={k}m': round(float((standing >= k).mean()), 3) for k in (1, 3, 6, 10)}
        if 'feature' in r['layers']:
            out['feature_share'] = out['diff_share']          # older readers
    out['next'] = ('--format grid for a readable map, --format json for values, --format png to look at it; '
                   '--layers dtm,dsm,diff,ground_y,top_y  (diff = DSM−DTM standing height)')
    return out


def format_grid(r: dict) -> str:
    a, st = r['area'], r['step']
    lines = []
    if a['frame'] == 'mc':
        x0, z0, x1, z1 = a['mc']
        lines.append(f'LIDAR x {x0}..{x1}  z {z0}..{z1}  step {st}  (columns east ->, rows south down; north is -Z, at the top)')
        xs = list(range(x0, x1 + 1, st))
        ys = list(range(z0, z1 + 1, st))
        rl, cl = 'z', 'x'
    else:
        e0, n0, e1, n1 = a['osgb']
        lines.append(f'LIDAR E {e0}..{e1}  N {n0}..{n1}  step {st}  (columns east ->, rows south down; north at the top)')
        xs = list(range(e0, e1 + 1, st))
        ys = list(range(n1, n0 - 1, -st))
        rl, cl = 'N', 'E'
    for name, arr in r['layers'].items():
        t = _thin(arr, st)
        w = max(3, max(len(str(_cell_json(v))) for v in t.ravel()) if t.size else 3)
        lines += ['', f'{name} ({r["units"][name]})', f'{rl}\\{cl}'.ljust(8) + ' '.join(str(x).rjust(w) for x in xs)]
        for y, row in zip(ys, t):
            lines.append(str(y).ljust(8) + ' '.join(('.' if not np.isfinite(v) else str(_cell_json(v))).rjust(w) for v in row))
    if r['notes']:
        lines += [''] + [f'note: {n}' for n in r['notes']]
    return '\n'.join(lines)


def format_json(r: dict) -> str:
    st = r['step']
    body = {**_origin(r), 'frame': r['area']['frame'], 'crs': r['crs'], 'step': st, 'units': r['units'],
            'rows': -(-r['rows'] // st), 'cols': -(-r['cols'] // st),
            'layout': 'layers[name][row][col]; rows run north to south, cols west to east, from the origin corner',
            'notes': r['notes'],
            'layers': {n: [[_cell_json(v) for v in row] for row in _thin(arr, st)] for n, arr in r['layers'].items()}}
    return json.dumps(body, separators=(',', ':'))


def save_npz(r: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    st = r['step']
    np.savez_compressed(path, step=st, frame=r['area']['frame'], crs=r['crs'], **_origin(r),
                        **{n: _thin(a, st) for n, a in r['layers'].items()})
    return path


def save_png(r: dict, path: Path, target: int = 256) -> Path:
    """Colour map of each layer (low blue, high red, scaled per layer) side by side; one value = at least ``target / cols`` px."""
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    tiles = []
    for arr in r['layers'].values():
        t = _thin(arr, r['step']).astype(np.float64)
        ok = np.isfinite(t)
        lo, hi = (t[ok].min(), t[ok].max()) if ok.any() else (0.0, 1.0)
        f = np.where(ok, (t - lo) / (hi - lo if hi > lo else 1.0), 0.0)
        rgb = np.stack([f, 1 - np.abs(2 * f - 1), 1 - f], axis=-1)
        rgb[~ok] = 0.5
        img = Image.fromarray((rgb * 255).astype(np.uint8))
        k = max(1, target // max(1, img.width))
        tiles.append(img.resize((img.width * k, img.height * k), Image.NEAREST))
    w = sum(t.width for t in tiles) + 4 * (len(tiles) - 1)
    sheet = Image.new('RGB', (w, max(t.height for t in tiles)), (255, 255, 255))
    x = 0
    for t in tiles:
        sheet.paste(t, (x, 0))
        x += t.width + 4
    sheet.save(path)
    return path


def _area_label(a: dict) -> str:
    return '_'.join(str(v) for v in (a['mc'] if a['frame'] == 'mc' else a['osgb']))


def main(argv: list[str] | None = None) -> int:
    ap = make_parser(__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--cell', help='coverage cell tx,tz')
    g.add_argument('--bbox-mc', help='x0,z0,x1,z1 inclusive blocks')
    g.add_argument('--bbox-osgb', help='e0,n0,e1,n1 metres, EPSG:27700')
    ap.add_argument('--margin', type=int, default=0, help='grow --cell by this many blocks')
    ap.add_argument('--layers', default='dtm,dsm', help=f'comma list of {",".join(LAYERS)}')
    ap.add_argument('--format', default='summary', choices=('summary', 'grid', 'json', 'npz', 'png'))
    ap.add_argument('--step', type=int, help='keep every Nth value (default: automatic cap, 1 = all)')
    ap.add_argument('--max-cells', type=int, default=MAX_CELLS, help='cap per layer when --step is automatic; 0 = no cap')
    ap.add_argument('--float', action='store_true', help='true float heights and null for no data (not Geocraft rounding)')
    ap.add_argument('--fetch', action='store_true', help='download tiles that are not cached yet')
    ap.add_argument('--out', type=Path, help='file for npz / png (default verification/site/defra/)')
    a = ap.parse_args(argv)
    spec = (('cell', cellmath.parse_cell(a.cell)) if a.cell else
            ('mc', tuple(int(v) for v in a.bbox_mc.split(','))) if a.bbox_mc else
            ('osgb', tuple(int(v) for v in a.bbox_osgb.split(','))))
    try:
        r = query(spec, tuple(s.strip() for s in a.layers.split(',') if s.strip()), a.margin, not a.float, a.fetch,
                  1 if a.format == 'summary' else a.step, a.max_cells)
    except LookupError as exc:
        print(f'{exc}\nrun again with --fetch (downloads ~75 MB per tile and model), or: python3 scripts/defra_lidar.py fetch --cell tx,tz',
              file=sys.stderr)
        return 1
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    label = _area_label(r['area'])
    if a.format == 'summary':
        body = format_summary(r)                     # one line per key: compact, still easy to read
        print('{\n' + ',\n'.join(f' {json.dumps(k)}: {json.dumps(v)}' for k, v in body.items()) + '\n}')
    elif a.format == 'grid':
        print(format_grid(r))
    elif a.format == 'json':
        print(format_json(r))
    elif a.format == 'npz':
        print(save_npz(r, a.out or OUT_DIR / f'{label}.npz'))
    else:
        print(save_png(r, a.out or OUT_DIR / f'{label}.png'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
