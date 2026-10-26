#!/usr/bin/env python3
"""Lidar ghosts: DTM ground placeholders with DSM−DTM top colours (ADR 003/004).

Placeholder ground near **worked** cells (real ground/trees/decor/features). Default
Chebyshev frontier depth is 2 (``LIDAR_GHOST_RING``). No volumetric feature ghosts and
no trees/decor ghosts.

This module only *plans*: ``plan_ghost_ground`` from lidar heights, wrapped as the cell
journal's ghost planner (:func:`ghost_planner`). Ghost state is a view of the cell journal
(``CellRegistry.view()`` ``ghost`` / ``ground``); the registry supersedes ghost ground under
real ground and ensures the frontier as follow-up Builds after every real apply
(``site_kit.apply``). Ghost owners yield to real owners and never clash.

Top-block colour encodes DSM − DTM height per column (location check vs real features):

  light_gray_concrete  ≈ 0
  gray_concrete        1–2 blocks
  black_concrete       > 2

  Owner:  ghost_<tx>_<tz>_ground   (pass ghost-ground)

  python3 shims/lidar_ghost.py status --cell 48,-44 --ring 2
  python3 shims/lidar_ghost.py plan --cell 48,-44 --ring 2
  SITE_OWNER=<owner> python3 shims/lidar_ghost.py apply --cell 48,-44 --ring 2

``plan`` / unit tests inject height samples (no live server, no DEFRA download).
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

import cellmath
from cell_journal import ghost_name
from script_env import make_parser  # noqa: E402

DEFAULT_RING = 2
FILL_BOTTOM = 1                     # empty save has bedrock at y=0; fill solid above it
GROUND_BLOCK = 'light_gray_concrete'  # DTM surface, DSM − DTM ≈ 0
FEATURE_LOW_BLOCK = 'gray_concrete'   # DSM − DTM 1–2
FEATURE_HIGH_BLOCK = 'black_concrete'  # DSM − DTM > 2
FILL_BLOCK = 'stone'                # under the surface
FOOTPRINT_OK = 'lidar ghost massing (ADR 004); not a real feature build'
AIR = 'minecraft:air'

HeightsFn = Callable[[int, int, int, int], dict[str, Any]]

# Cell currently being planned/applied; SIGTERM names it so the operator can reconcile.
_in_flight_cell: tuple[int, int] | None = None


def set_in_flight_cell(cell: tuple[int, int] | None) -> None:
    """Record which cell ``ensure_cells`` is working on (tests and the SIGTERM handler).

    @param cell: (tx, tz), or None when idle
    """
    global _in_flight_cell
    _in_flight_cell = tuple(cell) if cell is not None else None


def handle_sigterm(signum: int, frame) -> None:  # noqa: ARG001
    """Exit cleanly on SIGTERM; name any in-flight cell and point at reconcile.

    Does not release the site lock (apply never claims it; ``site_lock.py claim`` does).

    @param signum: signal number
    @param frame: interrupted stack frame (unused)
    """
    cell = _in_flight_cell
    if cell is not None:
        msg = (f'lidar_ghost: stopped by SIGTERM while cell {cell[0]},{cell[1]} was in flight; '
               f'run cell_registry.py reconcile')
    else:
        msg = ('lidar_ghost: stopped by SIGTERM; '
               'run cell_registry.py reconcile if a cell was left begun')
    print(msg, file=sys.stderr, flush=True)
    raise SystemExit(128 + signal.SIGTERM)


def _progress(msg: str) -> None:
    """One progress/summary line on stderr, flushed so it shows while apply runs."""
    print(msg, file=sys.stderr, flush=True)


def summarise_statuses(counts: dict[str, int]) -> str:
    """Format the apply summary line from journal-status tallies.

    @param counts: keys ``landed`` / ``mismatched`` / ``abandoned`` / ``skipped``
    @returns: one summary line (without trailing newline)
    """
    return (f"lidar_ghost: summary landed={counts.get('landed', 0)} "
            f"mismatched={counts.get('mismatched', 0)} "
            f"abandoned={counts.get('abandoned', 0)} "
            f"skipped={counts.get('skipped', 0)}")


def ghost_owners_for(cell: tuple[int, int]) -> frozenset[str]:
    """Ghost plan name for one cell (the owner a real ground pass ignores).

    @param cell: (tx, tz)
    @returns: frozenset of owner names
    """
    return frozenset({ghost_name(cell, 'ground')})


def neighbour_cells(focus: tuple[int, int], ring: int = DEFAULT_RING,
                    include_focus: bool = False) -> list[tuple[int, int]]:
    """Cells within Chebyshev distance ``ring`` of ``focus``.

    @param focus: centre cell (usually the marker cell)
    @param ring: max Chebyshev distance
    @param include_focus: when False (default), omit the focus cell itself
    @returns: cells sorted by ring distance then (tx, tz)
    """
    if ring < 0:
        raise ValueError(f'ring must be >= 0, got {ring}')
    out: list[tuple[int, int]] = []
    for r in range(0 if include_focus else 1, ring + 1):
        out.extend(cellmath.ring_cells(r, origin=focus))
    return out


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


def top_block_for_feature_height(fh: int) -> str:
    """Surface block id for a DSM − DTM integer height (same bands as the old columns).

    @param fh: feature height in blocks (``<= 0`` → plain ground)
    @returns: short block id (no ``minecraft:`` prefix)
    """
    if fh <= 0:
        return GROUND_BLOCK
    if fh <= 2:
        return FEATURE_LOW_BLOCK
    return FEATURE_HIGH_BLOCK


def plan_ghost_ground(cell: tuple[int, int], heights: dict[str, Any] | None = None,
                      heights_fn: HeightsFn | None = None):
    """Plan solid DTM terrain for one cell (fill + colour-coded surface).

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
    plan = Plan(ghost_name((tx, tz), 'ground'))
    plan.outside_footprint_ok = FOOTPRINT_OK
    for i, x in enumerate(range(x0, x1 + 1)):
        for j, z in enumerate(range(z0, z1 + 1)):
            gy = int(ground[i, j])
            if gy < FILL_BOTTOM:
                continue
            for y in range(FILL_BOTTOM, gy):
                plan.set(x, y, z, FILL_BLOCK)
            fh = int(feature[i, j]) if feature.size else 0
            plan.set(x, gy, z, top_block_for_feature_height(fh))
    return plan


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


