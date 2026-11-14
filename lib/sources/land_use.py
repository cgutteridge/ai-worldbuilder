"""Land-use classes (#253): map tags to one of eleven classes, plus the per-class defaults table.

Pure module: tags in, class out. Rasterising polygons onto columns lives in
``coverage.area_classes``. The mapping and the defaults live together in
``config/land-use-classes.json`` so a new class or tag is one edit. The class never paints
surfaces and never authorises a fence: every shipped boundary default is null.
Design: ``docs/plans/land-use-classes.md``.

Every classified thing is ``{'class', 'reason', 'ref'}``. Known classes carry the evidence that
decided them (``building_tag``, ``use_polygon``, ``cover_polygon``, ``house_number``); ``unknown``
carries one of ``not_checked`` (no clip that asked for landuse covers the column), ``unmapped``
(checked, nothing there) or ``unlisted`` (a tag value the table does not know). There is no
inheritance from neighbours.
"""
from __future__ import annotations

import json
from functools import lru_cache

from script_env import TOOL_ROOT

TABLE_PATH = TOOL_ROOT / 'config' / 'land-use-classes.json'

CLASSES = ('residential', 'commercial', 'industrial', 'civic', 'park', 'rural', 'natural',
           'water', 'transport', 'parking', 'unknown')
UNKNOWN_REASONS = ('not_checked', 'unmapped', 'unlisted')
BOUNDARY_ROLES = ('front', 'side_front', 'side_back', 'rear', 'perimeter')
PRIVATE_SURFACES = ('grass', 'paving', 'keep')
OPENINGS = ('door_route', 'street_gate', 'none')
DEFAULT_PAVEMENT = 2.5      # blocks of public pavement between kerb and private frontage (#254)
NO_INFERENCE = ('rural', 'natural', 'water', 'unknown')

# Area keys in match order (the rest follow in this order too). ``*`` in the table matches any value.
AREA_KEY_ORDER = ('amenity', 'leisure', 'landuse', 'natural', 'man_made', 'power', 'waterway',
                  'aeroway', 'highway', 'shop', 'tourism')
# Building keys tested before the ``building`` value; an amenity value picks civic, transport or
# parking from the table's area lists, any other amenity is commercial.
BUILDING_AMENITY_CLASSES = ('civic', 'transport', 'parking')


class TableError(ValueError):
    """The land-use table is malformed."""


def _unknown(reason: str, ref=None) -> dict:
    return {'class': 'unknown', 'reason': reason, 'ref': ref}


