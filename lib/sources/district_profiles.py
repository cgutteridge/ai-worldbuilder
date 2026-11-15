"""District area evidence: validate small, vendor-independent profiles, never observations.

File loading lives beside ``garden_sources.load_convention``. This module only validates
JSON and selects the boundary/furniture sections; building appearance remains unread.
"""
from __future__ import annotations

import copy
import re

import land_use

BOUNDARY_TYPES = ('none', 'hedge', 'fence', 'brick_wall', 'iron_bars')
HIGHWAYS = ('motorway', 'motorway_link', 'trunk', 'trunk_link', 'primary', 'primary_link',
            'secondary', 'secondary_link', 'tertiary', 'tertiary_link', 'unclassified',
            'residential', 'living_street', 'service', 'pedestrian', 'footway', 'path',
            'cycleway', 'bridleway', 'steps', 'track', 'road', 'busway')


class ProfileError(ValueError):
    """Malformed or overlapping district evidence; never silently ignored."""


def _object(value, keys, where):
    if not isinstance(value, dict) or set(value) - set(keys):
        raise ProfileError(f'{where}: expected object with keys {tuple(keys)}')


def _strings(value, where):
    if not isinstance(value, list) or not value or any(not isinstance(v, str) or not v.strip() for v in value):
        raise ProfileError(f'{where}: expected a nonempty list of strings')


def _number(value, low, high, where):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ProfileError(f'{where}: expected a number between {low} and {high}')


def _block(value, where):
    if not isinstance(value, str) or not re.fullmatch(r'(minecraft:)?[a-z0-9_]+(\[[a-z0-9_=,]+\])?', value):
        raise ProfileError(f'{where}: expected a block id with optional states')


def cell_rectangles(cells) -> list[tuple[int, int, int, int]]:
    """Validate cell pairs / inclusive ``{min: [tx,tz], max: [tx,tz]}`` rectangles.

    @param cells: profile's cells list
    @returns: rectangles without expanding large areas into individual cells
    """
    if not isinstance(cells, list) or not cells:
        raise ProfileError('cells: expected a nonempty list')
    out = []
    for entry in cells:
        if isinstance(entry, dict):
            _object(entry, ('min', 'max'), 'cell rectangle')
            a, b = entry.get('min'), entry.get('max')
        else:
            a = b = entry
        if any(not isinstance(p, list) or len(p) != 2 or any(type(v) is not int for v in p) for p in (a, b)):
            raise ProfileError('cells: expected integer pairs or {min, max} pairs')
        if a[0] > b[0] or a[1] > b[1]:
            raise ProfileError('cells: rectangle min must not exceed max')
        out.append((a[0], a[1], b[0], b[1]))
    return out


def rectangles_overlap(a, b) -> bool:
    """Whether two inclusive cell rectangles intersect (including a shared edge cell)."""
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _boundaries(frontage, where, *, run=False):
    _object(frontage, ('boundary', 'materials') if run else ('boundary', 'materials', 'runs'), where)
    boundary = frontage.get('boundary', {})
    _object(boundary, land_use.BOUNDARY_ROLES, where + '.boundary')
    for role, spec in boundary.items():
        if spec is None:
            continue
        _object(spec, ('type', 'block', 'height', 'density'), f'{where}.{role}')
        if spec.get('type') not in BOUNDARY_TYPES:
            raise ProfileError(f'{where}.{role}: unknown boundary type')
        if 'density' in spec:
            _number(spec['density'], 0, 1, f'{where}.{role}.density')
        _material(spec, f'{where}.{role}')
    materials = frontage.get('materials', {})
    _object(materials, BOUNDARY_TYPES[1:], where + '.materials')
    for kind, material in materials.items():
        _object(material, ('block', 'height'), f'{where}.materials.{kind}')
        if 'block' not in material:
            raise ProfileError(f'{where}.materials.{kind}: missing block')
        _material(material, f'{where}.materials.{kind}')


def _material(spec, where):
    if 'block' in spec:
        _block(spec['block'], where + '.block')
    if 'height' in spec and (type(spec['height']) is not int or not 1 <= spec['height'] <= 8):
        raise ProfileError(f'{where}: height must be an integer from 1 to 8')


