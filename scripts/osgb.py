#!/usr/bin/env python3
"""OSGB36 ↔ Minecraft coordinates (thin wrapper around world_frame).

Prefer ``scripts/world_frame.py`` for new code. This module keeps the historic
``to_mc`` / ``to_osgb`` / ``load_offsets`` names used across the tree.

Offsets come from ``config/world-frame.json`` (legacy shape still available via
``load_offsets`` for callers that expect OFFSET_E / SEA_LEVEL keys).

  python3 scripts/osgb.py to-mc 441900 112100
  python3 scripts/osgb.py to-osgb 5652 -2492
  python3 scripts/osgb.py info
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from script_env import ROOT  # noqa: E402
import world_frame as wf  # noqa: E402

OFFSETS = ROOT / 'config/map-maker-offsets.json'


def load_offsets(path: Path | None = None) -> dict:
    """Load GRID offsets (legacy OFFSET_E shape from the world frame).

    @param path: ignored when world-frame.json exists; kept for API compat
    @returns: offset dict with OFFSET_E / OFFSET_N / SEA_LEVEL / bounds
    """
    del path  # world-frame is canonical; path kept so call sites need not change
    return wf.legacy_offsets_dict()


def to_mc(easting: float, northing: float, offsets: dict | None = None) -> tuple[int, int]:
    """Convert OSGB easting/northing to Minecraft X/Z (integer blocks).

    @param easting: OSGB36 easting (metres)
    @param northing: OSGB36 northing (metres)
    @param offsets: optional legacy offset dict (OFFSET_E / OFFSET_N)
    @returns: (x, z)
    """
    if offsets is not None:
        x = int(round(easting - offsets['OFFSET_E']))
        z = int(round(-(northing - offsets['OFFSET_N'])))
        return x, z
    return wf.osgb_to_mc(easting, northing)


def to_osgb(x: float, z: float, offsets: dict | None = None) -> tuple[float, float]:
    """Convert Minecraft X/Z to OSGB easting/northing.

    @param x: Minecraft X
    @param z: Minecraft Z
    @param offsets: optional legacy offset dict
    @returns: (easting, northing)
    """
    if offsets is not None:
        return float(offsets['OFFSET_E'] + x), float(offsets['OFFSET_N'] - z)
    return wf.mc_to_osgb(x, z)


def in_bounds(x: int, z: int, offsets: dict | None = None) -> bool:
    """True if (x, z) lies inside the Geocraft map rectangle.

    @param x: Minecraft X
    @param z: Minecraft Z
    @param offsets: optional legacy offset dict
    @returns: whether the point is inside WEST..EAST, SOUTH..NORTH
    """
    if offsets is not None:
        return (
            offsets['WEST'] <= x <= offsets['EAST']
            and offsets['SOUTH'] <= z <= offsets['NORTH']
        )
    return wf.in_bounds(x, z)


def main(argv: list[str] | None = None) -> int:
    """CLI: info | to-mc E N | to-osgb X Z."""
    argv = list(sys.argv[1:] if argv is None else argv)
    o = load_offsets()
    if not argv or argv[0] == 'info':
        print(json.dumps({
            'grid': o.get('GRID'),
            'OFFSET_E': o['OFFSET_E'],
            'OFFSET_N': o['OFFSET_N'],
            'bounds_mc': {
                'west': o['WEST'], 'east': o['EAST'],
                'south': o['SOUTH'], 'north': o['NORTH'],
            },
            'sea_level': o.get('SEA_LEVEL'),
            'elevation': o.get('ELEVATION_PLUGIN'),
            'world_frame': str(wf.FRAME_PATH.relative_to(ROOT)),
        }, indent=2))
        return 0
    cmd = argv[0]
    if cmd == 'to-mc' and len(argv) == 3:
        e, n = float(argv[1]), float(argv[2])
        x, z = to_mc(e, n, o)
        print(f'OSGB E{e:.1f} N{n:.1f}  ->  MC X{x} Z{z}'
              f'  ({"in" if in_bounds(x, z, o) else "OUT OF"} bounds)')
        return 0
    if cmd == 'to-osgb' and len(argv) == 3:
        x, z = float(argv[1]), float(argv[2])
        e, n = to_osgb(x, z, o)
        print(f'MC X{x:.0f} Z{z:.0f}  ->  OSGB E{e:.1f} N{n:.1f}'
              f'  ({"in" if in_bounds(int(x), int(z), o) else "OUT OF"} bounds)')
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
