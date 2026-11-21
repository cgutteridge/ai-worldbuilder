#!/usr/bin/env python3
"""Generic vector-map footprint house builder (house only: walls, floors, doors, windows, slab roofs).

One module for every "house only" run (layered pass 1: Granby Grove, Merton Rd, Ripstone and Blenheim Gardens
q165..q176 and later). A run is a small JSON spec in site/builds/<name>.json instead of a 390-line script copy:

  name           plan name (also the cell journal plan / feature id)
  notes          provenance: map ways, aerial/streetlevel evidence, date, queue item (free text, never read by code)
  modifies       earlier builds this one may overwrite (default [])
  ways           {"<map way id>": "<label>"} the footprints to build
  roofs          {"<label>": "<slab material>"} e.g. brick, mud_brick, deepslate_tile, cobbled_deepslate
  groups         [{"nos": [labels], "mode": "each"|"group", "front": "N"|"S"|"E"|"W"}]
                 "group" = one roof over several houses sharing a floor level
  floor_min      minimum floor y (front pavement height) or null
  step_max       how far a door stoop looks outward for ground to level or step (default 4; 13 on long ledges)
  ledge_stair    true: ground that ends at a 1-block ledge beyond 4 columns gets a rising stair (default false)
  door_frame     true (default): doors only where the wall has a wall cell each side, so they are framed on angled walls
  keep_edits     true: stoop/step cells survive the earlier-build clash filter (needed when `modifies` a ground build)

Usage: python3 scripts/house_build.py --world <world> site/builds/<name>.json [--apply [--overwrite]]
Direct apply refuses an existing landed build unless --overwrite is explicit; undo first to remove old blocks.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from script_env import ROOT  # noqa: E402
import osm_footprints  # noqa: E402
from site_kit import Plan, apply, fresh_planning_world, ground_scan_range, guard_door_frames, guard_roof_jogs, preview  # noqa: E402

# Same list as feature_build.GROUND, incl. the lidar ghost tops (task 286).
GROUND = ('minecraft:stone', 'minecraft:gray_terracotta', 'minecraft:polished_granite', 'minecraft:polished_diorite', 'minecraft:light_gray_concrete', 'minecraft:grass_block', 'minecraft:dirt', 'minecraft:coarse_dirt', 'minecraft:gravel', 'minecraft:andesite',
          'minecraft:gray_concrete', 'minecraft:black_concrete')
WALL, FLOOR = 'bricks', 'spruce_planks'
TOP = 20
DIRS = {'N': (0, -1), 'S': (0, 1), 'W': (-1, 0), 'E': (1, 0)}
from entrance_approach import entrance_approach, entrances_from_blocks  # noqa: E402,F401  (read-only; #240)
OPP = {'N': 'S', 'S': 'N', 'W': 'E', 'E': 'W'}
N8 = [(dx, dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1) if dx or dz]
FACE = {'S': 'south', 'N': 'north', 'W': 'west', 'E': 'east'}
TOWARD = {'S': 'north', 'N': 'south', 'W': 'east', 'E': 'west'}       # facing of a stair that rises toward the house


def stair(mat, facing, half='bottom'):
    return f'{mat}_stairs[facing={facing},half={half}]'


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
    for g in spec['groups']:
        if g['front'] not in DIRS or g.get('mode', 'each') not in ('each', 'group'):
            raise SystemExit(f'{path}: bad group {g}')
        g.setdefault('mode', 'each')
        for no in g['nos']:
            if no not in labels or no not in spec['roofs']:
                raise SystemExit(f'{path}: label {no} needs a way and a roof')
    return spec


class House:
    def __init__(self, no, cells, group):
        self.no, self.cells, self.group = no, cells, group
        self.front = group['front']


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
                self.houses.append(House(no, by_no[no], g))
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
            b = min(self.houses, key=lambda h: min(max(abs(x - c[0]), abs(z - c[1])) for c in h.cells)).b
            if (x, z) in self.foreign or (x, z) in self.shared:
                continue
            own = (x, z) in self.owner
            for y in range(37, b):
                self.set(x, y, z, 'stone')
            if own:
                top = b + TOP
                rng = range(b, top + 1)
            else:
                if (self.live.block(x, b, z) or 'minecraft:air') == 'minecraft:air':
                    self.set(x, b, z, 'stone')
                rng = range(b + 1, b + TOP + 1)
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
        for x, z in h.walls:
            for y in range(b, b + 6):
                self.set(x, y, z, WALL)
        for x, z in h.inner:
            self.set(x, b, z, FLOOR)
            for y in range(b + 1, b + 6):
                self.set(x, y, z, 'air')
            self.set(x, b + 3, z, FLOOR)

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
        if all(c in h.inner for c in cells):
            for i, c in enumerate(cells):
                self.set(c[0], b + 1 + i, c[1], stair('spruce', {'S': 'north', 'N': 'south', 'W': 'east', 'E': 'west'}[f]))
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
            self.stoop_set(x, b + 1, z, stair('spruce', {'S': 'south', 'N': 'north', 'W': 'west', 'E': 'east'}[d]))
            lo = b + 2
        else:
            lo = b + 1
        for y in range(lo, b + 4):
            self.stoop_set(x, y, z, 'air')

    def top_solid(self, x, z, near):
        # natural ground only (ignores roof fringe slabs and earlier steps of this build)
        for y in ground_scan_range(near):
            n = (self.live.block(x, y, z) or 'minecraft:air').split('[')[0]
            if n in GROUND and n != 'minecraft:stone_stairs':
                return y
        return None

    def step_out(self, st, d, b):
        # walk outward from the stoop to the road: ground ABOVE the floor in the way (old import ledge) is
        # lowered to the floor level; at the first column BELOW the floor a stair rises toward the house
        # (a 1-block hop is not walkable in site_walk)
        dx, dz = DIRS[d]
        facing = TOWARD[d]
        for k in range(1, self.spec['step_max'] + 1):
            n = (st[0] + dx * k, st[1] + dz * k)
            if n in self.owner or n in self.foreign:
                continue
            g = self.top_solid(*n, b)
            if g is None:
                continue
            if self.spec['ledge_stair'] and g == b + 1 and k > 4:
                # lowered ground ends at a 1-block ledge: stair rising away from the house onto it
                self.stoop_set(n[0], g, n[1], stair('stone', FACE[d]))
                print('ledge stair at', n)
                return
            if g > b + 1 or (g > b and k > 4):
                return  # real slope/terrace: leave it
            if g > b:
                self.stoop_set(n[0], b, n[1], 'stone')
                for y in range(b + 1, g + 1):
                    self.stoop_set(n[0], y, n[1], 'air')
                continue
            if g < b:
                self.stoop_set(n[0], b, n[1], stair('stone', facing))
                for y in range(g + 1, b):
                    self.stoop_set(n[0], y, n[1], 'stone')
                for y in range(b + 1, b + 4):
                    self.stoop_set(n[0], y, n[1], 'air')
                print('step at', n, 'ground', g, 'floor', b)
                return

    def windows(self, k, h):
        """Apply the shared bounded-opening policy to the house's pier rhythm."""
        from floor_reference import outside_ground
        from window_openings import Window, DIRECTIONS, choose_windows, report_lines
        del k
        door = {q for q in (getattr(h, 'door', None), getattr(h, 'rear', None)) if q}
        candidates = []
        for face in DIRECTIONS:
            cols = [c for c in h.walls if self.exposed(c, face)]
            edge = 1 if face in 'NS' else 0
            stepped = len({c[edge] for c in cols}) > 1
            for c in sorted(cols):
                if any(abs(c[0] - q[0]) + abs(c[1] - q[1]) < 3 for q in door):
                    continue
                if (c[0] if face in 'NS' else c[1]) % 2:
                    continue
                for dy in (2, 5):
                    candidates.append(Window((c[0], h.b + dy, c[1]), face, stepped))
        positions = {(w.pos[0] + DIRECTIONS[w.face][0], w.pos[2] + DIRECTIONS[w.face][1])
                     for w in candidates}
        ground = outside_ground(positions)
        def block(x, y, z):
            return self.p.cells.get((x, y, z), 'minecraft:air')
        writes, report = choose_windows(candidates, block=block, walls=h.walls,
            exposed=self.exposed, outside_ground=ground)
        for pos, state in writes.items():
            self.set(*pos, state)
        for line in report_lines(h.no, report):
            print(line)

    def roof(self):
        done = {}
        seen = set()
        todo = []
        for g in self.groups:
            hs = [h for h in self.houses if h.group is g]
            if g['mode'] == 'group':
                todo.append((hs, set().union(*[h.cells for h in hs])))
            else:
                todo.extend(([h], h.cells) for h in hs)
        chim = []
        for hs, own in todo:
            rs = set(own)
            for (x, z) in own:
                for dx, dz in N8:
                    n = (x + dx, z + dz)
                    if n not in self.owner and n not in self.foreign and n not in self.shared:
                        rs.add(n)
            # chebyshev distance to outside of rs
            dist = {}
            front = [c for c in rs if any((c[0] + dx, c[1] + dz) not in rs for dx, dz in N8)]
            for c in front:
                dist[c] = 0
            q = list(front)
            while q:
                nq = []
                for c in q:
                    for dx, dz in N8:
                        n = (c[0] + dx, c[1] + dz)
                        if n in rs and n not in dist:
                            dist[n] = dist[c] + 1
                            nq.append(n)
                q = nq
            b = hs[0].b
            for c, d in dist.items():
                y = b + 6 + d // 2
                t = 'bottom' if d % 2 == 0 else 'top'
                key = (y, 1 if t == 'top' else 0)
                cur = done.get(c)
                if cur is None or key > cur[0]:
                    done[c] = (key, t, b, c in self.owner, self.spec['roofs'][hs[0].no])
            best = max(dist, key=lambda c: (dist[c] if c in own else -1, -abs(c[0] - sum(x for x, _ in own) / len(own))))
            chim.append((best, b, hs[0].group))
        for (x, z), (key, t, b, inside, rm) in done.items():
            y = key[0]
            if inside:
                for yy in range(b + 6, y):
                    self.set(x, yy, z, WALL)
            self.set(x, y, z, f'{rm}_slab[type={t}]')
        for (x, z), b, g in chim:
            ridge = max(k[0][0] for (xx, zz), k in done.items() if abs(xx - x) <= 1 and abs(zz - z) <= 1)
            for y in range(b + 6, ridge + 2):
                self.set(x, y, z, WALL)
            self.set(x, ridge + 2, z, 'flower_pot')

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
        for q, v in osm_footprints.guard(self.p.cells)['outside'].items():
            if 'slab' in v[0]:
                del self.p.cells[q]
        own1 = {(x + dx, z + dz) for (x, z) in self.owner for dx in (-1, 0, 1) for dz in (-1, 0, 1)}
        for q in list(self.p.cells):
            if (q[0], q[2]) not in own1 and 'leaves' in (self.live.block(*q) or ''):
                del self.p.cells[q]
        self.p.outside_footprint_ok = 'roof-overhang fringe slabs; evidence: footprint-hits json'
        from site_kit import interior_ground_clears
        self.keep.update(interior_ground_clears(
            self.p, {c: h.b for h in self.houses for c in h.inner}))
        self.p.keep = set(self.keep)   # #243: stoop cells may overwrite earlier ground at apply, as in feature_build
        # roof-overhang slabs of a neighbouring earlier build that spill onto our corner are theirs: leave them
        from cell_registry import view_registry
        owned = view_registry().owned(self.p.cells, exclude={self.name}, real_only=True)
        for q in [q for q, st in self.p.cells.items() if q in owned and owned[q][1] != st and q not in self.keep]:
            del self.p.cells[q]
        # same refusals as the live apply, so the designer sees them before hand-off (#99)
        guard_door_frames(self.p, self.live.block)
        guard_roof_jogs(self.p)
        for h in self.houses:
            print(h.no, 'b', h.b, 'cells', len(h.cells), 'door', getattr(h, 'door', None), 'rear', getattr(h, 'rear', None))
        return self.p



def build(spec):
    return Build(spec).build()


def run(spec_path, do_apply=False, overwrite=False):
    """Preview or apply a world-relative spec; replacing a live build requires an override."""
    from builder_provenance import resolve_builder_path, provenance
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
                   meta=provenance('house_build', spec_path))
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