def validate_profile(doc: dict) -> dict:
    """Validate one profile without consuming reserved appearance or planting sections.

    @param doc: parsed JSON; sections are under ``classes.<land-use id>``
    @returns: unchanged document; raises ProfileError on malformed data
    """
    _object(doc, ('name', 'cells', 'evidence_ids', 'classes'), 'profile')
    if not isinstance(doc.get('name'), str) or not doc['name'].strip():
        raise ProfileError('profile: missing name')
    cell_rectangles(doc.get('cells'))
    _strings(doc.get('evidence_ids'), 'evidence_ids')
    classes = doc.get('classes')
    _object(classes, land_use.CLASSES, 'classes')
    for cid, section in classes.items():
        _object(section, ('frontage', 'planting', 'furniture', 'palette', 'roofs', 'storeys'), cid)
        if cid in land_use.NO_INFERENCE and any(section.get(k) for k in ('frontage', 'planting', 'furniture')):
            raise ProfileError(f'{cid}: this class allows no inference')
        fr = section.get('frontage')
        if fr is not None:
            _boundaries(fr, cid + '.frontage')
            runs = fr.get('runs', {})
            if not isinstance(runs, dict):
                raise ProfileError(f'{cid}.runs: expected an object keyed by run name')
            for name, run in runs.items():
                _object(run, ('street', 'numbers', 'boundary', 'materials'), f'{cid}.runs.{name}')
                if not isinstance(run.get('street'), str) or not run['street'].strip():
                    raise ProfileError(f'{cid}.runs.{name}: missing street')
                if 'numbers' in run:
                    _strings(run['numbers'], f'{cid}.runs.{name}.numbers')
                _boundaries({k: v for k, v in run.items() if k in ('boundary', 'materials')}, name, run=True)
        furniture = section.get('furniture')
        if furniture is not None:
            _object(furniture, ('lamp_spacing', 'bench_per_100m'), cid + '.furniture')
            lamps = furniture.get('lamp_spacing', {})
            _object(lamps, HIGHWAYS, cid + '.lamp_spacing')
            for highway, spacing in lamps.items():
                if spacing is not None:
                    _number(spacing, 1, 1000, cid + '.lamp_spacing.' + highway)
            if 'bench_per_100m' in furniture:
                _number(furniture['bench_per_100m'], 0, 100, cid + '.bench_per_100m')
        if section.get('planting') is not None:
            row = copy.deepcopy(land_use.defaults(cid))
            row['planting'] = section['planting']
            table = {'classes': [row if c == cid else r for c, r in land_use.load_table()['classes'].items()]}
            try:
                land_use.validate_table(table)
            except land_use.TableError as exc:
                raise ProfileError(str(exc)) from exc
        for key in ('palette', 'roofs'):
            value = section.get(key)
            if value is not None:
                _strings(value, cid + '.' + key)
                if key == 'palette':
                    for block in value:
                        _block(block, cid + '.palette')
                elif set(value) - {'flat', 'gable', 'hip', 'mansard', 'shed'}:
                    raise ProfileError(f'{cid}: unknown roof form')
        if section.get('storeys') is not None:
            s = section['storeys']
            _object(s, ('min', 'max'), cid + '.storeys')
            if any(type(s.get(k)) is not int or not 1 <= s[k] <= 100 for k in ('min', 'max')) or s['min'] > s['max']:
                raise ProfileError(f'{cid}: storeys must be {{min, max}} integers from 1 to 100 in order')
    return doc


def boundary_layer(profile: dict | None, cls: str, street: str, number: str = '') -> dict | None:
    """Select area/run boundary evidence only, flattened for ``land_use.resolve``.

    Run keys are human names; street and optional house numbers select the run. Ambiguous
    matches fail rather than depending on JSON order. Appearance/planting have no consumers.
    """
    if not profile or cls not in land_use.CLASSES or cls in land_use.NO_INFERENCE:
        return None
    fr = profile['classes'].get(cls, {}).get('frontage')
    if fr is None:
        return None
    canonical = lambda s: re.sub(r'[^a-z0-9]+', '_', s.lower()).strip('_')
    runs = [r for r in fr.get('runs', {}).values() if canonical(r['street']) == canonical(street)
            and ('numbers' not in r or str(number) in r['numbers'])]
    if len(runs) > 1:
        raise ProfileError(f'{profile["name"]}: overlapping street runs for {street} {number}')
    run = runs[0] if runs else {}
    out = {**fr.get('boundary', {}), **run.get('boundary', {})}
    out['materials'] = {**fr.get('materials', {}), **run.get('materials', {})}
    out['evidence_ids'] = profile['evidence_ids']
    out['name'] = profile['name']
    out['street'] = canonical(street)
    return out
