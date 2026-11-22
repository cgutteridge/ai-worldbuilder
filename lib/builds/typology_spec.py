#!/usr/bin/env python3
"""Rules-only specs for house_build and feature_build.

No model chooses a design. A footprint is a filled rectangle, or it comes back
as needs-designer. House tags (terrace, semi-detached, detached, or a plain
house whose shared walls make the row obvious) become one house_build spec.
Any other class the building's own tags name becomes a minimal feature_build
spec, for example a shop with flats above. Missing tags, an odd footprint, or
a class the tags do not name is reported with a reason and not guessed.

The district profile supplies the palette and, when roof:shape is absent, the
single roof form. Observed tags win. A pitched form is only a rise/run the
existing builders already send through pitched_roof_states. house_build's
shell is two storeys, so a house tagged with any other building:levels is
needs-designer rather than a shell of the wrong height.

This module writes spec JSON only. It never places blocks.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path

import land_use
from roof_material import load_catalogue, normalise_material, roof_blocks

# house_build walls run six blocks, two storeys of three. It cannot say otherwise.
HOUSE_STOREYS = 2
STOREY_M = 3.0
# One storey of slack so a pitched roof's median lidar rise still agrees with building:levels.
LIDAR_TOL_M = 4.0
FRONT_REACH = 40.0
PITCHED_RISE = 0.6
FLAT_RISE = 0.0
HOUSE_TAGS = frozenset({'house', 'residential', 'terrace', 'semidetached_house', 'detached'})
SPECIFIC_HOUSE = {
    'terrace': 'terrace',
    'semidetached_house': 'semi',
    'detached': 'detached',
}
PROFILE_ROOFS = frozenset({'flat', 'gable', 'hip', 'mansard', 'shed'})
PITCHED_FORMS = PROFILE_ROOFS - {'flat'}
ROOF_SHAPES = {
    'flat': 'flat',
    'gabled': 'gable',
    'gambrel': 'gable',
    'half-hipped': 'hip',
    'hipped': 'hip',
    'pyramidal': 'hip',
    'mansard': 'mansard',
    'shed': 'shed',
    'skillion': 'shed',
}
SHOP_BUILDINGS = frozenset({'retail', 'supermarket', 'kiosk', 'commercial'})


def generate(buildings, streets=()) -> dict:
    """Turn footprints, lidar height, map tags and a district profile into specs.

    Each building mapping needs ``way_id``, ``cells`` (block columns), ``tags``,
    ``lidar_m`` (median DSM minus DTM, metres, or None) and ``profile`` (a
    validated district document, or None). ``streets`` is a list of polylines
    in block coordinates. Buildings that share a full side are one row.

    @param buildings: one mapping per footprint
    @param streets: polylines, each a sequence of (x, z) points
    @returns: ``specs`` (``builder`` plus ``spec``) and ``needs_designer``
        (``way_id`` plus ``reason``)
    """
    prepared = []
    seen = set()
    for raw in buildings:
        way_id = str(raw.get('way_id', '')).strip()
        if not way_id:
            prepared.append({'way_id': '', 'reason': 'missing way id', 'box': None})
            continue
        if way_id in seen:
            prepared.append({'way_id': way_id, 'reason': 'duplicate way id', 'box': None})
            continue
        seen.add(way_id)
        if raw.get('absent'):
            prepared.append({'way_id': way_id, 'reason': 'not in the vector map', 'box': None})
            continue
        prepared.append(_prepare(raw, way_id))
    _mark_partial(prepared)
    groups = _components(prepared)
    specs = []
    for group in groups:
        if any(row['reason'] for row in group):
            for row in group:
                if not row['reason']:
                    row['reason'] = 'attached building cannot be decided'
            continue
        reason, axis = _row_reason(group, streets)
        if reason:
            for row in group:
                row['reason'] = reason
            continue
        specs.append(_emit(group, axis, streets))
    needs = [{'way_id': row['way_id'], 'reason': row['reason']}
             for row in prepared if row.get('reason')]
    return {
        'specs': sorted(specs, key=lambda item: item['spec']['name']),
        'needs_designer': sorted(needs, key=lambda item: (item['way_id'], item['reason'])),
    }


def load_buildings(index, *, way_ids=None, bbox=None, lidar=None, profile_at=None):
    """Read the footprints a request needs, including a row that crosses the edge.

    Pass way ids or an inclusive block box, not both. A selected footprint pulls
    in every footprint that touches it, so a terrace is not cut into a shorter
    row. A way id that is not in the map is returned with ``absent`` set, and
    :func:`generate` reports it as not in the vector map. Lidar and the district
    profile are sampled per footprint that was found.

    @param index: vector-map index with ``buildings``, ``cells()`` and ``streets``
    @param way_ids: map way ids, or None
    @param bbox: inclusive ``(x0, z0, x1, z1)``, or None
    @param lidar: optional ``cells -> metres or None`` (tests); default reads the cache
    @param profile_at: optional ``(tx, tz) -> profile or None`` (tests)
    @returns: ``(building mappings, street polylines)`` for :func:`generate`
    @raises ValueError: when the request is not exactly one of way ids or a box
    """
    if (way_ids is None) == (bbox is None):
        raise ValueError('pass way ids or a bounding box, not both and not neither')
    records = _index_records(index)
    if way_ids is not None:
        wanted = {str(way) for way in way_ids}
        selected = [row for row in records if row['way_id'] in wanted]
    else:
        x0, z0, x1, z1 = bbox
        lo_x, hi_x = min(x0, x1), max(x0, x1)
        lo_z, hi_z = min(z0, z1), max(z0, z1)
        selected = [row for row in records
                    if any(lo_x <= x <= hi_x and lo_z <= z <= hi_z for x, z in row['cells'])]
    chosen = _touching(records, {row['way_id'] for row in selected})
    absent = []
    if way_ids is not None:
        present = {row['way_id'] for row in records}
        absent = [{'way_id': str(way), 'cells': set(), 'tags': {}, 'lidar_m': None,
                   'profile': None, 'absent': True}
                  for way in way_ids if str(way) not in present]
    lidar_fn = lidar or _lidar_median
    profile_fn = profile_at or _profile_at
    profiles = {}
    buildings = []
    for row in chosen:
        sample = lidar_fn(row['cells'])
        cell = _cell_of(row['cells'])
        if cell not in profiles:
            profiles[cell] = profile_fn(cell)
        buildings.append({
            'way_id': row['way_id'],
            'cells': row['cells'],
            'tags': row['tags'],
            'lidar_m': sample,
            'profile': profiles[cell],
        })
    buildings.extend(absent)
    streets = _nearby_streets(getattr(index, 'streets', ()) or (), chosen)
    return buildings, streets


def write_specs(report: dict, directory: Path) -> list[str]:
    """Write each builder spec. An existing spec file is left untouched.

    Needs-designer rows stay in the printed report. They are not written as
    spec files.

    @param report: :func:`generate` result
    @param directory: output directory, created when missing
    @returns: spec paths written
    @raises SystemExit: when a spec path already exists
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = [directory / f"{item['spec']['name']}.json" for item in report['specs']]
    existing = [path for path in paths if path.exists()]
    if existing:
        raise SystemExit(f'{existing[0]}: already exists; not replacing a spec')
    for item, path in zip(report['specs'], paths):
        path.write_text(json.dumps(item['spec'], indent=2) + '\n')
    return [str(path) for path in paths]


