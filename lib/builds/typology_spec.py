#!/usr/bin/env python3
"""Rules-only specs for house_build and feature_build.

No model chooses a design. A footprint of any orientation is taken as drawn
(the builder fits it); a sliver or a multi-part shape comes back as needs-designer.
House tags (terrace, semi-detached, detached, or a plain
house whose shared walls make the row obvious) become one house_build spec.
A terrace row uses the terrace recipe (#441): one roof per house (mode each),
a domestic chimney on every party wall (explicit ``at`` sites from footprint
adjacency), and roof families alternating per consecutive pair from the class
``terrace_roofs`` palette when the appearance came from the class default and
the form is pitched. A district profile keeps its single roof family. Semi and
detached keep mode each.
Any other class the building's own tags name becomes a minimal feature_build
spec, for example a shop with flats above. Missing tags, an odd footprint, or
a class the tags do not name is reported with a reason and not guessed.

The district profile supplies the palette and, when roof:shape is absent, the
single roof form. Observed tags win. A missing district profile, or a class
the profile does not mention, uses the land-use class default (palette, roofs
and storeys on the class row) and the spec note names that fallback. A pitched
form is only a rise/run the existing builders already send through
pitched_roof_states. house_build's shell is two storeys, so a house whose
storeys are not two is needs-designer rather than a shell of the wrong height.
A missing building:levels takes the profile or class storeys when min and max
agree; a range is needs-designer. Lidar height is a note on the spec, never a
refusal, and a missing lidar height is not a refusal either.

This module writes spec JSON only. It never places blocks.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import land_use
import terrace_body
from house_build import footprint_party_walls
from roof_material import load_catalogue, normalise_material, roof_blocks

# house_build walls run six blocks, two storeys of three. It cannot say otherwise.
HOUSE_STOREYS = 2
STOREY_M = 3.0
# One storey of slack so a pitched roof's median lidar rise still agrees with building:levels.
LIDAR_TOL_M = 4.0
FRONT_REACH = 40.0
# Footprints are taken as drawn; the builder fits them. A house must fill this much of its bounding box
# (a rotated rectangle fills about half at worst) and a shop only needs this many block columns.
HOUSE_MIN_FILL = 0.45
HOUSE_MIN_CELLS = 9
SHOP_MIN_CELLS = 20
# Footprints with at most this many empty block columns between them belong to one row.
ROW_GAP = 1
# A footprint must survive this many erosion steps (4-neighbour), so a strip under 3 columns thick is a sliver.
THIN_STEPS = 1
# A street whose offset across the facing axis is under this many blocks is beside the end of a row, not in front.
FRONT_MIN_OFFSET = 1.0
# access lanes behind a row; an ordinary street in reach fronts instead, unless the lane is much nearer
REAR_HIGHWAYS = frozenset({'service', 'track'})
# a back lane beats an ordinary street only when under this fraction of its distance
REAR_NEARER = 0.5
# a row is spread along one axis when that spread is at least this many times the other
ROW_AXIS_CLEAR = 2.0
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
    validated district document, or None). None, and a profile that omits the
    class, fall back to the land-use class default. ``streets`` is a list of
    polylines in block coordinates. Buildings within one block of each other are one row.

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
        prepared.append(_prepare(raw, way_id, streets))
    groups = _components(prepared)
    specs = []
    for group in groups:
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


def load_buildings(index, *, way_ids=None, bbox=None, lidar=None, profile_at=None, rises=None):
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
    @param rises: optional ``cells -> {column: metres}`` (tests); sampled for buildings whose class has a
        body recipe only, default reads the lidar cache
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
    rises_fn = rises or _lidar_rises
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
        if _body_params(row['tags']):
            heights = rises_fn(row['cells'])
            if heights:
                buildings[-1]['rises'] = heights
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


def _prepare(raw: dict, way_id: str, streets=()) -> dict:
    """One footprint's decision, or the first reason it cannot be decided."""
    cells = _as_cells(raw.get('cells'))
    base = {'way_id': way_id, 'reason': None, 'box': None, 'cells': cells or set(), 'tags': {}}
    tags = dict(raw.get('tags') or {})
    box = _footprint(cells, tags.get('building') in HOUSE_TAGS) if cells else None
    base.update(box=box, tags=tags)
    if box is None:
        base['reason'] = 'odd footprint'
        return base
    cls, class_reason = _class_of(tags)
    if class_reason:
        base['reason'] = class_reason
        return base
    section, source, section_reason = _appearance(raw.get('profile'), cls)
    if section_reason:
        base['reason'] = section_reason
        return base
    body = _body_evidence(cls, tags, raw, streets)
    lidar_note = None
    if body:
        # lidar eave and ridge are the body's height truth; building:levels is not needed and does not veto
        levels, level_reason, levels_source = terrace_body.storeys_from_eave(body[0]), None, 'body'
    else:
        levels, level_reason = _levels(tags)
        if level_reason == 'missing building:levels':
            where = 'district profile' if source == 'district' else 'class default'
            levels, level_reason = _single_storey(section, where)
            levels_source = source
        else:
            levels_source = 'tag'
        if level_reason is None:
            lidar_note = _lidar_agrees(raw.get('lidar_m'), levels)
    # An eave count, a tag, or a pinned district default is refused when it sits
    # outside the district range. The class default is only a fallback.
    if level_reason is None and source == 'district' and _outside_storeys(section, levels):
        level_reason = 'storeys outside the district range'
    if level_reason:
        base['reason'] = level_reason
        return base
    house = cls == 'residential' and tags.get('building') in HOUSE_TAGS
    if house and levels != HOUSE_STOREYS:
        base['reason'] = 'house builder is two storeys'
        return base
    form, roof_source, roof_reason = _roof_form(tags, section, source)
    if roof_reason:
        base['reason'] = roof_reason
        return base
    family, wall, material_reason = _material(section)
    if material_reason:
        base['reason'] = material_reason
        return base
    if body and form not in PITCHED_FORMS:
        form = 'gable'   # the lidar ridge says pitched; a district default of flat does not override it
    slope = 'pitched' if form in PITCHED_FORMS else 'flatish'
    resolved = roof_blocks(family, slope)
    if not resolved.get('known') or not resolved.get('ok'):
        base['reason'] = 'palette has no roof material for this slope'
        return base
    if source == 'district':
        profile_name = str((raw.get('profile') or {}).get('name') or '').strip() or 'district'
    else:
        profile_name = 'land-use class default'
    base.update(
        cls=cls, levels=levels, form=form, roof_source=roof_source, family=family, wall=wall,
        path='house' if house else 'body' if body else 'feature',
        rises=raw.get('rises') if body else None, body_params=body[2] if body else None,
        eave_m=body[0] if body else None, ridge_m=body[1] if body else None,
        profile_name=profile_name, section_source=source, levels_source=levels_source,
        lidar_note=lidar_note,
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


def _body_params(tags: dict) -> dict | None:
    """The class's terrace-house body recipe for a building's own tags, or None (class has none)."""
    cls, _why = land_use.building_class(tags)
    return land_use.body_recipe(cls) if cls else None


def _body_evidence(cls, tags: dict, raw: dict, streets=()):
    """``(eave_m, ridge_m, recipe)`` when the class has a body recipe and lidar reads as a pitched body, else None.

    Lidar is read over the body columns, not the front strip, using the front the street gives this
    building. A ``roof:shape=flat`` tag, a ridge less than the recipe's ``min_rise_m`` above the eave,
    missing lidar, or no street to face leaves the building on its ordinary path. Keyed on the land-use
    class table, so a new class gains the typology by adding a ``body`` row, not by code.
    """
    params = land_use.body_recipe(cls) if cls else None
    cells = _as_cells(raw.get('cells'))
    rises = raw.get('rises')
    if params is None or tags.get('roof:shape') == 'flat' or not cells or not isinstance(rises, dict):
        return None
    front, _reason = _front(cells, streets, None)
    heights = terrace_body.eave_ridge(rises, cells, front, params) if front else None
    if heights is None or not terrace_body.reads_pitched(*heights, params):
        return None
    return heights[0], heights[1], params


def _footprint(cells: set[tuple[int, int]], house: bool) -> tuple[int, int, int, int] | None:
    """Bounding box of a footprint the builder can fit, else None (sliver or multi-part shape).

    Any orientation is accepted. A house fills enough of its bounding box; a shop needs enough columns.
    A footprint in more than one separate part is not one building.
    """
    if len(cells) < (HOUSE_MIN_CELLS if house else SHOP_MIN_CELLS) or _parts(cells) != 1 \
            or not _eroded(cells, THIN_STEPS):
        return None
    xs = [x for x, _z in cells]
    zs = [z for _x, z in cells]
    x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
    if house and len(cells) < HOUSE_MIN_FILL * (x1 - x0 + 1) * (z1 - z0 + 1):
        return None
    return (x0, z0, x1, z1)


def _eroded(cells: set[tuple[int, int]], steps: int) -> set[tuple[int, int]]:
    """Columns left after dropping, ``steps`` times, every column with a 4-neighbour outside the footprint."""
    for _ in range(steps):
        cells = {(x, z) for x, z in cells
                 if all(n in cells for n in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)))}
    return cells