def validate_table(doc: dict) -> dict:
    """Check a land-use table; return ``{'classes': {id: row}}`` or raise :class:`TableError`.

    Refuses: a missing or extra class id, a missing column, a tag value that maps to two classes
    (inside the building namespace, or inside the area namespace across use and cover), a bad
    boundary / surface / opening value, an ``unknown`` row that sets a boundary, planting or
    furniture value, and inference on ``rural``, ``natural``, ``water`` or ``unknown``.

    @param doc: parsed JSON table
    @returns: ``{'classes': {class id: row}}``
    """
    rows = doc.get('classes')
    if not isinstance(rows, list):
        raise TableError("table has no 'classes' list")
    out: dict[str, dict] = {}
    for row in rows:
        cid = row.get('id')
        if cid in out:
            raise TableError(f'duplicate class row {cid!r}')
        out[cid] = row
    if set(out) != set(CLASSES):
        raise TableError(f'class ids must be exactly {CLASSES}; got {sorted(out, key=str)}')
    seen_building: dict[str, str] = {}
    seen_area: dict[tuple, str] = {}
    for cid, row in out.items():
        for col in ('match', 'frontage', 'planting', 'furniture'):
            if col not in row:
                raise TableError(f'{cid}: missing column {col!r}')
        match = row['match']
        for col in ('building', 'use', 'cover'):
            if col not in match:
                raise TableError(f'{cid}: match is missing {col!r}')
        for value in match['building']:
            if value in seen_building:
                raise TableError(f'building={value} maps to {seen_building[value]} and {cid}')
            seen_building[value] = cid
        for part in ('use', 'cover'):
            for key, values in match[part].items():
                for value in values:
                    if (key, value) in seen_area:
                        raise TableError(f'{key}={value} maps to {seen_area[(key, value)]} and {cid}')
                    seen_area[(key, value)] = cid
        fr = row['frontage']
        for col in ('inference', 'requires', 'boundary', 'private_surface', 'opening'):
            if col not in fr:
                raise TableError(f'{cid}: frontage is missing {col!r}')
        if not isinstance(fr['inference'], bool):
            raise TableError(f'{cid}: inference must be true or false')
        if fr['private_surface'] not in PRIVATE_SURFACES:
            raise TableError(f'{cid}: private_surface {fr["private_surface"]!r} not in {PRIVATE_SURFACES}')
        if fr['opening'] not in OPENINGS:
            raise TableError(f'{cid}: opening {fr["opening"]!r} not in {OPENINGS}')
        pav = fr.get('pavement', DEFAULT_PAVEMENT)
        if isinstance(pav, bool) or not isinstance(pav, (int, float)) or not 0 < pav <= 8:
            raise TableError(f'{cid}: pavement must be a width in blocks, above 0 and at most 8')
        if set(fr['boundary']) - set(BOUNDARY_ROLES):
            raise TableError(f'{cid}: unknown boundary role in {sorted(fr["boundary"])}')
        for role, spec in fr['boundary'].items():
            if spec is not None and not (isinstance(spec, dict) and 'type' in spec and 'height' in spec):
                raise TableError(f'{cid}: boundary {role} must be null or {{type, height}}')
        if cid in NO_INFERENCE and fr['inference']:
            raise TableError(f'{cid}: this class allows no inference')
        planting = row['planting']
        if planting is not None:
            from foliage import REFINE_PLANTS

            if not fr['inference']:
                raise TableError(f'{cid}: this class allows no planting inference')
            if not isinstance(planting, dict) or set(planting) != {'shrubs', 'flowers', 'pond'}:
                raise TableError(f'{cid}: planting must be null or {{shrubs, flowers, pond}}')
            shrubs = planting['shrubs']
            if shrubs is not None:
                if not isinstance(shrubs, dict) or set(shrubs) != {'per_columns'}:
                    raise TableError(f'{cid}: shrubs must be null or {{per_columns}}')
                if type(shrubs['per_columns']) is not int or shrubs['per_columns'] < 1:
                    raise TableError(f'{cid}: shrubs per_columns must be an integer >= 1')
            flowers = planting['flowers']
            if flowers is not None:
                if not isinstance(flowers, dict) or set(flowers) != {'share', 'kinds'}:
                    raise TableError(f'{cid}: flowers must be null or {{share, kinds}}')
                share, kinds = flowers['share'], flowers['kinds']
                if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 < share <= 0.2:
                    raise TableError(f'{cid}: flowers share must be above 0 and at most 0.2')
                if not isinstance(kinds, list) or not 1 <= len(kinds) <= 2 or any(
                        not isinstance(k, str) or k not in REFINE_PLANTS for k in kinds):
                    raise TableError(f'{cid}: flowers kinds must be 1-2 names from foliage.REFINE_PLANTS')
            if planting['pond'] is not None:
                raise TableError(f'{cid}: planting pond must stay null')
        if cid == 'unknown' and (any(v is not None for v in fr['boundary'].values())
                                 or row['planting'] is not None or row['furniture'] is not None):
            raise TableError('unknown must not set a boundary, planting or furniture value')
    return {'classes': out}


@lru_cache(maxsize=1)
def load_table() -> dict:
    """Load and validate ``config/land-use-classes.json`` (cached).

    @returns: ``{'classes': {class id: row}}``
    """
    return validate_table(json.loads(TABLE_PATH.read_text()))


def defaults(cls: str) -> dict:
    """Per-class defaults row (frontage, planting, furniture) for a class id.

    @param cls: class id; anything not in :data:`CLASSES` falls to ``unknown``
    @returns: the table row
    """
    rows = load_table()['classes']
    return rows[cls if cls in rows else 'unknown']


