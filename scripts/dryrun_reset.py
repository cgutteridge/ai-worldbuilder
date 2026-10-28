#!/usr/bin/env python3
"""Reset the active world for another empty-world dry run (spec: lidar ghost v2).

Wipes agent place memory (the Minecraft save is never touched; it must already be blank) while keeping world
frame, providers, and reference downloads. Erases the registry directory (the cell
journal and its plan blobs, ADR 004); the first journal write recreates it, so a
reset world starts with a fresh journal and nothing is migrated or restored.

The save lives in the standalone Minecraft instance and this tool never deletes
inside it: delete and recreate the world in the game first (a live reset refuses
while region files remain), then raise the origin cell (ghost frontier).

  python3 scripts/dryrun_reset.py --dry-run
  python3 scripts/dryrun_reset.py --confirm-logged-out
  python3 scripts/dryrun_reset.py --archive-site --confirm-logged-out
  python3 scripts/dryrun_reset.py --force   # alias for --confirm-logged-out (dangerous)

Policy: docs/specs/lidar-ghost-v2-and-dryrun-reset.md
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from script_env import ROOT, TOOL_ROOT, add_world_arg, activate_world_from_args  # noqa: E402

SITE = ROOT / 'site'
REGISTRY = ROOT / 'registry'
TEMPLATE = TOOL_ROOT / 'templates' / 'new-world-site'
LOCK = SITE / 'LOCK.json'
HOLDS = SITE / 'CELL-HOLDS.jsonl'
VERIFICATION = ROOT / 'verification' / 'site'

# Legacy progress files removed by reset (ticket #33). Must not be re-scaffolded.
LEGACY_PROGRESS = (
    'QUEUE.txt',
    'CURRENT-CELL.json',
    'CELL-HOLDS.jsonl',
    'COVERAGE-SIGNOFFS.jsonl',
    'AERIAL-REVIEW.txt',
    '.handoff-pending.json',
    'HANDOFF.txt',
)

SKIP_TEMPLATE_NAMES = frozenset(LEGACY_PROGRESS) | frozenset({
    'QUEUE.txt',
    'CURRENT-CELL.json',
    'HANDOFF.txt',
})

PRESERVE_TOP = frozenset({
    'config',
    'providers',
    'reference',
    'instance',
    'backups',
    'verification',  # directory kept; contents under verification/site cleared
})


def load_region_dir() -> Path:
    """Overworld region folder from active world config.

    @returns: path to ``.../dimensions/minecraft/overworld/region``
    """
    from active_world import region_path

    try:
        return region_path()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc


def lock_held() -> dict | None:
    """Fresh site lock dict, or None.

    @returns: lock payload when held and not stale
    """
    import site_lock

    if not LOCK.is_file():
        return None
    try:
        lk = json.loads(LOCK.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    return lk if site_lock.fresh(lk) else None


def mcp_world_reachable() -> bool:
    """Whether the Minecraft MCP bridge answers (world likely open).

    @returns: True when the bridge responds
    """
    import mcp_probe

    return mcp_probe.mcp_world_reachable()


def list_region_files(region_dir: Path) -> list[Path]:
    """All ``.mca`` region files under the overworld region dir.

    @param region_dir: live region directory
    @returns: sorted paths
    """
    if not region_dir.is_dir():
        return []
    return sorted(p for p in region_dir.glob('*.mca') if p.is_file())


def site_wipe_paths() -> dict[str, list[str]]:
    """Paths removed or replaced on reset (repo-relative strings).

    @returns: ``{'replaced': [...], 'removed': [...], 'cleared_dirs': [...]}``
    """
    replaced = []
    for p in TEMPLATE.rglob('*'):
        if p.is_file():
            rel = p.relative_to(TEMPLATE)
            if rel.name in SKIP_TEMPLATE_NAMES:
                continue
            replaced.append(str(SITE.relative_to(ROOT) / rel))
    removed = [
        str(LOCK.relative_to(ROOT)) if LOCK.is_relative_to(ROOT) else str(LOCK),
        str(HOLDS.relative_to(ROOT)) if HOLDS.is_relative_to(ROOT) else str(HOLDS),
        'site/GEOCRAFT.txt',
        'site/builds',
    ]
    for name in LEGACY_PROGRESS:
        removed.append(f'site/{name}')
    cleared = [
        str((SITE / 'cells').relative_to(ROOT)),
        str(VERIFICATION.relative_to(ROOT)) if VERIFICATION.is_relative_to(ROOT) else str(VERIFICATION),
        str(REGISTRY.relative_to(ROOT)) if REGISTRY.is_relative_to(ROOT) else 'registry',
    ]
    return {'replaced': sorted(set(replaced)), 'removed': sorted(set(removed)), 'cleared_dirs': cleared}


def preserved_paths(region_dir: Path) -> list[str]:
    """Human-readable preserved roots for dry-run output.

    @param region_dir: overworld region directory
    @returns: sorted path strings
    """
    out = [
        str((ROOT / 'config').relative_to(ROOT)),
        str((ROOT / 'providers').relative_to(ROOT)),
        str((ROOT / 'reference').relative_to(ROOT)),
    ]
    cache_roots = []
    for rel in ('providers', 'reference'):
        for p in (ROOT / rel).rglob('*'):
            if p.is_dir() and p.name in ('cache', 'tiles', 'download'):
                cache_roots.append(str(p.relative_to(ROOT)))
    out.extend(sorted(set(cache_roots)))
    return sorted(set(out))


def scaffold_site() -> None:
    """Copy new-world site template over place memory (no legacy progress files)."""
    if not TEMPLATE.is_dir():
        raise SystemExit(f'missing template: {TEMPLATE}')
    SITE.mkdir(parents=True, exist_ok=True)
    for src in TEMPLATE.rglob('*'):
        if not src.is_file():
            continue
        rel = src.relative_to(TEMPLATE)
        if rel.name in SKIP_TEMPLATE_NAMES:
            continue
        dst = SITE / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def clear_place_artifacts() -> None:
    """Remove builds, cells, verification archives, lock, holds, legacy progress and the whole registry dir.

    ``registry/`` holds ``journal.jsonl`` and ``blobs/``; removing it is the erase half of
    erase-and-recreate (ADR 004). Nothing scaffolds it back.
    """
    LOCK.unlink(missing_ok=True)
    HOLDS.unlink(missing_ok=True)
    geo = SITE / 'GEOCRAFT.txt'
    geo.unlink(missing_ok=True)
    for name in LEGACY_PROGRESS:
        (SITE / name).unlink(missing_ok=True)
    builds = SITE / 'builds'
    if builds.is_dir():
        shutil.rmtree(builds)
    cells = SITE / 'cells'
    if cells.is_dir():
        shutil.rmtree(cells)
    cells.mkdir(parents=True, exist_ok=True)
    (cells / '.gitkeep').touch()
    if VERIFICATION.is_dir():
        shutil.rmtree(VERIFICATION)
    VERIFICATION.mkdir(parents=True, exist_ok=True)
    if REGISTRY.is_dir():
        shutil.rmtree(REGISTRY)


def _journal_entry_count() -> int:
    """Lines in ``registry/journal.jsonl`` (0 when the registry dir or journal is absent)."""
    journal = REGISTRY / 'journal.jsonl'
    if not journal.is_file():
        return 0
    return sum(1 for line in journal.read_text(encoding='utf-8').splitlines() if line.strip())


def require_blank_world(region_dir: Path) -> None:
    """Refuse a live reset while the save still holds chunks.

    The save belongs to the standalone Minecraft instance and this tool never
    deletes inside it. The owner makes the world blank (delete and recreate it in
    the game, or create a fresh one with ``scripts/new_blank_world.py``).

    @param region_dir: overworld region directory
    @raises SystemExit: when ``.mca`` files are still present
    """
    present = list_region_files(region_dir)
    if present:
        raise SystemExit(
            f'{len(present)} region file(s) still in {region_dir}. This tool does not '
            'delete inside the Minecraft save: delete and recreate the world in the '
            'game (or run scripts/new_blank_world.py), then re-run.')


def check_preconditions(*, force: bool = False, confirm_logged_out: bool = False) -> list[str]:
    """Refuse live reset when unsafe (ticket #32 logout gate).

    ``--force`` is accepted as an alias for ``--confirm-logged-out`` but still
    cannot bypass ``session.lock`` or a live MCP bridge.

    @param force: legacy alias for confirm_logged_out
    @param confirm_logged_out: operator confirmed the world is closed (title screen)
    @returns: warning lines (empty when ok)
    @raises SystemExit: when refused
    """
    import logout_gate

    confirmed = bool(confirm_logged_out or force)
    try:
        logout_gate.require_logged_out(confirm_logged_out=confirmed)
    except logout_gate.LogoutGateError as exc:
        raise SystemExit(str(exc)) from exc
    warnings: list[str] = []
    lk = lock_held()
    if lk and not confirmed:
        raise SystemExit(
            f'site lock held by {lk.get("owner")} for {lk.get("task")!r}; '
            'release first or pass --confirm-logged-out after leaving the world (title screen).')
    if lk and confirmed:
        warnings.append(
            f'warning: lock still held by {lk.get("owner")} ({lk.get("task")!r}) — release after reset')
    return warnings


def run_reset(*, dry_run: bool, archive_site: bool, force: bool = False,
              confirm_logged_out: bool = False) -> dict:
    """Execute or preview dry-run reset.

    @param dry_run: print plan only
    @param archive_site: move existing site tree to backups/<stamp>-dryrun-site first
    @param force: override lock / MCP refusal
    @param confirm_logged_out: operator confirmed the world is closed (title screen)
    @returns: report dict
    """
    region_dir = load_region_dir()
    regions = list_region_files(region_dir)
    wipe = site_wipe_paths()
    report: dict = {
        'dry_run': dry_run,
        'world_root': str(ROOT),
        'region_dir': str(region_dir),
        'regions_present': [p.name for p in regions],
        'preserved': preserved_paths(region_dir),
        'wipe': wipe,
        'archive_site': archive_site,
        'warnings': [],
        'next_steps': [
            'Live reset needs a blank save: delete and recreate the world in the game first (this tool never deletes inside saves/).',
            'Raise the origin cell (cell_marker.py --raise <origin>) to seed the ghost frontier.',
            'python3 scripts/cell_registry.py verify  # expect no issues; registry/ appears with the first journal write',
        ],
    }
    if dry_run:
        return report
    report['warnings'] = check_preconditions(force=force, confirm_logged_out=confirm_logged_out)
    require_blank_world(region_dir)
    stamp = time.strftime('%Y-%m-%d-%H%M%S')
    if archive_site and SITE.is_dir():
        dest = ROOT / 'backups' / f'{stamp}-dryrun-site'
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(SITE), str(dest))
        report['archived_site'] = str(dest.relative_to(ROOT))
        SITE.mkdir(parents=True, exist_ok=True)
    clear_place_artifacts()
    scaffold_site()
    report['registry'] = {
        'dir_present': REGISTRY.exists(),
        'journal_entries': _journal_entry_count(),
        'note': 'registry/ is created by the first journal write (registry/journal.jsonl, ADR 004)',
    }
    try:
        import registry_verify as rv
        report['verify'] = rv.run_verify(root=ROOT, check_world=False)
    except Exception as exc:  # noqa: BLE001
        report['verify_error'] = str(exc)
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI entry.

    @param argv: optional argument vector (tests)
    @returns: process exit code
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dry-run', action='store_true', help='print preserved vs wiped paths; no writes')
    ap.add_argument('--archive-site', action='store_true',
                    help='move the current site/ tree to backups/<timestamp>-dryrun-site before scaffold')
    ap.add_argument('--confirm-logged-out', action='store_true',
                    help='required for live reset: confirm you have left the world (title screen; the game can stay open)')
    ap.add_argument('--force', action='store_true',
                    help='legacy alias for --confirm-logged-out (still refuses if session.lock/MCP up)')
    add_world_arg(ap)
    args = ap.parse_args(argv)
    activate_world_from_args(args)
    report = run_reset(
        dry_run=args.dry_run,
        archive_site=args.archive_site,
        force=args.force,
        confirm_logged_out=args.confirm_logged_out,
    )
    print(json.dumps(report, indent=2))
    if not args.dry_run:
        for step in report.get('next_steps') or []:
            print(f'\nNext: {step}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
