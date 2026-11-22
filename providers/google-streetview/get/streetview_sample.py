#!/usr/bin/env python3
"""Sample Street View frames for one 128×128 coverage cell and keep the useful ones.

Single-step method (reference/SOURCES.txt): the unofficial thumbnail URL takes
``ll=<lat>,<lon>`` and returns the nearest pano's frame, so no pano id lookup.

``sample`` downloads thumbnails, writes a PNG contact sheet under
``instance/streetview/`` (gitignored), and prints frame ids so you can ``Read`` the
sheet and ``keep`` the useful ones. ``keep`` saves ONLY those frames to
``reference/photos/streetview/<tx>_<tz>/<id>.jpg`` (local, gitignored, per user
decision 2026-10-14), logs each in ``reference/manifest.jsonl``, and records URL +
params + note in a small committed JSON file. Do not open a browser for this.

  python3 shims/streetview_sample.py sample --cell 48,-44            # 3×3 points × 4 yaws
  python3 shims/streetview_sample.py sample --cell 48,-44 --grid 4 --yaws 0,90,180,270
  python3 shims/streetview_sample.py keep --cell 48,-44 --ids g01-y90,g11-y180 --note "brick frontage"
  python3 shims/streetview_sample.py show --cell 48,-44              # sheet of kept frames only
  python3 shims/streetview_sample.py list --cell 48,-44

Sheet: instance/streetview/<tx>_<tz>-sheet.png. Kept: site/cells/<tx>_<tz>-STREETVIEW.json.
Do not loop this over many cells/panos at once (ToS); keep grids small.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from script_env import ROOT  # noqa: E402

from cellmath import cell_bbox  # noqa: E402
from world_frame import osgb_batch_to_wgs84  # noqa: E402
from osgb import to_osgb  # noqa: E402
from ref_manifest import REF, append_manifest, find_dup, load_manifest, sha256_file  # noqa: E402

BASE = 'https://streetviewpixels-pa.clients6.google.com/v1/thumbnail?cb_client=maps_sv.tactile'
SHEETS = ROOT / 'instance' / 'streetview'
CELLS = ROOT / 'site' / 'cells'
CITE = 'll-method, working-copy'
HDR = {'Referer': 'https://www.google.com/', 'User-Agent': 'Mozilla/5.0'}
THUMB_W, THUMB_H = 450, 300
SHEET_COLS = 4


def parse_cell(spec: str) -> tuple[int, int]:
    """Parse ``tx,tz`` cell indices.

    @param spec: comma-separated integers
    @returns: (tx, tz)
    """
    a, b = spec.split(',')
    return int(a), int(b)


def kept_path(tx: int, tz: int) -> Path:
    """Committed kept-frames file for a cell.

    @param tx: cell X index
    @param tz: cell Z index
    @returns: path under site/cells/
    """
    return CELLS / f'{tx}_{tz}-STREETVIEW.json'


def sample_dir(tx: int, tz: int) -> Path:
    """Directory for downloaded sample thumbs for a cell.

    @param tx: cell X index
    @param tz: cell Z index
    @returns: path under instance/streetview/
    """
    return SHEETS / f'{tx}_{tz}'


def _font(size: int = 14):
    """Label font for contact-sheet captions.

    @param size: point size
    @returns: PIL font
    """
    try:
        return ImageFont.truetype('DejaVuSans.ttf', size)
    except OSError:
        return ImageFont.load_default()


def fetch_bytes(url: str) -> bytes:
    """Download URL bytes with Google-ish headers.

    @param url: image URL
    @returns: raw response body
    """
    req = urllib.request.Request(url, headers=HDR)
    return urllib.request.urlopen(req, timeout=30).read()


def save_frame(tx: int, tz: int, f: dict, note: str) -> str:
    """Download one kept frame under reference/photos and log it in the manifest.

    @param tx: cell X index
    @param tz: cell Z index
    @param f: frame record (id, url, x, z, yaw)
    @param note: why it is useful
    @returns: path relative to reference/
    """
    dest = REF / 'photos' / 'streetview' / f'{tx}_{tz}' / f"{f['id']}.jpg"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(fetch_bytes(f['url']))
    rel = str(dest.relative_to(REF))
    sha = sha256_file(dest)
    if not find_dup(load_manifest(), f['url'], sha):
        append_manifest({
            'id': re.sub(r'[^a-zA-Z0-9]+', '-', rel).strip('-').lower(),
            'place': f'CELL_{tx}_{tz}', 'path': rel, 'kind': 'street', 'source': 'google-streetview',
            'url': f['url'], 'sha256': sha, 'captured': '', 'added': str(date.today()), 'useful': 'yes',
            'models': ['facade', 'materials'], 'not_for': ['private_interior'],
            'notes': f"{note} (x{f['x']} z{f['z']} yaw {f['yaw']}; ll-method)"[:200],
            'mc_tiles': [], 'bbox_mc': [f['x'], f['z'], f['x'], f['z']],
        })
    return rel


def frame_url(lat: float, lon: float, yaw: int, w: int = 900, h: int = 600, pitch: int = 0) -> str:
    """Thumbnail URL for the nearest pano to lat/lon at a heading.

    @param lat: latitude
    @param lon: longitude
    @param yaw: heading in degrees (0 = north, 90 = east)
    @param w: image width
    @param h: image height
    @param pitch: pitch in degrees
    @returns: URL (download via ``fetch_bytes`` / ``save_frame``; do not open in a browser)
    """
    return f'{BASE}&w={w}&h={h}&pitch={pitch}&yaw={yaw}&ll={lat:.6f},{lon:.6f}'


def thumb_url(url: str) -> str:
    """Contact-sheet sized variant of a full frame URL.

    @param url: full-size thumbnail URL
    @returns: URL with sheet dimensions
    """
    return url.replace(f'w=900&h=600', f'w={THUMB_W}&h={THUMB_H}')


def sample_points(tx: int, tz: int, grid: int) -> list[dict]:
    """Evenly spaced sample points inside a cell, with lat/lon.

    @param tx: cell X index
    @param tz: cell Z index
    @param grid: points per side (grid × grid, centred in equal sub-squares)
    @returns: list of {pid, x, z, lat, lon}
    """
    x0, z0, x1, z1 = cell_bbox(tx, tz, tx, tz)
    step_x, step_z = (x1 - x0 + 1) / grid, (z1 - z0 + 1) / grid
    pts = [(r, c, int(x0 + (c + 0.5) * step_x), int(z0 + (r + 0.5) * step_z))
           for r in range(grid) for c in range(grid)]
    en = [to_osgb(x, z) for _, _, x, z in pts]
    ll = osgb_batch_to_wgs84(en)
    return [{'pid': f'g{r}{c}', 'x': x, 'z': z, 'lat': lat, 'lon': lon}
            for (r, c, x, z), (lat, lon) in zip(pts, ll)]


def load_kept(tx: int, tz: int) -> list[dict]:
    """Read kept frames for a cell.

    @param tx: cell X index
    @param tz: cell Z index
    @returns: list of frame records (empty if none)
    """
    p = kept_path(tx, tz)
    return json.loads(p.read_text())['frames'] if p.exists() else []


def build_frames(tx: int, tz: int, grid: int, yaws: list[int]) -> list[dict]:
    """All candidate frames (points × yaws) for a cell.

    @param tx: cell X index
    @param tz: cell Z index
    @param grid: points per side
    @param yaws: headings to sample at each point
    @returns: list of frame records with id and url
    """
    return [{'id': f"{p['pid']}-y{y}", 'x': p['x'], 'z': p['z'], 'lat': round(p['lat'], 6),
             'lon': round(p['lon'], 6), 'yaw': y, 'url': frame_url(p['lat'], p['lon'], y)}
            for p in sample_points(tx, tz, grid) for y in yaws]


def download_thumb(frame: dict, dest: Path) -> Image.Image:
    """Download a sheet-sized thumb for a frame and cache it.

    @param frame: frame record with url
    @param dest: jpeg path to write
    @returns: RGB image
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    raw = fetch_bytes(thumb_url(frame['url']))
    dest.write_bytes(raw)
    return Image.open(io.BytesIO(raw)).convert('RGB')