def ghost_planner(heights_fn: HeightsFn | None = None) -> Callable[[tuple[int, int], str], dict | None]:
    """The cell journal's ghost planner over the lidar ground plan builder.

    Heights are sampled once per cell (a few cells are cached).

    @param heights_fn: injectable sampler (tests / fixtures); default DEFRA cached tiles
    @returns: ``planner(cell, 'ground') -> {pos: state}``; None when lidar tiles are not
        cached for the cell (the registry then skips it). ``layer`` must be ``ground``.
    """
    cache: OrderedDict[tuple[int, int], dict | None] = OrderedDict()

    def heights(cell: tuple[int, int]) -> dict | None:
        cell = tuple(cell)
        if cell not in cache:
            x0, z0, x1, z1 = cellmath.bounds(cell)
            try:
                cache[cell] = (heights_fn or default_heights)(x0, z0, x1, z1)
            except LookupError as exc:                   # tile not cached and download is off
                print(f'lidar ghost: no lidar for cell {cell[0]},{cell[1]} ({exc})')
                cache[cell] = None
            while len(cache) > 4:
                cache.popitem(last=False)
        return cache[cell]

    def planner(cell: tuple[int, int], layer: str) -> dict | None:
        if layer != 'ground':
            raise ValueError(f'layer must be ground, got {layer!r}')
        h = heights(cell)
        if h is None:
            return None
        return plan_ghost_ground(cell, heights=h).cells

    return planner


