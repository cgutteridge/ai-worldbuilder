#!/usr/bin/env python3
"""Generic OSM-footprint builder for non-house building features.

Use for odd masses (SU, halls, sheds, campus lumps) — not for houses/terraces
(those stay on ``house_build.py``). Shares ``site_kit`` preview/apply.

A run is a JSON spec in ``site/builds/<name>.json``:

  name           plan name (also verification/site/<name>-cells.json)
  notes          provenance (free text, never read by code)
  modifies       earlier builds this one may overwrite (default [])
  ways           {"<osm way id>": "<label>"} footprints to build
  storeys        {"<label>": <int >= 1>} clear storeys (each STOREY_H blocks high)
  roofs          {"<label>": {"kind": "flat"|"pitched", "material": "<slab mat>"}}
  groups         [{"nos": [labels], "mode": "each"|"group", "front": "N"|"S"|"E"|"W"}]
  wall / floor   wall and interior floor materials (defaults bricks / smooth_stone)
  floor_min      minimum floor y or null
  step_max       stoop outward look (default 4)
  ledge_stair    rising stair on 1-block ledges beyond 4 columns (default false)
  door_frame     framed doors on angled walls (default true)
  keep_edits     stoop/step cells survive earlier-build clash filter (default false)

Facade table, double/coloured doors, greeblies: separate system tickets — not this module.

Usage:  python3 scripts/feature_build.py site/builds/<name>.json [--apply]
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from script_env import ROOT  # noqa: E402
import osm_footprints  # noqa: E402
from site_kit import Plan, World, apply, preview  # noqa: E402

STOREY_H = 3
GROUND = (
    'minecraft:stone', 'minecraft:gray_terracotta', 'minecraft:polished_granite',
    'minecraft:polished_diorite', 'minecraft:light_gray_concrete', 'minecraft:grass_block',
    'minecraft:dirt', 'minecraft:coarse_dirt', 'minecraft:gravel',
)
DIRS = {'N': (0, -1), 'S': (0, 1), 'W': (-1, 0), 'E': (1, 0)}
OPP = {'N': 'S', 'S': 'N', 'W': 'E', 'E': 'W'}
N8 = [(dx, dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1) if dx or dz]
FACE = {'S': 'south', 'N': 'north', 'W': 'west', 'E': 'east'}
TOWARD = {'S': 'north', 'N': 'south', 'W': 'east', 'E': 'west'}
ROOF_KINDS = frozenset({'flat', 'pitched'})


def pane(axis: str) -> str:
    """Glass pane block state spanning the given horizontal axis.

    @param axis: ``'x'`` or ``'z'``
    @returns: pane block state string
    """
    return 'glass_pane[east=true,west=true]' if axis == 'x' else 'glass_pane[north=true,south=true]'


def stair(mat: str, facing: str, half: str = 'bottom') -> str:
    """Stair block state.

    @param mat: stair material prefix (e.g. ``stone``, ``spruce``)
    @param facing: facing direction name
    @param half: ``bottom`` or ``top``
    @returns: stair block state string
    """
    return f'{mat}_stairs[facing={facing},half={half}]'


def load_spec(path) -> dict:
    """Read and validate a feature build spec; fill defaults.

    @param path: spec JSON path
    @returns: spec dict
    @raises SystemExit: on missing/invalid keys
    """
    spec = json.loads(Path(path).read_text())
    for k in ('name', 'ways', 'storeys', 'roofs', 'groups'):
        if k not in spec:
            raise SystemExit(f'{path}: missing "{k}"')
    spec.setdefault('modifies', [])
    spec.setdefault('floor_min', None)
    spec.setdefault('step_max', 4)
    spec.setdefault('ledge_stair', False)
    spec.setdefault('keep_edits', False)
    spec.setdefault('door_frame', True)
    spec.setdefault('wall', 'bricks')
    spec.setdefault('floor', 'smooth_stone')
    labels = set(spec['ways'].values())
    for lab, n in spec['storeys'].items():
        if lab not in labels:
            raise SystemExit(f'{path}: storeys label {lab} has no way')
        if not isinstance(n, int) or n < 1:
            raise SystemExit(f'{path}: storeys[{lab}] must be int >= 1')
    for lab, roof in spec['roofs'].items():
        if lab not in labels:
            raise SystemExit(f'{path}: roofs label {lab} has no way')
        if not isinstance(roof, dict) or roof.get('kind') not in ROOF_KINDS or not roof.get('material'):
            raise SystemExit(f'{path}: roofs[{lab}] needs {{kind: flat|pitched, material: ...}}')
    for g in spec['groups']:
        if g['front'] not in DIRS or g.get('mode', 'each') not in ('each', 'group'):
            raise SystemExit(f'{path}: bad group {g}')
        g.setdefault('mode', 'each')
        for no in g['nos']:
            if no not in labels or no not in spec['storeys'] or no not in spec['roofs']:
                raise SystemExit(f'{path}: label {no} needs a way, storeys and roof')
    return spec


class Feature:
    """One labelled footprint mass inside a feature build."""

    def __init__(self, no, cells, group, storeys, roof):
        self.no = no
        self.cells = cells
        self.group = group
        self.storeys = storeys
        self.roof = roof
        self.front = group['front']


class Build:
    """Plan a non-house feature from a validated spec."""

    def __init__(self, spec: dict, footprints: dict | None = None, world=None):
        """
        @param spec: validated feature spec
        @param footprints: optional ``{label: set[(x,z)]}`` (else OSMIndex from ways)
        @param world: optional ``World``-like with ``block(x,y,z)`` (else live ``World()``)
        """
        self.spec = spec
        self.name = spec['name']
        self.groups = spec['groups']
        self.wall = spec['wall']
        self.floor_mat = spec['floor']
        self.keep = set()
        self.p = Plan(self.name)
        self.live = world if world is not None else World()
        by_no = {k: set(v) for k, v in (footprints or {}).items()}
        if footprints is None:
            idx = osm_footprints.OSMIndex()
            for (x, z), i in idx.cells().items():
                bp = idx.buildings[i][1]
                n = self.spec['ways'].get(str(bp.get('osm_way_id') or bp.get('osm_id')))
                if n:
                    by_no.setdefault(n, set()).add((x, z))
            self.idx = idx
        else:
            self.idx = None
        missing = [lab for lab in spec['ways'].values() if lab not in by_no or not by_no[lab]]
        if missing:
            raise SystemExit(f'{self.name}: no footprint cells for {missing}')
        self.features = []
        for g in self.groups:
            for no in g['nos']:
                self.features.append(Feature(no, by_no[no], g, spec['storeys'][no], spec['roofs'][no]))
        self.owner = {}
        for k, f in enumerate(self.features):
            for c in f.cells:
                self.owner[c] = k
        self.by_no_all = by_no

    def set(self, x, y, z, s):
        self.p.set(x, y, z, s)

    def kset(self, x, y, z, st):
        """Set a stoop/step cell; with keep_edits it survives the earlier-build clash filter."""
        if self.spec['keep_edits']:
            self.keep.add((x, y, z))
        self.set(x, y, z, st)

    def surf(self, x, z):
        for y in range(75, 30, -1):
            n = (self.live.block(x, y, z) or 'minecraft:air').split('[')[0]
            if n in ('minecraft:stone', 'minecraft:grass_block', 'minecraft:dirt', 'minecraft:coarse_dirt', 'minecraft:granite'):
                return y
            if n in GROUND:
                return None
        return None

    def floor_y(self, f: Feature) -> int:
        """Floor level from plot-edge surface, honouring floor_min.

        @param f: feature mass
        @returns: floor y
        """
        ring = set()
        for (x, z) in f.cells:
            for dx in range(-7, 8):
                for dz in range(-7, 8):
                    c = (x + dx, z + dz)
                    if max(abs(dx), abs(dz)) >= 4 and c not in self.by_no_all_cells:
                        ring.add(c)
        near = {(x + dx, z + dz) for (x, z) in f.cells for dx in range(-3, 4) for dz in range(-3, 4)}
        ys = [self.surf(x, z) for x, z in ring - near]
        ys = [y for y in ys if y]
        if not ys:
            y = self.spec['floor_min'] if self.spec['floor_min'] is not None else 50
        else:
            y = int(statistics.median(ys))
            if self.spec['floor_min'] is not None:
                y = max(y, self.spec['floor_min'])
        return y

    def clear(self):
        cols = set()
        for (x, z) in self.owner:
            cols.update((x + dx, z + dz) for dx in (-2, -1, 0, 1, 2) for dz in (-2, -1, 0, 1, 2))
        self.cols = cols
        top_clear = max(f.b + f.storeys * STOREY_H + 8 for f in self.features)
        for x, z in cols:
            nearest = min(self.features, key=lambda f: min(max(abs(x - c[0]), abs(z - c[1])) for c in f.cells))
            b = nearest.b
            if (x, z) in self.foreign or (x, z) in self.shared:
                continue
            own = (x, z) in self.owner
            for y in range(37, b):
                self.set(x, y, z, 'stone')
            if own:
                rng = range(b, top_clear + 1)
            else:
                if (self.live.block(x, b, z) or 'minecraft:air') == 'minecraft:air':
                    self.set(x, b, z, 'stone')
                rng = range(b + 1, top_clear + 1)
            for y in rng:
                if own or (self.live.block(x, y, z) or 'minecraft:air').split('[')[0] not in GROUND:
                    self.set(x, y, z, 'air')

    def exposed(self, c, d) -> bool:
        dx, dz = DIRS[d]
        return (c[0] + dx, c[1] + dz) not in self.owner

    def shell(self, k: int, f: Feature):
        b = f.b
        own = f.cells
        height = f.storeys * STOREY_H
        f.walls = set()
        for c in own:
            for dx, dz in N8:
                n = (c[0] + dx, c[1] + dz)
                ko = self.owner.get(n)
                if ko is None or (ko != k and k < ko):
                    f.walls.add(c)
                    break
        f.inner = own - f.walls
        for x, z in own:
            for y in range(b - 4, b):
                self.set(x, y, z, 'stone')
        for x, z in f.walls:
            for y in range(b, b + height):
                self.set(x, y, z, self.wall)
        for x, z in f.inner:
            self.set(x, b, z, self.floor_mat)
            for y in range(b + 1, b + height):
                self.set(x, y, z, 'air')
            for s in range(1, f.storeys):
                self.set(x, b + s * STOREY_H, z, self.floor_mat)

    def framed(self, f: Feature, cand, d):
        """Door candidates with a wall cell on both sides along the wall.

        @param f: feature mass
        @param cand: candidate wall cells
        @param d: exposure direction
        @returns: filtered candidate list
        """
        if not self.spec['door_frame']:
            return cand
        lat = ((0, 1), (0, -1)) if d in 'EW' else ((1, 0), (-1, 0))
        strict = [c for c in cand
                  if all((c[0] + lx, c[1] + lz) in f.walls and self.exposed((c[0] + lx, c[1] + lz), d) for lx, lz in lat)]
        if strict:
            return strict
        return [c for c in cand if all((c[0] + lx, c[1] + lz) in f.walls for lx, lz in lat)] or cand

    def top_solid(self, x, z):
        for y in range(70, 40, -1):
            n = (self.live.block(x, y, z) or 'minecraft:air').split('[')[0]
            if n in GROUND and n != 'minecraft:stone_stairs':
                return y
        return None

    def step_out(self, st, d, b):
        dx, dz = DIRS[d]
        facing = TOWARD[d]
        for k in range(1, self.spec['step_max'] + 1):
            n = (st[0] + dx * k, st[1] + dz * k)
            if n in self.owner or n in self.foreign:
                continue
            g = self.top_solid(*n)
            if g is None:
                continue
            if self.spec['ledge_stair'] and g == b + 1 and k > 4:
                self.kset(n[0], g, n[1], stair('stone', FACE[d]))
                return
            if g > b + 1 or (g > b and k > 4):
                return
            if g > b:
                self.kset(n[0], b, n[1], 'stone')
                for y in range(b + 1, g + 1):
                    self.kset(n[0], y, n[1], 'air')
                continue
            if g < b:
                self.kset(n[0], b, n[1], stair('stone', facing))
                for y in range(g + 1, b):
                    self.kset(n[0], y, n[1], 'stone')
                for y in range(b + 1, b + 4):
                    self.kset(n[0], y, n[1], 'air')
                return

    def doors(self, k: int, f: Feature):
        b = f.b
        front = f.front
        r = OPP[front]
        fx, fz = DIRS[front]
        cand = [c for c in f.walls if self.exposed(c, front) and (c[0] - fx, c[1] - fz) in f.inner]
        if not cand:
            return
        cand = self.framed(f, cand, front)
        if front == 'N':
            front_edge = min(c[1] for c in cand)
            cand = [c for c in cand if c[1] <= front_edge + 2]
            mid = sum(c[0] for c in cand) / len(cand)
            c0 = min(cand, key=lambda c: abs(c[0] - mid))
        elif front == 'S':
            front_edge = max(c[1] for c in cand)
            cand = [c for c in cand if c[1] >= front_edge - 2]
            mid = sum(c[0] for c in cand) / len(cand)
            c0 = min(cand, key=lambda c: abs(c[0] - mid))
        else:
            front_edge = min(c[0] for c in cand) if front == 'W' else max(c[0] for c in cand)
            if front == 'W':
                cand = [c for c in cand if c[0] <= front_edge + 1]
            else:
                cand = [c for c in cand if c[0] >= front_edge - 1]
            mid = sum(c[1] for c in cand) / len(cand)
            c0 = min(cand, key=lambda c: abs(c[1] - mid))
        f.door = c0
        face = {'S': 'south', 'N': 'north', 'W': 'west', 'E': 'east'}[front]
        for y, part in ((b + 1, 'lower'), (b + 2, 'upper')):
            self.set(c0[0], y, c0[1], f'spruce_door[facing={face},half={part},hinge=left,open=false]')
        st = (c0[0] + fx, c0[1] + fz)
        self.set(st[0], b, st[1], 'smooth_stone')
        for y in range(b + 1, b + 4):
            self.set(st[0], y, st[1], 'air')
        self.step_out(st, front, b)
        cand = [c for c in f.walls if self.exposed(c, r) and (c[0] - DIRS[r][0], c[1] - DIRS[r][1]) in f.inner]
        if cand:
            cand = self.framed(f, cand, r)
            if r in 'NS':
                e = min(c[1] for c in cand) if r == 'N' else max(c[1] for c in cand)
                cc = [c for c in cand if (c[1] <= e + 2 if r == 'N' else c[1] >= e - 2)]
                m = sum(c[0] for c in cc) / len(cc)
                c1 = min(cc, key=lambda c: abs(c[0] - m))
            else:
                e = min(c[0] for c in cand) if r == 'W' else max(c[0] for c in cand)
                cc = [c for c in cand if (c[0] <= e + 1 if r == 'W' else c[0] >= e - 1)]
                m = sum(c[1] for c in cc) / len(cc)
                c1 = min(cc, key=lambda c: abs(c[1] - m))
            f.rear = c1
            rf = {'N': 'north', 'E': 'east', 'S': 'south', 'W': 'west'}[r]
            for y, part in ((b + 1, 'lower'), (b + 2, 'upper')):
                self.set(c1[0], y, c1[1], f'spruce_door[facing={rf},half={part},hinge=right,open=false]')
            sx, sz = c1[0] + DIRS[r][0], c1[1] + DIRS[r][1]
            self.set(sx, b, sz, 'smooth_stone')
            for y in range(b + 1, b + 4):
                self.set(sx, y, sz, 'air')
            self.step_out((sx, sz), r, b)
        else:
            f.rear = None

    def windows(self, f: Feature):
        """Placeholder pier rhythm (facade table is ticket #12)."""
        b = f.b
        door = {getattr(f, 'door', None), getattr(f, 'rear', None)}
        for c in f.walls:
            exp = [d for d in 'NSEW' if self.exposed(c, d)]
            if not exp:
                continue
            d = exp[0]
            diag = len(exp) > 1
            lat = [(0, 1), (0, -1)] if d in 'EW' else [(1, 0), (-1, 0)]
            ok = True
            for lx, lz in ([] if diag else lat):
                n = (c[0] + lx, c[1] + lz)
                if n not in f.walls or not self.exposed(n, d):
                    ok = False
            if not ok:
                continue
            if any(abs(c[0] - q[0]) + abs(c[1] - q[1]) < 3 for q in door if q):
                continue
            par = (c[0] + c[1]) if diag else (c[0] if d in 'NS' else c[1])
            if par % (3 if diag else 2):
                continue
            axis = 'z' if d in 'EW' else 'x'
            for s in range(f.storeys):
                self.set(c[0], b + s * STOREY_H + 2, c[1], pane(axis))

    def roof_flat(self, features: list[Feature], own: set):
        rm = features[0].roof['material']
        eave = features[0].b + features[0].storeys * STOREY_H
        rs = set(own)
        for (x, z) in own:
            for dx, dz in N8:
                n = (x + dx, z + dz)
                if n not in self.owner and n not in self.foreign and n not in self.shared:
                    rs.add(n)
        for x, z in rs:
            self.set(x, eave, z, f'{rm}_slab[type=bottom]')

    def roof_pitched(self, features: list[Feature], own: set):
        rm = features[0].roof['material']
        b = features[0].b
        eave = b + features[0].storeys * STOREY_H
        rs = set(own)
        for (x, z) in own:
            for dx, dz in N8:
                n = (x + dx, z + dz)
                if n not in self.owner and n not in self.foreign and n not in self.shared:
                    rs.add(n)
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
        done = {}
        for c, d in dist.items():
            y = eave + d // 2
            t = 'bottom' if d % 2 == 0 else 'top'
            key = (y, 1 if t == 'top' else 0)
            cur = done.get(c)
            if cur is None or key > cur[0]:
                done[c] = (key, t, c in self.owner)
        for (x, z), (key, t, inside) in done.items():
            y = key[0]
            if inside:
                for yy in range(eave, y):
                    self.set(x, yy, z, self.wall)
            self.set(x, y, z, f'{rm}_slab[type={t}]')

    def roof(self):
        todo = []
        for g in self.groups:
            hs = [f for f in self.features if f.group is g]
            if g['mode'] == 'group':
                todo.append((hs, set().union(*[f.cells for f in hs])))
            else:
                todo.extend(([f], f.cells) for f in hs)
        for hs, own in todo:
            kind = hs[0].roof['kind']
            if kind == 'flat':
                self.roof_flat(hs, own)
            else:
                self.roof_pitched(hs, own)

    def build(self) -> Plan:
        """Produce the offline plan for this feature build.

        @returns: ``site_kit.Plan``
        """
        all_cells = set(self.owner)
        if self.idx is not None:
            self.foreign = {c for c in self.idx.cells() if c not in self.owner}
        else:
            self.foreign = set()
        self.by_no_all_cells = all_cells | self.foreign
        self.shared = {(x + dx, z + dz) for (x, z) in self.foreign for dx, dz in N8} - self.foreign - set(self.owner)
        for f in self.features:
            f.b = self.floor_y(f)
        for g in self.groups:
            hs = [f for f in self.features if f.group is g]
            if g['mode'] == 'group':
                bb = min(f.b for f in hs)
                for f in hs:
                    f.b = bb
        self.clear()
        for k, f in enumerate(self.features):
            self.shell(k, f)
        for k, f in enumerate(self.features):
            self.doors(k, f)
            self.windows(f)
        self.roof()
        if self.idx is not None:
            for q, v in osm_footprints.guard(self.p.cells)['outside'].items():
                if 'slab' in v[0]:
                    del self.p.cells[q]
        own1 = {(x + dx, z + dz) for (x, z) in self.owner for dx in (-1, 0, 1) for dz in (-1, 0, 1)}
        for q in list(self.p.cells):
            if (q[0], q[2]) not in own1 and 'leaves' in (self.live.block(*q) or ''):
                del self.p.cells[q]
        self.p.outside_footprint_ok = 'roof-overhang fringe slabs; evidence: footprint-hits json'
        from site_kit import built_cells
        owned = built_cells(exclude={self.name}, within=self.p.cells)
        for q in [q for q, st in self.p.cells.items() if q in owned and owned[q][1] != st and q not in self.keep]:
            del self.p.cells[q]
        for f in self.features:
            print(f.no, 'b', f.b, 'storeys', f.storeys, 'cells', len(f.cells),
                  'door', getattr(f, 'door', None), 'rear', getattr(f, 'rear', None))
        return self.p


def build(spec: dict, footprints: dict | None = None, world=None) -> Plan:
    """Build a feature plan from a validated spec.

    @param spec: validated feature spec
    @param footprints: optional label→cells map (tests / offline)
    @param world: optional World-like
    @returns: Plan
    """
    return Build(spec, footprints=footprints, world=world).build()


def run(spec_path, do_apply: bool = False, footprints: dict | None = None, world=None) -> Plan:
    """Load spec, build plan, preview, and site_kit.apply (dry unless do_apply).

    @param spec_path: path to site/builds/<name>.json
    @param do_apply: when True, live apply; otherwise dry run
    @param footprints: optional label→cells map
    @param world: optional World-like
    @returns: Plan
    """
    spec = load_spec(spec_path)
    plan = build(spec, footprints=footprints, world=world)
    out = ROOT / f'verification/site/{spec["name"]}-preview.png'
    preview(plan, out, scale=4)
    print(json.dumps(plan.summary())[:600])
    apply(plan, dry=not do_apply, modifies=tuple(spec['modifies']))
    return plan


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('spec', help='site/builds/<name>.json')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    run(a.spec, do_apply=a.apply)
