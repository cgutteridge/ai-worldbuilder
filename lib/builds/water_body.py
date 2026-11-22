#!/usr/bin/env python3
"""Water-body features (ponds and rivers): spec, provenance and build.

A water body is a cell-plan feature like a building: outline columns, a water
surface level, a bank ring and an optional shoreline edge. Ground leaves mapped
water alone (``ground_layer.free_columns``); this module owns those columns as a
feature build.

A run is a JSON spec in ``site/builds/<name>.json``:

  name           plan name (also the cell journal plan / feature id)
  notes          free text (never read by code)
  modifies       earlier builds this one may overwrite (default [])
  outline        [[x, z], ...] water columns (from the vector map or aerial)
  water_level    integer Y of the water surface block
  water_level_profile  optional {"segments": [{"start": 0, "end": 6, "level": 55},
                           {"start": 6, "end": 12, "level": 54}],
                           "columns": [[x, z, distance], ...]}
                 Segments serialize water_level.water_level_profile() output.
                 Distances increase downstream; segments must be contiguous,
                 ordered and non-rising, with integer levels. Only a single
                 segment may have zero length. Required water_level equals the
                 first segment's level; the profile supplies every local level.
                 Each outline column (including clip context) appears exactly
                 once in columns, ordered by non-decreasing finite distance,
                 within the segment coverage. A join belongs to the downstream
                 segment; the final endpoint belongs to the last segment.
                 Water, bed and clear_above use each column's local level.
                 Banks use the nearest water column in N8 distance; ties use
                 smallest reach distance, then x, z, before clipping. Only the
                 whole outline's true shoreline is banked, never segment joins.
                 Adjacent clips must retain the same segment profile and column
                 distances in their CLIP_REACH context; do not restart distances
                 or recompute profiles at cell edges.
  depth          water column height including the surface (default 1; >= 1)
  floor          bed material under the deepest water (default dirt)
  clear_above    optional int >= 0 (default 0): air placed over the water columns
                 from water_level + 1 up this many layers, so a pond sits below
                 the surrounding paving instead of under a ground lid
  bank           {"material": "<block>", "width"?: 1, "rise"?: 0} shore ring
                 (N8 × width) at water_level + rise; rise lifts the coping to
                 paving height around a sunken pond
  edge           optional {"material": "<block>"} shoreline rim on bank cells
                 that touch water; absent → bank material only
  clip           optional {"cell": [tx, tz]} (#321): build only this cell's part of a
                 body that crosses cell lines. ``outline`` then also lists the body's
                 water up to ``CLIP_REACH`` blocks beyond the cell, so the bank ring
                 runs on real shorelines only, never along the cell edge. Each cell
                 of the body has its own spec and plan; together they build the
                 whole body without a seam. A cell with none of the body's
                 water but within reach of its shore has a bank-only clip
                 (proposed only when the default bank width can reach that cell).
                 ``bank.width`` must not exceed ``CLIP_REACH``. A clip plan id as
                 ``name`` requires ``clip`` (#353).
  provenance     shared schema from ``lib/site/provenance.py`` (#151):
                   {source, confidence, evidence_ids, unresolved_reason}

Usage:  python3 scripts/water_body_build.py --world <world> site/builds/<name>.json [--apply]
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import cellmath
from cellmath import N8
import provenance as prov
from water_level import LevelSegment, level_at_distance
from site_kit import Plan, apply, preview  # noqa: E402

DEFAULT_DEPTH = 1
DEFAULT_FLOOR = 'dirt'
DEFAULT_BANK_WIDTH = 1
MIN_WATER_COLUMNS = 4
CLIP_REACH = 8          # blocks of a body's water a clipped spec carries beyond its cell (#321)
# Clip plan ids from :func:`clip_plan_id` only: OSM ``w<digits>`` or synthetic ``water_<x>_<z>``,
# then ``_<tx>_<tz>`` (m for minus). Do not match free-form names like ``river_itchen_63_m45``.
_CLIP_PLAN_ID = re.compile(
    r'^(?P<ref>w\d+|water_-?\d+_-?\d+)_(?P<tx>m?\d+)_(?P<tz>m?\d+)$'
)


class WaterBodyError(SystemExit):
    """Invalid water-body spec (raised as SystemExit for CLI parity with feature_build)."""


def _fail(path: str | Path | None, msg: str) -> None:
    prefix = f'{path}: ' if path else ''
    raise WaterBodyError(f'{prefix}{msg}')


def _as_columns(outline: Sequence) -> list[tuple[int, int]]:
    cols: list[tuple[int, int]] = []
    for item in outline:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError(f'outline entry must be [x, z], got {item!r}')
        cols.append((int(item[0]), int(item[1])))
    return cols


def _validate_provenance(path: str | Path | None, raw: Any, root: Path | None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        _fail(path, 'provenance must be an object')
    try:
        return prov.provenance_record(
            source=str(raw.get('source', '')),
            confidence=str(raw.get('confidence', '')),
            evidence_ids=list(raw.get('evidence_ids') or []),
            unresolved_reason=str(raw.get('unresolved_reason') or ''),
            root=root,
        )
    except prov.ProvenanceError as exc:
        _fail(path, f'provenance: {exc}')


def _validate_bank(path: str | Path | None, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        _fail(path, 'bank must be an object {"material", "width"?}')
    material = str(raw.get('material') or '').strip()
    if not material:
        _fail(path, 'bank.material must be a non-empty block id')
    width = int(raw.get('width', DEFAULT_BANK_WIDTH))
    if width < 1:
        _fail(path, 'bank.width must be >= 1')
    rise = raw.get('rise', 0)
    if not isinstance(rise, int) or isinstance(rise, bool) or rise < 0:
        _fail(path, 'bank.rise must be an int >= 0')
    return {'material': material, 'width': width, 'rise': rise}


def _validate_edge(path: str | Path | None, raw: Any) -> dict[str, str] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        _fail(path, 'edge must be an object {"material"} or omitted')
    material = str(raw.get('material') or '').strip()
    if not material:
        _fail(path, 'edge.material must be a non-empty block id')
    return {'material': material}


def _validate_level_profile(path: str | Path | None, raw: Any,
                            outline: Sequence[tuple[int, int]], level: int) -> dict:
    """Validate serialized profiler output and its explicit column assignments."""
    if not isinstance(raw, dict):
        _fail(path, 'water_level_profile must be an object {"segments", "columns"}')
    rows = raw.get('segments')
    if not isinstance(rows, list) or not rows:
        _fail(path, 'water_level_profile.segments must be a non-empty list')
    segments: list[LevelSegment] = []
    for row in rows:
        if not isinstance(row, dict) or not all(k in row for k in ('start', 'end', 'level')):
            _fail(path, 'water_level_profile segment must have start, end and level')
        start, end, local = row['start'], row['end'], row['level']
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                   and math.isfinite(v) for v in (start, end)):
            _fail(path, 'water_level_profile segment distances must be finite numbers')
        if end < start or (end == start and len(rows) != 1):
            _fail(path, 'water_level_profile segments must have positive length (except a single point)')
        if not isinstance(local, int) or isinstance(local, bool):
            _fail(path, 'water_level_profile segment level must be an int')
        if segments and start != segments[-1].end:
            _fail(path, 'water_level_profile segments must be ordered and contiguous without gaps or overlaps')
        if segments and local > segments[-1].level:
            _fail(path, 'water_level_profile levels must not rise downstream')
        segments.append(LevelSegment(start, end, local))
    if segments[0].level != level:
        _fail(path, 'water_level must equal the first water_level_profile segment level')
    columns = raw.get('columns')
    if not isinstance(columns, list) or not columns:
        _fail(path, 'water_level_profile.columns must be a non-empty list of [x, z, distance]')
    outline_set = set(outline)
    seen: set[tuple[int, int]] = set()
    previous = None
    for row in columns:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            _fail(path, 'water_level_profile column must be [x, z, distance]')
        x, z, distance = row
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (x, z)):
            _fail(path, 'water_level_profile column coordinates must be integers')
        if (not isinstance(distance, (int, float)) or isinstance(distance, bool)
                or not math.isfinite(distance)):
            _fail(path, 'water_level_profile column distance must be a finite number')
        if previous is not None and distance < previous:
            _fail(path, 'water_level_profile columns must be ordered downstream by distance')
        previous = distance
        if (x, z) in seen:
            _fail(path, 'water_level_profile columns overlap: each outline column must appear once')
        if (x, z) not in outline_set:
            _fail(path, 'water_level_profile column is outside outline')
        seen.add((x, z))
        try:
            level_at_distance(segments, distance)
        except ValueError as exc:
            _fail(path, f'water_level_profile column: {exc}')
    if seen != outline_set:
        _fail(path, 'water_level_profile columns must cover every outline column, including clip context')
    return {'segments': [s._asdict() for s in segments], 'columns': [list(c) for c in columns]}


def _parse_clip_coord(token: str) -> int:
    return -int(token[1:]) if token.startswith('m') else int(token)


def parse_clip_plan_id(name: str) -> tuple[str, tuple[int, int]] | None:
    """Parse a per-cell clip plan id ``<ref>_<tx>_<tz>``, or None (#353).

    Only ids :func:`clip_plan_id` can produce: OSM ``w123_63_m45`` or synthetic
    ``water_<x>_<z>_<tx>_<tz>``. Free-form names that happen to end in
    ``_<n>_<n>`` (for example a legacy ``river_itchen_63_m45`` whole plan) are
    not clips.

    @param name: plan / spec name
    @returns: ``(ref, (tx, tz))`` when ``name`` is a clip id, else None
    """
    m = _CLIP_PLAN_ID.match(str(name).strip())
    if not m:
        return None
    return m.group('ref'), (_parse_clip_coord(m.group('tx')), _parse_clip_coord(m.group('tz')))


def _validate_clip(path: str | Path | None, raw: Any, outline: Sequence[tuple[int, int]],
                   bank: Mapping[str, Any]) -> dict[str, list[int]] | None:
    if raw is None:
        return None
    cell = raw.get('cell') if isinstance(raw, dict) else None
    if not isinstance(cell, (list, tuple)) or len(cell) != 2:
        _fail(path, 'clip must be an object {"cell": [tx, tz]} or omitted')
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in cell):
        _fail(path, 'clip.cell must be two integers [tx, tz]')
    tx, tz = int(cell[0]), int(cell[1])
    x0, z0, x1, z1 = cellmath.bounds((tx, tz), margin=bank['width'])
    if not any(x0 <= x <= x1 and z0 <= z <= z1 for x, z in outline):
        _fail(path, f'clip cell {tx},{tz} gets no water or bank at bank.width {bank["width"]}; '
                    'dismiss its plan with cell_registry.py dismiss-feature')
    if bank['width'] > CLIP_REACH:
        _fail(path, f'bank.width must be <= {CLIP_REACH} (CLIP_REACH) on a clipped spec')
    return {'cell': [tx, tz]}


def load_spec_dict(spec: dict, *, path: str | Path | None = None, root: Path | None = None) -> dict:
    """Validate an in-memory water-body spec and fill defaults.

    @param spec: raw spec dict
    @param path: optional path used only in error messages
    @param root: world root for provenance evidence-id checks (optional)
    @returns: normalised spec dict
    @raises WaterBodyError: on invalid input
    """
    for key in ('name', 'outline', 'water_level', 'bank', 'provenance'):
        if key not in spec:
            _fail(path, f'missing "{key}"')
    name = str(spec['name']).strip()
    if not name:
        _fail(path, 'name must be non-empty')
    try:
        outline = _as_columns(spec['outline'])
    except ValueError as exc:
        _fail(path, str(exc))
    if not outline:
        _fail(path, 'outline must list at least one [x, z] column')
    level = spec['water_level']
    if not isinstance(level, int) or isinstance(level, bool):
        _fail(path, 'water_level must be an int')
    profile = None
    if 'water_level_profile' in spec:
        profile = _validate_level_profile(path, spec['water_level_profile'], outline, level)
    depth = spec.get('depth', DEFAULT_DEPTH)
    if not isinstance(depth, int) or isinstance(depth, bool) or depth < 1:
        _fail(path, 'depth must be an int >= 1')
    floor = str(spec.get('floor', DEFAULT_FLOOR)).strip() or DEFAULT_FLOOR
    clear_above = spec.get('clear_above', 0)
    if not isinstance(clear_above, int) or isinstance(clear_above, bool) or clear_above < 0:
        _fail(path, 'clear_above must be an int >= 0')
    bank = _validate_bank(path, spec['bank'])
    edge = _validate_edge(path, spec.get('edge'))
    clip = _validate_clip(path, spec.get('clip'), outline, bank)
    # Clip plan ids without ``clip`` build neighbour water out to CLIP_REACH and bank
    # that artificial edge (#353). Require the field when the name is a clip id, and
    # keep the named cell aligned with clip.cell when both are present.
    parsed = parse_clip_plan_id(name)
    if clip is None and parsed is not None:
        _fail(path, f'name {name!r} is a clip plan id; set clip {{"cell": [tx, tz]}} '
                    '(or use a non-clip name for a legacy whole-body plan)')
    if clip is not None and parsed is not None and tuple(parsed[1]) != tuple(clip['cell']):
        _fail(path, f'name {name!r} clip cell {parsed[1][0]},{parsed[1][1]} does not match '
                    f'clip.cell {clip["cell"][0]},{clip["cell"][1]}')
    provenance = _validate_provenance(path, spec['provenance'], root)
    out = {
        'name': name,
        'notes': spec.get('notes', ''),
        'modifies': list(spec.get('modifies') or []),
        'outline': [[x, z] for x, z in outline],
        'water_level': level,
        'depth': depth,
        'floor': floor,
        'clear_above': clear_above,
        'bank': bank,
        'edge': edge,
        'clip': clip,
        'provenance': provenance,
    }
    if profile is not None:
        out['water_level_profile'] = profile
    return out


def load_spec(path, *, root: Path | None = None) -> dict:
    """Read and validate a water-body build spec from JSON.

    @param path: spec JSON path
    @param root: optional world root for evidence-id checks
    @returns: normalised spec dict
    """
    p = Path(path)
    return load_spec_dict(json.loads(p.read_text()), path=p, root=root)


def water_body_plan_item(
    *,
    label: str,
    outline: Sequence[Sequence[int] | tuple[int, int]],
    cell: tuple[int, int] | Sequence[int],
    refs: Sequence[str] = (),
    water_level: int | None = None,
    bank: Mapping[str, Any] | None = None,
    edge: Mapping[str, Any] | None = None,
    source: str | None = None,
    confidence: str = 'high',
    evidence_ids: Sequence[str] = (),
    unresolved_reason: str = '',
    root: Path | None = None,
) -> dict[str, Any]:
    """Build one water-body plan item with shared provenance (#151).

    @param label: human-readable name
    @param outline: water columns
    @param cell: ``(tx, tz)`` this body sits in
    @param refs: vector-map / evidence refs (osm ids, …)
    @param water_level: surface Y when known; ``None`` until read
    @param bank: bank dict when known
    @param edge: edge dict when known
    @param source: provenance source kind; omitted when the candidate has no source yet
    @param confidence: provenance confidence when ``source`` is set
    @param evidence_ids: ``registry/evidence`` ids
    @param unresolved_reason: non-empty when level/bank/edge still open
    @param root: world root for evidence checks
    @returns: plan-item dict (``kind`` ``water_body``)
    """
    cols = _as_columns(outline)
    xs = [c[0] for c in cols]
    zs = [c[1] for c in cols]
    item: dict[str, Any] = {
        'kind': 'water_body',
        'label': label,
        'refs': [str(r) for r in refs],
        'bbox': (min(xs), min(zs), max(xs), max(zs)) if cols else (0, 0, 0, 0),
        'area': len(cols),
        'outline': [[x, z] for x, z in cols],
        'water_level': water_level,
        'bank': dict(bank) if bank else None,
        'edge': dict(edge) if edge else None,
        'cells': [[int(cell[0]), int(cell[1])]],
    }
    if source is not None:
        prov.attach_provenance(
            item,
            source=source,
            confidence=confidence,
            evidence_ids=evidence_ids,
            unresolved_reason=unresolved_reason,
            root=root,
        )
    return item


def clip_plan_id(ref: str, cell: tuple[int, int] | Sequence[int]) -> str:
    """Plan id of one cell's clip of a water body over cell lines (#321).

    @param ref: the body's map ref (``w123``) or synthetic ``water_<x>_<z>`` id
    @param cell: ``(tx, tz)`` of the clip
    @returns: ``<ref>_<tx>_<tz>`` with ``m`` for a minus, e.g. ``w123_63_m45``
    """
    tag = '_'.join(f"{'m' if int(v) < 0 else ''}{abs(int(v))}" for v in cell)
    return f'{ref}_{tag}'


def clip_candidate(body: Mapping[str, Any], cell: tuple[int, int],
                   *, bank_width: int = DEFAULT_BANK_WIDTH) -> dict[str, Any] | None:
    """One cell's clip of a whole water-body candidate, or None when no water is within reach (#321).

    The clip keeps the body's refs and provenance and gains a per-cell ``id``, ``clip``, ``cells``
    and in-cell ``area`` / ``bbox``. Its ``outline`` is the in-cell water plus the body's water
    within ``CLIP_REACH`` of the cell, which :func:`build` needs to bank real shores only. A cell
    with no water of its own gets a bank-only clip only when water lies within ``bank_width``
    (default ``DEFAULT_BANK_WIDTH``) of the cell: farther water inside ``CLIP_REACH`` alone used
    to propose candidates that ``load_spec_dict`` then refused (#353).

    @param body: whole-body candidate (``outline``, ``refs``, …)
    @param cell: ``(tx, tz)``
    @param bank_width: reach used to decide bank-only proposals (default bank width)
    @returns: clip candidate dict, or None
    """
    cols = [(int(x), int(z)) for x, z in body['outline']]
    x0, z0, x1, z1 = cellmath.bounds(tuple(cell), margin=CLIP_REACH)
    near = sorted(q for q in cols if x0 <= q[0] <= x1 and z0 <= q[1] <= z1)
    if not near:
        return None
    inside = [q for q in near if cellmath.cell_of(*q) == tuple(cell)]
    if not inside:
        reach = max(1, int(bank_width))
        bx0, bz0, bx1, bz1 = cellmath.bounds(tuple(cell), margin=reach)
        if not any(bx0 <= q[0] <= bx1 and bz0 <= q[1] <= bz1 for q in cols):
            return None
    xs, zs = [x for x, _ in inside or near], [z for _, z in inside or near]
    what = f'{len(inside)} columns' if inside else 'bank only'
    return {
        **body,
        'id': clip_plan_id(body['refs'][0], cell),
        'label': f"{body['label']}, clip {cell[0]},{cell[1]} ({what})",
        'bbox': (min(xs), min(zs), max(xs), max(zs)),
        'area': len(inside),
        'outline': [[x, z] for x, z in near],
        'cells': [[int(cell[0]), int(cell[1])]],
        'clip': {'cell': [int(cell[0]), int(cell[1])]},
    }


def bank_columns_in_cell(
    outline: Iterable[Sequence[int] | tuple[int, int]],
    cell: tuple[int, int] | Sequence[int],
    *,
    width: int = DEFAULT_BANK_WIDTH,
) -> set[tuple[int, int]]:
    """Bank-ring columns of ``outline`` that fall inside ``cell`` (#353).

    Used to judge whether a bank-only clip is already built (no in-cell water to
    cover by outline alone).

    @param outline: water columns (clip outline may include neighbour water)
    @param cell: ``(tx, tz)``
    @param width: bank ring width
    @returns: set of ``(x, z)`` bank columns in the cell
    """
    cols = {(int(x), int(z)) for x, z in outline}
    ring = _bank_ring(cols, max(1, int(width)))
    key = (int(cell[0]), int(cell[1]))
    return {q for q in ring if cellmath.cell_of(*q) == key}


def water_body_components(
    water: Iterable[tuple[int, int]],
    *,
    cell: tuple[int, int],
    min_columns: int = MIN_WATER_COLUMNS,
    refs_for=None,
    column_refs: Mapping[tuple[int, int], str] | None = None,
    provenance_source: str | None = None,
    provenance_confidence: str = 'high',
    unresolved_reason: str = 'water level and bank not yet read',
    root: Path | None = None,
) -> list[dict[str, Any]]:
    """Package connected water columns as water-body feature candidates.

    @param water: water columns (vector map polygon water and/or aerial)
    @param cell: ``(tx, tz)``
    @param min_columns: drop slivers smaller than this
    @param refs_for: optional ``(component) -> [ref, ...]``; default uses
        ``column_refs`` when given, else synthetic ``water_<xmin>_<zmin>``
    @param column_refs: optional ``(x, z) -> osm way ref`` map (#163)
    @param provenance_source: when set, attach shared provenance (e.g. ``vector_map``)
    @param provenance_confidence: confidence when attaching provenance
    @param unresolved_reason: reason while level/bank are still unread
    @param root: world root for evidence-id checks when attaching provenance
    @returns: list of candidate dicts (``kind`` ``water_body``)
    """
    out: list[dict[str, Any]] = []
    for comp in sorted(cellmath.components(water, N8), key=lambda c: (min(c), len(c))):
        if len(comp) < min_columns:
            continue
        xs = [x for x, _ in comp]
        zs = [z for _, z in comp]
        bbox = (min(xs), min(zs), max(xs), max(zs))
        if refs_for is not None:
            refs = list(refs_for(comp))
        elif column_refs is not None:
            refs = sorted({
                column_refs[c] for c in comp
                if c in column_refs and column_refs[c]
            })
            if not refs:
                refs = [f'water_{bbox[0]}_{bbox[1]}']
        else:
            refs = [f'water_{bbox[0]}_{bbox[1]}']
        outline = sorted(comp)
        body = water_body_plan_item(
            label=f'pond ({len(comp)} columns)',
            outline=outline,
            cell=cell,
            refs=refs,
            water_level=None,
            source=provenance_source,
            confidence=provenance_confidence,
            unresolved_reason=unresolved_reason if provenance_source else '',
            root=root,
        )
        # Same candidate shape as a building row (street / portals / pictures).
        body['street'] = None
        body['portals'] = []
        body['pictures'] = []
        out.append(body)
    return out


def _bank_ring(outline: set[tuple[int, int]], width: int) -> set[tuple[int, int]]:
    ring = set(outline)
    for _ in range(width):
        grow = set()
        for x, z in ring:
            for dx, dz in N8:
                grow.add((x + dx, z + dz))
        ring |= grow
    return ring - outline


def _shoreline(outline: set[tuple[int, int]], bank: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """Bank cells that share an N8 neighbour with water."""
    return {
        q for q in bank
        if any((q[0] + dx, q[1] + dz) in outline for dx, dz in N8)
    }


def _bank_levels(levels: Mapping[tuple[int, int], int],
                 distances: Mapping[tuple[int, int], float], width: int) -> dict[tuple[int, int], int]:
    """Grow N8 banks with nearest-water ownership, resolving ties upstream."""
    owners = {q: (distances[q], *q) for q in levels}
    frontier = owners
    for _ in range(width):
        grow = {}
        for (x, z), source in frontier.items():
            for dx, dz in N8:
                q = (x + dx, z + dz)
                if q not in owners and (q not in grow or source < grow[q]):
                    grow[q] = source
        owners.update(grow)
        frontier = grow
    return {q: levels[(source[1], source[2])] for q, source in owners.items() if q not in levels}


def build(spec: Mapping[str, Any]) -> Plan:
    """Build a water-body ``Plan`` from a validated spec.

    Places floor, water and air using each column's local profile level (or
    ``water_level``), a bank ring and optional shoreline edge. With ``clip``
    only the clip cell's columns are written; the ring is still worked out on the
    whole outline, so water across the cell edge is never banked (#321).

    @param spec: output of :func:`load_spec` / :func:`load_spec_dict`
    @returns: offline ``Plan`` ready for preview/apply
    """
    outline = {(int(x), int(z)) for x, z in spec['outline']}
    level = int(spec['water_level'])
    depth = int(spec['depth'])
    floor = str(spec['floor'])
    bank_spec = spec['bank']
    edge_spec = spec.get('edge')
    profile = spec.get('water_level_profile')
    levels = dict.fromkeys(outline, level)
    bank_levels = {}
    if profile:
        segments = [LevelSegment(**row) for row in profile['segments']]
        distances = {(x, z): distance for x, z, distance in profile['columns']}
        levels = {q: level_at_distance(segments, distance) for q, distance in distances.items()}
        bank_levels = _bank_levels(levels, distances, int(bank_spec['width']))
        bank = set(bank_levels)
    else:
        bank = _bank_ring(outline, int(bank_spec['width']))
    shore = _shoreline(outline, bank) if edge_spec else set()
    clip = spec.get('clip')
    if clip:
        cell = (int(clip['cell'][0]), int(clip['cell'][1]))
        outline = {q for q in outline if cellmath.cell_of(*q) == cell}
        bank = {q for q in bank if cellmath.cell_of(*q) == cell}

    plan = Plan(str(spec['name']))
    for x, z in outline:
        local = levels[(x, z)]
        bed_y = local - depth
        plan.set(x, bed_y, z, floor)
        for y in range(bed_y + 1, local + 1):
            plan.set(x, y, z, 'water')
        for y in range(local + 1, local + int(spec.get('clear_above', 0)) + 1):
            plan.set(x, y, z, 'air')
    for x, z in bank:
        bank_y = bank_levels.get((x, z), level) + int(bank_spec.get('rise', 0))
        if (x, z) in shore and edge_spec:
            plan.set(x, bank_y, z, edge_spec['material'])
        else:
            plan.set(x, bank_y, z, bank_spec['material'])
    # Stamp against every cell the water body and bank touch (task 232); one cell when clipped.
    plan.capture_inputs()
    return plan


def run(path: str | Path, *, do_apply: bool = False, root: Path | None = None) -> Plan:
    """Load spec, build plan, preview, and optionally apply.

    @param path: spec JSON path
    @param do_apply: when True, write through ``site_kit.apply``
    @param root: optional world root for evidence checks
    @returns: the built ``Plan``
    """
    spec = load_spec(path, root=root)
    plan = build(spec)
    out = Path(path).with_suffix('.png')
    preview(plan, out, scale=4)
    print(json.dumps(plan.summary())[:600])
    apply(plan, dry=not do_apply, modifies=tuple(spec['modifies']))
    return plan


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: dry-run (default) or ``--apply`` a water-body spec.

    @param argv: optional argument list
    @returns: process exit code
    """
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('spec', help='site/builds/<name>.json')
    ap.add_argument('--apply', action='store_true', help='write the plan (default: dry run)')
    args = ap.parse_args(argv)
    run(args.spec, do_apply=args.apply)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