def _parts(cells: set[tuple[int, int]]) -> int:
    """Number of separate pieces, joining columns that touch at a corner."""
    left = set(cells)
    parts = 0
    while left:
        parts += 1
        stack = [left.pop()]
        while stack:
            x, z = stack.pop()
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    if (x + dx, z + dz) in left:
                        left.remove((x + dx, z + dz))
                        stack.append((x + dx, z + dz))
    return parts


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


def _appearance(profile, cls: str) -> tuple[dict | None, str | None, str | None]:
    """District class section, or the land-use class default when the profile does not speak.

    A profile that has a section for the class is used as written, including when
    that section cannot supply a palette. A missing profile, or a profile that
    omits the class, falls back to :func:`land_use.typology_defaults`.

    @param profile: district document, or None
    @param cls: land-use class id
    @returns: ``(section, source, reason)``; source is ``district`` or ``class``
    """
    if isinstance(profile, dict):
        classes = profile.get('classes')
        section = classes.get(cls) if isinstance(classes, dict) else None
        if isinstance(section, dict):
            palette = section.get('palette')
            if not isinstance(palette, list) or not palette:
                return None, None, 'no district palette'
            return section, 'district', None
    section = land_use.typology_defaults(cls)
    if not section or not isinstance(section.get('palette'), list) or not section.get('palette'):
        return None, None, 'no class default'
    return section, 'class', None


