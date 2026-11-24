"""Shared roof-material catalogue and the flat(ish) / pitched rule.

Feature builds, houses and aerial roof analysis ask which blocks a roof
material may use. The only slope rule for that choice is rise/run: up to 1 in 2
is flat(ish) and may be any catalogue full block or slab; steeper is pitched
and needs a triad (full block, slab and stairs). Lidar flattening stays in
``feature_build`` and is not decided here.

Each family stores an RGB ``colour``. There is no ``minecraft:concrete`` id in
the 26.2 list, so the flat(ish)-only concrete rows are the solid colour
blocks (black, gray, light gray, white). Terracotta and those concretes have
neither slab nor stairs. ``smooth_stone`` has a slab only.

Each family also names a ``carpet`` dye colour: the flat-roof carpet finish
(``roofs[label].finish``, #455) lays that carpet over a flat deck, so the roof
reads as a different material with no extra height. ``carpet_block`` resolves it.

Roof types (``types`` in the catalogue) name the common real roofs a designer
meets: each maps to a recommended family plus one or two alternates, chosen
against real roof colours and the vanilla textures. ``roof_type_blocks`` returns
the blocks. Types are generic and UK-wide, never tied to a place.

Names outside the catalogue are not judged. Callers that still need a
full/slab/stairs triple keep the historical prefix (``brick`` -> ``bricks`` is
catalogue data; ``granite`` stays ``granite`` / ``granite_slab`` /
``granite_stairs``).
"""

from __future__ import annotations

import json
from pathlib import Path

from script_env import TOOL_ROOT

CATALOGUE_PATH = TOOL_ROOT / 'config' / 'roof-materials.json'
BLOCK_IDS_PATH = TOOL_ROOT / 'config' / 'minecraft-block-ids-26.2.txt'
TEXTURES = frozenset({'smooth', 'rough', 'tile-like', 'shingle-like', 'ridged'})
SUGGESTION_LIMIT = 3
# Same-texture families win unless another colour is far closer than this.
TEXTURE_MISMATCH_PENALTY = 40_000
# The sixteen vanilla dye colours; each has a ``<dye>_carpet`` block.
CARPET_DYES = frozenset({
    'white', 'light_gray', 'gray', 'black', 'brown', 'red', 'orange', 'yellow',
    'lime', 'green', 'cyan', 'light_blue', 'blue', 'purple', 'magenta', 'pink',
})

_CACHE: dict[str, dict] = {}


def load_catalogue(path: Path | None = None) -> dict:
    """Load and check the roof-material catalogue.

    @param path: optional override (tests); default ``config/roof-materials.json``
    @returns: catalogue dict with ``flatish_max_rise_over_run`` and ``families``
    @raises ValueError: when the file is not the expected shape
    """
    cfg_path = (path or CATALOGUE_PATH).resolve()
    cached = _CACHE.get(str(cfg_path))
    if cached is not None:
        return cached
    data = json.loads(cfg_path.read_text(encoding='utf-8'))
    _validate_catalogue(data, cfg_path)
    _CACHE[str(cfg_path)] = data
    return data


def roof_class(rise: float, run: float, *, catalogue: dict | None = None) -> str:
    """Classify a slope for material choice.

    Flat(ish) is ``abs(rise) / abs(run)`` at or below the catalogue limit
    (1 in 2). A zero run with zero rise is flat(ish); any other zero run is
    pitched. This is the only slope rule used when choosing roof materials.

    @param rise: vertical rise, same units as ``run``
    @param run: horizontal run
    @param catalogue: optional loaded catalogue
    @returns: ``flatish`` or ``pitched``
    """
    limit = float((catalogue or load_catalogue())['flatish_max_rise_over_run'])
    if run == 0:
        return 'flatish' if rise == 0 else 'pitched'
    if abs(rise) / abs(run) <= limit:
        return 'flatish'
    return 'pitched'


def roof_class_for_kind(kind: str) -> str:
    """Map a feature or house roof kind onto the material slope class.

    @param kind: ``flat`` or ``pitched``
    @returns: ``flatish`` or ``pitched``
    @raises ValueError: when ``kind`` is neither
    """
    if kind == 'flat':
        return 'flatish'
    if kind == 'pitched':
        return 'pitched'
    raise ValueError(f'roof kind {kind!r} is not flat or pitched')


