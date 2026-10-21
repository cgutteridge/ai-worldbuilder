"""Registry verify: report-only integrity check (ticket #32).

Exit non-zero when any error-severity finding is present. World block presence
checks are optional (``check_world=True``) and skipped in unit tests.

  python3 scripts/cell_registry.py verify
  python3 scripts/cell_registry.py verify --json
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cell_registry import RegistryStore
from script_env import ROOT  # noqa: E402

SEVERITY_ERROR = 'error'
SEVERITY_WARN = 'warn'


def _issue(iid: str, severity: str, message: str, **extra: Any) -> dict[str, Any]:
    out = {'id': iid, 'severity': severity, 'message': message}
    out.update(extra)
    return out


def check_leftover_progress_files(root: Path) -> list[dict[str, Any]]:
    """Legacy progress files that should not drive work after the registry cut-over.

    @param root: world root
    @returns: issues
    """
    site = root / 'site'
    out: list[dict[str, Any]] = []
    # These block unsupervised runs until #33 wipe; treat as errors so verify fails.
    for name in (
        'QUEUE.txt',
        'CURRENT-CELL.json',
        'COVERAGE-SIGNOFFS.jsonl',
        'CELL-HOLDS.jsonl',
        'AERIAL-REVIEW.txt',
        '.handoff-pending.json',
    ):
        path = site / name
        if path.exists():
            out.append(_issue(
                f'leftover:{name}', SEVERITY_ERROR,
                f'legacy progress file still present: site/{name}',
            ))
    cells = site / 'cells'
    if cells.is_dir():
        stages = list(cells.glob('*-STAGES.json'))
        if stages:
            out.append(_issue(
                'leftover:STAGES', SEVERITY_WARN,
                f'{len(stages)} per-cell STAGES.json file(s) under site/cells '
                '(in-flight bookkeeping only until #33 wipe)',
            ))
    return out


def check_feature_specs(store: RegistryStore, root: Path) -> list[dict[str, Any]]:
    """Registered features should have ``site/builds/<id>.json``.

    @param store: registry
    @param root: world root
    @returns: issues
    """
    builds = root / 'site' / 'builds'
    out: list[dict[str, Any]] = []
    for fid, feat in store._load_features().items():
        path = builds / f'{fid}.json'
        if not path.is_file():
            sev = SEVERITY_ERROR if feat.get('status') == 'built' else SEVERITY_WARN
            out.append(_issue(
                f'missing-spec:{fid}', sev,
                f'feature {fid} (status={feat.get("status")}) has no site/builds/{fid}.json',
            ))
    return out


def check_marker_invariant(store: RegistryStore) -> list[dict[str, Any]]:
    """At most one cell may have marker=up.

    @param store: registry
    @returns: issues
    """
    ups = [cid for cid, rec in store._load_cells().items() if rec.get('marker') == 'up']
    if len(ups) > 1:
        return [_issue(
            'marker:multiple', SEVERITY_ERROR,
            f'multiple marker=up cells: {", ".join(sorted(ups))}',
        )]
    return []


def check_done_invariants(store: RegistryStore) -> list[dict[str, Any]]:
    """Cells marked logically inconsistent (trees without generated ground, etc.).

    @param store: registry
    @returns: issues
    """
    out: list[dict[str, Any]] = []
    for cid, rec in store._load_cells().items():
        if rec.get('trees') and rec.get('ground') != 'generated':
            out.append(_issue(
                f'invariant:trees:{cid}', SEVERITY_ERROR,
                f'cell {cid} has trees=true but ground={rec.get("ground")!r}',
            ))
        if rec.get('decor') and rec.get('ground') != 'generated':
            out.append(_issue(
                f'invariant:decor:{cid}', SEVERITY_ERROR,
                f'cell {cid} has decor=true but ground={rec.get("ground")!r}',
            ))
    return out


def check_coverage_warnings(store: RegistryStore) -> list[dict[str, Any]]:
    """Open coverage warnings that block DONE.

    @param store: registry
    @returns: issues
    """
    out: list[dict[str, Any]] = []
    for cid in store._load_cells():
        for w in store.open_coverage_warnings(cid):
            out.append(_issue(
                f'coverage:{cid}:{w.get("id")}', SEVERITY_WARN,
                f'open coverage warning on {cid}: {w.get("id")} — {w.get("detail") or w.get("reason")}',
            ))
    return out


def run_verify(
    *,
    root: Path | None = None,
    check_world: bool = False,
) -> dict[str, Any]:
    """Run all verify checks and return a report.

    @param root: world root
    @param check_world: also probe live region/block presence (needs Minecraft)
    @returns: report with ``issues``, ``error_count``, ``warn_count``
    """
    world = Path(root) if root else Path(ROOT)
    store = RegistryStore(world)
    issues: list[dict[str, Any]] = []
    issues += check_marker_invariant(store)
    issues += check_done_invariants(store)
    issues += check_feature_specs(store, world)
    issues += check_coverage_warnings(store)
    issues += check_leftover_progress_files(world)
    if check_world:
        issues += check_world_presence(store, world)
    errors = sum(1 for i in issues if i['severity'] == SEVERITY_ERROR)
    warns = sum(1 for i in issues if i['severity'] == SEVERITY_WARN)
    return {
        'world_root': str(world),
        'issues': issues,
        'error_count': errors,
        'warn_count': warns,
        'ok': errors == 0,
    }


def check_world_presence(store: RegistryStore, root: Path) -> list[dict[str, Any]]:
    """Optional live checks: region files for non-empty cells (no MCP block scan yet).

    @param store: registry
    @param root: world root
    @returns: issues
    """
    out: list[dict[str, Any]] = []
    try:
        from active_world import load_world_config
        cfg = load_world_config()
        region = Path(cfg.get('region_path') or '')
        if region and not region.is_absolute():
            region = root / region
    except Exception:
        return out
    if not region.is_dir():
        return [_issue('region:missing-dir', SEVERITY_ERROR, f'region path missing: {region}')]
    import cellmath
    for cid, rec in store._load_cells().items():
        if rec.get('ground') in (None, 'none'):
            continue
        cell = tuple(int(p) for p in cid.split(','))
        for rx, rz in cellmath.regions_for_cell(cell):  # type: ignore[arg-type]
            name = f'r.{rx}.{rz}.mca'
            if not (region / name).is_file():
                out.append(_issue(
                    f'region:missing:{cid}:{name}', SEVERITY_WARN,
                    f'cell {cid} has ground={rec.get("ground")!r} but {name} is absent',
                ))
    return out


def exit_code(report: dict[str, Any]) -> int:
    """Process exit code from a verify report.

    @param report: from :func:`run_verify`
    @returns: 0 when no errors (warnings ok), 1 otherwise
    """
    return 0 if report.get('error_count', 0) == 0 else 1


def format_report(report: dict[str, Any]) -> str:
    """Human-readable verify report.

    @param report: from :func:`run_verify`
    @returns: text
    """
    lines = [
        f"verify: {report['world_root']}",
        f"  errors={report['error_count']} warnings={report['warn_count']}",
    ]
    for issue in report.get('issues') or []:
        lines.append(f"  [{issue['severity']}] {issue['id']}: {issue['message']}")
    if not report.get('issues'):
        lines.append('  (clean)')
    return '\n'.join(lines)
