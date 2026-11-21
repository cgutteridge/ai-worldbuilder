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
import sys
from functools import lru_cache

from script_env import TOOL_ROOT

TABLE_PATH = TOOL_ROOT / 'config' / 'land-use-classes.json'

CLASSES = ('residential', 'commercial', 'industrial', 'civic', 'park', 'rural', 'natural',
           'water', 'transport', 'parking', 'unknown')
UNKNOWN_REASONS = ('not_checked', 'unmapped', 'unlisted')
BOUNDARY_ROLES = ('front', 'side_front', 'side_back', 'rear', 'perimeter')
PRIVATE_SURFACES = ('grass', 'paving', 'keep')
OPENINGS = ('door_route', 'street_gate', 'none')
# Optional row columns beyond the required ones. Add a new per-class key here (and its checks in
# validate_table) so an unknown key is still refused; absent or null means "infer nothing".
OPTIONAL_COLUMNS = ('surface', 'street_face', 'trees', 'ground_texture')
SURFACE_KEYS = ('unmapped_paving', 'path_widen')
TREE_SOURCES = ('mapped', 'wood', 'lidar')
TREE_SPECIES = ('oak', 'birch', 'dark_oak')
GROUND_TEXTURE_PALETTES = ('pasture', 'meadow', 'wood')  # open grass (#319); wood floor (#348)
GROUND_TEXTURE_KEYS = ('tufts', 'tall_grass', 'bare', 'flowers')
WOOD_FLOOR_BLOCKS = ('podzol', 'coarse_dirt', 'moss_block')  # roll order for ground_texture.wood
WOOD_FLOOR_KEYS = WOOD_FLOOR_BLOCKS + ('edge', 'scrub')
UNMAPPED_PAVING_KINDS = ('dirt', 'grass')     # ground_layer surface kinds; null keeps aerial paving
MAX_PATH_WIDEN = 6                            # blocks; surface.path_widen is null or 1..this (#325)
MAX_GROUND_TEXTURE_FLOWER_SHARE = 0.05        # meadow flowers stay sparse (#319 prototype 1.5%)
MAX_WOOD_FLOOR_EDGE = 8                       # ground_texture.wood.edge, in columns (#348)
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
        extra = set(row) - {'id', 'match', 'frontage', 'planting', 'furniture', *OPTIONAL_COLUMNS}
        if extra:
            raise TableError(f'{cid}: unknown column {sorted(extra)}')
        surface = row.get('surface')
        if surface is not None:
            if not isinstance(surface, dict) or set(surface) - set(SURFACE_KEYS):
                raise TableError(f'{cid}: surface must be null or a map with keys from {SURFACE_KEYS}')
            paving = surface.get('unmapped_paving')
            if paving is not None and paving not in UNMAPPED_PAVING_KINDS:
                raise TableError(f'{cid}: surface.unmapped_paving {paving!r} not null or in {UNMAPPED_PAVING_KINDS}')
            widen = surface.get('path_widen')
            if widen is not None and (isinstance(widen, bool) or not isinstance(widen, int)
                                      or not 1 <= widen <= MAX_PATH_WIDEN):
                raise TableError(f'{cid}: surface.path_widen {widen!r} not null or a whole number of blocks 1..{MAX_PATH_WIDEN}')
        trees = row.get('trees')
        if trees is not None:
            if not isinstance(trees, dict) or set(trees) - {'sources', 'species_weights'}:
                raise TableError(f'{cid}: trees must be null or a map with sources and species_weights')
            sources = trees.get('sources')
            if sources is not None and (not isinstance(sources, list)
                                        or any(source not in TREE_SOURCES for source in sources)
                                        or len(sources) != len(set(sources))):
                raise TableError(f'{cid}: trees.sources must be null or a list of distinct values from {TREE_SOURCES}')
            weights = trees.get('species_weights')
            if weights is not None:
                if (not isinstance(weights, dict) or not weights or set(weights) - set(TREE_SPECIES)
                        or any(isinstance(w, bool) or not isinstance(w, (int, float))
                               or not 0 <= w <= sys.float_info.max for w in weights.values())
                        or not 0 < sum(weights.values()) <= sys.float_info.max):
                    raise TableError(f'{cid}: trees.species_weights must map {TREE_SPECIES} to nonnegative finite weights with a positive total')
        _validate_ground_texture(cid, row.get('ground_texture'))
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
        _validate_street_face(cid, row.get('street_face'))
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
            if not isinstance(planting, dict) or set(planting) != {'shrubs', 'flowers', 'cover', 'pond'}:
                raise TableError(f'{cid}: planting must be null or {{shrubs, flowers, cover, pond}}')
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
            cover = planting['cover']
            if cover is not None:
                if not isinstance(cover, dict) or set(cover) != {'share', 'kinds'}:
                    raise TableError(f'{cid}: cover must be null or {{share, kinds}}')
                share, kinds = cover['share'], cover['kinds']
                if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 < share <= 0.3:
                    raise TableError(f'{cid}: cover share must be above 0 and at most 0.3')
                if not isinstance(kinds, list) or not 1 <= len(kinds) <= 3 or any(
                        not isinstance(k, str) or k not in REFINE_PLANTS for k in kinds) or len(set(kinds)) != len(kinds):
                    raise TableError(f'{cid}: cover kinds must be 1-3 different names from foliage.REFINE_PLANTS')
            if planting['pond'] is not None:
                raise TableError(f'{cid}: planting pond must stay null')
        furniture = row['furniture']
        if furniture is not None:
            if not fr['inference']:
                raise TableError(f'{cid}: this class allows no furniture inference')
            if not isinstance(furniture, dict) or set(furniture) != {'bench_per_100m'}:
                raise TableError(f'{cid}: furniture must be null or {{bench_per_100m}}')
            bench = furniture['bench_per_100m']
            if isinstance(bench, bool) or not isinstance(bench, (int, float)) or not 0 < bench <= 100:
                raise TableError(f'{cid}: bench_per_100m must be above 0 and at most 100')
        if cid == 'unknown' and (any(v is not None for v in fr['boundary'].values())
                                 or row['planting'] is not None or row['furniture'] is not None):
            raise TableError('unknown must not set a boundary, planting or furniture value')
    highways = doc.get('highways', {})
    if not isinstance(highways, dict):
        raise TableError('highways must be a lamp-spacing map')
    for highway, spacing in highways.items():
        if not isinstance(highway, str) or (spacing is not None and
                (isinstance(spacing, bool) or not isinstance(spacing, (int, float)) or not 1 <= spacing <= 1000)):
            raise TableError('highways: lamp spacing must be null or 1 to 1000 blocks')
    return {'classes': out, 'highways': highways}


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


