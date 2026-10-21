#!/usr/bin/env python3
"""DEFRA LIDAR composite DTM / DSM: our own source, same method as Geocraft (lib/Elevation/UKDEFRA.pm).

Catalogue provider id: ``ukdefra-lidar`` (aliases ``ukdefra-dtm`` / ``ukdefra-dsm`` in
``config/system-providers.json``). DTM and last-return DSM are one feed, two products.

Why: this fetches the open DEFRA (Environment Agency) 1 m LIDAR composites itself, so heights exist for
any place the data covers. England only: Scotland and Wales publish their own LIDAR, which would be a second
source with the same interface (``crs``, ``sample(model, e, n)``).

Method (matches Geocraft, so a column here equals the column Geocraft drew):
  catalogue   POST a GeoJSON polygon (WGS84) to the DEFRA tiles catalogue; it lists the products per 5 km tile.
  tiles       one zip per 5 km tile and product, holding a 5000 x 5000 float32 GeoTIFF in EPSG:27700 at 1 m.
  products    DTM = lidar_composite_dtm, DSM = lidar_composite_last_return_dsm (Geocraft's choice),
              DSM_FIRST = lidar_composite_first_return_dsm (sees treetops and roofs rather than gaps).
  sampling    Geocraft rounds every pixel to a whole metre (gdal_translate -ot Int32), treats each pixel value
              as sitting at the pixel's south-west corner, and bilinearly interpolates between four corners.
              Anything below 0 (including no-data) becomes 0. ``geocraft=True`` (default) does exactly that;
              ``geocraft=False`` keeps float heights and NaN for no-data.
  blocks      ground Y = SEA_LEVEL + int(DTM); diff / feature height = int(DSM - DTM); top Y = ground Y + diff.
  noise       Unfiltered DSM can be noisy: strip cars/trains; keep street furniture where practical
              (rules/evidence-order.md). For maps of DTM, DSM or their difference use
              shims/lidar_query.py (--layers dtm,dsm,diff).

CRS: DEFRA data is British National Grid, so this module speaks eastings/northings. The Minecraft frame is the
only place that knows the world offsets (scripts/osgb.py, ``mc_heights`` below). A world elsewhere in the
world needs its own source and frame; nothing here assumes Southampton.

  python3 shims/defra_lidar.py tiles --cell 48,-44          # which 5 km tiles, cached or not
  python3 shims/defra_lidar.py fetch --cell 48,-44          # download what is missing (large: ~75 MB a tile)
  python3 shims/defra_lidar.py probe 6140,-3760             # DTM, DSM, diff (DSM−DTM) and Y at one column
  # Area maps / highs-lows: shims/lidar_query.py --cell TX,TZ --layers dtm,dsm,diff

Cached under downloads/defra-lidar/ (gitignored). Needs Pillow and numpy (already in .venv) and gdaltransform
on PATH for the catalogue polygon. Open Government Licence v3: attribute "Contains Environment Agency
information (c) Environment Agency and/or database right".
"""
from __future__ import annotations

import io
import json
import math
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

import cellmath
from atomic_json import write_json
from script_env import ROOT, make_parser  # noqa: E402

Image.MAX_IMAGE_PIXELS = None           # a DEFRA tile is 25 M pixels

DEFRA_CRS = 'EPSG:27700'
CACHE = ROOT / 'downloads' / 'defra-lidar'
CATALOGUE_URL = 'https://environment.data.gov.uk/backend/catalog/api/tiles/collections/survey/search'
KEY = 'subscription-key=public'         # the public key the DEFRA data portal itself uses
USER_AGENT = 'Southampton-Refine/defra_lidar'
TILE_M = 5000                           # DEFRA 5 km tiles
INSET_M = 50                            # catalogue polygon sits this far inside the tile, so neighbours are not returned
PRODUCTS = {
    'DTM': 'lidar_composite_dtm',
    'DSM': 'lidar_composite_last_return_dsm',
    'DSM_FIRST': 'lidar_composite_first_return_dsm',
}
RESOLUTION = '1'                        # metres; Geocraft's sampling assumes 1


@dataclass
class Tile:
    """One cached GeoTIFF: ``e0`` west edge, ``n_top`` north edge, square pixels of ``cell`` metres."""
    path: Path
    e0: float
    n_top: float
    ncols: int
    nrows: int
    cell: float
    nodata: float | None
    _data: np.ndarray | None = None

    @property
    def n0(self) -> float:
        return self.n_top - self.nrows * self.cell

    def data(self) -> np.ndarray:
        """float32 array [row, col] with no-data as NaN, decoded once."""
        if self._data is None:
            with Image.open(self.path) as im:
                a = np.array(im, dtype=np.float32)
            bad = ~np.isfinite(a) | (a < -1e30)
            if self.nodata is not None:
                bad |= a == np.float32(self.nodata)
            a[bad] = np.nan
            self._data = a
        return self._data


