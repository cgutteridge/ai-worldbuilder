#!/usr/bin/env python3
"""Single home for place geography ↔ Minecraft coordinates.

Reads ``config/world-frame.json`` (CRS, offsets, Y offset, bounds, anchor).
Converts between WGS84, OSGB36 / other EPSG, Minecraft x/z(/y), coverage cells,
and web-mercator XYZ tiles. Prefer this module over scattered OFFSET_E /
private CRS copies.

Point conversion (WGS84 ↔ OSGB / arbitrary EPSG pairs) uses in-process
``pyproj`` with cached ``Transformer`` instances and ``always_xy=True``
(lon,lat for geographic CRS; easting,northing for projected). GDAL remains
appropriate for bulk geospatial *file* work (lidar/raster); it is not used
here for individual or small-batch points.

**OSTN15 policy:** for EPSG:4326 ↔ EPSG:27700, PROJ prefers the Ordnance
Survey OSTN15 grid when that grid file is on the PROJ data path (Homebrew
``proj`` data, ``PROJ_DATA``, or ``PROJ_NETWORK=ON`` downloading from
cdn.proj.org). Without the grid, PROJ falls back to a Helmert datum shift
(~1–5 m across GB). Callers must not assume identity with a particular
GDAL/gdaltransform install; use ``ostn15_grid_available()`` when centimetre
agreement with OS published National Grid is required.

  .venv/bin/python scripts/world_frame.py info
  .venv/bin/python scripts/world_frame.py to-mc --osgb 441900,112100
  .venv/bin/python scripts/world_frame.py to-mc --wgs84 50.903,-1.4044
  .venv/bin/python scripts/world_frame.py to-osgb --mc 5652,-2492
  .venv/bin/python scripts/world_frame.py to-wgs84 --mc 5652,-2492
  .venv/bin/python scripts/world_frame.py to-cell --mc 5652,-2492
  .venv/bin/python scripts/world_frame.py to-wm --wgs84 50.903,-1.4044 --zoom 18
  .venv/bin/python scripts/world_frame.py to-wm --mc 5652,-2492 --zoom 18
  .venv/bin/python scripts/world_frame.py from-wm --tile 18/130049/87872 --east-px 88.9 --south-px 9.7 --as wgs84
  .venv/bin/python scripts/world_frame.py batch-wm --file points.json
  .venv/bin/python scripts/world_frame.py y --aod 45.2

XYZ tile positions use a 256-pixel *logical* tile: ``east_px`` increases
rightward and ``south_px`` downward from the northwest corner; offsets are
independent of any downloaded image resolution. Latitude/longitude conversion
needs no world; Minecraft conversion needs ``--frame`` or an active world.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

from pyproj import Transformer, datadir
from pyproj.transformer import TransformerGroup

import script_env  # noqa: E402
import cellmath  # noqa: E402

_FRAME: 'WorldFrame | None' = None


def _require_root() -> Path:
    """Return the bound world root or raise.

    @returns: active world checkout
    @raises RuntimeError: when no world is selected
    """
    if not script_env.world_bound():
        raise RuntimeError(
            'no world selected; pass --world <name> (a directory under worlds/)'
        )
    return Path(script_env.ROOT)


def frame_path() -> Path:
    """Path to ``config/world-frame.json`` in the active world.

    @returns: absolute path
    """
    return _require_root() / 'config' / 'world-frame.json'


def legacy_offsets_path() -> Path:
    """Path to legacy ``config/map-maker-offsets.json`` in the active world.

    @returns: absolute path
    """
    return _require_root() / 'config' / 'map-maker-offsets.json'


def clear_frame_cache() -> None:
    """Drop the cached :class:`WorldFrame` (call after switching world)."""
    global _FRAME
    _FRAME = None


def __getattr__(name: str):
    """Lazy ``FRAME_PATH`` / ``LEGACY_OFFSETS`` / ``ROOT`` for callers and CLIs."""
    if name == 'FRAME_PATH':
        return frame_path()
    if name == 'LEGACY_OFFSETS':
        return legacy_offsets_path()
    if name == 'ROOT':
        return _require_root()
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


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
        """Block Y of metres AOD 0 (same role as legacy SEA_LEVEL)."""
        return int(self.data.get('y_offset', self.data.get('sea_level', 11)))

    @property
    def sea_level(self) -> int:
        """Alias of y_offset for legacy callers."""
        return self.y_offset

    @property
    def bounds_mc(self) -> dict:
        """Inclusive MC rectangle: west/east/north/south."""
        return dict(self.data['bounds_mc'])

    @property
    def anchor(self) -> dict:
        """Real-world point mapped to MC (0, 0)."""
        return dict(self.data.get('anchor') or {})


def load_frame(path: Path | None = None, *, reload: bool = False) -> WorldFrame:
    """Load and cache the world-frame config.

    @param path: optional JSON path (default config/world-frame.json)
    @param reload: force re-read from disk
    @returns: WorldFrame
    """
    global _FRAME
    if _FRAME is not None and not reload and path is None:
        return _FRAME
    p = path or frame_path()
    legacy = legacy_offsets_path()
    if not p.exists() and legacy.exists():
        frame = WorldFrame(_legacy_to_frame(json.loads(legacy.read_text())))
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
            'description': 'Legacy map-maker origin (from offsets)',
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


# ---- EPSG / pyproj -----------------------------------------------------------

_TRANSFORMERS: dict[tuple[str, str], Transformer] = {}
_PROJ_DATA_READY = False

# Common system installs of the OSTN15 NTv2 grid (Homebrew / Linux packages).
_SYSTEM_PROJ_DATA_CANDIDATES = (
    Path('/opt/homebrew/share/proj'),
    Path('/usr/local/share/proj'),
    Path('/usr/share/proj'),
)
_OSTN15_GRID = 'uk_os_OSTN15_NTv2_OSGBtoETRS.tif'


def _ensure_proj_data() -> None:
    """Append system PROJ data dirs that carry the OSTN15 grid, once per process.

    pyproj's bundled share/proj often omits UK grids; Homebrew ``proj`` installs
    them under ``/opt/homebrew/share/proj``. Appending that path (when present)
    lets ``Transformer`` pick OSTN15 without requiring ``PROJ_NETWORK``.
    """
    global _PROJ_DATA_READY
    if _PROJ_DATA_READY:
        return
    for candidate in _SYSTEM_PROJ_DATA_CANDIDATES:
        if (candidate / _OSTN15_GRID).is_file():
            datadir.append_data_dir(str(candidate))
            break
    _PROJ_DATA_READY = True


def _transformer(src: str, dst: str) -> Transformer:
    """Return a cached always_xy Transformer for ``src`` → ``dst``.

    @param src: source SRS (e.g. EPSG:4326)
    @param dst: destination SRS
    @returns: pyproj Transformer
    """
    _ensure_proj_data()
    key = (src, dst)
    cached = _TRANSFORMERS.get(key)
    if cached is not None:
        return cached
    transformer = Transformer.from_crs(src, dst, always_xy=True)
    _TRANSFORMERS[key] = transformer
    return transformer


def ostn15_grid_available() -> bool:
    """True when PROJ can use the OSTN15 grid for WGS84 ↔ OSGB36.

    @returns: whether the preferred OSTN15 operation is available (not merely
        listed as unavailable pending a network download)
    """
    _ensure_proj_data()
    group = TransformerGroup('EPSG:4326', 'EPSG:27700', always_xy=True)
    return len(group.unavailable_operations) == 0


def transform_epsg(
    pairs: list[tuple[float, float]], src: str, dst: str,
) -> list[tuple[float, float]]:
    """Transform coordinate pairs between EPSG (or other) SRS strings.

    Axis order is always ``(x, y)`` / ``(lon, lat)`` / ``(easting, northing)``
    via ``always_xy=True``, matching the historic gdaltransform pair order.

    @param pairs: (x, y) in source CRS (lon,lat for EPSG:4326; E,N for 27700)
    @param src: source SRS
    @param dst: destination SRS
    @returns: transformed pairs in destination order
    """
    if not pairs:
        return []
    transformer = _transformer(src, dst)
    xs, ys = zip(*pairs)
    out_x, out_y = transformer.transform(xs, ys)
    return list(zip(out_x, out_y))


def wgs84_to_osgb(lat: float, lon: float) -> tuple[float, float]:
    """WGS84 → OSGB36 easting/northing.

    @param lat: latitude
    @param lon: longitude
    @returns: (easting, northing)
    """
    e, n = transform_epsg([(lon, lat)], 'EPSG:4326', 'EPSG:27700')[0]
    return e, n


def osgb_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    """OSGB36 → WGS84 lat/lon.

    @param easting: easting
    @param northing: northing
    @returns: (lat, lon)
    """
    lon, lat = transform_epsg([(easting, northing)], 'EPSG:27700', 'EPSG:4326')[0]
    return lat, lon


def wgs84_batch_to_osgb(lat_lon: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Many WGS84 points → OSGB.

    @param lat_lon: list of (lat, lon)
    @returns: list of (easting, northing)
    """
    return transform_epsg(
        [(lon, lat) for lat, lon in lat_lon], 'EPSG:4326', 'EPSG:27700',
    )


