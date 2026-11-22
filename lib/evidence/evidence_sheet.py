#!/usr/bin/env python3
"""Evidence contact sheet for a built feature (frontage-and-evidence §4 / Q-202).

One command: portal-facing client cameras, top-down over the footprint, aerial crop;
writes ``verification/site/<name>-contact.png`` and records the path on
``verification/site/<name>-result.json`` as ``contact_sheet``.

  python3 scripts/evidence_sheet.py susu_42
  python3 scripts/evidence_sheet.py susu_42 --from-dir verification/site/susu_42-shots
  python3 scripts/evidence_sheet.py susu_42 --no-minecraft   # aerial + portal photos only

Tiles (labelled by stem): each portal client view (and portal photo when on disk),
top-down over the footprint, aerial crop of the footprint bbox.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import re
import sys
import time
from pathlib import Path

from script_env import ROOT  # noqa: E402
from contact_sheet import make_sheet  # noqa: E402

OUT = ROOT / 'verification' / 'site'
BUILDS = ROOT / 'site' / 'builds'
PORTAL_PHOTOS = ROOT / 'reference' / 'photos' / 'portals'
STAND_BACK = 14
TOPDOWN_MIN_Y = 80


def sheet_path(name: str) -> Path:
    """Canonical contact-sheet path for a build name.

    @param name: build / plan name
    @returns: ``verification/site/<name>-contact.png``
    """
    return OUT / f'{name}-contact.png'


def result_path(name: str) -> Path:
    """Path to the build's ``*-result.json`` record.

    @param name: build / plan name
    @returns: result JSON path
    """
    return OUT / f'{name}-result.json'


def record_sheet(name: str, sheet: Path) -> str:
    """Write ``contact_sheet`` onto ``<name>-result.json`` (create if missing).

    @param name: build / plan name
    @param sheet: absolute or relative path to the contact sheet PNG
    @returns: repo-relative path string stored on the result
    """
    OUT.mkdir(parents=True, exist_ok=True)
    sheet = Path(sheet)
    if sheet.is_absolute() and sheet.is_relative_to(ROOT):
        rel = str(sheet.relative_to(ROOT))
    elif not sheet.is_absolute():
        rel = str(sheet)
    else:
        rel = str(sheet)
    path = result_path(name)
    data = json.loads(path.read_text()) if path.is_file() else {}
    data['contact_sheet'] = rel
    path.write_text(json.dumps(data, indent=1) + '\n')
    return rel


def find_contact_sheet(name: str) -> Path | None:
    """Return the contact sheet for ``name`` when the file exists.

    Prefers the path recorded on ``*-result.json``; falls back to
    ``verification/site/<name>-contact.png``.

    @param name: build / plan name
    @returns: existing sheet Path, or None
    """
    path = result_path(name)
    if path.is_file():
        try:
            rel = json.loads(path.read_text()).get('contact_sheet')
        except (json.JSONDecodeError, OSError):
            rel = None
        if rel:
            cand = Path(rel) if Path(rel).is_absolute() else ROOT / rel
            if cand.is_file():
                return cand
    cand = sheet_path(name)
    return cand if cand.is_file() else None


def footprint_bbox(cells: dict) -> tuple[int, int, int, int]:
    """XZ bbox of a recorded plan map.

    @param cells: ``{"x,y,z": state}`` map
    @returns: (x0, z0, x1, z1)
    @raises ValueError: when ``cells`` is empty
    """
    xs, zs = [], []
    for key in cells:
        x, _y, z = (int(p) for p in key.split(','))
        xs.append(x)
        zs.append(z)
    if not xs:
        raise ValueError('empty footprint')
    return min(xs), min(zs), max(xs), max(zs)


def load_cells(name: str) -> dict:
    """Recorded cells of a build from the cell journal (its latest entry, also while in flight).

    @param name: build name
    @returns: ``{"x,y,z": state}`` map
    @raises SystemExit: when the journal has no such build
    """
    from cell_registry import view_registry
    reg = view_registry()
    got = reg.recorded(name)
    if got is None:
        flying = [e for e in reg.entries(plan=name) if e.status == 'begun']
        got = dict(reg.ticket(flying[-1].seq).writes.cells) if flying else None
    if got is None:
        raise SystemExit(f'no recorded build {name!r} in the cell journal; apply the build first')
    return {f'{x},{y},{z}': st for (x, y, z), st in got.items()}


def portal_facing_camera(
    px: float, pz: float, cx: float, cz: float, eye_y: float = 35.0, stand_back: float = STAND_BACK,
) -> tuple[list[float], list[float]]:
    """Eye and look-at for a camera outside a portal, facing it.

    Stands ``stand_back`` blocks outward from the footprint centre through the portal.

    @param px: portal x
    @param pz: portal z
    @param cx: footprint centre x
    @param cz: footprint centre z
    @param eye_y: camera height
    @param stand_back: blocks outward from the portal
    @returns: (eye [x,y,z], look [x,y,z])
    """
    dx, dz = px - cx, pz - cz
    dist = math.hypot(dx, dz) or 1.0
    ox, oz = dx / dist * stand_back, dz / dist * stand_back
    eye = [px + ox, eye_y, pz + oz]
    look = [px, eye_y - 1.0, pz]
    return eye, look


def topdown_camera(bbox: tuple[int, int, int, int], pad: int = 16) -> tuple[list[float], list[float]]:
    """Eye and look-at for a top-down view over a footprint bbox.

    @param bbox: (x0, z0, x1, z1)
    @param pad: extra blocks of margin in the framing estimate
    @returns: (eye, look)
    """
    x0, z0, x1, z1 = bbox
    cx, cz = (x0 + x1) / 2.0, (z0 + z1) / 2.0
    span = max(x1 - x0, z1 - z0) + pad
    eye_y = max(TOPDOWN_MIN_Y, span * 1.2)
    eye = [cx, eye_y, cz]
    look = [cx, 40.0, cz + 0.01]
    return eye, look


def aerial_crop_bbox(bbox: tuple[int, int, int, int], out: Path, pad: int = 8, scale: int = 2) -> Path | None:
    """Crop the university z19 mosaic (or a registered aerial) to the footprint.

    @param bbox: MC (x0, z0, x1, z1)
    @param out: output PNG path
    @param pad: blocks of margin around the bbox
    @param scale: nearest-neighbour upscale after crop
    @returns: ``out`` when a crop was written, else None
    """
    from PIL import Image

    x0, z0, x1, z1 = bbox
    x0, z0, x1, z1 = x0 - pad, z0 - pad, x1 + pad, z1 + pad
    # Prefer a registered user aerial when one covers the bbox.
    from aerial_registered import crop as reg_crop, registered
    for reg in registered():
        try:
            reg_crop(reg.path, [x0, z0, x1, z1], out, scale)
            if out.is_file():
                return out
        except Exception:
            continue
    # Fall back to a local XYZ aerial mosaic that covers the whole bbox.
    # Mosaic.pixels locates column centres, so ask for the bbox's outer corners.
    try:
        import aerial_coverage
        found = aerial_coverage.covering_mosaic([(x0 - .5, z0 - .5), (x1 + .5, z1 + .5)])
    except Exception:
        return None
    if found is None:
        return None
    mosaic, px = found
    (u0, v0), (u1, v1) = px[(x0 - .5, z0 - .5)], px[(x1 + .5, z1 + .5)]
    with Image.open(mosaic.path) as src:
        im = src.convert('RGB')
    box = (int(min(u0, u1)), int(min(v0, v1)), int(max(u0, u1)) + 1, int(max(v0, v1)) + 1)
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    crop = im.crop(box)
    if scale != 1:
        crop = crop.resize((crop.width * scale, crop.height * scale), Image.NEAREST)
    out.parent.mkdir(parents=True, exist_ok=True)
    crop.save(out)
    return out


def _capture_view(
    eye: list[float],
    look: list[float],
    dest: Path,
    downscale: int = 4,
    why: str = 'Evidence sheet: capturing a view',
    m=None,
) -> Path:
    """Teleport the player and write one client screenshot to ``dest``.

    @param eye: camera position
    @param look: look-at point
    @param dest: output PNG
    @param downscale: view_capture downscale factor
    @param why: non-empty explanation for the teleport
    @param m: optional paced world client to reuse
    @returns: ``dest``
    """
    from mc_paced import Paced
    from mc_player import move_player

    w = m or Paced()
    v = Paced(8766)
    move_player(why=why, position=eye, facing=look, m=w)
    time.sleep(4)
    r = v.call('view_capture', {'downscale': downscale, 'close_screen': True})
    for b in r['content']:
        if b['type'] == 'image':
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(base64.b64decode(b['data']))
            return dest
    raise SystemExit(f'view_capture returned no image for {dest.name}')


def portal_photo_path(portal_id: str) -> Path | None:
    """Local portal photo on disk when present.

    @param portal_id: university portal id (e.g. ``42-A``)
    @returns: JPG/PNG path or None
    """
    for ext in ('.jpg', '.jpeg', '.png'):
        p = PORTAL_PHOTOS / f'{portal_id}{ext}'
        if p.is_file():
            return p
    return None


def build_evidence_sheet(
    name: str,
    *,
    portals: list[dict] | None = None,
    bbox: tuple[int, int, int, int] | None = None,
    capture=None,
    aerial_fn=None,
    from_dir: Path | None = None,
    downscale: int = 4,
    use_minecraft: bool = True,
) -> Path:
    """Produce the contact sheet for a built feature and record its path.

    @param name: build name
    @param portals: optional portal dicts (``id``, ``x``, ``z``); loaded from footprint when omitted
    @param bbox: optional footprint bbox; derived from cells when omitted
    @param capture: optional ``(eye, look, dest) -> Path`` (tests / offline)
    @param aerial_fn: optional ``(bbox, dest) -> Path | None``
    @param from_dir: when set, tile every image in that directory instead of capturing
    @param downscale: client capture downscale
    @param use_minecraft: when False, skip client captures (aerial + portal photos only)
    @returns: path to the written contact sheet
    """
    from PIL import Image

    out = sheet_path(name)
    shots_dir = OUT / f'{name}-shots'
    shots_dir.mkdir(parents=True, exist_ok=True)
    tiles: list[Path] = []

    if from_dir is not None:
        src = Path(from_dir)
        if not src.is_dir():
            raise SystemExit(f'--from-dir not a directory: {src}')
        for p in sorted(src.iterdir()):
            if p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp') and p.is_file():
                tiles.append(p)
        if not tiles:
            raise SystemExit(f'no images in {src}')
        sheet = make_sheet(tiles, out)
        if not sheet:
            raise SystemExit('make_sheet produced nothing')
        record_sheet(name, sheet)
        return sheet

    if bbox is None:
        bbox = footprint_bbox(load_cells(name))
    x0, z0, x1, z1 = bbox
    cx, cz = (x0 + x1) / 2.0, (z0 + z1) / 2.0

    if portals is None:
        portals = _portals_for_build(name, bbox)

    live_capture = capture is None and use_minecraft
    capture_fn = capture
    world_m = None
    aerial_fn = aerial_fn or aerial_crop_bbox

    mode = None
    if live_capture:
        from mc_paced import UUID, Paced
        from mc_player import move_player
        import re as _re
        world_m = Paced()
        info = world_m.text('player_get_info', {'uuid': UUID})
        mode = _re.search(r'gameMode: (\w+)', info).group(1)
        move_player(
            why=f'Evidence sheet: capturing {len(portals)} portal view(s) + top-down for {name}.',
            gamemode='spectator',
            m=world_m,
        )

        def capture_fn(eye, look, dest, *, why):
            return _capture_view(eye, look, dest, downscale, why=why, m=world_m)

    try:
        for p in portals:
            pid = p['id']
            photo = portal_photo_path(pid)
            if photo is not None:
                tiles.append(photo)
            if capture_fn is not None:
                eye, look = portal_facing_camera(p['x'], p['z'], cx, cz)
                dest = shots_dir / f'portal-{_safe(pid)}.png'
                if live_capture:
                    tiles.append(capture_fn(
                        eye, look, dest, why=f'Evidence sheet {name}: portal {pid}',
                    ))
                else:
                    tiles.append(capture_fn(eye, look, dest))
        if capture_fn is not None:
            eye, look = topdown_camera(bbox)
            dest = shots_dir / 'top-down.png'
            if live_capture:
                tiles.append(capture_fn(
                    eye, look, dest, why=f'Evidence sheet {name}: top-down',
                ))
            else:
                tiles.append(capture_fn(eye, look, dest))
    finally:
        if mode is not None:
            from mc_player import move_player
            move_player(
                why=f'Evidence sheet captures done for {name}; game mode restored.',
                gamemode=mode,
                m=world_m,
            )

    aerial_dest = shots_dir / 'aerial-crop.png'
    aerial = aerial_fn(bbox, aerial_dest)
    if aerial is not None and Path(aerial).is_file():
        tiles.append(Path(aerial))

    if not tiles:
        # Offline / test fallback: write a labelled placeholder so the path still records.
        placeholder = shots_dir / 'placeholder.png'
        Image.new('RGB', (64, 64), (40, 40, 40)).save(placeholder)
        tiles.append(placeholder)

    sheet = make_sheet(tiles, out)
    if not sheet:
        raise SystemExit('make_sheet produced nothing')
    record_sheet(name, sheet)
    return sheet


def _safe(s: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]+', '_', s)


def _portals_for_build(name: str, bbox: tuple[int, int, int, int]) -> list[dict]:
    """Portals on the build footprint: facade_plan entrance ids, else register near bbox.

    @param name: build name
    @param bbox: footprint bbox
    @returns: portal dicts with id, x, z
    """
    import facade_plan as FP
    import portals as P

    spec_path = BUILDS / f'{name}.json'
    wanted: set[str] | None = None
    if spec_path.is_file():
        spec = json.loads(spec_path.read_text())
        plan = spec.get('facade_plan') or {}
        if plan:
            wanted = FP.accounted_portal_ids(plan) or None
        ways = spec.get('ways') or {}
        if ways:
            try:
                from osm_footprints import OSMIndex
                idx = OSMIndex()
                by_id = {}
                for rings, props in idx.buildings:
                    wid = str(props.get('osm_way_id') or props.get('osm_id') or '')
                    by_id[wid] = rings
                rings_list = [by_id[w] for w in ways if w in by_id]
                if rings_list:
                    found = []
                    seen = set()
                    for rings in rings_list:
                        for p in P.for_footprint(rings):
                            if p['id'] not in seen and (wanted is None or p['id'] in wanted):
                                seen.add(p['id'])
                                found.append(p)
                    if found:
                        return found
            except Exception:
                pass

    x0, z0, x1, z1 = bbox
    near = P.in_bbox(x0, z0, x1, z1, pad=8)
    if wanted is not None:
        near = [p for p in near if p['id'] in wanted]
    return near


def main(argv: list[str] | None = None) -> int:
    """CLI entry for the evidence contact sheet.

    @param argv: optional argv override
    @returns: exit code
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('name', help='build name (a plan recorded in the cell journal)')
    ap.add_argument('--from-dir', type=Path, help='tile existing images instead of capturing')
    ap.add_argument('--no-minecraft', action='store_true',
                    help='skip client captures (aerial + portal photos only)')
    ap.add_argument('--hires', action='store_true', help='full-size client captures')
    ap.add_argument('--midres', action='store_true', help='half-res client captures')
    args = ap.parse_args(argv)
    downscale = 1 if args.hires else (2 if args.midres else 4)
    sheet = build_evidence_sheet(
        args.name,
        from_dir=args.from_dir,
        use_minecraft=not args.no_minecraft and args.from_dir is None,
        downscale=downscale,
    )
    rel = json.loads(result_path(args.name).read_text()).get('contact_sheet', sheet)
    print(f'CONTACT SHEET: {sheet}')
    print(f'recorded on {result_path(args.name)} as contact_sheet={rel}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