def _single_storey(section: dict, where: str) -> tuple[int | None, str | None]:
    """The storey count when min and max agree, else a needs-designer reason.

    @param section: district class section or class-default section
    @param where: ``district profile`` or ``class default``, named in the reason
    @returns: ``(levels, None)`` or ``(None, reason)``
    """
    storeys = section.get('storeys') if isinstance(section, dict) else None
    if not isinstance(storeys, dict):
        return None, f'{where} does not give a single storey count'
    low, high = storeys.get('min'), storeys.get('max')
    if type(low) is not int or type(high) is not int or low < 1 or high < 1 or low > high:
        return None, f'{where} does not give a single storey count'
    if low != high:
        return None, f'{where} storeys min and max differ'
    return low, None


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
    """Advisory note when median lidar is missing or more than a storey off the count.

    The caller records this on the spec. It is never a reason to refuse the building.
    """
    if isinstance(lidar_m, bool) or not isinstance(lidar_m, (int, float)) or not math.isfinite(lidar_m):
        return 'missing lidar height'
    if abs(float(lidar_m) - levels * STOREY_M) > LIDAR_TOL_M:
        return 'lidar height disagrees with building:levels'
    return None


def _outside_storeys(section: dict, levels: int) -> bool:
    """True when the district section states a storey range and the count sits outside it."""
    storeys = section.get('storeys')
    if storeys is None:
        return False
    if not isinstance(storeys, dict):
        return True
    low, high = storeys.get('min'), storeys.get('max')
    return type(low) is not int or type(high) is not int or not low <= levels <= high


