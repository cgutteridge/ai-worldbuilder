"""Per-face facade plan vocabulary for non-house building specs.

The designer reads portals (register and/or street photos) into ``facade_plan``:
each portal is either a placed entrance or an unresolved plan item with a
reason. Provenance uses ``lib/site/provenance.py`` (#151) — this module defines
no private source/confidence/unresolved fields.

Shape (per way label)::

  {
    "<label>": {
      "faces": {
        "N"|"S"|"E"|"W": {
          "glazing": [ {storey, glazed_h, pier, wall?, rhythm?} ],  # optional
          "roof_edge": null,   # reserved; roofs[label].edge remains SoT for now
          "detail": []         # reserved; sibling ``details`` remains SoT (#132)
        }
      },
      "entrances": [
        {
          "id": "<stable id>",
          "portal_id"?: "<register id>",
          "face"?: "N"|"S"|"E"|"W",          # required when placed
          "leaves"?: 1|2,                     # required when placed
          "block"?: "<*_door>",               # required when placed
          "at"?: [x, z],
          "awning_like"?: {"kind": "none"|"awning"|"covered_area"|"porch_roof",
                           "depth"?: int},
          "steps"?: int >= 0,
          "floor_offset"?: int,               # blocks vs outside ground (0 = level)
          "cell"?: [tx, tz],                  # for cell-view unresolved listing
          "cells"?: [[tx, tz], ...],
          "provenance": { source, confidence, evidence_ids, unresolved_reason }
        }
      ]
    }
  }

``awning_like`` is any roof or cover over an entrance (awning, porch roof,
covered area) — not a tree canopy. Kind ``porch_roof`` is a solid projecting
entrance roof (formerly the kind value ``canopy``). The legacy entrance field
``canopy`` is refused (no silent alias).

Relation to neighbouring work:

- **Windows:** ``faces[face].glazing`` is the only source for window rows;
  placement and glazed-share reporting both read it.
- **#131 window frames:** build-time only — not plan fields.
- **#132 roof/facade detail:** stays in sibling ``details`` / ``roofs.edge``.
  Entrance awning_like / covered area is owned here; face slots ``roof_edge``
  and ``detail`` are reserved room only.
- **#146:** ``feature_build`` places doors from placed entrances only; the old
  ``doors`` map / nearest-wall portal path is removed.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
import math
from typing import Any

import provenance as prov

FACES = ('N', 'S', 'E', 'W')
_OUTWARD = {'N': (0, -1), 'S': (0, 1), 'E': (1, 0), 'W': (-1, 0)}
_FRONT_HIGHWAYS = frozenset({
    'primary', 'secondary', 'tertiary', 'unclassified', 'residential', 'living_street',
    'service', 'pedestrian',
})
# Entrance cover kinds. ``porch_roof`` replaces the old kind value ``canopy``
# (tree-ambiguous); ``awning`` is fabric-style; ``covered_area`` is a recess /
# soffit; ``none`` is flush with no cover.
AWNING_LIKE_KINDS = ('none', 'awning', 'covered_area', 'porch_roof')
PROVENANCE_KEY = prov.PROVENANCE_KEY
STOREY_H = 3

# Reserved face keys: accepted when present, not deeply interpreted here.
_FACE_RESERVED = ('roof_edge', 'detail')


class FacadePlanError(ValueError):
    """Refused facade_plan input (shape, entrance, glazing, or portal coverage)."""


def front_columns(footprint: set, streets: Sequence, *, occupied=(), street_name=None) -> dict:
    """Find exposed wall columns whose outside neighbour is nearer a mapped street line.

    Street rows are ``(highway, points, flattened tags)``. Alleys, driveways and parking aisles
    cannot choose a frontage. A matching address street wins when present. Segment endpoints
    cannot turn a side wall into a front, and the route to the line must clear all footprints.
    Both step directions of a rotated frontage are supported; each column gets one outward face.

    @param footprint: building's columns
    @param streets: mapped street rows (e.g. ``footprint_overlap.streets_from_index``)
    @param occupied: all building columns, including this footprint and party-wall neighbours
    @param street_name: optional mapped ``addr:street``
    @returns: ``{(x, z): 'N'|'S'|'E'|'W'}``; empty without a usable street
    """
    rows = [(pts, tags) for highway, pts, tags in streets
            if highway in _FRONT_HIGHWAYS and tags.get('service') not in
            ('alley', 'driveway', 'parking_aisle') and len(pts) > 1]
    named = [(pts, tags) for pts, tags in rows if street_name and tags.get('name') == street_name]
    segments = [(a, b) for pts, _tags in (named or rows) for a, b in zip(pts, pts[1:]) if a != b]
    if not segments:
        return {}
    blocked = set(occupied) | footprint

    def nearest(p):
        choices = []
        for (ax, az), (bx, bz) in segments:
            dx, dz = bx - ax, bz - az
            t = ((p[0] - ax) * dx + (p[1] - az) * dz) / (dx * dx + dz * dz)
            if 0 <= t <= 1:
                q = (ax + t * dx, az + t * dz)
                choices.append((math.dist(p, q), q))
        return min(choices, default=None)

    result = {}
    for x, z in sorted(footprint):
        p = (x + 0.5, z + 0.5)
        hit = nearest(p)
        if hit is None or hit[0] < 1:
            continue
        distance, q = hit
        faces = []
        for face, (dx, dz) in _OUTWARD.items():
            if (x + dx, z + dz) in blocked:
                continue
            outside = (p[0] + dx, p[1] + dz)
            gain = distance - math.dist(outside, q)
            if gain > 1e-6:
                faces.append((gain, face))
        if not faces:
            continue
        # Average the nearby stepped edge's normal, so a diagonal street does not dress
        # a long perpendicular side wall merely because one step reduces the distance.
        nx = nz = 0
        for sx in range(x - 1, x + 2):
            for sz in range(z - 1, z + 2):
                if (sx, sz) in footprint:
                    for dx, dz in _OUTWARD.values():
                        if (sx + dx, sz + dz) not in footprint:
                            nx += dx
                            nz += dz
        normal = math.hypot(nx, nz)
        alignment = ((nx * (q[0] - p[0]) + nz * (q[1] - p[1])) / (normal * distance)
                     if normal else 0)
        cardinal = max((_OUTWARD[face][0] * (q[0] - p[0]) +
                        _OUTWARD[face][1] * (q[1] - p[1])) / distance for _, face in faces)
        if max(alignment, cardinal) < 0.9:
            continue
        # Trace from just outside the wall to the street, including narrow intervening buildings.
        steps = max(1, math.ceil(distance * 2))
        route = {(math.floor(p[0] + (q[0] - p[0]) * i / steps),
                  math.floor(p[1] + (q[1] - p[1]) * i / steps)) for i in range(1, steps + 1)}
        route.discard((x, z))
        if not route & blocked:
            result[(x, z)] = max(faces)[1]
    return result


def glazing_by_face(plan: Mapping[str, Any] | None, label: str) -> dict:
    """Return the evidenced glazing rows for a label, keyed by face.

    @param plan: label-to-plan mapping (or None)
    @param label: feature label
    @returns: face-to-glazing-row mapping, omitting empty rows
    """
    body = (plan or {}).get(label) or {}
    return {face: data['glazing'] for face, data in (body.get('faces') or {}).items()
            if data.get('glazing')}


def iter_entrances(plan: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Flatten every entrance row in a facade_plan.

    @param plan: ``facade_plan`` object (label → plan), or ``None``
    @returns: entrance dicts in label then list order
    """
    out: list[dict[str, Any]] = []
    if not plan:
        return out
    for _lab, body in plan.items():
        if not isinstance(body, Mapping):
            continue
        rows = body.get('entrances') or []
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    out.append(row)
    return out