def osgb_batch_to_wgs84(en: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Many OSGB points → WGS84.

    @param en: list of (easting, northing)
    @returns: list of (lat, lon)
    """
    ll = transform_epsg(en, 'EPSG:27700', 'EPSG:4326')
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
    """Map metres above datum to Minecraft Y (trunc toward −∞ like the legacy importer).

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


# ---- cells (owned by cellmath) -----------------------------------------------

def parse_cell(spec: str) -> tuple[int, int]:
    """Parse ``tx,tz`` (delegates to cellmath).

    @param spec: comma-separated cell indices
    @returns: (tx, tz)
    """
    return cellmath.parse_cell(spec)


def cell_of(x: int, z: int, size: int | None = None) -> tuple[int, int]:
    """Coverage cell of a block column (delegates to cellmath).

    @param x: Minecraft X
    @param z: Minecraft Z
    @param size: cell edge (default: configured cell size)
    @returns: (tx, tz)
    """
    return cellmath.cell_of(x, z, size=size)


def cell_bounds(
    cell: tuple[int, int], margin: int = 0, size: int | None = None,
) -> tuple[int, int, int, int]:
    """Inclusive (x0, z0, x1, z1) of a cell (delegates to cellmath).

    @param cell: (tx, tz)
    @param margin: blocks grown on every side
    @param size: cell edge (default: configured cell size)
    @returns: bbox
    """
    return cellmath.bounds(cell, margin=margin, size=size)


# ---- web-mercator / tile XYZ -------------------------------------------------

WM_TILE_PX = 256
WM_MIN_ZOOM = 0
WM_MAX_ZOOM = 30
# Max latitude representable in spherical Web Mercator (tile y domain).
WM_MAX_LAT = math.degrees(math.atan(math.sinh(math.pi)))


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


def wgs84_to_wm_global_px(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """WGS84 → global pixel on the zoom's 256-px logical Web Mercator grid.

    Same NW-origin convention as ``east_px`` / ``south_px`` (right = east,
    down = south). Prefer ``wgs84_to_wm_position`` when you need a tile code.

    @param lat: latitude
    @param lon: longitude
    @param zoom: zoom level
    @returns: (global_x_px, global_y_px)
    """
    fx, fy = tile_float(lat, lon, zoom)
    return fx * WM_TILE_PX, fy * WM_TILE_PX


def _tile_float_to_wgs84(fx: float, fy: float, zoom: int) -> tuple[float, float]:
    """Fractional tile (x, y) → WGS84 lat/lon (inverse of tile_float)."""
    n = 2.0 ** zoom
    lon_deg = fx / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * fy / n)))
    return math.degrees(lat_rad), lon_deg


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
    return _tile_float_to_wgs84(float(xtile), float(ytile), zoom)


