"""Restartable preparation of a named cell, independent of queue order.

Report-only by default. Explicit apply uses the existing local map extractor,
stable plan allocator and cached-lidar single-cell ghost path, under the site lock.
No downloads, marker moves, real rebuilds or mask edits happen here.
"""
from __future__ import annotations

import contextlib
import json
import shutil
import sys
from pathlib import Path

import cellmath
from cell_readiness import ReadinessPolicy


def prepared_place_slug(cell: tuple[int, int]) -> str:
    """Stable place id for a cell-scoped prepare extract.

    @param cell: coverage cell ``(tx, tz)``
    @returns: place slug under ``reference/vector_map/``
    """
    return f'prepared-{cell[0]}-{cell[1]}'


def _vector_map_provider_package() -> tuple[dict, Path]:
    """Locate the world's single packaged ``vector_map`` provider.

    @returns: ``(catalogue_row, absolute_package_dir)``
    @raises RuntimeError: when the catalogue is missing or ambiguous
    """
    from cell_sources import load_place_sources, provider_package_dir

    data = load_place_sources()
    candidates = [
        row for row in data.get('providers', [])
        if 'vector_map' in row.get('types', []) and row.get('package')
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            'Configure exactly one packaged vector_map provider in '
            'config/local-providers.json (system catalogue also considered); '
            f'found {len(candidates)}'
        )
    row = candidates[0]
    package = provider_package_dir(row['id'], data)
    if package is None or not package.is_dir():
        raise RuntimeError(f"vector_map provider {row['id']!r} package missing")
    return row, package


def local_vector_map_pbf(world_root: Path) -> Path:
    """Resolve the cached regional PBF for this world's vector_map provider.

    Prefers ``providers/.../provider.json`` ``pbf_filename`` (scaffolded
    Geofabrik providers) via :func:`vector_map_clips.default_pbf_path`. Falls
    back to a legacy module ``DEFAULT_PBF`` or ``PROVIDER``/``CONFIG``
    ``pbf_filename`` when those still exist.

    @param world_root: active world checkout root
    @returns: absolute path under ``<world>/downloads/``
    @raises RuntimeError: when the provider cannot name a PBF basename
    """
    import vector_map_clips as vmc

    _row, package = _vector_map_provider_package()
    meta = package / 'provider.json'
    if meta.is_file():
        filename = json.loads(meta.read_text()).get('pbf_filename')
        if not filename:
            raise RuntimeError(f'{meta} has no pbf_filename')
        return vmc.default_pbf_path(str(filename), world_root)

    import osm_extract

    default = getattr(osm_extract, 'DEFAULT_PBF', None)
    if default is not None:
        return Path(default)
    for attr in ('PROVIDER', 'CONFIG'):
        cfg = getattr(osm_extract, attr, None)
        if isinstance(cfg, dict) and cfg.get('pbf_filename'):
            return vmc.default_pbf_path(str(cfg['pbf_filename']), world_root)
    raise RuntimeError(
        f'vector_map provider package {package} has neither provider.json '
        'nor DEFAULT_PBF/PROVIDER pbf_filename'
    )


