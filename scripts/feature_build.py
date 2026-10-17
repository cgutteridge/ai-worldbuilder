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
  doors          optional per-portal door map (university register ids):
                   {"<portal id>": {"leaves": 1|2, "block": "<*_door>"}}
                 leaves 2 = mirrored hinges; dry run prints each beside the portal name.
                 Absent/empty → legacy front/rear single spruce doors.
  facades        optional per-label per-face table of window rows (photo reading):
                   {"<label>": {"N"|"S"|"E"|"W": [
                     {"storey": 1..n, "glazed_h": 1..3, "pier": >=0,
                      "wall"?: "<mat>", "rhythm"?: "WW_BB_WW"}]}}
                 Dry run prints glazed share per storey per face.

Greeblies / path-in-footprint / contact sheet: separate system tickets.

Usage:  python3 scripts/feature_build.py site/builds/<name>.json [--apply]
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from script_env import ROOT  # noqa: E402
import osm_footprints  # noqa: E402
import portals  # noqa: E402
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
    spec.setdefault('doors', {})
    spec.setdefault('facades', {})
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
    _validate_doors(path, spec)
    _validate_facades(path, spec, labels)
    return spec


def _validate_doors(path, spec: dict) -> None:
    """Validate optional per-portal door map.

    @param path: spec path (for error text)
    @param spec: feature spec being loaded
    @raises SystemExit: on invalid door entries
    """
    doors = spec.get('doors') or {}
    if not isinstance(doors, dict):
        raise SystemExit(f'{path}: doors must be an object')
    for pid, row in doors.items():
        if not isinstance(row, dict):
            raise SystemExit(f'{path}: doors[{pid}] must be an object')
        leaves = row.get('leaves')
        block = row.get('block')
        if not isinstance(leaves, int) or leaves not in (1, 2):
            raise SystemExit(f'{path}: doors[{pid}].leaves must be 1 or 2')
        if not isinstance(block, str) or not block.endswith('_door') or 'trap' in block:
            raise SystemExit(f'{path}: doors[{pid}].block must be a *_door id (not trapdoor)')


