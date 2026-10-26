#!/usr/bin/env python3
"""Cropped, gridded Google satellite windows for reading trees by eye, and the same window with a trees plan drawn on it.

Downloads tiles via ``google_aerial.fetch_tile``, warps them to an exact MC block crop (affine fit of tile
pixels to the MC grid), draws a 5-block grid, writes a PNG under ``instance/google-aerial/``. Agents ``Read``
the PNG — no browser. Do not commit these files into ``reference/``.

  python3 shims/google_window.py crop --at 6240,-5664 --half 32x16 --ppb 13        # reading window
  python3 shims/google_window.py crop --at 6240,-5664 --half 32x16 --outlines      # + OSM building outlines
  python3 shims/google_window.py plan --at 6240,-5664 --half 32x16 --plan p.json   # planned crowns

``--half 32x16 --ppb 13`` → 64 × 32 blocks at 13 px/block (832 × 416). Method: rules/foliage.md "Redo from aerial".
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Callable

from PIL import Image, ImageDraw, ImageFont

import google_aerial as G

ZOOM = 20


def _font(size: int = 11):
    """Small label font.

    @param size: point size
    @returns: PIL font
    """
    try:
        return ImageFont.truetype('DejaVuSans.ttf', size)
    except OSError:
        return ImageFont.load_default()


def window_image(cx: int, cz: int, hx: int, hz: int, ppb: int,
                 draw_extra: Callable[[ImageDraw.ImageDraw, int, int, int], None]) -> Image.Image:
    """Exact crop x cx-hx..cx+hx, z cz-hz..cz+hz with MC grid and optional overlays.

    @param cx: centre MC x
    @param cz: centre MC z
    @param hx: half-width in blocks
    @param hz: half-height in blocks
    @param ppb: pixels per block
    @param draw_extra: ``(draw, x0, z0, ppb)`` to paint extra overlays
    @returns: RGB image
    """
    x0, z0, x1, z1 = cx - hx, cz - hz, cx + hx, cz + hz
    w, h = 2 * hx * ppb, 2 * hz * ppb
    tx0, ty0, tx1, ty1 = G.tile_range((x0, z0, x1, z1), ZOOM)
    s = 256
    tw, th = (tx1 - tx0 + 1) * s, (ty1 - ty0 + 1) * s
    tiles = Image.new('RGB', (tw, th))
    for y in range(ty0, ty1 + 1):
        for x in range(tx0, tx1 + 1):
            tiles.paste(G.fetch_tile(x, y, ZOOM), ((x - tx0) * s, (y - ty0) * s))

    # Window corners in tile-pixel space → affine window→tile for PIL Image.transform.
    p = [((fx - tx0) * s, (fy - ty0) * s) for fx, fy in G.mc_to_tile([(x0, z0), (x1, z0), (x0, z1)], ZOOM)]
    (ax, ay), (bx, by), (cx_, cy_) = p
    a, b, c, d = (bx - ax) / w, (by - ay) / w, (cx_ - ax) / h, (cy_ - ay) / h
    crop = tiles.transform(
        (w, h), Image.Transform.AFFINE, (a, c, ax, b, d, ay),
        resample=Image.Resampling.BILINEAR,
    )

    out = crop.convert('RGBA')
    draw = ImageDraw.Draw(out)
    font = _font(11)
    for v in range((x0 // 5 + 1) * 5, x1 + 1, 5):
        X = (v - x0) * ppb
        draw.line([(X, 0), (X, h)], fill=(51, 255, 102, 150), width=1)
        draw.text((X + 2, 1), str(v), fill=(51, 255, 102), font=font)
    for v in range((z0 // 5 + 1) * 5, z1 + 1, 5):
        Y = (v - z0) * ppb
        draw.line([(0, Y), (w, Y)], fill=(51, 204, 255, 150), width=1)
        draw.text((2, Y - 12), str(v), fill=(51, 204, 255), font=font)
    draw_extra(draw, x0, z0, ppb)
    return out.convert('RGB')


def outlines_draw(box: tuple[int, int, int, int]) -> Callable[[ImageDraw.ImageDraw, int, int, int], None]:
    """Draw OSM building outlines in magenta for registration checks.

    @param box: MC bbox of the window
    @returns: overlay callback
    """
    def fn(draw: ImageDraw.ImageDraw, x0: int, z0: int, ppb: int) -> None:
        for o in G.osm_outlines((box[0] - 20, box[1] - 20, box[2] + 20, box[3] + 20)):
            pts = [((x - x0) * ppb, (z - z0) * ppb) for x, z in o['ring']]
            draw.polygon(pts, outline=(255, 0, 255))
    return fn


def plan_draw(plan: dict) -> Callable[[ImageDraw.ImageDraw, int, int, int], None]:
    """Draw planned crowns (yellow) and skipped (red dashed).

    @param plan: ``{"trees": [[x,z,r,...]], "skipped": [[x,z,r,why]]}``
    @returns: overlay callback
    """
    def fn(draw: ImageDraw.ImageDraw, x0: int, z0: int, ppb: int) -> None:
        for x, z, r, *_k in plan['trees']:
            cx, cy = (x - x0 + .5) * ppb, (z - z0 + .5) * ppb
            rad = r * ppb
            draw.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], outline=(255, 255, 0), width=2)
            draw.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=(255, 255, 0))
        for x, z, r, _why in plan['skipped']:
            cx, cy = (x - x0 + .5) * ppb, (z - z0 + .5) * ppb
            rad = r * ppb
            # Approximate dash: many short arcs via chord segments
            bbox = [cx - rad, cy - rad, cx + rad, cy + rad]
            draw.ellipse(bbox, outline=(255, 51, 51), width=2)
    return fn


def main(argv=None) -> int:
    """CLI entry point.

    @param argv: optional argv override
    @returns: exit code
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('mode', choices=('crop', 'plan'))
    ap.add_argument('--at', required=True, help='X,Z centre')
    ap.add_argument('--half', default='32x16', help='half width x half height in blocks')
    ap.add_argument('--ppb', type=int, default=13, help='pixels per block')
    ap.add_argument('--outlines', action='store_true')
    ap.add_argument('--plan', help='JSON {"trees": [[x, z, r, kind]], "skipped": [[x, z, r, why]]} (trees_replan.py writes it)')
    a = ap.parse_args(argv)
    cx, cz = (int(v) for v in a.at.split(','))
    hx, hz = (int(v) for v in a.half.split('x'))
    if a.mode == 'plan':
        fn = plan_draw(json.loads(open(a.plan).read()))
    elif a.outlines:
        fn = outlines_draw((cx - hx, cz - hz, cx + hx, cz + hz))
    else:
        fn = lambda *_a: None  # noqa: E731
    out = G.SHEETS / f'{"p" if a.mode == "plan" else "w"}_{cx}_{cz}.png'
    G.SHEETS.mkdir(parents=True, exist_ok=True)
    window_image(cx, cz, hx, hz, a.ppb, fn).save(out, format='PNG')
    print(f'Read: {out}  x {cx - hx}..{cx + hx} z {cz - hz}..{cz + hz}  {a.ppb} px/block')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