def read_tile(path: Path) -> Tile:
    """Georeferencing of a GeoTIFF from its tags, without decoding the pixels."""
    with Image.open(path) as im:
        scale = im.tag_v2.get(33550)                 # ModelPixelScale
        tie = im.tag_v2.get(33922)                   # ModelTiepoint: (i, j, k, E, N, 0)
        nodata = im.tag_v2.get(42113)                # GDAL_NODATA, text
        if not scale or not tie:
            raise ValueError(f'{path.name}: not a georeferenced GeoTIFF')
        if abs(scale[0] - scale[1]) > 1e-9:
            raise ValueError(f'{path.name}: pixels are not square')
        ncols, nrows = im.size
    return Tile(path, float(tie[3]), float(tie[4]), ncols, nrows, float(scale[0]),
                float(nodata) if nodata else None)


def tile_origin(e: float, n: float) -> tuple[int, int]:
    """South-west corner of the 5 km DEFRA tile holding (e, n)."""
    return int(math.floor(e / TILE_M) * TILE_M), int(math.floor(n / TILE_M) * TILE_M)


def tiles_for(e0: float, n0: float, e1: float, n1: float) -> list[tuple[int, int]]:
    """South-west corners of every 5 km tile touching the E/N box (plus one metre east and north for bilinear corners)."""
    (a, b), (c, d) = tile_origin(e0, n0), tile_origin(e1 + 1, n1 + 1)
    return [(e, n) for e in range(a, c + 1, TILE_M) for n in range(b, d + 1, TILE_M)]


def _http(url: str, data: bytes | None = None, headers: dict | None = None, tries: int = 3) -> bytes:
    req = urllib.request.Request(url, data=data, headers={'User-Agent': USER_AGENT, **(headers or {})})
    for k in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError):
            if k == tries - 1:
                raise
            time.sleep(2 ** k)
    raise AssertionError('unreachable')


def catalogue_polygon(e: int, n: int) -> dict:
    """GeoJSON polygon (lon, lat) just inside the 5 km tile at south-west corner (e, n)."""
    from geodetic import osgb_batch_to_wgs84
    a, b, c, d = e + INSET_M, n + INSET_M, e + TILE_M - INSET_M, n + TILE_M - INSET_M
    ll = osgb_batch_to_wgs84([(a, b), (c, b), (c, d), (a, d), (a, b)])
    return {'type': 'Polygon', 'coordinates': [[[lon, lat] for lat, lon in ll]]}


def pick_uri(catalogue: dict, model: str) -> str | None:
    """Download URL for ``model`` at 1 m from a catalogue answer: the latest year, or None if not published here."""
    want = PRODUCTS[model]
    found = {}
    for r in catalogue.get('results', []):
        if r['product']['id'] == want and r['resolution']['id'] == RESOLUTION:
            found[r['year']['id']] = r['uri']
    return f'{found[max(found)]}?{KEY}' if found else None