def normalise_material(material: str) -> str:
    """Strip a namespace, block state, and a trailing slab or stair suffix.

    @param material: family id, block id, or block state
    @returns: family id used as a catalogue key
    """
    mat = material.replace('minecraft:', '').split('[', 1)[0]
    if mat.endswith('_slab'):
        mat = mat[: -len('_slab')]
    if mat.endswith('_stairs'):
        mat = mat[: -len('_stairs')]
    return mat


def family_variants(material: str, *, catalogue: dict | None = None) -> dict[str, str]:
    """Full / slab / stairs names for a roof material.

    Catalogue families return only the variants they list (``smooth_stone`` has
    no stairs; terracotta has neither slab nor stairs). Any other name keeps
    the historical prefix: the name itself, plus ``_slab`` and ``_stairs``.
    Never substitutes another material.

    @param material: family id, block id, or block state
    @param catalogue: optional loaded catalogue
    @returns: ``{'full': ...}`` plus optional ``slab`` / ``stairs``
    """
    cat = catalogue or load_catalogue()
    key = normalise_material(material)
    family = cat['families'].get(key)
    if family is None:
        return {'full': key, 'slab': f'{key}_slab', 'stairs': f'{key}_stairs'}
    out = {'full': family['full']}
    slab = family.get('slab')
    stairs = family.get('stairs')
    if isinstance(slab, str) and slab:
        out['slab'] = slab
    if isinstance(stairs, str) and stairs:
        out['stairs'] = stairs
    return out


def carpet_block(material: str, finish: str = 'carpet', *, catalogue: dict | None = None) -> str:
    """Carpet block for a flat-roof carpet finish.

    ``finish`` ``"carpet"`` takes the roof family's ``carpet`` colour; an explicit
    ``"<dye>_carpet"`` names the block directly.

    @param material: roof family id, block id, or block state
    @param finish: ``"carpet"`` or ``"<dye>_carpet"``
    @param catalogue: optional loaded catalogue
    @returns: carpet block name without ``minecraft:``
    @raises ValueError: when the finish is not a carpet, or the material has no carpet colour
    """
    if finish.endswith('_carpet'):
        if finish[: -len('_carpet')] not in CARPET_DYES:
            raise ValueError(f'{finish!r} is not a carpet block; dyes: {", ".join(sorted(CARPET_DYES))}')
        return finish
    if finish != 'carpet':
        raise ValueError(f'roof finish {finish!r} must be "carpet" or "<dye>_carpet"')
    key = normalise_material(material)
    family = (catalogue or load_catalogue())['families'].get(key)
    if family is None or 'carpet' not in family:
        raise ValueError(
            f'roof material {key!r} has no carpet colour in config/roof-materials.json; '
            'name the carpet, e.g. "gray_carpet"'
        )
    return f"{family['carpet']}_carpet"


def roof_type_names(*, catalogue: dict | None = None) -> list[str]:
    """Names of the catalogue's roof types, in catalogue order.

    @param catalogue: optional loaded catalogue
    @returns: type names
    """
    return list((catalogue or load_catalogue()).get('types', {}))


def roof_type_blocks(type_name: str, *, catalogue: dict | None = None) -> dict:
    """Recommended blocks for a named roof type.

    Pitched types return a triad (full block, slab and stairs); a flatish type
    returns its full block and slab where it has them. Alternates come back in
    the same shape, best first.

    @param type_name: a name from ``roof_type_names``
    @param catalogue: optional loaded catalogue
    @returns: ``type``, ``slope``, ``family``, ``full``, ``slab``, ``stairs``,
        ``alternates`` (list of the same keys, per family) and ``note``
    @raises ValueError: when ``type_name`` is not a roof type
    """
    cat = catalogue or load_catalogue()
    types = cat.get('types', {})
    row = types.get(type_name)
    if row is None:
        raise ValueError(
            f'roof type {type_name!r} is not known; types: {", ".join(types) or "none"}'
        )

    def entry(family_id: str) -> dict:
        got = roof_blocks(family_id, row['slope'], catalogue=cat)
        return {
            'family': family_id,
            'full': got['full'],
            'slab': got['slab'],
            'stairs': got['stairs'],
        }

    out = {'type': type_name, 'slope': row['slope'], **entry(row['family'])}
    out['alternates'] = [entry(alt) for alt in row.get('alternates', [])]
    out['note'] = row.get('note', '')
    return out


