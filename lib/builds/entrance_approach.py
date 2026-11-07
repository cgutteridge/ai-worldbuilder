"""Read-only description of a house entrance approach (#240).

Pure data, no world access, so ground, decor and the frontage description can read it without
importing the feature builder. ``house_build`` re-exports ``entrance_approach``.
"""
from __future__ import annotations

import re

DIRS = {'N': (0, -1), 'S': (0, 1), 'W': (-1, 0), 'E': (1, 0)}
FACING_FACE = {'north': 'N', 'south': 'S', 'west': 'W', 'east': 'E'}   # door facing = outward
SCAN_DOWN = 5          # steps/landings are looked for this far below the door's floor
MAX_APPROACH = 8       # outward columns scanned for feature steps


def entrance_approach(door, front, *, approach_cells=(), base_y=None, generated=True, headroom=2,
                      evidence_ids=()):
    """Read-only description of a house entrance approach for ground and decor (#240).

    The feature owns the door, threshold and any external landing or steps (``approach_cells``, the
    columns outside the footprint it generated). Ground joins its onward route to ``connection`` (the
    outer end of the approach, or the door's outward neighbour when there is none); decor keeps the
    ``clearance`` open. Built from current geometry on each call, so a rebuild cannot leave a stale
    endpoint. ``generated`` marks a door the builder placed rather than one observed in evidence; a
    generated door is never reported as a verified connection.

    @param door: ``(x, z)`` door column, or None when a legacy build records no door
    @param front: ``'N'``/``'S'``/``'E'``/``'W'``, the side the door faces (outward)
    @param approach_cells: ``(x, z)`` columns of feature-generated landing/steps outside the footprint
    @param base_y: Y of the surface at the connection, when known
    @param generated: True when the door position came from the builder, False when observed
    @param headroom: blocks of clear space owned by nobody that ground/decor must keep open
    @param evidence_ids: registry evidence ids backing an observed door
    @returns: ``{door, approach, connection, clearance, provenance}``; ``connection`` is None with a
        ``reason`` when the endpoint is unavailable
    """
    import provenance as prov

    if door is None or front not in DIRS:
        return {'door': None, 'approach': [], 'connection': None, 'clearance': None,
                'reason': 'no recorded door or facing (legacy build); endpoint unavailable',
                'provenance': prov.provenance_record(
                    source='inference', confidence='low', evidence_ids=evidence_ids,
                    unresolved_reason='entrance endpoint unavailable')}
    dx, dz = DIRS[front]
    cells = [tuple(c) for c in approach_cells]
    end = max(cells, key=lambda c: (c[0] - door[0]) * dx + (c[1] - door[1]) * dz) if cells else tuple(door)
    out = (end[0] + dx, end[1] + dz)
    return {
        'door': {'cell': list(door), 'origin': 'generated' if generated else 'observed'},
        'approach': [list(c) for c in cells],
        'connection': {'cell': list(out), 'y': base_y, 'direction': front,
                       'verified': not generated},
        'clearance': {'columns': [list(c) for c in cells + [out]], 'headroom': int(headroom)},
        'provenance': prov.provenance_record(
            source='inference' if generated else 'vector_map',
            confidence='low' if generated else 'medium', evidence_ids=evidence_ids),
    }


def entrances_from_blocks(blocks, footprint=()):
    """Reconstruct entrances from a landed plan's blocks (read-only; legacy builds with no recorded door).

    A lower door leaf marks the door; its ``facing`` is outward. Feature-generated steps/landing are
    the consecutive outward columns (outside ``footprint``) that hold a block between the door's floor
    and ``SCAN_DOWN`` below it. Two leaves of one double door give one entrance. The block list cannot
    say whether the door came from evidence, so every result is ``generated`` (never verified).

    @param blocks: ``{(x, y, z): block id with properties}`` from the journal's recorded plan
    @param footprint: ``(x, z)`` footprint columns; approach scanning stops at them
    @returns: list of ``entrance_approach`` dicts, ordered by door column
    """
    fp = {tuple(c) for c in footprint}
    column: dict = {}
    for (x, y, z), b in blocks.items():
        column.setdefault((x, z), []).append((y, b))
    doors = []
    for (x, y, z), b in blocks.items():
        m = re.match(r'(?:minecraft:)?(\w+_door)\[(.*)\]', b or '')
        if not m or 'half=lower' not in m.group(2):
            continue
        f = re.search(r'facing=(\w+)', m.group(2))
        if f and f.group(1) in FACING_FACE:
            doors.append(((x, z), y, FACING_FACE[f.group(1)]))
    doors.sort()
    out, used = [], set()
    for cell, y, face in doors:
        if cell in used:
            continue
        lat = (1, 0) if face in 'NS' else (0, 1)
        for k in (1, -1):
            other = (cell[0] + lat[0] * k, cell[1] + lat[1] * k)
            if any(c == other and f == face and abs(yy - y) <= 1 for c, yy, f in doors):
                used.add(other)                         # the other leaf of a double door, same facing
        dx, dz = DIRS[face]
        approach, base = [], None
        for k in range(1, MAX_APPROACH + 1):
            col = (cell[0] + dx * k, cell[1] + dz * k)
            if col in fp:
                break
            ys = [yy for yy, bb in column.get(col, ()) if y - 1 - SCAN_DOWN <= yy <= y and bb and
                  not re.match(r'(?:minecraft:)?(air|cave_air|void_air)\b', bb)]
            if not ys:
                break
            approach.append(col)
            base = max(ys)
        out.append(entrance_approach(cell, face, approach_cells=approach,
                                     base_y=(base + 1) if base is not None else y, generated=True))
    return out