def main(argv=None) -> int:
    """Print specs, or write them with ``--write``. Never places blocks.

    The world is already selected by the scripts wrapper (``--world``).

    @param argv: arguments after ``--world`` has been taken off
    @returns: process status, 0 on success
    """
    parser = argparse.ArgumentParser(
        description='Write typology specs from footprints, lidar and the district profile. '
                    'Dry run unless --write. Never places blocks.')
    parser.add_argument('--ways', nargs='+', help='map way ids')
    parser.add_argument('--bbox', nargs=4, type=int, metavar=('X0', 'Z0', 'X1', 'Z1'),
                        help='inclusive block box')
    parser.add_argument('--out', type=Path, help='spec directory (default: the world site/builds)')
    parser.add_argument('--write', action='store_true', help='write spec files (default: print only)')
    args = parser.parse_args(argv)
    if (args.ways is None) == (args.bbox is None):
        parser.error('pass exactly one of --ways or --bbox')
    import osm_footprints
    buildings, streets = load_buildings(
        osm_footprints.OSMIndex(),
        way_ids=args.ways,
        bbox=tuple(args.bbox) if args.bbox is not None else None,
    )
    report = generate(buildings, streets)
    if args.write:
        from script_env import ROOT
        out = args.out or (Path(ROOT) / 'site' / 'builds')
        report = {**report, 'wrote': write_specs(report, out)}
    print(json.dumps(report, indent=2))
    return 0