def _stats(cells: dict) -> dict[str, int]:
    from collections import Counter
    from site_kit import bid
    counts = Counter(bid(b).split(':', 1)[-1] for b in cells.values())
    return {'blocks': len(cells), **dict(counts)}


def needs(view, *, force: bool = False) -> bool:
    """Whether a cell still needs ghost ground, from its journal view.

    @param view: ``CellView``
    @param force: re-plan when ghost ground is already present (not over real ground)
    @returns: True when ghost ground should be planned
    """
    return view.ground == 'none' or (force and view.ground != 'generated')


def cell_builds(reg, cell: tuple[int, int], layer: str = 'ground'):
    """The ghost Build for one cell (positions owned by real builds are dropped).

    @param reg: ``CellRegistry`` with a ghost planner
    @param cell: (tx, tz)
    @param layer: must be ``ground``
    @returns: Build, or None when lidar cannot plan the cell
    """
    if layer != 'ground':
        raise ValueError(f'layer must be ground, got {layer!r}')
    from cell_journal import Build
    planned = reg.ghosts(cell, layer) if reg.ghosts is not None else None
    if planned is None:
        return None
    real = reg.owned(planned, real_only=True)
    cells = {p: s for p, s in planned.items() if p not in real}
    name = ghost_name(cell, layer)
    return Build(name, f'ghost-{layer}', cells, modifies=(name,), strict=True, cell=tuple(cell))


def ensure_cells(reg, cells, *, apply=None, force: bool = False) -> list[dict[str, Any]]:
    """Plan (``apply`` None) or apply ghost ground each cell still needs.

    Unlike ``CellRegistry.ensure`` this also serves cells outside the frontier (an explicit
    ``--cell``, or the focus ring before any real work).

    Prints one flushed progress line per cell on stderr, then a summary counting builds by
    journal status (``landed`` / ``mismatched`` / ``abandoned`` / ``skipped``). Stdout is left
    for the caller (CLI JSON).

    @param reg: ``CellRegistry`` with a ghost planner
    @param cells: target cells
    @param apply: journal writer (``site_kit.McpWriter``); None = dry run
    @param force: re-plan ghost ground already present
    @returns: per-cell reports
    """
    targets = [tuple(c) for c in cells]
    n = len(targets)
    reports = []
    counts = {'landed': 0, 'mismatched': 0, 'abandoned': 0, 'skipped': 0}
    try:
        for i, cell in enumerate(targets, 1):
            set_in_flight_cell(cell)
            _progress(f'lidar_ghost: cell {cell[0]},{cell[1]} ({i}/{n})')
            rep: dict[str, Any] = {'cell': list(cell), 'dry': apply is None, 'actions': []}
            reports.append(rep)
            view = reg.cell(cell)
            if view.in_flight:
                rep['skipped'] = 'in-flight'
                counts['skipped'] += 1
                continue
            if not needs(view, force=force):
                if view.ground == 'generated':
                    rep['ground_skip'] = 'real-ground'
                rep['skipped'] = 'layers-complete'
                counts['skipped'] += 1
                continue
            build = cell_builds(reg, cell, 'ground')
            if build is None:
                rep['skipped'] = 'no-lidar'
                counts['skipped'] += 1
                continue
            rep['ground'] = _stats(build.cells)
            rep['actions'].append('plan-ground')
            if apply is not None:
                receipt = reg.change(build, apply)
                rep['actions'].append('apply-ground')
                status = receipt.status
                rep.setdefault('statuses', []).append(status)
                counts[status] = counts.get(status, 0) + 1
    finally:
        set_in_flight_cell(None)
    _progress(summarise_statuses(counts))
    return reports