class DefraLidar:
    """DEFRA composite LIDAR by easting/northing, fetching and caching tiles as needed.

    @param cache: directory for catalogue answers and tiles
    @param download: fetch missing tiles; False raises LookupError for them instead (tests, offline work)
    """
    crs = DEFRA_CRS

    def __init__(self, cache: Path = CACHE, download: bool = True):
        self.cache = Path(cache)
        self.download = download
        self._tiles: dict[str, dict[tuple[int, int], Tile]] = {}

    # ---- cache -----------------------------------------------------------------------------------------------

    def _dir(self, model: str) -> Path:
        return self.cache / model

    def tiles(self, model: str) -> dict[tuple[int, int], Tile]:
        """Cached tiles of one model keyed by south-west corner (re-read when files are added)."""
        d = self._dir(model)
        files = sorted(d.glob('*.tif')) if d.is_dir() else []
        got = self._tiles.get(model, {})
        have = {t.path for t in got.values()}
        if have != set(files):
            fresh = {}
            for p in files:
                t = next((t for t in got.values() if t.path == p), None) or read_tile(p)
                fresh[(int(round(t.e0)), int(round(t.n0)))] = t
            self._tiles[model] = got = fresh
        return got

    def missing(self, model: str, e0: float, n0: float, e1: float, n1: float) -> list[tuple[int, int]]:
        """Tiles under the box that are not cached for ``model``."""
        have = self.tiles(model)
        return [t for t in tiles_for(e0, n0, e1, n1) if t not in have]

    # ---- fetching --------------------------------------------------------------------------------------------

    def catalogue(self, e: int, n: int) -> dict:
        """Catalogue answer for the tile at (e, n), cached as catalogue/<E>E-<N>N.json."""
        p = self.cache / 'catalogue' / f'{e}E-{n}N.json'
        if p.is_file():
            return json.loads(p.read_text())
        body = json.dumps(catalogue_polygon(e, n)).encode()
        raw = _http(CATALOGUE_URL, body, {'Content-type': 'application/geo+json'})
        answer = json.loads(raw)                 # parse before caching so an error page is never stored
        if 'results' not in answer:
            raise RuntimeError(f'DEFRA catalogue gave no results for {e},{n}: {raw[:200]!r}')
        write_json(p, answer)
        return answer

    def fetch_tile(self, model: str, e: int, n: int) -> Path | None:
        """Download the zip for one tile and unpack its GeoTIFF; None when DEFRA publishes none there (sea, Wales)."""
        uri = pick_uri(self.catalogue(e, n), model)
        if uri is None:
            return None
        print(f'  {model} {e}E-{n}N  {uri.split("?")[0]}', file=sys.stderr)
        z = zipfile.ZipFile(io.BytesIO(_http(uri)))
        out = None
        for name in z.namelist():
            if name.lower().endswith('.tif'):
                dest = self._dir(model) / Path(name).name      # basename only: never trust paths inside a zip
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(dest.name + '.part')
                tmp.write_bytes(z.read(name))
                tmp.replace(dest)
                out = dest
        self._tiles.pop(model, None)
        return out

    def ensure(self, e0: float, n0: float, e1: float, n1: float, models=('DTM', 'DSM')) -> list[tuple[str, int, int]]:
        """Fetch every missing tile under the box. Returns the (model, e, n) with no data published."""
        absent = []
        for model in models:
            for e, n in self.missing(model, e0, n0, e1, n1):
                if not self.download:
                    raise LookupError(f'{model} tile {e}E-{n}N is not cached and download is off')
                if self.fetch_tile(model, e, n) is None:
                    absent.append((model, e, n))
        return absent

    # ---- sampling --------------------------------------------------------------------------------------------

    def _corners(self, model: str, ce: np.ndarray, cn: np.ndarray) -> np.ndarray:
        """Pixel value at integer corner (ce, cn): the pixel spanning [ce, ce+1) x [cn, cn+1). NaN where no tile."""
        out = np.full(ce.shape, np.nan, dtype=np.float64)
        for (te, tn), t in self.tiles(model).items():
            if t.cell != 1:
                raise ValueError(f'{t.path.name}: {t.cell} m pixels, sampling needs 1 m')
            col, row = ce - int(t.e0), int(t.n_top) - cn - 1
            m = (col >= 0) & (col < t.ncols) & (row >= 0) & (row < t.nrows)
            if m.any():
                out[m] = t.data()[row[m], col[m]]
        return out

    def sample(self, model: str, e, n, geocraft: bool = True) -> np.ndarray:
        """Height of ``model`` ('DTM', 'DSM', 'DSM_FIRST') at eastings ``e`` and northings ``n`` (arrays or scalars).

        Bilinear between the four surrounding corners, like Geocraft's raw_en. With ``geocraft`` the pixels are
        rounded to whole metres first and anything below 0 or without data is 0; without it heights are float
        and no-data is NaN. Tiles must already be cached (``ensure``).
        """
        e, n = np.broadcast_arrays(np.asarray(e, dtype=np.float64), np.asarray(n, dtype=np.float64))
        ce, cn = np.floor(e).astype(np.int64), np.floor(n).astype(np.int64)
        fe, fn = e - ce, n - cn

        def corner(de, dn):
            v = self._corners(model, ce + de, cn + dn)
            return np.floor(v + 0.5) if geocraft else v

        sw, nw, ne, se = corner(0, 0), corner(0, 1), corner(1, 1), corner(1, 0)
        south = sw + (se - sw) * fe
        north = nw + (ne - nw) * fe
        h = south + (north - south) * fn
        if geocraft:
            h = np.where(np.isfinite(h) & (h > 0), h, 0.0)
        return h


# Type-facing name for scripts/lidar_provider.py (catalogue modules.tiles).
LidarTiles = DefraLidar