def _prepare(raw: dict, way_id: str) -> dict:
    """One footprint's decision, or the first reason it cannot be decided."""
    cells = _as_cells(raw.get('cells'))
    base = {'way_id': way_id, 'reason': None, 'box': None, 'cells': cells or set(), 'tags': {}}
    if not cells:
        base['reason'] = 'odd footprint'
        return base
    box = _box(cells)
    tags = dict(raw.get('tags') or {})
    base.update(cells=cells, box=box, tags=tags)
    if box is None:
        base['reason'] = 'odd footprint'
        return base
    cls, class_reason = _class_of(tags)
    if class_reason:
        base['reason'] = class_reason
        return base
    profile = raw.get('profile')
    if not isinstance(profile, dict):
        base['reason'] = 'no district profile'
        return base
    section = profile.get('classes', {}).get(cls) if isinstance(profile.get('classes'), dict) else None
    if not isinstance(section, dict):
        base['reason'] = 'no district palette'
        return base
    levels, level_reason = _levels(tags)
    if level_reason:
        base['reason'] = level_reason
        return base
    lidar_reason = _lidar_agrees(raw.get('lidar_m'), levels)
    if lidar_reason:
        base['reason'] = lidar_reason
        return base
    if _outside_storeys(section, levels):
        base['reason'] = 'storeys outside the district range'
        return base
    house = cls == 'residential' and tags.get('building') in HOUSE_TAGS
    if house and levels != HOUSE_STOREYS:
        base['reason'] = 'house builder is two storeys'
        return base
    form, roof_source, roof_reason = _roof_form(tags, section)
    if roof_reason:
        base['reason'] = roof_reason
        return base
    family, wall, material_reason = _material(section)
    if material_reason:
        base['reason'] = material_reason
        return base
    slope = 'pitched' if form in PITCHED_FORMS else 'flatish'
    resolved = roof_blocks(family, slope)
    if not resolved.get('known') or not resolved.get('ok'):
        base['reason'] = 'palette has no roof material for this slope'
        return base
    base.update(
        cls=cls, levels=levels, form=form, roof_source=roof_source, family=family, wall=wall,
        path='house' if house else 'feature',
        profile_name=str(profile.get('name') or '').strip() or 'district',
    )
    return base


def _as_cells(raw) -> set[tuple[int, int]] | None:
    """Block columns, or None when the footprint is not a set of integer columns."""
    if not isinstance(raw, (set, list, tuple)) or isinstance(raw, (str, bytes)):
        return None
    cells = set()
    for item in raw:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            return None
        x, z = item
        if type(x) is not int or type(z) is not int:
            return None
        cells.add((x, z))
    return cells


def _box(cells: set[tuple[int, int]]) -> tuple[int, int, int, int] | None:
    """Inclusive box of a filled rectangle at least 3 by 3, else None."""
    xs = [x for x, _z in cells]
    zs = [z for _x, z in cells]
    x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
    width, depth = x1 - x0 + 1, z1 - z0 + 1
    if width < 3 or depth < 3 or len(cells) != width * depth:
        return None
    return (x0, z0, x1, z1)


def _class_of(tags: dict) -> tuple[str | None, str | None]:
    """Class from the building's own tags, or a needs-designer reason.

    A land-use polygon and a house number are not consulted: those would guess
    a class the tags did not name.
    """
    cls, why = land_use.building_class(tags)
    if cls:
        return cls, None
    if tags.get('building') in land_use.OUTBUILDING_VALUES:
        return None, 'outbuilding'
    if why == 'unlisted':
        return None, 'unknown class'
    return None, 'missing tags'


