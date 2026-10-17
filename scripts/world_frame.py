#!/usr/bin/env python3
"""Single home for place geography ↔ Minecraft coordinates.

Reads ``config/world-frame.json`` (CRS, offsets, Y offset, bounds, anchor).
Converts between WGS84, OSGB36 / other EPSG, Minecraft x/z(/y), coverage cells,
and OSM web-mercator XYZ tiles. Prefer this module over scattered OFFSET_E /
gdaltransform copies.

  .venv/bin/python scripts/world_frame.py info
  .venv/bin/python scripts/world_frame.py to-mc --osgb 441900,112100
  .venv/bin/python scripts/world_frame.py to-mc --wgs84 50.903,-1.4044
  .venv/bin/python scripts/world_frame.py to-osgb --mc 5652,-2492
  .venv/bin/python scripts/world_frame.py to-wgs84 --mc 5652,-2492
  .venv/bin/python scripts/world_frame.py to-cell --mc 5652,-2492
  .venv/bin/python scripts/world_frame.py to-wm --mc 5652,-2492 --zoom 18
  .venv/bin/python scripts/world_frame.py y --aod 45.2
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from script_env import ROOT  # noqa: E402

FRAME_PATH = ROOT / 'config' / 'world-frame.json'
LEGACY_OFFSETS = ROOT / 'config' / 'map-maker-offsets.json'
CELL = 128


@dataclass(frozen=True)
class WorldFrame:
    """Loaded world-frame config for one place.

    @param data: raw JSON object from world-frame.json
    """

    data: dict

    @property
    def crs(self) -> str:
        """Projected CRS (e.g. EPSG:27700)."""
        return str(self.data.get('crs') or 'EPSG:27700')

    @property
    def offset_e(self) -> float:
        """Easting of MC x=0."""
        return float(self.data['offsets']['easting'])

    @property
    def offset_n(self) -> float:
        """Northing of MC z=0."""
        return float(self.data['offsets']['northing'])

    @property
    def y_offset(self) -> int:
        """Block Y of metres AOD 0 (same role as Geocraft SEA_LEVEL)."""
        return int(self.data.get('y_offset', self.data.get('sea_level', 11)))

    @property
    def sea_level(self) -> int:
        """Alias of y_offset for Geocraft callers."""
        return self.y_offset

    @property
    def bounds_mc(self) -> dict:
        """Inclusive MC rectangle: west/east/north/south."""
        return dict(self.data['bounds_mc'])

    @property
    def anchor(self) -> dict:
        """Real-world point mapped to MC (0, 0)."""
        return dict(self.data.get('anchor') or {})


_FRAME: WorldFrame | None = None


def load_frame(path: Path | None = None, *, reload: bool = False) -> WorldFrame:
    """Load and cache the world-frame config.

    @param path: optional JSON path (default config/world-frame.json)
    @param reload: force re-read from disk
    @returns: WorldFrame
    """
    global _FRAME
    if _FRAME is not None and not reload and path is None:
        return _FRAME
    p = path or FRAME_PATH
    if not p.exists() and LEGACY_OFFSETS.exists():
        frame = WorldFrame(_legacy_to_frame(json.loads(LEGACY_OFFSETS.read_text())))
    else:
        data = json.loads(p.read_text())
        for key in ('offsets', 'bounds_mc'):
            if key not in data:
                raise SystemExit(f'{p} missing {key}')
        frame = WorldFrame(data)
    if path is None:
        _FRAME = frame
    return frame


def _legacy_to_frame(o: dict) -> dict:
    """Build a world-frame dict from map-maker-offsets.json shape."""
    return {
        'place': 'Southampton',
        'crs': 'EPSG:27700',
        'grid': o.get('GRID', 'OSGB36'),
        'anchor': {
            'description': 'Geocraft map-maker origin (from legacy offsets)',
            'osgb': {'easting': o['OFFSET_E'], 'northing': o['OFFSET_N']},
            'mc': {'x': 0, 'z': 0},
        },
        'offsets': {'easting': o['OFFSET_E'], 'northing': o['OFFSET_N']},
        'bounds_mc': {
            'west': o['WEST'], 'east': o['EAST'],
            'north': o['NORTH'], 'south': o['SOUTH'],
        },
        'y_offset': o.get('SEA_LEVEL', 11),
        'sea_level': o.get('SEA_LEVEL', 11),
        'top_of_world': o.get('TOP_OF_WORLD'),
        'extend_downwards': o.get('EXTEND_DOWNWARDS'),
        'elevation_plugin': o.get('ELEVATION_PLUGIN'),
        'map_zoom': o.get('MAP_ZOOM'),
    }


def legacy_offsets_dict(frame: WorldFrame | None = None) -> dict:
    """OFFSET_E-shaped dict for callers that still expect map-maker-offsets keys.

    @param frame: optional loaded frame
    @returns: dict with OFFSET_E, OFFSET_N, WEST…SEA_LEVEL, …
    """
    f = frame or load_frame()
    b = f.bounds_mc
    d = f.data
    return {
        'GRID': d.get('grid', 'OSGB36'),
        'OFFSET_E': f.offset_e,
        'OFFSET_N': f.offset_n,
        'WEST': b['west'],
        'EAST': b['east'],
        'NORTH': b['north'],
        'SOUTH': b['south'],
        'SEA_LEVEL': f.sea_level,
        'TOP_OF_WORLD': d.get('top_of_world'),
        'EXTEND_DOWNWARDS': d.get('extend_downwards'),
        'ELEVATION_PLUGIN': d.get('elevation_plugin'),
        'FLATLAND': d.get('flatland', 0),
        'FLOOD': d.get('flood', 0),
        'MAP_ZOOM': d.get('map_zoom'),
        'SCALE': None,
        'ROTATE': None,
        'YSHIFT': None,
    }


# ---- EPSG / gdal -------------------------------------------------------------

def _gdal_transform(pairs: list[tuple[float, float]], src: str, dst: str) -> list[tuple[float, float]]:
    """Run gdaltransform on (x, y) pairs in source CRS order.

    @param pairs: input points
    @param src: source SRS
    @param dst: target SRS
    @returns: transformed pairs
    """
    if not pairs:
        return []
    inp = ''.join(f'{a} {b}\n' for a, b in pairs)
    proc = subprocess.run(
        ['gdaltransform', '-s_srs', src, '-t_srs', dst],
        input=inp.encode(),
        capture_output=True,
        check=True,
    )
    out = []
    for line in proc.stdout.decode().splitlines():
        if not line.strip():
            continue
        parts = line.split()
        out.append((float(parts[0]), float(parts[1])))
    return out


def transform_epsg(
    pairs: list[tuple[float, float]], src: str, dst: str,
) -> list[tuple[float, float]]:
    """Transform coordinate pairs between EPSG (or other) SRS strings.

    @param pairs: (x, y) in source CRS (lon,lat for EPSG:4326; E,N for 27700)
    @param src: source SRS
    @param dst: destination SRS
    @returns: transformed pairs in destination order
    """
    return _gdal_transform(pairs, src, dst)


def wgs84_to_osgb(lat: float, lon: float) -> tuple[float, float]:
    """WGS84 → OSGB36 easting/northing.

    @param lat: latitude
    @param lon: longitude
    @returns: (easting, northing)
    """
    e, n = _gdal_transform([(lon, lat)], 'EPSG:4326', 'EPSG:27700')[0]
    return e, n


def osgb_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    """OSGB36 → WGS84 lat/lon.

    @param easting: easting
    @param northing: northing
    @returns: (lat, lon)
    """
    lon, lat = _gdal_transform([(easting, northing)], 'EPSG:27700', 'EPSG:4326')[0]
    return lat, lon


def wgs84_batch_to_osgb(lat_lon: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Many WGS84 points → OSGB.

    @param lat_lon: list of (lat, lon)
    @returns: list of (easting, northing)
    """
    return _gdal_transform([(lon, lat) for lat, lon in lat_lon], 'EPSG:4326', 'EPSG:27700')


