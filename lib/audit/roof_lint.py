#!/usr/bin/env python3
"""Roof lint: ridge jogs and stair shapes that fight the game's neighbour rule.

A real pitched roof (gable or hip) on a squared Minecraft footprint keeps each ridge
on one straight block line. A ridge whose position shifts by one or more columns from
one row to the next means the slope under it has been displaced sideways, so every stair
on that seam changes height and the roof reads as zig-zagged / misaligned blocks
(I-034: broadlands_rd_56_62 squared a 22 deg pair as three stepped segments, each with
its own ridge column).

The stable stair ``shape`` follows stairs at the **same Y** (and same ``half``)
beside the block (vanilla ``StairBlock.getStairsShape``). Explicit batch-written
states may persist until a neighbour update, so live readback is separate. Plans
must set facing so that rule yields the intended outer/inner/straight mitre, and
write that same shape into the state; otherwise joins change after apply (#169).

  python3 scripts/roof_lint.py --world <world> <build> [<build> ...]   # recorded plans in the cell journal
  python3 scripts/roof_lint.py --world <world> --all
  python3 scripts/roof_lint.py --world <world> --live <build> [<build> ...]   # also read the live world

``--live`` flushes and reads the open world around each recorded plan's stairs and reports
(a) live stairs beside the plan's stairs whose shape disagrees with the neighbour rule on the live
neighbours (judged only where all four neighbours were read), and
(b) planned stairs whose live shape differs from the plan (or that are no longer stairs).
The apply readback ignores stair ``shape`` because the game rewrites it, so this is the check
that the world shows the shapes that were designed.

site_kit.apply() runs ridge_jogs() on every plan and refuses jogs unless the plan
sets `roof_jog_ok = "<reason + evidence id>"`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from block_store import state_props
from roof_geometry import ROOF_DELTA, stair_shape_from_neighbours  # noqa: E402
from script_env import ROOT  # noqa: E402
from site_kit import AIR_IDS, full
from surface_probe import PLANTS, short_id
OUT = ROOT / 'verification/site'

# facing -> (axis the slope rises along, +1/-1 uphill direction on that axis)
_UP = {'east': ('x', 1), 'west': ('x', -1), 'south': ('z', 1), 'north': ('z', -1)}
# Roof lint historically skips air/cave_air, but treats void_air as a column.
_AIR_EXCLUSIONS = frozenset({'void_air'})
_AIR_EXTRAS = ('minecraft:light',)
_ROOF_AIRS = tuple(full(name) for name in AIR_IDS if name not in _AIR_EXCLUSIONS)
_SKIP = _ROOF_AIRS[:1] + _AIR_EXTRAS + _ROOF_AIRS[1:]
_THIN = ('pane', 'pot', 'potted', 'torch', 'lantern', 'sign', 'button', 'fence', 'wall', 'bars',
         'door', 'carpet', 'leaves', 'flower', 'sapling', 'grass[', 'short_grass', 'fern')


def _stair_facing_half(state: str) -> tuple[str, str] | None:
    """Parse stair facing/half from a block state, or None if not a bottom/top stair."""
    if '_stairs' not in state:
        return None
    p = state_props(state)
    facing = p.get('facing')
    half = p.get('half', 'bottom')
    if facing not in ROOF_DELTA or half not in ('bottom', 'top'):
        return None
    return facing, half


def stair_shape_mismatches(cells: dict) -> list[dict]:
    """Stairs whose planned ``shape`` disagrees with the same-Y neighbour rule.

    @param cells: ``{(x, y, z): block state}`` (a Plan's cells or a recorded journal plan)
    @returns: list of ``{x, y, z, facing, half, planned, expected}`` — one per mismatch
    """
    by_pos = {}
    for (x, y, z), s in cells.items():
        if s in _SKIP or short_id(s) in PLANTS or any(t in s.split(':', 1)[-1] for t in _THIN):
            continue
        fh = _stair_facing_half(s)
        if fh is None:
            continue
        by_pos[(x, y, z)] = (s, fh[0], fh[1], state_props(s).get('shape', 'straight'))

    out = []
    for (x, y, z), (state, facing, half, planned) in by_pos.items():
        adjacent: dict[str, tuple[str, str] | None] = {}
        for face, (dx, dz) in ROOF_DELTA.items():
            n = by_pos.get((x + dx, y, z + dz))
            adjacent[face] = (n[1], n[2]) if n is not None else None
        expected = stair_shape_from_neighbours(facing, half, adjacent)
        if planned != expected:
            out.append({
                'x': x, 'y': y, 'z': z,
                'facing': facing, 'half': half,
                'planned': planned, 'expected': expected,
            })
    return out


def live_stair_cells(planned: dict, block, margin: int = 2, prefetch=None) -> dict:
    """Live stair states in the box around a plan's stairs.

    The margin must be at least 2: a stair next to a planned stair is judged by its own four
    neighbours, which therefore have to lie inside the box (see :func:`live_rule_mismatches`).

    @param planned: ``{(x, y, z): state}`` recorded plan
    @param block: ``block(x, y, z)`` reader returning a state string or ``None``
    @param margin: cells of margin around the planned stairs (catches neighbouring stairs)
    @param prefetch: optional ``prefetch(positions)`` that warms ``block`` for the whole box
    @returns: ``{(x, y, z): state}`` of the stairs found
    """
    spots = [pos for pos, s in planned.items() if _stair_facing_half(s) is not None]
    if not spots:
        return {}
    lo = [min(p[i] for p in spots) - margin for i in range(3)]
    hi = [max(p[i] for p in spots) + margin for i in range(3)]
    box = [(x, y, z) for x in range(lo[0], hi[0] + 1) for y in range(lo[1], hi[1] + 1)
           for z in range(lo[2], hi[2] + 1)]
    if prefetch is not None:
        prefetch(box)
    live = {}
    for x, y, z in box:
        state = block(x, y, z)
        if state and _stair_facing_half(state) is not None:
            live[x, y, z] = state
    return live


def live_rule_mismatches(planned: dict, live: dict) -> list[dict]:
    """Live stairs beside the plan's stairs whose shape disagrees with the neighbour rule.

    Only stairs within one cell (same height) of a planned stair are judged, because only they
    have all four neighbours inside the box read by :func:`live_stair_cells`; a stair at the edge
    would be judged against neighbours that were never read.

    @param planned: recorded plan cells
    @param live: ``live_stair_cells`` result
    @returns: ``stair_shape_mismatches`` entries for the judged stairs
    """
    near = {(x + dx, y, z + dz) for (x, y, z), s in planned.items() if _stair_facing_half(s) is not None
            for dx in (-1, 0, 1) for dz in (-1, 0, 1)}
    return [m for m in stair_shape_mismatches(live) if (m['x'], m['y'], m['z']) in near]


def plan_vs_live_shapes(planned: dict, live: dict) -> list[dict]:
    """Planned stairs whose live state differs in facing, half or shape (or is no longer a stair).

    @param planned: recorded plan cells
    @param live: ``live_stair_cells`` result
    @returns: list of ``{x, y, z, planned, live}`` with shapes (``live`` is ``None`` when missing)
    """
    out = []
    for (x, y, z), state in planned.items():
        fh = _stair_facing_half(state)
        if fh is None:
            continue
        got = live.get((x, y, z))
        want = (fh[0], fh[1], _props(state).get('shape', 'straight'))
        have = None if got is None else (*_stair_facing_half(got), _props(got).get('shape', 'straight'))
        if have != want:
            out.append({'x': x, 'y': y, 'z': z, 'planned': want, 'live': have})
    return out


def _tops(cells: dict) -> dict:
    """Highest structural planned block per column.

    @param cells: {(x, y, z): block state}
    @returns: {(x, z): (y, state)} ignoring air, panes, pots and other thin attachables
    """
    top = {}
    for (x, y, z), s in cells.items():
        if s in _SKIP or short_id(s) in PLANTS or any(t in s.split(':', 1)[-1] for t in _THIN):
            continue
        if (x, z) not in top or y > top[(x, z)][0]:
            top[(x, z)] = (y, s)
    return top


def _slope(top: dict, x: int, z: int):
    """Facing of the roof stair on top of column (x, z), or None if it is not one."""
    t = top.get((x, z))
    if not t or '_stairs' not in t[1]:
        return None
    p = state_props(t[1])
    return p.get('facing') if p.get('half', 'bottom') == 'bottom' else None


def _party_cap(top: dict, at, row: int, p: int, q: int) -> bool:
    """True if the ridge cap between slope ends p and q is a party-wall parapet.

    A parapet is >= 2 blocks wide and not of the roof's own material (e.g. bricks across a
    deepslate_tile ridge). A 1-wide stack is a chimney, not a party wall.
    """
    if q - p - 1 < 2:
        return False
    mat = top[at(row, p)][1].split(':', 1)[-1].split('[')[0].replace('_stairs', '')
    return any(not top[at(row, i)][1].split(':', 1)[-1].startswith(mat) for i in range(p + 1, q))


def _ridges(top: dict, axis: str) -> dict:
    """Ridge positions per row for slopes rising along `axis`.

    A ridge is where a run of >= 2 stairs rising in + direction meets (directly or across a
    1-4 block cap of non-stair blocks) a run of >= 2 stairs rising in - direction.

    @returns: {row: [(pos2, y, party), ...]} with pos2 = twice the midpoint of the crest-height
        blocks between the two stair runs (integer) and party = True when the cap is a
        party-wall parapet (see _party_cap)
    """
    rise, fall = ('east', 'west') if axis == 'x' else ('south', 'north')
    key = (lambda c: (c[1], c[0])) if axis == 'x' else (lambda c: (c[0], c[1]))
    rows = {}
    for c in top:
        row, pos = key(c)
        rows.setdefault(row, set()).add(pos)
    at = (lambda row, pos: (pos, row)) if axis == 'x' else (lambda row, pos: (row, pos))
    out = {}
    for row, poss in rows.items():
        lo, hi = min(poss), max(poss)
        face = {p: _slope(top, *at(row, p)) for p in range(lo, hi + 1)}
        for p in range(lo + 1, hi + 1):
            if face[p] != rise or face[p - 1] != rise or face.get(p + 1) == rise:
                continue
            for cap in range(5):
                q = p + cap + 1
                if face.get(q) == fall and face.get(q + 1) == fall and \
                        all(face.get(p + i) not in (rise, fall) and (p + i) in poss
                            for i in range(1, cap + 1)):
                    y = max(top[at(row, i)][0] for i in range(p, q + 1))
                    # Midpoint of the crest-height blocks, not of the stair ends: on a diagonal
                    # ridge the crest edge is a stair in one row and a corner slab in the next,
                    # which moved the stair-end midpoint a block with no change in the roof.
                    crest = [i for i in range(p, q + 1) if top[at(row, i)][0] == y]
                    out.setdefault(row, []).append(
                        (min(crest) + max(crest), y, _party_cap(top, at, row, p, q)))
                    break
    return out


def ridge_jogs(cells: dict) -> list[dict]:
    """Find ridge lines that shift sideways between adjacent rows.

    Steps beside a party-wall parapet are allowed: terrace houses may stagger where they
    meet, and a ridge that keeps drifting the same way across nearby rows is a rotated roof
    on the block grid (``_drifts_on``). A shift anywhere else is one roof displaced against
    itself.

    @param cells: {(x, y, z): block state} (a Plan's cells or a recorded journal plan)
    @returns: list of {'axis', 'row', 'pos', 'next_pos', 'y', 'shift'} — one per seam; the
        positions are MC block coordinates of the ridge midpoint on the slope axis
    """
    top = _tops(cells)
    jogs = []
    for axis in ('x', 'z'):
        ridges = _ridges(top, axis)
        for row, rs in ridges.items():
            nxt = ridges.get(row - 1, ())
            for pos2, y, party in rs:
                if party or any(q == pos2 for q, _, _ in nxt):
                    continue
                for npos2, ny, nparty in nxt:
                    shift = (npos2 - pos2) / 2
                    # half-block shifts are a ridge cap widening by one, not a displaced slope
                    if not nparty and 1 <= abs(shift) <= 4 and abs(ny - y) <= abs(shift) + 1:
                        if _drifts_on(ridges, row, pos2, npos2):
                            continue
                        jogs.append({'axis': axis, 'row': row, 'pos': pos2 / 2,
                                     'next_pos': npos2 / 2, 'y': y, 'shift': shift})
    return jogs


_DRIFT_ROWS = 3  # rows searched either side of a seam for a ridge that keeps moving the same way
_DRIFT_REACH2 = 4  # a continuing ridge lies within 2 blocks (pos2 units: 4) of the seam end


def _drifts_on(ridges: dict, row: int, pos2: int, npos2: int) -> bool:
    """True if the ridge keeps moving the same way beyond a seam, as a rotated roof's ridge does.

    A displaced slope is straight on both sides of the seam. A diagonal ridge steps one block
    every few rows on the block grid, so the ridge one or more rows away on either side has
    already moved, or goes on moving, in the same direction.

    @param ridges: {row: [(pos2, y, party), ...]} for one axis (see _ridges)
    @param row: row of the ridge at ``pos2``; the next row (row - 1) holds ``npos2``
    @param pos2: twice the ridge midpoint at ``row``
    @param npos2: twice the ridge midpoint at ``row - 1``
    @returns: whether a ridge before or after the seam continues the shift's direction
    """
    sign = 1 if npos2 > pos2 else -1
    for k in range(1, _DRIFT_ROWS + 1):
        # rows before the seam (away from row - 1): the ridge should sit behind pos2
        for q, _, _ in ridges.get(row + k, ()):
            if 0 < (pos2 - q) * sign <= _DRIFT_REACH2:
                return True
        # rows after the seam: the ridge should sit ahead of npos2
        for q, _, _ in ridges.get(row - 1 - k, ()):
            if 0 < (q - npos2) * sign <= _DRIFT_REACH2:
                return True
    return False


def _load(name: str) -> dict:
    from cell_registry import view_registry
    got = view_registry().recorded(name)
    if got is None:
        raise SystemExit(f'no recorded build {name!r} in the cell journal')
    return got


def main() -> int:
    """CLI: report ridge jogs and stair-shape mismatches for recorded builds.

    @returns: process exit code (1 if any jog or stair-shape mismatch was found)
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('builds', nargs='*')
    ap.add_argument('--all', action='store_true', help='every build recorded in the cell journal')
    ap.add_argument('--live', action='store_true',
                    help='also read the open world: live stair shapes against the rule and the plan')
    a = ap.parse_args()
    if a.all:
        from cell_registry import latest_entries, view_registry
        names = a.builds or [e.plan for e in latest_entries(view_registry())]
    else:
        names = a.builds
    if not names:
        ap.error('name a build or pass --all')
    bad = 0
    block = prefetch = None
    if a.live:
        from site_kit import fresh_planning_world
        world = fresh_planning_world(strict=True)
        block, prefetch = world.block, getattr(world, 'prefetch', None)
    for n in names:
        cells = _load(n)
        jogs = ridge_jogs(cells)
        shapes = stair_shape_mismatches(cells)
        if block is not None:
            live = live_stair_cells(cells, block, prefetch=prefetch)
            drift = plan_vs_live_shapes(cells, live)
            seen = live_rule_mismatches(cells, live)
            print(f'{n}: live read {len(live)} stair(s): {len(drift)} differ from the plan, '
                  f'{len(seen)} disagree with the neighbour rule')
            for m in drift[:8]:
                print(f"   ({m['x']},{m['y']},{m['z']}) planned={m['planned']} live={m['live']}")
            for m in seen[:8]:
                print(f"   live ({m['x']},{m['y']},{m['z']}) facing={m['facing']} "
                      f"live shape={m['planned']} rule says {m['expected']}")
            bad += bool(drift or seen)
        if jogs or shapes:
            bad += 1
            if jogs:
                print(f'{n}: {len(jogs)} ridge jog(s)')
                for j in jogs[:8]:
                    print(f"   ridge along {'z' if j['axis'] == 'x' else 'x'} row {j['row']}: "
                          f"{j['axis']}={j['pos']:g} -> {j['next_pos']:g} (y{j['y']}, shift {j['shift']:+g})")
            if shapes:
                print(f'{n}: {len(shapes)} stair-shape mismatch(es)')
                for m in shapes[:8]:
                    print(f"   ({m['x']},{m['y']},{m['z']}) facing={m['facing']} "
                          f"planned={m['planned']} expected={m['expected']}")
    if not bad:
        print(f'{len(names)} build(s): no ridge jogs, 0 stair-shape mismatches')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