class LocalPreparation:
    """Existing provider repair operations, limited to this world's cached inputs."""

    def __init__(self, root):
        self.root = Path(root)

    def preflight(self, cell, needs_extract):
        """Read-only local extraction dependencies/costs; absent downloads remain blockers."""
        if not needs_extract:
            return {'blockers': [], 'inputs': []}
        try:
            pbf = local_vector_map_pbf(self.root)
            cached = pbf.is_relative_to(self.root) and pbf.is_file()
            blockers = [] if cached else [f'local PBF required: {pbf}; acquire explicitly before preparation']
            if shutil.which('ogr2ogr') is None:
                blockers.append('local map extraction requires ogr2ogr (GDAL)')
            return {'blockers': blockers, 'inputs': [{'filename': str(pbf), 'cached': cached,
                                                     'bytes': pbf.stat().st_size if cached else None}]}
        except (OSError, ValueError, ImportError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
            return {'blockers': [f'map preparation dependency: {exc}'], 'inputs': []}

    def extract(self, cell):
        """Complete this cell's query from a local PBF, with the full landscape preset."""
        import osm_extract

        cell = tuple(cell)
        pbf = local_vector_map_pbf(self.root)
        if not pbf.is_relative_to(self.root) or not pbf.is_file():
            raise FileNotFoundError(f'local PBF required: {pbf}; acquire explicitly before preparation')
        if shutil.which('ogr2ogr') is None:
            raise FileNotFoundError('local map extraction requires ogr2ogr (GDAL)')
        x0, z0, _x1, _z1 = cellmath.bounds(cell)
        argv = [
            '--at', f'{x0},{z0}', '--cell',
            '--pbf', str(pbf),
            '--place', prepared_place_slug(cell),
        ]
        with contextlib.redirect_stdout(sys.stderr):
            rc = osm_extract.main(argv)
        if rc:
            raise RuntimeError(f'local map extraction failed: exit {rc}')

    def ghost(self, cell):
        """Apply only the cold focus cell, using cached lidar and existing ownership guards."""
        import lidar_ghost
        import site_kit
        reg, writer = site_kit.live_session(root=self.root, ghosts=lidar_ghost.ghost_planner(), ring=0)
        with contextlib.redirect_stdout(sys.stderr):
            lidar_ghost.ensure_cells(reg, [cell], apply=writer)


def prepare_cell(reg, evidence, cell, *, apply=False, repairs=None, check_lock=None, evidence_only=False):
    """Report or perform idempotent local preparation; re-read actual results after every step.

    ``evidence_only`` prepares a cross-cell feature's neighbour (task 292): the same coverage and
    terrain repairs, then a ``readiness_evidence`` note; it records no feature plans and validates
    none, so the cell's own builds still need a full prepare.

    Tests inject synthetic provider evidence/repairs and a lock assertion. Production
    callers use the existing site lock; the CLI claims/releases it around this call.
    """
    from cell_registry import record_feature_candidates
    cell = tuple(cell)
    policy = ReadinessPolicy(reg, evidence)
    repairs = repairs or LocalPreparation(reg.root)

    def report():
        result = policy.check(cell, require_validation=False, neighbour=evidence_only)
        result['prepare_actions'] = []
        if result.get('clip_coverage', 1) < 1 and not reg.clip_coverage_dismissed(cell):
            result['prepare_actions'].append('extract full cell from local PBF (no download)')
        if result.get('candidates'):
            result['prepare_actions'].append('record missing feature plans with existing stable IDs')
        if reg.cell(cell).ground == 'none':
            result['prepare_actions'].append('apply cached-lidar focus ghost only (about 25–55 s)')
        preflight = repairs.preflight(cell, result.get('clip_coverage', 1) < 1
                                      and not reg.clip_coverage_dismissed(cell))
        result['preparation_inputs'] = preflight['inputs']
        result['preflight_blockers'] = preflight['blockers']
        result['blockers'] += preflight['blockers']
        result['ready'] = result['ready'] and not preflight['blockers']
        return result

    result = report()
    if not apply:
        return result
    if check_lock is None:
        import site_lock
        check_lock = site_lock.check
    check_lock()
    # Fail cheaply on missing downloads, holds, recovery or provider errors. A missing
    # map tree may be repaired by the local extractor, so its candidate error can wait.
    if ('clip_coverage' not in result or result.get('lidar_missing') or result['preflight_blockers']
            or result.get('aerial_coverage', 0) < 1 or reg.cell(cell).hold or reg.cell(cell).in_flight):
        return result

    def refresh():
        method = getattr(evidence, 'refresh', None)
        if method is not None:
            method()

    try:
        if result['clip_coverage'] < 1 and not reg.clip_coverage_dismissed(cell):
            repairs.extract(cell)
            refresh()
            result = report()
            if result.get('clip_coverage', 0) < 1:
                return result
        if not evidence_only:
            candidates = evidence.candidates(reg, cell)
            record_feature_candidates(reg, f'{cell[0]},{cell[1]}', candidates)
            refresh()
            result = report()
        if any(b != 'terrain: focus ghost baseline required' for b in result['blockers']):
            return result
        if reg.cell(cell).ground == 'none':
            check_lock()
            repairs.ghost(cell)
            refresh()
        result = report()
        if result['ready']:
            result = policy.validate_evidence(cell) if evidence_only else policy.validate(cell)
            result['prepare_actions'] = []
        return result
    except (OSError, ValueError, LookupError, RuntimeError, ImportError) as exc:
        result = report()
        result['blockers'].append(f'preparation failed: {exc}')
        result['ready'] = result['complete'] = False
        return result