def _levels(tags: dict) -> tuple[int | None, str | None]:
    """Whole ``building:levels``, or a reason."""
    raw = tags.get('building:levels')
    if raw is None or raw == '':
        return None, 'missing building:levels'
    if isinstance(raw, bool):
        return None, 'building:levels is not a whole number'
    if isinstance(raw, int) or (isinstance(raw, float) and raw.is_integer()):
        levels = int(raw)
    elif isinstance(raw, str) and raw.isdigit():
        levels = int(raw)
    else:
        return None, 'building:levels is not a whole number'
    if levels < 1:
        return None, 'building:levels is not a whole number'
    return levels, None


def _lidar_agrees(lidar_m, levels: int) -> str | None:
    """Whether the median lidar rise is present and within a storey of the tag."""
    if isinstance(lidar_m, bool) or not isinstance(lidar_m, (int, float)) or not math.isfinite(lidar_m):
        return 'missing lidar height'
    if abs(float(lidar_m) - levels * STOREY_M) > LIDAR_TOL_M:
        return 'lidar height disagrees with building:levels'
    return None


def _outside_storeys(section: dict, levels: int) -> bool:
    """True when the profile states a storey range and the tag sits outside it."""
    storeys = section.get('storeys')
    if storeys is None:
        return False
    if not isinstance(storeys, dict):
        return True
    low, high = storeys.get('min'), storeys.get('max')
    return type(low) is not int or type(high) is not int or not low <= levels <= high


def _roof_form(tags: dict, section: dict) -> tuple[str | None, str | None, str | None]:
    """Roof form, where it came from, and a reason when it cannot be chosen.

    ``roof:shape`` wins. Otherwise the district must list exactly one form.
    """
    raw = tags.get('roof:shape')
    if raw not in (None, ''):
        if not isinstance(raw, str) or raw not in ROOF_SHAPES:
            return None, None, 'unknown roof:shape'
        return ROOF_SHAPES[raw], 'tag', None
    roofs = section.get('roofs')
    if not isinstance(roofs, list) or not roofs:
        return None, None, 'missing roof form'
    if len(roofs) != 1:
        return None, None, 'district lists more than one roof form'
    if roofs[0] not in PROFILE_ROOFS:
        return None, None, 'unknown roof form'
    return roofs[0], 'district', None


def _material(section: dict) -> tuple[str | None, str | None, str | None]:
    """First palette entry that is a catalogue roof family, plus its full block.

    The profile lists the palette in choice order. A block that is not a roof
    family is skipped; if none match, the building is needs-designer.
    """
    palette = section.get('palette')
    if not isinstance(palette, list) or not palette:
        return None, None, 'no district palette'
    families = load_catalogue()['families']
    for entry in palette:
        if not isinstance(entry, str) or not entry.strip():
            continue
        key = normalise_material(entry)
        family = key if key in families else next(
            (name for name, row in families.items() if row.get('full') == key), None)
        if family:
            return family, families[family]['full'], None
    return None, None, 'palette has no roof material'


def _mark_partial(prepared: list[dict]) -> None:
    """A side that is shared only in part is an odd footprint, not a row."""
    boxed = [row for row in prepared if row.get('box')]
    for i, left in enumerate(boxed):
        for right in boxed[i + 1:]:
            if _relation(left['box'], right['box']) == 'partial':
                for row in (left, right):
                    if not row['reason']:
                        row['reason'] = 'odd footprint'


def _components(prepared: list[dict]) -> list[list[dict]]:
    """Groups joined by a full shared side. Refused footprints stay in the group."""
    boxed = [row for row in prepared if row.get('box')]
    parent = {row['way_id']: row['way_id'] for row in boxed}

    def find(way_id: str) -> str:
        while parent[way_id] != way_id:
            parent[way_id] = parent[parent[way_id]]
            way_id = parent[way_id]
        return way_id

    for i, left in enumerate(boxed):
        for right in boxed[i + 1:]:
            if _relation(left['box'], right['box']) in ('x', 'z'):
                parent[find(left['way_id'])] = find(right['way_id'])
    groups = defaultdict(list)
    for row in boxed:
        groups[find(row['way_id'])].append(row)
    return list(groups.values())


