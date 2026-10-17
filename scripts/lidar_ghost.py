#!/usr/bin/env python3
"""Idempotent lidar ghosts: DTM ground + grey DSM−DTM features (ADR 003, task #26).

Placeholder massing for neighbour cells before real refine. No trees/decor ghosts in v1.
No per-cell generated Python — one tool plans and applies from DEFRA composites.

  Owners:  ghost_<tx>_<tz>_ground / ghost_<tx>_<tz>_features
  STAGES:  ghost-ground / ghost-features  (meta kind=ghost; real layers stay ground-layer/…)

  python3 scripts/lidar_ghost.py plan --cell 48,-44              # dry-run one cell (no Minecraft)
  python3 scripts/lidar_ghost.py plan --cell 48,-44 --ring 1    # dry-run Chebyshev neighbours
  python3 scripts/lidar_ghost.py status --cell 48,-44 --ring 1
  SITE_OWNER=<owner> python3 scripts/lidar_ghost.py apply --cell 48,-44 --ring 1

``plan`` / unit tests inject height samples (no live server, no DEFRA download).
Real refine still uses ground_layer / trees_layer / decor_layer / house_build / feature_build;
those layers treat ghost owners as overwritable (see ground_layer.ignored_builds).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

import cellmath
from script_env import ROOT, make_parser  # noqa: E402

DEFAULT_RING = 1
FILL_BOTTOM = 1                     # empty save has bedrock at y=0; fill solid above it
GROUND_BLOCK = 'gray_concrete'      # DTM surface
FILL_BLOCK = 'stone'                # under the surface
FEATURE_BLOCK = 'light_gray_concrete'  # DSM − DTM mass
STAGE_GROUND = 'ghost-ground'
STAGE_FEATURES = 'ghost-features'
KIND_GHOST = 'ghost'
FOOTPRINT_OK = 'lidar ghost massing (ADR 003); not a real feature build'

HeightsFn = Callable[[int, int, int, int], dict[str, Any]]


def tz_token(tz: int) -> str:
    """Encode tz for owner names (negative → ``m`` prefix), matching ground_layer.plan_name."""
    return f"{'m' if tz < 0 else ''}{abs(tz)}"


def owner_name(tx: int, tz: int, layer: str) -> str:
    """Build ownership / plan name, e.g. ``ghost_48_m44_ground``.

    @param tx: cell tile X
    @param tz: cell tile Z
    @param layer: ``ground`` or ``features``
    @returns: owner string
    """
    if layer not in ('ground', 'features'):
        raise ValueError(f'layer must be ground or features, got {layer!r}')
    return f'ghost_{tx}_{tz_token(tz)}_{layer}'


def ghost_owners_for(cell: tuple[int, int]) -> frozenset[str]:
    """Both ghost plan names for one cell (for ``modifies`` / ignored_builds).

    @param cell: (tx, tz)
    @returns: frozenset of owner names
    """
    tx, tz = cell
    return frozenset({owner_name(tx, tz, 'ground'), owner_name(tx, tz, 'features')})


def neighbour_cells(focus: tuple[int, int], ring: int = DEFAULT_RING,
                    include_focus: bool = False) -> list[tuple[int, int]]:
    """Cells within Chebyshev distance ``ring`` of ``focus``.

    @param focus: centre cell (usually CURRENT-CELL)
    @param ring: max Chebyshev distance (default 1)
    @param include_focus: when False (default), omit the focus cell itself
    @returns: cells sorted by ring distance then (tx, tz)
    """
    if ring < 0:
        raise ValueError(f'ring must be >= 0, got {ring}')
    out: list[tuple[int, int]] = []
    for r in range(0 if include_focus else 1, ring + 1):
        out.extend(cellmath.ring_cells(r, origin=focus))
    return out


def stage_path(cell: tuple[int, int], cells_dir: Path | None = None) -> Path:
    """Path of ``site/cells/<tx>_<tz>-STAGES.json``.

    @param cell: (tx, tz)
    @param cells_dir: override (tests)
    @returns: path
    """
    from site_stages import CELLS, stage_path as _sp
    return _sp(cell, cells_dir or CELLS)


def read_stages(cell: tuple[int, int], cells_dir: Path | None = None) -> dict:
    """Load STAGES for a cell, or an empty stages dict.

    @param cell: (tx, tz)
    @param cells_dir: override (tests)
    @returns: ``{'cell': 'tx,tz', 'stages': {...}}``
    """
    path = stage_path(cell, cells_dir)
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {'cell': f'{cell[0]},{cell[1]}', 'stages': {}}
    data.setdefault('cell', f'{cell[0]},{cell[1]}')
    data.setdefault('stages', {})
    return data


def is_ghost_stage(key: str, entry: dict | None) -> bool:
    """True when a STAGES entry is a lidar ghost (not real refine).

    @param key: stage key
    @param entry: stage value
    @returns: whether this is ghost massing
    """
    if not isinstance(entry, dict):
        return False
    if entry.get('kind') == KIND_GHOST:
        return True
    return key in (STAGE_GROUND, STAGE_FEATURES)


def real_work_started(cell: tuple[int, int], cells_dir: Path | None = None) -> bool:
    """True when the cell has a non-ghost stage that is applying or finished.

    @param cell: (tx, tz)
    @param cells_dir: override (tests)
    @returns: whether real refine has begun
    """
    stages = read_stages(cell, cells_dir).get('stages') or {}
    for key, entry in stages.items():
        if is_ghost_stage(key, entry):
            continue
        if isinstance(entry, dict) and entry.get('status') in (
                'applying', 'applied', 'applied-with-mismatches', 'verified'):
            return True
    return False


def ghost_stage_status(cell: tuple[int, int], key: str, cells_dir: Path | None = None) -> str | None:
    """Status string for a ghost stage key, or None if absent.

    @param cell: (tx, tz)
    @param key: ``ghost-ground`` or ``ghost-features``
    @param cells_dir: override (tests)
    @returns: status or None
    """
    entry = (read_stages(cell, cells_dir).get('stages') or {}).get(key)
    if isinstance(entry, dict):
        return entry.get('status')
    return None


def ghosts_complete(cell: tuple[int, int], cells_dir: Path | None = None) -> bool:
    """True when both ghost stages are applied (idempotent short-circuit).

    @param cell: (tx, tz)
    @param cells_dir: override (tests)
    @returns: whether ground + features ghosts are done
    """
    good = ('applied', 'verified')
    return (ghost_stage_status(cell, STAGE_GROUND, cells_dir) in good
            and ghost_stage_status(cell, STAGE_FEATURES, cells_dir) in good)


def default_heights(x0: int, z0: int, x1: int, z1: int) -> dict[str, Any]:
    """Sample DEFRA composite heights for an inclusive block box (needs cached tiles).

    @param x0: min block X
    @param z0: min block Z
    @param x1: max block X
    @param z1: max block Z
    @returns: ``mc_heights`` dict (ground_y, feature/diff, …)
    """
    import defra_lidar as dl
    lidar = dl.DefraLidar(download=False)
    en = dl.mc_box_en(x0, z0, x1, z1)
    lidar.ensure(*en)
    return dl.mc_heights(lidar, x0, z0, x1, z1)


def plan_ghost_ground(cell: tuple[int, int], heights: dict[str, Any] | None = None,
                      heights_fn: HeightsFn | None = None):
    """Plan solid DTM terrain for one cell (fill + surface).

    @param cell: (tx, tz)
    @param heights: precomputed ``mc_heights`` for the cell box (tests)
    @param heights_fn: sampler when ``heights`` is omitted
    @returns: ``site_kit.Plan``
    """
    from site_kit import Plan

    tx, tz = cell
    x0, z0, x1, z1 = cellmath.bounds(cell)
    h = heights if heights is not None else (heights_fn or default_heights)(x0, z0, x1, z1)
    ground = np.asarray(h['ground_y'])
    plan = Plan(owner_name(tx, tz, 'ground'))
    plan.outside_footprint_ok = FOOTPRINT_OK
    for i, x in enumerate(range(x0, x1 + 1)):
        for j, z in enumerate(range(z0, z1 + 1)):
            gy = int(ground[i, j])
            if gy < FILL_BOTTOM:
                continue
            for y in range(FILL_BOTTOM, gy):
                plan.set(x, y, z, FILL_BLOCK)
            plan.set(x, gy, z, GROUND_BLOCK)
    return plan


def plan_ghost_features(cell: tuple[int, int], heights: dict[str, Any] | None = None,
                        heights_fn: HeightsFn | None = None):
    """Plan grey DSM−DTM columns above ghost ground (no trees/decor).

    @param cell: (tx, tz)
    @param heights: precomputed ``mc_heights`` for the cell box (tests)
    @param heights_fn: sampler when ``heights`` is omitted
    @returns: ``site_kit.Plan``
    """
    from site_kit import Plan

    tx, tz = cell
    x0, z0, x1, z1 = cellmath.bounds(cell)
    h = heights if heights is not None else (heights_fn or default_heights)(x0, z0, x1, z1)
    ground = np.asarray(h['ground_y'])
    feature = np.asarray(h.get('feature', h.get('diff')))
    plan = Plan(owner_name(tx, tz, 'features'))
    plan.outside_footprint_ok = FOOTPRINT_OK
    for i, x in enumerate(range(x0, x1 + 1)):
        for j, z in enumerate(range(z0, z1 + 1)):
            fh = int(feature[i, j])
            if fh <= 0:
                continue
            gy = int(ground[i, j])
            for y in range(gy + 1, gy + fh + 1):
                plan.set(x, y, z, FEATURE_BLOCK)
    return plan


def plan_stats(plan) -> dict[str, int]:
    """Compact counts for CLI / tests.

    @param plan: ``site_kit.Plan``
    @returns: block counts by short id plus total
    """
    from collections import Counter
    from site_kit import bid
    counts = Counter(bid(b).split(':', 1)[-1] for b in plan.cells.values())
    return {'blocks': len(plan.cells), **dict(counts)}


def ensure_cell(cell: tuple[int, int], *, dry: bool = True, force: bool = False,
                heights_fn: HeightsFn | None = None, cells_dir: Path | None = None,
                skip_if_real: bool = True) -> dict[str, Any]:
    """Plan (and optionally apply) ghost ground + features for one cell.

    Idempotent: when both ghost stages are already applied and ``force`` is false,
    returns without rebuilding. When ``skip_if_real`` and real work has started, skips.

    @param cell: (tx, tz)
    @param dry: when True, do not write the world or STAGES (default)
    @param force: re-plan/apply even if ghosts are already applied
    @param heights_fn: injectable height sampler (tests / offline)
    @param cells_dir: STAGES directory override (tests)
    @param skip_if_real: skip cells that already have real refine stages
    @returns: report dict
    """
    tx, tz = cell
    report: dict[str, Any] = {'cell': [tx, tz], 'dry': dry, 'actions': []}
    if skip_if_real and real_work_started(cell, cells_dir):
        report['skipped'] = 'real-work'
        return report
    if not force and ghosts_complete(cell, cells_dir):
        report['skipped'] = 'already-applied'
        return report

    h = None
    if heights_fn is not None:
        x0, z0, x1, z1 = cellmath.bounds(cell)
        h = heights_fn(x0, z0, x1, z1)

    ground_plan = plan_ghost_ground(cell, heights=h, heights_fn=heights_fn)
    feature_plan = plan_ghost_features(cell, heights=h, heights_fn=heights_fn)
    report['ground'] = plan_stats(ground_plan)
    report['features'] = plan_stats(feature_plan)
    report['actions'] = ['plan-ground', 'plan-features']

    if dry:
        return report

    if not os.environ.get('SITE_OWNER'):
        raise SystemExit('SITE_OWNER is not set; claim the lock first (scripts/site_lock.py claim …)')

    from site_kit import apply

    mods = ghost_owners_for(cell)
    meta = {'kind': KIND_GHOST, 'note': 'lidar ghost (DTM / DSM−DTM); real layers overwrite'}
    apply(ground_plan, dry=False, modifies=mods, stage=STAGE_GROUND, cell=cell, meta=meta, strict=True)
    apply(feature_plan, dry=False, modifies=mods, stage=STAGE_FEATURES, cell=cell, meta=meta, strict=True)
    report['actions'].extend(['apply-ground', 'apply-features'])
    return report


def ensure_ring(focus: tuple[int, int], ring: int = DEFAULT_RING, *, dry: bool = True,
                force: bool = False, include_focus: bool = False,
                heights_fn: HeightsFn | None = None, cells_dir: Path | None = None,
                skip_if_real: bool = True) -> list[dict[str, Any]]:
    """Ensure ghosts for every cell in the Chebyshev ring around ``focus``.

    @param focus: centre cell
    @param ring: neighbour depth (default 1)
    @param dry: plan-only when True
    @param force: re-apply even when already ghosted
    @param include_focus: also ghost the focus cell
    @param heights_fn: injectable sampler
    @param cells_dir: STAGES override
    @param skip_if_real: skip cells with real refine
    @returns: list of per-cell reports
    """
    return [ensure_cell(c, dry=dry, force=force, heights_fn=heights_fn, cells_dir=cells_dir,
                        skip_if_real=skip_if_real)
            for c in neighbour_cells(focus, ring, include_focus=include_focus)]


def ring_from_env(default: int = DEFAULT_RING) -> int:
    """Neighbour ring depth from ``LIDAR_GHOST_RING``, else ``default``.

    @param default: fallback when the env var is unset
    @returns: non-negative ring depth
    """
    raw = os.environ.get('LIDAR_GHOST_RING')
    if raw is None or raw.strip() == '':
        return default
    try:
        ring = int(raw)
    except ValueError as e:
        raise ValueError(f'LIDAR_GHOST_RING must be an int, got {raw!r}') from e
    if ring < 0:
        raise ValueError(f'LIDAR_GHOST_RING must be >= 0, got {ring}')
    return ring


def on_cell_focus(focus: tuple[int, int], ring: int | None = None, *, dry: bool = False,
                  force: bool = False, heights_fn: HeightsFn | None = None,
                  cells_dir: Path | None = None) -> list[dict[str, Any]]:
    """Ensure neighbour ghosts when CURRENT-CELL focus is set (raise / handoff).

    Skips the focus cell itself and any neighbour that already has real refine.
    Idempotent via ``ensure_cell``. Default ring is ``LIDAR_GHOST_RING`` or 1.

    @param focus: newly focused cell
    @param ring: Chebyshev depth; None → ``ring_from_env()``
    @param dry: plan-only when True (marker ``--dry-run``)
    @param force: re-apply even when ghosts already applied
    @param heights_fn: injectable sampler (tests)
    @param cells_dir: STAGES override (tests)
    @returns: per-neighbour reports from ``ensure_ring``
    """
    depth = ring_from_env() if ring is None else ring
    if depth < 0:
        raise ValueError(f'ring must be >= 0, got {depth}')
    return ensure_ring(focus, depth, dry=dry, force=force, include_focus=False,
                       heights_fn=heights_fn, cells_dir=cells_dir, skip_if_real=True)


def _mock_or_default(heights_json: Path | None) -> HeightsFn | None:
    """Load a fixture heights JSON, or None to use DEFRA defaults."""
    if heights_json is None:
        return None

    data = json.loads(heights_json.read_text())

    def fn(x0: int, z0: int, x1: int, z1: int) -> dict[str, Any]:
        nx, nz = x1 - x0 + 1, z1 - z0 + 1
        gy = np.asarray(data['ground_y'], dtype=np.int64)
        feat = np.asarray(data['feature'], dtype=np.int64)
        if gy.shape != (nx, nz) or feat.shape != (nx, nz):
            raise SystemExit(
                f'heights fixture shape {gy.shape}/{feat.shape} != cell box {(nx, nz)}')
        return {'ground_y': gy, 'feature': feat, 'diff': feat}

    return fn


def main(argv: list[str] | None = None) -> int:
    """CLI entry: plan / status / apply.

    @param argv: optional argument list (tests)
    @returns: process exit code
    """
    ap = make_parser(__doc__)
    sub = ap.add_subparsers(dest='cmd', required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument('--cell', required=True, help='focus cell tx,tz')
        p.add_argument('--ring', type=int, default=None,
                       help=f'Chebyshev neighbour depth (default: single cell; '
                            f'use {DEFAULT_RING} for the usual neighbour ring)')
        p.add_argument('--include-focus', action='store_true',
                       help='with --ring, also process the focus cell')
        p.add_argument('--heights-json', type=Path, default=None,
                       help='fixture: {"ground_y":[[…]],"feature":[[…]]} for the cell box')
        p.add_argument('--force', action='store_true', help='re-plan even if ghosts already applied')

    for name in ('plan', 'status', 'apply'):
        add_common(sub.add_parser(name))

    a = ap.parse_args(argv)
    focus = cellmath.parse_cell(a.cell)
    heights_fn = _mock_or_default(a.heights_json)

    if a.cmd == 'status':
        cells = ([focus] if a.ring is None
                 else neighbour_cells(focus, a.ring, include_focus=a.include_focus))
        rows = []
        for c in cells:
            rows.append({
                'cell': list(c),
                'real_work': real_work_started(c),
                'ghost_ground': ghost_stage_status(c, STAGE_GROUND),
                'ghost_features': ghost_stage_status(c, STAGE_FEATURES),
                'ghosts_complete': ghosts_complete(c),
            })
        print(json.dumps({'focus': list(focus), 'cells': rows}, indent=2))
        return 0

    dry = a.cmd != 'apply'
    if a.ring is None:
        reports = [ensure_cell(focus, dry=dry, force=a.force, heights_fn=heights_fn)]
    else:
        reports = ensure_ring(focus, a.ring, dry=dry, force=a.force,
                              include_focus=a.include_focus, heights_fn=heights_fn)
    print(json.dumps({'focus': list(focus), 'ring': a.ring, 'dry': dry, 'reports': reports},
                     indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
