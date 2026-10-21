#!/usr/bin/env python3
"""Idempotent lidar ghosts v2: frontier ring, split ground vs features (spec 2026-10).

Placeholder massing near **worked** cells (real ground/trees/decor/features), not only
focus-time neighbours. Default Chebyshev ring depth is 2 (``LIDAR_GHOST_RING``). No
trees/decor ghosts. Real applies refresh the frontier and strip grey mass under features.

  Owners:  ghost_<tx>_<tz>_ground / ghost_<tx>_<tz>_features
  STAGES:  ghost-ground / ghost-features  (meta kind=ghost; real layers stay ground-layer/…)

  python3 shims/lidar_ghost.py status --cell 48,-44 --ring 2
  python3 shims/lidar_ghost.py plan --cell 48,-44 --ring 2
  SITE_OWNER=<owner> python3 shims/lidar_ghost.py apply --cell 48,-44 --ring 2

``plan`` / unit tests inject height samples (no live server, no DEFRA download).
Post-apply reconcile runs from ``site_kit.apply`` after successful real work.
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

DEFAULT_RING = 2
FILL_BOTTOM = 1                     # empty save has bedrock at y=0; fill solid above it
GROUND_BLOCK = 'gray_concrete'      # DTM surface
FILL_BLOCK = 'stone'                # under the surface
FEATURE_BLOCK = 'light_gray_concrete'  # DSM − DTM mass
STAGE_GROUND = 'ghost-ground'
STAGE_FEATURES = 'ghost-features'
KIND_GHOST = 'ghost'
REAL_GROUND_KEYS = ('ground', 'ground-layer')
GHOST_APPLIED = frozenset({'applied', 'verified'})
GHOST_TERMINAL = frozenset({'superseded', 'stripped', 'partial'})
FOOTPRINT_OK = 'lidar ghost massing (ADR 003); not a real feature build'
AIR = 'minecraft:air'

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


def is_ghost_feature_owner(build: str) -> bool:
    """True when ``build`` is a lidar ghost features owner (not ground).

    @param build: verification/site build name
    @returns: whether this owner is grey feature mass
    """
    name = str(build)
    return name.startswith('ghost_') and name.endswith('_features')


def ghost_owners_for(cell: tuple[int, int]) -> frozenset[str]:
    """Both ghost plan names for one cell (for ``modifies`` / ignored_builds).

    @param cell: (tx, tz)
    @returns: frozenset of owner names
    """
    tx, tz = cell
    return frozenset({owner_name(tx, tz, 'ground'), owner_name(tx, tz, 'features')})


def ghost_modifies_for_plan(volume_plan) -> frozenset[str]:
    """Ghost owner names for every cell a feature plan touches (cross-cell footprints).

    @param volume_plan: feature ``site_kit.Plan``
    @returns: names to pass in ``site_kit.apply(..., modifies=...)``
    """
    out: set[str] = set()
    for cell in cells_for_positions(volume_plan.cells.keys()):
        out |= ghost_owners_for(cell)
    return frozenset(out)


def strip_ghost_features_before_feature_apply(volume_plan, *, cells_dir: Path | None = None) -> list[dict]:
    """Remove grey ghost-feature blocks inside a planned feature volume before live fill.

    @param volume_plan: offline feature plan about to apply
    @param cells_dir: STAGES override (tests)
    @returns: per-cell strip reports from ``apply_strip_plans``
    """
    strips = plan_strip_ghost_features(volume_plan, cells_dir=cells_dir)
    return apply_strip_plans(strips, dry=False, cells_dir=cells_dir)


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
    return (ghost_stage_status(cell, STAGE_GROUND, cells_dir) in GHOST_APPLIED
            and ghost_stage_status(cell, STAGE_FEATURES, cells_dir) in GHOST_APPLIED)


def real_ground_applied(cell: tuple[int, int], cells_dir: Path | None = None) -> bool:
    """True when the cell has a real ground layer stage in a finished state.

    @param cell: (tx, tz)
    @param cells_dir: STAGES directory override (tests)
    @returns: whether real ground owns the surface reference
    """
    stages = read_stages(cell, cells_dir).get('stages') or {}
    good = ('applying', 'applied', 'applied-with-mismatches', 'verified')
    for key in REAL_GROUND_KEYS:
        entry = stages.get(key)
        if isinstance(entry, dict) and entry.get('status') in good:
            return True
    return False


def list_stage_cells(cells_dir: Path | None = None) -> list[tuple[int, int]]:
    """Every cell that has a ``*-STAGES.json`` file.

    @param cells_dir: override (tests)
    @returns: sorted (tx, tz) list
    """
    from site_stages import CELLS

    root = cells_dir or CELLS
    if not root.is_dir():
        return []
    out: list[tuple[int, int]] = []
    for path in root.glob('*-STAGES.json'):
        stem = path.name[:-len('-STAGES.json')]
        if '_' not in stem:
            continue
        tx_s, tz_s = stem.split('_', 1)
        try:
            out.append((int(tx_s), int(tz_s)))
        except ValueError:
            continue
    return sorted(out)


def seed_cells(cells_dir: Path | None = None) -> list[tuple[int, int]]:
    """Cells where real refine has started (non-ghost stages).

    @param cells_dir: override (tests)
    @returns: sorted seed cells
    """
    return [c for c in list_stage_cells(cells_dir) if real_work_started(c, cells_dir)]


def frontier_targets(ring: int | None = None, cells_dir: Path | None = None) -> list[tuple[int, int]]:
    """Union of Chebyshev ``ring`` neighbourhoods around every seed cell.

    @param ring: depth; None → ``ring_from_env()``
    @param cells_dir: STAGES override (tests)
    @returns: sorted target cells (includes seeds)
    """
    depth = ring_from_env() if ring is None else ring
    if depth < 0:
        raise ValueError(f'ring must be >= 0, got {depth}')
    seeds = seed_cells(cells_dir)
    seen: set[tuple[int, int]] = set()
    for s in seeds:
        seen.update(neighbour_cells(s, depth, include_focus=True))
    return sorted(seen)


def real_feature_touches_cell(cell: tuple[int, int]) -> bool:
    """True when a non-layer feature build touches this cell's columns.

    @param cell: (tx, tz)
    @returns: whether real feature volume overlaps the cell
    """
    from cell_features import is_feature_build
    from touched_cells import touched_by_build

    x0, z0, x1, z1 = cellmath.bounds(cell)
    cols = {(x, z) for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)}
    for name, touched in touched_by_build().items():
        if is_feature_build(name) and cols & touched:
            return True
    return False


def ghost_ground_ready(cell: tuple[int, int], cells_dir: Path | None = None) -> bool:
    """True when ghost ground is applied and not superseded.

    @param cell: (tx, tz)
    @param cells_dir: override (tests)
    @returns: whether ghost ground blocks are expected in-world
    """
    st = ghost_stage_status(cell, STAGE_GROUND, cells_dir)
    return st in GHOST_APPLIED


def ghost_features_ready(cell: tuple[int, int], cells_dir: Path | None = None) -> bool:
    """True when ghost features are applied and not stripped/superseded.

    @param cell: (tx, tz)
    @param cells_dir: override (tests)
    @returns: whether ghost feature blocks are expected in-world
    """
    st = ghost_stage_status(cell, STAGE_FEATURES, cells_dir)
    return st in GHOST_APPLIED


def needs_ghost_ground(cell: tuple[int, int], cells_dir: Path | None = None) -> bool:
    """Whether ghost ground should be planned/applied for ``cell``.

    @param cell: (tx, tz)
    @param cells_dir: override (tests)
    @returns: True when placeholders are still appropriate
    """
    if real_ground_applied(cell, cells_dir):
        return False
    if ghost_ground_ready(cell, cells_dir):
        return False
    return True


def needs_ghost_features(cell: tuple[int, int], cells_dir: Path | None = None) -> bool:
    """Whether ghost features should be planned/applied for ``cell``.

    @param cell: (tx, tz)
    @param cells_dir: override (tests)
    @returns: True when grey mass is still appropriate
    """
    if real_feature_touches_cell(cell):
        return False
    if not (real_ground_applied(cell, cells_dir) or ghost_ground_ready(cell, cells_dir)):
        return False
    if ghost_features_ready(cell, cells_dir):
        return False
    return True


def _write_ghost_stage(cell: tuple[int, int], key: str, status: str, cells_dir: Path | None,
                       **fields) -> None:
    """Update one ghost stage entry without a live apply.

    @param cell: (tx, tz)
    @param key: ghost stage key
    @param status: new status string
    @param cells_dir: STAGES override (tests)
    @param fields: extra JSON fields on the entry
    """
    import site_stages

    data = read_stages(cell, cells_dir)
    entry = data['stages'].get(key)
    if not isinstance(entry, dict):
        entry = {'kind': KIND_GHOST}
    entry.update({'status': status, **fields})
    data['stages'][key] = entry
    if cells_dir is not None:
        path = stage_path(cell, cells_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + '\n')
    else:
        site_stages.write(cell, data)


def supersede_ghost_ground(cell: tuple[int, int], cells_dir: Path | None = None, *,
                           note: str = 'real ground layer applied') -> None:
    """Mark ghost-ground superseded after real ground overwrites blocks.

    @param cell: (tx, tz)
    @param cells_dir: override (tests)
    @param note: human reason stored on the stage entry
    """
    if ghost_stage_status(cell, STAGE_GROUND, cells_dir) in GHOST_APPLIED:
        _write_ghost_stage(cell, STAGE_GROUND, 'superseded', cells_dir, note=note)


def mark_ghost_features_stripped_for_volume(volume_plan, *, cells_dir: Path | None = None) -> list[tuple[int, int]]:
    """Mark ghost-features stages stripped in every cell the volume touches.

    Used after a real feature apply when blocks were overwritten in place (so
    post-apply ``plan_strip_ghost_features`` finds no registry hits) but grey
    mass may still be present in cross-cell columns until stripped or replaced.

    @param volume_plan: applied feature plan
    @param cells_dir: STAGES override (tests)
    @returns: cells whose ghost-features stage was updated
    """
    touched: list[tuple[int, int]] = []
    for cell in sorted(cells_for_positions(volume_plan.cells.keys())):
        st = ghost_stage_status(cell, STAGE_FEATURES, cells_dir)
        if st in GHOST_APPLIED:
            mark_ghost_features_stripped(cell, partial=False, cells_dir=cells_dir)
            touched.append(cell)
    return touched


def mark_ghost_features_stripped(cell: tuple[int, int], *, partial: bool = False,
                                 cells_dir: Path | None = None,
                                 note: str = 'real feature overlap') -> None:
    """Mark ghost-features terminal so idempotent ensure can re-run when needed.

    @param cell: (tx, tz)
    @param partial: use ``partial`` instead of ``stripped``
    @param cells_dir: override (tests)
    @param note: human reason stored on the stage entry
    """
    status = 'partial' if partial else 'stripped'
    prev = ghost_stage_status(cell, STAGE_FEATURES, cells_dir)
    if prev in GHOST_APPLIED | GHOST_TERMINAL:
        _write_ghost_stage(cell, STAGE_FEATURES, status, cells_dir, note=note)


def cells_for_positions(positions) -> set[tuple[int, int]]:
    """Map block positions to coverage cells.

    @param positions: iterable of (x, y, z)
    @returns: set of (tx, tz)
    """
    import cellmath

    return {cellmath.cell_of(x, z) for x, _y, z in positions}


def plan_strip_ghost_features(volume_plan, *, cells_dir: Path | None = None):
    """Plan air cells for ghost-feature-owned blocks inside a real feature volume.

    @param volume_plan: applied feature ``site_kit.Plan``
    @param cells_dir: unused; reserved for tests
    @returns: list of (cell, strip_plan) pairs (empty when nothing to strip)
    """
    from site_kit import Plan, built_cells

    del cells_dir
    positions = set(volume_plan.cells.keys())
    owned = built_cells(within=positions)
    by_cell: dict[tuple[int, int], dict] = {}
    for pos, (build, _st) in owned.items():
        if not is_ghost_feature_owner(build):
            continue
        import cellmath

        cell = cellmath.cell_of(pos[0], pos[2])
        by_cell.setdefault(cell, {})[pos] = build
    out = []
    for cell, hits in sorted(by_cell.items()):
        tx, tz = cell
        strip = Plan(f'ghost_strip_{tx}_{tz_token(tz)}_features')
        strip.outside_footprint_ok = FOOTPRINT_OK
        for pos in hits:
            strip.set(*pos, AIR)
        if strip.cells:
            out.append((cell, strip))
    return out


def apply_strip_plans(strips, *, dry: bool = False, cells_dir: Path | None = None) -> list[dict]:
    """Apply ghost-feature strip plans and update STAGES.

    @param strips: output of ``plan_strip_ghost_features``
    @param dry: plan-only
    @param cells_dir: STAGES override (tests)
    @returns: per-cell strip reports
    """
    reports: list[dict] = []
    if not strips:
        return reports
    if not dry and not os.environ.get('SITE_OWNER'):
        raise SystemExit('SITE_OWNER is not set; claim the lock before stripping ghost features')
    from site_kit import apply

    for cell, strip in strips:
        rep = {'cell': list(cell), 'blocks': len(strip.cells), 'dry': dry}
        if dry:
            reports.append(rep)
            continue
        mods = {owner_name(cell[0], cell[1], 'features')}
        apply(strip, dry=False, modifies=mods, stage=STAGE_FEATURES, cell=cell,
              meta={'kind': KIND_GHOST, 'note': 'strip ghost features under real build'},
              strict=False)
        mark_ghost_features_stripped(cell, partial=False, cells_dir=cells_dir)
        rep['stripped'] = True
        reports.append(rep)
    return reports


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
                skip_if_real: bool = False) -> dict[str, Any]:
    """Plan (and optionally apply) ghost ground and features for one cell (split lifecycle).

    @param cell: (tx, tz)
    @param dry: when True, do not write the world or STAGES (default)
    @param force: re-plan/apply even if a layer is already applied
    @param heights_fn: injectable height sampler (tests / offline)
    @param cells_dir: STAGES directory override (tests)
    @param skip_if_real: legacy whole-cell skip when any real work exists
    @returns: report dict
    """
    tx, tz = cell
    report: dict[str, Any] = {'cell': [tx, tz], 'dry': dry, 'actions': []}
    if skip_if_real and real_work_started(cell, cells_dir):
        report['skipped'] = 'real-work'
        return report
    want_ground = force or needs_ghost_ground(cell, cells_dir)
    want_features = force or needs_ghost_features(cell, cells_dir)
    if want_ground and not want_features and not real_ground_applied(cell, cells_dir):
        # Ground and features are applied in one ensure pass; plan both together.
        want_features = True
    if not want_ground and not want_features:
        report['skipped'] = 'layers-complete'
        return report

    h = None
    if heights_fn is not None:
        x0, z0, x1, z1 = cellmath.bounds(cell)
        h = heights_fn(x0, z0, x1, z1)

    if want_ground:
        ground_plan = plan_ghost_ground(cell, heights=h, heights_fn=heights_fn)
        report['ground'] = plan_stats(ground_plan)
        report['actions'].append('plan-ground')
    else:
        ground_plan = None
        report['ground_skip'] = ghost_stage_status(cell, STAGE_GROUND, cells_dir) or 'real-ground'

    if want_features:
        feature_plan = plan_ghost_features(cell, heights=h, heights_fn=heights_fn)
        report['features'] = plan_stats(feature_plan)
        report['actions'].append('plan-features')
    else:
        feature_plan = None
        report['features_skip'] = ghost_stage_status(cell, STAGE_FEATURES, cells_dir) or 'no-ground-or-overlap'

    if dry:
        return report

    if not os.environ.get('SITE_OWNER'):
        raise SystemExit('SITE_OWNER is not set; claim the lock first (scripts/site_lock.py claim …)')

    from site_kit import apply

    mods = ghost_owners_for(cell)
    meta = {'kind': KIND_GHOST, 'note': 'lidar ghost (DTM / DSM−DTM); real layers overwrite'}
    if ground_plan is not None:
        apply(ground_plan, dry=False, modifies=mods, stage=STAGE_GROUND, cell=cell, meta=meta, strict=True)
        report['actions'].append('apply-ground')
    if feature_plan is not None:
        apply(feature_plan, dry=False, modifies=mods, stage=STAGE_FEATURES, cell=cell, meta=meta, strict=True)
        report['actions'].append('apply-features')
    return report


def ensure_frontier(*, ring: int | None = None, dry: bool = True, force: bool = False,
                    heights_fn: HeightsFn | None = None,
                    cells_dir: Path | None = None) -> dict[str, Any]:
    """Ensure ghosts for every cell in the v2 frontier (all seeds + ring union).

    @param ring: Chebyshev depth; None → env default (2)
    @param dry: plan-only when True
    @param force: re-apply layers even when applied
    @param heights_fn: injectable sampler
    @param cells_dir: STAGES override (tests)
    @returns: summary with seeds, targets, and per-cell reports
    """
    depth = ring_from_env() if ring is None else ring
    seeds = seed_cells(cells_dir)
    targets = frontier_targets(depth, cells_dir)
    reports = [ensure_cell(c, dry=dry, force=force, heights_fn=heights_fn, cells_dir=cells_dir)
               for c in targets]
    return {'ring': depth, 'seeds': [list(c) for c in seeds], 'targets': [list(c) for c in targets],
            'dry': dry, 'reports': reports}


def reconcile_after_real_apply(plan, *, stage_key: str, cell: tuple[int, int],
                               mismatches: int = 0, cells_dir: Path | None = None,
                               heights_fn: HeightsFn | None = None) -> dict[str, Any]:
    """Refresh frontier ghosts after a successful real apply (not ghost applies).

    @param plan: applied plan
    @param stage_key: STAGES key written by the apply
    @param cell: primary cell for the apply
    @param mismatches: readback mismatch count (skip reconcile when non-zero)
    @param cells_dir: STAGES override (tests)
    @param heights_fn: injectable sampler for follow-up ensure
    @returns: reconcile report
    """
    if mismatches:
        return {'skipped': 'readback-mismatches', 'cell': list(cell)}
    rep: dict[str, Any] = {'cell': list(cell), 'stage': stage_key}
    if stage_key in REAL_GROUND_KEYS:
        supersede_ghost_ground(cell, cells_dir)
        rep['ghost_ground'] = 'superseded'
    elif stage_key.startswith('feature-'):
        strips = plan_strip_ghost_features(plan, cells_dir=cells_dir)
        rep['strip'] = apply_strip_plans(strips, dry=False, cells_dir=cells_dir)
        rep['ghost_features_marked'] = [
            list(c) for c in mark_ghost_features_stripped_for_volume(plan, cells_dir=cells_dir)
        ]
    frontier = ensure_frontier(dry=False, heights_fn=heights_fn, cells_dir=cells_dir)
    rep['frontier'] = {'seeds': frontier['seeds'], 'targets': frontier['targets'],
                        'planned': sum(1 for r in frontier['reports'] if 'skipped' not in r)}
    return rep


def ensure_ring(focus: tuple[int, int], ring: int = DEFAULT_RING, *, dry: bool = True,
                force: bool = False, include_focus: bool = False,
                heights_fn: HeightsFn | None = None, cells_dir: Path | None = None,
                skip_if_real: bool = False) -> list[dict[str, Any]]:
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
    """Ensure frontier ghosts when CURRENT-CELL focus is set (raise / handoff).

    Uses all worked cells as seeds (v2 frontier). When nothing is seeded yet,
    falls back to a ring around ``focus`` so the first raise still shows neighbours.
    Default ring is ``LIDAR_GHOST_RING`` or 2.

    @param focus: newly focused cell
    @param ring: Chebyshev depth; None → ``ring_from_env()``
    @param dry: plan-only when True (marker ``--dry-run``)
    @param force: re-apply even when ghosts already applied
    @param heights_fn: injectable sampler (tests)
    @param cells_dir: STAGES override (tests)
    @returns: per-target reports
    """
    depth = ring_from_env() if ring is None else ring
    if depth < 0:
        raise ValueError(f'ring must be >= 0, got {depth}')
    if seed_cells(cells_dir):
        return ensure_frontier(ring=depth, dry=dry, force=force, heights_fn=heights_fn,
                               cells_dir=cells_dir)['reports']
    return ensure_ring(focus, depth, dry=dry, force=force, include_focus=False,
                       heights_fn=heights_fn, cells_dir=cells_dir, skip_if_real=False)


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
        depth = a.ring if a.ring is not None else ring_from_env()
        seeds = seed_cells()
        if a.ring is None:
            cells = [focus]
        elif seed_cells():
            cells = frontier_targets(depth)
        else:
            cells = neighbour_cells(focus, depth, include_focus=a.include_focus)
        rows = []
        for c in cells:
            rows.append({
                'cell': list(c),
                'in_frontier': c in set(frontier_targets(depth)) if seed_cells() else None,
                'real_work': real_work_started(c),
                'real_ground': real_ground_applied(c),
                'feature_overlap': real_feature_touches_cell(c),
                'needs_ghost_ground': needs_ghost_ground(c),
                'needs_ghost_features': needs_ghost_features(c),
                'ghost_ground': ghost_stage_status(c, STAGE_GROUND),
                'ghost_features': ghost_stage_status(c, STAGE_FEATURES),
                'ghosts_complete': ghosts_complete(c),
            })
        print(json.dumps({
            'focus': list(focus),
            'ring': depth,
            'seeds': [list(c) for c in seeds],
            'cells': rows,
        }, indent=2))
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
