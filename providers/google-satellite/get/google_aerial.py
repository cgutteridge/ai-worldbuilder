#!/usr/bin/env python3
"""Google satellite tiles for one cell or one spot: a downloaded PNG sheet with the MC grid drawn on it.

Always look at this for every cell (place-sources provider ``google-satellite``; alias ``google``): it is often
clearer than keepable aerial. Uses the unofficial plain-image tile URL: download the few tiles needed, stitch
them, overlay the MC grid, write a PNG under ``instance/google-aerial/`` (gitignored), and ``Read`` that file.
Do not open a browser for this. Do not hash or commit the PNGs into ``reference/``. Keep grids small (ToS).

  python3 shims/google_aerial.py sheet --cell 49,-42            # whole cell, z19
  python3 shims/google_aerial.py sheet --at 6379,-5277          # around a building, z20, radius 30 blocks
  python3 shims/google_aerial.py sheet --at 6379,-5277 --radius 60 --zoom 19
  python3 shims/google_aerial.py sheet --cell 49,-42 --outlines # + OSM building outlines (red, way-id tail)
  python3 shims/google_aerial.py sheet --at 6180,-5606 --marks cells.json   # colour listed MC cells
  python3 shims/google_aerial.py url --at 6379,-5277 --zoom 19  # the single tile URL

Sheet: instance/google-aerial/<name>.png (gitignored). After looking, record once:
  cell_sources.py --cell TX,TZ --record google-satellite --result found --note "what it shows"
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from script_env import ROOT  # noqa: E402

from coverage import cell_bbox  # noqa: E402
from world_frame import mc_batch_to_wgs84, tile_float  # noqa: E402

BASE = 'https://mt1.google.com/vt/lyrs=s'
SHEETS = ROOT / 'instance' / 'google-aerial'
TILE = 256
MAX_TILES = 6                       # per side: one cell at z19 is 3-4; keeps the request count small (ToS)
GRID = 10                           # MC blocks between grid lines
HDR = {'Referer': 'https://www.google.com/', 'User-Agent': 'Mozilla/5.0'}


def mc_to_tile(points: list[tuple[float, float]], zoom: int) -> list[tuple[float, float]]:
    """Fractional tile coordinates of MC (x, z) points.

    @param points: MC (x, z) pairs
    @param zoom: zoom level
    @returns: one (tile_x, tile_y) per point
    """
    return [tile_float(lat, lon, zoom) for lat, lon in mc_batch_to_wgs84(list(points))]


def tile_url(x: int, y: int, zoom: int) -> str:
    """Satellite tile URL (download via ``fetch_tile``; do not open in a browser).

    @param x: tile x
    @param y: tile y
    @param zoom: zoom level
    @returns: URL
    """
    return f'{BASE}&x={x}&y={y}&z={zoom}'


def fetch_tile(x: int, y: int, zoom: int) -> Image.Image:
    """Download one satellite tile as an RGB image.

    @param x: tile x
    @param y: tile y
    @param zoom: zoom level
    @returns: 256×256 RGB image
    """
    req = urllib.request.Request(tile_url(x, y, zoom), headers=HDR)
    raw = urllib.request.urlopen(req, timeout=30).read()
    return Image.open(io.BytesIO(raw)).convert('RGB')


def tile_range(bbox: tuple[int, int, int, int], zoom: int) -> tuple[int, int, int, int]:
    """Tile indices (x0, y0, x1, y1, inclusive) covering an MC bbox.

    @param bbox: MC x0, z0, x1, z1
    @param zoom: zoom level
    @returns: tile index box
    @raises ValueError: when the box needs more than MAX_TILES tiles a side
    """
    x0, z0, x1, z1 = bbox
    pts = mc_to_tile([(x0, z0), (x1, z0), (x0, z1), (x1, z1)], zoom)
    tx0, tx1 = int(min(p[0] for p in pts)), int(max(p[0] for p in pts))
    ty0, ty1 = int(min(p[1] for p in pts)), int(max(p[1] for p in pts))
    if tx1 - tx0 + 1 > MAX_TILES or ty1 - ty0 + 1 > MAX_TILES:
        raise ValueError(f'{tx1 - tx0 + 1}x{ty1 - ty0 + 1} tiles at z{zoom}: lower --zoom or --radius (max {MAX_TILES} a side)')
    return tx0, ty0, tx1, ty1


def grid_lines(bbox: tuple[int, int, int, int]) -> list[dict]:
    """MC grid lines every GRID blocks inside a bbox, as endpoint pairs.

    @param bbox: MC x0, z0, x1, z1
    @returns: [{axis, value, a: (x, z), b: (x, z)}] for x lines then z lines
    """
    x0, z0, x1, z1 = bbox
    out = [{'axis': 'x', 'value': v, 'a': (v, z0), 'b': (v, z1)} for v in range(-(-x0 // GRID) * GRID, x1 + 1, GRID)]
    out += [{'axis': 'z', 'value': v, 'a': (x0, v), 'b': (x1, v)} for v in range(-(-z0 // GRID) * GRID, z1 + 1, GRID)]
    return out


def osm_outlines(bbox: tuple[int, int, int, int]) -> list[dict]:
    """OSM building outlines with a vertex inside a bbox, labelled by the last 3 digits of the way id (as aerial_annot.py).

    @param bbox: MC x0, z0, x1, z1
    @returns: [{'ring': [(x, z), ...], 'label': str}]
    """
    from osm_footprints import OSMIndex
    x0, z0, x1, z1 = bbox
    out = []
    for rings, props in OSMIndex().buildings:
        ring = rings[0]
        if any(x0 <= x <= x1 and z0 <= z <= z1 for x, z in ring):
            out.append({'ring': list(ring), 'label': str(props.get('osm_way_id') or props.get('osm_id') or '')[-3:]})
    return out


def _parse_rgb(col: str) -> tuple[int, int, int]:
    """Parse ``#rrggbb`` / ``#rgb`` (with or without ``#``) to an RGB triple.

    @param col: colour string
    @returns: (r, g, b)
    """
    c = col.lstrip('#')
    if len(c) == 3:
        c = ''.join(ch * 2 for ch in c)
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _font(size: int = 11):
    """Load a small bitmap font for grid labels.

    @param size: requested point size (best-effort)
    @returns: a PIL font
    """
    try:
        return ImageFont.truetype('DejaVuSans.ttf', size)
    except OSError:
        return ImageFont.load_default()


def sheet_image(bbox: tuple[int, int, int, int], zoom: int, scale: float = 1.0,
                outlines: list[dict] | None = None, marks: list | None = None) -> Image.Image:
    """Stitch downloaded tiles and draw the bbox outline plus labelled MC grid.

    @param bbox: MC x0, z0, x1, z1 to outline (tiles cover it)
    @param zoom: zoom level
    @param scale: resize factor for each 256 px tile (1 = native)
    @param outlines: optional building outlines (osm_outlines()), drawn red with a yellow label at the centroid
    @param marks: optional [(x, z, colour)] MC cells drawn as translucent squares
    @returns: RGB image
    """
    tx0, ty0, tx1, ty1 = tile_range(bbox, zoom)
    s = max(1, int(round(TILE * scale)))
    w, h = (tx1 - tx0 + 1) * s, (ty1 - ty0 + 1) * s
    im = Image.new('RGBA', (w, h))
    for y in range(ty0, ty1 + 1):
        for x in range(tx0, tx1 + 1):
            tile = fetch_tile(x, y, zoom)
            if s != TILE:
                tile = tile.resize((s, s), Image.Resampling.BILINEAR)
            im.paste(tile, ((x - tx0) * s, (y - ty0) * s))

    draw = ImageDraw.Draw(im)
    font = _font(11)
    x0, z0, x1, z1 = bbox
    lines = grid_lines(bbox)
    ends = [p for ln in lines for p in (ln['a'], ln['b'])] + [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]
    px = [((fx - tx0) * s, (fy - ty0) * s) for fx, fy in mc_to_tile(ends, zoom)]

    for i, ln in enumerate(lines):
        (ax, ay), (bx, by) = px[2 * i], px[2 * i + 1]
        col = (51, 255, 102, 140) if ln['axis'] == 'x' else (51, 204, 255, 140)
        draw.line([(ax, ay), (bx, by)], fill=col, width=1)
        draw.text((ax + 2, ay + 1), f"{ln['axis']}{ln['value']}", fill=col[:3], font=font)

    if marks:
        corners = [(x + a, z + b) for x, z, _c in marks for a, b in ((0, 0), (1, 0), (1, 1), (0, 1))]
        cp = [((fx - tx0) * s, (fy - ty0) * s) for fx, fy in mc_to_tile(corners, zoom)]
        for i, (_x, _z, col) in enumerate(marks):
            q = cp[4 * i:4 * i + 4]
            r, g, b = _parse_rgb(col)
            draw.polygon(q, fill=(r, g, b, 115))

    if outlines:
        flat = [p for o in outlines for p in o['ring']]
        proj = [((fx - tx0) * s, (fy - ty0) * s) for fx, fy in mc_to_tile(flat, zoom)]
        i = 0
        for o in outlines:
            pts = proj[i:i + len(o['ring'])]
            i += len(o['ring'])
            draw.polygon(pts, outline=(255, 51, 51, 255))
            cx = sum(x for x, _ in pts) / len(pts)
            cy = sum(y for _, y in pts) / len(pts)
            draw.text((cx, cy), o['label'], fill=(255, 255, 0), font=font, anchor='mm')

    c = px[-4:]
    draw.polygon([c[0], c[1], c[2], c[3]], outline=(255, 51, 51, 255), width=2)
    return im.convert('RGB')


def write_sheet(name: str, bbox: tuple[int, int, int, int], zoom: int, scale: float,
                outlines: list[dict] | None = None, marks: list | None = None) -> Path:
    """Download tiles, compose the sheet, write PNG under instance/google-aerial/.

    @param name: file stem
    @param bbox: MC bbox
    @param zoom: zoom level
    @param scale: tile resize factor
    @param outlines: optional building outlines (osm_outlines())
    @param marks: optional [(x, z, colour)] cells to colour
    @returns: path to the PNG
    """
    SHEETS.mkdir(parents=True, exist_ok=True)
    out = SHEETS / f'{name}.png'
    sheet_image(bbox, zoom, scale, outlines, marks).save(out, format='PNG')
    return out


def _bbox(a: argparse.Namespace) -> tuple[tuple[int, int, int, int], str, int]:
    """Resolve --cell / --at into (bbox, file stem, default zoom).

    @param a: parsed args
    @returns: (bbox, name, zoom)
    """
    if a.cell:
        tx, tz = (int(v) for v in a.cell.split(','))
        return cell_bbox(tx, tz, tx, tz), f'{tx}_{tz}', a.zoom or 19
    x, z = (int(v) for v in a.at.split(','))
    r = a.radius
    return (x - r, z - r, x + r, z + r), f'at_{x}_{z}', a.zoom or 20


def main() -> int:
    """CLI entry point.

    @returns: exit code
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=('sheet', 'url'))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--cell', help='tx,tz')
    g.add_argument('--at', help='MC x,z')
    ap.add_argument('--radius', type=int, default=30, help='blocks either side of --at (default 30)')
    ap.add_argument('--zoom', type=int, help='default 19 for a cell, 20 for --at')
    ap.add_argument('--scale', type=float, default=1.0, help='tile resize factor (1 = native 256 px)')
    ap.add_argument('--outlines', action='store_true', help='draw the OSM building outlines (red, way-id tail) on the sheet')
    ap.add_argument('--marks', help='JSON file [[x, z, "#rrggbb"], ...]: MC cells to colour on the sheet')
    a = ap.parse_args()
    bbox, name, zoom = _bbox(a)
    if a.cmd == 'url':
        cx, cz = (bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2
        fx, fy = mc_to_tile([(cx, cz)], zoom)[0]
        print(tile_url(int(fx), int(fy), zoom))
        return 0
    try:
        outlines = osm_outlines(bbox) if a.outlines else None
        marks = [tuple(m) for m in json.loads(Path(a.marks).read_text())] if a.marks else None
        path = write_sheet(name + ('_osm' if a.outlines else '') + ('_marks' if marks else ''), bbox, zoom,
                           a.scale, outlines, marks)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    tx0, ty0, tx1, ty1 = tile_range(bbox, zoom)
    print(f'{(tx1 - tx0 + 1) * (ty1 - ty0 + 1)} tiles at z{zoom}, bbox {bbox}')
    print(f'Read: {path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
