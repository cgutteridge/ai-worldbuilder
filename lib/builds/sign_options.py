"""Sign wood, fascia colour and board-contrast text colour (#437).

Street-face dressing and facade detail signs read optional per-label
``signs`` keys (and optional detail-row overrides). Wood defaults to oak;
fascia colour defaults to the hashed name palette; text colour is chosen
against the sign board's Minecraft map colour, not the fascia band. A
low-contrast wood/text pair is refused at validate time (same catalogue
gate style as the roof-material module for I-057).
"""

from __future__ import annotations

import json
from pathlib import Path

from script_env import TOOL_ROOT

CATALOGUE_PATH = TOOL_ROOT / 'config' / 'sign-options.json'
BLOCK_IDS_PATH = TOOL_ROOT / 'config' / 'minecraft-block-ids-26.2.txt'
AUTO_TEXT_COLOURS = ('black', 'white')

_CACHE: dict[str, dict] = {}


def load_catalogue(path: Path | None = None) -> dict:
    """Load and check the sign-options catalogue.

    @param path: optional override (tests); default ``config/sign-options.json``
    @returns: catalogue dict with woods, text colours, fascia colours
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


def clear_catalogue_cache() -> None:
    """Drop the loaded catalogue cache (tests that swap the file)."""
    _CACHE.clear()


def default_wood(*, catalogue: dict | None = None) -> str:
    """Default sign wood family when a spec omits ``wood``.

    @param catalogue: optional loaded catalogue
    @returns: wood family id (``oak``)
    """
    return str((catalogue or load_catalogue())['default_wood'])


def sign_blocks(wood: str, *, catalogue: dict | None = None) -> dict[str, str]:
    """Wall-sign and hanging-sign block names for a wood family.

    @param wood: catalogue wood id (``oak``, ``dark_oak``, ...)
    @param catalogue: optional loaded catalogue
    @returns: ``{'wall_sign', 'hanging_sign', 'wall_hanging_sign'}``
    @raises ValueError: when ``wood`` is not in the catalogue
    """
    cat = catalogue or load_catalogue()
    if wood not in cat['woods']:
        raise ValueError(
            f'sign wood {wood!r} is not in the catalogue '
            f'({", ".join(sorted(cat["woods"]))})'
        )
    return {
        'wall_sign': f'{wood}_wall_sign',
        'hanging_sign': f'{wood}_hanging_sign',
        'wall_hanging_sign': f'{wood}_wall_hanging_sign',
    }


def relative_luminance(rgb: tuple[int, int, int] | list[int]) -> float:
    """WCAG relative luminance of an 0..255 RGB triple.

    @param rgb: map colour (R, G, B)
    @returns: luminance in 0..1
    """
    def channel(value: float) -> float:
        c = value / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(
    foreground: tuple[int, int, int] | list[int],
    background: tuple[int, int, int] | list[int],
) -> float:
    """WCAG contrast ratio between two map colours.

    @param foreground: text dye map colour
    @param background: sign-board map colour
    @returns: contrast ratio (>= 1)
    """
    light = relative_luminance(foreground)
    dark = relative_luminance(background)
    brighter, dimmer = (light, dark) if light >= dark else (dark, light)
    return (brighter + 0.05) / (dimmer + 0.05)


def wood_map_colour(wood: str, *, catalogue: dict | None = None) -> tuple[int, int, int]:
    """Minecraft map colour RGB for a sign wood board.

    @param wood: catalogue wood id
    @param catalogue: optional loaded catalogue
    @returns: (R, G, B)
    @raises ValueError: when ``wood`` is unknown
    """
    cat = catalogue or load_catalogue()
    if wood not in cat['woods']:
        raise ValueError(f'sign wood {wood!r} is not in the catalogue')
    return tuple(cat['woods'][wood]['map_colour'])  # type: ignore[return-value]


def text_map_colour(colour: str, *, catalogue: dict | None = None) -> tuple[int, int, int]:
    """Minecraft map colour RGB for a sign text dye.

    @param colour: dye name (``black``, ``gray``, ...)
    @param catalogue: optional loaded catalogue
    @returns: (R, G, B)
    @raises ValueError: when ``colour`` is unknown
    """
    cat = catalogue or load_catalogue()
    if colour not in cat['text_colours']:
        raise ValueError(
            f'sign text colour {colour!r} is not in the catalogue '
            f'({", ".join(sorted(cat["text_colours"]))})'
        )
    return tuple(cat['text_colours'][colour]['map_colour'])  # type: ignore[return-value]


def board_text_contrast(
    wood: str, text_colour: str, *, catalogue: dict | None = None,
) -> float:
    """Contrast ratio of a text dye against a wood sign board.

    @param wood: catalogue wood id
    @param text_colour: dye name
    @param catalogue: optional loaded catalogue
    @returns: contrast ratio
    """
    cat = catalogue or load_catalogue()
    return contrast_ratio(
        text_map_colour(text_colour, catalogue=cat),
        wood_map_colour(wood, catalogue=cat),
    )


def contrast_ok(
    wood: str, text_colour: str, *, catalogue: dict | None = None,
) -> bool:
    """True when wood/text meet the catalogue minimum contrast.

    @param wood: catalogue wood id
    @param text_colour: dye name
    @param catalogue: optional loaded catalogue
    @returns: whether the pair is accepted
    """
    cat = catalogue or load_catalogue()
    return board_text_contrast(wood, text_colour, catalogue=cat) >= float(cat['min_contrast'])


def resolve_text_colour(
    wood: str,
    text_colour: str | None = None,
    *,
    catalogue: dict | None = None,
) -> str:
    """Text dye that reads on the sign board.

    An explicit colour must clear the catalogue contrast floor against the
    board (not the fascia). When omitted, pick the better of black and white.
    A low-contrast explicit pair raises; callers that want a soft recovery
    can catch and call again with ``text_colour=None``.

    @param wood: catalogue wood id
    @param text_colour: optional dye name from the spec
    @param catalogue: optional loaded catalogue
    @returns: dye name to write on the sign
    @raises ValueError: unknown wood/colour, or low-contrast explicit pair
    """
    cat = catalogue or load_catalogue()
    if wood not in cat['woods']:
        raise ValueError(f'sign wood {wood!r} is not in the catalogue')
    if text_colour is None:
        return max(
            AUTO_TEXT_COLOURS,
            key=lambda dye: board_text_contrast(wood, dye, catalogue=cat),
        )
    if text_colour not in cat['text_colours']:
        raise ValueError(
            f'sign text colour {text_colour!r} is not in the catalogue '
            f'({", ".join(sorted(cat["text_colours"]))})'
        )
    ratio = board_text_contrast(wood, text_colour, catalogue=cat)
    if ratio < float(cat['min_contrast']):
        better = resolve_text_colour(wood, None, catalogue=cat)
        raise ValueError(
            f'sign text colour {text_colour!r} on {wood} wood is low contrast '
            f'({ratio:.2f} < {float(cat["min_contrast"]):.1f}); use {better!r} or another '
            f'board that clears the floor'
        )
    return text_colour


def resolve_fascia_colour(
    key: str,
    override: str | None = None,
    *,
    catalogue: dict | None = None,
    hash_colour=None,
) -> str:
    """Concrete fascia colour: optional override, else the hashed default.

    @param key: mapped name or feature label for the hash fallback
    @param override: optional catalogue fascia colour from the spec
    @param catalogue: optional loaded catalogue
    @param hash_colour: callable ``key -> colour`` (defaults to facade hash)
    @returns: concrete colour name (without ``_concrete``)
    @raises ValueError: when ``override`` is not a catalogue fascia colour
    """
    cat = catalogue or load_catalogue()
    if override is not None:
        if override not in cat['fascia_colours']:
            raise ValueError(
                f'fascia colour {override!r} is not in the catalogue '
                f'({", ".join(sorted(cat["fascia_colours"]))})'
            )
        return override
    if hash_colour is None:
        from feature_facade import fascia_colour as hash_colour
    return hash_colour(key)


def label_sign_options(spec: dict | None, label: str) -> dict:
    """Per-label ``signs`` row, or an empty dict.

    @param spec: feature build spec
    @param label: way label
    @returns: ``{wood?, fascia_colour?, text_colour?}`` (may be empty)
    """
    row = ((spec or {}).get('signs') or {}).get(label) or {}
    return dict(row) if isinstance(row, dict) else {}


def resolve_label_sign(
    spec: dict | None,
    label: str,
    *,
    hash_key: str,
    catalogue: dict | None = None,
    hash_colour=None,
) -> dict:
    """Resolved wood, fascia colour, text colour and wall-sign block for a label.

    @param spec: feature build spec
    @param label: way label
    @param hash_key: name/brand/label for hashed fascia when unset
    @param catalogue: optional loaded catalogue
    @param hash_colour: optional fascia hash callable
    @returns: ``{wood, fascia_colour, text_colour, wall_sign, hanging_sign, wall_hanging_sign}``
    """
    cat = catalogue or load_catalogue()
    opts = label_sign_options(spec, label)
    wood = opts.get('wood') or default_wood(catalogue=cat)
    blocks = sign_blocks(wood, catalogue=cat)
    fascia = resolve_fascia_colour(
        hash_key, opts.get('fascia_colour'), catalogue=cat, hash_colour=hash_colour,
    )
    text = resolve_text_colour(wood, opts.get('text_colour'), catalogue=cat)
    return {
        'wood': wood,
        'fascia_colour': fascia,
        'text_colour': text,
        **blocks,
    }


def validate_sign_row(
    row: dict,
    *,
    context: str,
    catalogue: dict | None = None,
    strict_keys: bool = False,
) -> None:
    """Refuse unknown woods/colours or a low-contrast wood/text pair.

    @param row: ``signs[label]`` object or a detail row carrying the same keys
    @param context: path prefix for error text
    @param catalogue: optional loaded catalogue
    @param strict_keys: when True, refuse keys other than wood/fascia/text
    @raises ValueError: on an invalid or low-contrast row
    """
    cat = catalogue or load_catalogue()
    if not isinstance(row, dict):
        raise ValueError(f'{context} must be an object')
    wood = row.get('wood', default_wood(catalogue=cat))
    if 'wood' in row:
        if not isinstance(row['wood'], str) or not row['wood']:
            raise ValueError(f'{context}.wood must be a non-empty wood family name')
        sign_blocks(row['wood'], catalogue=cat)
        wood = row['wood']
    if 'fascia_colour' in row:
        colour = row['fascia_colour']
        if not isinstance(colour, str) or colour not in cat['fascia_colours']:
            raise ValueError(
                f'{context}.fascia_colour must be one of '
                f'{sorted(cat["fascia_colours"])}'
            )
    text = row.get('text_colour')
    if text is not None:
        if not isinstance(text, str) or not text:
            raise ValueError(f'{context}.text_colour must be a non-empty dye name')
        resolve_text_colour(wood, text, catalogue=cat)
    if strict_keys:
        unknown = set(row) - {'wood', 'fascia_colour', 'text_colour'}
        if unknown:
            raise ValueError(f'{context} unknown keys {sorted(unknown)}')


def _validate_catalogue(data: dict, path: Path) -> None:
    """Check catalogue shape and that every wood has both sign families.

    @param data: parsed JSON
    @param path: source path (for errors)
    @raises ValueError: on a bad catalogue
    """
    if not isinstance(data, dict):
        raise ValueError(f'{path}: catalogue must be an object')
    for key in ('min_contrast', 'default_wood', 'woods', 'text_colours', 'fascia_colours'):
        if key not in data:
            raise ValueError(f'{path}: missing {key!r}')
    if not isinstance(data['min_contrast'], (int, float)) or float(data['min_contrast']) < 1:
        raise ValueError(f'{path}: min_contrast must be a number >= 1')
    woods = data['woods']
    texts = data['text_colours']
    fascias = data['fascia_colours']
    if not isinstance(woods, dict) or not woods:
        raise ValueError(f'{path}: woods must be a non-empty object')
    if data['default_wood'] not in woods:
        raise ValueError(f'{path}: default_wood must name a woods entry')
    ids = {
        line.removeprefix('minecraft:')
        for line in BLOCK_IDS_PATH.read_text(encoding='utf-8').splitlines()
        if line.startswith('minecraft:')
    }
    for section_name, section in (
        ('woods', woods), ('text_colours', texts), ('fascia_colours', fascias),
    ):
        if not isinstance(section, dict) or not section:
            raise ValueError(f'{path}: {section_name} must be a non-empty object')
        for name, row in section.items():
            if not isinstance(row, dict) or 'map_colour' not in row:
                raise ValueError(f'{path}: {section_name}.{name} needs map_colour')
            rgb = row['map_colour']
            if (
                not isinstance(rgb, list) or len(rgb) != 3
                or not all(isinstance(v, int) and 0 <= v <= 255 for v in rgb)
            ):
                raise ValueError(f'{path}: {section_name}.{name}.map_colour must be [r,g,b] 0..255')
    for wood in woods:
        for suffix in ('_wall_sign', '_hanging_sign', '_wall_hanging_sign'):
            block = f'{wood}{suffix}'
            if block not in ids:
                raise ValueError(f'{path}: wood {wood!r} missing block {block}')
    for colour in fascias:
        if f'{colour}_concrete' not in ids:
            raise ValueError(f'{path}: fascia colour {colour!r} has no {colour}_concrete block')