def planting_rule(cls: str, kind: str) -> dict | None:
    """Return the class's planting rule, or None when it allows no inference of this kind."""
    row = defaults(cls)
    if not row['frontage']['inference'] or row['planting'] is None:
        return None
    return row['planting'].get(kind)


@lru_cache(maxsize=1)
def _index() -> dict:
    """Lookups built once: building value, area (key, value) -> (class, kind), civic/transport amenities."""
    rows = load_table()['classes']
    building: dict[str, str] = {}
    area: dict[tuple, tuple] = {}
    for cid, row in rows.items():
        for value in row['match']['building']:
            building[value] = cid
        for kind in ('use', 'cover'):
            for key, values in row['match'][kind].items():
                for value in values:
                    area[(key, value)] = (cid, kind)
    amenity = {v: cid for (k, v), (cid, _kind) in area.items()
               if k == 'amenity' and cid in BUILDING_AMENITY_CLASSES}
    any_key = [k for row in rows.values() for k in row['match'].get('building_any_key', [])]
    return {'building': building, 'area': area, 'amenity': amenity, 'any_key': any_key}


def building_class(tags: dict) -> tuple[str | None, str | None]:
    """Class a building's own tags decide, or ``(None, reason)`` when they decide nothing.

    Other tags are tested before the ``building`` value, so ``building=house`` with ``shop=*`` is
    commercial. ``reason`` is ``None`` for ``building=yes`` / outbuildings (inherit) and
    ``'unlisted'`` for a value the table does not know.

    @param tags: flattened tags
    @returns: ``(class id, None)`` or ``(None, 'unlisted' | None)``
    """
    ix = _index()
    if any(tags.get(k) for k in ix['any_key']):
        return 'commercial', None
    amenity = tags.get('amenity')
    if amenity:
        return ix['amenity'].get(amenity, 'commercial'), None
    value = tags.get('building')
    if value in ix['building']:
        return ix['building'][value], None
    if value in (None, '', 'yes') or value in OUTBUILDING_VALUES:
        return None, None
    return None, 'unlisted'


# Outbuildings take their plot's or polygon's class (same list as coverage.OUTBUILDINGS).
OUTBUILDING_VALUES = ('shed', 'garage', 'garages', 'roof', 'carport', 'greenhouse', 'hut')


def area_class(tags: dict) -> dict | None:
    """Class of a non-building polygon from its tags.

    First match in :data:`AREA_KEY_ORDER`. Returns ``None`` when the polygon carries no
    class-bearing key at all (a barrier outline, a pedestrian surface) or is an underground car park.
    A class-bearing key with an unknown value gives ``{'class': 'unknown', 'kind': None,
    'reason': 'unlisted', 'value': 'key=value'}``: counted for the report, it claims no column.

    @param tags: flattened tags
    @returns: ``{'class', 'kind': 'use'|'cover'|None, 'reason', 'value'}`` or ``None``
    """
    area = _index()['area']
    seen = None
    for key in AREA_KEY_ORDER:
        value = tags.get(key)
        if not value:
            continue
        if key == 'amenity' and value in ('parking', 'motorcycle_parking') \
                and tags.get('parking') == 'underground':
            return None
        hit = area.get((key, value)) or area.get((key, '*'))
        if hit:
            cid, kind = hit
            reason = 'unlisted' if cid == 'unknown' else f'{kind}_polygon'
            return {'class': cid, 'kind': kind, 'reason': reason, 'value': f'{key}={value}'}
        if seen is None and key not in ('highway', 'shop', 'tourism'):
            seen = f'{key}={value}'
    if seen:
        return {'class': 'unknown', 'kind': None, 'reason': 'unlisted', 'value': seen}
    return None