def parse_wm(s: str) -> tuple[int, int, int]:
    """Parse ``z/x/y`` web-mercator id.

    @param s: tile id
    @returns: (z, x, y)
    @raises ValueError: when the string is not three integer fields
    """
    parts = s.strip().split('/')
    if len(parts) != 3:
        raise ValueError(f'expected z/x/y tile code, got {s!r}')
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError as exc:
        raise ValueError(f'expected z/x/y tile code, got {s!r}') from exc


def _require_finite(value: float, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f'{label} must be a finite number')
    v = float(value)
    if not math.isfinite(v):
        raise ValueError(f'{label} must be finite')
    return v


def _validate_zoom(zoom: int) -> int:
    if not isinstance(zoom, int) or isinstance(zoom, bool):
        raise ValueError(f'zoom must be an integer 0..{WM_MAX_ZOOM}')
    if zoom < WM_MIN_ZOOM or zoom > WM_MAX_ZOOM:
        raise ValueError(f'zoom must be in {WM_MIN_ZOOM}..{WM_MAX_ZOOM}, got {zoom}')
    return zoom


def _validate_wgs84(lat: float, lon: float) -> tuple[float, float]:
    lat = _require_finite(lat, 'latitude')
    lon = _require_finite(lon, 'longitude')
    if lon < -180.0 or lon >= 180.0:
        raise ValueError(f'longitude must be in [-180, 180), got {lon}')
    # Exclusive of ±WM_MAX_LAT: at the exact poles tile_float can emit y==n or
    # negative south_px that reverse conversion then rejects.
    if lat <= -WM_MAX_LAT or lat >= WM_MAX_LAT:
        raise ValueError(
            f'latitude must be in ({-WM_MAX_LAT}, {WM_MAX_LAT}), got {lat}',
        )
    return lat, lon