def load_frame_image(frame: dict, cache_dir: Path | None = None) -> Image.Image:
    """Load a frame image from a kept path, sample cache, or download.

    @param frame: frame record (may include ``path`` relative to reference/)
    @param cache_dir: optional sample-thumb directory
    @returns: RGB image at sheet thumb size
    """
    if frame.get('path'):
        p = REF / frame['path']
        if p.exists():
            im = Image.open(p).convert('RGB')
            return im.resize((THUMB_W, THUMB_H), Image.Resampling.BILINEAR)
    if cache_dir is not None:
        cached = cache_dir / f"{frame['id']}.jpg"
        if cached.exists():
            return Image.open(cached).convert('RGB')
    return download_thumb(frame, (cache_dir or SHEETS) / f"{frame['id']}.jpg")


def contact_sheet(frames: list[dict], images: list[Image.Image], cols: int = SHEET_COLS) -> Image.Image:
    """Tile labelled thumbs into one RGB contact sheet.

    @param frames: frame records (ids / coords for captions)
    @param images: one RGB image per frame (already sized)
    @param cols: columns in the grid
    @returns: RGB contact sheet
    """
    if not frames:
        raise ValueError('no frames for contact sheet')
    cols = max(1, min(cols, len(frames)))
    rows = math.ceil(len(frames) / cols)
    caption_h = 28
    cell_h = THUMB_H + caption_h
    sheet = Image.new('RGB', (cols * THUMB_W, rows * cell_h), (17, 17, 17))
    draw = ImageDraw.Draw(sheet)
    font = _font(13)
    for k, (f, im) in enumerate(zip(frames, images)):
        if im.size != (THUMB_W, THUMB_H):
            im = im.resize((THUMB_W, THUMB_H), Image.Resampling.BILINEAR)
        x, y = (k % cols) * THUMB_W, (k // cols) * cell_h
        sheet.paste(im, (x, y))
        label = f"{f['id']}  x{f['x']} z{f['z']}  yaw {f['yaw']}"
        draw.rectangle([x, y + THUMB_H, x + THUMB_W, y + cell_h], fill=(34, 34, 34))
        draw.text((x + 6, y + THUMB_H + 6), label, fill=(221, 221, 221), font=font)
    return sheet


def write_sheet(tx: int, tz: int, frames: list[dict], stem: str,
                cache: bool = True) -> Path:
    """Download (or reuse) thumbs, write a PNG contact sheet.

    @param tx: cell X index
    @param tz: cell Z index
    @param frames: frame records to show
    @param stem: file stem (without ``.png``)
    @param cache: when True, store thumbs under ``instance/streetview/<tx>_<tz>/``
    @returns: path of the written PNG
    """
    SHEETS.mkdir(parents=True, exist_ok=True)
    cache_dir = sample_dir(tx, tz) if cache else None
    images = [load_frame_image(f, cache_dir) for f in frames]
    out = SHEETS / f'{stem}.png'
    contact_sheet(frames, images).save(out, format='PNG')
    return out


def cmd_sample(a: argparse.Namespace) -> int:
    """Download thumbs and write the contact sheet for a cell.

    @param a: parsed args
    @returns: exit code
    """
    tx, tz = parse_cell(a.cell)
    yaws = [int(y) for y in a.yaws.split(',')]
    frames = build_frames(tx, tz, a.grid, yaws)
    path = write_sheet(tx, tz, frames, f'{tx}_{tz}-sheet', cache=True)
    ids = ','.join(f['id'] for f in frames)
    print(f'{len(frames)} frames ({a.grid}x{a.grid} points x {len(yaws)} yaws)')
    print(f'Read: {path}')
    print(f'keep: python3 shims/streetview_sample.py keep --cell {tx},{tz} --ids <ids> --note "..."')
    print(f'ids: {ids}')
    return 0


def cmd_keep(a: argparse.Namespace) -> int:
    """Record chosen frames (by id) in the committed kept file. Re-derives from args so ids stay stable.

    @param a: parsed args
    @returns: exit code
    """
    tx, tz = parse_cell(a.cell)
    yaws = [int(y) for y in a.yaws.split(',')]
    by_id = {f['id']: f for f in build_frames(tx, tz, a.grid, yaws)}
    kept = {f['id']: f for f in load_kept(tx, tz)}
    for fid in [i for i in a.ids.split(',') if i]:
        if fid not in by_id:
            print(f'unknown id {fid} (use the same --grid/--yaws as sample)', file=sys.stderr)
            return 2
        path = save_frame(tx, tz, by_id[fid], a.note)
        kept[fid] = {**by_id[fid], 'note': a.note, 'kept': date.today().isoformat(), 'method': CITE, 'path': path}
    p = kept_path(tx, tz)
    p.write_text(json.dumps({'cell': f'{tx},{tz}', 'frames': list(kept.values())}, indent=1) + '\n')
    print(f'{len(kept)} kept frames -> {p.relative_to(ROOT)}')
    return 0


def cmd_show(a: argparse.Namespace) -> int:
    """Contact sheet of kept frames only.

    @param a: parsed args
    @returns: exit code
    """
    tx, tz = parse_cell(a.cell)
    frames = load_kept(tx, tz)
    if not frames:
        print('no kept frames')
        return 1
    path = write_sheet(tx, tz, frames, f'{tx}_{tz}-kept', cache=False)
    print(f'Read: {path}')
    return 0


def cmd_list(a: argparse.Namespace) -> int:
    """Print kept frames.

    @param a: parsed args
    @returns: exit code
    """
    tx, tz = parse_cell(a.cell)
    for f in load_kept(tx, tz):
        print(f"{f['id']}  x{f['x']} z{f['z']} yaw {f['yaw']}  {f['note']}\n  {f['url']}")
    return 0


def main() -> int:
    """CLI entry point.

    @returns: exit code
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name, fn in (('sample', cmd_sample), ('keep', cmd_keep), ('show', cmd_show), ('list', cmd_list)):
        p = sub.add_parser(name)
        p.add_argument('--cell', required=True)
        p.add_argument('--grid', type=int, default=3)
        p.add_argument('--yaws', default='0,90,180,270')
        if name == 'keep':
            p.add_argument('--ids', required=True)
            p.add_argument('--note', default='')
        p.set_defaults(fn=fn)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == '__main__':
    raise SystemExit(main())
