"""Feature spec loading and validation. Schema: rules/feature-builds.md."""

from __future__ import annotations

from pathlib import Path
import json
import math

from cellmath import DIRS
from chimney import validate_chimney
import facade_plan as facade_plan_mod
import sign_options as sign_options_mod


STOREY_H = 3


ROOF_KINDS = frozenset({'flat', 'pitched'})


EDGE_WHERE = frozenset({'outer', 'all'})


ZONE_FROM = frozenset({'lidar'})


ROOF_DETAIL_KINDS = frozenset({
    'plant_room', 'vent', 'louvred_vent', 'skylight', 'solar_panel', 'lift_overrun', 'chimney',
})


FACADE_DETAIL_KINDS = frozenset({
    'drainpipe', 'sign', 'awning_like', 'ledge', 'fascia_band',
})


DETAIL_KINDS = ROOF_DETAIL_KINDS | FACADE_DETAIL_KINDS


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
    spec.setdefault('facade_plan', {})
    spec.setdefault('details', {})
    spec.setdefault('signs', {})
    spec.setdefault('zones', {})
    spec.setdefault('exclude', {})
    if 'facades' in spec:
        raise SystemExit(f'{path}: "facades" was removed; use facade_plan[label].faces[face].glazing')
    if 'doors' in spec:
        raise SystemExit(
            f'{path}: "doors" was removed; place entrances on facade_plan '
            f'(lib/builds/facade_plan.py)'
        )
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
        _validate_roof_edge(path, lab, roof)
        _validate_flat_tol(path, lab, roof)
        _validate_zone_materials(path, lab, roof)
        _validate_pv_arrays(path, lab, roof)
    for g in spec['groups']:
        if g['front'] not in DIRS or g.get('mode', 'each') not in ('each', 'group'):
            raise SystemExit(f'{path}: bad group {g}')
        g.setdefault('mode', 'each')
        for no in g['nos']:
            if no not in labels or no not in spec['storeys'] or no not in spec['roofs']:
                raise SystemExit(f'{path}: label {no} needs a way, storeys and roof')
    _validate_facade_plan(path, spec, labels)
    _validate_details(path, spec, labels)
    _validate_signs(path, spec, labels)
    _validate_zones(path, spec, labels)
    _validate_exclude(path, spec, labels)
    _validate_material_regions(path, spec)
    return spec