def _validate_tile_indices(zoom: int, x: int, y: int) -> None:
    n = 1 << zoom
    if not isinstance(x, int) or isinstance(x, bool) or x < 0 or x >= n:
        raise ValueError(f'tile x must be in 0..{n - 1} at zoom {zoom}, got {x}')
    if not isinstance(y, int) or isinstance(y, bool) or y < 0 or y >= n:
        raise ValueError(f'tile y must be in 0..{n - 1} at zoom {zoom}, got {y}')


def _validate_pixel_offset(value: float, label: str) -> float:
    v = _require_finite(value, label)
    if v < 0.0 or v >= WM_TILE_PX:
        raise ValueError(f'{label} must be in [0, {WM_TILE_PX}), got {v}')
    return v


def _resolve_frame(frame: WorldFrame | None) -> WorldFrame:
    """Return an explicit frame or the active world's loaded frame.

    @param frame: optional WorldFrame
    @returns: frame to use for Minecraft conversion
    @raises ValueError: when no frame is available
    """
    if frame is not None:
        return frame
    try:
        return load_frame()
    except SystemExit as exc:
        raise ValueError(
            'Minecraft conversion needs an explicit frame or a selected world '
            f'with world-frame.json ({exc})',
        ) from exc
    except (OSError, KeyError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError(
            'Minecraft conversion needs an explicit frame or a selected world '
            f'with world-frame.json ({exc})',
        ) from exc


def wgs84_to_wm_position(lat: float, lon: float, zoom: int) -> dict:
    """WGS84 → XYZ tile code plus logical pixel offsets (no world required).

    Offsets are floating-point pixels on a 256-px logical tile from the
    northwest corner: ``east_px`` rightward, ``south_px`` downward.

    @param lat: WGS84 latitude
    @param lon: WGS84 longitude in [-180, 180)
    @param zoom: integer zoom 0..30
    @returns: dict with zoom, x, y, tile, east_px, south_px
    """
    zoom = _validate_zoom(zoom)
    lat, lon = _validate_wgs84(lat, lon)
    fx, fy = tile_float(lat, lon, zoom)
    n = float(1 << zoom)
    # Clamp into [0, n) so float noise near the Web Mercator poles never
    # yields an out-of-range tile index or offset outside [0, 256).
    eps = 1e-12
    fx = min(max(fx, 0.0), n - eps)
    fy = min(max(fy, 0.0), n - eps)
    tx, ty = int(fx), int(fy)
    east_px = (fx - tx) * WM_TILE_PX
    south_px = (fy - ty) * WM_TILE_PX
    if east_px < 0.0:
        east_px = 0.0
    if south_px < 0.0:
        south_px = 0.0
    if east_px >= WM_TILE_PX:
        east_px = WM_TILE_PX - eps
    if south_px >= WM_TILE_PX:
        south_px = WM_TILE_PX - eps
    return {
        'zoom': zoom,
        'x': tx,
        'y': ty,
        'tile': f'{zoom}/{tx}/{ty}',
        'east_px': east_px,
        'south_px': south_px,
    }


def wm_position_to_wgs84(
    zoom: int, x: int, y: int, east_px: float, south_px: float,
) -> dict:
    """XYZ tile + pixel offsets → WGS84 (no world required).

    @param zoom: integer zoom 0..30
    @param x: tile X
    @param y: tile Y
    @param east_px: pixels east of NW corner in [0, 256)
    @param south_px: pixels south of NW corner in [0, 256)
    @returns: dict with lat, lon
    """
    zoom = _validate_zoom(zoom)
    _validate_tile_indices(zoom, x, y)
    east_px = _validate_pixel_offset(east_px, 'east_px')
    south_px = _validate_pixel_offset(south_px, 'south_px')
    fx = x + east_px / WM_TILE_PX
    fy = y + south_px / WM_TILE_PX
    lat, lon = _tile_float_to_wgs84(fx, fy, zoom)
    return {'lat': lat, 'lon': lon}


def mc_to_wm_position(
    x: float, z: float, zoom: int, frame: WorldFrame | None = None,
) -> dict:
    """Minecraft column → XYZ tile code plus logical pixel offsets.

    @param x: Minecraft X
    @param z: Minecraft Z
    @param zoom: integer zoom 0..30
    @param frame: explicit frame, or None to use the selected active world
    @returns: dict with zoom, x, y, tile, east_px, south_px
    """
    zoom = _validate_zoom(zoom)
    x = _require_finite(x, 'mc x')
    z = _require_finite(z, 'mc z')
    lat, lon = mc_to_wgs84(x, z, _resolve_frame(frame))
    return wgs84_to_wm_position(lat, lon, zoom)


def wm_position_to_mc(
    zoom: int, x: int, y: int, east_px: float, south_px: float,
    frame: WorldFrame | None = None,
) -> dict:
    """XYZ tile + pixel offsets → Minecraft x,z (existing block rounding).

    @param zoom: integer zoom 0..30
    @param x: tile X
    @param y: tile Y
    @param east_px: pixels east of NW corner in [0, 256)
    @param south_px: pixels south of NW corner in [0, 256)
    @param frame: explicit frame, or None to use the selected active world
    @returns: dict with integer x, z
    """
    ll = wm_position_to_wgs84(zoom, x, y, east_px, south_px)
    mx, mz = wgs84_to_mc(ll['lat'], ll['lon'], _resolve_frame(frame))
    return {'x': mx, 'z': mz}


def mc_to_wm(
    x: float, z: float, zoom: int, frame: WorldFrame | None = None,
) -> tuple[int, int]:
    """Minecraft column → web-mercator tile indices (compatibility wrapper).

    @param x: Minecraft X
    @param z: Minecraft Z
    @param zoom: zoom level
    @param frame: optional frame
    @returns: (xtile, ytile)
    """
    pos = mc_to_wm_position(x, z, zoom, frame)
    return pos['x'], pos['y']


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


def convert_wm_batch(
    items: list[dict], frame: WorldFrame | None = None,
) -> list[dict]:
    """Convert a list of to-wm / from-wm requests, preserving order.

    Each item is a dict:

    - ``{"op": "to-wm", "zoom": N, "wgs84": {"lat", "lon"}}``
    - ``{"op": "to-wm", "zoom": N, "mc": {"x", "z"}}``
    - ``{"op": "from-wm", "tile": "z/x/y", "east_px", "south_px", "as": "wgs84"|"mc"}``

    @param items: batch requests
    @param frame: optional frame for Minecraft items
    @returns: one result dict per input item
    """
    if not items:
        return []
    out: list[dict] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f'batch item {i}: expected object')
        op = item.get('op')
        try:
            if op == 'to-wm':
                zoom = item['zoom']
                if 'wgs84' in item and item['wgs84'] is not None:
                    ll = item['wgs84']
                    out.append(wgs84_to_wm_position(ll['lat'], ll['lon'], zoom))
                elif 'mc' in item and item['mc'] is not None:
                    mc = item['mc']
                    out.append(mc_to_wm_position(mc['x'], mc['z'], zoom, frame))
                else:
                    raise ValueError('to-wm needs wgs84 or mc')
            elif op == 'from-wm':
                z, x, y = parse_wm(str(item['tile']))
                east = item['east_px']
                south = item['south_px']
                as_ = item.get('as', 'wgs84')
                if as_ == 'wgs84':
                    out.append(wm_position_to_wgs84(z, x, y, east, south))
                elif as_ == 'mc':
                    out.append(wm_position_to_mc(z, x, y, east, south, frame))
                else:
                    raise ValueError(f"from-wm as must be 'wgs84' or 'mc', got {as_!r}")
            else:
                raise ValueError(f"op must be 'to-wm' or 'from-wm', got {op!r}")
        except (KeyError, TypeError) as exc:
            raise ValueError(f'batch item {i}: {exc}') from exc
        except ValueError as exc:
            raise ValueError(f'batch item {i}: {exc}') from exc
    return out