# ---- the Minecraft frame (Southampton2023 offsets from scripts/osgb.py) ----------------------------------------

def mc_box_en(x0: int, z0: int, x1: int, z1: int) -> tuple[float, float, float, float]:
    """E/N box (south-west, north-east) of an inclusive block box; north is -Z."""
    from osgb import to_osgb
    ea, na = to_osgb(x0, z1)
    eb, nb = to_osgb(x1, z0)
    return ea, na, eb, nb


def mc_heights(lidar: DefraLidar, x0: int, z0: int, x1: int, z1: int, sea_level: int | None = None,
               geocraft: bool = True) -> dict:
    """Per-column heights for an inclusive block box, arrays indexed [x - x0, z - z0].

    @param sea_level: block Y of DTM 0 (default: y_offset from config/world-frame.json)
    @returns: dtm, dsm (metres), diff and feature (same array: int(dsm - dtm), never below 0),
              ground_y (sea_level + int(dtm)), top_y (ground_y + diff)
    """
    from world_frame import load_frame, mc_grid_to_en
    f = load_frame()
    if sea_level is None:
        sea_level = f.y_offset
    xs, zs = np.meshgrid(np.arange(x0, x1 + 1), np.arange(z0, z1 + 1), indexing='ij')
    e, n = mc_grid_to_en(xs, zs, f)
    dtm = lidar.sample('DTM', e, n, geocraft)
    dsm = lidar.sample('DSM', e, n, geocraft)
    diff = np.maximum(np.trunc(dsm - dtm), 0)         # DSM can sit below DTM in the composites: nothing stands there
    ground = sea_level + np.trunc(dtm)
    if geocraft:                                      # no NaN possible: whole-block integers
        diff, ground = diff.astype(np.int64), ground.astype(np.int64)
    return {'dtm': dtm, 'dsm': dsm, 'diff': diff, 'feature': diff, 'ground_y': ground, 'top_y': ground + diff}


def _bbox_from_args(a) -> tuple[int, int, int, int]:
    if a.cell:
        return cellmath.bounds(cellmath.parse_cell(a.cell), a.margin)
    parts = [int(p) for p in a.bbox_mc.split(',')]
    if len(parts) != 4:
        raise SystemExit('--bbox-mc wants x0,z0,x1,z1')
    return min(parts[0], parts[2]), min(parts[1], parts[3]), max(parts[0], parts[2]), max(parts[1], parts[3])


def main(argv: list[str] | None = None) -> int:
    ap = make_parser(__doc__)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('tiles', 'fetch'):
        p = sub.add_parser(name)
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument('--cell', help='coverage cell tx,tz')
        g.add_argument('--bbox-mc', help='x0,z0,x1,z1 inclusive blocks')
        p.add_argument('--margin', type=int, default=0, help='grow the cell by this many blocks')
        p.add_argument('--cache', type=Path, default=CACHE)
    pr = sub.add_parser('probe')
    pr.add_argument('xz', help='x,z block column')
    pr.add_argument('--cache', type=Path, default=CACHE)
    pr.add_argument('--float', action='store_true', help='float heights, not Geocraft whole-metre rounding')
    a = ap.parse_args(argv)

    if a.cmd == 'probe':
        x, z = (int(v) for v in a.xz.split(','))
        lidar = DefraLidar(a.cache, download=False)
        try:
            lidar.ensure(*mc_box_en(x, z, x, z))
        except LookupError as exc:
            print(f'{exc}; run fetch first', file=sys.stderr)
            return 1
        h = mc_heights(lidar, x, z, x, z, geocraft=not a.float)
        # Prefer diff in the printed keys; feature stays for older readers (same value).
        keys = ('dtm', 'dsm', 'diff', 'feature', 'ground_y', 'top_y')
        print(json.dumps({k: float(h[k][0, 0]) for k in keys} | {'x': x, 'z': z}))
        return 0

    box = _bbox_from_args(a)
    en = mc_box_en(*box)
    lidar = DefraLidar(a.cache, download=a.cmd == 'fetch')
    if a.cmd == 'tiles':
        for model in ('DTM', 'DSM'):
            gone = set(lidar.missing(model, *en))
            for t in tiles_for(*en):
                print(f'{model} {t[0]}E-{t[1]}N  {"MISSING" if t in gone else "cached"}')
        return 0
    if a.cmd == 'fetch':
        absent = lidar.ensure(*en)
        for m, e, n in absent:
            print(f'no {m} published for {e}E-{n}N', file=sys.stderr)
        return 1 if absent else 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