def unmapped_paving(cls: str) -> str | None:
    """Surface kind that aerial "paving" takes in this class where no mapped way lies (``surface.unmapped_paving``).

    ``dirt`` (park) or ``grass`` (rural, natural) replace shade, undergrowth and worn grass that the
    aerial reads as concrete; None (every other class, and residential) keeps the aerial paving.

    @param cls: class id; unknown ids use the ``unknown`` row
    @returns: ``'dirt'``, ``'grass'`` or None
    """
    return (defaults(cls).get('surface') or {}).get('unmapped_paving')


def path_widen(cls: str) -> int | None:
    """Blocks beside a mapped path where aerial paving takes the path's own surface (``surface.path_widen``, #325).

    A mapped path is only 1.6-2.5 blocks wide; where the aerial shows paving beside it, the path is
    wider than the map says. Park sets 3; None (every other class, and residential) widens nothing.

    @param cls: class id; unknown ids use the ``unknown`` row
    @returns: reach in whole blocks, or None
    """
    return (defaults(cls).get('surface') or {}).get('path_widen')


def tree_sources(cls: str) -> tuple[str, ...] | None:
    """Allowed tree evidence in this class (``trees.sources``, #317), or None for existing sources.

    Mapped includes tree points and rows; wood includes wood, scrub and forest polygons;
    lidar requires standing height of at least 2 metres. Missing/null applies no source gate.

    @param cls: class id; unknown ids use the ``unknown`` row
    @returns: allowed source names, possibly empty, or None
    """
    sources = (defaults(cls).get('trees') or {}).get('sources')
    return tuple(sources) if sources is not None else None