def _validate_signs(path, spec: dict, labels: set) -> None:
    """Validate optional per-label sign wood, fascia and text colour.

    @param path: spec path (for error text)
    @param spec: feature spec being loaded
    @param labels: known way labels
    @raises SystemExit: on invalid signs entries
    """
    signs = spec.get('signs') or {}
    if not isinstance(signs, dict):
        raise SystemExit(f'{path}: signs must be an object')
    for lab, row in signs.items():
        if lab not in labels:
            raise SystemExit(f'{path}: signs label {lab} has no way')
        try:
            sign_options_mod.validate_sign_row(
                row, context=f'{path}: signs[{lab}]', strict_keys=True,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc


def _validate_facade_plan(path, spec: dict, labels: set) -> None:
    """Validate optional ``facade_plan`` via ``lib/builds/facade_plan.py``.

    @param path: spec path (for error text)
    @param spec: feature spec being loaded
    @param labels: labels declared in ``ways``
    @raises SystemExit: on invalid facade_plan
    """
    try:
        facade_plan_mod.validate_facade_plan(
            spec.get('facade_plan') or {},
            labels=labels,
            storeys=spec.get('storeys') or {},
            portal_ids=None,
            path=f'{path}: facade_plan',
        )
    except facade_plan_mod.FacadePlanError as exc:
        raise SystemExit(str(exc)) from exc


def _validate_flat_tol(path, lab: str, roof: dict) -> None:
    """Validate optional ``roofs[label].flat_tol_m`` (absent = ``FLAT_TOL_M`` default).

    @param path: spec path (for error text)
    @param lab: roof label
    @param roof: roofs[lab] dict
    @raises SystemExit: when not a finite number in [0, 3], or set on a flat roof
    """
    if 'flat_tol_m' not in roof:
        return
    tol = roof['flat_tol_m']
    if roof.get('kind') != 'pitched':
        raise SystemExit(f'{path}: roofs[{lab}].flat_tol_m only applies to pitched roofs')
    if (isinstance(tol, bool) or not isinstance(tol, (int, float))
            or not math.isfinite(tol) or not 0 <= tol <= 3):
        raise SystemExit(f'{path}: roofs[{lab}].flat_tol_m must be a finite number of metres in [0, 3]')


def _validate_roof_edge(path, lab: str, roof: dict) -> None:
    """Validate optional ``roofs[label].edge`` (absent = no edging).

    When present must be ``{block: <mat>, where: outer|all}``. ``outer`` skips
    zone steps; ``all`` edges every step including zone drops.

    @param path: spec path (for error text)
    @param lab: roof label
    @param roof: roofs[lab] dict
    @raises SystemExit: on invalid edge entries
    """
    edge = roof.get('edge')
    if edge is None:
        return
    if not isinstance(edge, dict):
        raise SystemExit(f'{path}: roofs[{lab}].edge must be an object')
    block = edge.get('block')
    where = edge.get('where')
    if not isinstance(block, str) or not block.strip():
        raise SystemExit(f'{path}: roofs[{lab}].edge.block must be a non-empty material name')
    if where not in EDGE_WHERE:
        raise SystemExit(
            f'{path}: roofs[{lab}].edge.where must be one of {sorted(EDGE_WHERE)}')


def _validate_zone_materials(path, lab: str, roof: dict) -> None:
    """Validate optional ``roofs[label].zone_materials`` (per-plateau mat override).

    Keys are plateau levels (metres AOD, int or numeric string); values are
    non-empty material names. Absent = every zone uses ``roofs[label].material``.

    @param path: spec path (for error text)
    @param lab: roof label
    @param roof: roofs[lab] dict
    @raises SystemExit: on invalid zone_materials entries
    """
    zm = roof.get('zone_materials')
    if zm is None:
        return
    if not isinstance(zm, dict):
        raise SystemExit(f'{path}: roofs[{lab}].zone_materials must be an object')
    for key, mat in zm.items():
        try:
            float(key)
        except (TypeError, ValueError):
            raise SystemExit(
                f'{path}: roofs[{lab}].zone_materials key {key!r} must be a plateau level'
            ) from None
        if not isinstance(mat, str) or not mat.strip():
            raise SystemExit(
                f'{path}: roofs[{lab}].zone_materials[{key!r}] must be a non-empty material name'
            )


PV_ARRAY_KEYS = frozenset({'box', 'rows', 'pitch', 'source'})


def _validate_pv_arrays(path, lab: str, roof: dict) -> None:
    """Validate optional flat-roof ``pv_arrays`` rows; fill the default pitch.

    @param path: spec path (for error text)
    @param lab: roof label
    @param roof: roofs[lab] dict (rows gain ``pitch`` 2 when absent)
    @raises SystemExit: on a malformed row or a non-flat roof
    """
    if 'pv_arrays' not in roof:
        return
    rows = roof['pv_arrays']
    context = f'{path}: roofs[{lab}].pv_arrays'
    if not isinstance(rows, list):
        raise SystemExit(f'{context} must be a list')
    if rows and roof.get('kind') != 'flat':
        raise SystemExit(f'{context} needs a flat roof')
    for i, row in enumerate(rows):
        pref = f'{context}[{i}]'
        if not isinstance(row, dict):
            raise SystemExit(f'{pref} must be an object')
        unknown = sorted(set(row) - PV_ARRAY_KEYS)
        if unknown:
            raise SystemExit(f'{pref} unknown keys {unknown}')
        box = row.get('box')
        if (not isinstance(box, list) or len(box) != 4
                or not all(isinstance(v, int) and not isinstance(v, bool) for v in box)
                or box[0] > box[2] or box[1] > box[3]):
            raise SystemExit(f'{pref} box must be [x0, z0, x1, z1] ints with x0 <= x1, z0 <= z1')
        if row.get('rows') not in ('x', 'z'):
            raise SystemExit(f'{pref} rows must be "x" or "z"')
        pitch = row.setdefault('pitch', 2)
        if not isinstance(pitch, int) or isinstance(pitch, bool) or pitch < 1:
            raise SystemExit(f'{pref} pitch must be an int >= 1')
        source = row.get('source')
        if not isinstance(source, str) or not source.strip():
            raise SystemExit(f'{pref} source must be a non-empty string')


def _validate_material_regions(path, spec: dict) -> None:
    """Validate explicit pitched-roof membership; raster checks need the footprint.

    @param path: spec path used in diagnostics
    @param spec: validated roof and lidar-zone configuration
    @raises SystemExit: on malformed region, polygon or unknown plateau
    """
    for lab, roof in spec['roofs'].items():
        if 'material_regions' not in roof:
            continue
        rows = roof['material_regions']
        context = f'{path}: roofs[{lab}].material_regions'
        if not isinstance(rows, list):
            raise SystemExit(f'{context} must be a list')
        if roof['kind'] != 'pitched' or lab not in spec['zones']:
            raise SystemExit(f'{context} requires a pitched roof with zones')
        configured = spec['zones'][lab]['levels']
        for i, row in enumerate(rows):
            prefix = f'{context}[{i}]'
            if not isinstance(row, dict) or not {'level', 'polygon'} <= set(row) <= {'level', 'polygon', 'material'}:
                raise SystemExit(f'{prefix} needs {{level, polygon}} and optionally material')
            if 'material' in row and (not isinstance(row['material'], str) or not row['material'].strip()):
                raise SystemExit(f'{prefix}.material must be a non-empty material name')
            level = row['level']
            if isinstance(level, bool) or not isinstance(level, (int, float)) or not math.isfinite(level):
                raise SystemExit(f'{prefix}.level must be a finite plateau number')
            if configured != 'auto' and level not in configured:
                raise SystemExit(f'{prefix}: unknown plateau {level}')
            polygon = row['polygon']
            if not isinstance(polygon, list) or len(polygon) < 3:
                raise SystemExit(f'{prefix}.polygon needs at least three vertices')
            for point in polygon:
                if (not isinstance(point, list) or len(point) != 2
                        or any(isinstance(v, bool) or not isinstance(v, (int, float))
                               or not math.isfinite(v) for v in point)):
                    raise SystemExit(f'{prefix}.polygon vertices must be finite [x, z] numbers')
            if len({tuple(p) for p in polygon}) < 3:
                raise SystemExit(f'{prefix}.polygon needs three distinct vertices')


def _validate_zones(path, spec: dict, labels: set) -> None:
    """Validate optional per-label lidar roof-zone config.

    @param path: spec path (for error text)
    @param spec: feature spec being loaded
    @param labels: known way labels
    @raises SystemExit: on invalid zone entries
    """
    zones = spec.get('zones') or {}
    if not isinstance(zones, dict):
        raise SystemExit(f'{path}: zones must be an object')
    for lab, row in zones.items():
        if lab not in labels:
            raise SystemExit(f'{path}: zones label {lab} has no way')
        if not isinstance(row, dict):
            raise SystemExit(f'{path}: zones[{lab}] must be an object')
        if row.get('from') not in ZONE_FROM:
            raise SystemExit(f'{path}: zones[{lab}].from must be one of {sorted(ZONE_FROM)}')
        levels = row.get('levels')
        if levels != 'auto':
            if not isinstance(levels, list) or not levels:
                raise SystemExit(f'{path}: zones[{lab}].levels must be a non-empty list or \"auto\"')
            for lv in levels:
                if not isinstance(lv, (int, float)):
                    raise SystemExit(f'{path}: zones[{lab}].levels entries must be numbers')
        bucket = row.get('bucket', 4)
        if not isinstance(bucket, int) or bucket < 1:
            raise SystemExit(f'{path}: zones[{lab}].bucket must be int >= 1')
        row.setdefault('bucket', 4)
        axis = row.get('axis_deg', 'auto')
        if axis != 'auto' and not isinstance(axis, (int, float)):
            raise SystemExit(f'{path}: zones[{lab}].axis_deg must be \"auto\" or a number')
        row.setdefault('axis_deg', 'auto')
        _validate_zone_regions(path, lab, row.get('regions'))
        roof = spec['roofs'].get(lab) or {}
        if roof.get('kind') not in ROOF_KINDS:
            raise SystemExit(
                f'{path}: zones[{lab}] needs roofs[{lab}].kind flat|pitched '
                f'(got {roof.get("kind")!r})'
            )


def _validate_zone_regions(path, lab: str, regions) -> None:
    """Validate optional aerial-drawn zone polygons: ``zones[label].regions``.

    Each region is a polygon ``[[x, z], ...]`` in MC block coordinates. The image says
    where a roof zone is; lidar still sets its level.

    @param path: spec path (for error text)
    @param lab: way label
    @param regions: the ``regions`` value, or None when absent
    @raises SystemExit: on a malformed polygon
    """
    if regions is None:
        return
    context = f'{path}: zones[{lab}].regions'
    if not isinstance(regions, list) or not regions:
        raise SystemExit(f'{context} must be a non-empty list of polygons')
    for i, polygon in enumerate(regions):
        if not isinstance(polygon, list) or len(polygon) < 3:
            raise SystemExit(f'{context}[{i}] needs at least three vertices')
        for point in polygon:
            ok = (isinstance(point, list) and len(point) == 2
                  and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                          and math.isfinite(v) for v in point))
            if not ok:
                raise SystemExit(f'{context}[{i}] vertices must be finite [x, z] numbers')
        if len({tuple(p) for p in polygon}) < 3:
            raise SystemExit(f'{context}[{i}] needs three distinct vertices')


