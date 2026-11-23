"""Journalled railway features built from validated, JSON-able corridor plans.

Spec: ``kind: railway``, ``name: railway_<tx>_<tz>``, ``cell: [tx,tz]``,
``plans: [railway_plan.plan_line(...) outputs]``, optional ``modifies`` and
``exclude_boxes`` (already-covered inclusive MC boxes). Each plan may carry
``clear_to: [[x,z,y],...]`` from its recorded surface evidence. Metadata keeps
line ids, box cuts and gap end records (``junctions`` lists the ``junction`` gaps for a
person to finish); active journal entries are the coverage record, so normal feature
undo also removes coverage and column reservations.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import cellmath
from railway_plan import (PLANNER_BOX, RAIL_CLEARANCE, deck_evidence, junction_gaps, plan_line,
                          share_footprints, validate_plan)
from site_kit import Plan

KIND = 'railway'
BUILDER = 'railway_build'


def feature_name(cell) -> str:
    """Stable feature id for the cell that starts a corridor box."""
    return 'railway_' + '_'.join(str(int(n)).replace('-', 'm') for n in cell)


def active_railways(reg):
    """Latest active, landed railway entries; an undone feature reserves nothing."""
    seen = set()
    for entry in reversed(reg.entries()):
        if entry.plan in seen or entry.kind != 'build' or entry.pass_ != 'feature' or entry.undone:
            continue
        seen.add(entry.plan)
        if entry.status == 'landed' and entry.meta.get('kind') == KIND:
            yield entry


def covered(cell, reg) -> bool:
    """Whether an earlier landed box accounts for this whole cell."""
    x0, z0, x1, z1 = cellmath.bounds(cell)
    return any(a <= x0 and x1 <= c and b <= z0 and z1 <= d
               for entry in active_railways(reg) for a, b, c, d in entry.meta.get('boxes', ()))


def reserved_columns(cell, reg=None) -> set:
    """Railway footprint columns near a layer cell, derived from active journal blocks."""
    if reg is None:
        import script_env
        if not script_env.world_bound() and not os.environ.get('AI_WORLDBUILDER_REGISTRY_ROOT'):
            return set()
        from cell_registry import view_registry
        reg = view_registry()
    entries = list(active_railways(reg))
    if not entries:
        return set()
    x0, z0, x1, z1 = cellmath.bounds(cell, 12)
    out = set()
    for entry in entries:
        out.update((x, z) for x, _y, z in (reg.recorded(entry.plan) or {})
                   if x0 <= x <= x1 and z0 <= z <= z1)
    return out


def proposal(cell, reg, *, root=None, source=None, frame=None) -> list:
    """Cheap cell evidence check; no box extract or lidar is opened here.

    Explicit source/frame inputs are the synthetic/recorded fixture seam.
    Production searches only railway-labelled cell clips that overlap this cell.
    """
    if covered(cell, reg):
        return []
    from railway_evidence import THROUGH, cell_has_railway, extract_railway_box
    from world_frame import load_frame
    if root is None:
        root = reg.root
    if source is None:
        from vector_map_clips import vector_map_clip_root
        try:
            clips = vector_map_clip_root(root)
        except FileNotFoundError:
            return []  # readiness separately reports missing cell evidence
        from osm_footprints import query_bbox
        x0, z0, x1, z1 = cellmath.bounds(cell)
        sources = []
        for path in sorted(clips.glob('*/*.geojson')):
            box = query_bbox(path, 'railway')
            if box and box[0] <= x0 and box[2] >= x1 and box[1] <= z0 and box[3] >= z1:
                sources.append(path)
        sources.sort(key=lambda p: ((lambda b: (b[2]-b[0]+1)*(b[3]-b[1]+1))(query_bbox(p, 'railway')), str(p)))
        sources = sources[:1]  # the smallest complete cell query is authoritative
    else:
        sources = [Path(source)]
    if not sources:
        return []
    refs = set()
    for path in sources:
        # Empty cell evidence needs no frame, and never loads a larger box.
        try:
            present = cell_has_railway(cell, path, frame=frame)
        except ValueError as exc:
            if frame is not None or 'frame is required' not in str(exc):
                raise
            frame = load_frame(Path(root) / 'config/world-frame.json')
            present = cell_has_railway(cell, path, frame=frame)
        if not present:
            continue
        frame = frame or load_frame(Path(root) / 'config/world-frame.json')
        refs.update(w.osm_id for w in extract_railway_box(cellmath.bounds(cell), source=path, frame=frame)
                    if w.kind == THROUGH)
        if refs:
            break
    if not refs:
        return []
    return [{'id': feature_name(cell), 'kind': KIND, 'cell': list(cell),
             'label': 'Railway corridor', 'refs': sorted(refs),
             'bbox': list(cellmath.bounds(cell)), 'area': 0}]


def load_spec_dict(raw) -> dict:
    """Validate the entire feature before any Plan or journal write is created."""
    if not isinstance(raw, dict) or raw.get('kind') != KIND:
        raise ValueError('railway spec needs kind: railway')
    cell = raw.get('cell')
    if (not isinstance(cell, list) or len(cell) != 2
            or any(not isinstance(v, int) or isinstance(v, bool) for v in cell)):
        raise ValueError('railway cell must be two integers')
    if raw.get('name') != feature_name(cell):
        raise ValueError('railway name must match its starting cell')
    plans = raw.get('plans')
    if not isinstance(plans, list) or not plans:
        raise ValueError('railway spec needs corridor plans')
    for p in plans:
        if not isinstance(p, dict):
            raise ValueError('invalid railway corridor plan')
        if p.get('failures'):
            raise ValueError('railway planning failed: ' + json.dumps(p['failures']))
        try:
            from ground_palette import MATERIAL
            if not isinstance(p['line_id'], str) or not p['line_id']:
                raise ValueError('line_id must be a nonempty string')
            for key, length, coordinates in (('rails', 5, 3), ('columns', 5, 4),
                                             ('surfaces', 3, 2), ('planting', 4, 3),
                                             ('torches', 3, 3), ('torch_supports', 3, 3)):
                if not isinstance(p[key], list):
                    raise ValueError(f'{key} must be a list')
                for row in p[key]:
                    if (not isinstance(row, list) or len(row) != length
                            or any(not isinstance(v, int) or isinstance(v, bool) for v in row[:coordinates])):
                        raise ValueError(f'invalid {key} placement')
            if any(row[2] not in MATERIAL for row in p['surfaces']):
                raise ValueError('unknown railway surface kind')
            if any(row[4] not in ('bed', 'slope') for row in p['columns']):
                raise ValueError('unknown railway column kind')
            if {tuple(r[:2]) for r in p['surfaces']} != {tuple(r[:2]) for r in p['columns']}:
                raise ValueError('railway surface coverage differs from corridor columns')
            if (not isinstance(p['box'], list) or len(p['box']) != 4
                    or any(not isinstance(n, int) or isinstance(n, bool) for n in p['box'])):
                raise ValueError('invalid railway box')
            identities = p.get('track_way_ids')
            if identities is not None and (not isinstance(identities, list)
                    or len(identities) != len(p['tracks'])
                    or any(not isinstance(ids, list) or not ids
                           or any(not isinstance(w, str) or not w for w in ids) for ids in identities)):
                raise ValueError('invalid constituent track way identities')
            validate_plan(p)
            if not p['rails'] or len(p['box']) != 4:
                raise ValueError('empty railway plan or invalid box')
            for row in p.get('clear_to', ()):
                if len(row) != 3 or any(not isinstance(n, int) or isinstance(n, bool) for n in row):
                    raise ValueError('clear_to needs integer x,z,y rows')
        except (AssertionError, KeyError, IndexError, TypeError) as exc:
            raise ValueError(f'invalid railway plan: {exc}') from exc
    for box in raw.get('exclude_boxes', ()):
        if (len(box) != 4 or any(not isinstance(n, int) or isinstance(n, bool) for n in box)
                or box[0] > box[2] or box[1] > box[3]):
            raise ValueError('invalid previously covered box')
    if not isinstance(raw.get('modifies', []), list) or any(not isinstance(s, str) for s in raw.get('modifies', ())):
        raise ValueError('modifies must be plan names')
    return raw


def load_spec(path, *, root=None) -> dict:
    """Read and validate a saved railway feature spec; no world calls."""
    return load_spec_dict(json.loads(Path(path).read_text()))


def place_rails(plan: Plan, rails, torches, supports, *, support='stone') -> None:
    """Shared line/crossing placement seam: solid support, powered torch, exact rail states.

    Input rows are the planner's public placements. Support is y-3, torch y-2,
    formation y-1, rail y. Call after forming and clearing the carried surface.
    """
    for x, y, z in supports:
        plan.set(x, y, z, support)
    for x, y, z in torches:
        from block_store import block_id
        carrier = (x, y + 1, z)
        if block_id(plan.cells.get(carrier, 'minecraft:air')) in ('minecraft:air', 'minecraft:gravel'):
            plan.set(*carrier, support)
        plan.set(x, y, z, 'redstone_torch')
    for x, y, z, _shape, state in rails:
        plan.set(x, y, z, state)


def build(raw) -> Plan:
    """Convert validated corridor placements to the normal site_kit Plan."""
    spec = load_spec_dict(raw)
    from ground_palette import MATERIAL
    plan = Plan(spec['name'], input_cells=[spec['cell']])
    rail_columns = set()
    for corridor in spec['plans']:
        incoming_columns = {(r[0], r[2]) for r in corridor['rails']}
        if rail_columns & incoming_columns:
            raise ValueError('overlapping railway lines need a human')
        rail_columns.update(incoming_columns)
        previous = dict(plan.cells)
        surfaces = {(x, z): kind for x, z, kind in corridor['surfaces']}
        clear_to = {(x, z): y for x, z, y in corridor.get('clear_to', ())}
        for x, z, original, surface, _kind in corridor['columns']:
            plan.fill((x, min(original, surface) - 3, z), (x, surface, z), 'stone')
            plan.set(x, surface, z, MATERIAL[surfaces[(x, z)]])
            top = max(original, surface + RAIL_CLEARANCE, clear_to.get((x, z), surface))
            # clear_to may cap clearing beneath an overhead deck.
            if (x, z) in clear_to:
                top = clear_to[(x, z)]
            if top > surface:
                plan.fill((x, surface + 1, z), (x, top, z), 'air')
        for x, y, z, _kind in corridor['planting']:
            plan.set(x, y, z, 'oak_leaves[persistent=true,distance=1]')
        place_rails(plan, corridor['rails'], corridor['torches'], corridor['torch_supports'])
        if any(plan.cells.get(p) != b for p, b in previous.items()):
            raise ValueError('railway corridor plans disagree in their overlapping footprint')
    for x, _y, z in tuple(plan.cells):
        if any(a <= x <= c and b <= z <= d for a, b, c, d in spec.get('exclude_boxes', ())):
            plan.cells.pop((x, _y, z), None)
    if not plan.cells:
        raise ValueError('railway plan contains no uncovered positions')
    plan.outside_footprint_ok = 'mapped railway corridor; source ways recorded in railway spec'
    plan.capture_inputs()
    return plan


def metadata(spec) -> dict:
    """Portable railway records for coverage, column ownership and later crossing joins."""
    joins = []
    for corridor in spec['plans']:
        identities = corridor.get('track_way_ids', ())
        for join in corridor['joins']:
            tracks = []
            for record in join['tracks']:
                ti = record['track_index']
                ways = identities[ti] if 0 <= ti < len(identities) else []
                tracks.append({**record, 'way_ids': list(ways)})
            joins.append({**join, 'tracks': tracks})
    gaps = [{**g, 'line_id': p['line_id']} for p in spec['plans'] for g in p['gaps']]
    junctions = [{'line_id': g['line_id'], 'main_line_id': g['source'], 'track_index': g['track_index'],
                  'where': g['where'], 'last': g['columns'][-1], 'ends': g['ends']}
                 for g in gaps if g['kind'] == 'junction']
    return {'kind': KIND, 'tiled_backup': True, 'boxes': [p['box'] for p in spec['plans']],
            'line_ids': [p['line_id'] for p in spec['plans']], 'joins': joins,
            'gaps': gaps, 'junctions': junctions}


def _incoming_joins(line, prior, cell):
    """Match constituent tracks and directed boundary segments, never aggregate ids alone."""
    from railway_plan import snap_track
    from plan_refs import norm_ref
    x0, z0, x1, z1 = cellmath.bounds(cell)
    cx, cz = (x0+x1+1)//2, (z0+z1+1)//2
    bx, bz, ex, ez = cx-PLANNER_BOX//2, cz-PLANNER_BOX//2, cx+PLANNER_BOX//2-1, cz+PLANNER_BOX//2-1
    by_track = {}
    for ti, poly in enumerate(line.tracks):
        path = snap_track(poly)
        indices = {q: i for i, q in enumerate(path)}
        ways = {norm_ref(w) for w in line.track_way_ids[ti]}
        for entry in prior:
            for join in entry.meta.get('joins', ()):
                for record in join['tracks']:
                    at, nxt = tuple(record['at']), tuple(record['next'])
                    if not (bx <= nxt[0] <= ex and bz <= nxt[1] <= ez):
                        continue
                    old_ways = {norm_ref(w) for w in record.get('way_ids', ())}
                    same_track = bool(old_ways & ways) if old_ways else join.get('line_id') == line.id
                    if nxt not in indices:
                        if same_track:
                            raise ValueError('prior boundary join is not on the current constituent track')
                        continue
                    i = indices[nxt]
                    neighbours = {path[j] for j in (i-1, i+1) if 0 <= j < len(path)}
                    direction = cellmath.FACING.get(record['direction'])
                    aligned = ((nxt[0]-at[0], nxt[1]-at[1]) == direction
                               and (at in neighbours or at not in indices and i in (0, len(path)-1)))
                    if not aligned:
                        if same_track:
                            raise ValueError('prior boundary join direction cannot connect to the current track')
                        continue
                    if not same_track:
                        raise ValueError('prior boundary join lacks matching constituent track identity')
                    distance = (nxt[0]-cx)**2+(nxt[1]-cz)**2
                    candidate = {**record, 'track_index': ti}
                    if ti not in by_track or distance < by_track[ti][0]:
                        by_track[ti] = distance, candidate
                    elif distance == by_track[ti][0] and candidate != by_track[ti][1]:
                        raise ValueError('conflicting prior boundary joins for one constituent track')
    return [{'line_id': line.id, 'tracks': [record]} for _distance, record in by_track.values()]


def _check_continuations(corridor, incoming, excludes):
    """The first rail remaining after overlap removal must meet the recorded old endpoint."""
    if corridor['failures']:
        return  # the normal spec validator reports the precise planner failure
    profiles = {track['track_index']: track for track in corridor['tracks']}
    rails = {(x, z): y for x, y, z, _shape, _block in corridor['rails']}
    for join in incoming:
        for record in join['tracks']:
            track = profiles.get(record['track_index'])
            if track is None:
                raise ValueError('prior boundary join has no continuation profile')
            uncovered = [tuple(q) for q in track['path']
                         if tuple(q) in rails and not any(a <= q[0] <= c and b <= q[1] <= d
                                                         for a, b, c, d in excludes)]
            if (not uncovered or uncovered[0] != tuple(record['next'])
                    or rails[uncovered[0]] != record['level']
                    or track['before'] != record['at']):
                raise ValueError('first uncovered rail does not connect to its recorded boundary join')


def plan_feature(lines, ground, cell, *, reg=None, crossing_roads=None, road_surfaces=None,
                 structures=None, inferred_deck_usable=None) -> dict:
    """Plan one feature from explicit evidence arrays and immutable through lines.

    ``structures`` is ``structure_evidence.by_osm_id`` for the overhead decks; unknown
    deck heights become railway gaps (see ``railway_plan.deck_evidence``). Lines that
    share rail columns are resolved here: the main line keeps its rails and the side
    track leaves ``junction`` gaps (``railway_plan.junction_gaps``); shared ground goes
    to one owner (``railway_plan.share_footprints``). Anything else still refuses.
    """
    prior = list(active_railways(reg)) if reg is not None else []
    excludes = [box for e in prior for box in e.meta.get('boxes', ())]
    joined = [_incoming_joins(line, prior, cell) if prior else [] for line in lines]

    def plan_one(i, junctions=None):
        return plan_line(lines[i], ground, tuple(cell), joined[i], crossing_roads=crossing_roads,
                         road_surfaces=road_surfaces, structures=structures,
                         inferred_deck_usable=inferred_deck_usable, junction_gaps=junctions)

    plans = [plan_one(i) for i in range(len(lines))]
    if len(plans) > 1 and not any(p['failures'] for p in plans):
        # Lines that share rail columns: the main line keeps them, side tracks leave gaps.
        for i, side in junction_gaps(plans).items():
            plans[i] = plan_one(i, side)
    if len(plans) > 1 and not any(p['failures'] for p in plans):
        share_footprints(plans)
    for line, joins, p in zip(lines, joined, plans):
        under, _decks = deck_evidence(line, structures, inferred_deck_usable)
        _check_continuations(p, joins, excludes)
        p['track_way_ids'] = [list(ways) for ways in line.track_way_ids]
        deck = {tuple(q) for span in line.overhead for q in span.deck}
        p['clear_to'] = []
        for x, z, original, surface, _kind in p['columns']:
            i, j = x - ground['x0'], z - ground['z0']
            h = int(ground['h'][i][j])
            top = max(original, surface + RAIL_CLEARANCE, h)
            if (x, z) in deck or ('kind' in ground and ground['kind'][i][j] == 4):
                ceiling = under.get((x, z), None)
                if ceiling is None:
                    ceiling = int(ground.get('deck_y', ground['h'])[i][j])
                top = min(top, ceiling - 1)
            p['clear_to'].append([x, z, top])
    spec = {'kind': KIND, 'name': feature_name(cell), 'cell': list(cell),
            'plans': plans, 'exclude_boxes': excludes, 'modifies': []}
    return load_spec_dict(spec)


def ground_for_box(box, *, world=None) -> dict:
    """Mosaic cached lidar height maps across a planner box; never download or read the game."""
    import numpy as np
    from lidar_heightmap import reference_world
    world = world or reference_world()
    x0, z0, x1, z1 = box
    result = {'x0': x0, 'z0': z0}
    for key in ('terrain', 'h', 'kind'):
        result[key] = np.zeros((x1-x0+1, z1-z0+1), dtype=np.int16)
    result['have'] = np.zeros(result['terrain'].shape, dtype=bool)
    c0, c1 = cellmath.cell_of(x0, z0), cellmath.cell_of(x1, z1)
    for tx in range(c0[0], c1[0]+1):
        for tz in range(c0[1], c1[1]+1):
            hm = world.heightmap((tx, tz))
            hx, hz = hm['x0'], hm['z0']
            a, b = max(x0, hx), max(z0, hz)
            c, d = min(x1+1, hx+len(hm['h'])), min(z1+1, hz+len(hm['h'][0]))
            dst = (slice(a-x0, c-x0), slice(b-z0, d-z0))
            src = (slice(a-hx, c-hx), slice(b-hz, d-hz))
            for key in ('terrain', 'h', 'kind'):
                result[key][dst] = hm[key][src]
            result['have'][dst] = np.isfinite(hm['terrain'][src]) & np.isfinite(hm['h'][src])
    return result


def run(path, *, do_apply=False) -> Plan:
    """Build one saved spec through the normal guarded, backed-up apply path."""
    spec = load_spec(path)
    plan = build(spec)
    from builder_provenance import builder_record, provenance
    from site_kit import apply
    apply(plan, dry=not do_apply, stage='feature', cell=spec['cell'],
          modifies=tuple(spec.get('modifies', ())), strict=True, readback_retries=2,
          meta={**metadata(spec), 'builder': builder_record('spec', path), **provenance(BUILDER, path)})
    return plan


def main(argv=None) -> int:
    """Build a saved railway spec, or create one from explicit cached evidence."""
    import script_env
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    script_env.add_world_arg(ap)
    ap.add_argument('spec', nargs='?')
    ap.add_argument('--cell')
    ap.add_argument('--railway-extract', type=Path)
    ap.add_argument('--points-extract', type=Path)
    ap.add_argument('--roads-extract', type=Path)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args(argv)
    script_env.activate_world_from_args(args)
    if args.spec:
        run(args.spec, do_apply=args.apply)
        return 0
    if not all((args.cell, args.railway_extract, args.points_extract, args.roads_extract)):
        ap.error('use a saved spec, or --cell with railway, points and roads extracts')
    if args.apply:
        ap.error('planning writes a spec only; review it then apply the saved spec')
    from cell_registry import view_registry
    from railway_evidence import extract_railway_box, read_map_points, build_lines
    from world_frame import load_frame, wgs84_to_mc
    from osm_footprints import osm_tags
    from ground_sources import line_cells, road_width, SURFACE_KIND
    cell = cellmath.parse_cell(args.cell)
    reg = view_registry()
    if covered(cell, reg):
        print('already covered by an earlier railway feature')
        return 0
    frame = load_frame()
    if not proposal(cell, reg, source=args.railway_extract, frame=frame):
        print('no through railway in this cell')
        return 0
    x0, z0, x1, z1 = cellmath.bounds(cell)
    cx, cz = (x0+x1+1)//2, (z0+z1+1)//2
    box = (cx-PLANNER_BOX//2, cz-PLANNER_BOX//2, cx+PLANNER_BOX//2-1, cz+PLANNER_BOX//2-1)
    roads = []
    for feat in json.loads(args.roads_extract.read_text())['features']:
        tags, geom = osm_tags(feat.get('properties') or {}), feat.get('geometry') or {}
        if not tags.get('highway') or geom.get('type') != 'LineString':
            continue
        roads.append({'osm_id': str(tags.get('osm_id') or tags.get('osm_way_id') or ''),
                      'tags': tags, 'points': [wgs84_to_mc(lat, lon, frame) for lon, lat, *_ in geom['coordinates']]})
    points = read_map_points(args.points_extract, frame)
    ways = extract_railway_box(box, source=args.railway_extract, frame=frame)
    lines = build_lines(ways, points, overhead=roads)
    surfaces, crossing_roads = {}, {}
    for road in roads:
        tags = road['tags']
        columns = line_cells(road['points'], road_width(tags)/2)
        if tags.get('bridge') not in ('yes', 'viaduct') and tags.get('tunnel') != 'yes':
            surfaces.update({q: SURFACE_KIND.get(tags.get('surface'), 'tarmac') for q in columns})
        for point in points:
            if tuple(point.at) in columns:
                crossing_roads[point.osm_id] = tags
    ground = ground_for_box(box)
    from structure_evidence import build_structures, by_osm_id
    rail_ways = [{'osm_id': w.osm_id, 'tags': dict(w.tags), 'points': list(w.points)} for w in ways]
    structures = by_osm_id(build_structures(roads + rail_ways, ground))
    spec = plan_feature(lines, ground, cell, reg=reg, crossing_roads=crossing_roads,
                        road_surfaces=surfaces, structures=structures)
    out = Path(script_env.ROOT) / 'site/builds' / (spec['name']+'.json')
    if out.exists():
        raise SystemExit(f'{out} exists; preserve the recorded spec or choose its explicit rebuild path')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, indent=1)+'\n')
    print(out)
    return 0