def tree_species_weights(cls: str) -> dict:
    """Configured per-crown species weights; absent/null keeps the original oak-only output.

    The shipped woodland mix is a placeholder for #318, not evidence of local species.

    @param cls: land-use class; unknown ids use the unknown row
    @returns: species-to-weight map (a copy, safe for callers to change)
    """
    weights = (defaults(cls).get('trees') or {}).get('species_weights')
    return dict(weights) if weights is not None else {'oak': 1}


def ground_texture(cls: str) -> dict | None:
    """Ground-texture palettes for a class (``ground_texture``), or None when off.

    Absent or null means no rough grass, mottling or woodland floor. ``pasture`` / ``meadow``
    (#319) enable placement on gated OSM tags (farmland, meadow, grassland, heath, scrub,
    nature_reserve); lawns never. ``wood`` (#348) is the woodland floor mix for ``natural=wood``
    and ``landuse=forest`` (scrub only when ``wood.scrub`` is true). Does not use ``planting``
    (null when ``frontage.inference`` is false). Flower kinds reuse ``foliage.REFINE_PLANTS``.

    @param cls: class id; unknown ids use the ``unknown`` row
    @returns: palette map, or None
    """
    value = defaults(cls).get('ground_texture')
    return value if value else None


def _share_range(cid: str, field: str, value) -> None:
    """Refuse a texture share range that is not ``[lo, hi]`` with ``0 <= lo <= hi <= 1``.

    @param cid: class id
    @param field: field name for the error
    @param value: candidate range
    @returns: None
    """
    if (not isinstance(value, list) or len(value) != 2
            or any(isinstance(n, bool) or not isinstance(n, (int, float)) for n in value)
            or not 0 <= value[0] <= value[1] <= 1):
        raise TableError(f'{cid}: ground_texture {field} must be [lo, hi] with 0 <= lo <= hi <= 1')