def status_rows(reg, cells, ring: int) -> list[dict[str, Any]]:
    """Per-cell ghost status from the journal views.

    @param reg: ``CellRegistry``
    @param cells: cells to report
    @param ring: frontier depth
    @returns: rows
    """
    seeds = set(reg.seeds())
    frontier = reg.frontier(ring) if seeds else frozenset()
    rows = []
    for c in cells:
        v = reg.cell(c)
        want = needs(v)
        rows.append({
            'cell': list(c),
            'in_frontier': (c in frontier) if seeds else None,
            'real_work': c in seeds,
            'real_ground': v.ground == 'generated',
            'feature_overlap': bool(v.features),
            'needs_ghost_ground': want,
            'ghost_ground': 'applied' if v.ghost.ground else None,
            'ghosts_complete': bool(v.ghost.ground) or v.ground == 'generated',
            'in_flight': [f.plan for f in v.in_flight],
        })
    return rows


def targets_for(reg, focus: tuple[int, int], ring: int | None, *, include_focus: bool = False,
                frontier_when_seeded: bool = True) -> list[tuple[int, int]]:
    """Cells a ghost run covers: the journal frontier once real work exists, else the focus ring.

    @param reg: ``CellRegistry``
    @param focus: focus cell
    @param ring: depth (None: only the focus cell)
    @param include_focus: with a focus ring, include the focus cell
    @param frontier_when_seeded: use the registry frontier when there are seeds
    @returns: sorted cells
    """
    if ring is None:
        return [tuple(focus)]
    if frontier_when_seeded and reg.seeds():
        return sorted(reg.frontier(ring))
    return neighbour_cells(focus, ring, include_focus=include_focus)


def on_cell_focus(focus: tuple[int, int], ring: int | None = None, *, dry: bool = False,
                  force: bool = False, heights_fn: HeightsFn | None = None) -> list[dict[str, Any]]:
    """Ensure frontier ghosts when the marker is raised (cell_marker --raise / --adopt).

    All worked cells seed the frontier; before any real work, the ring around ``focus``
    (focus excluded) is ghosted so the first raise still shows neighbours.

    @param focus: newly focused cell
    @param ring: Chebyshev depth; None → ``ring_from_env()``
    @param dry: plan-only when True
    @param force: re-plan layers already present
    @param heights_fn: injectable sampler (tests)
    @returns: per-target reports
    """
    depth = ring_from_env() if ring is None else ring
    if depth < 0:
        raise ValueError(f'ring must be >= 0, got {depth}')
    reg, writer = _session(dry, heights_fn, depth)
    cells = targets_for(reg, focus, depth)
    return ensure_cells(reg, cells, apply=writer, force=force)


def _session(dry: bool, heights_fn: HeightsFn | None, ring: int):
    """(registry, writer): a view registry for dry runs, the live journal session otherwise."""
    planner = ghost_planner(heights_fn)
    if dry:
        import cell_registry
        return cell_registry.open_registry(ghosts=planner, ring=ring), None
    if not os.environ.get('SITE_OWNER'):
        raise SystemExit('SITE_OWNER is not set; claim the lock first (scripts/site_lock.py claim …), or run the whole command via scripts/site_lock.py run <owner> "<task>" -- <cmd>')
    import site_kit
    return site_kit.live_session(ghosts=planner, ring=ring)


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
    depth = a.ring if a.ring is not None else ring_from_env()

    if a.cmd == 'status':
        import cell_registry
        reg = cell_registry.open_registry(ghosts=None, ring=depth)
        cells = targets_for(reg, focus, a.ring, include_focus=a.include_focus)
        print(json.dumps({
            'focus': list(focus),
            'ring': depth,
            'seeds': [list(c) for c in reg.seeds()],
            'cells': status_rows(reg, cells, depth),
        }, indent=2))
        return 0

    dry = a.cmd != 'apply'
    if a.cmd == 'apply':
        # SIGINT already raises KeyboardInterrupt; SIGTERM would otherwise leave exit 144 unclear.
        signal.signal(signal.SIGTERM, handle_sigterm)
    reg, writer = _session(dry, heights_fn, depth)
    cells = targets_for(reg, focus, a.ring, include_focus=a.include_focus, frontier_when_seeded=False)
    reports = ensure_cells(reg, cells, apply=writer, force=a.force)
    print(json.dumps({'focus': list(focus), 'ring': a.ring, 'dry': dry, 'reports': reports}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
