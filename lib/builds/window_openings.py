"""Choose bounded window openings against a finished shell, before writing any glass."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from cellmath import DIRS
from site_kit import AIR_IDS
from surface_probe import PLANTS



@dataclass(frozen=True)
class Window:
    """One intended glazed voxel; corner permission must come from explicit evidence."""
    pos: tuple[int, int, int]
    face: str
    stepped: bool = False
    corner_evidence: bool = False


_NON_STRUCTURAL_EXTRAS = {'water', 'lava', 'iron_bars', 'snow', 'grass', 'short_grass', 'tall_grass', 'vine'}
_NON_STRUCTURAL = set(AIR_IDS) | _NON_STRUCTURAL_EXTRAS


def structural(state):
    """Whether a block can terminate an opening; doors and glazing are never jambs."""
    name = (state or 'air').split('[')[0].split(':')[-1]
    return name not in _NON_STRUCTURAL and name not in PLANTS and not any(
        token in name for token in ('glass', 'door', 'fence', 'leaves', 'torch', 'flower',
                                   'button', 'carpet', 'rail', 'sign', 'chain', 'sapling', 'ladder',
                                   'lantern', 'pressure_plate', 'candle', 'banner'))


def choose_windows(candidates, *, block, walls, exposed, outside_ground, protected=()):
    """Resolve all intended openings against one shell snapshot, independently of write order.

    @param candidates: Window values for all faces/storeys of one mass
    @param block: finished-shell lookup (x, y, z), before any window writes
    @param walls: shell wall columns
    @param exposed: callback (column, face) for exterior geometry
    @param outside_ground: mapping exterior (x, z) to known terrain Y or None
    @param protected: door/frame positions that must remain intact
    @returns: (position-to-block writes, per-face Counter of outcomes/reasons).
        Each intended position counts once per face, including shared corners;
        face totals must not be summed to count distinct glass writes. Rejection
        reasons name the first failed check; boundary details apply to the whole
        connected opening, which is accepted or refused together.
    """
    protected = set(protected)
    report = defaultdict(Counter)
    grouped = defaultdict(list)
    for window in candidates:
        grouped[window.pos].append(window)

    def record(pos, reason):
        for face in {v.face for v in grouped[pos]}:
            report[face][reason] += 1

    def is_tread(c):
        """A tread corner of a diagonal staircase: no neighbouring wall cell shares any face it exposes.

        A sharp corner of an L-shape or setback has a straight run along at least one exposed face, so
        it is not a tread and keeps its pier.
        """
        for face in DIRS:
            if not exposed(c, face):
                continue
            along = ((1, 0), (-1, 0)) if face in 'NS' else ((0, 1), (0, -1))
            if any((c[0] + dx, c[1] + dz) in walls and exposed((c[0] + dx, c[1] + dz), face)
                   for dx, dz in along):
                return False
        return True

    active = {}
    for pos, choices in sorted(grouped.items()):
        # A corner with two evidenced faces still has one deterministic material.
        w = min(choices, key=lambda v: (not v.corner_evidence, v.face))
        x, y, z = pos
        c = (x, z)
        corner = sum(exposed(c, d) for d in DIRS) > 1
        if pos in protected:
            reason = 'protected entrance'
        elif not structural(block(*pos)):
            reason = 'non-wall'
        elif corner and not (any(v.corner_evidence for v in choices)
                             or (any(v.stepped for v in choices) and is_tread(c))):
            reason = 'corner pier / tread too short'
        else:
            heights = [outside_ground.get((x + DIRS[v.face][0], z + DIRS[v.face][1]))
                       for v in choices]
            if any(h is None for h in heights):
                reason = 'outside ground unavailable'
            elif y <= max(heights):
                reason = 'below outside sill'
            else:
                active[pos] = (w, corner)
                continue
        record(pos, reason)

    writes = {}
    remaining = set(active)
    while remaining:
        start = min(remaining)
        w, _corner = active[start]
        def offsets_for(window):
            dx, dz = (1, 0) if window.face in 'NS' else (0, 1)
            return ((dx, 0, dz), (-dx, 0, -dz), (0, 1, 0), (0, -1, 0))
        component, todo = set(), [start]
        while todo:
            p = todo.pop()
            if p in component:
                continue
            component.add(p)
            current = active[p][0]
            offsets = offsets_for(current)
            if current.corner_evidence:
                offsets += tuple((dx, 0, dz) for dx, dz in DIRS.values())
            for ox, oy, oz in offsets:
                q = (p[0] + ox, p[1] + oy, p[2] + oz)
                if q in remaining and q not in component and (
                    active[q][0].face == current.face or current.corner_evidence or active[q][0].corner_evidence
                ):
                    todo.append(q)
        remaining -= component
        missing_bounds = set()
        # Full glass blocks need no frame (jamb, sill, lintel); only panes do. A window on a stepped
        # face or an evidenced corner is always full glass, so it skips the boundary check.
        frameless = any(active[p][0].stepped or active[p][1] for p in component)
        for x, y, z in () if frameless else component:
            current = active[x, y, z][0]
            for ox, oy, oz in offsets_for(current):
                q = (x + ox, y + oy, z + oz)
                if q in component:
                    continue
                supported = q not in active and structural(block(*q)) and (oy != 0 or (q[0], q[2]) in walls)
                if not supported and oy == 0 and active[x, y, z][1] and active[x, y, z][0].corner_evidence:
                    # An explicitly observed wraparound corner terminates against
                    # the perpendicular wall instead of inventing an outside jamb.
                    nx, nz = DIRS[current.face]
                    supported = any((x + sign * nx, z + sign * nz) in walls
                        and ((x + sign * nx, y, z + sign * nz) in component or (
                            (x + sign * nx, y, z + sign * nz) not in active
                            and structural(block(x + sign * nx, y, z + sign * nz)))) for sign in (-1, 1))
                if not supported:
                    boundary = 'lintel' if oy > 0 else 'sill' if oy < 0 else 'jamb'
                    if q in active:
                        missing_bounds.add(f'{boundary} meets another opening')
                    elif oy == 0 and (q[0], q[2]) not in walls:
                        missing_bounds.add('jamb outside wall')
                    else:
                        missing_bounds.add(f'non-structural {boundary}')
        if missing_bounds:
            reason = 'unbounded opening: ' + ', '.join(sorted(missing_bounds))
            for p in component:
                record(p, reason)
            continue
        # Stepped treads and explicitly evidenced corners use full cubes. A pane
        # must not acquire a third arm from an opening in a perpendicular plane.
        solid = any(active[p][0].stepped or active[p][1] for p in component)
        for x, y, z in component:
            nx, nz = DIRS[active[x, y, z][0].face]
            for sign in (-1, 1):
                q = (x + sign * nx, y, z + sign * nz)
                if q in active or structural(block(*q)) or 'glass' in (block(*q) or '') or 'iron_bars' in (block(*q) or ''):
                    solid = True
        axis = 'east=true,west=true' if w.face in 'NS' else 'north=true,south=true'
        state = 'glass' if solid else f'glass_pane[{axis}]'
        writes.update((p, state) for p in component)
        for p in component:
            record(p, 'solid-glass' if solid else 'panes')
    return writes, report


def report_lines(label, report):
    """Compare selected glass with unique intended voxels on each face.

    The percentage measures retention of intended glazing, not glazed wall area.
    Shared corner positions count on each face that requested them.
    """
    lines = []
    for face, counts in sorted(report.items()):
        reasons = {k: v for k, v in counts.items() if k not in ('panes', 'solid-glass')}
        omitted = sum(reasons.values())
        panes, solid = counts.get('panes', 0), counts.get('solid-glass', 0)
        selected = panes + solid
        intended = selected + omitted
        pct = 100 * selected / intended if intended else 0
        detail = ', '.join(f'{k}={v}' for k, v in sorted(reasons.items()))
        lines.append(f'{label} face {face}: windows intended={intended} '
                     f'selected={selected} ({pct:.1f}%) panes={panes} '
                     f'solid-glass={solid} omitted={omitted}'
                     + (f' ({detail})' if detail else ''))
    return lines