def _validate_exclude(path, spec: dict, labels: set) -> None:
    """Validate optional per-label footprint exclusion boxes.

    @param path: spec path (for error text)
    @param spec: feature spec being loaded
    @param labels: labels declared in ``ways``
    @raises SystemExit: on invalid boxes
    """
    exclude = spec.get('exclude') or {}
    if not isinstance(exclude, dict):
        raise SystemExit(f'{path}: exclude must be an object')
    for lab, boxes in exclude.items():
        if lab not in labels:
            raise SystemExit(f'{path}: exclude label {lab} has no way')
        if not isinstance(boxes, list):
            raise SystemExit(f'{path}: exclude[{lab}] must be a list of [x0, z0, x1, z1]')
        for box in boxes:
            ok = (isinstance(box, list) and len(box) == 4
                  and all(isinstance(v, int) and not isinstance(v, bool) for v in box)
                  and box[0] <= box[2] and box[1] <= box[3])
            if not ok:
                raise SystemExit(f'{path}: exclude[{lab}] box {box} must be [x0, z0, x1, z1] ints, min <= max')


def _validate_details(path, spec: dict, labels: set) -> None:
    """Validate optional per-label roof/facade detail (greeblie) rows.

    Each row needs ``kind``, ``at`` ``[x,y,z]``, ``size`` ``[sx,sy,sz]`` (>=1),
    and a non-empty ``source``. Facade kinds also need ``face`` in N/S/E/W.

    @param path: spec path (for error text)
    @param spec: feature spec being loaded
    @param labels: known way labels
    @raises SystemExit: on invalid details structure
    """
    details = spec.get('details') or {}
    if not isinstance(details, dict):
        raise SystemExit(f'{path}: details must be an object')
    for lab, rows in details.items():
        if lab not in labels:
            raise SystemExit(f'{path}: details label {lab} has no way')
        if not isinstance(rows, list):
            raise SystemExit(f'{path}: details[{lab}] must be a list')
        for i, row in enumerate(rows):
            pref = f'{path}: details[{lab}][{i}]'
            if not isinstance(row, dict):
                raise SystemExit(f'{pref} must be an object')
            kind = row.get('kind')
            if kind == 'canopy':
                raise SystemExit(
                    f'{pref} kind \'canopy\' is renamed to \'awning_like\' '
                    f'(allowed: {sorted(DETAIL_KINDS)})'
                )
            if kind not in DETAIL_KINDS:
                raise SystemExit(
                    f'{pref} kind must be one of {sorted(DETAIL_KINDS)}')
            at = row.get('at')
            if (
                not isinstance(at, (list, tuple)) or len(at) != 3
                or not all(isinstance(v, int) and not isinstance(v, bool) for v in at)
            ):
                raise SystemExit(f'{pref} at must be [x, y, z] ints')
            if kind == 'chimney':
                validate_chimney(row, pref)
            size = row.get('size')
            if (
                not isinstance(size, (list, tuple)) or len(size) != 3
                or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in size)
            ):
                raise SystemExit(f'{pref} size must be [sx, sy, sz] ints >= 1')
            source = row.get('source')
            if not isinstance(source, str) or not source.strip():
                raise SystemExit(f'{pref} source must be a non-empty string')
            if kind in FACADE_DETAIL_KINDS:
                face = row.get('face')
                if face not in DIRS:
                    raise SystemExit(f'{pref} face must be one of N/S/E/W')
            if kind in ('sign', 'fascia_band'):
                try:
                    sign_options_mod.validate_sign_row(
                        {k: row[k] for k in ('wood', 'fascia_colour', 'text_colour')
                         if k in row},
                        context=pref,
                    )
                except ValueError as exc:
                    raise SystemExit(str(exc)) from exc
