#!/usr/bin/env python3
"""Read-only cell-boundary reports for layered-pass handoff.

  python3 scripts/cross_zone.py --world <world> report --cell 48,-44 [--margin 8]
"""
from __future__ import annotations

import argparse
import json
import sys

from cellmath import CELL, cell_bbox  # noqa: E402,F401
DEFAULT_MARGIN = 8


def neighbor_cells(tx: int, tz: int, *, chebyshev: int = 1) -> list[tuple[int, int]]:
    """Coverage cell indices within Chebyshev distance of ``(tx, tz)``.

    @param tx: cell X index
    @param tz: cell Z index
    @param chebyshev: distance (1 = 8-neighbour ring)
    @returns: sorted list including centre when chebyshev >= 0
    """
    out = []
    for dx in range(-chebyshev, chebyshev + 1):
        for dz in range(-chebyshev, chebyshev + 1):
            if max(abs(dx), abs(dz)) <= chebyshev:
                out.append((tx + dx, tz + dz))
    return sorted(out)


def expanded_cell_bbox(tx: int, tz: int, margin: int = 0) -> tuple[int, int, int, int]:
    """Inclusive MC bbox for a cell optionally padded by ``margin`` blocks.

    @param tx: cell X
    @param tz: cell Z
    @param margin: block padding on each side
    @returns: (x0, z0, x1, z1)
    """
    x0, z0, x1, z1 = cell_bbox(tx, tz, tx, tz)
    if margin:
        return x0 - margin, z0 - margin, x1 + margin, z1 + margin
    return x0, z0, x1, z1


def columns_outside_cell(columns: set[tuple[int, int]], tx: int, tz: int) -> set[tuple[int, int]]:
    """Subset of columns not in the cell bbox.

    @param columns: {(x, z)}
    @param tx: cell X
    @param tz: cell Z
    @returns: columns outside cell
    """
    x0, z0, x1, z1 = cell_bbox(tx, tz, tx, tz)
    return {(x, z) for x, z in columns if x < x0 or x > x1 or z < z0 or z > z1}


def cross_zone_report(
    tx: int,
    tz: int,
    *,
    margin: int = DEFAULT_MARGIN,
    plan_columns: set[tuple[int, int]] | None = None,
) -> dict:
    """Unified cross-zone warnings for handoff and foliage dry-run.

    @param tx: cell X
    @param tz: cell Z
    @param margin: foliage margin blocks
    @param plan_columns: optional planned {(x, z)} from a dry-run plan
    @returns: report dict with warnings, deferrals, and metadata
    """
    warnings: list[str] = []
    deferrals: list[str] = []

    if plan_columns:
        outside = columns_outside_cell(plan_columns, tx, tz)
        if outside:
            warnings.append(f'plan writes {len(outside)} columns outside cell {tx},{tz}')
            by_cell: dict[tuple[int, int], int] = {}
            for x, z in outside:
                ct = (x // CELL, z // CELL)
                by_cell[ct] = by_cell.get(ct, 0) + 1
            for (ntx, ntz), n in sorted(by_cell.items()):
                deferrals.append(f'foliage straddle: {n} cols → cell {ntx},{ntz}')

    ex0, ez0, ex1, ez1 = expanded_cell_bbox(tx, tz, margin)
    return {
        'cell': [tx, tz],
        'margin': margin,
        'expanded_bbox': [ex0, ez0, ex1, ez1],
        'neighbors': neighbor_cells(tx, tz),
        'warnings': warnings,
        'deferrals': deferrals,
    }


def format_report(report: dict) -> str:
    """Human-readable cross-zone report.

    @param report: ``cross_zone_report`` result
    @returns: multi-line text
    """
    lines = [
        f"CROSS-ZONE cell {report['cell'][0]},{report['cell'][1]} "
        f"margin={report['margin']}",
    ]
    for w in report['warnings']:
        lines.append(f'  warn: {w}')
    for d in report['deferrals']:
        lines.append(f'  defer: {d}')
    if not report['warnings'] and not report['deferrals']:
        lines.append('  (no cross-zone warnings)')
    return '\n'.join(lines)


def main() -> int:
    """CLI entry.

    @returns: exit code
    """
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='cmd', required=True)
    rep = sub.add_parser('report', help='print cross-zone report')
    rep.add_argument('--cell', required=True, help='tx,tz')
    rep.add_argument('--margin', type=int, default=DEFAULT_MARGIN)
    rep.add_argument('--json', action='store_true')
    args = ap.parse_args()
    tx, tz = (int(v.strip()) for v in args.cell.split(','))
    r = cross_zone_report(tx, tz, margin=args.margin)
    if args.json:
        print(json.dumps(r, indent=2))
    else:
        print(format_report(r))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