def _roof_form(tags: dict, section: dict, source: str) -> tuple[str | None, str | None, str | None]:
    """Roof form, where it came from, and a reason when it cannot be chosen.

    ``roof:shape`` wins. Otherwise the district or the class default must list
    exactly one form.

    @param source: ``district`` or ``class``, recorded when the form is not from a tag
    """
    raw = tags.get('roof:shape')
    if raw not in (None, ''):
        if not isinstance(raw, str) or raw not in ROOF_SHAPES:
            return None, None, 'unknown roof:shape'
        return ROOF_SHAPES[raw], 'tag', None
    roofs = section.get('roofs')
    if not isinstance(roofs, list) or not roofs:
        return None, None, 'missing roof form' if source == 'district' else 'no class default'
    if len(roofs) != 1:
        if source == 'class':
            return None, None, 'class default lists more than one roof form'
        return None, None, 'district lists more than one roof form'
    if roofs[0] not in PROFILE_ROOFS:
        return None, None, 'unknown roof form'
    return roofs[0], source, None


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


def _components(prepared: list[dict]) -> list[list[dict]]:
    """Rows: decided footprints of one kind within ROW_GAP empty columns of each other.

    A terrace, semi or detached count depends on every neighbour, so a house row that sits next to a
    refused footprint is refused with it. Shops and bodies are built one by one, so a refused
    neighbour does not hold them back.
    """
    boxed = [row for row in prepared if row.get('box')]
    by_way = {row['way_id']: row for row in boxed}
    owner = {}
    for row in boxed:
        for cell in row['cells']:
            owner.setdefault(cell, row['way_id'])
    reach = range(-ROW_GAP - 1, ROW_GAP + 2)
    near = {way: set() for way in by_way}
    for row in boxed:
        for x, z in row['cells']:
            for dx, dz in itertools.product(reach, repeat=2):
                other = owner.get((x + dx, z + dz))
                if other and other != row['way_id']:
                    near[row['way_id']].add(other)
    parent = {row['way_id']: row['way_id'] for row in boxed if not row['reason']}

    def find(way_id: str) -> str:
        while parent[way_id] != way_id:
            parent[way_id] = parent[parent[way_id]]
            way_id = parent[way_id]
        return way_id

    for way in parent:
        for other in near[way]:
            if other in parent and by_way[other]['path'] == by_way[way]['path']:
                parent[find(way)] = find(other)
    groups = defaultdict(list)
    for way in parent:
        groups[find(way)].append(by_way[way])
    rows = []
    for group in groups.values():
        if group[0]['path'] == 'house' and any(
                by_way[other]['reason'] for row in group for other in near[row['way_id']]):
            for row in group:
                row['reason'] = 'attached building cannot be decided'
            continue
        rows.append(group)
    return rows


def _row_axis(group: list[dict]) -> str | None:
    """``x`` or ``z``, the way the row's footprints are spread (centroids), or None for one building."""
    if len(group) < 2:
        return None
    centres = [_centroid(row['cells']) for row in group]
    spread_x = max(c[0] for c in centres) - min(c[0] for c in centres)
    spread_z = max(c[1] for c in centres) - min(c[1] for c in centres)
    return 'x' if spread_x >= spread_z else 'z'


