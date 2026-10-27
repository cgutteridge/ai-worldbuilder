#!/usr/bin/env python3
"""Water-body features (ponds): plan vocabulary, provenance and build (#148).

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
  depth          water column height including the surface (default 1; >= 1)
  floor          bed material under the deepest water (default dirt)
  bank           {"material": "<block>", "width"?: 1} shore ring (N8 × width)
  edge           optional {"material": "<block>"} shoreline rim on bank cells
                 that touch water; absent → bank material only
  provenance     shared schema from ``lib/site/provenance.py`` (#151):
                   {source, confidence, evidence_ids, unresolved_reason}

Usage:  python3 scripts/water_body_build.py site/builds/<name>.json [--apply]
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import provenance as prov
from site_kit import Plan, apply, preview  # noqa: E402

N8 = [(dx, dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1) if dx or dz]
DEFAULT_DEPTH = 1
DEFAULT_FLOOR = 'dirt'
DEFAULT_BANK_WIDTH = 1
MIN_WATER_COLUMNS = 4


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
    return {'material': material, 'width': width}


def _validate_edge(path: str | Path | None, raw: Any) -> dict[str, str] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        _fail(path, 'edge must be an object {"material"} or omitted')
    material = str(raw.get('material') or '').strip()
    if not material:
        _fail(path, 'edge.material must be a non-empty block id')
    return {'material': material}


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
    depth = spec.get('depth', DEFAULT_DEPTH)
    if not isinstance(depth, int) or isinstance(depth, bool) or depth < 1:
        _fail(path, 'depth must be an int >= 1')
    floor = str(spec.get('floor', DEFAULT_FLOOR)).strip() or DEFAULT_FLOOR
    bank = _validate_bank(path, spec['bank'])
    edge = _validate_edge(path, spec.get('edge'))
    provenance = _validate_provenance(path, spec['provenance'], root)
    out = {
        'name': name,
        'notes': spec.get('notes', ''),
        'modifies': list(spec.get('modifies') or []),
        'outline': [[x, z] for x, z in outline],
        'water_level': level,
        'depth': depth,
        'floor': floor,
        'bank': bank,
        'edge': edge,
        'provenance': provenance,
    }
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
    source: str,
    confidence: str,
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
    @param source: provenance source kind
    @param confidence: provenance confidence
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
    prov.attach_provenance(
        item,
        source=source,
        confidence=confidence,
        evidence_ids=evidence_ids,
        unresolved_reason=unresolved_reason,
        root=root,
    )
    return item


def _components(cells: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    """8-connected components of a column set."""
    left, out = set(cells), []
    while left:
        seed = left.pop()
        comp, todo = {seed}, [seed]
        while todo:
            q = todo.pop()
            for dx, dz in N8:
                n = (q[0] + dx, q[1] + dz)
                if n in left:
                    left.discard(n)
                    comp.add(n)
                    todo.append(n)
        out.append(comp)
    return out


def water_body_components(
    water: Iterable[tuple[int, int]],
    *,
    cell: tuple[int, int],
    min_columns: int = MIN_WATER_COLUMNS,
    refs_for=None,
) -> list[dict[str, Any]]:
    """Package connected water columns as water-body feature candidates.

    @param water: water columns (vector map and/or aerial)
    @param cell: ``(tx, tz)``
    @param min_columns: drop slivers smaller than this
    @param refs_for: optional ``(component) -> [ref, ...]``; default synthetic
        ``water_<xmin>_<zmin>``
    @returns: list of candidate dicts (``kind`` ``water_body``)
    """
    out: list[dict[str, Any]] = []
    for comp in sorted(_components(set(water)), key=lambda c: (min(c), len(c))):
        if len(comp) < min_columns:
            continue
        xs = [x for x, _ in comp]
        zs = [z for _, z in comp]
        bbox = (min(xs), min(zs), max(xs), max(zs))
        if refs_for is not None:
            refs = list(refs_for(comp))
        else:
            refs = [f'water_{bbox[0]}_{bbox[1]}']
        outline = sorted(comp)
        out.append({
            'kind': 'water_body',
            'label': f'pond ({len(comp)} columns)',
            'street': None,
            'refs': refs,
            'bbox': bbox,
            'area': len(comp),
            'outline': [[x, z] for x, z in outline],
            'cells': [[int(cell[0]), int(cell[1])]],
            'portals': [],
            'pictures': [],
            'water_level': None,
            'bank': None,
            'edge': None,
        })
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


def build(spec: Mapping[str, Any]) -> Plan:
    """Build a water-body ``Plan`` from a validated spec.

    Places floor under each outline column, water up to ``water_level``, a bank
    ring at the water surface, and optional shoreline edge material.

    @param spec: output of :func:`load_spec` / :func:`load_spec_dict`
    @returns: offline ``Plan`` ready for preview/apply
    """
    outline = {(int(x), int(z)) for x, z in spec['outline']}
    level = int(spec['water_level'])
    depth = int(spec['depth'])
    floor = str(spec['floor'])
    bank_spec = spec['bank']
    edge_spec = spec.get('edge')
    bank = _bank_ring(outline, int(bank_spec['width']))
    shore = _shoreline(outline, bank) if edge_spec else set()

    plan = Plan(str(spec['name']))
    bed_y = level - depth
    for x, z in outline:
        plan.set(x, bed_y, z, floor)
        for y in range(bed_y + 1, level + 1):
            plan.set(x, y, z, 'water')
    for x, z in bank:
        if (x, z) in shore and edge_spec:
            plan.set(x, level, z, edge_spec['material'])
        else:
            plan.set(x, level, z, bank_spec['material'])
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