def roof_blocks(material: str, slope: str, *, catalogue: dict | None = None) -> dict:
    """Resolve catalogue blocks for a material at a slope class.

    Flat(ish) accepts a catalogue full block or slab. Pitched requires a triad.
    A pitched catalogue material without a triad does not raise: ``ok`` is
    false, messages explain it, and ``suggestions`` names the nearest triad
    families by colour and texture. A name outside the catalogue is not judged
    (``known`` false, ``ok`` true, no suggestions).

    @param material: family id or block name
    @param slope: ``flatish`` or ``pitched``
    @param catalogue: optional loaded catalogue
    @returns: ``ok``, ``known``, ``full``, ``slab``, ``stairs``, ``messages``, ``suggestions``
    @raises ValueError: when ``slope`` is not a material class
    """
    if slope not in ('flatish', 'pitched'):
        raise ValueError(f'roof class {slope!r} is not flatish or pitched')
    cat = catalogue or load_catalogue()
    key = normalise_material(material)
    family = cat['families'].get(key)
    if family is None:
        return {
            'ok': True,
            'known': False,
            'full': None,
            'slab': None,
            'stairs': None,
            'messages': [],
            'suggestions': [],
        }
    full = family['full']
    slab = family.get('slab') or None
    stairs = family.get('stairs') or None
    suggestions: list[str] = []
    messages: list[str] = []
    ok = True
    if slope == 'pitched' and not family['triad']:
        ok = False
        suggestions = _nearest_triads(key, family, cat)
        listed = ', '.join(suggestions) if suggestions else 'none'
        messages.append(
            f"roof material {key!r} is not a pitched triad "
            f"(needs full, slab and stairs); nearest triads: {listed}"
        )
    elif slope == 'flatish' and not full and not slab:
        ok = False
        messages.append(
            f"roof material {key!r} has no full block or slab for a flat roof"
        )
    return {
        'ok': ok,
        'known': True,
        'full': full,
        'slab': slab if isinstance(slab, str) and slab else None,
        'stairs': stairs if isinstance(stairs, str) and stairs else None,
        'messages': messages,
        'suggestions': suggestions,
    }


def validate_roof_material(
    material: str, slope: str, *, catalogue: dict | None = None,
) -> list[str]:
    """Warning messages for a roof material at this slope class.

    Existing specs are listed, not rewritten. An empty list means the
    catalogue accepts the material, or the name is outside the catalogue and
    is left to the historical prefix.

    @param material: family id or block name
    @param slope: ``flatish`` or ``pitched``
    @param catalogue: optional loaded catalogue
    @returns: warning strings (possibly empty)
    """
    return list(roof_blocks(material, slope, catalogue=catalogue)['messages'])


def roof_material_warnings(roof: dict, *, catalogue: dict | None = None) -> list[str]:
    """List material warnings for one ``roofs[label]`` object.

    Uses ``kind: flat`` as flat(ish) and ``kind: pitched`` as pitched. Zone and
    region material overrides are checked with the same class. The spec is not changed.

    @param roof: roof dict with ``kind``, ``material``, optional ``zone_materials``
    @param catalogue: optional loaded catalogue
    @returns: warning strings (possibly empty)
    """
    slope = roof_class_for_kind(roof.get('kind') or 'flat')
    names: list[str] = []
    primary = roof.get('material') or 'smooth_stone'
    names.append(str(primary))
    extras = list((roof.get('zone_materials') or {}).values())
    extras += [row['material'] for row in roof.get('material_regions') or [] if row.get('material')]
    for extra in extras:
        text = str(extra)
        if text not in names:
            names.append(text)
    messages: list[str] = []
    for name in names:
        messages.extend(validate_roof_material(name, slope, catalogue=catalogue))
    return messages


def block_id_names(path: Path | None = None) -> set[str]:
    """Block ids from the vendored 26.2 list, without the ``minecraft:`` prefix.

    @param path: optional override of ``config/minecraft-block-ids-26.2.txt``
    @returns: short block names
    """
    text = (path or BLOCK_IDS_PATH).read_text(encoding='utf-8')
    return {
        line.removeprefix('minecraft:')
        for line in text.splitlines()
        if line and not line.startswith('#')
    }