def _row_reason(group: list[dict], streets) -> tuple[str | None, str | None]:
    """Why a clean component still cannot be one spec, and its row axis."""
    axis = _row_axis(group)
    if len({row['profile_name'] for row in group}) > 1:
        return 'districts differ', axis
    if group[0]['path'] == 'house':
        kind = _house_kind(group)
        if kind is None:
            return 'tag disagrees with the footprint', axis
        if len({row['levels'] for row in group}) > 1:
            return 'storeys differ', axis
        if len({row['form'] for row in group}) > 1:
            return 'roof forms differ', axis
    front, front_reason = _row_front(group, streets, axis)
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


def _row_front(group: list[dict], streets, axis: str | None) -> tuple[str | None, str | None]:
    """The front a row shares: the one most of its footprints give for the street each faces.

    A row spread clearly along one axis keeps the perpendicular rule for every footprint. A row
    spread about evenly in x and z (a street running at an angle to the grid) is rotated, so each
    footprint takes whichever cardinal its own street direction leans on, and the row follows the
    majority. One building, or no footprint with a front of its own, falls back to the whole row's
    centroid and its reason. A row whose centroid has no front (the nearest street sits off one end)
    can now get one from its footprints; a clearly straight row keeps the perpendicular rule for each.
    """
    whole = _front(_cells_of(group), streets, axis)
    if len(group) < 2:
        return whole
    centres = [_centroid(row['cells']) for row in group]
    spread_x = max(c[0] for c in centres) - min(c[0] for c in centres)
    spread_z = max(c[1] for c in centres) - min(c[1] for c in centres)
    clear = max(spread_x, spread_z) >= ROW_AXIS_CLEAR * min(spread_x, spread_z)
    votes = Counter(_front(row['cells'], streets, axis if clear else None)[0] for row in group)
    votes.pop(None, None)
    if not votes:
        return whole
    best = max(votes.values())
    leaders = [side for side, count in votes.items() if count == best]
    return (whole[0] if whole[0] in leaders else sorted(leaders)[0]), None


def _front(cells: set[tuple[int, int]], streets, axis: str | None) -> tuple[str | None, str | None]:
    """Cardinal facing the nearest street; perpendicular to the row when there is one.

    The street's direction from the centroid picks whichever allowed axis it leans on more, so a
    diagonal road still gives a front. Only no street in reach, or a centroid on the street, has none.
    """
    hit = _facing(_centroid(cells), streets)
    if hit is None or hit[0] > FRONT_REACH:
        return None, 'no street to face'
    _dist, px, pz = hit
    cx, cz = _centroid(cells)
    dx, dz = px - cx, pz - cz
    if axis == 'x' or (axis is None and abs(dz) >= abs(dx)):
        dx = 0.0
    else:
        dz = 0.0
    if max(abs(dx), abs(dz)) < FRONT_MIN_OFFSET:
        return None, 'no clear front'
    if dz:
        return ('S' if dz > 0 else 'N'), None
    return ('E' if dx > 0 else 'W'), None


def _centroid(cells: set[tuple[int, int]]) -> tuple[float, float]:
    """Centre of the block columns."""
    return (sum(x + 0.5 for x, _z in cells) / len(cells),
            sum(z + 0.5 for _x, z in cells) / len(cells))


class RearLine(list):
    """A street polyline for a back lane (service or track way): it fronts only when nothing ordinary does."""