def _relation(a: tuple, b: tuple) -> str | None:
    """``x`` or ``z`` when two boxes share a full side, ``partial`` when they do not, else None."""
    ax0, az0, ax1, az1 = a
    bx0, bz0, bx1, bz1 = b
    horizontal = _span(ax0, ax1, bx0, bx1)
    vertical = _span(az0, az1, bz0, bz1)
    if horizontal == 'touch' and vertical == 'overlap':
        return 'x' if (az0, az1) == (bz0, bz1) else 'partial'
    if vertical == 'touch' and horizontal == 'overlap':
        return 'z' if (ax0, ax1) == (bx0, bx1) else 'partial'
    if horizontal == 'overlap' and vertical == 'overlap':
        return 'partial'
    return None


def _span(a0: int, a1: int, b0: int, b1: int) -> str | None:
    """How two inclusive ranges meet: touch, overlap, or neither."""
    if a1 + 1 == b0 or b1 + 1 == a0:
        return 'touch'
    if a0 <= b1 and b0 <= a1:
        return 'overlap'
    return None


def _row_reason(group: list[dict], streets) -> tuple[str | None, str | None]:
    """Why a clean component still cannot be one spec, and its row axis."""
    axes = set()
    for i, left in enumerate(group):
        for right in group[i + 1:]:
            rel = _relation(left['box'], right['box'])
            if rel in ('x', 'z'):
                axes.add(rel)
    if len(axes) > 1:
        return 'odd footprint', None
    axis = next(iter(axes), None) if len(group) > 1 else None
    if len(group) > 1 and axis is None:
        return 'odd footprint', None
    if len({row['profile_name'] for row in group}) > 1:
        return 'districts differ', axis
    if len({row['path'] for row in group}) > 1:
        return 'mixed classes in one row', axis
    if group[0]['path'] == 'house':
        kind = _house_kind(group)
        if kind is None:
            return 'tag disagrees with the footprint', axis
        if len({row['levels'] for row in group}) > 1:
            return 'storeys differ', axis
        if len({row['form'] for row in group}) > 1:
            return 'roof forms differ', axis
    front, front_reason = _front(_cells_of(group), streets, axis)
    if front_reason:
        return front_reason, axis
    return None, axis


def _house_kind(group: list[dict]) -> str | None:
    """Terrace, semi or detached when every explicit tag agrees with the row."""
    count = len(group)
    kind = 'detached' if count == 1 else 'semi' if count == 2 else 'terrace'
    specific = {SPECIFIC_HOUSE[row['tags']['building']]
                for row in group if row['tags'].get('building') in SPECIFIC_HOUSE}
    if specific and specific != {kind}:
        return None
    return kind


def _front(cells: set[tuple[int, int]], streets, axis: str | None) -> tuple[str | None, str | None]:
    """Cardinal facing the nearest street, perpendicular to a row."""
    hit = _closest(_centroid(cells), streets)
    if hit is None or hit[0] > FRONT_REACH:
        return None, 'no street to face'
    _dist, px, pz = hit
    cx, cz = _centroid(cells)
    dx, dz = px - cx, pz - cz
    if axis == 'x':
        if abs(dz) <= abs(dx):
            return None, 'no clear front'
        return ('S' if dz > 0 else 'N'), None
    if axis == 'z':
        if abs(dx) <= abs(dz):
            return None, 'no clear front'
        return ('E' if dx > 0 else 'W'), None
    if max(abs(dx), abs(dz)) < 1.5 * min(abs(dx), abs(dz)):
        return None, 'no clear front'
    if abs(dx) >= abs(dz):
        return ('E' if dx > 0 else 'W'), None
    return ('S' if dz > 0 else 'N'), None


def _centroid(cells: set[tuple[int, int]]) -> tuple[float, float]:
    """Centre of the block columns."""
    return (sum(x + 0.5 for x, _z in cells) / len(cells),
            sum(z + 0.5 for _x, z in cells) / len(cells))