def placed_entrances(plan: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Entrances whose provenance has no unresolved reason.

    @param plan: ``facade_plan`` object
    @returns: placed entrance rows
    """
    return [e for e in iter_entrances(plan) if not prov.is_unresolved(e.get(PROVENANCE_KEY))]


def unresolved_entrances(plan: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Entrances declared unresolved via shared provenance.

    @param plan: ``facade_plan`` object
    @returns: unresolved entrance rows
    """
    return [e for e in iter_entrances(plan) if prov.is_unresolved(e.get(PROVENANCE_KEY))]


def unresolved_entrances_for_cell(
    cell: Sequence[int] | tuple[int, int],
    plan: Mapping[str, Any] | None,
) -> list[Mapping[str, Any]]:
    """Unresolved entrances that touch a cell (for the cell view).

    @param cell: ``(tx, tz)``
    @param plan: ``facade_plan`` object
    @returns: matching unresolved entrances in plan order
    """
    return prov.unresolved_for_cell(cell, iter_entrances(plan))


def _portal_keys(entrance: Mapping[str, Any]) -> set[str]:
    keys: set[str] = set()
    eid = entrance.get('id')
    if isinstance(eid, str) and eid.strip():
        keys.add(eid.strip())
    pid = entrance.get('portal_id')
    if isinstance(pid, str) and pid.strip():
        keys.add(pid.strip())
    return keys


def accounted_portal_ids(plan: Mapping[str, Any] | None) -> set[str]:
    """Portal / entrance ids already placed or declared unresolved.

    @param plan: ``facade_plan`` object
    @returns: set of id and portal_id strings
    """
    keys: set[str] = set()
    for e in iter_entrances(plan):
        keys |= _portal_keys(e)
    return keys


def validate_glazing_rows(
    rows: Any,
    *,
    n_storeys: int,
    path: str,
) -> None:
    """Validate a face glazing list (same shape as legacy ``facades`` rows).

    @param rows: list of glazing row dicts
    @param n_storeys: storeys for the label
    @param path: error prefix
    @raises FacadePlanError: on invalid rows
    """
    if not isinstance(rows, list):
        raise FacadePlanError(f'{path} glazing must be a list')
    seen: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise FacadePlanError(f'{path} glazing row must be an object')
        storey = row.get('storey')
        glazed_h = row.get('glazed_h')
        pier = row.get('pier')
        if not isinstance(storey, int) or storey < 1 or storey > n_storeys:
            raise FacadePlanError(f'{path} glazing storey must be int 1..{n_storeys}')
        if storey in seen:
            raise FacadePlanError(f'{path} glazing duplicate storey {storey}')
        seen.add(storey)
        if not isinstance(glazed_h, int) or glazed_h < 1 or glazed_h > STOREY_H:
            raise FacadePlanError(f'{path} glazed_h must be int 1..{STOREY_H}')
        if not isinstance(pier, int) or pier < 0:
            raise FacadePlanError(f'{path} pier must be int >= 0')
        if 'rhythm' in row and row['rhythm'] is not None:
            if not isinstance(row['rhythm'], str) or not row['rhythm']:
                raise FacadePlanError(f'{path} rhythm must be a non-empty string')
            bad = [c for c in row['rhythm'] if c not in 'WB_']
            if bad:
                raise FacadePlanError(f'{path} rhythm chars must be W/B/_ (got {bad!r})')
        corners = row.get('corner_glazing', [])
        if not isinstance(corners, list):
            raise FacadePlanError(f'{path} corner_glazing must be a list')
        for corner in corners:
            if not isinstance(corner, dict) or not isinstance(corner.get('at'), list) or len(corner['at']) != 2 or not all(type(v) is int for v in corner['at']):
                raise FacadePlanError(f'{path} corner_glazing needs at: [x,z]')
            evidence = _validate_provenance(corner.get('provenance'), path)
            if evidence['source'] not in ('photo_read', 'aerial') or prov.is_unresolved(evidence):
                raise FacadePlanError(f'{path} corner glazing needs resolved photo/aerial provenance')
        if 'wall' in row and row['wall'] is not None and not isinstance(row['wall'], str):
            raise FacadePlanError(f'{path} wall must be a string')


def _validate_awning_like(awning_like: Any, path: str) -> None:
    """Validate an entrance ``awning_like`` object (cover over the door).

    @param awning_like: object with ``kind`` and optional ``depth``, or ``None``
    @param path: error prefix (entrance path)
    @raises FacadePlanError: on invalid shape or kind
    """
    if awning_like is None:
        return
    if not isinstance(awning_like, dict):
        raise FacadePlanError(f'{path} awning_like must be an object')
    kind = awning_like.get('kind')
    if kind == 'canopy':
        raise FacadePlanError(
            f'{path} awning_like.kind {kind!r} is renamed to \'porch_roof\' '
            f'(allowed: {AWNING_LIKE_KINDS})'
        )
    if kind not in AWNING_LIKE_KINDS:
        raise FacadePlanError(
            f'{path} awning_like.kind must be one of {AWNING_LIKE_KINDS}'
        )
    if 'depth' in awning_like and awning_like['depth'] is not None:
        depth = awning_like['depth']
        if not isinstance(depth, int) or isinstance(depth, bool) or depth < 0:
            raise FacadePlanError(f'{path} awning_like.depth must be int >= 0')


def _validate_provenance(raw: Any, path: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise FacadePlanError(f'{path} must include provenance (lib/site/provenance.py)')
    try:
        return prov.provenance_record(
            source=str(raw.get('source', '')),
            confidence=str(raw.get('confidence', '')),
            evidence_ids=list(raw.get('evidence_ids') or ()),
            unresolved_reason=str(raw.get('unresolved_reason') or ''),
            root=None,
        )
    except prov.ProvenanceError as exc:
        raise FacadePlanError(f'{path} provenance: {exc}') from exc


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_entrance(row: Any, *, path: str) -> None:
    """Validate one entrance (placed or unresolved).

    Placed: provenance with empty unresolved_reason, plus face, leaves, block.
    Unresolved: provenance with non-empty unresolved_reason; placement fields optional.

    @param row: entrance dict
    @param path: error prefix
    @raises FacadePlanError: on invalid entrance
    """
    if not isinstance(row, dict):
        raise FacadePlanError(f'{path} must be an object')
    eid = row.get('id')
    if not isinstance(eid, str) or not eid.strip():
        raise FacadePlanError(f'{path} id must be a non-empty string')
    if 'portal_id' in row and row['portal_id'] is not None:
        if not isinstance(row['portal_id'], str) or not row['portal_id'].strip():
            raise FacadePlanError(f'{path} portal_id must be a non-empty string when set')

    prov_rec = _validate_provenance(row.get(PROVENANCE_KEY), f'{path}.{PROVENANCE_KEY}')
    unresolved = prov.is_unresolved(prov_rec)

    if unresolved:
        # Placement fields optional; if present, still type-check lightly.
        if 'face' in row and row['face'] is not None and row['face'] not in FACES:
            raise FacadePlanError(f'{path} face must be one of {FACES}')
        if 'leaves' in row and row['leaves'] is not None:
            if not _is_int(row['leaves']) or row['leaves'] not in (1, 2):
                raise FacadePlanError(f'{path} leaves must be 1 or 2')
        if 'block' in row and row['block'] is not None:
            block = row['block']
            if not isinstance(block, str) or not block.endswith('_door') or 'trap' in block:
                raise FacadePlanError(f'{path} block must be a *_door id (not trapdoor)')
    else:
        face = row.get('face')
        if face not in FACES:
            raise FacadePlanError(f'{path} face must be one of {FACES} when placed')
        leaves = row.get('leaves')
        if not _is_int(leaves) or leaves not in (1, 2):
            raise FacadePlanError(f'{path} leaves must be 1 or 2 when placed')
        block = row.get('block')
        if not isinstance(block, str) or not block.endswith('_door') or 'trap' in block:
            raise FacadePlanError(f'{path} block must be a *_door id (not trapdoor) when placed')

    if 'at' in row and row['at'] is not None:
        at = row['at']
        if (
            not isinstance(at, (list, tuple)) or len(at) != 2
            or not all(_is_int(v) for v in at)
        ):
            raise FacadePlanError(f'{path} at must be [x, z] ints')

    if 'steps' in row and row['steps'] is not None:
        if not _is_int(row['steps']) or row['steps'] < 0:
            raise FacadePlanError(f'{path} steps must be int >= 0')

    if 'floor_offset' in row and row['floor_offset'] is not None:
        if not _is_int(row['floor_offset']):
            raise FacadePlanError(f'{path} floor_offset must be an int')

    if 'canopy' in row:
        raise FacadePlanError(
            f'{path} uses removed key \'canopy\'; rename to \'awning_like\' '
            f'(kinds {AWNING_LIKE_KINDS}; old kind \'canopy\' → \'porch_roof\')'
        )
    if 'awning_like' in row:
        _validate_awning_like(row.get('awning_like'), path)

    if 'cell' in row and row['cell'] is not None:
        cell = row['cell']
        if (
            not isinstance(cell, (list, tuple)) or len(cell) != 2
            or not all(_is_int(v) for v in cell)
        ):
            raise FacadePlanError(f'{path} cell must be [tx, tz] ints')

    if 'cells' in row and row['cells'] is not None:
        cells = row['cells']
        if not isinstance(cells, list) or not cells:
            raise FacadePlanError(f'{path} cells must be a non-empty list')
        for c in cells:
            if (
                not isinstance(c, (list, tuple)) or len(c) != 2
                or not all(_is_int(v) for v in c)
            ):
                raise FacadePlanError(f'{path} cells entries must be [tx, tz] ints')


def validate_facade_plan(
    plan: Any,
    *,
    labels: set[str] | Iterable[str],
    storeys: Mapping[str, int],
    portal_ids: Sequence[str] | None = None,
    path: str = 'facade_plan',
) -> None:
    """Validate a full ``facade_plan`` object.

    When ``portal_ids`` is provided (register portals on the footprint), each
    id must appear as an entrance ``id`` or ``portal_id`` (placed or unresolved).

    @param plan: facade_plan dict
    @param labels: way labels allowed as keys
    @param storeys: label → storey count (for glazing bounds)
    @param portal_ids: optional portal ids that must be accounted for
    @param path: error prefix
    @raises FacadePlanError: on invalid plan or incomplete portal coverage
    """
    label_set = set(labels)
    if plan is None:
        plan = {}
    if not isinstance(plan, dict):
        raise FacadePlanError(f'{path} must be an object')

    for lab, body in plan.items():
        pref = f'{path}[{lab}]'
        if lab not in label_set:
            raise FacadePlanError(f'{pref} label has no way')
        if not isinstance(body, dict):
            raise FacadePlanError(f'{pref} must be an object')
        faces = body.get('faces', {})
        if faces is None:
            faces = {}
        if not isinstance(faces, dict):
            raise FacadePlanError(f'{pref}.faces must be an object')
        n_storeys = int(storeys[lab])
        for face, face_body in faces.items():
            fpref = f'{pref}.faces[{face}]'
            if face not in FACES:
                raise FacadePlanError(f'{fpref} face must be one of {FACES}')
            if face_body is None:
                face_body = {}
            if not isinstance(face_body, dict):
                raise FacadePlanError(f'{fpref} must be an object')
            if 'glazing' in face_body and face_body['glazing'] is not None:
                validate_glazing_rows(
                    face_body['glazing'], n_storeys=n_storeys, path=fpref,
                )
            if 'detail' in face_body and face_body['detail'] is not None:
                if not isinstance(face_body['detail'], list):
                    raise FacadePlanError(f'{fpref}.detail must be a list (reserved)')
            # roof_edge: reserved; null or omit only for now (no private schema)
            if 'roof_edge' in face_body and face_body['roof_edge'] is not None:
                if not isinstance(face_body['roof_edge'], (dict, str)):
                    raise FacadePlanError(
                        f'{fpref}.roof_edge must be null, a string note, or an object'
                    )
            unknown = [
                k for k in face_body
                if k not in ('glazing',) + _FACE_RESERVED and k != 'entrances'
            ]
            # Allow only known keys on a face (entrances live at label level)
            if 'entrances' in face_body:
                raise FacadePlanError(
                    f'{fpref} entrances belong on the label (facade_plan[{lab}].entrances), '
                    f'not under faces'
                )
            for k in unknown:
                raise FacadePlanError(f'{fpref} unknown key {k!r}')

        entrances = body.get('entrances', [])
        if entrances is None:
            entrances = []
        if not isinstance(entrances, list):
            raise FacadePlanError(f'{pref}.entrances must be a list')
        seen_ids: set[str] = set()
        for i, row in enumerate(entrances):
            epref = f'{pref}.entrances[{i}]'
            validate_entrance(row, path=epref)
            eid = str(row['id']).strip()
            if eid in seen_ids:
                raise FacadePlanError(f'{pref} duplicate entrance id {eid!r}')
            seen_ids.add(eid)

        unknown_body = [k for k in body if k not in ('faces', 'entrances')]
        for k in unknown_body:
            raise FacadePlanError(f'{pref} unknown key {k!r}')

    if portal_ids is not None:
        needed = {str(p).strip() for p in portal_ids if str(p).strip()}
        have = accounted_portal_ids(plan)
        missing = sorted(needed - have)
        if missing:
            raise FacadePlanError(
                f'{path}: portal(s) not placed or declared unresolved: {", ".join(missing)}'
            )


def format_portal_inputs(
    portals: Sequence[Mapping[str, Any]],
    *,
    photo_for: Callable[[Mapping[str, Any]], str | None] | None = None,
    limit: int = 12,
) -> list[str]:
    """Designer-brief lines: every portal on the footprint with position and photo.

    Portals are inputs to ``facade_plan``, not a build stage. Each must be
    placed as an entrance or declared unresolved with provenance.

    @param portals: portal dicts (``id``, ``x``, ``z``, ``label``?, ``images``?)
    @param photo_for: optional callable returning a local path or URL for a portal
    @param limit: max portals to list (remainder summarised)
    @returns: brief lines (empty when no portals)
    """
    if not portals:
        return []
    lines = [
        'Portals (inputs to facade_plan; register position is the door when known; '
        'photos are look-only evidence):',
    ]
    shown = list(portals[:limit])
    for p in shown:
        pid = p.get('id', '?')
        label = p.get('label') or ''
        xy = f'{p.get("x")},{p.get("z")}' if 'x' in p and 'z' in p else '?'
        bit = f'  {pid}'
        if label:
            bit += f' "{label}"'
        bit += f' at {xy}'
        photo = None
        if photo_for is not None:
            photo = photo_for(p)
        elif p.get('images'):
            imgs = p['images']
            photo = imgs[0] if isinstance(imgs, (list, tuple)) and imgs else None
        if photo:
            bit += f'  {photo}'
        lines.append(bit)
    if len(portals) > limit:
        lines.append(f'  … and {len(portals) - limit} more')
    lines.append(
        'Account for every portal in facade_plan entrances: place it (face, leaves, '
        'block, awning_like/covered area, steps, floor_offset vs outside ground) or '
        'declare it unresolved with provenance.unresolved_reason. A world with no '
        'portal register still gets entrances from street-level photos (photo_read).'
    )
    return lines


def load_and_validate_facade_plan(
    spec: Mapping[str, Any],
    *,
    labels: set[str] | Iterable[str],
    path: str,
    portal_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Read ``facade_plan`` from a feature spec and validate it.

    @param spec: feature build spec
    @param labels: way labels
    @param path: spec path for error text
    @param portal_ids: optional portals that must be accounted for
    @returns: facade_plan dict (possibly empty)
    @raises FacadePlanError: on invalid plan
    """
    plan = spec.get('facade_plan') or {}
    validate_facade_plan(
        plan,
        labels=labels,
        storeys=spec.get('storeys') or {},
        portal_ids=portal_ids,
        path=f'{path}: facade_plan',
    )
    return plan if isinstance(plan, dict) else {}