def _facing(origin: tuple[float, float], streets) -> tuple[float, float, float] | None:
    """The street point a building at ``origin`` faces: distance and point.

    An ordinary street within FRONT_REACH wins over a back lane, unless the lane is less than
    ``REAR_NEARER`` times as far (a mews or farm on its own service road keeps its lane). With no
    ordinary street in reach, the nearest lane of any kind is used.
    """
    ordinary = _closest(origin, [line for line in streets if not isinstance(line, RearLine)])
    rear = _closest(origin, [line for line in streets if isinstance(line, RearLine)])
    if ordinary is None or ordinary[0] > FRONT_REACH:
        return rear if rear is not None else ordinary
    if rear is not None and rear[0] < REAR_NEARER * ordinary[0]:
        return rear
    return ordinary


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
    front, _reason = _row_front(group, streets, axis)
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
        if kind == 'terrace':
            return {'builder': 'house_build', 'spec': _terrace_spec(
                name, note, ordered, ways, labels, front, pitch)}
        spec = {
            'name': name,
            'notes': note,
            'ways': ways,
            'roofs': {row['label']: row['family'] for row in ordered},
            'roof_pitch': {row['label']: pitch for row in ordered},
            'groups': [{'nos': labels, 'mode': 'each', 'front': front}],
        }
        return {'builder': 'house_build', 'spec': spec}
    if ordered[0]['path'] == 'body':
        return {'builder': 'house_build', 'spec': _body_spec(name, note, ordered, ways, front)}
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
    spec.update(_class_facade(ordered, front))
    return {'builder': 'feature_build', 'spec': spec}


def _class_facade(ordered: list[dict], front: str | None) -> dict:
    """``signs`` and ``facade_plan`` from each class's shopfront recipe (#431), or ``{}``.

    A class's ``sign`` palette (wood, text colour) goes into ``signs`` for every label. Its ``facade``
    recipe becomes ``facade_plan`` only for a building the class ``street_face`` recipe does not
    already dress: the builder drops that dressing (glass, door, fascia, sign, awning) and the storey
    window grid for any label with an evidenced facade face. A class with neither key adds nothing.
    Both are defaults; a hand spec replaces a label's row.

    @param ordered: the row's decided buildings with ``label``
    @param front: the row's front, ``N``/``S``/``E``/``W``
    @returns: a mapping with ``signs`` and/or ``facade_plan``
    """
    signs, plan = {}, {}
    for row in ordered:
        palette = land_use.sign_palette(row['cls'])
        if palette:
            signs[row['label']] = palette
        recipe = land_use.facade_recipe(row['cls'])
        if not recipe or front not in tuple('NSEW'):
            continue
        dressed = land_use.street_face_defaults(
            row['cls'], row['tags'], columns=len(row['cells']), height=row['levels'] * STOREY_M)
        if not dressed:
            plan[row['label']] = _facade_plan(recipe, row['levels'], front)
    extra = {}
    if signs:
        extra['signs'] = signs
    if plan:
        extra['facade_plan'] = plan
    return extra


def _facade_plan(recipe: dict, storeys: int, front: str) -> dict:
    """One label's ``facade_plan``: glazing rows on every face and one entrance on the front.

    The front takes the ground row on storey 1 and the upper row above it; the other three faces take
    the side row (or the upper row when the recipe has no side row) on every storey. A face with no
    row would get no windows, so a recipe with no glazing at all gives no faces.
    """
    front_rows = recipe.get('front')
    side = recipe.get('side') or (front_rows or {}).get('upper')
    faces = {}
    for face in 'NSEW':
        if face == front and front_rows:
            rows = [{'storey': 1, **front_rows['ground']}] + [
                {'storey': n, **front_rows['upper']} for n in range(2, storeys + 1)]
        elif side:
            rows = [{'storey': n, **side} for n in range(1, storeys + 1)]
        else:
            continue
        faces[face] = {'glazing': rows}
    body = {'faces': faces} if faces else {}
    entrance = recipe.get('entrance')
    if entrance:
        door = {
            'id': 'front', 'face': front, 'leaves': entrance['leaves'], 'block': entrance['block'],
            'provenance': {'source': 'inference', 'confidence': 'low', 'evidence_ids': [],
                           'unresolved_reason': ''},
        }
        if entrance.get('awning'):
            door['awning_like'] = dict(entrance['awning'])
        body['entrances'] = [door]
    return body