def _closest(origin: tuple[float, float], streets) -> tuple[float, float, float] | None:
    """Distance and closest point on any street segment, or None when there are none."""
    ox, oz = origin
    best = None
    for line in streets:
        points = list(line)
        for start, end in zip(points, points[1:]):
            ax, az = start
            bx, bz = end
            dx, dz = bx - ax, bz - az
            denom = dx * dx + dz * dz
            portion = 0.0 if denom == 0 else max(0.0, min(1.0, ((ox - ax) * dx + (oz - az) * dz) / denom))
            px, pz = ax + portion * dx, az + portion * dz
            dist = math.hypot(px - ox, pz - oz)
            if best is None or dist < best[0]:
                best = (dist, px, pz)
    return best


def _emit(group: list[dict], axis: str | None, streets) -> dict:
    """One house_build or feature_build spec for a decided row."""
    ordered = _order(group, axis)
    front, _reason = _front(_cells_of(group), streets, axis)
    labels = []
    ways = {}
    for index, row in enumerate(ordered):
        label = chr(ord('a') + index) if index < 26 else f'h{index + 1}'
        labels.append(label)
        ways[row['way_id']] = label
        row['label'] = label
    kind, phrase = _kind(ordered)
    name = f"typology_{kind}_{_slug(ordered[0]['way_id'])}"
    note = _note(ordered, phrase)
    if ordered[0]['path'] == 'house':
        pitch = PITCHED_RISE if ordered[0]['form'] in PITCHED_FORMS else FLAT_RISE
        spec = {
            'name': name,
            'notes': note,
            'ways': ways,
            'roofs': {row['label']: row['family'] for row in ordered},
            'roof_pitch': {row['label']: pitch for row in ordered},
            'groups': [{'nos': labels, 'mode': 'group' if kind == 'terrace' else 'each', 'front': front}],
        }
        return {'builder': 'house_build', 'spec': spec}
    spec = {
        'name': name,
        'notes': note,
        'ways': ways,
        'storeys': {row['label']: row['levels'] for row in ordered},
        'roofs': {row['label']: {
            'kind': 'pitched' if row['form'] in PITCHED_FORMS else 'flat',
            'material': row['family'],
        } for row in ordered},
        'groups': [{'nos': labels, 'mode': 'each', 'front': front}],
        'wall': ordered[0]['wall'],
    }
    return {'builder': 'feature_build', 'spec': spec}


def _order(group: list[dict], axis: str | None) -> list[dict]:
    """Left to right, or southwards when the row runs that way."""
    if axis == 'z':
        return sorted(group, key=lambda row: (row['box'][1], row['box'][0], row['way_id']))
    return sorted(group, key=lambda row: (row['box'][0], row['box'][1], row['way_id']))


def _kind(group: list[dict]) -> tuple[str, str]:
    """Filename token and the phrase used in the spec note."""
    if group[0]['path'] == 'house':
        kind = _house_kind(group)
        phrase = {'semi': 'semi-detached', 'terrace': 'terrace', 'detached': 'detached'}[kind]
        return kind, phrase
    if group[0]['cls'] == 'commercial' and any(
            row['tags'].get('shop') or row['tags'].get('building') in SHOP_BUILDINGS for row in group):
        if any(row['levels'] >= 2 for row in group):
            return 'shop', 'shop with flats above'
        return 'shop', 'shop'
    return group[0]['cls'], group[0]['cls']


def _note(group: list[dict], phrase: str) -> str:
    """Provenance a person can read. The builders do not interpret it."""
    ways = ', '.join(row['way_id'] for row in group)
    sources = {row['roof_source'] for row in group}
    source = 'roof:shape' if sources == {'tag'} else (
        'the district profile' if sources == {'district'} else 'roof:shape or the district profile')
    forms = list(dict.fromkeys(row['form'] for row in group))
    roof = f"Roof {forms[0]} from {source}." if len(forms) == 1 else "Roofs follow each building's roof:shape."
    return (f"Typology {phrase}. Ways {ways}. {roof} "
            f"Material {group[0]['family']} from the district palette ({group[0]['profile_name']}).")


def _cells_of(group: list[dict]) -> set[tuple[int, int]]:
    cells = set()
    for row in group:
        cells.update(row['cells'])
    return cells


def _slug(way_id: str) -> str:
    """A filename token from a map way id."""
    cleaned = re.sub(r'[^A-Za-z0-9]+', '_', way_id).strip('_')
    return cleaned or 'way'


