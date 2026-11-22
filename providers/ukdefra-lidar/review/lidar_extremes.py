#!/usr/bin/env python3
"""Highs and lows of DTM/DSM over an MC or OSGB bbox (for Y-offset planning).

Talk through the result with the user before locking ``y_offset`` in
``config/world-frame.json`` (see skill ``new-world-build``). Bad offset is painful.

  .venv/bin/python shims/lidar_extremes.py --bbox-mc 0,-2000,8000,0
  .venv/bin/python shims/lidar_extremes.py --cell 48,-44
  .venv/bin/python shims/lidar_extremes.py --bbox-osgb 436248,107608,444248,109608
  .venv/bin/python shims/lidar_extremes.py --bbox-mc … --fetch   # ensure DEFRA tiles first

Reports min/max metres AOD for DTM and DSM, and the Minecraft Y those map to
with the current world-frame ``y_offset`` (and optionally a proposed offset).
"""
from __future__ import annotations

import argparse
import json
import math
import sys

from script_env import ROOT  # noqa: E402

import cellmath  # noqa: E402
from defra_lidar import DefraLidar, mc_box_en, mc_heights  # noqa: E402
from world_frame import load_frame, metres_aod_to_y, osgb_to_mc  # noqa: E402


def _bbox_mc(args: argparse.Namespace) -> tuple[int, int, int, int]:
    if args.cell:
        return cellmath.bounds(cellmath.parse_cell(args.cell), args.margin)
    if args.bbox_mc:
        parts = [int(p) for p in args.bbox_mc.split(',')]
        if len(parts) != 4:
            raise SystemExit('--bbox-mc wants x0,z0,x1,z1')
        x0, z0, x1, z1 = parts
        return min(x0, x1), min(z0, z1), max(x0, x1), max(z0, z1)
    if args.bbox_osgb:
        parts = [float(p) for p in args.bbox_osgb.split(',')]
        if len(parts) != 4:
            raise SystemExit('--bbox-osgb wants e0,n0,e1,n1')
        e0, n0, e1, n1 = parts
        corners = [
            osgb_to_mc(e0, n0), osgb_to_mc(e1, n0),
            osgb_to_mc(e0, n1), osgb_to_mc(e1, n1),
        ]
        xs = [c[0] for c in corners]
        zs = [c[1] for c in corners]
        return min(xs), min(zs), max(xs), max(zs)
    raise SystemExit('need --cell, --bbox-mc, or --bbox-osgb')


def suggest_y_offset(ground_min: float, top_max: float) -> dict:
    """Fit ground and surface into the build range with room below and above.

    @param ground_min: lowest DTM height in metres above datum
    @param top_max: highest DSM height in metres above datum
    @returns: integer suggestion (or None), feasible interval and explanation
    """
    # Leave 14 blocks below the lowest ground and four above the highest surface.
    ground_floor, top_ceiling = -50, 315
    lower = math.ceil(ground_floor - ground_min)
    upper = math.floor(top_ceiling - top_max)
    fits = lower <= upper
    suggested = min(max(math.floor(64 - ground_min), lower), upper) if fits else None
    return {
        'suggested_y_offset': suggested,
        'offset_min': lower,
        'offset_max': upper,
        'ground_floor': ground_floor,
        'top_ceiling': top_ceiling,
        'reason': (
            f'Integer y_offset fits {lower}..{upper} '
            '(14 blocks below ground and four above the surface).'
            if fits else
            f'No integer y_offset fits this relief '
            f'(needed {lower}..{upper} with 14 blocks below ground and four '
            'above the surface); shrink the survey area or explicitly reconsider the margins.'
        ),
    }