# ---- CLI ---------------------------------------------------------------------

def _parse_pair(spec: str, label: str) -> tuple[float, float]:
    parts = [p.strip() for p in spec.split(',')]
    if len(parts) != 2:
        raise SystemExit(f'expected {label} got {spec!r}')
    return float(parts[0]), float(parts[1])


def _cli_frame(path: str | None) -> WorldFrame | None:
    """Load ``--frame PATH`` when given; otherwise None (active world on demand)."""
    if path is None:
        return None
    return load_frame(Path(path))


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2))


def _read_batch_json(path: str) -> list:
    """Load a JSON array from a file path or stdin (``-``)."""
    raw = sys.stdin.read() if path == '-' else Path(path).read_text()
    data = json.loads(raw)
    if not isinstance(data, list):
        raise SystemExit('batch-wm input must be a JSON array')
    return data


def main(argv: list[str] | None = None) -> int:
    """CLI entry for world-frame conversions.

    @param argv: optional argv override
    @returns: exit code
    """
    ap = argparse.ArgumentParser(
        description=(
            'World-frame coordinate conversions. Preferred XYZ interface: '
            'to-wm / from-wm / batch-wm (logical 256-px east_px/south_px from NW). '
            'Tile-image fetch and evidence sheets are separate tools '
            '(e.g. google_aerial sheet).'
        ),
    )
    sub = ap.add_subparsers(dest='cmd', required=True)

    sub.add_parser('info', help='print loaded frame as JSON')

    p = sub.add_parser('to-mc', help='to Minecraft x,z')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--osgb', help='easting,northing')
    g.add_argument('--wgs84', help='lat,lon')
    g.add_argument('--epsg', nargs=2, metavar=('SRS', 'XY'),
                   help='source EPSG and x,y in that CRS (e.g. EPSG:4326 lon,lat)')
    p.add_argument('--frame', help='world-frame.json path (else active world)')

    p = sub.add_parser('to-osgb', help='to OSGB easting,northing')
    p.add_argument('--mc', required=True, help='x,z')
    p.add_argument('--frame', help='world-frame.json path (else active world)')

    p = sub.add_parser('to-wgs84', help='to WGS84 lat,lon')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--mc', help='x,z')
    g.add_argument('--osgb', help='easting,northing')
    p.add_argument('--frame', help='world-frame.json path (else active world)')

    p = sub.add_parser('to-cell', help='coverage cell of an MC point')
    p.add_argument('--mc', required=True, help='x,z')

    p = sub.add_parser(
        'to-wm',
        help='WGS84 or MC → XYZ tile + east_px/south_px (JSON)',
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--wgs84', help='lat,lon (no world required)')
    g.add_argument('--mc', help='x,z (needs --frame or active world)')
    p.add_argument('--zoom', type=int, default=18, help='integer zoom 0..30')
    p.add_argument('--frame', help='world-frame.json for --mc')

    p = sub.add_parser(
        'from-wm',
        help='XYZ tile + east_px/south_px → WGS84 or MC (JSON)',
    )
    p.add_argument('--tile', required=True, help='z/x/y')
    p.add_argument('--east-px', type=float, required=True, dest='east_px',
                   help='pixels east of NW corner [0,256)')
    p.add_argument('--south-px', type=float, required=True, dest='south_px',
                   help='pixels south of NW corner [0,256)')
    p.add_argument('--as', dest='as_', choices=('wgs84', 'mc'), default='wgs84',
                   help='output CRS (mc needs --frame or active world)')
    p.add_argument('--frame', help='world-frame.json for --as mc')

    p = sub.add_parser(
        'batch-wm',
        help='JSON array of to-wm/from-wm ops from --file or stdin (-)',
    )
    p.add_argument('--file', required=True,
                   help='path to JSON array, or - for stdin')
    p.add_argument('--frame', help='world-frame.json for Minecraft items')

    p = sub.add_parser('y', help='metres AOD ↔ block Y')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--aod', type=float, help='metres above datum → Y')
    g.add_argument('--block-y', type=int, dest='block_y', help='block Y → metres AOD')
    p.add_argument('--frame', help='world-frame.json path (else active world)')

    args = ap.parse_args(argv)

    # Lat/lon-only WM paths must not force-load a world frame.
    needs_default_frame = args.cmd in (
        'info', 'to-mc', 'to-osgb', 'to-wgs84', 'to-cell', 'y',
    ) or (
        args.cmd == 'to-wm' and args.mc is not None
    ) or (
        args.cmd == 'from-wm' and args.as_ == 'mc'
    ) or (
        args.cmd == 'batch-wm'  # may include mc; resolve per item via optional --frame
    )

    frame_override = _cli_frame(getattr(args, 'frame', None))
    f: WorldFrame | None = frame_override
    if f is None and needs_default_frame and args.cmd != 'batch-wm':
        if args.cmd == 'to-wgs84' and args.osgb and not args.mc:
            f = None  # OSGB→WGS84 needs no frame
        elif args.cmd in ('to-wm', 'from-wm'):
            pass  # resolved below via helpers
        else:
            f = load_frame()

    if args.cmd == 'info':
        f = f or load_frame()
        fp = frame_path()
        root = _require_root()
        print(json.dumps({
            'path': str(fp.relative_to(root)) if fp.exists() else None,
            'place': f.data.get('place'),
            'crs': f.crs,
            'anchor': f.anchor,
            'offsets': {'easting': f.offset_e, 'northing': f.offset_n},
            'bounds_mc': f.bounds_mc,
            'cell': f.data.get('cell') or {
                'size': cellmath.cell_size(),
                'origin': list(cellmath.origin_cell()),
            },
            'y_offset': f.y_offset,
            'elevation_plugin': f.data.get('elevation_plugin'),
        }, indent=2))
        return 0

    if args.cmd == 'to-mc':
        f = f or load_frame()
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
        f = f or load_frame()
        x, z = _parse_pair(args.mc, 'x,z')
        e, n = mc_to_osgb(x, z, f)
        print(f'OSGB E{e:.1f} N{n:.1f}')
        return 0

    if args.cmd == 'to-wgs84':
        if args.mc:
            f = f or load_frame()
            x, z = _parse_pair(args.mc, 'x,z')
            lat, lon = mc_to_wgs84(x, z, f)
        else:
            e, n = _parse_pair(args.osgb, 'easting,northing')
            lat, lon = osgb_to_wgs84(e, n)
        print(f'WGS84 {lat:.6f},{lon:.6f}')
        return 0

    if args.cmd == 'to-cell':
        f = f or load_frame()
        x, z = (int(v) for v in _parse_pair(args.mc, 'x,z'))
        tx, tz = cell_of(x, z)
        print(f'cell {tx},{tz}')
        return 0

    if args.cmd == 'to-wm':
        try:
            if args.wgs84:
                lat, lon = _parse_pair(args.wgs84, 'lat,lon')
                _print_json(wgs84_to_wm_position(lat, lon, args.zoom))
            else:
                x, z = _parse_pair(args.mc, 'x,z')
                _print_json(mc_to_wm_position(x, z, args.zoom, frame_override))
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        return 0

    if args.cmd == 'from-wm':
        try:
            z, x, y = parse_wm(args.tile)
            if args.as_ == 'wgs84':
                _print_json(wm_position_to_wgs84(z, x, y, args.east_px, args.south_px))
            else:
                _print_json(wm_position_to_mc(
                    z, x, y, args.east_px, args.south_px, frame_override,
                ))
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        return 0

    if args.cmd == 'batch-wm':
        try:
            items = _read_batch_json(args.file)
            _print_json(convert_wm_batch(items, frame_override))
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        except json.JSONDecodeError as exc:
            raise SystemExit(f'invalid JSON: {exc}') from exc
        return 0

    if args.cmd == 'y':
        f = f or load_frame()
        if args.aod is not None:
            print(f'Y {metres_aod_to_y(args.aod, f)}  (y_offset={f.y_offset})')
        else:
            print(f'AOD {y_to_metres_aod(args.block_y, f):.1f} m  (y_offset={f.y_offset})')
        return 0

    return 2


if __name__ == '__main__':
    raise SystemExit(main())