def classify_building(tags: dict, *, polygon: tuple | None = None, checked: bool = True,
                      ref=None) -> dict:
    """Class of one building.

    Own tags win; otherwise the smallest use polygon at the centroid (``polygon`` is
    ``(class, ref)`` or ``None``); otherwise residential with a house number; otherwise unknown.

    @param tags: flattened building tags
    @param polygon: ``(class, ref)`` of the smallest use polygon at the centroid, or None
    @param checked: whether a clip that asked for landuse covers the centroid
    @param ref: map reference of the building
    @returns: ``{'class', 'reason', 'ref'}``
    """
    cls, why = building_class(tags)
    if cls:
        return {'class': cls, 'reason': 'building_tag', 'ref': ref}
    if polygon:
        return {'class': polygon[0], 'reason': 'use_polygon', 'ref': polygon[1]}
    if tags.get('addr:housenumber'):
        return {'class': 'residential', 'reason': 'house_number', 'ref': ref}
    if why:
        return _unknown(why, ref)
    return _unknown('unmapped' if checked else 'not_checked', ref)


def classify_column(hit: tuple | None, *, checked: bool) -> dict:
    """Class of an open column from the area raster.

    @param hit: ``(class, ref, kind)`` from ``coverage.area_classes`` or None
    @param checked: whether a clip that asked for landuse covers the column
    @returns: ``{'class', 'reason', 'ref'}``
    """
    if hit:
        return {'class': hit[0], 'reason': f'{hit[2]}_polygon', 'ref': hit[1]}
    return _unknown('unmapped' if checked else 'not_checked')


def pavement_width(cls: str | None = None, row: dict | None = None) -> float:
    """Public pavement width in blocks for a class (``frontage.pavement``; suburban residential 2.5).

    @param cls: class id (ignored when ``row`` is given); None or unknown ids use the ``unknown`` row
    @param row: optional table row (tests, synthetic tables)
    @returns: width in blocks
    """
    row = row or defaults(cls or 'unknown')
    return float(row['frontage'].get('pavement', DEFAULT_PAVEMENT))


def is_open(spec) -> bool:
    """True for a spec that says "no boundary here": ``None``, empty, or ``type`` none."""
    return not spec or spec.get('type') in (None, 'none')


def resolve(cls: str, key: str, *, role: str | None = None, property: dict | None = None,
            street: dict | None = None, district: dict | None = None, row: dict | None = None) -> dict:
    """The one precedence function for frontage defaults (#254): which layer speaks first.

    Layers, strongest first: ``property`` (per-house override), ``street`` (convention),
    ``district`` (profile, empty until #255), ``class`` (the table row). A layer speaks when it holds
    the field: for a boundary role (``key='boundary'``, ``role`` given) the role key, otherwise ``key``
    itself (``private_surface``, ``opening``, ``pavement``). ``none`` speaks: it is an explicit open
    frontage and stops the search. Observed evidence (mapped barrier, aerial hedge) sits above all
    four and is the caller's business. The class layer speaks for a boundary only when the class
    allows inference and its row names a non-null spec, so a missing convention never authorises a
    fence and ``unknown`` never invents one; scalar fields always have a class default.

    @param cls: class id of the property
    @param key: ``'boundary'`` or a scalar frontage field
    @param role: boundary role (``front``, ``side_front``, ``side_back``, ``rear``, ``perimeter``)
    @param property: per-house override dict (``overrides.<number>``) or None
    @param street: street convention dict or None
    @param district: district profile section for the class or None
    @param row: table row to use instead of the class's own (synthetic tests)
    @returns: ``{'value': spec or None, 'layer': 'property'|'street'|'district'|'class'|None,
        'open': bool}``; ``open`` is an explicit none, ``layer`` None means nothing spoke
    """
    field = role if key == 'boundary' else key
    for name, layer in (('property', property), ('street', street), ('district', district)):
        if layer and field in layer:
            value = layer[field]
            if key == 'boundary' and is_open(value):
                return {'value': None, 'layer': name, 'open': True}
            return {'value': value, 'layer': name, 'open': False}
    row = row or defaults(cls)
    fr = row['frontage']
    if key == 'boundary':
        spec = fr['boundary'].get(role)
        if cls not in CLASSES or cls == 'unknown' or not fr['inference'] or is_open(spec):
            return {'value': None, 'layer': None, 'open': False}
        return {'value': spec, 'layer': 'class', 'open': False}
    if key in fr:
        return {'value': fr[key], 'layer': 'class', 'open': False}
    return {'value': None, 'layer': None, 'open': False}