def _nearest_triads(family_id: str, family: dict, catalogue: dict) -> list[str]:
    """Triad family ids nearest to ``family`` by colour, then texture.

    @param family_id: catalogue key being rejected
    @param family: that family's record
    @param catalogue: loaded catalogue
    @returns: up to ``SUGGESTION_LIMIT`` family ids, best first
    """
    colour = tuple(family['colour'])
    texture = family['texture']
    scored: list[tuple[int, str]] = []
    for other_id, other in catalogue['families'].items():
        if other_id == family_id or not other['triad']:
            continue
        dist = sum((a - b) ** 2 for a, b in zip(colour, other['colour']))
        if other['texture'] != texture:
            dist += TEXTURE_MISMATCH_PENALTY
        scored.append((dist, other_id))
    scored.sort()
    return [other_id for _dist, other_id in scored[:SUGGESTION_LIMIT]]


def _validate_catalogue(data: dict, path: Path) -> None:
    """Refuse a catalogue that is not the shape builders expect.

    @param data: parsed JSON
    @param path: source path, named in errors
    @raises ValueError: on a missing or inconsistent field
    """
    if not isinstance(data, dict):
        raise ValueError(f'roof catalogue must be an object: {path}')
    limit = data.get('flatish_max_rise_over_run')
    if isinstance(limit, bool) or not isinstance(limit, (int, float)) or limit <= 0:
        raise ValueError(f'{path}: flatish_max_rise_over_run must be a positive number')
    families = data.get('families')
    if not isinstance(families, dict) or not families:
        raise ValueError(f'{path}: families must be a non-empty object')
    allowed = {'full', 'slab', 'stairs', 'colour', 'texture', 'triad', 'carpet'}
    for family_id, family in families.items():
        if not isinstance(family_id, str) or not family_id:
            raise ValueError(f'{path}: family id must be a non-empty string')
        if not isinstance(family, dict):
            raise ValueError(f'{path}: families[{family_id!r}] must be an object')
        extra = set(family) - allowed
        if extra:
            raise ValueError(f'{path}: families[{family_id!r}] has unknown keys {sorted(extra)}')
        full = family.get('full')
        if not isinstance(full, str) or not full:
            raise ValueError(f'{path}: families[{family_id!r}].full must be a block name')
        for variant in ('slab', 'stairs'):
            if variant in family and (not isinstance(family[variant], str) or not family[variant]):
                raise ValueError(
                    f'{path}: families[{family_id!r}].{variant} must be a block name'
                )
        colour = family.get('colour')
        if (
            not isinstance(colour, list) or len(colour) != 3
            or not all(isinstance(channel, int) and not isinstance(channel, bool)
                       and 0 <= channel <= 255 for channel in colour)
        ):
            raise ValueError(f'{path}: families[{family_id!r}].colour must be three 0..255 ints')
        if 'carpet' in family and family['carpet'] not in CARPET_DYES:
            raise ValueError(
                f'{path}: families[{family_id!r}].carpet must be one of {sorted(CARPET_DYES)}'
            )
        if family.get('texture') not in TEXTURES:
            raise ValueError(
                f'{path}: families[{family_id!r}].texture must be one of {sorted(TEXTURES)}'
            )
        has_triad = bool(family.get('slab')) and bool(family.get('stairs')) and bool(full)
        if family.get('triad') is not has_triad:
            raise ValueError(
                f'{path}: families[{family_id!r}].triad must be {has_triad}'
            )
    _validate_types(data.get('types', {}), families, path)


def _validate_types(types: object, families: dict, path: Path) -> None:
    """Refuse roof types that name unknown families or break the slope rule.

    @param types: parsed ``types`` section (may be empty)
    @param families: validated family table
    @param path: source path, named in errors
    @raises ValueError: on a missing or inconsistent field
    """
    if not isinstance(types, dict):
        raise ValueError(f'{path}: types must be an object')
    for type_id, row in types.items():
        where = f'{path}: types[{type_id!r}]'
        if not isinstance(row, dict):
            raise ValueError(f'{where} must be an object')
        extra = set(row) - {'slope', 'family', 'alternates', 'note'}
        if extra:
            raise ValueError(f'{where} has unknown keys {sorted(extra)}')
        slope = row.get('slope')
        if slope not in ('flatish', 'pitched'):
            raise ValueError(f'{where}.slope must be flatish or pitched')
        alternates = row.get('alternates', [])
        if not isinstance(alternates, list):
            raise ValueError(f'{where}.alternates must be a list')
        for family_id in [row.get('family'), *alternates]:
            family = families.get(family_id) if isinstance(family_id, str) else None
            if family is None:
                raise ValueError(f'{where} names unknown family {family_id!r}')
            if slope == 'pitched' and not family['triad']:
                raise ValueError(f'{where} is pitched but {family_id!r} is not a triad')
