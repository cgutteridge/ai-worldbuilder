"""Hollow industrial stack geometry; dimensions come from the designer's photos."""
from __future__ import annotations

import math


def validate_chimney(row: dict, context: str) -> None:
    """Validate a stack and fill its common detail-report dimensions."""
    for key in ('base_diameter', 'top_diameter', 'height'):
        value = row.get(key)
        minimum = 1 if key == 'height' else 3
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise SystemExit(f'{context}: chimney {key} must be an integer >= {minimum}')
    if row.get('shape', 'round') not in ('round', 'square'):
        raise SystemExit(f'{context}: chimney shape must be round or square')
    if not isinstance(row.get('material', 'white_concrete'), str):
        raise SystemExit(f'{context}: chimney material must be a block name')
    if 'rim' in row and (not isinstance(row['rim'], str) or not row['rim']):
        raise SystemExit(f'{context}: chimney rim must be a block name')
    band = row.get('band')
    if band is not None:
        if (not isinstance(band, dict) or isinstance(band.get('offset'), bool)
                or not isinstance(band.get('offset'), int)
                or not 0 <= band['offset'] < row['height']
                or not isinstance(band.get('block'), str) or not band['block']):
            raise SystemExit(f'{context}: chimney band needs offset within height and block')
    diameter = max(row['base_diameter'], row['top_diameter'])
    row['size'] = [diameter, row['height'], diameter]


def chimney_layers(row: dict) -> list[tuple[int, set, set]]:
    """Return (Y, outer ring, interior) layers around at's X/Z centre.

    A one-block Manhattan erosion leaves connected stepped rings. Explicit
    interior air keeps the stack hollow even when replacing an older detail.
    """
    cx, by, cz = row['at']
    layers = []
    for level in range(row['height']):
        ratio = level / max(1, row['height'] - 1)
        diameter = round(row['base_diameter'] + ratio * (row['top_diameter'] - row['base_diameter']))
        radius = diameter / 2
        extent = math.ceil(radius)
        shift = 0.5 if diameter % 2 == 0 else 0.0
        disk = {(cx + dx, cz + dz) for dx in range(-extent, extent + 1)
                for dz in range(-extent, extent + 1)
                if (max(abs(dx - shift), abs(dz - shift)) if row.get('shape', 'round') == 'square'
                    else math.hypot(dx - shift, dz - shift)) < radius}
        interior = {(x, z) for x, z in disk
                    if all((x + dx, z + dz) in disk for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)))}
        layers.append((by + level, disk - interior, interior))
    return layers