def extremes(
    x0: int, z0: int, x1: int, z1: int, *, fetch: bool = False,
) -> dict:
    """Compute DTM/DSM min/max over an inclusive MC bbox.

    @param x0: min X
    @param z0: min Z
    @param x1: max X
    @param z1: max Z
    @param fetch: ensure DEFRA tiles covering the box
    @returns: summary dict
    """
    lidar = DefraLidar()
    if fetch:
        ea, na, eb, nb = mc_box_en(x0, z0, x1, z1)
        lidar.ensure(ea, na, eb, nb, models=('DTM', 'DSM'))
    h = mc_heights(lidar, x0, z0, x1, z1)
    f = load_frame()
    dtm, dsm = h['dtm'], h['dsm']
    import numpy as np
    valid = np.isfinite(dtm) & np.isfinite(dsm)
    if not valid.any():
        raise SystemExit('no finite lidar samples in bbox (fetch tiles? pass --fetch)')
    dtm_v, dsm_v = dtm[valid], dsm[valid]
    out = {
        'bbox_mc': [x0, z0, x1, z1],
        'columns': int(valid.sum()),
        'y_offset': f.y_offset,
        'dtm_m': {
            'min': float(dtm_v.min()),
            'max': float(dtm_v.max()),
            'mean': float(dtm_v.mean()),
        },
        'dsm_m': {
            'min': float(dsm_v.min()),
            'max': float(dsm_v.max()),
            'mean': float(dsm_v.mean()),
        },
        'mc_y_with_current_offset': {
            'ground_min': metres_aod_to_y(float(dtm_v.min()), f),
            'ground_max': metres_aod_to_y(float(dtm_v.max()), f),
            'top_min': metres_aod_to_y(float(dsm_v.min()), f),
            'top_max': metres_aod_to_y(float(dsm_v.max()), f),
        },
        'vanilla_overworld_y': {'min': -64, 'max': 319},
    }
    out['y_offset_fit'] = suggest_y_offset(out['dtm_m']['min'], out['dsm_m']['max'])
    fit = out['y_offset_fit']
    out['suggested_y_offset'] = fit['suggested_y_offset']
    out['note'] = (
        f"Fitting y_offset integers: {fit['offset_min']}..{fit['offset_max']}. "
        'Recommend surveying ≥ 5 km × 5 km.'
    )
    return out


def main(argv: list[str] | None = None) -> int:
    """CLI entry.

    @param argv: optional argv
    @returns: exit code
    """
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--cell', help='coverage cell tx,tz')
    g.add_argument('--bbox-mc', help='x0,z0,x1,z1 inclusive')
    g.add_argument('--bbox-osgb', help='e0,n0,e1,n1')
    ap.add_argument('--margin', type=int, default=0, help='grow --cell by N blocks')
    ap.add_argument('--fetch', action='store_true', help='ensure DEFRA DTM+DSM tiles first')
    ap.add_argument('--json', action='store_true', help='print JSON only')
    args = ap.parse_args(argv)
    x0, z0, x1, z1 = _bbox_mc(args)
    rec = extremes(x0, z0, x1, z1, fetch=args.fetch)
    if args.json:
        print(json.dumps(rec, indent=2))
        return 0
    print(f"bbox_mc {rec['bbox_mc']}  columns {rec['columns']}  y_offset {rec['y_offset']}")
    print(f"DTM m  min={rec['dtm_m']['min']:.2f}  max={rec['dtm_m']['max']:.2f}  mean={rec['dtm_m']['mean']:.2f}")
    print(f"DSM m  min={rec['dsm_m']['min']:.2f}  max={rec['dsm_m']['max']:.2f}  mean={rec['dsm_m']['mean']:.2f}")
    y = rec['mc_y_with_current_offset']
    print(f"MC Y (current offset): ground {y['ground_min']}…{y['ground_max']}  tops {y['top_min']}…{y['top_max']}")
    fit = rec['y_offset_fit']
    print(
        f"suggested y_offset: {fit['suggested_y_offset']}  "
        f"(fitting range {fit['offset_min']}..{fit['offset_max']})"
    )
    print(fit['reason'])
    print(rec['note'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