def _unit_share(cid: str, field: str, value) -> float:
    """Refuse a woodland-floor share that is not a number from 0 to 1.

    @param cid: class id
    @param field: field name for the error
    @param value: candidate share
    @returns: the share as a float
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise TableError(f'{cid}: ground_texture {field} must be a share from 0 to 1')
    return float(value)


def _validate_wood_floor(cid: str, palette) -> None:
    """Refuse a bad ``ground_texture.wood`` palette (#348).

    Keys are the floor blocks plus ``edge`` (grass rim, in columns) and ``scrub`` (whether
    ``natural=scrub`` takes this floor instead of the pasture palette). Block shares add up
    to at most 1; the rest of the interior keeps grass.

    @param cid: class id
    @param palette: the ``wood`` value
    @returns: None
    """
    if not isinstance(palette, dict) or set(palette) != set(WOOD_FLOOR_KEYS):
        raise TableError(
            f'{cid}: ground_texture.wood must have keys {sorted(WOOD_FLOOR_KEYS)}')
    total = 0.0
    for block in WOOD_FLOOR_BLOCKS:
        total += _unit_share(cid, f'wood.{block}', palette[block])
    if total > 1 + 1e-9:
        raise TableError(f'{cid}: ground_texture.wood shares must add up to at most 1')
    edge = palette['edge']
    if isinstance(edge, bool) or not isinstance(edge, int) or not 0 <= edge <= MAX_WOOD_FLOOR_EDGE:
        raise TableError(
            f'{cid}: ground_texture.wood.edge must be a whole number of columns '
            f'from 0 to {MAX_WOOD_FLOOR_EDGE}')
    if type(palette['scrub']) is not bool:
        raise TableError(f'{cid}: ground_texture.wood.scrub must be true or false')


def _validate_open_grass(cid: str, name: str, palette) -> None:
    """Refuse a bad ``pasture`` or ``meadow`` palette (#319).

    @param cid: class id
    @param name: ``pasture`` or ``meadow``
    @param palette: the palette value
    @returns: None
    """
    from foliage import REFINE_PLANTS

    if not isinstance(palette, dict) or set(palette) != set(GROUND_TEXTURE_KEYS):
        raise TableError(f'{cid}: ground_texture.{name} must have keys {sorted(GROUND_TEXTURE_KEYS)}')
    _share_range(cid, f'{name}.tufts', palette['tufts'])
    _share_range(cid, f'{name}.tall_grass', palette['tall_grass'])
    if type(palette['bare']) is not bool:
        raise TableError(f'{cid}: ground_texture.{name}.bare must be true or false')
    flowers = palette['flowers']
    if flowers is None:
        return
    if not isinstance(flowers, dict) or set(flowers) != {'share', 'kinds'}:
        raise TableError(f'{cid}: ground_texture.{name}.flowers must be null or {{share, kinds}}')
    share, kinds = flowers['share'], flowers['kinds']
    if (isinstance(share, bool) or not isinstance(share, (int, float))
            or not 0 < share <= MAX_GROUND_TEXTURE_FLOWER_SHARE):
        raise TableError(
            f'{cid}: ground_texture.{name}.flowers share must be above 0 and '
            f'at most {MAX_GROUND_TEXTURE_FLOWER_SHARE}')
    if (not isinstance(kinds, list) or not 1 <= len(kinds) <= 3
            or any(not isinstance(k, str) or k not in REFINE_PLANTS for k in kinds)
            or len(set(kinds)) != len(kinds)):
        raise TableError(
            f'{cid}: ground_texture.{name}.flowers kinds must be 1-3 different names '
            f'from foliage.REFINE_PLANTS')


def _validate_ground_texture(cid: str, texture) -> None:
    """Refuse a bad ``ground_texture`` column (absent/null means infer nothing).

    @param cid: class id
    @param texture: the column value
    @returns: None
    """
    if texture is None:
        return
    if not isinstance(texture, dict) or set(texture) - set(GROUND_TEXTURE_PALETTES):
        raise TableError(f'{cid}: ground_texture must be null or a map with keys from {GROUND_TEXTURE_PALETTES}')
    for name, palette in texture.items():
        if name == 'wood':
            _validate_wood_floor(cid, palette)
        else:
            _validate_open_grass(cid, name, palette)


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


def _validate_street_face(cid: str, face) -> None:
    """Refuse a street-face recipe on the wrong class, or a shop recipe that is not keyed by kind.

    Industrial carries a shutter recipe. Commercial carries a big-box scale test plus a shop
    recipe whose awning flag is per shop or amenity kind (UK-wide tokens, not a place name).

    @param cid: class id
    @param face: the ``street_face`` object, or None when the class has none
    @returns: None
    """
    if face is None:
        return
    expected = {'industrial': 'industrial', 'commercial': 'big_box'}.get(cid)
    if not isinstance(face, dict) or face.get('kind') != expected or expected is None:
        raise TableError(f'{cid}: street_face must be an industrial or big_box recipe on its class')
    fields = ('shutter_width', 'shutter_height', 'bay_depth') if cid == 'industrial' else (
        'min_columns', 'min_height', 'glazed_storeys')
    for field in fields:
        if type(face.get(field)) is not int or face[field] < 1:
            raise TableError(f'{cid}: street_face {field} must be an integer >= 1')
    shop = face.get('shop')
    if cid != 'commercial':
        if shop is not None:
            raise TableError(f'{cid}: street_face.shop belongs on the commercial class')
        return
    allowed = {'glass_height', 'door', 'sign_wrap', 'kinds'}
    if not isinstance(shop, dict) or set(shop) != allowed:
        raise TableError(f'{cid}: street_face.shop must have keys {sorted(allowed)}')
    if type(shop['glass_height']) is not int or shop['glass_height'] < 1:
        raise TableError(f'{cid}: street_face.shop glass_height must be an integer >= 1')
    if type(shop['sign_wrap']) is not int or shop['sign_wrap'] < 1:
        raise TableError(f'{cid}: street_face.shop sign_wrap must be an integer >= 1')
    if not isinstance(shop['door'], str) or not shop['door'].endswith('_door'):
        raise TableError(f'{cid}: street_face.shop door must be a door block')
    kinds = shop['kinds']
    if not isinstance(kinds, dict) or not kinds:
        raise TableError(f'{cid}: street_face.shop kinds must list shop and amenity kinds')
    for kind, spec in kinds.items():
        if not isinstance(kind, str) or not kind or kind != kind.strip() or any(ch.isspace() for ch in kind):
            raise TableError(f'{cid}: shop kind {kind!r} must be a shop or amenity value')
        if (not isinstance(spec, dict) or set(spec) != {'awning'}
                or type(spec.get('awning')) is not bool):
            raise TableError(f'{cid}: shop kind {kind!r} must be {{"awning": true or false}}')


def _tag_token(tags: dict, key: str) -> str | None:
    """One non-blank tag value.

    @param tags: flattened building tags
    @param key: tag key
    @returns: the value, or None
    """
    value = tags.get(key)
    if isinstance(value, str) and value.strip() and value == value.strip():
        return value
    return None


def _big_box_applies(recipe: dict, tags: dict, *, columns: int, height: float) -> bool:
    """True when a commercial shell is large enough to take the big-box recipe.

    @param recipe: commercial ``street_face`` object
    @param tags: flattened building tags
    @param columns: footprint area in block columns
    @param height: shell height in blocks
    @returns: whether the big-box scale test passes
    """
    if tags.get('building') not in ('retail', 'supermarket', 'yes', None):
        return False
    if tags.get('office') or tags.get('tourism'):
        return False
    if columns < recipe['min_columns'] and height < recipe['min_height']:
        return False
    return True


def _shop_recipe(table: dict | None, tags: dict) -> dict | None:
    """Shop street-face recipe when the tags are a high-street shop.

    Any ``shop`` value qualifies. An ``amenity`` qualifies only when that kind is listed
    on the recipe (cafe, restaurant, fast food, food). ``building=retail``, ``kiosk`` or
    ``supermarket`` qualifies with no shop tag. The awning flag comes from the kind.

    @param table: ``street_face.shop`` object, or None
    @param tags: flattened building tags
    @returns: shop recipe, or None when this is not a shop
    """
    if not isinstance(table, dict):
        return None
    if tags.get('office') or tags.get('tourism'):
        return None
    kinds = table.get('kinds') or {}
    kind = _tag_token(tags, 'shop')
    if kind is None:
        amenity = _tag_token(tags, 'amenity')
        if amenity in kinds:
            kind = amenity
    if kind is None and tags.get('building') not in ('retail', 'kiosk', 'supermarket'):
        return None
    spec = kinds.get(kind) if isinstance(kind, str) else None
    awning = bool(spec.get('awning')) if isinstance(spec, dict) else False
    return {
        'kind': 'shop',
        'glass_height': table['glass_height'],
        'door': table['door'],
        'sign_wrap': table['sign_wrap'],
        'awning': awning,
        'shop_kind': kind,
    }


def street_face_defaults(cls: str, tags: dict, *, columns: int, height: float) -> dict | None:
    """Industrial, big-box or shop street-face recipe for one classified building.

    A named shop kind other than ``supermarket`` stays a shop at any size. A large
    retail shell or supermarket with no other shop kind is a big box. Offices and
    anything the class table does not dress return None.

    @param cls: classified land use (including an inherited use polygon)
    @param tags: flattened building tags
    @param columns: footprint area in block columns
    @param height: shell height in blocks (lidar height when zoned)
    @returns: class recipe, or None when no accepted default applies
    """
    recipe = defaults(cls).get('street_face')
    if not recipe:
        return None
    if recipe.get('kind') != 'big_box':
        return recipe
    shop = _shop_recipe(recipe.get('shop'), tags)
    named = shop.get('shop_kind') if shop else None
    if shop and named not in (None, 'supermarket'):
        return shop
    if _big_box_applies(recipe, tags, columns=columns, height=height):
        return {key: value for key, value in recipe.items() if key != 'shop'}
    return shop


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
    ``district`` (profile), ``class`` (the table row). A layer speaks when it holds
    the field: for a boundary role (``key='boundary'``, ``role`` given) the role key, otherwise ``key``
    itself (``private_surface``, ``opening``, ``pavement``). ``none`` speaks: it is an explicit open
    frontage and stops the search. Observed evidence (mapped barrier, aerial hedge) sits above all
    four and is the caller's business. The class layer speaks for a boundary only when the class
    allows inference and its row names a non-null spec, so a missing convention never authorises a
    fence and ``unknown`` never invents one; scalar fields always have a class default.

    @param cls: class id of the property
    @param key: ``'boundary'``, ``'lamp_spacing'``, ``'bench_per_100m'`` or a scalar frontage field
    @param role: boundary role, or OSM highway class for ``lamp_spacing``
    @param property: per-house override dict (``overrides.<number>``) or None
    @param street: street convention dict or None
    @param district: district profile section for the class or None
    @param row: table row to use instead of the class's own (synthetic tests)
    @returns: ``{'value': spec or None, 'layer': 'property'|'street'|'district'|'class'|None,
        'open': bool}``; ``open`` is an explicit none, ``layer`` None means nothing spoke
    """
    row = row or defaults(cls)
    fr = row['frontage']
    if cls not in CLASSES or not fr['inference'] or cls in NO_INFERENCE:
        district = None
    layers = [('property', property), ('street', street), ('district', district)]
    if key == 'lamp_spacing':
        for name, layer in layers:
            lamps = (layer or {}).get('lamp_spacing', {})
            if role in lamps:
                return {'value': lamps[role], 'layer': name, 'open': False}
        value = (load_table().get('highways', {}).get(role)
                 if cls in CLASSES and cls not in NO_INFERENCE and fr['inference'] else None)
        return {'value': value, 'layer': 'class' if value is not None else None, 'open': False}
    if key == 'bench_per_100m':
        for name, layer in layers:
            if layer and 'bench_per_100m' in layer:
                return {'value': layer['bench_per_100m'], 'layer': name, 'open': False}
        value = (row['furniture'] or {}).get('bench_per_100m') if fr['inference'] else None
        return {'value': value, 'layer': 'class' if value is not None else None, 'open': False}
    field = role if key == 'boundary' else key
    for n, (name, layer) in enumerate(layers):
        if layer and field in layer:
            value = layer[field]
            if key == 'boundary' and is_open(value):
                return {'value': None, 'layer': name, 'open': True}
            if key == 'boundary':
                # Presence and material have the same ladder. Fill only omitted material fields,
                # from the same boundary kind; an exact per-house block is never recoloured.
                value = dict(value)
                material_layers = {}
                candidates = [(layer_name, v) for layer_name, v in layers[n:] if v]
                if cls in CLASSES and fr['inference'] and cls not in NO_INFERENCE:
                    candidates.append(('class', fr['boundary']))
                for material_layer, candidate in candidates:
                    same = candidate.get(field) or {}
                    dominant = candidate.get('materials', {}).get(value['type'], {})
                    for k in ('block', 'height'):
                        fallback = same.get(k) if same.get('type') == value['type'] else None
                        fallback = fallback if fallback is not None else dominant.get(k)
                        if k not in value and fallback is not None:
                            value[k] = fallback
                            if material_layer != name:
                                material_layers[k] = material_layer
                if material_layers:
                    return {'value': value, 'layer': name, 'open': False, 'material_layers': material_layers}
            return {'value': value, 'layer': name, 'open': False}
    if key == 'boundary':
        spec = fr['boundary'].get(role)
        if cls not in CLASSES or cls == 'unknown' or not fr['inference'] or is_open(spec):
            return {'value': None, 'layer': None, 'open': False}
        return {'value': spec, 'layer': 'class', 'open': False}
    if key in fr:
        return {'value': fr[key], 'layer': 'class', 'open': False}
    return {'value': None, 'layer': None, 'open': False}
