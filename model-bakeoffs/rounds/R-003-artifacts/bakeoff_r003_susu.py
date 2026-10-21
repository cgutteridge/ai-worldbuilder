#!/usr/bin/env python3
"""SUSU / Building 42, bake-off round R-003 (claude-code, claude-haiku-5-5, cold). Cell (48, -44).

Evidence table (real sources only; no earlier Minecraft build used):

| Fact | Source | Value |
| --- | --- | --- |
| Footprint | vector-map way 33830305, "University of Southampton Students' Union (SUSU)", loc_ref 42 | 3924 raster cells, bbox MC 6142,-5582 .. 6226,-5492 |
| Neighbours | vector-map ways 33830236 (B57 retail, flat), 34391673 (West Bldg), 391790374 (roof) | kept out of this footprint |
| Ground | lidar_heightmap composite, lump 1 | ground_y 50 |
| Eave / ridge | lidar_heightmap composite, lump 1 | eave_y 53 (10th pct), ridge_y 62 |
| Storeys | lidar estimate (ridge - ground - base) / 3 | 3 |
| Storeys (OSM) | building:levels | 4 (recorded as a conflict; height follows lidar, priority 1) |
| Roof shape | lidar pitch flag | pitched on the lump, NOT used: aerial read shows a flat dark membrane on the SUSU roof |
| Roof greeblies | aerial z19 at_6184_-5537 (look-only, Google tiles) | one large pale rectangular plant / rooflight near west-centre; dark stepped block at south-west edge; no pitched tiles on this roof |
| Street openings | none local for way 33830305 (no street photo read this sitting) | generic storey rhythm, flagged as unverified |

Massing: squared footprint (closing then opening, 3x3), one 3-storey box, flat roof.
Pad line is printed on every run; no fixed halo.

  python3 scripts/bakeoff_r003_susu.py                 # dry run (prints pad line, preview)
  SITE_OWNER=bakeoff-R-003-haiku python3 scripts/bakeoff_r003_susu.py --apply
"""
from __future__ import annotations

import argparse

import numpy as np

import feature_build as fb
import osm_footprints
from script_env import ROOT

WAY = '33830305'
SPEC = ROOT / 'site' / 'builds' / 'bakeoff_r003_susu.json'


def _shift_or(m: np.ndarray, op) -> np.ndarray:
    """Apply a 3x3 square neighbourhood reduction (np.logical_or / np.logical_and) to a boolean mask."""
    p = np.pad(m, 1, mode='constant', constant_values=False)
    out = m.copy()
    h, w = m.shape
    for dx in (0, 1, 2):
        for dz in (0, 1, 2):
            out = op(out, p[dx:dx + h, dz:dz + w])
    return out


def _closing(m):
    """Dilate then erode: fills one-block notches on the outline."""
    return _shift_or(_shift_or(m, np.logical_or), np.logical_and)


def _opening(m):
    """Erode then dilate: removes one-block spurs on the outline."""
    return _shift_or(_shift_or(m, np.logical_and), np.logical_or)


def square_footprint(cells: set) -> tuple[set, dict]:
    """Square the outline (one-block stair-steps) without a fixed halo.

    @param cells: OSM footprint raster cells {(x, z)}
    @returns: (squared cells, stats dict with osm, readable, pad, cover)
    """
    xs = [x for x, _ in cells]; zs = [z for _, z in cells]
    x0, z0 = min(xs) - 2, min(zs) - 2
    h, w = max(xs) - x0 + 3, max(zs) - z0 + 3
    m = np.zeros((h, w), dtype=bool)
    for x, z in cells:
        m[x - x0, z - z0] = True
    sq = _opening(_closing(m))
    readable = {(int(i) + x0, int(j) + z0) for i, j in zip(*np.nonzero(sq))}
    osm_n = len(cells)
    stats = {
        'osm': osm_n,
        'readable': len(readable),
        'pad': len(readable) - osm_n,
        'cover': round(100.0 * len(readable & cells) / osm_n, 1),
    }
    return readable, stats


def osm_way_cells() -> set:
    """Raster cells of footprint way WAY (centre-inside test, as feature_build uses)."""
    idx = osm_footprints.OSMIndex()
    out = set()
    for (x, z), i in idx.cells().items():
        p = idx.buildings[i][1]
        if str(p.get('osm_way_id') or p.get('osm_id')) == WAY:
            out.add((x, z))
    if not out:
        raise SystemExit(f'way {WAY} not found in vector-map footprints')
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--apply', action='store_true', help='LIVE: needs SITE_OWNER + claimed lock')
    a = ap.parse_args(argv)
    osm = osm_way_cells()
    squared, s = square_footprint(osm)
    print(f"footprint osm={s['osm']} readable={s['readable']} pad={s['pad']} cover={s['cover']}%")
    fb.run(SPEC, do_apply=a.apply, footprints={'SU': squared})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
