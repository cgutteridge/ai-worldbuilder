"""Shop fascia colour read from street-level imagery (#475): a row-read step, one designer ask per shop.

Today ``signs.<label>.fascia_colour`` is hand-written world data or the land-use class palette. This step
finds street-level frames that look at the shop front (KartaView first, then Street View, the configured
``streetlevel`` providers; no new host), crops each to the shop, tiles up to :data:`MAX_FRAMES` crops on one
sheet, and asks the designer one question: which catalogue colour is this shop's fascia. The answer is
mapped to the nearest ``config/sign-options.json`` fascia colour and written as
``record['patch']['signs'] = {'fascia_colour': name}`` with the frame ids as evidence.

No frame, an unseen fascia, a failed ask or an unsure answer keeps the class default and says so on the record.
Frames are fetched into the world's gitignored ``instance/fascia-frames`` and never committed. Nothing here
names a shop: the shop's own ``name`` tag is the only hint the designer gets.

Plug-in sources are ``source(target, out_dir) -> [frame]`` where a frame is ``{id, source, path, crop}`` and
``crop`` is the (left, right) share of the image width that holds the shop. Tests pass doubles.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

MAX_FRAMES = 3
SHEET_H = 360                 # px height of each crop on the sheet
KARTAVIEW_FOV = 70.0          # horizontal field of view assumed for a KartaView frame, degrees
KARTAVIEW_RANGE_M = (6.0, 38.0)
STANDOFF_M = 12.0             # Street View aim point: this far in front of the shop face
COMPASS = {'N': (0.0, -1.0), 'S': (0.0, 1.0), 'E': (1.0, 0.0), 'W': (-1.0, 0.0)}


def target(ctx) -> dict | None:
    """Where the shop is and which way it faces, in block coordinates.

    @returns: ``{x, z, fx, fz, half_width, depth}`` (``fx, fz`` the unit vector from the shop towards the
        street), or None when the building has no front
    """
    cells = ctx.cells
    if not cells:
        return None
    cx = sum(x + 0.5 for x, _z in cells) / len(cells)
    cz = sum(z + 0.5 for _x, z in cells) / len(cells)
    street = getattr(ctx, 'loaded', {}).get('street')
    if street:
        dx, dz = street[0] - cx, street[1] - cz
        norm = math.hypot(dx, dz)
        vec = (dx / norm, dz / norm) if norm > 1e-6 else None
    else:
        vec = COMPASS.get(ctx.front or '')
    if vec is None:
        return None
    along = [(x + 0.5 - cx) * vec[0] + (z + 0.5 - cz) * vec[1] for x, z in cells]
    across = [-(x + 0.5 - cx) * vec[1] + (z + 0.5 - cz) * vec[0] for x, z in cells]
    return {'x': cx, 'z': cz, 'fx': vec[0], 'fz': vec[1],
            'half_width': (max(across) - min(across)) / 2 + 0.5, 'depth': max(along) + 0.5}


def bearing(from_x: float, from_z: float, to_x: float, to_z: float) -> float:
    """Compass bearing in degrees (0 = north = -Z, clockwise)."""
    return math.degrees(math.atan2(to_x - from_x, -(to_z - from_z))) % 360


def _wrap(angle: float) -> float:
    return (angle + 180) % 360 - 180


def crop_share(offset_deg: float, half_angle_deg: float, fov_deg: float) -> tuple[float, float] | None:
    """Left and right share of a rectilinear frame covering ``offset +- half_angle``; None when off-frame."""
    half_fov = fov_deg / 2
    lo, hi = offset_deg - half_angle_deg, offset_deg + half_angle_deg
    if lo <= -half_fov or hi >= half_fov:
        return None
    scale = 2 * math.tan(math.radians(half_fov))

    def share(deg):
        return 0.5 + math.tan(math.radians(deg)) / scale
    return round(share(lo), 4), round(share(hi), 4)


def kartaview_frames(tgt: dict, out_dir: Path, *, query=None, fetch=None) -> list[dict]:
    """The nearest KartaView frames that face the shop from its street side, with the crop that holds it.

    @param query: ``(x, z, radius_m) -> rows`` (rows carry ``id``, ``mc``, ``heading``, ``d``); default is
        :func:`kartaview_fetch.query`
    @param fetch: ``(row, path) -> None`` saves the processed JPEG; default downloads ``fileurlProc``
    """
    if query is None:
        import kartaview_fetch as kv
        query = kv.query
    if fetch is None:
        fetch = _kartaview_download
    face_x, face_z = tgt['x'] + tgt['fx'] * tgt['depth'], tgt['z'] + tgt['fz'] * tgt['depth']
    scored = []
    for row in query(face_x, face_z, int(KARTAVIEW_RANGE_M[1])):
        cam_x, cam_z = row['mc']
        dist = math.hypot(cam_x - tgt['x'], cam_z - tgt['z'])
        if not KARTAVIEW_RANGE_M[0] <= dist <= KARTAVIEW_RANGE_M[1]:
            continue
        if (cam_x - tgt['x']) * tgt['fx'] + (cam_z - tgt['z']) * tgt['fz'] <= tgt['depth']:
            continue                                     # the camera is not on the street side of the shop
        heading = float(row.get('heading') or 0)
        offset = _wrap(bearing(cam_x, cam_z, tgt['x'], tgt['z']) - heading)
        half = math.degrees(math.atan2(tgt['half_width'] * 1.4, dist)) + 4
        crop = crop_share(offset, half, KARTAVIEW_FOV)
        if crop is None:
            continue
        scored.append((dist + 0.4 * abs(offset), str(row['id']), row, crop))
    frames, used = [], []
    for _score, rid, row, crop in sorted(scored, key=lambda s: s[:2]):
        if any(math.hypot(row['mc'][0] - ux, row['mc'][1] - uz) < 4 for ux, uz in used):
            continue
        path = Path(out_dir) / f'kartaview-{rid}.jpg'
        if not path.is_file():
            fetch(row, path)
        frames.append({'id': f'kartaview-{rid}', 'source': 'kartaview', 'path': path, 'crop': crop})
        used.append(row['mc'])
        if len(frames) == MAX_FRAMES:
            break
    return frames


def _kartaview_download(row: dict, path: Path) -> None:
    import urllib.request

    import kartaview_fetch as kv
    url = row.get('fileurlProc') or kv._json(f"{kv.API}{row['id']}")['result']['data']['fileurlProc']
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(urllib.request.Request(url, headers=kv.UA), timeout=60) as resp:
        path.write_bytes(resp.read())


def streetview_frames(tgt: dict, out_dir: Path, *, fetch=None, to_latlon=None) -> list[dict]:
    """Street View frames aimed at the shop from a point in front of it (two yaws a few blocks apart).

    The thumbnail service returns the nearest pano facing the yaw, so the camera is nominal and only the
    middle of the frame is kept. Few requests per shop (Google ToS); frames stay under ``out_dir``.
    """
    import streetview_sample as sv
    if fetch is None:
        fetch = sv.fetch_bytes
    if to_latlon is None:
        from world_frame import mc_to_wgs84 as to_latlon
    frames = []
    side = (-tgt['fz'], tgt['fx'])
    for slide in (0.0, 9.0):
        cam_x = tgt['x'] + tgt['fx'] * (tgt['depth'] + STANDOFF_M) + side[0] * slide
        cam_z = tgt['z'] + tgt['fz'] * (tgt['depth'] + STANDOFF_M) + side[1] * slide
        yaw = round(bearing(cam_x, cam_z, tgt['x'], tgt['z']))
        fid = f'streetview-{round(cam_x)}_{round(cam_z)}-y{yaw}'
        path = Path(out_dir) / f'{fid}.jpg'
        if not path.is_file():
            lat, lon = to_latlon(cam_x, cam_z)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(fetch(sv.frame_url(lat, lon, yaw)))
        frames.append({'id': fid, 'source': 'google-streetview', 'path': path, 'crop': (0.25, 0.75)})
    return frames


def default_sources():
    """KartaView first; Street View only when KartaView has no frame of the shop."""
    return [kartaview_frames, streetview_frames]


def sheet(frames: list[dict], path: Path) -> Path:
    """Tile the crops of ``frames`` side by side, each captioned with its id."""
    from PIL import Image, ImageDraw
    tiles = []
    for frame in frames:
        img = Image.open(frame['path']).convert('RGB')
        left, right = frame['crop']
        img = img.crop((int(left * img.width), 0, int(right * img.width), img.height))
        tile = img.resize((max(1, round(img.width * SHEET_H / img.height)), SHEET_H))
        ImageDraw.Draw(tile).text((6, 6), frame['id'], fill=(255, 255, 0))
        tiles.append(tile)
    out = Image.new('RGB', (sum(t.width for t in tiles), SHEET_H), 'black')
    x = 0
    for tile in tiles:
        out.paste(tile, (x, 0))
        x += tile.width
    out.save(path)
    return path


def catalogue() -> dict:
    """Fascia colour name to RGB from ``config/sign-options.json``."""
    import sign_options
    return {name: row['map_colour'] for name, row in sign_options.load_catalogue()['fascia_colours'].items()}


def prompt(tags: dict, frames: list[dict], names: list[str]) -> str:
    """The one question for a shop: its fascia colour, in catalogue words, with honesty about doubt."""
    shop = tags.get('name')
    which = f'the shop named "{shop}"' if shop else 'the shop in the middle of each crop'
    return (f"The image shows {len(frames)} street-level crop(s) side by side, captioned with frame ids. "
            f'Each should show the front of {which}, centred. Look only at that shop\'s fascia: the board or band '
            f'above its window that carries the shop sign (not the awning, window frames or neighbours). '
            f'Answer with a JSON object and nothing else: '
            f'{{"visible": true|false, "fascia_colour": one of {json.dumps(names)}, "rgb": [r, g, b] 0-255 of '
            f'the board itself, "confident": true|false, "frames": [ids you used], "why": "few words"}}. '
            f'Say visible false when no crop shows that fascia clearly; say confident false when lighting, '
            f'distance or a mixed fascia leaves the colour in doubt.')


def parse_answer(text: str) -> dict:
    """The JSON object in the designer's text.

    @raises ValueError: when there is none
    """
    match = re.search(r'\{.*\}', text, re.S)
    if match is None:
        raise ValueError('no JSON object in the answer')
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError('answer is not an object')
    return data


def nearest(rgb, table: dict) -> str:
    """The catalogue colour closest to ``rgb`` (Euclidean in sRGB)."""
    return min(table, key=lambda name: sum((a - b) ** 2 for a, b in zip(rgb, table[name])))


def resolve(answer: dict, table: dict) -> tuple[str | None, str | None]:
    """The catalogue colour an answer names, or the one nearest its rgb; ``(colour, note)``.

    @returns: ``(None, reason)`` when the answer gives neither a catalogue name nor a usable rgb
    """
    name = answer.get('fascia_colour')
    rgb = answer.get('rgb')
    usable = isinstance(rgb, (list, tuple)) and len(rgb) == 3 and all(isinstance(c, (int, float)) for c in rgb)
    if name in table:
        near = nearest(rgb, table) if usable else None
        return name, (f'named {name}, rgb nearest {near}' if near and near != name else None)
    if usable:
        return nearest(rgb, table), f'{name!r} is not a catalogue colour: nearest to rgb'
    return None, f'answer names no catalogue colour ({name!r})'


def make_step(sources=None, out_dir: Path | None = None):
    """A row-read step ``step(ctx, record)``; ``sources`` and ``out_dir`` default to the world's providers."""
    def step(ctx, record):
        tgt = target(ctx)
        if tgt is None:
            record['unsure'].append('fascia colour: no street front to look from: class default fascia')
            return
        frames_dir = out_dir
        if frames_dir is None:
            from script_env import ROOT
            frames_dir = Path(ROOT) / 'instance' / 'fascia-frames'
        frames = []
        for source in sources if sources is not None else default_sources():
            try:
                frames = source(tgt, frames_dir)
            except Exception as exc:  # noqa: BLE001 - a provider failing means the next one, then the default
                record['unsure'].append(f'fascia colour: {getattr(source, "__name__", "source")} failed '
                                        f'({str(exc)[:100]})')
                continue
            if frames:
                break
        if not frames:
            record['unsure'].append('fascia colour: no street-level frame of the shop front: class default fascia')
            return
        table = catalogue()
        png = ctx.work_dir / f'{ctx.way_id}-fascia.png'
        sheet(frames, png)
        try:
            call = ctx.ask('fascia', png, prompt(ctx.tags, frames, sorted(table)))
            answer = parse_answer(call['text'])
        except (RuntimeError, ValueError) as exc:
            record['unsure'].append(f'fascia colour: designer ask failed ({str(exc)[:100]}): class default fascia')
            return
        used = [f['id'] for f in frames if f['id'] in (answer.get('frames') or [])] or [f['id'] for f in frames]
        evidence = f"{frames[0]['source']} frames {', '.join(used)}" + (f"; {answer['why']}" if answer.get('why') else '')
        if not answer.get('visible', True) or answer.get('confident') is False:
            record['unsure'].append(f'fascia colour: unsure from {", ".join(used)} '
                                    f'({"fascia not visible" if not answer.get("visible", True) else "designer not confident"}): '
                                    f'class default fascia')
            return
        colour, note = resolve(answer, table)
        if colour is None:
            record['unsure'].append(f'fascia colour: {note}: class default fascia')
            return
        flags = [note] if note else []
        record['fields']['fascia_colour'] = {'value': colour, 'rests_on': 'street-level imagery',
                                             'flags': flags, 'evidence': evidence}
        record['patch'].setdefault('signs', {})['fascia_colour'] = colour
        record['unsure'] += [f'fascia colour: {flag}' for flag in flags]
    step.__name__ = 'fascia_step'
    return step


