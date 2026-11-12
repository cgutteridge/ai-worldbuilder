#!/usr/bin/env python3
"""Aerial frontage profile (#269): pavement width and forecourt depth per frontage, read-only.

For each frontage the ray walks from the road centreline foot toward the house, perpendicular to the
road, over registered-aerial samples. Colour classes: road (dark grey), pavement (pale grey), then the
forecourt classes paving, gravel, lawn, planting. Kerb = last road sample before the pavement run
(the vector-map centreline is not the kerb, painted centre can sit blocks away). Pavement = that pale-grey
run; forecourt = pavement edge to the first footprint column. Reads aerial_coverage samples and
footprints only; no ground change, no world write.

  python3 scripts/frontage_profile.py --world <name> --cell 53,-43 [--street "Kitchener Road"]
"""
from __future__ import annotations

import argparse
import math
import statistics
import sys
from collections import Counter, defaultdict

STEP = 0.5
MAX_REACH = 40.0
PAVE_STEP = 0.07     # pavement is at least this much paler (mean RGB) than the carriageway
MIN_RUN = 2          # samples (1 block) that confirm a pavement or forecourt run


def colour_class(rgb) -> str:
    """Class of one aerial sample, rgb in [0, 1]."""
    r, g, b = rgb
    mean = (r + g + b) / 3
    sat = max(rgb) - min(rgb)
    if g > r + .02 and g > b + .02:
        return 'planting' if mean < .3 else 'lawn'
    if r - g > .15 and r - b > .12:
        return 'paving'
    if sat > .06 and r >= b and mean > .45:
        return 'gravel'
    if sat <= .12 and mean < .4:
        return 'road'
    if sat <= .12:
        return 'pavement'
    return 'gravel'


def profile_frontage(sample, foot, centre, footprint, *, step=STEP, reach=MAX_REACH):
    """Walk foot -> house; sample(x, z) returns rgb or None, footprint is a set of columns.

    @returns dict(pavement, forecourt, surface, kerb, front, status) in blocks; widths None when unmeasurable
    """
    ax, az = centre[0] - foot[0], centre[1] - foot[1]
    n = math.hypot(ax, az)
    out = {'pavement': None, 'forecourt': None, 'surface': None, 'kerb': None, 'front': None, 'status': ''}
    if n < 1e-6:
        out['status'] = 'no direction'
        return out
    ax, az = ax / n, az / n
    classes, means, front, d = [], [], None, 0.0
    while d <= reach:
        x, z = foot[0] + ax * d, foot[1] + az * d
        if (math.floor(x), math.floor(z)) in footprint:
            front = d
            break
        c = sample(x, z)
        classes.append((d, colour_class(c) if c is not None else None))
        means.append(sum(c) / 3 if c is not None else None)
        d += step
    if front is None:
        out['status'] = 'no house'
        return out
    out['front'] = round(front, 2)
    # The aerial's asphalt can read as mid grey, so grey samples are re-split against the carriageway's
    # own brightness (median of the first 3 blocks): pavement must be a step paler than that.
    first = sorted(m for (dd, _c), m in zip(classes, means) if m is not None and dd < 3)
    if first:
        ref = first[len(first) // 2]
        classes = [(dd, 'pavement' if c in ('road', 'pavement') and m >= ref + PAVE_STEP else
                    'road' if c in ('road', 'pavement') else c) for (dd, c), m in zip(classes, means)]
    need = MIN_RUN
    # pavement run: first MIN_RUN consecutive pavement samples; kerb = start of that run
    start = next((i for i in range(len(classes) - need + 1)
                  if all(classes[i + k][1] == 'pavement' for k in range(need))), None)
    if start is None:
        out['status'] = 'no pavement'
        return out
    out['kerb'] = round(classes[start][0], 2)
    end = start
    while end < len(classes) and classes[end][1] == 'pavement':
        end += 1
    # tolerate single-sample gaps (shadow, bins)
    while end + 1 < len(classes) and classes[end + 1][1] == 'pavement' and classes[end][1] is not None:
        end += 1
        while end < len(classes) and classes[end][1] == 'pavement':
            end += 1
    edge = classes[end][0] if end < len(classes) else front
    out['pavement'] = round(edge - classes[start][0], 2)
    out['forecourt'] = round(max(front - edge, 0.0), 2)
    forecourt = [c for d, c in classes[end:] if c and c != 'road']
    out['surface'] = Counter(forecourt).most_common(1)[0][0] if forecourt else None
    out['status'] = 'ok'
    return out


def side(foot, centre) -> str:
    dx, dz = centre[0] - foot[0], centre[1] - foot[1]
    if abs(dz) >= abs(dx):
        return 'south' if dz > 0 else 'north'
    return 'east' if dx > 0 else 'west'


def summarise(rows) -> list:
    """Per (street, side) run: n, median and range of pavement and forecourt, dominant surface."""
    runs = defaultdict(list)
    for r in rows:
        runs[(r['street'], r['side'])].append(r)
    out = []
    for (street, sd), rs in sorted(runs.items()):
        ok = [r for r in rs if r.get('status') == 'ok']

        def stat(key):
            v = [r[key] for r in ok]
            return ({'median': round(statistics.median(v), 2), 'min': min(v), 'max': max(v)} if v else None)
        surf = Counter(r['surface'] for r in ok if r['surface'])
        out.append({'street': street, 'side': sd, 'frontages': len(rs), 'measured': len(ok),
                    'pavement': stat('pavement'), 'forecourt': stat('forecourt'),
                    'surface': surf.most_common(1)[0][0] if surf else None,
                    'surfaces': dict(surf)})
    return out


def cell_rows(cell, street_filter=''):
    import aerial_coverage as aerial
    from ground_sources import cell_box
    from garden_sources import plots_for

    box = cell_box(cell)
    owner, info, _street, idx = plots_for(box)
    fp = idx.cells()
    rgb = aerial.sample_rgb(cell, margin=int(MAX_REACH))
    sample = lambda x, z: rgb.get((math.floor(x), math.floor(z)))
    x0, z0, x1, z1 = box
    rows = []
    for i, meta in sorted(info.items(), key=lambda kv: (kv[1]['street'], str(kv[1]['number']))):
        cx, cz = meta['centre']
        if not (x0 <= cx <= x1 and z0 <= cz <= z1) or not meta['foot']:
            continue
        if street_filter and meta['street'] != street_filter:
            continue
        own = {q for q, k in fp.items() if k == i}
        p = profile_frontage(sample, meta['foot'], meta['centre'], own)
        rows.append({'street': meta['street'], 'number': meta['number'],
                     'side': side(meta['foot'], meta['centre']), **p})
    return rows


def fmt(s):
    return 'none' if not s else f"{s['median']} ({s['min']}-{s['max']})"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cell', required=True)
    ap.add_argument('--street', default='')
    ap.add_argument('--each', action='store_true', help='also print every frontage')
    a = ap.parse_args()
    cell = tuple(int(v) for v in a.cell.split(','))
    rows = cell_rows(cell, a.street)
    if a.each:
        for r in rows:
            print(r['street'], r['number'], r['side'], r['status'], r['pavement'], r['forecourt'], r['surface'])
    print('street / side: n measured | pavement median (range) | forecourt median (range) | surface')
    for s in summarise(rows):
        print(f"{s['street']} {s['side']}: {s['frontages']} {s['measured']} | {fmt(s['pavement'])} | "
              f"{fmt(s['forecourt'])} | {s['surface']} {s['surfaces']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