def osgb_batch_to_wgs84(en: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Many OSGB points → WGS84.

    @param en: list of (easting, northing)
    @returns: list of (lat, lon)
    """
    ll = _gdal_transform(en, 'EPSG:27700', 'EPSG:4326')
    return [(lat, lon) for lon, lat in ll]


# ---- OSGB ↔ MC ---------------------------------------------------------------

def osgb_to_mc(
    easting: float, northing: float, frame: WorldFrame | None = None,
) -> tuple[int, int]:
    """OSGB → Minecraft X/Z (integer blocks). North is −Z.

    @param easting: OSGB easting
    @param northing: OSGB northing
    @param frame: optional frame
    @returns: (x, z)
    """
    f = frame or load_frame()
    x = int(round(easting - f.offset_e))
    z = int(round(-(northing - f.offset_n)))
    return x, z


def mc_to_osgb(
    x: float, z: float, frame: WorldFrame | None = None,
) -> tuple[float, float]:
    """Minecraft X/Z → OSGB easting/northing.

    @param x: Minecraft X
    @param z: Minecraft Z
    @param frame: optional frame
    @returns: (easting, northing)
    """
    f = frame or load_frame()
    return float(f.offset_e + x), float(f.offset_n - z)


def mc_grid_to_en(xs, zs, frame: WorldFrame | None = None):
    """Vectorised MC → OSGB for numpy meshgrids (or array-likes).

    @param xs: X array
    @param zs: Z array
    @param frame: optional frame
    @returns: (easting_array, northing_array)
    """
    f = frame or load_frame()
    return f.offset_e + xs, f.offset_n - zs


def in_bounds(x: int, z: int, frame: WorldFrame | None = None) -> bool:
    """True if (x, z) lies inside the configured MC rectangle.

    @param x: Minecraft X
    @param z: Minecraft Z
    @param frame: optional frame
    @returns: whether inside bounds
    """
    f = frame or load_frame()
    b = f.bounds_mc
    return b['west'] <= x <= b['east'] and b['south'] <= z <= b['north']


# ---- WGS84 ↔ MC (composed) ---------------------------------------------------

def wgs84_to_mc(lat: float, lon: float, frame: WorldFrame | None = None) -> tuple[int, int]:
    """WGS84 → Minecraft X/Z.

    @param lat: latitude
    @param lon: longitude
    @param frame: optional frame
    @returns: (x, z)
    """
    e, n = wgs84_to_osgb(lat, lon)
    return osgb_to_mc(e, n, frame)


def mc_to_wgs84(x: float, z: float, frame: WorldFrame | None = None) -> tuple[float, float]:
    """Minecraft X/Z → WGS84 lat/lon.

    @param x: Minecraft X
    @param z: Minecraft Z
    @param frame: optional frame
    @returns: (lat, lon)
    """
    e, n = mc_to_osgb(x, z, frame)
    return osgb_to_wgs84(e, n)


def wgs84_batch_to_mc(
    lat_lon: list[tuple[float, float]], frame: WorldFrame | None = None,
) -> list[tuple[int, int]]:
    """Many WGS84 → MC.

    @param lat_lon: list of (lat, lon)
    @param frame: optional frame
    @returns: list of (x, z)
    """
    return [osgb_to_mc(e, n, frame) for e, n in wgs84_batch_to_osgb(lat_lon)]


def mc_batch_to_wgs84(
    xz: list[tuple[float, float]], frame: WorldFrame | None = None,
) -> list[tuple[float, float]]:
    """Many MC → WGS84.

    @param xz: list of (x, z)
    @param frame: optional frame
    @returns: list of (lat, lon)
    """
    en = [mc_to_osgb(x, z, frame) for x, z in xz]
    return osgb_batch_to_wgs84(en)


# ---- elevation / Y -----------------------------------------------------------

def metres_aod_to_y(metres: float, frame: WorldFrame | None = None) -> int:
    """Map metres above datum to Minecraft Y (trunc toward −∞ like Geocraft).

    @param metres: metres AOD
    @param frame: optional frame
    @returns: block Y
    """
    f = frame or load_frame()
    return f.y_offset + int(math.trunc(metres))


def y_to_metres_aod(y: int, frame: WorldFrame | None = None) -> float:
    """Inverse of metres_aod_to_y (exact only for integer metres).

    @param y: block Y
    @param frame: optional frame
    @returns: metres AOD
    """
    f = frame or load_frame()
    return float(y - f.y_offset)


# ---- cells -------------------------------------------------------------------

def parse_cell(spec: str) -> tuple[int, int]:
    """Parse ``tx,tz``.

    @param spec: comma-separated cell indices
    @returns: (tx, tz)
    """
    parts = [p.strip() for p in str(spec).split(',')]
    if len(parts) != 2:
        raise SystemExit(f'expected tx,tz got {spec!r}')
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        raise SystemExit(f'expected tx,tz got {spec!r}') from None


def cell_of(x: int, z: int, size: int = CELL) -> tuple[int, int]:
    """Coverage cell of a block column.

    @param x: Minecraft X
    @param z: Minecraft Z
    @param size: cell edge (default 128)
    @returns: (tx, tz)
    """
    return x // size, z // size


def cell_bounds(cell: tuple[int, int], margin: int = 0, size: int = CELL) -> tuple[int, int, int, int]:
    """Inclusive (x0, z0, x1, z1) of a cell.

    @param cell: (tx, tz)
    @param margin: blocks grown on every side
    @param size: cell edge
    @returns: bbox
    """
    x0, z0 = cell[0] * size, cell[1] * size
    return x0 - margin, z0 - margin, x0 + size - 1 + margin, z0 + size - 1 + margin


# ---- web-mercator / OSM XYZ --------------------------------------------------

def tile_float(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """Fractional web-mercator tile coordinate of a WGS84 point.

    @param lat: latitude
    @param lon: longitude
    @param zoom: zoom level
    @returns: (x, y) in tiles
    """
    n = 2 ** zoom
    return (
        (lon + 180) / 360 * n,
        (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n,
    )


def deg2num(lat_deg: float, lon_deg: float, zoom: int) -> tuple[int, int]:
    """WGS84 → integer web-mercator tile indices.

    @param lat_deg: latitude
    @param lon_deg: longitude
    @param zoom: zoom level
    @returns: (xtile, ytile)
    """
    fx, fy = tile_float(lat_deg, lon_deg, zoom)
    return int(fx), int(fy)


def num2deg(xtile: int, ytile: int, zoom: int) -> tuple[float, float]:
    """NW corner of a web-mercator tile → WGS84 lat/lon.

    @param xtile: tile X
    @param ytile: tile Y
    @param zoom: zoom level
    @returns: (lat, lon)
    """
    n = 2.0 ** zoom
    lon_deg = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * ytile / n)))
    return math.degrees(lat_rad), lon_deg


def parse_wm(s: str) -> tuple[int, int, int]:
    """Parse ``z/x/y`` web-mercator id.

    @param s: tile id
    @returns: (z, x, y)
    """
    z, x, y = s.strip().split('/')
    return int(z), int(x), int(y)


def mc_to_wm(
    x: float, z: float, zoom: int, frame: WorldFrame | None = None,
) -> tuple[int, int]:
    """Minecraft column → web-mercator tile at zoom.

    @param x: Minecraft X
    @param z: Minecraft Z
    @param zoom: zoom level
    @param frame: optional frame
    @returns: (xtile, ytile)
    """
    lat, lon = mc_to_wgs84(x, z, frame)
    return deg2num(lat, lon, zoom)


def wm_tile_bbox_mc(
    z: int, x: int, y: int, frame: WorldFrame | None = None,
) -> tuple[int, int, int, int]:
    """Minecraft inclusive XZ bbox covering a web-mercator tile.

    @param z: zoom
    @param x: tile X
    @param y: tile Y
    @param frame: optional frame
    @returns: (x0, z0, x1, z1)
    """
    lat_nw, lon_nw = num2deg(x, y, z)
    lat_se, lon_se = num2deg(x + 1, y + 1, z)
    corners_ll = [
        (lat_nw, lon_nw), (lat_se, lon_se),
        (lat_nw, lon_se), (lat_se, lon_nw),
    ]
    corners = [wgs84_to_mc(lat, lon, frame) for lat, lon in corners_ll]
    xs = [c[0] for c in corners]
    zs = [c[1] for c in corners]
    return min(xs), min(zs), max(xs), max(zs)


# ---- CLI ---------------------------------------------------------------------

def _parse_pair(spec: str, label: str) -> tuple[float, float]:
    parts = [p.strip() for p in spec.split(',')]
    if len(parts) != 2:
        raise SystemExit(f'expected {label} got {spec!r}')
    return float(parts[0]), float(parts[1])


def main(argv: list[str] | None = None) -> int:
    """CLI entry for world-frame conversions.

    @param argv: optional argv override
    @returns: exit code
    """
    ap = argparse.ArgumentParser(description='World-frame coordinate conversions')
    sub = ap.add_subparsers(dest='cmd', required=True)

    sub.add_parser('info', help='print loaded frame as JSON')

    p = sub.add_parser('to-mc', help='to Minecraft x,z')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--osgb', help='easting,northing')
    g.add_argument('--wgs84', help='lat,lon')
    g.add_argument('--epsg', nargs=2, metavar=('SRS', 'XY'),
                   help='source EPSG and x,y in that CRS (e.g. EPSG:4326 lon,lat)')

    p = sub.add_parser('to-osgb', help='to OSGB easting,northing')
    p.add_argument('--mc', required=True, help='x,z')

    p = sub.add_parser('to-wgs84', help='to WGS84 lat,lon')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--mc', help='x,z')
    g.add_argument('--osgb', help='easting,northing')

    p = sub.add_parser('to-cell', help='coverage cell of an MC point')
    p.add_argument('--mc', required=True, help='x,z')

    p = sub.add_parser('to-wm', help='web-mercator tile of an MC point')
    p.add_argument('--mc', required=True, help='x,z')
    p.add_argument('--zoom', type=int, default=18)

    p = sub.add_parser('y', help='metres AOD ↔ block Y')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--aod', type=float, help='metres above datum → Y')
    g.add_argument('--block-y', type=int, dest='block_y', help='block Y → metres AOD')

    args = ap.parse_args(argv)
    f = load_frame()

    if args.cmd == 'info':
        print(json.dumps({
            'path': str(FRAME_PATH.relative_to(ROOT)) if FRAME_PATH.exists() else None,
            'place': f.data.get('place'),
            'crs': f.crs,
            'anchor': f.anchor,
            'offsets': {'easting': f.offset_e, 'northing': f.offset_n},
            'bounds_mc': f.bounds_mc,
            'y_offset': f.y_offset,
            'elevation_plugin': f.data.get('elevation_plugin'),
        }, indent=2))
        return 0

    if args.cmd == 'to-mc':
        if args.osgb:
            e, n = _parse_pair(args.osgb, 'easting,northing')
            x, z = osgb_to_mc(e, n, f)
        elif args.wgs84:
            lat, lon = _parse_pair(args.wgs84, 'lat,lon')
            x, z = wgs84_to_mc(lat, lon, f)
        else:
            srs, xy = args.epsg
            a, b = _parse_pair(xy, 'x,y')
            e, n = transform_epsg([(a, b)], srs, f.crs)[0]
            x, z = osgb_to_mc(e, n, f)
        print(f'MC X{x} Z{z}  ({"in" if in_bounds(x, z, f) else "OUT OF"} bounds)')
        return 0

    if args.cmd == 'to-osgb':
        x, z = _parse_pair(args.mc, 'x,z')
        e, n = mc_to_osgb(x, z, f)
        print(f'OSGB E{e:.1f} N{n:.1f}')
        return 0

    if args.cmd == 'to-wgs84':
        if args.mc:
            x, z = _parse_pair(args.mc, 'x,z')
            lat, lon = mc_to_wgs84(x, z, f)
        else:
            e, n = _parse_pair(args.osgb, 'easting,northing')
            lat, lon = osgb_to_wgs84(e, n)
        print(f'WGS84 {lat:.6f},{lon:.6f}')
        return 0

    if args.cmd == 'to-cell':
        x, z = (int(v) for v in _parse_pair(args.mc, 'x,z'))
        tx, tz = cell_of(x, z)
        print(f'cell {tx},{tz}')
        return 0

    if args.cmd == 'to-wm':
        x, z = _parse_pair(args.mc, 'x,z')
        tx, ty = mc_to_wm(x, z, args.zoom, f)
        print(f'{args.zoom}/{tx}/{ty}')
        return 0

    if args.cmd == 'y':
        if args.aod is not None:
            print(f'Y {metres_aod_to_y(args.aod, f)}  (y_offset={f.y_offset})')
        else:
            print(f'AOD {y_to_metres_aod(args.block_y, f):.1f} m  (y_offset={f.y_offset})')
        return 0

    return 2


if __name__ == '__main__':
    raise SystemExit(main())
