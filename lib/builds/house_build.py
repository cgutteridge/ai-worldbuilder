#!/usr/bin/env python3
"""Generic vector-map footprint house builder (house only: walls, floors, doors, windows, flatish or pitched roofs).

One module for every house-only run. A run is a small JSON spec in site/builds/<name>.json
instead of a script copy:

  name           plan name (also the cell journal plan / feature id)
  notes          provenance: map ways, aerial/streetlevel evidence, date, queue item (free text, never read by code)
  modifies       earlier builds this one may overwrite (default [])
  ways           {"<map way id>": "<label>"} the footprints to build
  roofs          {"<label>": "<roof material>"} from config/roof-materials.json
  roof_pitch     optional {"<label>": rise_per_run}, finite number 0..1 (default 0.5)
                 <=0.5 keeps legacy slab rings; >0.5 uses stairs/slabs at half-block-snapped heights,
                 the overhanging eave ring starting at wall-top level as a slab or stair (#414);
                 the full block under a membrane at that level does not replace the wall's top course (#429);
                 a footprint that is a rotated rectangle takes heights from its fitted axes (#415)
                 group roofs require equal effective pitches; first label supplies the material
  roof_fit       optional {"<label>": "columns"|"octant"} (default columns). octant designs a pitched
                 rectangular roof as half-block cells and fits slab/stair/full blocks to it (#415);
                 needs roof_pitch above 0.5 and mode each; only the rim of the eave is held to one
                 slab or stair thick, notch columns of a rotated outline are fitted freely
  groups         [{"nos": [labels], "mode": "each"|"group", "front": "N"|"S"|"E"|"W"}]
                 "group" = one roof over several houses sharing a floor level
  floor_min      minimum floor y (front pavement height) or null
  step_max       how far a door stoop looks outward for ground to level or step (default 4; 13 on long ledges)
  ledge_stair    true: ground that ends at a 1-block ledge beyond 4 columns gets a rising stair (default false)
  door_frame     true (default): doors only where the wall has a wall cell each side, so they are framed on angled walls
  keep_edits     true: stoop/step cells survive the earlier-build clash filter (needed when `modifies` a ground build)
  body           optional {"<label>": {...}} terrace-house body (task #333): storeys (wall storeys, default 2),
                 front_depth (columns of one-storey flat roof in front of the body, default 0), dormers (flush wall
                 dormers on the front slope, 0-3), bay (upper-storey bay window on the front wall; needs two storeys),
                 bay_storey (default 1). Needs roof_pitch above 0.5 and
                 mode each. lib/builds/terrace_body.py derives the numbers from lidar eave and ridge and the class's
                 `body` recipe (config/land-use-classes.json) for the typology generator
  chimneys       optional {"<label>": "per_house"|"per_pair"|"party_walls"|"none"|[{"at": [x, z]|[x, y, z]}, ...]}
                 Domestic one-by-one brick stacks through the roof (WALL to ridge+2, flower_pot cap).
                 Default when omitted: one stack per roof unit (legacy). party_walls / per_pair read
                 footprint adjacency in this module (see footprint_party_walls). Group roofs require
                 matching options when more than one label sets chimneys.

Usage: python3 scripts/house_build.py --world <world> site/builds/<name>.json [--apply [--overwrite]]
Direct apply refuses an existing landed build unless --overwrite is explicit; undo first to remove old blocks.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from script_env import ROOT  # noqa: E402
import place_config  # noqa: E402
import osm_footprints  # noqa: E402
from roof_geometry import (edge_distance_field, pitched_roof_states, roof_block_y,  # noqa: E402
                           align_stair_shapes, rotated_hip_depth, stair_effect_lines, stair_shape_from_neighbours)
from roof_octant import block_states, fit_octant_roof  # noqa: E402
from roof_material import family_variants, roof_blocks, roof_class  # noqa: E402
import terrace_body  # noqa: E402
import stoop  # noqa: E402
from stoop import FACE, GROUND, TOWARD, stair  # noqa: E402
from site_kit import Plan, apply, fresh_planning_world, full, guard_door_frames, guard_roof_jogs, preview  # noqa: E402

WALL, FLOOR = 'bricks', 'spruce_planks'
TOP = 20
STOREY = terrace_body.STOREY_H
CHIMNEY_MODES = ('per_house', 'per_pair', 'party_walls', 'none')
from cellmath import DIRS, N4, N8, OPP  # noqa: E402


def footprint_party_walls(footprints):
    """Party walls from footprint adjacency in this module.

    Two footprints share a party wall when any cell of one is 4-adjacent (N4) to
    any cell of the other. Wall-line cells are the columns on either side of that
    shared edge.

    @param footprints: sequence of ``{(x, z), ...}`` footprints
    @returns: list of ``(i, j, wall_cells)`` with ``i < j`` and wall_cells a set of columns
    """
    out = []
    indexed = list(enumerate(footprints))
    for i, a in indexed:
        for j, b in indexed[i + 1:]:
            wall = set()
            for x, z in a:
                for dx, dz in N4:
                    n = (x + dx, z + dz)
                    if n in b:
                        wall.add((x, z))
                        wall.add(n)
            if wall:
                out.append((i, j, wall))
    return out


def _chimney_column(candidates, depth, own, prefer_x):
    """Deepest column among ``candidates``, preferring ``prefer_x`` (legacy tie-break).

    @param candidates: columns to choose from (iterable)
    @param depth: depth field (rotated hip depth or edge distance)
    @param own: columns that count as belonging to the pick region
    @param prefer_x: X preference for ties (unit or house centre)
    @returns: chosen ``(x, z)``
    """
    return max(candidates, key=lambda c: (
        depth[c] if c in own and c in depth else -1,
        -abs(c[0] - prefer_x),
    ))


def _house_axis_order(houses):
    """Order house indices along a terrace/semi by footprint centroid (x, then z).

    @param houses: house masses under one roof unit
    @returns: list of indices into ``houses``
    """
    def key(i):
        cells = houses[i].cells
        return (sum(x for x, _ in cells) / len(cells),
                sum(z for _, z in cells) / len(cells))

    return sorted(range(len(houses)), key=key)


def chimney_sites(option, houses, own, dist, depth):
    """Column positions for domestic through-chimneys on one roof unit.

    ``option`` None keeps the legacy one-stack-per-roof-unit pick. Mode strings
    and explicit ``[{"at": ...}]`` lists come from the per-label ``chimneys`` spec.

    @param option: None, a CHIMNEY_MODES string, or an explicit at-list
    @param houses: houses under this roof unit
    @param own: roof-unit footprint columns
    @param dist: edge-distance field (also the candidate column set)
    @param depth: depth field used for ridge preference
    @returns: list of ``(x, z)`` stack columns
    """
    if option == 'none':
        return []
    if isinstance(option, list):
        sites = []
        for row in option:
            at = row['at']
            sites.append((int(at[0]), int(at[2] if len(at) > 2 else at[1])))
        return sites
    if option is None:
        prefer = sum(x for x, _ in own) / len(own)
        return [_chimney_column(dist, depth, own, prefer)]
    party = footprint_party_walls([h.cells for h in houses])
    if option == 'party_walls':
        sites = []
        for _i, _j, wall in party:
            # Sort so ties on depth/centre keep a stable lower-x column.
            cand = sorted(c for c in wall if c in dist)
            if cand:
                prefer = sum(x for x, _ in cand) / len(cand)
                sites.append(_chimney_column(cand, depth, own, prefer))
        return sites
    if option == 'per_house':
        sites = []
        for h in houses:
            region = h.body or h.cells
            prefer = sum(x for x, _ in region) / len(region)
            sites.append(_chimney_column(dist, depth, region, prefer))
        return sites
    # per_pair: one stack at the party wall of each consecutive pair along the terrace
    order = _house_axis_order(houses)
    by_pair = {(min(i, j), max(i, j)): wall for i, j, wall in party}
    sites = []
    idx = 0
    while idx < len(order):
        if idx + 1 < len(order):
            a, b = order[idx], order[idx + 1]
            wall = by_pair.get((min(a, b), max(a, b)))
            if wall is not None:
                cand = sorted(c for c in wall if c in dist)
                if cand:
                    prefer = sum(x for x, _ in cand) / len(cand)
                    sites.append(_chimney_column(cand, depth, own, prefer))
                idx += 2
                continue
        region = houses[order[idx]].body or houses[order[idx]].cells
        prefer = sum(x for x, _ in region) / len(region)
        sites.append(_chimney_column(dist, depth, region, prefer))
        idx += 1
    return sites


def _validate_chimney_option(path, no, opt):
    """Refuse a bad per-label chimneys value.

    @param path: spec path (for error text)
    @param no: house label
    @param opt: chimneys value for that label
    """
    if isinstance(opt, str):
        if opt not in CHIMNEY_MODES:
            raise SystemExit(f'{path}: chimneys[{no!r}] must be one of {CHIMNEY_MODES} or an at-list')
        return
    if isinstance(opt, list):
        if not opt:
            raise SystemExit(f'{path}: chimneys[{no!r}] at-list must not be empty (use "none")')
        for i, row in enumerate(opt):
            if not isinstance(row, dict) or 'at' not in row:
                raise SystemExit(f'{path}: chimneys[{no!r}][{i}] needs an "at" coordinate')
            at = row['at']
            if (not isinstance(at, (list, tuple)) or len(at) not in (2, 3)
                    or any(isinstance(v, bool) or not isinstance(v, int) for v in at)):
                raise SystemExit(f'{path}: chimneys[{no!r}][{i}].at must be [x, z] or [x, y, z] ints')
        return
    raise SystemExit(f'{path}: chimneys[{no!r}] must be a mode string or an at-list')


def load_spec(path):
    """Read and minimally validate a build spec; fills defaults.

    @param path: spec JSON path
    @returns: spec dict
    """
    spec = json.loads(Path(path).read_text())
    roofs = spec.get('roofs', {})
    if ('storeys' in spec or 'facade_plan' in spec
            or str(spec.get('notes', '')).lstrip().startswith('FEATURE PATH')
            or not isinstance(roofs, dict)
            or any(not isinstance(material, str) for material in roofs.values())):
        raise SystemExit(f'{path}: feature-path spec; use scripts/feature_build.py instead of house_build.py')
    for k in ('name', 'ways', 'roofs', 'groups'):
        if k not in spec:
            raise SystemExit(f'{path}: missing "{k}"')
    spec.setdefault('modifies', [])
    spec.setdefault('floor_min', None)
    spec.setdefault('step_max', 4)
    spec.setdefault('ledge_stair', False)
    spec.setdefault('keep_edits', False)
    spec.setdefault('door_frame', True)
    labels = set(spec['ways'].values())
    pitches = spec.get('roof_pitch', {})
    if not isinstance(pitches, dict):
        raise SystemExit(f'{path}: roof_pitch must be a label-to-rise_per_run object')
    for no, pitch in pitches.items():
        if no not in labels or no not in roofs:
            raise SystemExit(f'{path}: roof_pitch label {no!r} needs a way and a roof')
        if (isinstance(pitch, bool) or not isinstance(pitch, (int, float))
                or not 0 <= pitch <= 1 or not math.isfinite(pitch)):
            raise SystemExit(f'{path}: roof_pitch[{no!r}] must be a finite number in [0, 1]')
    for no, material in roofs.items():
        resolved = roof_blocks(material, roof_class(pitches.get(no, 0.5), 1))
        if not resolved['ok']:
            raise SystemExit(f'{path}: roof {no!r}: {"; ".join(resolved["messages"])}')
    fits = spec.get('roof_fit', {})
    if not isinstance(fits, dict):
        raise SystemExit(f'{path}: roof_fit must be a label-to-fit object')
    for no, fit in fits.items():
        if no not in labels or no not in roofs:
            raise SystemExit(f'{path}: roof_fit label {no!r} needs a way and a roof')
        if fit not in ('columns', 'octant'):
            raise SystemExit(f'{path}: roof_fit[{no!r}] must be "columns" or "octant"')
        if fit == 'octant' and roof_class(pitches.get(no, 0.5), 1) != 'pitched':
            raise SystemExit(f'{path}: roof_fit[{no!r}] octant needs a roof_pitch above 0.5')
    bodies = spec.setdefault('body', {})
    if not isinstance(bodies, dict):
        raise SystemExit(f'{path}: body must be a label-to-block object')
    for no, block in bodies.items():
        if no not in labels or no not in roofs:
            raise SystemExit(f'{path}: body label {no!r} needs a way and a roof')
        if not isinstance(block, dict):
            raise SystemExit(f'{path}: body[{no!r}] must be an object')
        try:
            terrace_body.validate_block(no, block)
        except ValueError as exc:
            raise SystemExit(f'{path}: {exc}') from exc
        if roof_class(pitches.get(no, 0.5), 1) != 'pitched':
            raise SystemExit(f'{path}: body[{no!r}] needs a roof_pitch above 0.5 (a pitched body)')
    chimneys = spec.get('chimneys', {})
    if not isinstance(chimneys, dict):
        raise SystemExit(f'{path}: chimneys must be a label-to-option object')
    for no, opt in chimneys.items():
        if no not in labels or no not in roofs:
            raise SystemExit(f'{path}: chimneys label {no!r} needs a way and a roof')
        _validate_chimney_option(path, no, opt)
    for g in spec['groups']:
        if g['front'] not in DIRS or g.get('mode', 'each') not in ('each', 'group'):
            raise SystemExit(f'{path}: bad group {g}')
        g.setdefault('mode', 'each')
        if g['mode'] == 'group' and any(no in bodies for no in g['nos']):
            raise SystemExit(f'{path}: body needs mode "each": each body keeps its own roof')
        if g['mode'] == 'group' and any(fits.get(no) == 'octant' for no in g['nos']):
            raise SystemExit(f'{path}: roof_fit octant needs mode "each", not a group roof')
        if g['mode'] == 'group' and len({pitches.get(no, 0.5) for no in g['nos']}) > 1:
            raise SystemExit(f'{path}: group roof labels must have equal roof_pitch values')
        if g['mode'] == 'group':
            group_chim = [chimneys[no] for no in g['nos'] if no in chimneys]
            if len(group_chim) > 1 and any(opt != group_chim[0] for opt in group_chim[1:]):
                raise SystemExit(f'{path}: group chimney options must match')
        for no in g['nos']:
            if no not in labels or no not in spec['roofs']:
                raise SystemExit(f'{path}: label {no} needs a way and a roof')
    return spec


class House:
    def __init__(self, no, cells, group, recipe=None):
        self.no, self.cells, self.group = no, cells, group
        self.front = group['front']
        # A body block makes this a terrace-house body (wall storeys, pitched roof) with an optional one-storey front strip.
        self.recipe = recipe
        self.storeys = recipe.get('storeys', 2) if recipe else 2
        self.strip = (terrace_body.front_strip(cells, self.front, recipe.get('front_depth', 0))
                      if recipe else set())
        self.body = cells - self.strip
        # body columns above the strip: the upper front wall the street sees over the front strip roof
        self.upper_row = terrace_body.front_row(self.body, self.front) if self.strip else set()


class Build:
    def __init__(self, spec):
        self.spec = spec
        self.name = spec['name']
        self.groups = spec['groups']
        self.keep = set()
        self.p = Plan(self.name)
        self.live = fresh_planning_world()
        idx = osm_footprints.OSMIndex()
        by_no = {}
        for (x, z), i in idx.cells().items():
            bp = idx.buildings[i][1]
            n = self.spec['ways'].get(str(bp.get('osm_way_id') or bp.get('osm_id')))
            if n:
                by_no.setdefault(n, set()).add((x, z))
        self.idx = idx
        self.houses = []
        for g in self.groups:
            for no in g['nos']:
                self.houses.append(House(no, by_no[no], g, self.spec.get('body', {}).get(no)))
        self.owner = {}
        for k, h in enumerate(self.houses):
            for c in h.cells:
                self.owner[c] = k
        self.by_no_all = by_no
        # Stamp against footprint cells so unrelated neighbouring prepares do not
        # invalidate this plan while cross-cell geometry still counts (task 232).
        import cellmath
        self.p.capture_inputs({cellmath.cell_of(x, z) for cols in by_no.values() for x, z in cols})

    def set(self, x, y, z, s):
        self.p.set(x, y, z, s)

    def stoop_set(self, x, y, z, st):
        """Set a door stoop cell that always survives the earlier-build clash filter (#243).

        The column outside a door must be level with the floor. Without keep, earlier ground at
        floor+1 owns the cell, the plan drops the stoop's air, and the door ends up one block
        below street level.
        """
        self.keep.add((x, y, z))
        self.set(x, y, z, st)

    def kset(self, x, y, z, st):
        """Set a stoop/step cell; with keep_edits it survives the earlier-build clash filter."""
        if self.spec['keep_edits']:
            self.keep.add((x, y, z))
        self.set(x, y, z, st)

    def floor_y(self, h):
        """Reference the footprint's lidar terrain, with the spec's floor_min override."""
        from floor_reference import floor_reference
        return floor_reference(self.name, h.no, h.cells, self.spec['floor_min'])

    def clear(self):
        cols = set()
        for (x, z) in self.owner:
            cols.update((x + dx, z + dz) for dx in (-2, -1, 0, 1, 2) for dz in (-2, -1, 0, 1, 2))
        self.cols = cols
        for x, z in cols:
            near = min(self.houses, key=lambda h: min(max(abs(x - c[0]), abs(z - c[1])) for c in h.cells))
            b = near.b
            height = max(TOP, near.storeys * STOREY + 14)
            if (x, z) in self.foreign or (x, z) in self.shared:
                continue
            own = (x, z) in self.owner
            for y in range(place_config.base_y(), b):
                self.set(x, y, z, 'stone')
            if own:
                top = b + height
                rng = range(b, top + 1)
            else:
                # Margin: clear only, no stone shelf at floor level (#115); door stoops come from step_out.
                rng = range(b + 1, b + height + 1)
            for y in rng:
                if own or (self.live.block(x, y, z) or 'minecraft:air').split('[')[0] not in GROUND:
                    self.set(x, y, z, 'air')

    def exposed(self, c, d):
        dx, dz = DIRS[d]
        return (c[0] + dx, c[1] + dz) not in self.owner

    def shell(self, k, h):
        b = h.b
        own = h.cells
        h.walls = set()
        for c in own:
            for dx, dz in N8:
                n = (c[0] + dx, c[1] + dz)
                ko = self.owner.get(n)
                if ko is None or (ko != k and k < ko):
                    h.walls.add(c)
                    break
        h.inner = own - h.walls
        for x, z in own:
            for y in range(b - 4, b):
                self.set(x, y, z, 'stone')
        top = b + STOREY * h.storeys
        for x, z in h.walls:
            # a front strip's walls stop under its flat roof; the body's rise to the eave
            for y in range(b, top if (x, z) in h.body else b + STOREY):
                self.set(x, y, z, WALL)
        for x, z in h.inner:
            self.set(x, b, z, FLOOR)
            storeys = 1 if (x, z) in h.strip else h.storeys
            for y in range(b + 1, b + STOREY * storeys):
                self.set(x, y, z, 'air')
            for s in range(1, storeys):
                self.set(x, b + STOREY * s, z, FLOOR)
        for x, z in h.upper_row:
            # upper front wall over the strip roof; the ground storey stays open to the strip
            for y in range(b + STOREY, top):
                self.set(x, y, z, WALL)

    def with_jamb_walls(self, h, cand, d):
        """Door candidates with a wall cell on both sides along the wall, exposed the same way.

        On a diagonal (stair-stepped) wall a door in a step corner has air beside it and lacks jambs.
        spec door_frame false restores the old pick (kept only to prove the generator against old scripts).
        Second choice: both neighbours are wall cells but one is set forward (a notch). Last: ``cand`` unchanged.

        @param h: house mass
        @param cand: candidate wall cells
        @param d: exposure direction
        @returns: filtered candidate list
        """
        if not self.spec['door_frame']:
            return cand
        lat = ((0, 1), (0, -1)) if d in 'EW' else ((1, 0), (-1, 0))
        strict = [c for c in cand
                  if all((c[0] + lx, c[1] + lz) in h.walls and self.exposed((c[0] + lx, c[1] + lz), d) for lx, lz in lat)]
        if strict:
            return strict
        # a notch (neighbour wall set forward of the door) still frames it; air beside the door does not
        return [c for c in cand if all((c[0] + lx, c[1] + lz) in h.walls for lx, lz in lat)] or cand

    def doors(self, k, h, taken):
        b = h.b
        f = h.front
        r = OPP[f]
        fx, fz = DIRS[f]
        # front door cell: wall cell exposed to the front, nearest the middle of the
        # front-most run of the house (per unit for the block group)
        cand = [c for c in h.walls if self.exposed(c, f) and (c[0] - fx, c[1] - fz) in h.inner]
        if not cand:
            return
        cand = self.with_jamb_walls(h, cand, f)
        if f == 'N':
            front_edge = min(c[1] for c in cand)
            cand = [c for c in cand if c[1] <= front_edge + 2]
            mid = sum(c[0] for c in cand) / len(cand)
            c0 = min(cand, key=lambda c: abs(c[0] - mid))
        elif f == 'S':
            front_edge = max(c[1] for c in cand)
            cand = [c for c in cand if c[1] >= front_edge - 2]
            mid = sum(c[0] for c in cand) / len(cand)
            c0 = min(cand, key=lambda c: abs(c[0] - mid))
        else:
            front_edge = min(c[0] for c in cand)
            cand = [c for c in cand if c[0] <= front_edge + 1]
            mid = sum(c[1] for c in cand) / len(cand)
            c0 = min(cand, key=lambda c: abs(c[1] - mid))
        h.door = c0
        st = (c0[0] + fx, c0[1] + fz)
        bd = self.door_level(st, b)
        for y, p in ((bd + 1, 'lower'), (bd + 2, 'upper')):
            self.stoop_set(c0[0], y, c0[1], f"spruce_door[facing={ {'S':'south','N':'north','W':'west','E':'east'}[f] },half={p},hinge=left,open=false]")
        self.stoop_set(st[0], bd, st[1], 'smooth_stone')
        for y in range(bd + 1, bd + 4):
            self.stoop_set(st[0], y, st[1], 'air')
        self.step_out(st, f, bd)
        self.raise_inner(c0, f, b, bd)
        # stair inward from the door, ascending away from the front
        ix, iz = -fx, -fz
        cells = [(c0[0] + ix * d, c0[1] + iz * d) for d in (2, 3, 4)]
        if h.storeys > 1 and all(c in h.inner and c in h.body for c in cells):
            for i, c in enumerate(cells):
                self.set(c[0], b + 1 + i, c[1], stair('spruce', TOWARD[f]))
            for c in cells[:2]:
                self.set(c[0], b + 3, c[1], 'air')
        # rear door
        cand = [c for c in h.walls if self.exposed(c, r) and (c[0] - DIRS[r][0], c[1] - DIRS[r][1]) in h.inner]
        if cand:
            cand = self.with_jamb_walls(h, cand, r)
            if r in 'NS':
                e = min(c[1] for c in cand) if r == 'N' else max(c[1] for c in cand)
                cc = [c for c in cand if (c[1] <= e + 2 if r == 'N' else c[1] >= e - 2)]
                m = sum(c[0] for c in cc) / len(cc)
                c1 = min(cc, key=lambda c: abs(c[0] - m))
            else:
                e = max(c[0] for c in cand)
                cc = [c for c in cand if c[0] >= e - 1]
                m = sum(c[1] for c in cc) / len(cc)
                c1 = min(cc, key=lambda c: abs(c[1] - m))
            h.rear = c1
            rf = {'N': 'north', 'E': 'east', 'S': 'south', 'W': 'west'}[r]
            sx, sz = c1[0] + DIRS[r][0], c1[1] + DIRS[r][1]
            bd = self.door_level((sx, sz), b)
            for y, p in ((bd + 1, 'lower'), (bd + 2, 'upper')):
                self.stoop_set(c1[0], y, c1[1], f'spruce_door[facing={rf},half={p},hinge=right,open=false]')
            self.stoop_set(sx, bd, sz, 'smooth_stone')
            for y in range(bd + 1, bd + 4):
                self.stoop_set(sx, y, sz, 'air')
            self.step_out((sx, sz), r, bd)
            self.raise_inner(c1, r, b, bd)
        else:
            h.rear = None

    def door_level(self, st, b):
        """Floor level the door sits at: one above the house floor when the ground outside it stands there (#243).

        Raising the door (not lowering the street) keeps the lawn and pavement as they are; the
        step up inside is made by raise_inner.
        """
        return b + 1 if self.top_solid(st[0], st[1], b) == b + 1 else b

    def raise_inner(self, door, d, b, bd):
        """Keep the cell just inside a door clear (earlier paving would block it); raised doors get a stair up.

        A door raised one block above the floor needs a stair inside so the step is walkable, with headroom.
        """
        dx, dz = DIRS[d]
        x, z = door[0] - dx, door[1] - dz
        if bd > b:
            self.stoop_set(x, b + 1, z, stair('spruce', FACE[d]))
            lo = b + 2
        else:
            lo = b + 1
        for y in range(lo, b + 4):
            self.stoop_set(x, y, z, 'air')

    def top_solid(self, x, z, near):
        return stoop.top_solid(self.live, x, z, near)

    def step_out(self, st, d, b):
        stoop.step_out(self, st, d, b, self.stoop_set, log=print)

    def windows(self, k, h):
        """Apply the shared bounded-opening policy to the house's pier rhythm."""
        from floor_reference import outside_ground
        from window_openings import Window, choose_windows, report_lines
        del k
        door = {q for q in (getattr(h, 'door', None), getattr(h, 'rear', None)) if q}
        # a body wall above the front strip roof is not a shell wall but is exposed to the front
        walls = h.walls | h.upper_row

        def exposed(c, d):
            return self.exposed(c, d) or (c in h.upper_row and d == h.front)

        rows = tuple(STOREY * s + 2 for s in range(h.storeys))
        candidates = []
        for face in ('N', 'S', 'E', 'W'):
            cols = [c for c in walls if exposed(c, face)]
            edge = 1 if face in 'NS' else 0
            stepped = len({c[edge] for c in cols}) > 1
            for c in sorted(cols):
                if any(abs(c[0] - q[0]) + abs(c[1] - q[1]) < 3 for q in door):
                    continue
                if (c[0] if face in 'NS' else c[1]) % 2:
                    continue
                for dy in rows:
                    if c in h.strip and dy >= STOREY:
                        continue   # the front strip's walls stop under its flat roof: no wall to glaze above it
                    candidates.append(Window((c[0], h.b + dy, c[1]), face, stepped))
        positions = {(w.pos[0] + DIRS[w.face][0], w.pos[2] + DIRS[w.face][1])
                     for w in candidates}
        ground = outside_ground(positions)
        def block(x, y, z):
            return self.p.cells.get((x, y, z), 'minecraft:air')
        writes, report = choose_windows(candidates, block=block, walls=walls,
            exposed=exposed, outside_ground=ground)
        for pos, state in writes.items():
            self.set(*pos, state)
        for line in report_lines(h.no, report):
            print(line)

    def body_details(self, h):
        """Front-range roof, upper bay window and roof dormers of a terrace-house body (#333)."""
        from terrace_body import centres, lateral_runs
        b, front = h.b, h.front
        rm = self.spec['roofs'][h.no]
        variants = family_variants(rm)
        deck = f'{variants["slab"]}[type=bottom]' if 'slab' in variants else variants['full']
        for x, z in h.strip:
            self.set(x, b + STOREY, z, deck)
            if 'slab' in variants:
                self.p.keep_slab_cells.add((x, b + STOREY, z))
        runs = lateral_runs(terrace_body.front_row(h.body, front), front)
        if h.recipe.get('bay') and runs:
            for centre in centres(runs[0], 1):
                self.bay_window(h, centre, h.recipe.get('bay_storey', 1))
        if h.recipe.get('dormers') and runs:
            for centre in centres(runs[0], h.recipe['dormers']):
                self.dormer(h, centre, variants['full'])

    def skin_y(self, x, z, low):
        """Highest planned block in a column at or above ``low`` (the roof skin), or None."""
        ys = [y for (xx, y, zz), st in self.p.cells.items()
              if xx == x and zz == z and y >= low and not st.endswith('air')]
        return max(ys) if ys else None

    def bay_window(self, h, centre, storey):
        """A square glass bay one block proud of the upper front wall, on a solid base.

        The base is a full block (the strip's flat roof carries it, or it corbels out over the
        pavement), the glass is two blocks high, and the roof above comes from the eave. The
        wall behind the centre column is opened so the room looks out through the bay.
        """
        from terrace_body import bay_cells
        cells = bay_cells(centre, h.front)
        sill = h.b + STOREY * storey
        outside = cells['outside']
        if any((c in self.foreign or c in self.shared or (self.owner.get(c) not in (None, self.owner.get(centre))))
               for c in outside):
            print(f'{h.no} bay window: columns in front belong to a neighbour; dropped')
            return
        for c in outside:
            self.set(c[0], sill, c[1], WALL)
            self.set(c[0], sill + 1, c[1], 'glass')
            self.set(c[0], sill + 2, c[1], 'glass')
        cap = family_variants(self.spec['roofs'][h.no])['full']
        for c in outside:
            if self.skin_y(c[0], c[1], sill + 3) is None:
                self.set(c[0], sill + 3, c[1], cap)
        self.set(centre[0], sill + 1, centre[1], 'air')
        self.set(centre[0], sill + 2, centre[1], 'air')
        print(f'{h.no} bay window at {centre}, storey {storey}')

    def dormer(self, h, centre, cap):
        """A flush wall dormer on the front slope: sill, two panes between piers, a flat cap.

        It sits on the front wall row where the roof starts, and is dropped when its cap would stand
        above the ridge (the roof has no room for a top floor there).
        """
        from terrace_body import dormer_cells
        cells = dormer_cells(centre, h.front)
        top = h.b + STOREY * h.storeys
        base = self.skin_y(centre[0], centre[1], top - 2)
        ridge = max((self.skin_y(x, z, top - 2) or 0) for x, z in h.body)
        if base is None or base + 3 > ridge:
            print(f'{h.no} dormer at {centre}: no room under the ridge; dropped')
            return
        axis = 'east=true,west=true' if h.front in 'NS' else 'north=true,south=true'
        for i, c in enumerate(cells['front']):
            self.set(c[0], base, c[1], WALL)
            for dy in (1, 2):
                self.set(c[0], base + dy, c[1], f'glass_pane[{axis}]' if i == 1 else WALL)
            self.set(c[0], base + 3, c[1], cap)
        for c in cells['back']:
            skin = self.skin_y(c[0], c[1], top - 2) or base
            for y in range(skin + 1, base + 3):
                self.set(c[0], y, c[1], WALL)
            if base + 3 > skin:
                self.set(c[0], base + 3, c[1], cap)
        print(f'{h.no} dormer at {centre}, sill y {base}')

    @classmethod
    def _ridge_jogs(cls, half_heights, states):
        """Ridge jogs the roof skin would have, judged on its own cells (membrane plus underlay)."""
        cells = {}
        for (x, z), state in states.items():
            y = roof_block_y(half_heights[x, z])
            cells[x, y, z] = state
            cells[x, y - 1, z] = 'minecraft:bricks'
        return cls._ridge_jogs_of(cells)

    @staticmethod
    def _ridge_jogs_of(cells):
        """Ridge jogs in a finished ``{(x, y, z): state}`` roof volume."""
        import roof_lint
        return roof_lint.ridge_jogs(cells)

    def _chimney_option(self, hs):
        """Resolved chimneys value for a roof unit, or None for the legacy default.

        @param hs: houses under the roof unit
        @returns: mode string, explicit at-list, or None
        """
        opts = self.spec.get('chimneys', {})
        for h in hs:
            if h.no in opts:
                return opts[h.no]
        return None

    def _through_chimney(self, x, z, b, ridge):
        """One-by-one brick stack through the roof: WALL to ridge+2, flower_pot cap.

        @param x: stack column X
        @param z: stack column Z
        @param b: house floor y (eave is b+6)
        @param ridge: highest roof block y beside the stack
        """
        for y in range(b + 6, ridge + 2):
            self.set(x, y, z, WALL)
        self.set(x, ridge + 2, z, 'flower_pot')

    def roof(self):
        """Plan shared or individual roof skins, solid underlays and through-chimneys."""
        done = {}
        todo = []
        for g in self.groups:
            hs = [h for h in self.houses if h.group is g]
            if g['mode'] == 'group':
                todo.append((hs, set().union(*[h.cells for h in hs])))
            else:
                todo.extend(([h], h.body) for h in hs)
        chim = []
        for hs, own in todo:
            dist = edge_distance_field(own, set(self.owner) | self.foreign | self.shared)
            # b + 6 is the eave everywhere below: shift b for a body of more or fewer than two storeys
            b = hs[0].b + STOREY * (hs[0].storeys - 2)
            rm = self.spec['roofs'][hs[0].no]
            pitch = self.spec.get('roof_pitch', {}).get(hs[0].no, 0.5)
            pitched = roof_class(pitch, 1) == 'pitched'
            resolved = roof_blocks(rm, 'pitched' if pitched else 'flatish')
            if not resolved['ok']:
                raise SystemExit('; '.join(resolved['messages']))
            variants = family_variants(rm)
            chimney_opt = self._chimney_option(hs)
            rotated = None
            if pitched and self.spec.get('roof_fit', {}).get(hs[0].no) == 'octant':
                # Design the roof as a half-block-cell volume and fit blocks to it (#415).
                depth = rotated_hip_depth(own, set(dist)) or dist
                sites = chimney_sites(chimney_opt, hs, own, dist, depth)
                fitted = fit_octant_roof(own, set(dist), pitch, b + 6, set(sites))
                fitted_states = block_states(rm, fitted.blocks) if fitted is not None else None
                if fitted is None:
                    print(f'{hs[0].no} roof_fit octant: footprint is not close to a rectangle; using columns')
                elif self._ridge_jogs_of(fitted_states):
                    print(f'{hs[0].no} roof_fit octant: the fitted ridge would jog; using columns')
                else:
                    print(f'{hs[0].no} roof_fit octant: rectangle at {fitted.angle} degrees, '
                          f'{fitted.missed} of {fitted.wanted} half-block cells missed'
                          + ('' if fitted.settled else ' (facing search stopped at its pass limit)'))
                    for pos, state in fitted_states.items():
                        self.set(*pos, state)
                        if 'slab' in state and 'type=bottom' in state:
                            self.p.keep_slab_cells.add(pos)
                    for chimney in sites:
                        ridge = max(y for (x, y, z) in fitted.blocks
                                    if abs(x - chimney[0]) <= 1 and abs(z - chimney[1]) <= 1)
                        self._through_chimney(chimney[0], chimney[1], b, ridge)
                    continue
            if pitched:
                # The outermost eave cell starts half a block below the wall-top level so it is a
                # bottom slab (shallow pitch) or a stair (steep pitch), as on the flatish ring; an
                # even start made it a full block (#414).
                # A footprint drawn square to an angled road is a rotated rectangle: take heights from
                # its fitted axes so the ridge and hips are straight (#415). The eave ring keeps the
                # start height (#414), and a rotated roof that would jog its ridge falls back to the
                # grid field, so this never refuses a roof the grid field would plan.
                materials = dict.fromkeys(dist, rm)
                rotated = rotated_hip_depth(own, set(dist))
                if rotated is not None:
                    half_heights = {c: 2 * (b + 6) - 1 + (0 if dist[c] == 0 else math.floor(2 * d * pitch + 1e-9))
                                    for c, d in rotated.items()}
                    states = pitched_roof_states(half_heights, materials)
                    if self._ridge_jogs(half_heights, states):
                        rotated = None
                if rotated is None:
                    half_heights = {c: 2 * (b + 6) - 1 + math.floor(2 * d * pitch)
                                    for c, d in dist.items()}
                    states = pitched_roof_states(half_heights, materials)
            for c, d in dist.items():
                if pitched:
                    y = roof_block_y(half_heights[c])
                    state = states[c]
                    key = (y, 0 if 'type=bottom' in state else 1)
                else:
                    y = b + 6 + d // 2
                    t = 'bottom' if d % 2 == 0 else 'top'
                    key = (y, 1 if t == 'top' else 0)
                    state = (f'{variants["slab"]}[type={t}]' if 'slab' in variants
                             else variants['full'])
                cur = done.get(c)
                if cur is None or key > cur[0]:
                    done[c] = (key, b, c in self.owner, rm, state, pitched, dist[c] == 0)
            depth = rotated if pitched and rotated is not None else dist
            for site in chimney_sites(chimney_opt, hs, own, dist, depth):
                chim.append((site, b))
        # Chimneys replace roof stairs, so choose corner states against the final skin.
        chimney_cells = {c for c, _b in chim}
        facings = {c: state.split('facing=', 1)[1].split(',', 1)[0]
                   for c, (_key, _b, _inside, _rm, state, _pitched, _eave) in done.items()
                   if '_stairs[' in state and c not in chimney_cells}
        for (x, z), (key, b, inside, rm, state, pitched, eave) in done.items():
            y = key[0]
            if inside:
                for yy in range(b + 6, y):
                    self.set(x, yy, z, WALL)
            if pitched:
                # A two-layer skin seals the stair cutouts; the overhanging eave ring stays one
                # slab or stair thick, with nothing under it (#414). A membrane at the eave
                # level (b+6) would put that full block in the wall's top course (b+5). The
                # wall block already seals the stair, so leave it (#429). Any other column
                # at that height (no wall there) still gets the roof underlay.
                if not eave and (y - 1 != b + 5 or self.p.cells.get((x, y - 1, z)) != full(WALL)):
                    self.set(x, y - 1, z, family_variants(rm)['full'])
                if (x, z) in facings:
                    adjacent = {FACE[face]: (facings[n], 'bottom')
                                if n in facings and done[n][0][0] == y else None
                                for face, (dx, dz) in DIRS.items()
                                for n in [(x + dx, z + dz)]}
                    shape = stair_shape_from_neighbours(facings[(x, z)], 'bottom', adjacent)
                    state = stair(rm, facings[(x, z)], shape=shape)
            self.set(x, y, z, state)
            if pitched and 'type=bottom' in state:
                self.p.keep_slab_cells.add((x, y, z))
        for (x, z), b in chim:
            ridge = max(k[0][0] for (xx, zz), k in done.items() if abs(xx - x) <= 1 and abs(zz - z) <= 1)
            self._through_chimney(x, z, b, ridge)

    def build(self):
        self.foreign = {c for c in self.idx.cells() if c not in self.owner}
        # columns next to neighbouring buildings: their roof overhang is already there
        self.shared = {(x + dx, z + dz) for (x, z) in self.foreign for dx, dz in N8} - self.foreign - set(self.owner)
        for h in self.houses:
            h.b = self.floor_y(h)
        # a group shares one floor level
        for g in self.groups:
            hs = [h for h in self.houses if h.group is g]
            if g['mode'] == 'group':
                bb = min(h.b for h in hs)
                for h in hs:
                    h.b = bb
        self.clear()
        for k, h in enumerate(self.houses):
            self.shell(k, h)
        for k, h in enumerate(self.houses):
            self.doors(k, h, set())
        self.roof()
        for k, h in enumerate(self.houses):
            self.windows(k, h)
        for h in self.houses:
            if h.recipe:
                self.body_details(h)
        for q, v in osm_footprints.guard(self.p.cells)['outside'].items():
            if 'slab' in v[0]:
                del self.p.cells[q]
        own1 = {(x + dx, z + dz) for (x, z) in self.owner for dx in (-1, 0, 1) for dz in (-1, 0, 1)}
        for q in list(self.p.cells):
            if (q[0], q[2]) not in own1 and 'leaves' in (self.live.block(*q) or ''):
                del self.p.cells[q]
        self.p.outside_footprint_ok = 'roof-overhang fringe; evidence: footprint-hits json'
        from site_kit import interior_ground_clears
        self.keep.update(interior_ground_clears(
            self.p, {c: h.b for h in self.houses for c in h.inner}))
        self.p.keep = set(self.keep)   # #243: stoop cells may overwrite earlier ground at apply, as in feature_build
        # roof-overhang slabs of a neighbouring earlier build that spill onto our corner are theirs: leave them
        from cell_registry import view_registry
        owned = view_registry().owned(self.p.cells, exclude={self.name}, real_only=True)
        for q in [q for q, st in self.p.cells.items() if q in owned and owned[q][1] != st and q not in self.keep]:
            del self.p.cells[q]
        # Every stair, roof or step, takes the shape the game gives it from the stairs beside it in
        # the plan and in the world, whatever their material (#415).
        aligned = align_stair_shapes(self.p.cells, self.live.block, getattr(self.live, 'prefetch', None))
        for pos, state in aligned.items():
            if self.p.cells[pos] != state:
                self.p.cells[pos] = state
        for line in stair_effect_lines(self.p.cells, self.live.block):
            print(line)
        # same refusals as the live apply, so the designer sees them before hand-off (#99)
        guard_door_frames(self.p, self.live.block)
        guard_roof_jogs(self.p)
        for h in self.houses:
            print(h.no, 'b', h.b, 'cells', len(h.cells), 'door', getattr(h, 'door', None), 'rear', getattr(h, 'rear', None))
        # Re-stamp from the finished geometry so roof overhangs into neighbours are
        # part of the plan identity apply will check (task 232).
        self.p.capture_inputs()
        return self.p



def build(spec):
    return Build(spec).build()


def run(spec_path, do_apply=False, overwrite=False):
    """Preview or apply a world-relative spec; replacing a live build requires an override."""
    from builder_provenance import HOUSE_BUILDER, resolve_builder_path, provenance
    from cell_registry import view_registry
    spec_path = resolve_builder_path(spec_path)
    spec = load_spec(spec_path)
    if do_apply and not overwrite:
        seqs = [e.seq for e in view_registry().entries(plan=spec['name'])
                if e.kind == 'build' and e.status == 'landed' and not e.undone]
        if seqs:
            raise SystemExit(
                f'{spec["name"]}: already built (journal seq {seqs[-1]}). A re-apply writes only '
                'the new plan, so old blocks outside it survive. Undo first: '
                f'element_undo.py --world <world> {spec["name"]} --apply; '
                'or pass --overwrite to apply over it anyway.')
    if do_apply:
        from readiness_evidence import require_feature_ready
        require_feature_ready(spec['name'])
    plan = build(spec)
    out = ROOT / f'verification/site/{spec["name"]}-preview.png'
    preview(plan, out, scale=4)
    print(json.dumps(plan.summary())[:600])
    result = apply(plan, dry=not do_apply, modifies=tuple(spec['modifies']), strict=True,
                   meta=provenance(HOUSE_BUILDER, spec_path))
    if result is not None:
        print(f'readback: {result["planned"]} planned, {result["mismatches"]} mismatches')
    return plan


def main(argv=None):
    """Run the house builder CLI (world activation belongs to the scripts wrapper)."""
    ap = argparse.ArgumentParser()
    ap.add_argument('spec', help='site/builds/<name>.json')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--overwrite', action='store_true',
                    help='apply over an existing build (leaves old blocks outside the new plan)')
    a = ap.parse_args(argv)
    run(a.spec, do_apply=a.apply, overwrite=a.overwrite)


if __name__ == '__main__':
    main()
