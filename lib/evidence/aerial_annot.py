#!/usr/bin/env python3
"""Annotated aerial crop: a local aerial over an MC bbox with footprint outlines (way-id tails) and a 10-block grid.

  python3 scripts/aerial_annot.py x0,z0,x1,z1 out.png [scale] [mosaic.png] [extra.geojson]

By default the finest local mosaic that holds the whole bbox is used (``aerial_coverage.covering_mosaic``);
name one with ``mosaic.png`` (a file ``aerial_coverage.mosaics`` finds). ``extra.geojson`` (EPSG:27700) adds
non-building outlines (parking etc.) in blue. ``annotate`` takes any aerial source with ``pixels(points)``
and ``image()``: a ``Mosaic`` or a registered aerial.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Bootstrap tool path so bare lib imports resolve when run via scripts/ wrapper.
_TOOL = Path(__file__).resolve().parents[2]
if str(_TOOL) not in sys.path:
    sys.path.insert(0, str(_TOOL))
import script_env  # noqa: E402, F401

from PIL import Image, ImageDraw  # noqa: E402

import aerial_coverage  # noqa: E402


def locate(source, points):
    """Image pixels of exact MC coordinates (``Mosaic.pixels`` and friends locate column centres, so shift by half)."""
    px = source.pixels([(x - .5, z - .5) for x, z in points])
    return [px[(x - .5, z - .5)] for x, z in points]


def covering_source(bbox, mosaic=None):
    """The aerial that holds ``bbox``: the named local mosaic, else the finest covering one.

    @param bbox: MC (x0, z0, x1, z1)
    @param mosaic: optional path of a local mosaic known to ``aerial_coverage.mosaics``
    @returns: a ``Mosaic``
    @raises ValueError: the named mosaic is unknown, or no local mosaic holds the whole bbox
    """
    x0, z0, x1, z1 = bbox
    if mosaic is not None:
        want = Path(mosaic).resolve()
        for m in aerial_coverage.mosaics():
            if m.path.resolve() == want:
                return m
        raise ValueError(f'not a local geolocated mosaic: {mosaic}')
    found = aerial_coverage.covering_mosaic([(x0 - .5, z0 - .5), (x1 + .5, z1 + .5)])
    if found is None:
        raise ValueError(f'no local mosaic covers bbox {x0},{z0},{x1},{z1}')
    return found[0]


def annotate(source, bbox, out, scale=4, footprints=None, extra=None):
    """Write an annotated crop of ``source`` over an MC bbox.

    @param source: aerial with ``pixels(points)`` and ``image()``
    @param bbox: MC (x0, z0, x1, z1)
    @param out: output PNG path
    @param scale: upscale factor
    @param footprints: ``[(rings, props)]`` with ``rings[0]`` the MC outline; default ``OSMIndex().buildings``
    @param extra: optional GeoJSON path (EPSG:27700) of non-building outlines drawn in blue
    @returns: size of the written image
    """
    x0, z0, x1, z1 = bbox
    S = scale
    corners = locate(source, [(x0, z0), (x1, z0), (x0, z1), (x1, z1)])
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    box = (min(xs), min(ys), max(xs), max(ys))
    crop = source.image().crop(tuple(int(v) for v in box))
    w, h = crop.size
    crop = crop.resize((w * S, h * S), Image.LANCZOS)
    d = ImageDraw.Draw(crop)

    def tr(p):
        return ((p[0] - int(box[0])) * S, (p[1] - int(box[1])) * S)

    def project(pts):
        return [tr(p) for p in locate(source, pts)]

    if footprints is None:
        from osm_footprints import OSMIndex
        footprints = OSMIndex().buildings
    for ring, props in footprints:
        r = ring[0]
        if not any(x0 <= x <= x1 and z0 <= z <= z1 for x, z in r):
            continue
        pp = project(r)
        d.line(pp + [pp[0]], fill=(255, 0, 0), width=1)
        centre = (sum(v[0] for v in r[:-1]) / (len(r) - 1), sum(v[1] for v in r[:-1]) / (len(r) - 1))
        d.text(project([centre])[0], (props.get('osm_way_id') or '')[-3:], fill=(255, 255, 0))
    if extra:
        from world_frame import legacy_offsets_dict
        o = legacy_offsets_dict()
        for f in json.load(open(extra))['features']:
            g, pr = f['geometry'], f['properties']
            if not g or g['type'] not in ('Polygon', 'MultiPolygon') or pr.get('building'):
                continue
            for poly in ([g['coordinates']] if g['type'] == 'Polygon' else g['coordinates']):
                r = [(e - o['OFFSET_E'], o['OFFSET_N'] - n) for e, n in poly[0]]
                if not any(x0 <= x <= x1 and z0 <= z <= z1 for x, z in r):
                    continue
                pp = project(r)
                d.line(pp, fill=(60, 140, 255), width=2)
                lab = pr.get('amenity') or pr.get('landuse') or pr.get('natural') or pr.get('leisure') or ''
                d.text(pp[0], lab[:4] + (pr.get('osm_way_id') or '')[-3:], fill=(60, 140, 255))
    for gx in range(x0 - x0 % 10 + 10, x1, 10):
        a, b = project([(gx, z0), (gx, z1)])
        d.line([a, b], fill=(0, 255, 255), width=1)
        d.text(a, str(gx), fill=(0, 255, 255))
    for gz in range(z0 - z0 % 10 + 10, z1, 10):
        a, b = project([(x0, gz), (x1, gz)])
        d.line([a, b], fill=(0, 255, 0), width=1)
        d.text(a, str(gz), fill=(0, 255, 0))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    crop.save(out)
    return crop.size


def main(argv=None) -> None:
    """CLI: ``x0,z0,x1,z1 out.png [scale] [mosaic.png] [extra.geojson]``."""
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        raise SystemExit(__doc__)
    bbox = tuple(map(int, argv[0].split(',')))
    scale = int(argv[2]) if len(argv) > 2 else 4
    source = covering_source(bbox, argv[3] if len(argv) > 3 else None)
    print(annotate(source, bbox, argv[1], scale, extra=argv[4] if len(argv) > 4 else None))


if __name__ == '__main__':
    main()