fascia_step = make_step()


def label_in_spec(spec: dict, way_id: str) -> str | None:
    """The label a spec gives a map way (``ways`` maps way id or ``w<id>`` plan to label)."""
    ways = spec.get('ways') or {}
    return ways.get(str(way_id)) or ways.get(f'w{way_id}')


def main(argv=None) -> int:
    """Read one shop's fascia colour and print the ``signs`` patch; never edits a spec.

    For a hand-specced shop: run it with ``--spec`` (a spec file, read only) to get the label-keyed patch to
    apply by hand or through the owner's spec edit.
    """
    import argparse

    import osm_footprints
    import roof_read as RR
    import row_read
    import typology_spec
    from build_roles import resolve_roles
    from script_env import ROOT
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument('--way', required=True, help='map way id of the shop')
    parser.add_argument('--spec', type=Path, help='spec file to take the label from (read only)')
    parser.add_argument('--read-cache', type=Path, help='cache dir (default: the world instance/row-read-cache)')
    args = parser.parse_args(argv)
    buildings, streets = typology_spec.load_buildings(osm_footprints.OSMIndex(), way_ids=[args.way])
    cache = args.read_cache or Path(ROOT) / 'instance' / 'row-read-cache'
    record = row_read.read_building(args.way, '', loader=lambda way: RR.load(way, (buildings, streets)),
                                    role_id=resolve_roles().designer, cache_dir=cache, steps=[fascia_step])
    label = label_in_spec(json.loads(args.spec.read_text()), args.way) if args.spec else None
    value = (record['patch'].get('signs') or {}).get('fascia_colour')
    print(json.dumps({'way': args.way, 'label': label, 'fascia': record['fields'].get('fascia_colour'),
                      'spec_patch': {'signs': {label or '<label>': {'fascia_colour': value}}} if value else None,
                      'unsure': record['unsure'], 'asks': record['asks'], 'cost': row_read.cost(record)}, indent=2))
    return 0