def _validate_facades(path, spec: dict, labels: set) -> None:
    """Validate optional per-face facade table rows.

    @param path: spec path (for error text)
    @param spec: feature spec being loaded
    @param labels: known way labels
    @raises SystemExit: on invalid facade structure
    """
    facades = spec.get('facades') or {}
    if not isinstance(facades, dict):
        raise SystemExit(f'{path}: facades must be an object')
    for lab, faces in facades.items():
        if lab not in labels:
            raise SystemExit(f'{path}: facades label {lab} has no way')
        if not isinstance(faces, dict):
            raise SystemExit(f'{path}: facades[{lab}] must be face→rows')
        n_storeys = spec['storeys'][lab]
        for face, rows in faces.items():
            if face not in DIRS:
                raise SystemExit(f'{path}: facades[{lab}] bad face {face}')
            if not isinstance(rows, list) or not rows:
                raise SystemExit(f'{path}: facades[{lab}][{face}] needs a non-empty row list')
            seen = set()
            for row in rows:
                if not isinstance(row, dict):
                    raise SystemExit(f'{path}: facades[{lab}][{face}] row must be an object')
                storey = row.get('storey')
                glazed_h = row.get('glazed_h')
                pier = row.get('pier')
                if not isinstance(storey, int) or storey < 1 or storey > n_storeys:
                    raise SystemExit(
                        f'{path}: facades[{lab}][{face}] storey must be int 1..{n_storeys}')
                if storey in seen:
                    raise SystemExit(f'{path}: facades[{lab}][{face}] duplicate storey {storey}')
                seen.add(storey)
                if not isinstance(glazed_h, int) or glazed_h < 1 or glazed_h > STOREY_H:
                    raise SystemExit(
                        f'{path}: facades[{lab}][{face}] glazed_h must be int 1..{STOREY_H}')
                if not isinstance(pier, int) or pier < 0:
                    raise SystemExit(f'{path}: facades[{lab}][{face}] pier must be int >= 0')
                if 'rhythm' in row and row['rhythm'] is not None:
                    if not isinstance(row['rhythm'], str) or not row['rhythm']:
                        raise SystemExit(f'{path}: facades[{lab}][{face}] rhythm must be a non-empty string')
                    bad = [c for c in row['rhythm'] if c not in 'WB_']
                    if bad:
                        raise SystemExit(
                            f'{path}: facades[{lab}][{face}] rhythm chars must be W/B/_ (got {bad!r})')
                if 'wall' in row and row['wall'] is not None and not isinstance(row['wall'], str):
                    raise SystemExit(f'{path}: facades[{lab}][{face}] wall must be a string')


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

    def __init__(self, spec: dict, footprints: dict | None = None, world=None, osm_root=None):
        """
        @param spec: validated feature spec
        @param footprints: optional ``{label: set[(x,z)]}`` (else OSMIndex from ways)
        @param world: optional ``World``-like with ``block(x,y,z)`` (else live ``World()``)
        @param osm_root: optional OSM clip root for ``OSMIndex`` (tests / alternate trees)
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
            idx = osm_footprints.OSMIndex(root=osm_root) if osm_root is not None else osm_footprints.OSMIndex()
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
        """Place doors: portal map from the spec when present, else front/rear spruce.

        @param k: feature index
        @param f: feature mass
        """
        door_specs = self.spec.get('doors') or {}
        if door_specs:
            self.portal_doors(f, door_specs)
        else:
            self.default_doors(k, f)

    def _door_candidates(self, f: Feature, face: str) -> list:
        """Framed wall cells on ``face`` with interior behind them.

        @param f: feature mass
        @param face: ``N``/``S``/``E``/``W``
        @returns: candidate ``(x, z)`` cells
        """
        dx, dz = DIRS[face]
        cand = [c for c in f.walls if self.exposed(c, face) and (c[0] - dx, c[1] - dz) in f.inner]
        return self.framed(f, cand, face)

    def _set_door_leaf(self, cell, face: str, block: str, hinge: str, b: int):
        """Write one framed door leaf (lower+upper) and clear the stoop column.

        @param cell: wall ``(x, z)``
        @param face: facing letter
        @param block: door block id (e.g. ``iron_door``)
        @param hinge: ``left`` or ``right``
        @param b: floor y
        """
        dx, dz = DIRS[face]
        face_name = FACE[face]
        for y, part in ((b + 1, 'lower'), (b + 2, 'upper')):
            self.set(cell[0], y, cell[1],
                     f'{block}[facing={face_name},half={part},hinge={hinge},open=false]')
        st = (cell[0] + dx, cell[1] + dz)
        self.set(st[0], b, st[1], 'smooth_stone')
        for y in range(b + 1, b + 4):
            self.set(st[0], y, st[1], 'air')

    def portal_doors(self, f: Feature, door_specs: dict) -> None:
        """Place per-portal doors from the spec; print each beside its portal name.

        @param f: feature mass
        @param door_specs: ``{portal_id: {leaves, block}}``
        """
        by_id = {p['id']: p for p in portals.load()}
        placed: set = set()
        f.door = None
        f.rear = None
        f.door_cells = set()
        reach2 = portals.DOOR_REACH ** 2
        for pid, row in sorted(door_specs.items()):
            portal = by_id.get(pid)
            if portal is None:
                print(f'door {pid}: unknown portal: skipped')
                continue
            px, pz = portal['x'], portal['z']
            leaves = row['leaves']
            block = row['block']
            best = None
            for face in DIRS:
                for c in self._door_candidates(f, face):
                    if c in placed:
                        continue
                    d2 = (c[0] - px) ** 2 + (c[1] - pz) ** 2
                    if best is None or d2 < best[0]:
                        best = (d2, c, face)
            if best is None or best[0] > reach2:
                print(f'door {pid} "{portal["label"]}": no wall near {px},{pz}: skipped')
                continue
            _d2, c0, face = best
            lat = (1, 0) if face in 'NS' else (0, 1)
            cells = [c0]
            if leaves == 2:
                opts = [
                    (c0[0] + lat[0], c0[1] + lat[1]),
                    (c0[0] - lat[0], c0[1] - lat[1]),
                ]
                opts.sort(key=lambda c: (c[0] - px) ** 2 + (c[1] - pz) ** 2)
                pair = next(
                    (c for c in opts
                     if c not in placed and c in self._door_candidates(f, face)),
                    None,
                )
                if pair is None:
                    print(f'door {pid} "{portal["label"]}": no pair cell; using 1-leaf')
                    leaves = 1
                else:
                    cells = sorted([c0, pair], key=lambda c: c[0] * lat[0] + c[1] * lat[1])
            hinges = ('left',) if leaves == 1 else ('left', 'right')
            b = f.b
            for cell, hinge in zip(cells, hinges):
                self._set_door_leaf(cell, face, block, hinge, b)
                placed.add(cell)
                f.door_cells.add(cell)
            dx, dz = DIRS[face]
            self.step_out((cells[0][0] + dx, cells[0][1] + dz), face, b)
            if f.door is None:
                f.door = cells[0]
            print(
                f'door {pid} "{portal["label"]}" {leaves}-leaf {block} '
                f'at {cells[0][0]},{b + 1},{cells[0][1]} facing {FACE[face]}'
            )

    def default_doors(self, k: int, f: Feature):
        """Legacy front and rear single spruce doors when the spec has no ``doors`` map.

        @param k: feature index (unused; kept for call-site parity)
        @param f: feature mass
        """
        del k  # call-site parity with doors()
        b = f.b
        front = f.front
        r = OPP[front]
        cand = self._door_candidates(f, front)
        if not cand:
            return
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
        f.door_cells = {c0}
        self._set_door_leaf(c0, front, 'spruce_door', 'left', b)
        fx, fz = DIRS[front]
        self.step_out((c0[0] + fx, c0[1] + fz), front, b)
        cand = self._door_candidates(f, r)
        if cand:
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
            f.door_cells.add(c1)
            self._set_door_leaf(c1, r, 'spruce_door', 'right', b)
            sx, sz = c1[0] + DIRS[r][0], c1[1] + DIRS[r][1]
            self.step_out((sx, sz), r, b)
        else:
            f.rear = None

    def face_runs(self, f: Feature, face: str) -> list[list]:
        """Contiguous straight wall runs exposed to ``face``, each sorted along the face.

        An L-shaped or notched mass yields several runs (one per constant edge
        coordinate), so pier rhythm applies per straight stretch — not across a
        courtyard notch. Corner cells stay in their run (craftsmanship end piers).

        @param f: feature mass
        @param face: ``N``/``S``/``E``/``W``
        @returns: list of ordered ``(x, z)`` runs
        """
        cand = [c for c in f.walls if self.exposed(c, face)]
        if not cand:
            return []
        by_edge: dict = {}
        if face in 'NS':
            for c in cand:
                by_edge.setdefault(c[1], []).append(c)
            along = 0
        else:
            for c in cand:
                by_edge.setdefault(c[0], []).append(c)
            along = 1
        runs = []
        for _edge, cells in sorted(by_edge.items()):
            cells = sorted(cells, key=lambda c: c[along])
            run = [cells[0]]
            for c in cells[1:]:
                prev = run[-1]
                if c[along] == prev[along] + 1:
                    run.append(c)
                else:
                    runs.append(run)
                    run = [c]
            runs.append(run)
        return runs

    def face_run(self, f: Feature, face: str) -> list:
        """All wall cells exposed to ``face`` (concat of ``face_runs``), for share totals.

        @param f: feature mass
        @param face: ``N``/``S``/``E``/``W``
        @returns: ordered ``(x, z)`` cells
        """
        out = []
        for run in self.face_runs(f, face):
            out.extend(run)
        return out

    @staticmethod
    def facade_roles(row: dict, n: int) -> list[str]:
        """Expand a facade row into per-cell roles (``W`` glaze, ``B`` balcony, ``_`` pier).

        Without ``rhythm``, uses 2-wide windows and ``pier`` solids; end cells are piers.

        @param row: facade table row
        @param n: face length in cells
        @returns: list of length ``n``
        """
        if n <= 0:
            return []
        rhythm = row.get('rhythm')
        if rhythm:
            chars = list(rhythm)
            roles = (chars * ((n // len(chars)) + 1))[:n]
        else:
            pier = row['pier']
            win_w = 2
            period = win_w + pier
            roles = []
            for i in range(n):
                if period > 0 and (i % period) < win_w:
                    roles.append('W')
                else:
                    roles.append('_')
            roles[0] = '_'
            if n > 1:
                roles[-1] = '_'
        return roles

    def _door_keepout(self, f: Feature) -> set:
        """Wall cells that must not become glass (every placed door leaf).

        @param f: feature mass
        @returns: set of ``(x, z)``
        """
        cells = set(getattr(f, 'door_cells', ()) or ())
        for q in (getattr(f, 'door', None), getattr(f, 'rear', None)):
            if q:
                cells.add(q)
        return cells

    def windows(self, f: Feature):
        """Punch openings from the facade table, or placeholder pier rhythm if absent."""
        facades = (self.spec.get('facades') or {}).get(f.no)
        if not facades:
            self._windows_placeholder(f)
            return
        door = self._door_keepout(f)
        for face, rows in facades.items():
            runs = self.face_runs(f, face)
            if not runs:
                continue
            axis = 'z' if face in 'EW' else 'x'
            by_storey = {r['storey']: r for r in rows}
            for s in range(f.storeys):
                row = by_storey.get(s + 1)
                if not row:
                    continue
                wall_mat = row.get('wall') or self.wall
                glazed_h = row['glazed_h']
                base = f.b + s * STOREY_H
                for run in runs:
                    roles = self.facade_roles(row, len(run))
                    for c, role in zip(run, roles):
                        if c in door:
                            continue
                        for dy in range(STOREY_H):
                            y = base + dy
                            if role == 'W' and dy >= STOREY_H - glazed_h:
                                self.set(c[0], y, c[1], pane(axis))
                            elif wall_mat != self.wall:
                                self.set(c[0], y, c[1], wall_mat)

    def _windows_placeholder(self, f: Feature):
        """Legacy pier rhythm when the spec has no facade table for this label."""
        b = f.b
        door = self._door_keepout(f)
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
            if c in door or any(abs(c[0] - q[0]) + abs(c[1] - q[1]) < 3 for q in door):
                continue
            par = (c[0] + c[1]) if diag else (c[0] if d in 'NS' else c[1])
            if par % (3 if diag else 2):
                continue
            axis = 'z' if d in 'EW' else 'x'
            for s in range(f.storeys):
                self.set(c[0], b + s * STOREY_H + 2, c[1], pane(axis))

    def glazed_share_report(self) -> list[str]:
        """Glazed share of each storey per face (for dry-run photo compare).

        @returns: human-readable lines ``<label> face <F> storey <n>: glazed a/b (p%)``
        """
        lines = []
        for f in self.features:
            facades = (self.spec.get('facades') or {}).get(f.no) or {}
            if not facades:
                continue
            for face in sorted(facades):
                run = self.face_run(f, face)
                if not run:
                    continue
                for s in range(f.storeys):
                    base = f.b + s * STOREY_H
                    total = glazed = 0
                    for c in run:
                        for dy in range(STOREY_H):
                            st = self.p.cells.get((c[0], base + dy, c[1]), '')
                            name = st.split('[')[0]
                            if name.endswith('_door') or name == 'air':
                                continue
                            total += 1
                            if 'glass' in name:
                                glazed += 1
                    if total == 0:
                        continue
                    pct = 100 * glazed // total
                    lines.append(
                        f'{f.no} face {face} storey {s + 1}: glazed {glazed}/{total} ({pct}%)')
        return lines

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
        for line in self.glazed_share_report():
            print(line)
        return self.p


def build(spec: dict, footprints: dict | None = None, world=None, osm_root=None) -> Plan:
    """Build a feature plan from a validated spec.

    @param spec: validated feature spec
    @param footprints: optional label→cells map (tests / offline)
    @param world: optional World-like
    @param osm_root: optional OSM clip root
    @returns: Plan
    """
    return Build(spec, footprints=footprints, world=world, osm_root=osm_root).build()


def run(spec_path, do_apply: bool = False, footprints: dict | None = None, world=None, osm_root=None) -> Plan:
    """Load spec, build plan, preview, and site_kit.apply (dry unless do_apply).

    @param spec_path: path to site/builds/<name>.json
    @param do_apply: when True, live apply; otherwise dry run
    @param footprints: optional label→cells map
    @param world: optional World-like
    @param osm_root: optional OSM clip root
    @returns: Plan
    """
    spec = load_spec(spec_path)
    plan = build(spec, footprints=footprints, world=world, osm_root=osm_root)
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
