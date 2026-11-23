"""Crossing, bridge and tunnel features that fill a gap left by line features.

A saved spec is ``site/builds/<kind>_<tx>_<tz>_<n>.json``. ``kind`` is
``crossing``, ``bridge`` or ``tunnel``. Negative cell coordinates use the usual
``m`` prefix, and ``n`` starts at 1. Bridge and tunnel use only the header
below; their geometry is not built here.

Header (every kind):

* ``kind``, ``name``, ``cell`` ``[tx, tz]``, ``n``
* ``abutments``: exactly two. Each names a landed line ``feature``, the
  ``line_id`` and ``source`` (crossing node, or bridge/tunnel ref) of the gap,
  and ``ends``: track index, rail cell ``at``, rail ``level``, and ``direction``
  into the gap. Both ends may belong to one line feature.
* ``evidence.osm``: the map tags the equipment flags are checked against.
* ``evidence.frames``: Street View frame ids (may be empty in a fixture).

Crossing body:

* ``road``: ``way_ids``, a ground surface kind, and the columns to pave,
  including every rail column.
* ``tracks``: one row per track, ``columns`` ordered from one join to the other.
* ``barrier``, ``light``, ``bell``: present only when the matching OSM tag is
  set. ``flag`` repeats that tag. Barrier ``blocks`` names a block id for each
  ``part`` a position uses (``bar`` and ``post`` are the usual parts). Light
  and bell each carry one ``block``. Positions are explicit. Ids are checked
  with :func:`block_reference.known_block`. A name in
  :data:`barrier_vocab.BARRIER_KINDS` resolves through that table.

A mapped road, or any crossing with a barrier, light or bell tag, is this
feature: the line leaves a gap. A footpath or private crossing with none of
those tags is not a gap (:func:`railway_plan.crossing_is_feature`). There is
no default style. The builder refuses (``NotReady``, nothing written) until
both abutting line features are landed and their end records match.

  python3 scripts/crossing_build.py --world WORLD PATH
  python3 scripts/crossing_build.py --world WORLD PATH --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import barrier_vocab as bv
import block_reference
import cellmath
from ground_palette import MATERIAL
from railway_feature import active_railways, place_rails
from railway_plan import RAIL_CLEARANCE, rail_rows
from site_kit import Plan

KIND = 'crossing'
KINDS = ('crossing', 'bridge', 'tunnel')
BUILDER = 'crossing_build'
_FLAGS = (
    ('barrier', 'crossing:barrier'),
    ('light', 'crossing:light'),
    ('bell', 'crossing:bell'),
)


class NotReady(Exception):
    """Abutting line features are not both landed. Nothing is written."""


def feature_name(kind: str, cell, n: int) -> str:
    """Stable id for one structure that starts in a cell.

    @param kind: ``crossing``, ``bridge`` or ``tunnel``
    @param cell: ``(tx, tz)``
    @param n: index among that kind in the cell, starting at 1
    @returns: ``<kind>_<tx>_<tz>_<n>`` with negatives written ``m``
    """
    slug = '_'.join(str(int(v)).replace('-', 'm') for v in cell)
    return f'{kind}_{slug}_{int(n)}'


def resolve_block(block: str) -> str:
    """Block id to place, resolving a barrier kind through the shared vocabulary.

    @param block: block id, or a :data:`barrier_vocab.BARRIER_KINDS` name
    @returns: short block id, keeping a ``[state]`` suffix on a real block
    @raises ValueError: when the id is not a known block
    """
    short = str(block).strip().removeprefix('minecraft:')
    base, sep, state = short.partition('[')
    if base in bv.BARRIER_KINDS:
        base = bv.DEFAULT_BLOCK[base]
        short = base
    elif sep:
        short = base + sep + state
    if not block_reference.known_block(base):
        raise ValueError(f'not a valid block: {block}')
    return short


def _ints(value, n: int, what: str) -> list:
    if (not isinstance(value, list) or len(value) != n
            or any(isinstance(v, bool) or not isinstance(v, int) for v in value)):
        raise ValueError(what)
    return value


def _words(value, what: str) -> list:
    if (not isinstance(value, list) or not value
            or any(not isinstance(v, str) or not v for v in value)):
        raise ValueError(what)
    return value


def _check_flags(raw: dict) -> None:
    osm = raw['evidence']['osm']
    for name, tag in _FLAGS:
        recorded = osm.get(tag)
        wanted = None if recorded in (None, '', 'no') else str(recorded)
        section = raw.get(name)
        if wanted is None and section is not None:
            raise ValueError(f'{name} is specified but {tag} is not set in the evidence')
        if wanted is not None and section is None:
            raise ValueError(f'{tag} is {recorded!r} but the spec places no {name}')
        if wanted is not None and str(section.get('flag')) != wanted:
            raise ValueError(f'{name} flag {section.get("flag")!r} disagrees with {tag} {recorded!r}')


def _check_equipment(raw: dict) -> None:
    barrier = raw.get('barrier')
    if barrier is not None:
        blocks = barrier.get('blocks')
        if not isinstance(blocks, dict) or not blocks:
            raise ValueError('barrier needs block ids')
        resolved = {part: resolve_block(block) for part, block in blocks.items()}
        if any(not isinstance(part, str) or not part for part in resolved):
            raise ValueError('barrier part names must be strings')
        positions = barrier.get('positions')
        if not isinstance(positions, list) or not positions:
            raise ValueError('barrier needs positions')
        for pos in positions:
            if not isinstance(pos, dict) or pos.get('part') not in resolved:
                raise ValueError('barrier position needs a known part')
            _ints(pos.get('at'), 3, 'barrier position needs integer x, y, z')
        barrier['blocks'] = {part: resolved[part] for part in blocks}
    for name in ('light', 'bell'):
        section = raw.get(name)
        if section is None:
            continue
        section['block'] = resolve_block(section.get('block', ''))
        positions = section.get('positions')
        if not isinstance(positions, list) or not positions:
            raise ValueError(f'{name} needs positions')
        for pos in positions:
            _ints(pos, 3, f'{name} position needs integer x, y, z')


def _parse_abutment(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError('abutment must be an object')
    feature = raw.get('feature')
    line_id = raw.get('line_id')
    if not isinstance(feature, str) or not feature or not isinstance(line_id, str) or not line_id:
        raise ValueError('abutment needs a feature id and a line id')
    source = raw.get('source')
    if source is not None and not isinstance(source, str):
        raise ValueError('abutment source must be a string')
    ends = raw.get('ends')
    if not isinstance(ends, list) or not ends:
        raise ValueError('abutment needs join ends')
    parsed = []
    for end in ends:
        if not isinstance(end, dict):
            raise ValueError('join end must be an object')
        track = end.get('track_index')
        if isinstance(track, bool) or not isinstance(track, int) or track < 0:
            raise ValueError('join end needs a track index')
        if end.get('direction') not in cellmath.FACING:
            raise ValueError('join end direction must be a facing')
        parsed.append({
            'track_index': track,
            'at': _ints(end.get('at'), 2, 'join end needs integer x, z'),
            'level': _ints([end.get('level')], 1, 'join end needs an integer level')[0],
            'direction': end['direction'],
        })
    if len({row['track_index'] for row in parsed}) != len(parsed):
        raise ValueError('an abutment repeats a track')
    return {'feature': feature, 'line_id': line_id, 'source': source or '', 'ends': parsed}


def _check_tracks(raw: dict) -> None:
    left = {end['track_index'] for end in raw['abutments'][0]['ends']}
    right = {end['track_index'] for end in raw['abutments'][1]['ends']}
    if left != right:
        raise ValueError('the two abutments must name the same tracks')
    tracks = raw.get('tracks')
    if not isinstance(tracks, list) or {row.get('track_index') for row in tracks} != left:
        raise ValueError('tracks must match the abutment ends')
    rail_cells = set()
    for track in tracks:
        columns = track.get('columns')
        if not isinstance(columns, list) or not columns:
            raise ValueError('a track needs columns')
        parsed = [_ints(column, 2, 'track column needs integer x, z') for column in columns]
        if len({tuple(column) for column in parsed}) != len(parsed):
            raise ValueError('a track repeats a column')
        for a, b in zip(parsed, parsed[1:]):
            if (b[0] - a[0], b[1] - a[1]) not in cellmath.N4:
                raise ValueError('track is not 4-connected')
        ends = [end for abutment in raw['abutments'] for end in abutment['ends']
                if end['track_index'] == track['track_index']]
        _join_neighbours(ends, parsed)
        track['columns'] = parsed
        rail_cells.update(tuple(column) for column in parsed)
    road = raw.get('road')
    if not isinstance(road, dict):
        raise ValueError('crossing needs a road')
    road['way_ids'] = _words(road.get('way_ids'), 'road needs way ids')
    if road.get('surface') not in MATERIAL:
        raise ValueError('unknown road surface')
    columns = road.get('columns')
    if not isinstance(columns, list) or not columns:
        raise ValueError('road needs columns')
    parsed = [_ints(column, 2, 'road column needs integer x, z') for column in columns]
    if not rail_cells <= {tuple(column) for column in parsed}:
        raise ValueError('the road must cover every rail column')
    road['columns'] = parsed
    levels = {end['level'] for abutment in raw['abutments'] for end in abutment['ends']}
    if len(levels) != 1:
        raise ValueError('crossing joins are not at one level')
    rail_level = next(iter(levels))
    occupied = {(column[0], rail_level, column[1]) for column in rail_cells}
    for x, y, z, _block in _equipment_rows(raw):
        if (x, y, z) in occupied:
            raise ValueError('equipment must not replace a rail')


def _join_neighbours(ends, columns):
    """Abutment cells just outside a track, in column order."""
    def hits(end, column):
        step = cellmath.FACING[end['direction']]
        return (end['at'][0] + step[0], end['at'][1] + step[1]) == (column[0], column[1])

    first, last = columns[0], columns[-1]
    if len(columns) == 1:
        if len(ends) != 2 or not all(hits(end, first) for end in ends):
            raise ValueError('track does not meet both recorded joins')
        return ends[0]['at'], ends[1]['at']
    start = [end for end in ends if hits(end, first)]
    finish = [end for end in ends if hits(end, last)]
    if len(start) != 1 or len(finish) != 1 or start[0] is finish[0]:
        raise ValueError('track does not run from one recorded join to the other')
    return start[0]['at'], finish[0]['at']


def _equipment_rows(raw: dict) -> list:
    rows = []
    barrier = raw.get('barrier')
    if barrier:
        for pos in barrier['positions']:
            rows.append((*pos['at'], barrier['blocks'][pos['part']]))
    for name in ('light', 'bell'):
        section = raw.get(name)
        if section:
            for at in section['positions']:
                rows.append((*at, section['block']))
    return rows


def load_spec_dict(raw) -> dict:
    """Validate a crossing, bridge or tunnel spec. No journal and no world calls.

    @param raw: decoded JSON object
    @returns: the same object, with block ids resolved
    @raises ValueError: when the spec is malformed
    """
    if not isinstance(raw, dict) or raw.get('kind') not in KINDS:
        raise ValueError('spec kind must be crossing, bridge or tunnel')
    cell = raw.get('cell')
    _ints(cell, 2, 'cell must be two integers')
    n = raw.get('n')
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError('n must be an integer starting at 1')
    if raw.get('name') != feature_name(raw['kind'], cell, n):
        raise ValueError('name must match kind, cell and n')
    abutments = raw.get('abutments')
    if not isinstance(abutments, list) or len(abutments) != 2:
        raise ValueError('spec needs exactly two abutments')
    raw['abutments'] = [_parse_abutment(row) for row in abutments]
    if raw['kind'] == 'crossing' and any(not row['source'] for row in raw['abutments']):
        raise ValueError('crossing abutment needs a source id')
    evidence = raw.get('evidence')
    if not isinstance(evidence, dict):
        raise ValueError('spec needs evidence')
    osm = evidence.get('osm')
    if not isinstance(osm, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in osm.items()):
        raise ValueError('evidence.osm must be a map of strings')
    frames = evidence.get('frames')
    if not isinstance(frames, list) or any(not isinstance(v, str) or not v for v in frames):
        raise ValueError('evidence.frames must be a list of frame ids')
    if not isinstance(raw.get('modifies', []), list) or any(not isinstance(v, str) for v in raw.get('modifies', ())):
        raise ValueError('modifies must be plan names')
    if raw['kind'] == 'crossing':
        _check_flags(raw)
        _check_equipment(raw)
        _check_tracks(raw)
    elif any(key in raw for key in ('road', 'tracks', 'barrier', 'light', 'bell')):
        raise ValueError(f'{raw["kind"]} geometry is not part of this spec yet')
    return raw


def load_spec(path) -> dict:
    """Read and validate a saved spec. No world calls.

    @param path: ``site/builds/<name>.json``
    @returns: validated spec
    """
    return load_spec_dict(json.loads(Path(path).read_text()))


def read_joins(reg, feature: str) -> list:
    """Gap records on one landed railway feature.

    @param reg: cell journal
    @param feature: line feature id
    @returns: that feature's gap records, or an empty list when it is not landed
    """
    for entry in active_railways(reg):
        if entry.plan == feature:
            return list(entry.meta.get('gaps') or [])
    return []


def _end_matches(gap, end, abutment, kind: str) -> bool:
    if gap.get('kind') != kind or str(gap.get('line_id')) != abutment['line_id']:
        return False
    if str(gap.get('source') or '') != abutment['source']:
        return False
    for recorded in gap.get('ends') or ():
        if (recorded.get('track_index') == end['track_index']
                and list(recorded.get('at')) == list(end['at'])
                and recorded.get('level') == end['level']
                and recorded.get('direction') == end['direction']):
            return True
    return False


def require_ready(spec, reg) -> None:
    """Refuse until every abutment is a landed line with a matching end.

    @param spec: validated spec
    @param reg: cell journal
    @raises NotReady: when an abutting feature is missing or undone
    @raises ValueError: when a landed feature has no matching end
    """
    if reg is None:
        raise NotReady('not ready: no journal to read abutting line features')
    landed = {}
    for entry in active_railways(reg):
        landed[entry.plan] = list(entry.meta.get('gaps') or [])
    missing = []
    for abutment in spec['abutments']:
        if abutment['feature'] not in landed:
            missing.append(abutment['feature'])
            continue
        for end in abutment['ends']:
            if not any(_end_matches(gap, end, abutment, spec['kind']) for gap in landed[abutment['feature']]):
                raise ValueError(f'abutment on {abutment["feature"]} does not match a recorded gap end')
    if missing:
        names = ', '.join(dict.fromkeys(missing))
        raise NotReady(f'not ready: abutting line features missing ({names})')


def metadata(spec) -> dict:
    """Journal fields for one crossing, bridge or tunnel.

    @param spec: validated spec
    @returns: meta stored on the build entry
    """
    return {
        'kind': spec['kind'],
        'tiled_backup': True,
        'cell': list(spec['cell']),
        'abutments': spec['abutments'],
        'line_features': list(dict.fromkeys(row['feature'] for row in spec['abutments'])),
    }


def build(raw, reg) -> Plan:
    """Place a crossing, or refuse when the line features or the geometry are not ready.

    Bridge and tunnel pass the same gate and then refuse: their geometry is a
    later ticket. A refusal writes nothing.

    @param raw: spec dict
    @param reg: cell journal to read join records from
    @returns: the site plan, before apply
    @raises NotReady: when an abutting line feature is missing
    @raises ValueError: when the spec does not match the recorded ends, or the kind has no geometry yet
    """
    spec = load_spec_dict(raw)
    require_ready(spec, reg)
    if spec['kind'] != KIND:
        raise ValueError(f'{spec["kind"]} geometry is not implemented')
    level = next(end['level'] for end in spec['abutments'][0]['ends'])
    plan = Plan(spec['name'], input_cells=[spec['cell']])
    surface = MATERIAL[spec['road']['surface']]
    for x, z in spec['road']['columns']:
        plan.fill((x, level - 3, z), (x, level - 2, z), 'stone')
        plan.set(x, level - 1, z, surface)
    for track in spec['tracks']:
        columns = [tuple(column) for column in track['columns']]
        ends = [end for abutment in spec['abutments'] for end in abutment['ends']
                if end['track_index'] == track['track_index']]
        before, after = _join_neighbours(ends, columns)
        for x, z in columns:
            plan.fill((x, level, z), (x, level - 1 + RAIL_CLEARANCE, z), 'air')
        rails, torches, supports = rail_rows(
            columns, [level - 1] * len(columns), before=tuple(before), after=tuple(after))
        place_rails(plan, rails, torches, supports)
    for x, y, z, block in _equipment_rows(spec):
        plan.set(x, y, z, block)
    plan.outside_footprint_ok = 'level crossing; joins and road recorded in the crossing spec'
    plan.capture_inputs()
    return plan


def run(path, *, do_apply=False, reg=None) -> Plan:
    """Build one saved spec. Apply journals it with a tiled backup and read-back.

    @param path: spec file
    @param do_apply: when False, dry-run through the normal apply path
    @param reg: journal to read; the active world registry when omitted
    @returns: the plan
    @raises NotReady: when the abutting line features are not landed
    """
    spec = load_spec(path)
    if reg is None:
        from cell_registry import view_registry
        reg = view_registry()
    plan = build(spec, reg)
    from builder_provenance import builder_record, provenance
    from site_kit import apply
    apply(plan, dry=not do_apply, stage='feature-' + spec['name'], cell=spec['cell'],
          modifies=tuple(spec.get('modifies', ())), strict=True, readback_retries=2,
          meta={**metadata(spec), 'builder': builder_record('spec', path), **provenance(BUILDER, path)})
    return plan


def main(argv=None) -> int:
    """Dry-run or apply a saved crossing, bridge or tunnel spec.

    @param argv: argument vector without the program name
    @returns: 0, or 1 when the feature is not ready
    """
    import script_env
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    script_env.add_world_arg(ap)
    ap.add_argument('spec')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args(argv)
    script_env.activate_world_from_args(args)
    try:
        run(args.spec, do_apply=args.apply)
    except NotReady as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0