def _index_records(index) -> list[dict]:
    """Footprints from a vector-map index, one record per building that has cells."""
    import osm_footprints
    by_index = defaultdict(set)
    for (x, z), i in index.cells().items():
        by_index[i].add((int(x), int(z)))
    records = []
    for i, (_rings, props) in enumerate(index.buildings):
        cells = by_index.get(i)
        if not cells:
            continue
        tags = osm_footprints.osm_tags(props)
        way_id = str(props.get('osm_way_id') or props.get('osm_id') or '')
        if not way_id:
            continue
        records.append({'way_id': way_id, 'cells': cells, 'tags': tags})
    return records


def _touching(records: list[dict], selected: set[str]) -> list[dict]:
    """Selected footprints plus every footprint that touches one of them, by 4-neighbour."""
    owner = {}
    for row in records:
        for cell in row['cells']:
            owner.setdefault(cell, row['way_id'])
    seen = set(selected)
    frontier = set(selected)
    while frontier:
        nxt = set()
        for way_id in frontier:
            cells = next(row['cells'] for row in records if row['way_id'] == way_id)
            for x, z in cells:
                for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    other = owner.get((x + dx, z + dz))
                    if other and other not in seen:
                        seen.add(other)
                        nxt.add(other)
        frontier = nxt
    return [row for row in records if row['way_id'] in seen]


def _cell_of(cells: set[tuple[int, int]]) -> tuple[int, int]:
    """World-grid cell of a footprint's centroid."""
    import cellmath
    xs = [x for x, _z in cells]
    zs = [z for _x, z in cells]
    return cellmath.cell_of(int(sum(xs) / len(xs)), int(sum(zs) / len(zs)))


def _nearby_streets(streets, chosen: list[dict]) -> list[list[tuple[float, float]]]:
    """Street polylines whose segment box meets the selection, padded by the front reach."""
    cells = [cell for row in chosen for cell in row['cells']]
    if not cells:
        return []
    xs = [x for x, _z in cells]
    zs = [z for _x, z in cells]
    x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
    kept = []
    for _name, _highway, points in streets:
        if len(points) < 2:
            continue
        near = False
        for start, end in zip(points, points[1:]):
            min_x, max_x = min(start[0], end[0]), max(start[0], end[0])
            min_z, max_z = min(start[1], end[1]), max(start[1], end[1])
            if max_x >= x0 - FRONT_REACH and min_x <= x1 + FRONT_REACH \
                    and max_z >= z0 - FRONT_REACH and min_z <= z1 + FRONT_REACH:
                near = True
                break
        if near:
            kept.append([(float(x), float(z)) for x, z in points])
    return kept


def _lidar_median(cells: set[tuple[int, int]]) -> float | None:
    """Median DSM minus DTM over the footprint, or None when the cache cannot say."""
    import numpy as np
    import lidar_provider as lp
    xs = [x for x, _z in cells]
    zs = [z for _x, z in cells]
    x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
    try:
        client = lp.tile_client(download=False)
        east0, north0, east1, north1 = lp.mc_box_en(x0, z0, x1, z1)
        if client.missing('DTM', east0, north0, east1, north1) \
                or client.missing('DSM', east0, north0, east1, north1):
            return None
        heights = lp.mc_heights_float(client, x0, z0, x1, z1)
    except (LookupError, FileNotFoundError, OSError, RuntimeError):
        return None
    dsm = np.asarray(heights['dsm'], dtype=float)
    dtm = np.asarray(heights['dtm'], dtype=float)
    values = []
    for x, z in cells:
        i, j = x - x0, z - z0
        if not (0 <= i < dsm.shape[0] and 0 <= j < dsm.shape[1]):
            continue
        rise = float(dsm[i, j] - dtm[i, j])
        if math.isfinite(rise):
            values.append(rise)
    if len(values) * 2 < len(cells):
        return None
    return float(statistics.median(values))


def _profile_at(cell: tuple[int, int]):
    """The active world's district profile for one cell, or None."""
    import garden_sources
    return garden_sources.load_district(cell)


if __name__ == '__main__':
    raise SystemExit(main())
