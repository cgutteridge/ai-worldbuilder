#!/usr/bin/env python3
"""Reset the active world for another empty-world dry run (spec: lidar ghost v2).

Wipes Minecraft overworld region chunks and agent place memory while keeping world
frame, providers, reference downloads, and (by default) current cell focus.

Player must **leave the save** before a live reset; reopen afterward so chunks
regenerate empty.

  python3 scripts/dryrun_reset.py --dry-run
  python3 scripts/dryrun_reset.py
  python3 scripts/dryrun_reset.py --archive-site
  python3 scripts/dryrun_reset.py --force   # lock held or MCP up (dangerous)

Policy: docs/specs/lidar-ghost-v2-and-dryrun-reset.md
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from script_env import ROOT, TOOL_ROOT  # noqa: E402

SITE = ROOT / 'site'
TEMPLATE = TOOL_ROOT / 'templates' / 'new-world-site'
CURRENT_CELL = SITE / 'CURRENT-CELL.json'
LOCK = SITE / 'LOCK.json'
HOLDS = SITE / 'CELL-HOLDS.jsonl'
VERIFICATION = ROOT / 'verification' / 'site'
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
    from active_world import load_world_config

    cfg = load_world_config()
    raw = cfg.get('region_path')
    if not raw:
        raise SystemExit('config/world.json missing region_path')
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    return path


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
    import region_backup as rb

    return rb.mcp_world_reachable()


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
            if rel.name == 'CURRENT-CELL.json':
                continue
            replaced.append(str(SITE.relative_to(ROOT) / rel))
    removed = [
        str(LOCK.relative_to(ROOT)) if LOCK.is_relative_to(ROOT) else str(LOCK),
        str(HOLDS.relative_to(ROOT)) if HOLDS.is_relative_to(ROOT) else str(HOLDS),
        'site/GEOCRAFT.txt',
        'site/builds',
    ]
    cleared = [
        str((SITE / 'cells').relative_to(ROOT)),
        str(VERIFICATION.relative_to(ROOT)) if VERIFICATION.is_relative_to(ROOT) else str(VERIFICATION),
    ]
    return {'replaced': sorted(replaced), 'removed': removed, 'cleared_dirs': cleared}


def preserved_paths(region_dir: Path) -> list[str]:
    """Human-readable preserved roots for dry-run output.

    @param region_dir: overworld region directory (listed for delete, not preserve)
    @returns: sorted path strings
    """
    out = [
        str((ROOT / 'config').relative_to(ROOT)),
        str((ROOT / 'providers').relative_to(ROOT)),
        str((ROOT / 'reference').relative_to(ROOT)),
        str(CURRENT_CELL.relative_to(ROOT)) + ' (content kept)',
    ]
    cache_roots = []
    for name in ('ukdefra-lidar', 'defra', 'lidar', 'cache'):
        for base in (ROOT / 'providers', TOOL_ROOT / 'providers'):
            p = base
            if name in str(p):
                cache_roots.append(str(p.relative_to(ROOT) if p.is_relative_to(ROOT) else p))
    for rel in ('providers', 'reference'):
        for p in (ROOT / rel).rglob('*'):
            if p.is_dir() and p.name in ('cache', 'tiles', 'download'):
                cache_roots.append(str(p.relative_to(ROOT)))
    out.extend(sorted(set(cache_roots)))
    return sorted(set(out))


def read_current_cell() -> dict:
    """Parse ``site/CURRENT-CELL.json`` if present.

    @returns: JSON object (may be empty)
    """
    if not CURRENT_CELL.is_file():
        return {}
    try:
        return json.loads(CURRENT_CELL.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}


def scaffold_site(*, keep_current: dict) -> None:
    """Copy new-world site template over place memory, preserving focus.

    @param keep_current: ``CURRENT-CELL.json`` payload to restore after copy
    """
    if not TEMPLATE.is_dir():
        raise SystemExit(f'missing template: {TEMPLATE}')
    SITE.mkdir(parents=True, exist_ok=True)
    for src in TEMPLATE.rglob('*'):
        if not src.is_file():
            continue
        rel = src.relative_to(TEMPLATE)
        if rel.name == 'CURRENT-CELL.json':
            continue
        dst = SITE / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    from atomic_json import write_json

    write_json(CURRENT_CELL, keep_current if keep_current else {})


def clear_place_artifacts() -> None:
    """Remove builds, STAGES, verification archives, lock, and holds."""
    LOCK.unlink(missing_ok=True)
    HOLDS.unlink(missing_ok=True)
    geo = SITE / 'GEOCRAFT.txt'
    geo.unlink(missing_ok=True)
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


def delete_regions(region_dir: Path) -> list[str]:
    """Delete all ``.mca`` files in the overworld region folder.

    @param region_dir: live region directory
    @returns: deleted file names
    """
    deleted = []
    for p in list_region_files(region_dir):
        p.unlink()
        deleted.append(p.name)
    return deleted


def check_preconditions(*, force: bool) -> list[str]:
    """Refuse live reset when unsafe unless ``force``.

    @param force: allow lock / MCP override
    @returns: warning lines (empty when ok)
    @raises SystemExit: when refused
    """
    warnings: list[str] = []
    lk = lock_held()
    if lk and not force:
        raise SystemExit(
            f'site lock held by {lk.get("owner")} for {lk.get("task")!r}; '
            'release first or pass --force (dangerous).')
    if lk and force:
        warnings.append(
            f'warning: --force with lock held by {lk.get("owner")} ({lk.get("task")!r})')
    if mcp_world_reachable() and not force:
        raise SystemExit(
            'Minecraft MCP bridge is reachable (world likely open). '
            'Quit to the launcher, then reset again, or pass --force (dangerous).')
    if mcp_world_reachable() and force:
        warnings.append('warning: --force while MCP was reachable; re-open the save after reset')
    return warnings


def run_reset(*, dry_run: bool, archive_site: bool, force: bool) -> dict:
    """Execute or preview dry-run reset.

    @param dry_run: print plan only
    @param archive_site: move existing site tree to site/archive/<stamp> first
    @param force: override lock / MCP refusal
    @returns: report dict
    """
    region_dir = load_region_dir()
    regions = list_region_files(region_dir)
    keep = read_current_cell()
    wipe = site_wipe_paths()
    report: dict = {
        'dry_run': dry_run,
        'world_root': str(ROOT),
        'region_dir': str(region_dir),
        'regions_to_delete': [p.name for p in regions],
        'preserved': preserved_paths(region_dir),
        'wipe': wipe,
        'current_cell_kept': keep,
        'archive_site': archive_site,
        'warnings': [],
    }
    if dry_run:
        return report
    report['warnings'] = check_preconditions(force=force)
    stamp = time.strftime('%Y-%m-%d-%H%M%S')
    if archive_site and SITE.is_dir():
        dest = SITE / 'archive' / f'{stamp}-dryrun-site'
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(SITE), str(dest))
        report['archived_site'] = str(dest.relative_to(ROOT) if dest.is_relative_to(ROOT) else dest)
        SITE.mkdir(parents=True, exist_ok=True)
    clear_place_artifacts()
    scaffold_site(keep_current=keep)
    report['regions_deleted'] = delete_regions(region_dir)
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI entry.

    @param argv: optional argument vector (tests)
    @returns: process exit code
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dry-run', action='store_true', help='print preserved vs wiped paths; no writes')
    ap.add_argument('--archive-site', action='store_true',
                    help='move the current site/ tree to site/archive/<timestamp> before scaffold')
    ap.add_argument('--force', action='store_true',
                    help='reset even if site lock is held or MCP is up (dangerous)')
    args = ap.parse_args(argv)
    report = run_reset(dry_run=args.dry_run, archive_site=args.archive_site, force=args.force)
    print(json.dumps(report, indent=2))
    if not args.dry_run:
        print('\nRe-open the Minecraft save so overworld chunks regenerate empty near your focus cell.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
