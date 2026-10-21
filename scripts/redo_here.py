#!/usr/bin/env python3
"""Identify what a player-standing redo would touch at a column.

  .venv/bin/python scripts/redo_here.py --at 6240,-5664
  .venv/bin/python scripts/redo_here.py --at 6240,-5664 --y 55 --json

Prints the 128² cell, any feature build covering the column (and its script), and
whether trees/decor masks exist for that cell. Used by the redo skill.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from script_env import ROOT  # noqa: E402

from cell_features import is_feature_build  # noqa: E402
from coverage import cell_of, touched_by_build  # noqa: E402
from ground_layer import RETIRED_GROUND_PASSES  # noqa: E402
from pass_mask import mask_path  # noqa: E402

SCRIPTS = ROOT / 'scripts'
_NAME_RE = re.compile(r"""^NAME\s*=\s*['"]([^'"]+)['"]""", re.M)


def script_for(name: str, scripts_dir: Path = SCRIPTS) -> Path | None:
    """Path of the build script that sets ``NAME = name``, or None.

    @param name: build (plan) name in the cell journal
    @param scripts_dir: scripts directory to search
    @returns: path under scripts/, or None if not found
    """
    direct = scripts_dir / f'build_{name}.py'
    if direct.is_file():
        return direct
    needle = f"NAME = '{name}'"
    alt = f'NAME = "{name}"'
    for path in sorted(scripts_dir.glob('build_*.py')):
        text = path.read_text(encoding='utf-8', errors='replace')
        if needle in text or alt in text or (m := _NAME_RE.search(text)) and m.group(1) == name:
            return path
    return None


def features_covering(x: int, z: int) -> list[str]:
    """Feature build names whose recorded plan touches column (x, z).

    Skips layer passes and retired ground records (those are not redo targets).

    @param x: block x
    @param z: block z
    @returns: sorted feature names
    """
    return sorted(
        name for name, cols in touched_by_build().items()
        if (x, z) in cols
        and is_feature_build(name)
        and name not in RETIRED_GROUND_PASSES
    )


def primary_feature(x: int, z: int, y: int | None = None) -> str | None:
    """The feature to redo at this column, or None if none / ambiguous without a y hit.

    One covering feature wins. Several: the covering feature that owns the nearest
    non-air cell in this column to ``y`` (exact feet first). If none of the owners
    in a ±32 band around ``y`` are covering features, returns None.

    @param x: block x
    @param z: block z
    @param y: player block y when known
    @returns: feature name or None
    """
    hits = features_covering(x, z)
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    if y is None:
        return None
    band = [(x, yy, z) for yy in range(max(0, y - 8), y + 33)]
    from cell_registry import view_registry
    owned = view_registry().owned(band)
    exact = owned.get((x, y, z))
    if exact and exact[0] in hits and exact[1]:
        return exact[0]
    best_name, best_d = None, None
    for (_x, yy, _z), (build, state) in owned.items():
        if build not in hits or not state:
            continue
        d = abs(yy - y)
        if best_d is None or d < best_d:
            best_name, best_d = build, d
    return best_name


def cell_masks(cell: tuple[int, int]) -> dict[str, bool]:
    """Whether trees/decor masks exist for the cell.

    @param cell: (tx, tz)
    @returns: {'trees': bool, 'decor': bool}
    """
    return {
        'trees': mask_path('trees', cell).exists(),
        'decor': mask_path('decor', cell).exists(),
    }


def describe(x: int, z: int, y: int | None = None) -> dict:
    """Full redo-here payload for a column.

    @param x: block x
    @param z: block z
    @param y: optional player block y (disambiguates overlapping features)
    @returns: dict with at, cell, features, primary, masks
    """
    cell = cell_of(x, z)
    names = features_covering(x, z)
    features = []
    for name in names:
        script = script_for(name)
        features.append({
            'name': name,
            'script': str(script.relative_to(ROOT)) if script else None,
        })
    return {
        'at': [x, z],
        'y': y,
        'cell': [cell[0], cell[1]],
        'features': features,
        'primary': primary_feature(x, z, y),
        'masks': cell_masks(cell),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--at', required=True, help='x,z block column')
    ap.add_argument('--y', type=int, help='player block y (disambiguates overlapping features)')
    ap.add_argument('--json', action='store_true', help='machine-readable output')
    a = ap.parse_args(argv)
    try:
        xs, zs = a.at.split(',')
        x, z = int(xs), int(zs)
    except ValueError:
        raise SystemExit(f'--at needs x,z, got {a.at!r}') from None
    info = describe(x, z, a.y)
    if a.json:
        print(json.dumps(info, indent=2))
        return 0
    tx, tz = info['cell']
    print(f'at {x},{z}' + (f' y={a.y}' if a.y is not None else '') + f'  cell {tx},{tz}')
    print(f"masks  trees={'yes' if info['masks']['trees'] else 'no'}  "
          f"decor={'yes' if info['masks']['decor'] else 'no'}")
    if not info['features']:
        print('feature: none at this column')
        return 0
    for f in info['features']:
        mark = ' *' if f['name'] == info['primary'] else ''
        script = f['script'] or '(no script found)'
        print(f"feature: {f['name']}{mark}  {script}")
    if info['primary'] is None and len(info['features']) > 1:
        print('primary: ambiguous — pass --y or stand inside the feature to redo')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