def _terrace_spec(
        name: str, note: str, ordered: list[dict], ways: dict, labels: list[str],
        front: str, pitch: float) -> dict:
    """House_build terrace recipe: per-house roofs, party-wall stacks, alternating colours (#441).

    Every house is its own roof unit (mode ``each``). Chimneys use the domestic
    ``at`` list: one stack column on each party wall from
    ``footprint_party_walls``. When the row's appearance came from the class
    default and the form is pitched, roof families cycle the class
    ``terrace_roofs`` palette per consecutive pair; a district profile, a flat
    form, or a missing palette keeps the ordinary family for every house.
    """
    pitched = ordered[0]['form'] in PITCHED_FORMS
    palette = (
        land_use.terrace_roof_families(ordered[0]['cls'])
        if pitched and ordered[0].get('section_source') == 'class' else None
    )
    families = palette or [ordered[0]['family']]
    roofs = {}
    for index, row in enumerate(ordered):
        roofs[row['label']] = families[(index // 2) % len(families)]
    return {
        'name': name,
        'notes': note,
        'ways': ways,
        'roofs': roofs,
        'roof_pitch': {label: pitch for label in labels},
        'groups': [{'nos': labels, 'mode': 'each', 'front': front}],
        'chimneys': _terrace_chimneys(ordered),
    }


def _terrace_chimneys(ordered: list[dict]) -> dict:
    """Per-label domestic chimney ``at`` lists: one stack on each party wall.

    @param ordered: terrace houses left-to-right (or southwards)
    @returns: chimneys map for house_build (``none`` when a house has no wall)
    """
    sites = {row['label']: [] for row in ordered}
    for i, j, wall in footprint_party_walls([row['cells'] for row in ordered]):
        col = min(wall)
        owner = ordered[i] if col in ordered[i]['cells'] else ordered[j]
        sites[owner['label']].append({'at': [col[0], col[1]]})
    return {label: entries if entries else 'none' for label, entries in sites.items()}


def _body_spec(name: str, note: str, ordered: list[dict], ways: dict, front: str) -> dict:
    """A house_build spec for terrace-house bodies: one roof per building, numbers from lidar and the class recipe (#333)."""
    block = {}
    for row in ordered:
        # re-read the lidar for the row's final front, which can differ from the one a lone building had
        eave, ridge = (terrace_body.eave_ridge(row['rises'], row['cells'], front, row['body_params'])
                       or (row['eave_m'], row['ridge_m']))
        block[row['label']] = terrace_body.recipe(eave, ridge, row['cells'], front, row['body_params'])
    return {
        'name': name,
        'notes': note + ' Terrace-house body from lidar eave and ridge (lib/builds/terrace_body.py).',
        'ways': ways,
        'roofs': {row['label']: row['family'] for row in ordered},
        'roof_pitch': {label: item.pop('roof_pitch') for label, item in block.items()},
        'body': block,
        'groups': [{'nos': [row['label'] for row in ordered], 'mode': 'each', 'front': front}],
    }


def _order(group: list[dict], axis: str | None) -> list[dict]:
    """Left to right, or southwards when the row runs that way."""
    def key(row):
        cx, cz = _centroid(row['cells'])
        return (cz, cx, row['way_id']) if axis == 'z' else (cx, cz, row['way_id'])
    return sorted(group, key=key)


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
    roof = _roof_sentence(group)
    return (f"Typology {phrase}. Ways {ways}. {roof} {_material_sentence(group)}"
            f"{_storey_clause(group)}{_lidar_clause(group)}")


def _roof_sentence(group: list[dict]) -> str:
    """One sentence naming the roof form and whether a tag, the district or the class default chose it."""
    labels = {
        'tag': 'roof:shape',
        'district': 'the district profile',
        'class': 'the land-use class default',
    }
    sources = {row['roof_source'] for row in group}
    if len(sources) == 1:
        source = labels[next(iter(sources))]
    else:
        source = ' or '.join(labels[key] for key in ('tag', 'district', 'class') if key in sources)
    forms = list(dict.fromkeys(row['form'] for row in group))
    if len(forms) == 1:
        return f"Roof {forms[0]} from {source}."
    return "Roofs follow each building's roof:shape."


def _material_sentence(group: list[dict]) -> str:
    """Palette provenance. A class-table fallback is named; a district profile keeps its own name."""
    row = group[0]
    if row.get('section_source') == 'class':
        return f"Material {row['family']} from the land-use class default ({row['cls']})."
    return f"Material {row['family']} from the district palette ({row['profile_name']})."


def _storey_clause(group: list[dict]) -> str:
    """Name storeys that were not taken from building:levels. Empty when every count is a tag."""
    rows = [row for row in group if row.get('levels_source') in ('district', 'class')]
    if not rows:
        return ''
    if len(rows) == len(group) and len({(row['levels'], row['levels_source']) for row in rows}) == 1:
        where = 'the district profile' if rows[0]['levels_source'] == 'district' else 'the land-use class default'
        return f" Storeys {rows[0]['levels']} from {where}."
    bits = []
    for row in rows:
        where = 'the district profile' if row['levels_source'] == 'district' else 'the land-use class default'
        bits.append(f"Way {row['way_id']} storeys {row['levels']} from {where}.")
    return ' ' + ' '.join(bits)


def _lidar_clause(group: list[dict]) -> str:
    """Advisory lidar sentences. Missing and disagreeing heights are notes, not refusals."""
    notes = [row.get('lidar_note') for row in group]
    if not any(notes):
        return ''
    phrases = {
        'missing lidar height': 'Lidar height missing.',
        'lidar height disagrees with building:levels': 'Lidar height disagrees with building:levels.',
    }

    def sentence(note: str) -> str:
        return phrases.get(note, note if note.endswith('.') else note + '.')

    if len(set(notes)) == 1 and notes[0]:
        return ' ' + sentence(notes[0])
    bits = [f"Way {row['way_id']}: {sentence(note)}"
            for row, note in zip(group, notes) if note]
    return ' ' + ' '.join(bits)


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
    """Selected footprints plus every footprint within ROW_GAP columns of one of them."""
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
                for dx, dz in itertools.product(range(-ROW_GAP - 1, ROW_GAP + 2), repeat=2):
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
    for _name, highway, points in streets:
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
            line = [(float(x), float(z)) for x, z in points]
            kept.append(RearLine(line) if highway in REAR_HIGHWAYS else line)
    return kept


def _lidar_median(cells: set[tuple[int, int]]) -> float | None:
    """Median DSM minus DTM over the footprint, or None when the cache cannot say."""
    values = list(_lidar_rises(cells).values())
    return float(statistics.median(values)) if values else None


def _lidar_rises(cells: set[tuple[int, int]]) -> dict[tuple[int, int], float]:
    """DSM minus DTM (metres) at each footprint column the cache covers, or an empty dict."""
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
            return {}
        heights = lp.mc_heights_float(client, x0, z0, x1, z1)
    except (LookupError, FileNotFoundError, OSError, RuntimeError):
        return {}
    dsm = np.asarray(heights['dsm'], dtype=float)
    dtm = np.asarray(heights['dtm'], dtype=float)
    values = {}
    for x, z in cells:
        i, j = x - x0, z - z0
        if not (0 <= i < dsm.shape[0] and 0 <= j < dsm.shape[1]):
            continue
        rise = float(dsm[i, j] - dtm[i, j])
        if math.isfinite(rise):
            values[(x, z)] = rise
    if len(values) * 2 < len(cells):
        return {}
    return values


def _profile_at(cell: tuple[int, int]):
    """The active world's district profile for one cell, or None."""
    import garden_sources
    return garden_sources.load_district(cell)


if __name__ == '__main__':
    raise SystemExit(main())
