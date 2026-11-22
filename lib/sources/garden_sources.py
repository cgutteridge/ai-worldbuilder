#!/usr/bin/env python3
"""Garden boundary evidence for the decor layer: per-parcel fences, walls and hedges.

The vector map has few garden boundaries, so the decor layer would leave every house garden bare.
This module derives them, cheapest evidence first, and tags each segment with its source:

  1. PLOTS     each map house footprint grows to a first-guess plot (osm_footprints.grow_plots,
               approximate Voronoi, stops at street surface and neighbours).
  2. SEGMENTS  plot edge columns are classed by what lies beyond them:
                 front  street surface
                 side_front / side_back  neighbour plot, edge runs front-to-back, before / behind the house line
                 rear   neighbour plot, edge runs across the plot   (open edge: nothing, no evidence)
  3. CONVENTION  site/gardens/<street>.json says what each role is made of on that street
               (one streetlevel look per street run; per-house overrides by house number).
  4. AERIAL    a segment whose aerial colour is a continuous dark-green strip (ignoring nDSM tree-
               canopy columns) is a hedge, unless the designer convention vetoes that role with
               ``type: none`` (hedges are the one boundary the z19 aerial can see; canopy is not a hedge).

Without street, district or class boundary evidence, only aerial hedges show.
Reads the vector map, the aerial, the lidar tree mask and the live ground surface; never an earlier fence build.

  python3 scripts/garden_sources.py --cell 47,-43 [--bbox x0,z0,x1,z1]     # report + overlay PNG
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from script_env import ROOT  # noqa: E402

import barrier_vocab as bv  # noqa: E402
import cellmath  # noqa: E402
import land_use  # noqa: E402
import district_profiles  # noqa: E402

GARDENS = ROOT / 'site' / 'gardens'
DISTRICTS = ROOT / 'site' / 'districts'
HEDGE_SHARE = 0.6        # share of a segment's columns that must read dark green to call it a hedge
SIDE_DEPTH = 40          # side boundaries only run this far behind the front line unless told


def slug(street: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', street.lower()).strip('_')


def load_convention(street: str) -> dict | None:
    p = GARDENS / f'{slug(street)}.json'
    return json.loads(p.read_text()) if p.exists() else None


def load_district(cell: tuple[int, int], directory: Path | None = None) -> dict | None:
    """Load a cell's district from ``site/districts/<slug>.json``; refuse any district overlap.

    @param cell: world-grid (tx, tz), never a map-provider place polygon
    @param directory: synthetic directory in tests; defaults to the active world's districts
    @returns: validated profile or None; malformed/unrelated profiles also fail loudly
    """
    regions, selected = [], None
    for path in sorted((directory if directory is not None else DISTRICTS).glob('*.json')):
        try:
            profile = district_profiles.validate_profile(json.loads(path.read_text()))
        except (ValueError, TypeError) as exc:
            raise district_profiles.ProfileError(f'{path}: {exc}') from exc
        rectangles = district_profiles.cell_rectangles(profile['cells'])
        for previous, other in regions:
            if any(district_profiles.rectangles_overlap(a, b) for a in rectangles for b in other):
                raise district_profiles.ProfileError(f'overlapping districts: {previous} and {path.name}')
        regions.append((path.name, rectangles))
        if any(x0 <= cell[0] <= x1 and z0 <= cell[1] <= z1 for x0, z0, x1, z1 in rectangles):
            selected = profile
    return selected


def lamp_spacing(cell: tuple[int, int], cls: str, highway: str, conv: dict | None = None,
                 number: str = '', *, profile: dict | None = None) -> dict:
    """Resolve lamp spacing for #260 (no lamp placement here); null means no inference.

    @param cell: world-grid cell; profile is loaded here unless supplied by the caller
    @param cls: adjoining land-use class id
    @param highway: OSM highway value
    @param conv: optional street convention, with a furniture section
    @param number: optional per-property furniture override
    @param profile: already loaded profile, e.g. for a batch of street runs
    @returns: value/layer/open resolution with inference provenance and evidence ids
    """
    import provenance as prov

    profile = load_district(cell) if profile is None else profile
    prop, street = _layers(conv, number)
    furniture = ((profile or {}).get('classes', {}).get(cls, {}).get('furniture') or {})
    result = land_use.resolve(cls, 'lamp_spacing', role=highway,
                              property=(prop or {}).get('furniture'), street=(street or {}).get('furniture'),
                              district=furniture)
    ids = (profile or {}).get('evidence_ids', ()) if result['layer'] == 'district' else (conv or {}).get('evidence_ids', ())
    return {**result, 'provenance': prov.provenance_record(source='inference', confidence='low', evidence_ids=ids)}


def bench_density(cell: tuple[int, int], cls: str, conv: dict | None = None, number: str = '', *,
                  profile: dict | None = None) -> dict:
    """Resolve bench density (per 100 blocks of path) for #260; null means no bench inference.

    Same ladder as :func:`lamp_spacing`: per-house override, street convention, district profile,
    then the class row's ``furniture`` section (shipped for ``park`` only).

    @param cell: world-grid cell; profile is loaded here unless supplied by the caller
    @param cls: adjoining land-use class id
    @param conv: optional street convention, with a furniture section
    @param number: optional per-property furniture override
    @param profile: already loaded profile
    @returns: value/layer/open resolution with inference provenance and evidence ids
    """
    import provenance as prov

    profile = load_district(cell) if profile is None else profile
    prop, street = _layers(conv, number)
    furniture = ((profile or {}).get('classes', {}).get(cls, {}).get('furniture') or {})
    result = land_use.resolve(cls, 'bench_per_100m',
                              property=(prop or {}).get('furniture'), street=(street or {}).get('furniture'),
                              district=furniture)
    ids = (profile or {}).get('evidence_ids', ()) if result['layer'] == 'district' else (conv or {}).get('evidence_ids', ())
    return {**result, 'provenance': prov.provenance_record(source='inference', confidence='low', evidence_ids=ids)}


def _layers(conv: dict | None, number: str) -> tuple[dict | None, dict | None]:
    """(per-house override, street convention) layers for ``land_use.resolve``."""
    if not conv:
        return None, None
    return (conv.get('overrides') or {}).get(str(number)), conv


def role_resolution(conv: dict | None, number: str, role: str, cls: str = 'residential',
                    row: dict | None = None, district: dict | None = None) -> dict:
    """Resolve a boundary role and deterministically sample whole district-inferred frontages."""
    prop, street = _layers(conv, number)
    result = land_use.resolve(cls, 'boundary', role=role, property=prop, street=street, district=district, row=row)
    if result['layer'] == 'district' and result['value']:
        spec = result['value']
        identity = json.dumps([district.get('name'), district.get('street'), str(number), cls, role])
        share = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], 'big') / 2**64
        if share >= spec.get('density', 1):
            result = {**result, 'value': None}     # missing inference, never an observation/veto
    return result


def role_spec(conv: dict | None, number: str, role: str, cls: str = 'residential',
              row: dict | None = None, district: dict | None = None) -> dict | None:
    """Barrier spec for one role: override, street convention, district, class default, else None.

    The class default is null in every shipped row, so this is the old override-then-street answer
    unless a district or class row carries a boundary for ``role``.
    """
    res = role_resolution(conv, number, role, cls, row, district)
    spec = res['value']
    if not spec or res['open']:
        return None
    t = spec['type']
    if t not in bv.CONVENTION_BLOCK:              # unknown type: keep it so the plan can report it
        return {'type': t, 'block': spec.get('block') or '',
                'height': int(spec.get('height') or 1), 'depth': spec.get('depth')}
    block = spec.get('block') or bv.CONVENTION_BLOCK[t]
    plank = bv.barrier_kind(t, block) == 'plank_fence'
    return {'type': t, 'block': block,
            'height': int(spec.get('height') or (bv.PLANK_HEIGHT if plank else bv.CONVENTION_HEIGHT[t])),
            'depth': spec.get('depth')}


OPENING_WIDTH = 2        # columns of front boundary left open at an entrance without drive evidence
MAX_OPENING_WIDTH = 4    # cap on an opening widened to a paved strip (#270)
FRONT_GARDEN_MARGIN = 6  # plot columns this far or nearer to the house line count as front garden


def meta_class(meta: dict) -> str:
    """Land-use class id of a plot: ``meta['land_use']`` (dict from the classifier, or an id), else
    residential (today's default for a house plot)."""
    lu = meta.get('land_use')
    if isinstance(lu, dict):
        return lu.get('class') or 'unknown'
    if lu:
        return str(lu)
    return 'residential'


def _front_extent(meta: dict, plot_cells) -> list:
    """Plot columns of the front extent: more than FRONT_GARDEN_MARGIN from the house line, street side."""
    foot, centre = meta.get('foot'), meta.get('centre')
    if not foot or not centre:
        return []
    fx, fz = foot
    ax, az = centre[0] - fx, centre[1] - fz
    n = math.hypot(ax, az) or 1.0
    ax, az = ax / n, az / n
    return sorted(q for q in plot_cells if ((q[0] - fx) * ax + (q[1] - fz) * az) < n - FRONT_GARDEN_MARGIN)


def _pavement_strip(meta: dict, front, width: float) -> list:
    """Columns of public pavement: the strip just outside the front boundary, toward the street."""
    fx, fz = meta['foot']
    ax, az = meta['centre'][0] - fx, meta['centre'][1] - fz
    ox, oz = -ax, -az
    step = (1 if ox > 0 else -1, 0) if abs(ox) >= abs(oz) else (0, 1 if oz > 0 else -1)
    out = {(q[0] + step[0] * k, q[1] + step[1] * k) for q in front for k in range(1, max(1, int(width)) + 1)}
    return sorted(out - set(map(tuple, front)))


def frontage_inference_reason(meta: dict, role_cells: dict, row: dict) -> str:
    """Why this plot cannot use class/district inference, or an empty string when eligible."""
    cls, fr = meta_class(meta), row['frontage']
    if cls not in land_use.CLASSES or not fr['inference'] or cls in land_use.NO_INFERENCE:
        return f'land-use class {cls} allows no frontage inference'
    requires = fr['requires']
    if 'identified_property' in requires and (not meta.get('street') or not meta.get('number')):
        return 'property has no street and house number'
    if 'front_segment' in requires and (not role_cells.get('front') or not meta.get('foot')):
        return 'no street-run frontage evidence'
    if 'perimeter_segment' in requires and not (role_cells.get('front') or role_cells.get('perimeter')):
        return 'no street-facing parcel edge'
    return '' if meta.get('foot') else 'no street-run frontage evidence'


def frontage(meta: dict, plot_cells, role_cells: dict, house_cells, conv: dict | None,
             *, door: tuple[int, int] | None = None, approach: dict | None = None,
             evidence_ids=(), boundary_cells=None, row: dict | None = None,
             paved=None, district: dict | None = None) -> dict:
    """One shared description of a property's private frontage, any land-use class (#240, #254).

    The class comes from ``meta['land_use']`` (#253 classifier); ``row`` overrides the table row
    (synthetic tests). Residential is the residential row: inference needs an identified property
    and a ``front`` segment. Other classes read their own row: ``requires`` may name
    ``perimeter_segment`` (a ``front`` or ``perimeter`` edge); ``private_surface`` says what guessed
    ground in the front extent becomes (``grass`` and ``keep`` leave lawn, ``paving`` lets ground
    pave it); ``opening`` says how the entrance gap is reserved (``door_route``, ``street_gate``,
    ``none``). A class that allows no inference (rural, natural, water, unknown) is never eligible.
    Per field, ``land_use.resolve`` picks the layer (per-house override, street, district, class);
    its winner for the front boundary goes into ``scope['layer']``.

    Ground reads ``extent`` (private frontage, not public pavement), ``pavement`` (the public strip
    outside the front boundary), ``forecourt`` and ``route``; decor reads ``boundary`` roles and
    ``opening``. Pure: callers pass the plot, its role segments and house.

    Eligibility (inference only): an identified residential property (street and number) with
    street-run evidence (a ``front`` segment). A missing convention alone never authorises a fence;
    ``front`` set to ``none`` in the convention (or an override) is an explicit open frontage.
    Entrance: ``door`` is an observed door column; without it the nearest house column to the street
    foot is used and labelled ``inferred`` (a plausible entrance, never a verified connection).

    @param meta: ``{street, number, centre, foot, map_ref, land_use}`` plot info from ``plots_for``
    @param plot_cells: columns of the plot outside footprints
    @param role_cells: ``{role: [cells]}`` plot edge segments from ``segments``
    @param house_cells: footprint columns of this property's house
    @param conv: street convention or None
    @param door: observed door column, if known
    @param approach: ``entrance_approach`` dict of a landed feature (door, steps, outward connection);
        the route starts at its connection, so ground joins the outer end of the feature's steps and
        never duplicates them. Its door is a built door (``generated`` position, not observed)
    @param evidence_ids: registry evidence ids backing the property
    @param row: table row replacing the class's own (tests)
    @param district: flattened boundary layer selected from the profile for this property
    @param paved: aerial-paved columns (None = no aerial read: the opening keeps ``OPENING_WIDTH``);
        otherwise the opening is widened to the paved strip meeting the front boundary, capped at
        ``MAX_OPENING_WIDTH`` and labelled inference in ``opening_provenance`` (#270)
    @returns: dict with eligible, reason, extent, boundary, opening, route, door, connection, provenance,
        land_use, private_surface, pavement, forecourt, surface_keep
    """
    import provenance as prov

    street, number = meta.get('street') or '', str(meta.get('number') or '')
    cls = meta_class(meta)
    row = row or land_use.defaults(cls)
    fr = row['frontage']
    prop, street_layer = _layers(conv, number)
    surface = land_use.resolve(cls, 'private_surface', property=prop, street=street_layer, row=row)['value']
    opening_kind = land_use.resolve(cls, 'opening', property=prop, street=street_layer, row=row)['value']
    front_res = role_resolution(conv, number, 'front', cls, row, district)
    out = {'id': f'frontage-{slug(street)}-{number}' if street and number else 'frontage-unidentified',
           'scope': {'street': street, 'property': number, 'land_use': cls,
                     'layer': front_res['layer'] or 'class'},
           'eligible': False, 'reason': '', 'land_use': cls, 'private_surface': surface,
           'extent': [], 'boundary': [], 'open_frontage': False, 'opening': [], 'route': [],
           'pavement': [], 'forecourt': [], 'surface_keep': [],
           'door': None, 'connection': {'status': 'none', 'reason': ''}, 'surface': 'private_frontage'}
    front = list(role_cells.get('front') or ())
    foot = meta.get('foot')
    out['reason'] = frontage_inference_reason(meta, role_cells, row)
    if out['reason']:
        if surface == 'keep':
            out['surface_keep'] = _front_extent(meta, plot_cells)
        out['provenance'] = prov.provenance_record(
            source='inference', confidence='low', evidence_ids=evidence_ids, unresolved_reason=out['reason'])
        return out
    out['eligible'] = True
    fx, fz = foot
    ax, az = meta['centre'][0] - fx, meta['centre'][1] - fz
    n = math.hypot(ax, az) or 1.0
    ax, az = ax / n, az / n
    house = set(house_cells)
    out['extent'] = _front_extent(meta, plot_cells)
    if surface == 'keep':
        out['surface_keep'] = list(out['extent'])
    else:
        out['forecourt'] = list(out['extent'])
    out['pavement'] = _pavement_strip(meta, front, land_use.pavement_width(cls, row)) if front else []
    out['open_frontage'] = front_res['open'] and not (front_res['layer'] == 'district' and boundary_cells)
    spec = role_spec(conv, number, 'front', cls, row, district)
    if spec and front_res['layer'] == 'class':
        out['scope']['layer'] = 'class'
    boundary = list(front if spec else (boundary_cells or ()))
    out['boundary'] = sorted(boundary) if not out['open_frontage'] else []
    if opening_kind != 'door_route':
        gate = []
        if opening_kind == 'street_gate' and out['boundary'] and front:
            target = min(front, key=lambda q: (math.hypot(q[0] - fx, q[1] - fz), q))
            near = sorted((q for q in front if q != target and max(abs(q[0] - target[0]), abs(q[1] - target[1])) <= 1),
                          key=lambda q: (math.hypot(q[0] - target[0], q[1] - target[1]), q))
            gate = sorted([target] + near[:OPENING_WIDTH - 1])
        out['opening'] = gate
        out['connection'] = {'status': 'none', 'reason': f'{opening_kind}: no door route for class {cls}'}
        out['provenance'] = prov.provenance_record(
            source='inference', confidence='low', evidence_ids=evidence_ids)
        return out
    observed = door is not None
    built = approach is not None and approach.get('connection') is not None
    if built:
        door = tuple(approach['door']['cell'])
        observed = approach['door']['origin'] == 'observed'
    if door is None and house:
        door = min(house, key=lambda q: (math.hypot(q[0] - fx, q[1] - fz), q))
    if door is None:
        out['connection'] = {'status': 'unavailable', 'reason': 'no house columns to place an entrance'}
        out['provenance'] = prov.provenance_record(
            source='vector_map', confidence='medium', evidence_ids=evidence_ids)
        return out
    out['door'] = {'cell': list(door),
                   'origin': ('observed' if observed else 'generated') if built
                   else ('observed' if observed else 'inferred')}
    if built:
        out['approach'] = approach
    ox, oz = -ax, -az                                     # outward: house -> street
    if abs(ox) >= abs(oz):
        step = (1 if ox > 0 else -1, 0)
    else:
        step = (0, 1 if oz > 0 else -1)
    start = tuple(approach['connection']['cell']) if built else (door[0] + step[0], door[1] + step[1])
    if built:
        out['clearance'] = [list(c) for c in (approach.get('clearance') or {}).get('columns', ())]
    target = min(front, key=lambda q: (math.hypot(q[0] - start[0], q[1] - start[1]), q))
    route = [q for q in line4(start, target)]
    blocked = [q for q in route if q in house]
    if blocked:
        out['connection'] = {'status': 'blocked', 'pass': 'ground', 'endpoints': [list(start), list(target)],
                             'reason': 'route crosses the house; the house is not altered to force access'}
    else:
        out['route'] = route
        near = sorted((q for q in front if q != target and max(abs(q[0] - target[0]), abs(q[1] - target[1])) <= 1),
                      key=lambda q: (math.hypot(q[0] - target[0], q[1] - target[1]), q))
        if not out['boundary']:
            out['opening'] = []
        elif paved is None:
            out['opening'] = sorted([target] + near[:OPENING_WIDTH - 1])
            out['opening_evidence'] = 'default_width'
        else:
            out['opening'], out['opening_evidence'] = widen_opening(
                target, front, plot_cells, paved, route, house=house, inward=(ax, az))
            out['opening_provenance'] = prov.provenance_record(
                source='inference', confidence='low', evidence_ids=evidence_ids,
                unresolved_reason=(f'opening widened to the aerial paved strip ({len(out["opening"])} columns,'
                                   f' cap {MAX_OPENING_WIDTH}): it continues past the side of the house;'
                                   ' inferred, not a surveyed drive')
                if out['opening_evidence'] == 'side_passage' else
                'door route only: no drive evidence (paving up to the boundary is not a drive)')
        out['connection'] = {
            'status': 'verified' if observed else ('built' if built else 'inferred'),
            'endpoints': [list(start), list(target)],
            'reason': '' if observed else (
                'joins a built door whose position was generated, not observed' if built
                else 'entrance inferred from the street foot, not a mapped door')}
    out['provenance'] = prov.provenance_record(
        source='vector_map' if observed else 'inference',
        confidence='medium' if (observed or built) else 'low', evidence_ids=evidence_ids)
    return out


def residential_frontage(meta: dict, plot_cells, role_cells: dict, house_cells, conv: dict | None, **kw) -> dict:
    """Old name of :func:`frontage`, kept for callers; a plot without a class is residential."""
    return frontage(meta, plot_cells, role_cells, house_cells, conv, **kw)


def frontages_for_cell(cell: tuple[int, int]) -> list:
    """Shared frontage descriptions for the plots touching ``cell`` (same plots/segments as the garden pass)."""
    box = cellmath.bounds(cell)
    owner, info, street, idx = plots_for(box)
    try:
        approaches = landed_approaches(cell)
    except Exception:                                      # no journal (tests, fresh world): inferred doors
        approaches = []
    return frontages_for_plots(owner, info, segments(owner, info, street, idx, box), idx,
                               approaches=approaches, profile=load_district(cell))


def paved_columns(rgb: dict | None) -> set:
    """Columns whose aerial colour reads as hard paving (brick, light or dark neutral), not lawn or shade."""
    if not rgb:
        return set()
    from ground_sources import aerial_class
    return {tuple(q) for q, c in rgb.items() if aerial_class(c) in ('brick', 'light', 'dark')}


def widen_opening(target, front, plot_cells, paved, route=(), cap: int = MAX_OPENING_WIDTH,
                  house=(), inward=None) -> tuple[list, str]:
    """Front-boundary columns of a drive that meets the boundary at ``target`` (#270, #275).

    A front column belongs to the strip when it, or the plot column just inside it, is paved
    (aerial) or on the door route. The strip is the chain of such columns, each within one column
    of the last, that contains ``target``; it is clipped to the ``cap`` columns nearest the target.
    Only this plot's own front columns are used, so a neighbour's plot line is never crossed.

    The strip only opens wider than the door when it is drive-like (#275). A forecourt paved up
    to the pavement is not a drive, so without evidence the opening is the door column alone.
    Evidence ``side_passage``: the paved ground connected to the strip runs past the house's front
    face and hugs the house side, and is no wider than ``cap`` columns where it passes the house
    (a drive between buildings, not a paved front garden). It needs ``house`` and ``inward``
    (unit vector from the street towards the house).

    @returns: ``(columns, evidence)``; evidence is ``side_passage`` or ``door_route`` (door only)
    """
    own = {tuple(q) for q in plot_cells}
    hard = {tuple(q) for q in paved} | {tuple(q) for q in route}
    target = tuple(target)

    edge = {tuple(q) for q in front}

    def hit(q):      # the column itself, or the plot column just inside it (not sideways along the front)
        return q in hard or any((q[0] + a, q[1] + b) in hard and (q[0] + a, q[1] + b) in own
                                and (q[0] + a, q[1] + b) not in edge
                                for a, b in cellmath.N4)
    cand = {tuple(q) for q in front if hit(tuple(q))} | {target}
    chain, todo = {target}, [target]
    while todo:
        c = todo.pop()
        for q in cand - chain:
            if max(abs(q[0] - c[0]), abs(q[1] - c[1])) <= 1:
                chain.add(q)
                todo.append(q)
    if not side_passage(chain, own, {tuple(q) for q in paved}, house, inward, target, cap):
        return [target], 'door_route'
    keep = sorted(chain, key=lambda q: (math.hypot(q[0] - target[0], q[1] - target[1]), q))[:cap]
    return sorted(keep), 'side_passage'


def side_passage(strip, own, paved, house, inward, origin, cap: int = MAX_OPENING_WIDTH) -> bool:
    """True when paved ground connected to ``strip`` continues back past the side of the house (#275).

    Depth is measured along ``inward`` from ``origin`` on the front line. Paved plot columns
    (from the strip, bridging gaps of one column, never houses) count as passage when they lie more than a column
    beyond the house's front face and touch the house side; the passage is a drive only when
    the paving beside the house there is at most ``cap`` columns wide (one column of slack for a
    diagonal edge), so a paved garden wrapping the house is not read as a drive.
    """
    if not house or inward is None:
        return False
    house = {tuple(q) for q in house}
    ax, az = inward

    def depth(q):
        return (q[0] - origin[0]) * ax + (q[1] - origin[1]) * az
    face = min(depth(q) for q in house)
    ground = {q for q in paved if q in own and q not in house}
    todo = list(strip)
    reach = set(strip)
    while todo:
        c = todo.pop()
        for a in (-2, -1, 0, 1, 2):          # two columns: aerial paving breaks (cars, shade) must not hide a drive
            for b in (-2, -1, 0, 1, 2):
                n = (c[0] + a, c[1] + b)
                if n in ground and n not in reach:
                    reach.add(n)
                    todo.append(n)

    def beside(q):
        return any((q[0] + a, q[1] + b) in house for a in (-1, 0, 1) for b in (-1, 0, 1))
    band = {q for q in reach if face + 1 <= depth(q) < face + 2.5}
    left = set(band)
    while left:
        comp, todo = set(), [left.pop()]
        comp.add(todo[0])
        while todo:
            c = todo.pop()
            for a in (-1, 0, 1):
                for b in (-1, 0, 1):
                    n = (c[0] + a, c[1] + b)
                    if n in left:
                        left.discard(n)
                        comp.add(n)
                        todo.append(n)
        if any(beside(q) for q in comp):
            lat = [(q[0] - origin[0]) * -az + (q[1] - origin[1]) * ax for q in comp]
            if max(lat) - min(lat) + 1 <= cap + 1:       # the passage, not a paved garden wrapping the house
                return True
    return False


def frontage_route_columns(frontages) -> set:
    """Columns of eligible onward routes (ground surfaces guessed ground there as path)."""
    return {tuple(q) for f in frontages if f.get('eligible') for q in f.get('route', ())}


def reserved_route(frontages) -> set:
    """Route plus feature-approach clearance columns decor must leave walkable and open overhead."""
    out = set()
    for f in frontages:
        if f.get('eligible'):
            out.update(tuple(q) for q in f.get('route', ()))
            out.update(tuple(q) for q in f.get('clearance', ()))
    return out


def blocked_connections(frontages) -> list:
    """Report rows for connections that could not be made (endpoints, responsible pass, reason)."""
    return [{'frontage': f['id'], **f['connection']} for f in frontages
            if (f.get('connection') or {}).get('status') == 'blocked']


def landed_approaches(cell: tuple[int, int], registry=None, footprint=None) -> list:
    """Entrance approaches reconstructed read-only from the cell's landed, not undone feature plans.

    A plan with no recorded plan blocks, or no door block, yields nothing: its endpoint is unavailable
    and the frontage falls back to a labelled inferred entrance. Never writes to the registry.
    """
    from entrance_approach import entrances_from_blocks

    if registry is None:
        from cell_registry import view_registry
        registry = view_registry()
    if footprint is None:
        from osm_footprints import OSMIndex
        footprint = OSMIndex().cells()
    out = []
    seen = set()
    for e in reversed(registry.entries(cells=[tuple(cell)])):
        if e.kind != 'build' or e.pass_ != 'feature' or e.status != 'landed' or e.undone:
            continue
        if e.plan in seen:
            continue
        seen.add(e.plan)
        blocks = registry.recorded(e.plan)
        if blocks:
            out.extend(entrances_from_blocks(blocks, footprint=footprint))
    return out


def private_frontage_columns(frontages) -> set:
    """Columns where ground keeps guessed lawn instead of paving it (#254).

    Eligible frontage whose class keeps lawn (``private_surface`` ``grass`` or ``keep``; a missing
    value counts as ``grass``, the residential row), plus the front extent of classes that say
    ``keep`` but allow no inference (park, rural, natural, water). ``paving`` classes are not
    listed, so ground paves their guessed lawn.
    """
    out = {tuple(q) for f in frontages if f.get('eligible') and f.get('private_surface', 'grass') != 'paving'
           for q in f.get('extent', ())}
    out |= {tuple(q) for f in frontages for q in f.get('surface_keep', ())}
    return out


def forecourt_columns(frontages) -> set:
    """Columns of private forecourt (eligible frontage extent) where seen paving gets its own surface."""
    return {tuple(q) for f in frontages if f.get('eligible') for q in f.get('forecourt', ())}


def pavement_columns(frontages) -> set:
    """Columns of public pavement: the per-class strip just outside each eligible front boundary."""
    return {tuple(q) for f in frontages if f.get('eligible') for q in f.get('pavement', ())}


def reserved_openings(frontages) -> set:
    """Columns reserved for entrance gaps in front boundaries (decor and final joins keep them clear)."""
    return {tuple(q) for f in frontages for q in f.get('opening', ())}


def tree_canopy_columns(cell: tuple[int, int], maps=None) -> set[tuple[int, int]]:
    """Columns whose lidar heightmap kind is tree / tree-over-roof (nDSM canopy mask).

    @param cell: ``(tx, tz)``
    @param maps: optional ``lidar_heightmap.Maps`` (tests); default is the shared lidar Maps
    @returns: set of ``(x, z)`` canopy columns; empty when lidar is unavailable
    """
    import numpy as np
    import lidar_heightmap as L

    try:
        hm = (maps or L.Maps()).of(cell)
    except Exception:
        return set()
    kind = hm['kind']
    x0, z0 = int(hm['x0']), int(hm['z0'])
    out: set[tuple[int, int]] = set()
    for i, j in zip(*np.nonzero((kind == L.TREE) | (kind == L.TREE_OVER_ROOF))):
        out.add((x0 + int(i), z0 + int(j)))
    return out


def plots_for(box: tuple[int, int, int, int], margin: int = 60):
    """Plots touching ``box``: ({cell: plot id}, {plot id: info}, set of street columns)."""
    from ground_sources import ROAD_CLASSES, line_cells, road_width
    from osm_footprints import OSMIndex, _addr, grow_plots

    from coverage import area_classes, building_land_use

    x0, z0, x1, z1 = box
    idx = OSMIndex()
    fp = idx.cells()
    classes = area_classes(idx, (x0 - margin, z0 - margin, x1 + margin, z1 + margin))
    seeds = {q: i for q, i in fp.items() if x0 - margin <= q[0] <= x1 + margin and z0 - margin <= q[1] <= z1 + margin}
    # street = carriageway plus a pavement either side (the front boundary sits at its back edge).
    # Plots grow before their class is known, so the band uses the widest class pavement in the table.
    pavement = max(land_use.pavement_width(c) for c in land_use.CLASSES)
    street: dict = {}
    for (name, hw, pts), props in zip(idx.streets, idx.street_props):
        if hw in ROAD_CLASSES and any(x0 - margin <= x <= x1 + margin and z0 - margin <= z <= z1 + margin
                                      for x, z in pts):
            for q in line_cells(pts, road_width(props) / 2 + pavement):
                street[q] = True

    def is_street(x, z):
        return street.get((x, z), False)

    owner = grow_plots(seeds, lambda x, z: (x, z) not in fp and not is_street(x, z))
    info = {}
    for i in set(owner.values()):
        rings, p = idx.buildings[i]
        cx = sum(x for x, _ in rings[0]) / len(rings[0])
        cz = sum(z for _, z in rings[0]) / len(rings[0])
        num, st = _addr(p)
        s = idx.nearest_street(cx, cz)
        info[i] = {'street': st or (s[1] if s else ''), 'number': num or '', 'centre': (cx, cz),
                   'foot': s[3] if s else None, 'map_ref': p.get('osm_id') or p.get('osm_way_id'),
                   'land_use': building_land_use(idx, rings, p, classes)}
    return owner, info, street, idx


def segments(owner: dict, info: dict, street: dict, idx, box) -> dict:
    """{(plot id, role): [cells]} for plot edge columns (outside footprints) inside ``box``."""
    x0, z0, x1, z1 = box
    fp = idx.cells()
    seg = defaultdict(list)
    for (x, z), i in owner.items():
        if (x, z) in fp or not (x0 <= x <= x1 and z0 <= z <= z1):
            continue
        meta = info[i]
        fx, fz = (meta['foot'] or (x, z))
        ax, az = meta['centre'][0] - fx, meta['centre'][1] - fz       # street -> house axis
        n = math.hypot(ax, az) or 1.0
        ax, az = ax / n, az / n
        for dx, dz in cellmath.N4:
            q = (x + dx, z + dz)
            if q in fp or owner.get(q) == i:
                continue
            if street.get(q):
                role = 'front'
            elif q in owner:
                if meta_class(meta) not in ('residential', 'unknown'):
                    role = 'perimeter'                                # a non-house parcel has no front/side/rear
                elif abs(dx * ax + dz * az) > math.sqrt(0.5):
                    role = 'rear'
                else:       # side: in front of the house line (front garden) or behind it
                    role = 'side_front' if ((x - fx) * ax + (z - fz) * az) < n - 6 else 'side_back'
            else:
                continue                                              # open edge: no evidence
            seg[(i, role)].append((x, z))
    return seg


def walk(group) -> list:
    """Order a group's columns along itself: start at an end, then always step to the nearest unvisited."""
    pts = set(group)
    near = lambda p: sum((p[0] + a, p[1] + b) in pts for a in (-1, 0, 1) for b in (-1, 0, 1) if a or b)
    cur = min(pts, key=lambda p: (near(p), p))
    path = [cur]
    pts.remove(cur)
    while pts:
        cur = min(pts, key=lambda p: ((p[0] - cur[0]) ** 2 + (p[1] - cur[1]) ** 2, p))
        path.append(cur)
        pts.remove(cur)
    return path


def simplify(path, tol: float = 1.2) -> list:
    """Douglas-Peucker: the few corner points a boundary really turns at."""
    if len(path) < 3:
        return list(path)
    (ax, az), (bx, bz) = path[0], path[-1]
    n = math.hypot(bx - ax, bz - az) or 1.0
    dist = [abs((bx - ax) * (az - z) - (ax - x) * (bz - az)) / n for x, z in path]
    k = max(range(len(path)), key=dist.__getitem__)
    if dist[k] <= tol:
        return [path[0], path[-1]]
    return simplify(path[:k + 1], tol)[:-1] + simplify(path[k:], tol)


def line4(a, b) -> list:
    """4-connected columns of the straight line a-b (every column the line crosses, no diagonal gaps)."""
    (x0, z0), (x1, z1) = a, b
    dx, dz = abs(x1 - x0), abs(z1 - z0)
    sx, sz = (1 if x1 > x0 else -1), (1 if z1 > z0 else -1)
    x, z, out = x0, z0, [(x0, z0)]
    ix = iz = 0
    while ix < dx or iz < dz:
        if (1 + 2 * ix) * dz == (1 + 2 * iz) * dx:      # exactly through a corner: step both
            x, z, ix, iz = x + sx, z + sz, ix + 1, iz + 1
            out.append((x - sx, z))
        elif (1 + 2 * ix) * dz < (1 + 2 * iz) * dx:
            x, ix = x + sx, ix + 1
        else:
            z, iz = z + sz, iz + 1
        out.append((x, z))
    return out


def straighten(cells) -> list:
    """Replace a rasterised plot edge by straight, 4-connected runs between its corner points."""
    out = []
    for g in map(sorted, cellmath.components(cells, cellmath.N8)):
        pts = simplify(walk(g)) if len(g) > 1 else g
        for a, b in zip(pts, pts[1:]):
            out += line4(a, b)
        if len(pts) == 1:
            out += pts
    return out


def hedge_share(cells: list, rgb: dict, canopy: set | None = None) -> float:
    """Share of non-canopy cells whose aerial colour is dark saturated green (hedge/shrub strip).

    Tree-canopy columns (nDSM / lidar tree mask) are excluded so woodland crowns are not
    read as garden hedges.

    @param cells: candidate boundary columns
    @param rgb: ``{(x, z): (r, g, b)}`` aerial samples
    @param canopy: columns to ignore (tree canopy); None means ignore none
    @returns: fraction in ``[0, 1]``, or 0 when no scorables remain
    """
    from ground_sources import aerial_class
    skip = canopy or ()
    seen = [aerial_class(rgb[q]) for q in cells if q in rgb and q not in skip]
    return (sum(1 for c in seen if c == 'shade') / len(seen)) if seen else 0.0


PUBLIC_ROLES = ('front', 'side_front')


def outward_facings(cols, plot_id, role: str, owner: dict, centre) -> tuple[dict, dict]:
    """Outward direction of each boundary column, so a plank panel (shelf) can face the right way.

    A shelf's back board sits flush on the edge opposite its facing, so ``out`` here means the board
    lands on the inner edge of the boundary column and its open front looks to the street / neighbour.
    Boundary columns are the plot's own outermost columns (``segments``), so the board stands about one
    block inside the true plot line (see docs/plans/244-boundary-evidence-findings.md, #272).

    "Out" is the cardinal neighbour that is not this plot's own land. Preference, per column:

    1. ``public``: an unowned neighbour (street, pavement, path, verge). Front and side-front boundaries
       always have one where the plot meets the street.
    2. ``neighbour``: a neighbour owned by another plot, used only when no public side exists.
       Fallback rule, labelled ``fallback: private-private``: face away from the plot that owns the
       fence (its better side shows to the neighbour). Deterministic, and independent of plot ids.
    3. ``fallback: no outside neighbour``: every neighbour is this plot's own (a sliver); face away
       from the plot centre.

    Ties (corner columns with two public sides) take the side most aligned with centre -> column, then
    the fixed order east, west, south, north. A column lying outside the plot (just beyond the line)
    treats the direction away from its own-plot neighbour as out.

    @param cols: boundary columns of one plot role
    @param plot_id: id of the plot that owns the fence
    @param role: boundary role (``front``, ``side_back``, ...); kept in the basis label
    @param owner: ``{column: plot id}`` (houses included)
    @param centre: plot centre ``(x, z)``
    @returns: ``({'x,z': direction}, {'x,z': basis label})``
    """
    facing, basis = {}, {}
    for q in cols:
        q = (int(q[0]), int(q[1]))
        own = owner.get(q) == plot_id
        order = list(cellmath.FACING.items())
        order.sort(key=lambda kv: -((kv[1][0] * (q[0] - centre[0])) + (kv[1][1] * (q[1] - centre[1]))))
        cands = []                                         # (direction, owner of the square across)
        for name, (a, b) in order:
            n = (q[0] + a, q[1] + b)
            if own:
                if owner.get(n) != plot_id:
                    cands.append((name, owner.get(n)))
            elif owner.get((q[0] - a, q[1] - b)) == plot_id:   # the far side from our own land
                cands.append((name, owner.get(n)))
        if not cands:
            cands = [(name, owner.get((q[0] + a, q[1] + b))) for name, (a, b) in order
                     if owner.get((q[0] + a, q[1] + b)) != plot_id]
        pub = [c for c in cands if c[1] is None]
        if pub:
            d = pub[0][0]
            why = 'street' if role in PUBLIC_ROLES else 'public'
        elif cands:
            d, why = cands[0][0], 'fallback: private-private (faces away from fence owner)'
        else:
            d, why = order[0][0], 'fallback: no outside neighbour (faces away from plot centre)'
        facing[f'{q[0]},{q[1]}'] = d
        basis[f'{q[0]},{q[1]}'] = why
    return facing, basis


PANEL_DRIFT = 2   # a panel run holds its row until the boundary has drifted this many columns (#276)


def flatten_panel_run(cols, plot_id, owner: dict, avoid=(), clear=(), tol: int = PANEL_DRIFT) -> list:
    """Straighten a panel run so it steps only when the boundary drifts ``tol`` or more columns.

    The boundary of a plot follows a pixel-jagged edge, so ``straighten`` leaves a staircase with a
    one-row step every couple of columns, and every step needs a sideways closer shelf. Here each
    straight stretch (the corner points ``simplify`` finds) is refit: along its dominant axis ``u``
    the outermost boundary column of each ``u`` is ``o(u)``; the run holds one row ``g`` for as long as
    all those ``o`` lie within ``tol - 1`` of each other, with ``g`` the innermost of them, then jogs
    to the next row. So the fence is never outside the boundary (never on the pavement or in a
    neighbour's land) and never more than ``tol - 1`` columns inside it; a jog appears only once the
    line has drifted ``tol`` columns. Jog columns are placed where they stay on the plot's own land.

    A stretch keeps its original cells when its inner side cannot be told from the owners, when a fitted
    column is not the plot's own (original cells excepted) or lies on ``avoid`` (house footprint), or when
    it is shorter than 3 columns or steeper than about 31 degrees. Columns where an original cell lies
    on ``clear`` (reserved openings) get no fitted cell, so openings stay open. The corner points
    between stretches are kept so stretches stay joined.

    @param cols: boundary columns of one plot role (4-connected runs from ``straighten``)
    @param plot_id: plot that owns the fence
    @param owner: ``{column: plot id}``
    @param avoid: columns never fitted onto
    @param clear: reserved opening columns
    @param tol: drift in columns at which the run may step (2: at most 1 column inside the boundary)
    @returns: the fitted columns, unordered, free of duplicates
    """
    avoid, clear = set(avoid), set(clear)
    out: set = set()
    for comp in sorted(map(sorted, cellmath.components(
            {(int(q[0]), int(q[1])) for q in cols}, cellmath.N8)), key=lambda g: g[0]):
        if len(comp) < 4:
            out.update(comp)
            continue
        path = walk(comp)
        pts = simplify(path)
        idx = [path.index(p) for p in pts]
        for n, (i, j) in enumerate(zip(idx, idx[1:])):
            seg = path[i:j + 1]
            fitted = _fit_stretch(seg, plot_id, owner, avoid, clear, tol)
            out.update(fitted if fitted is not None else seg)
            if fitted is not None:                               # corner points between stretches stay, so they stay joined
                out.update(q for q in (seg[0] if n > 0 else None, seg[-1] if j < idx[-1] else None) if q)
    return sorted(out)


def _fit_stretch(seg, plot_id, owner, avoid, clear, tol):
    """Fitted columns for one straight stretch, or None to keep the original cells."""
    xs = [q[0] for q in seg]
    zs = [q[1] for q in seg]
    ux = (max(xs) - min(xs)) >= (max(zs) - min(zs))
    major = max(xs) - min(xs) if ux else max(zs) - min(zs)
    minor = max(zs) - min(zs) if ux else max(xs) - min(xs)
    if major < 2 or minor * 10 > major * 6:                      # short, or steeper than about 31 degrees
        return None
    pos = (lambda u, v: (u, v)) if ux else (lambda u, v: (v, u))
    uv = [(q[0], q[1]) if ux else (q[1], q[0]) for q in seg]
    own = lambda u, v: owner.get(pos(u, v)) == plot_id
    plus = sum(own(u, v + 1) and not own(u, v - 1) for u, v in uv)
    minus = sum(own(u, v - 1) and not own(u, v + 1) for u, v in uv)
    if plus == minus:
        return None
    s = 1 if plus > minus else -1
    outer: dict = {}
    for u, v in uv:
        outer[u] = min(outer.get(u, s * v), s * v)
    us = sorted(outer)
    if us != list(range(us[0], us[-1] + 1)):
        return None
    blocked = {u for u, v in uv if pos(u, v) in clear}
    windows, cur = [], [us[0]]
    for u in us[1:]:
        vals = [outer[k] for k in cur] + [outer[u]]
        if max(vals) - min(vals) <= tol - 1:
            cur.append(u)
        else:
            windows.append(cur)
            cur = [u]
    windows.append(cur)
    hold = [max(outer[u] for u in w) for w in windows]
    cells: set = set()
    for w, g in zip(windows, hold):
        cells.update((u, g) for u in w if u not in blocked)
    for k in range(len(windows) - 1):                            # vertical jog between two holds
        g1, g2 = hold[k], hold[k + 1]
        for u in (windows[k][-1], windows[k + 1][0]):
            if min(g1, g2) >= outer[u]:                                 # in an opening column the later clearance removes it
                cells.update((u, v) for v in range(min(g1, g2), max(g1, g2) + 1))
                break
        else:
            return None
    fitted = {pos(u, s * v) for u, v in cells}
    keep = {(q[0], q[1]) for q in seg}                           # the straightened line may sit on the plot edge
    if any(q in avoid or not (q in keep or own(*((q[0], q[1]) if ux else (q[1], q[0])))) for q in fitted):
        return None
    return fitted


def panel_runs(cols, plot_id, role: str, owner: dict, street, centre, avoid=(), clear=(), fit: bool = True) -> dict:
    """One outward facing per panel run, and closing pieces so each run is continuous.

    ``outward_facings`` answers per column, which on a diagonal boundary (about 1 z per 2 x) flips
    between two axes along one run. Here a run is an 8-connected group of boundary columns. Its
    facing is the dominant axis of the run's outward normal (principal direction of the columns,
    sign from the summed per-column outward votes); a near tie between axes (about 45 degrees) goes
    to the larger vote, then the fixed order east, west, south, north.

    A shelf's back board lies on the column edge opposite its facing, so on a staircase the boards
    of neighbouring rows are a block apart. The column of a step that lies on the inner side (opposite
    the facing) becomes a thin cross shelf (``_closer_facing``) whose board runs across the step and
    ties the two board lines together. Where the run only touches at a corner (8-connected), such a
    cross shelf is added in the inner candidate column (the other candidate if that one is blocked,
    nothing if both are). Never added: street columns or columns owned by another plot.

    @param cols: boundary columns of one plot role
    @param plot_id: plot that owns the fence
    @param role: boundary role
    @param owner: ``{column: plot id}``
    @param street: columns of road, pavement and path (never closed over)
    @param centre: plot centre ``(x, z)``
    @param avoid: columns the straightened line may not move onto (house footprints)
    @param clear: reserved opening columns; the straightened line leaves them open
    @param fit: straighten the run (``flatten_panel_run``); False for observed boundaries. A fitted run whose
        boards do not join (``boards_joined``) or that has more separate pieces than the unfitted run is discarded for it
    @returns: ``{'cells', 'facing', 'basis', 'closers', 'cross'}``: cells include added closer columns;
        ``closers`` lists the cross-shelf columns (added and converted step columns); ``cross`` maps
        each to its facing (also in ``facing``)
    """
    if fit:
        plain = _panel_runs(cols, plot_id, role, owner, street, centre)
        fitted = _panel_runs(flatten_panel_run(cols, plot_id, owner, avoid, clear), plot_id, role, owner, street, centre)
        shut = set(clear)
        pieces = lambda run: len(cellmath.components([q for q in map(tuple, run['cells']) if q not in shut], cellmath.N8))
        if pieces(fitted) <= pieces(plain) and (boards_joined(fitted) or not boards_joined(plain)):
            return fitted                                          # never more pieces (gaps) than the unfitted run
        return plain
    return _panel_runs(cols, plot_id, role, owner, street, centre)


SHELF_BOARD = {'north': (0, 13, 16, 16), 'south': (0, 0, 16, 3), 'west': (13, 0, 16, 16), 'east': (0, 0, 3, 16)}


def boards_joined(run: dict) -> bool:
    """True when the shelf back boards of a panel run (3/16, flush with the edge opposite the facing)
    form one piece, boards touching along an edge of positive length. Runs split by an opening or a
    street are judged per 8-connected group."""
    boxes = {}
    for key, f in run['facing'].items():
        x, z = map(int, key.split(','))
        b = SHELF_BOARD[f]
        boxes[(x, z)] = (x * 16 + b[0], z * 16 + b[1], x * 16 + b[2], z * 16 + b[3])
    for group in sorted(map(sorted, cellmath.components(boxes, cellmath.N8)), key=lambda g: g[0]):
        seen, stack = {group[0]}, [group[0]]
        while stack:
            a = boxes[stack.pop()]
            for q in group:
                if q in seen:
                    continue
                b = boxes[q]
                ox, oz = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
                if (ox >= 0 and oz > 0) or (oz >= 0 and ox > 0):
                    seen.add(q)
                    stack.append(q)
        if len(seen) != len(group):
            return False
    return True


def _panel_runs(cols, plot_id, role: str, owner: dict, street, centre) -> dict:
    face, basis = outward_facings(cols, plot_id, role, owner, centre)
    cells = {(int(q[0]), int(q[1])) for q in cols}
    run_face: dict = {}
    for run in sorted(map(sorted, cellmath.components(cells, cellmath.N8)), key=lambda g: g[0]):
        votes = [cellmath.FACING[face[f'{q[0]},{q[1]}']] for q in run]
        vx, vz = sum(v[0] for v in votes), sum(v[1] for v in votes)
        mx, mz = sum(q[0] for q in run) / len(run), sum(q[1] for q in run) / len(run)
        sxx = sum((q[0] - mx) ** 2 for q in run)
        szz = sum((q[1] - mz) ** 2 for q in run)
        sxz = sum((q[0] - mx) * (q[1] - mz) for q in run)
        if len(run) > 1 and (sxx + szz) > 0:
            theta = 0.5 * math.atan2(2 * sxz, sxx - szz)          # principal direction
            nx, nz = -math.sin(theta), math.cos(theta)
        else:
            nx, nz = float(vx), float(vz)
        if nx * vx + nz * vz < 0:
            nx, nz = -nx, -nz
        n = math.hypot(nx, nz) or 1.0
        nx, nz = nx / n, nz / n
        if abs(abs(nx) - abs(nz)) < 0.15:                         # about 45 degrees: larger vote
            axis_x = (abs(vx), 0) > (abs(vz), 1) if abs(vx) != abs(vz) else None
            if axis_x is None:
                first = min(votes, key=lambda v: ('east', 'west', 'south', 'north').index(
                    next(k for k, d in cellmath.FACING.items() if d == v)))
                axis_x = first[0] != 0
        else:
            axis_x = abs(nx) > abs(nz)
        if axis_x:
            d = 'east' if (nx if nx else vx) > 0 else 'west'
        else:
            d = 'south' if (nz if nz else vz) > 0 else 'north'
        for q in run:
            run_face[q] = d
    closers: set = set()                                           # columns turned into cross shelves
    for q in sorted(cells):
        d = cellmath.FACING[run_face[q]]
        for a, b in ((1, 0), (0, 1), (1, 1), (1, -1)):
            p = (q[0] + a, q[1] + b)
            if p not in cells or run_face[p] != run_face[q]:
                continue
            if a == 0 or b == 0:                                   # face to face
                if (a * d[0] + b * d[1]) != 0:                     # step along the facing axis
                    closers.add(q if (a * d[0] + b * d[1]) > 0 else p)   # the one opposite the facing
                continue
            bridges = sorted(((q[0] + a, q[1]), (q[0], q[1] + b)),
                             key=lambda c: (c[0] * d[0] + c[1] * d[1], c))
            if any(c in cells for c in bridges):                  # already joined face to face: no extra closer
                continue
            for c in bridges:
                if c in street or owner.get(c) not in (None, plot_id):
                    continue
                cells.add(c)
                run_face[c] = run_face[q]
                closers.add(c)
                break
    out_face, out_basis, cross = {}, {}, {}
    closer_face: dict = {}
    for q in sorted(closers):                                      # closers beside a neighbour decide first
        d = cellmath.FACING[run_face[q]]
        if any((q[0] + a, q[1] + b) in cells for a, b in ((d[1], d[0]), (-d[1], -d[0]))):
            closer_face[q] = _closer_facing(q, run_face[q], cells, closers)
    todo = [q for q in sorted(closers) if q not in closer_face]
    while todo:                                                    # a jog of several rows shares one board edge
        left = []
        for q in todo:
            d = cellmath.FACING[run_face[q]]
            same = [closer_face[n] for n in ((q[0] + d[0], q[1] + d[1]), (q[0] - d[0], q[1] - d[1])) if n in closer_face]
            if same:
                closer_face[q] = same[0]
            else:
                left.append(q)
        if len(left) == len(todo):
            for q in left:
                closer_face[q] = _closer_facing(q, run_face[q], cells, closers)
            break
        todo = left
    for q in sorted(cells):
        key = f'{q[0]},{q[1]}'
        if q in closers:
            f = closer_face[q]
            out_face[key] = cross[key] = f
            out_basis[key] = f'closing piece at a diagonal step: cross shelf facing {f}'
            continue
        out_face[key] = run_face[q]
        base = basis.get(key, '')
        out_basis[key] = base if run_face[q] == face.get(key) else f'{base}; run facing'
    return {'cells': sorted(cells), 'facing': out_face, 'basis': out_basis,
            'closers': sorted(closers), 'cross': cross}


def _closer_facing(c, run_dir: str, cells, closers) -> str:
    """Facing of the cross shelf that closes a step column ``c`` of a run facing ``run_dir``.

    The run's boards lie on the inner edge of their columns. A staircase leaves the boards of the two
    rows one block apart, so the closer's own board runs across the step, along the perpendicular
    axis, on the edge that touches the next column of the run: its 3/16 board is flush with the edge
    opposite its facing, so it faces away from that neighbour. The board then meets the neighbour's
    board end on that edge and the previous row's board at the step. Neighbours that are ordinary
    shelves are preferred to other closers; ties take the fixed order east, west, south, north.
    """
    d = cellmath.FACING[run_dir]
    sides = [s for s in ('east', 'west', 'south', 'north') if cellmath.FACING[s][0] * d[0] + cellmath.FACING[s][1] * d[1] == 0]
    def score(s):
        n = (c[0] + cellmath.FACING[s][0], c[1] + cellmath.FACING[s][1])
        return (0 if n not in cells else 1 if n in closers else 2)
    side = max(sides, key=lambda s: (score(s), -sides.index(s)))   # best score; first in the order wins ties
    return {'east': 'west', 'west': 'east', 'south': 'north', 'north': 'south'}[side]


def _joining_chain(it, other, q, p, chains):
    """The chain (of ``chains`` for the jog ``q`` to ``p``) whose boards join both pieces, or None.

    Shelf boards lie on the edge opposite the facing, so the piece further back (against the facing)
    has its board a row or more behind the other one. A closer chain reaching that piece's row, in the
    column next to the other piece, ties the two board lines together. ``chains`` is the pair from
    ``join_front_diagonals``: the first ends in ``q``'s row at ``p``'s column, the second in ``p``'s
    row at ``q``'s column.
    """
    face = next((f for k, f in (it.get('facing') or {}).items() if k not in
                 {f'{x},{z}' for x, z in map(tuple, it.get('closers', ()))}), None)
    if face is None:
        return None
    d = cellmath.FACING[face]
    ax = 0 if d[0] else 1                                         # the facing axis: the jog is measured along it
    if p[ax] == q[ax]:
        return None                                               # no step along the facing: no preference
    q_back = q[ax] * d[ax] < p[ax] * d[ax]                        # q lies further back than p
    return chains[0] if q_back else chains[1]


JOIN_MAX_JOG = 4   # tallest row jump between two plots' fronts that gets a closer chain (#277)


def join_front_diagonals(items: list, keep_clear, owner: dict, street, footprint) -> int:
    """Close corner-only joins between the front boundaries of two plots (#275).

    Each plot's own run is 4-connected (``line4``, ``panel_runs``), but where two neighbouring
    plots' front lines meet on a diagonal their end columns touch only at a corner, so the boundary
    shows a gap. When two columns of different inferred front items touch diagonally and neither
    orthogonal bridge column is covered, one bridge column is added: the one owned by one of the two
    plots (not street, house or reserved opening), else the first free one. A plank fence takes it
    as a cross shelf closer. Observed (map) items are never changed.

    A jog of two or more rows (#277; up to ``JOIN_MAX_JOG``) between two plots' pieces that end facing
    each other gets one closer chain instead: a column of cross shelves reaching the row of the piece
    further back, sharing one facing, so the board edges join. Straightened runs (``panel_runs``) leave
    such jogs between plots, never inside one.

    @returns: number of bridge columns added
    """
    front = [it for it in items if it.get('boundary_role') in PUBLIC_ROLES
             and it['provenance']['source'] != 'vector_map']
    cover: dict = {}
    for it in items:
        for q in it['cells']:
            cover[tuple(q)] = it
    blocked = set(keep_clear) | set(footprint)
    added = 0
    pairs = [(1, s * k) for k in range(1, JOIN_MAX_JOG + 1) for s in (1, -1)]
    pairs += [(s * k, 1) for k in range(2, JOIN_MAX_JOG + 1) for s in (1, -1)]    # the same jogs along the other axis
    for it in front:
        mine_cells = {tuple(c) for c in it['cells']}
        for q in sorted(mine_cells):
            for a, b in pairs:
                p = (q[0] + a, q[1] + b)
                other = cover.get(p)
                if other is None or other is it or other.get('scope') == it.get('scope'):
                    continue
                if other['provenance']['source'] == 'vector_map':
                    continue                                     # observed boundaries are never edited
                k = max(abs(a), abs(b))
                if k > 1:                                        # a taller jog: both pieces must end here, facing each other
                    ax = 0 if abs(a) == 1 else 1                 # the axis the pieces run along
                    step = a if abs(a) == 1 else b
                    their = {tuple(c) for c in other['cells']}
                    if any(c[ax] == q[ax] + step and abs(c[1 - ax] - q[1 - ax]) <= k + 1 for c in mine_cells):
                        continue
                    if any(c[ax] == p[ax] - step and abs(c[1 - ax] - p[1 - ax]) <= k + 1 for c in their):
                        continue
                if abs(a) == 1:                                  # q's column down to p's row, or p's column back to q's row
                    s_ = 1 if b > 0 else -1
                    chains = [[(p[0], p[1] - s_ * i) for i in range(1, k + 1)], [(q[0], q[1] + s_ * i) for i in range(1, k + 1)]]
                else:
                    s_ = 1 if a > 0 else -1
                    chains = [[(p[0] - s_ * i, p[1]) for i in range(1, k + 1)], [(q[0] + s_ * i, q[1]) for i in range(1, k + 1)]]
                if any(c in cover for ch in chains for c in ch):
                    continue
                free = [ch for ch in chains if not any(c in blocked or c in street for c in ch)]
                if not free:
                    continue
                mine = {_plot(it), _plot(other)} - {None}
                own = [ch for ch in free if all(owner.get(c) is not None and owner.get(c) in mine for c in ch)]
                joined = _joining_chain(it, other, q, p, chains) if it['kind'] == other['kind'] == 'plank_fence' else None
                chain = joined if joined in free else (own or free)[0]
                target = other if own and owner.get(chain[0]) == _plot(other) != _plot(it) else it
                for c in chain:
                    target['cells'].append([c[0], c[1]])
                    cover[c] = target
                    added += 1
                if target['kind'] == 'plank_fence':
                    near = [(k_, v) for k_, v in (target.get('facing') or {}).items()
                            if k_ not in {f'{x},{z}' for x, z in map(tuple, target.get('closers', ()))}]
                    run_dir = near[0][1] if near else 'south'
                    cells = {tuple(x) for x in target['cells']}
                    closers = {tuple(x) for x in target.get('closers', ())} | set(chain)
                    f = _closer_facing(chain[-1], run_dir, cells, closers)   # a jog of several rows shares one board edge
                    target['closers'] = [list(x) for x in sorted(closers)]
                    for c in chain:
                        target['facing'][f'{c[0]},{c[1]}'] = f
                        target.setdefault('facing_basis', {})[f'{c[0]},{c[1]}'] = (
                            'closing piece at a corner join between two plots: cross shelf facing ' + f)
    return added


def _plot(item: dict):
    """Plot id of a garden item (its id is ``garden-<plot id>-<role>``)."""
    try:
        return int(str(item['id']).split('-')[1])
    except (IndexError, ValueError):
        return None


def garden_barriers(cell: tuple[int, int], rgb: dict | None = None, report: dict | None = None):
    """Garden barrier items carrying property/street scope, evidence and inference reasons.

    Geometry still comes from plots, segments and straightening. A boundary's source is
    recorded when chosen, never guessed later from its material. Per-property observations
    and explicit property/street none vetoes win over aerial promotion. District area evidence
    supplies missing roles/materials only when the class's frontage conditions hold.
    """
    from ground_sources import aerial_rgb
    import provenance as prov

    box = cellmath.bounds(cell)
    rgb = rgb if rgb is not None else aerial_rgb(cell)
    canopy = tree_canopy_columns(cell)
    owner, info, street, idx = plots_for(box)
    seg = segments(owner, info, street, idx, box)
    profile = load_district(cell)
    districts = {}
    for i, meta in info.items():
        roles = {role: cells for (j, role), cells in seg.items() if j == i}
        districts[i] = (district_profiles.boundary_layer(profile, meta_class(meta), meta['street'], str(meta['number']))
                        if not frontage_inference_reason(meta, roles, land_use.defaults(meta_class(meta))) else None)
    items, rep, front_boundaries, panels = [], [], {}, {}
    for (i, role), cells in sorted(seg.items(), key=lambda kv: (info[kv[0][0]]['street'], kv[0][1])):
        meta = info[i]
        conv = load_convention(meta['street']) or {}
        number = str(meta['number'])
        override = (conv.get('overrides') or {}).get(number, {})
        property_role = role in override
        raw = (override if property_role else conv).get(role) or {}
        cls = meta_class(meta)
        district = districts[i]
        res = role_resolution(conv, number, role, cls, district=district)
        spec = role_spec(conv, number, role, cls, district=district)
        vetoed = res['open'] and res['layer'] in ('property', 'street')
        near = [q for q in cells if math.hypot(q[0] - meta['centre'][0], q[1] - meta['centre'][1]) <= 30]
        share = hedge_share(near, rgb, canopy=canopy) if len(near) >= 4 else 0.0
        src = ({'class': 'class default', 'district': 'district profile'}.get(res['layer'], 'convention')) if spec else 'none'
        if share >= HEDGE_SHARE and len(cells) >= 4 and not vetoed and not property_role:
            spec, src = {'type': 'hedge', 'block': bv.HEDGE_BLOCK, 'height': 2, 'depth': None}, 'aerial hedge'
        elif share >= HEDGE_SHARE and len(cells) >= 4 and vetoed:
            src = 'aerial hedge vetoed'
        if spec and spec.get('depth') and role.startswith('side'):
            fx, fz = meta['foot'] or meta['centre']
            cells = [q for q in cells if math.hypot(q[0] - fx, q[1] - fz) <= spec['depth'] + 12]
        if spec:
            def twin(q):
                for a, b in cellmath.N4:
                    j = owner.get((q[0] + a, q[1] + b))
                    if j is not None and j < i and role != 'front' and \
                            role_spec(load_convention(info[j]['street']), info[j]['number'], role, meta_class(info[j]),
                                      district=districts[j]):
                        return True
                return False
            cols = straighten([q for q in cells if not twin(q)])
            source = 'aerial' if src == 'aerial hedge' else ('register_entry' if property_role else 'inference')
            from_class = src == 'class default'
            from_district = src == 'district profile'
            layer = ('property' if property_role else 'aerial' if source == 'aerial'
                     else 'class' if from_class else 'district' if from_district else 'street')
            reason = ('aerial hedge strip outside tree canopy' if source == 'aerial' else
                      'explicit per-property boundary' if property_role else
                      f'land-use class {cls} default boundary' if from_class else
                      f'district {district["name"]} area evidence; boundary inference' if from_district else
                      'deterministic same-street inference from boundary convention')
            supplied = (raw.get('provenance') or {}) if source != 'aerial' else {}
            evidence_ids = (district['evidence_ids'] if from_district else
                            supplied.get('evidence_ids', raw.get('evidence_ids', conv.get('evidence_ids', ()))))
            record = prov.provenance_record(
                source=supplied.get('source', source) if property_role else source,
                confidence=supplied.get('confidence', 'low' if from_district else 'medium'),
                evidence_ids=evidence_ids, unresolved_reason=supplied.get('unresolved_reason', ''))
            items.append({'id': f'garden-{i}-{role}', 'role': 'barrier', 'kind': bv.barrier_kind(spec['type'], spec['block']),
                          'block': spec['block'], 'height': spec['height'],
                          'cells': [list(q) for q in cols], 'boundary_role': role,
                          'scope': {'street': meta['street'], 'property': number, 'layer': layer, 'land_use': cls},
                          'reason': reason, 'provenance': record})
            if src != 'aerial hedge' and 'district' in res.get('material_layers', {}).values():
                # Keep an observed/property presence separate from area-inferred material.
                items[-1]['scope']['material_layers'] = res['material_layers']
                items[-1]['scope']['material_provenance'] = prov.provenance_record(
                    source='inference', confidence='low', evidence_ids=district['evidence_ids'])
                items[-1]['reason'] += f'; material inferred from district {district["name"]}'
            if items[-1]['kind'] == 'plank_fence':
                panels[items[-1]['id']] = (cols, i, role, meta['centre'])   # run after openings are known
            if role == 'front':
                front_boundaries[i] = cols
        rep.append({'street': meta['street'], 'number': meta['number'], 'role': role,
                    'cells': len(cells), 'source': src, 'hedge_share': round(share, 2),
                    'type': spec['type'] if spec else None})
    approaches = landed_approaches(cell, footprint=idx.cells())
    frontages = frontages_for_plots(owner, info, seg, idx, approaches=approaches,
                                   boundaries=front_boundaries, paved=paved_columns(rgb) if rgb else None, profile=profile)
    opening = reserved_openings(frontages)
    keep_clear = opening | reserved_route(frontages)
    for item in items:
        if item['id'] in panels:
            cols, i, role, centre = panels[item['id']]
            observed = item['provenance']['source'] == 'vector_map'          # observed boundaries are never moved
            run = panel_runs(cols, i, role, owner, street, centre, avoid=idx.cells(), clear=keep_clear,
                             fit=not observed)
            item['cells'] = [list(q) for q in run['cells']]
            item['facing'], item['facing_basis'] = run['facing'], run['basis']
            item['closers'] = [list(q) for q in run['closers']]
    # Reserve after straightening. All later sources and final joins use the same clearance.
    for item in items:
        if item['provenance']['source'] == 'vector_map':
            continue  # Decor reports observed-boundary conflicts instead of erasing them.
        item['cells'] = [q for q in item['cells'] if tuple(q) not in keep_clear]
    items = [item for item in items if item['cells']]
    join_front_diagonals(items, keep_clear, owner, street, idx.cells())
    if report is not None:
        report['segments'] = rep
        report['frontages'] = frontages
        report['openings'] = sorted(opening)
        report['route_reserved'] = sorted(reserved_route(frontages))
        report['plots'] = owner
    return items


def frontages_for_plots(owner: dict, info: dict, seg: dict, idx, doors: dict | None = None,
                        approaches=(), boundaries=None, paved=None, profile: dict | None = None) -> list:
    """``residential_frontage`` for every plot, from the plots/segments ``garden_barriers`` already built.

    @param owner: ``{cell: plot id}`` from ``plots_for``
    @param info: ``{plot id: meta}`` from ``plots_for``
    @param seg: ``{(plot id, role): [cells]}`` from ``segments``
    @param idx: ``OSMIndex``
    @param doors: optional ``{plot id: (x, z)}`` observed door columns
    @param approaches: ``entrance_approach`` dicts of landed features; each joins the plot whose house
        holds (or touches) its door
    @param profile: already loaded district profile (boundary sections only)
    @returns: list of frontage dicts, one per plot, ordered by id
    """
    fp = idx.cells()
    plot_cells, house_cells = defaultdict(set), defaultdict(set)
    for q, i in owner.items():
        if q not in fp:
            plot_cells[i].add(q)
    for q, i in fp.items():
        house_cells[i].add(q)
    roles = defaultdict(dict)
    for (i, role), cells in seg.items():
        roles[i][role] = cells
    cands: dict = {}
    for a in approaches or ():
        if not a.get('door') or not a.get('connection'):
            continue
        dx, dz = a['door']['cell']
        for i, cells in house_cells.items():
            if any((dx + u, dz + v) in cells for u in (-1, 0, 1) for v in (-1, 0, 1)):
                cands.setdefault(i, []).append(a)
                break
    by_plot = {}
    for i, found in cands.items():
        front = roles[i].get('front') or ()
        if not front:
            continue

        def gap(a):                    # the street-facing door: its connection is nearest the front boundary
            cx, cz = a['connection']['cell']
            return min(math.hypot(cx - x, cz - z) for x, z in front)
        by_plot[i] = min(found, key=lambda a: (gap(a), a['door']['cell']))
    out = []
    for i in sorted(info):
        meta = info[i]
        district = district_profiles.boundary_layer(profile, meta_class(meta), meta['street'], str(meta['number']))
        conv = load_convention(meta['street'])
        evidence_ids = list((conv or {}).get('evidence_ids', ()))
        if district:
            evidence_ids = list(dict.fromkeys(evidence_ids + district['evidence_ids']))
        out.append(frontage(
            meta, plot_cells[i], roles[i], house_cells[i], conv,
            door=(doors or {}).get(i), approach=by_plot.get(i),
            evidence_ids=evidence_ids,
            boundary_cells=(boundaries or {}).get(i), paved=paved, district=district))
    return out


def overlay(cell, barriers: list, path: Path, rgb_cells=None) -> None:
    """Aerial crop of the cell with the barrier columns marked (fence cyan, wall red, hedge green)."""
    import numpy as np
    from PIL import Image
    import aerial_coverage

    x0, z0, x1, z1 = cellmath.bounds(cell)
    k = 4
    keys = [(x, z) for x in range(x0, x1 + 2) for z in range(z0, z1 + 2)]
    # block corners (Mosaic.pixels adds .5 to its input, so ask for x - .5)
    found = aerial_coverage.covering_mosaic([(x - .5, z - .5) for x, z in keys])
    if found is None:
        raise aerial_coverage.CoverageError(
            f'no single local aerial mosaic covers cell {cell[0]},{cell[1]}; overlay not drawn')
    mosaic, pixels = found
    px = {key: pixels[(key[0] - .5, key[1] - .5)] for key in keys}
    im = aerial_coverage.load_rgb(mosaic.path)
    W = (x1 - x0 + 1) * k
    H = (z1 - z0 + 1) * k
    out = np.zeros((H, W, 3), np.uint8)
    for a in range(W):
        for b in range(H):
            x, z = x0 + a / k, z0 + b / k
            xi, zi = int(math.floor(x)), int(math.floor(z))
            fx, fz = x - xi, z - zi
            p00, p10 = px[(xi, zi)], px[(xi + 1, zi)]
            p01, p11 = px[(xi, zi + 1)], px[(xi + 1, zi + 1)]
            X = (p00[0] * (1 - fx) + p10[0] * fx) * (1 - fz) + (p01[0] * (1 - fx) + p11[0] * fx) * fz
            Y = (p00[1] * (1 - fx) + p10[1] * fx) * (1 - fz) + (p01[1] * (1 - fx) + p11[1] * fx) * fz
            X, Y = int(X), int(Y)
            if 0 <= X < im.shape[1] and 0 <= Y < im.shape[0]:
                out[b, a] = im[Y, X]
    col = {'hedge': (40, 255, 40), 'brick_wall': (255, 40, 40), 'fence': (0, 220, 255),
           'iron_bars': (255, 0, 255)}
    for item in barriers:
        for x, z in item['cells']:
            a, b = (x - x0) * k, (z - z0) * k
            if 0 <= a < W and 0 <= b < H:
                out[b + 1:b + k - 1, a + 1:a + k - 1] = col.get(item['kind'], (255, 255, 0))
    Image.fromarray(out).resize((W * 2, H * 2), Image.NEAREST).save(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cell', required=True)
    ap.add_argument('--png', default='')
    a = ap.parse_args()
    cell = tuple(int(v) for v in a.cell.split(','))
    rep: dict = {}
    b = garden_barriers(cell, report=rep)
    by = Counter((r['street'], r['role'], r['source'], r['type']) for r in rep['segments'])
    for k, n in sorted(by.items(), key=str):
        print(n, k)
    print('barrier columns', sum(len(i['cells']) for i in b),
          dict(Counter({kind: sum(len(i['cells']) for i in b if i['kind'] == kind)
                        for kind in {i['kind'] for i in b}})))
    png = Path(a.png) if a.png else ROOT / f'verification/site/gardens_{cell[0]}_{cell[1]}.png'
    overlay(cell, b, png)
    print('overlay', png)
    return 0


if __name__ == '__main__':
    sys.exit(main())
