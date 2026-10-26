"""Registry verify: report-only integrity check over the cell journal (ticket #32, ADR 004).

Exit non-zero when any error-severity finding is present. World block presence
checks are optional (``check_world=True``) and skipped in unit tests. Files of the
retired record (STAGES, ``registry/cells.json`` / ``features.json``, ``*-cells.json``)
are reported as leftovers: nothing reads them.

  python3 scripts/cell_registry.py verify
  python3 scripts/cell_registry.py verify --json
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cell_journal import CellRegistry
from cell_registry import cell_id, feature_cells, view_registry
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
    retired = '(retired record, ADR 004: ignored; the cell journal registry/journal.jsonl is the only record)'
    cells = site / 'cells'
    if cells.is_dir():
        stages = list(cells.glob('*-STAGES.json'))
        if stages:
            out.append(_issue(
                'leftover:STAGES', SEVERITY_WARN,
                f'{len(stages)} per-cell STAGES.json file(s) under site/cells {retired}',
            ))
    for name in ('cells.json', 'features.json'):
        if (root / 'registry' / name).exists():
            out.append(_issue(f'leftover:registry/{name}', SEVERITY_WARN, f'registry/{name} {retired}'))
    owned = list((root / 'verification' / 'site').glob('*-cells.json'))
    if owned:
        out.append(_issue(
            'leftover:cells-json', SEVERITY_WARN,
            f'{len(owned)} verification/site/*-cells.json ownership file(s) {retired}',
        ))
    return out


def check_feature_specs(reg: CellRegistry, root: Path) -> list[dict[str, Any]]:
    """Features in the journal should have ``site/builds/<id>.json``.

    @param reg: registry
    @param root: world root
    @returns: issues
    """
    builds = root / 'site' / 'builds'
    out: list[dict[str, Any]] = []
    for fid, feat in feature_cells(reg).items():
        path = builds / f'{fid}.json'
        if not path.is_file():
            sev = SEVERITY_ERROR if feat.get('status') == 'built' else SEVERITY_WARN
            out.append(_issue(
                f'missing-spec:{fid}', sev,
                f'feature {fid} (status={feat.get("status")}) has no site/builds/{fid}.json',
            ))
    return out


def check_marker_invariant(reg: CellRegistry) -> list[dict[str, Any]]:
    """At most one cell may have marker=up.

    @param reg: registry
    @returns: issues
    """
    ups = [cell_id(*c) for c, v in reg.view().items() if v.marker]
    if len(ups) > 1:
        return [_issue(
            'marker:multiple', SEVERITY_ERROR,
            f'multiple marker=up cells: {", ".join(sorted(ups))}',
        )]
    return []


def check_done_invariants(reg: CellRegistry) -> list[dict[str, Any]]:
    """Cells logically inconsistent (trees without generated ground, etc.).

    @param reg: registry
    @returns: issues
    """
    out: list[dict[str, Any]] = []
    for c, v in reg.view().items():
        cid = cell_id(*c)
        if v.trees and v.ground != 'generated':
            out.append(_issue(
                f'invariant:trees:{cid}', SEVERITY_ERROR,
                f'cell {cid} has trees=true but ground={v.ground!r}',
            ))
        if v.decor and v.ground != 'generated':
            out.append(_issue(
                f'invariant:decor:{cid}', SEVERITY_ERROR,
                f'cell {cid} has decor=true but ground={v.ground!r}',
            ))
    return out


def check_coverage_warnings(reg: CellRegistry) -> list[dict[str, Any]]:
    """Open coverage warnings that block DONE.

    @param reg: registry
    @returns: issues
    """
    out: list[dict[str, Any]] = []
    for c, v in reg.view().items():
        cid = cell_id(*c)
        for n in v.notes:
            if n.kind == 'coverage':
                out.append(_issue(
                    f'coverage:{cid}:{n.ref}', SEVERITY_WARN,
                    f'open coverage warning on {cid}: {n.ref} — {n.body}',
                ))
    return out


def check_in_flight(reg: CellRegistry) -> list[dict[str, Any]]:
    """Journal entries still ``begun`` (an aborted apply; site_recover.py resolves them).

    @param reg: registry
    @returns: issues
    """
    return [_issue(f'in-flight:{e.seq}', SEVERITY_WARN,
                   f'entry {e.seq} {e.plan} ({e.pass_}, cell {cell_id(*e.cell)}, owner {e.owner}) is still begun '
                   '(python scripts/site_recover.py status)')
            for e in reg.entries() if e.status == 'begun']


def check_issue_notes(reg: CellRegistry, root: Path) -> list[dict[str, Any]]:
    """Open issue notes in the journal whose ISSUES.txt line is gone (closed by hand).

    @param reg: registry
    @param root: world root
    @returns: issues
    """
    from registry_issues import issue_ref, load_issues
    open_refs = {issue_ref(i) for i in load_issues(root)}
    out = []
    for c, v in reg.view().items():
        for n in v.notes:
            if n.kind == 'issue' and n.ref not in open_refs:
                cid = cell_id(*c)
                out.append(_issue(
                    f'issue-note:{cid}:{n.ref}', SEVERITY_WARN,
                    f'cell {cid} still has open issue note {n.ref} but ISSUES.txt does not list it; close it with '
                    f'cell_registry.py note {cid} --kind close --ref {n.ref}',
                ))
    return out


def check_decision_records(reg: CellRegistry, root: Path) -> list[dict[str, Any]]:
    """Warn for landed builds with no decision record (recording starts with the first record).

    @param reg: registry view
    @param root: world root
    @returns: issues (warn only while the record format settles)
    """
    import decision_record as dr
    decisions = dr.load_decisions(root)
    evidence = dr.load_evidence(root)
    out = [_issue(f'decision-record:{plan}', SEVERITY_WARN, f'landed build {plan} has no decision record')
           for plan in dr.missing_records(reg.entries(), decisions)]
    for el, rec in decisions.items():
        for ref in {r for p in rec['phases'] for r in p['available']} - set(evidence):
            out.append(_issue(f'decision-record:{el}:{ref}', SEVERITY_ERROR, f'{el} cites evidence {ref} with no record'))
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
    reg = view_registry(world)
    issues: list[dict[str, Any]] = []
    issues += check_marker_invariant(reg)
    issues += check_done_invariants(reg)
    issues += check_feature_specs(reg, world)
    issues += check_coverage_warnings(reg)
    issues += check_in_flight(reg)
    issues += check_issue_notes(reg, world)
    issues += check_decision_records(reg, world)
    issues += check_leftover_progress_files(world)
    if check_world:
        issues += check_world_presence(reg, world)
    errors = sum(1 for i in issues if i['severity'] == SEVERITY_ERROR)
    warns = sum(1 for i in issues if i['severity'] == SEVERITY_WARN)
    return {
        'world_root': str(world),
        'issues': issues,
        'error_count': errors,
        'warn_count': warns,
        'ok': errors == 0,
    }


def check_world_presence(reg: CellRegistry, root: Path) -> list[dict[str, Any]]:
    """Optional live checks: region files for non-empty cells (no MCP block scan yet).

    @param reg: registry
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
    for cell, v in reg.view().items():
        if v.ground == 'none':
            continue
        cid = cell_id(*cell)
        for rx, rz in cellmath.regions_for_cell(cell):  # type: ignore[arg-type]
            name = f'r.{rx}.{rz}.mca'
            if not (region / name).is_file():
                out.append(_issue(
                    f'region:missing:{cid}:{name}', SEVERITY_WARN,
                    f'cell {cid} has ground={v.ground!r} but {name} is absent',
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
