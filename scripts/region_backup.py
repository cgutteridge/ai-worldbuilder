#!/usr/bin/env python3
"""Sparse and whole-world region-file backups (ticket #32).

Copies ``.mca`` files that intersect a Minecraft XZ bbox, or every region for a
whole-world bake-off pair (regions + registry/).

  python3 scripts/region_backup.py snap --bbox 6900,-5400,7000,-5300 --name so173sf-q003
  python3 scripts/region_backup.py snap-world --name bakeoff-ghost --confirm-logged-out
  python3 scripts/region_backup.py list
  python3 scripts/region_backup.py restore 2026-10-18-172524-bakeoff-ghost --confirm-logged-out
  python3 scripts/region_backup.py prune --keep 5

Snap kinds in ``BACKUP.json``:
  - ``partial`` — bbox / repair slice (raw ``restore`` refuses these)
  - ``whole-world`` — full region dir + ``registry/`` copy (bake-off)
  - ``repair`` — cell repair backup (restore only via ``registry_repair undo``)

Policy: rules/process.md § backups; ticket #32 logout gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

from script_env import ROOT  # noqa: E402
CFG = json.loads((ROOT / 'config/world.json').read_text())
REGION = Path(CFG['region_path'])
if not REGION.is_absolute():
    REGION = ROOT / REGION
BACKUP_ROOT = ROOT / 'backups' / 'regions'
KIND_PARTIAL = 'partial'
KIND_WHOLE = 'whole-world'
KIND_REPAIR = 'repair'


def regions_for_bbox(x0: int, z0: int, x1: int, z1: int, margin: int = 0) -> list[tuple[int, int]]:
    """Region indices (rx, rz) covering an inclusive XZ bbox.

    Minecraft region = 512×512 blocks (32 chunks).

    @param x0: min X
    @param z0: min Z
    @param x1: max X
    @param z1: max Z
    @param margin: extra regions on each side
    @returns: sorted unique (rx, rz) pairs
    """
    if x0 > x1:
        x0, x1 = x1, x0
    if z0 > z1:
        z0, z1 = z1, z0
    rx0 = (x0 >> 9) - margin
    rx1 = (x1 >> 9) + margin
    rz0 = (z0 >> 9) - margin
    rz1 = (z1 >> 9) + margin
    return [(rx, rz) for rx in range(rx0, rx1 + 1) for rz in range(rz0, rz1 + 1)]


def _copy_regions(dest: Path, region_dir: Path, names: list[str] | None = None) -> tuple[list[str], list[str], int]:
    """Copy ``.mca`` files into ``dest``.

    @param dest: snap directory
    @param region_dir: live region folder
    @param names: optional explicit file names; default all ``*.mca``
    @returns: (copied names, missing names, total bytes)
    """
    if names is None:
        names = sorted(p.name for p in region_dir.glob('*.mca') if p.is_file())
    copied: list[str] = []
    missing: list[str] = []
    total = 0
    for name in names:
        src = region_dir / name
        if not src.is_file():
            missing.append(name)
            continue
        shutil.copy2(src, dest / name)
        total += src.stat().st_size
        copied.append(name)
    return copied, missing, total


def write_backup_meta(dest: Path, meta: dict) -> None:
    """Write ``BACKUP.json`` with sorted keys.

    @param dest: snap directory
    @param meta: metadata
    """
    (dest / 'BACKUP.json').write_text(json.dumps(meta, indent=2, sort_keys=True) + '\n')


def cmd_snap(args: argparse.Namespace) -> int:
    """Snapshot regions for a bbox (kind=partial).

    @param args: CLI args
    @returns: exit code
    """
    x0, z0, x1, z1 = map(int, args.bbox.split(','))
    cells = regions_for_bbox(x0, z0, x1, z1, margin=args.margin)
    stamp = time.strftime('%Y-%m-%d-%H%M%S')
    name = args.name or 'snap'
    dest = BACKUP_ROOT / f'{stamp}-{name}'
    dest.mkdir(parents=True, exist_ok=False)
    names = [f'r.{rx}.{rz}.mca' for rx, rz in cells]
    copied, missing, total = _copy_regions(dest, REGION, names)
    meta = {
        'time': stamp,
        'name': name,
        'kind': KIND_PARTIAL,
        'bbox': [x0, z0, x1, z1],
        'margin': args.margin,
        'region_path': str(REGION),
        'files': copied,
        'bytes': total,
        'missing': missing,
    }
    write_backup_meta(dest, meta)
    print(f'{dest.relative_to(ROOT)}: {len(copied)} regions, '
          f'{total / 1e6:.1f} MB' + (f', missing {missing}' if missing else ''))
    return 0


def registry_dir(root: Path | None = None) -> Path:
    """Path to ``registry/`` under a world root.

    @param root: world root (default active)
    @returns: registry directory
    """
    return (Path(root) if root else Path(ROOT)) / 'registry'


def snap_whole_world(
    *,
    name: str = 'whole-world',
    region_dir: Path | None = None,
    world_root: Path | None = None,
    backup_root: Path | None = None,
    confirm_logged_out: bool = False,
) -> Path:
    """Snapshot every region file plus a copy of ``registry/`` (bake-off pair).

    @param name: label suffix
    @param region_dir: live region folder
    @param world_root: world root for registry
    @param backup_root: backups/regions override
    @param confirm_logged_out: logout gate flag
    @returns: snap directory
    @raises logout_gate.LogoutGateError: when the world is not idle
    """
    import logout_gate

    logout_gate.require_logged_out(confirm_logged_out=confirm_logged_out)
    region = Path(region_dir) if region_dir else REGION
    root = Path(world_root) if world_root else Path(ROOT)
    broot = Path(backup_root) if backup_root else BACKUP_ROOT
    stamp = time.strftime('%Y-%m-%d-%H%M%S')
    dest = broot / f'{stamp}-{name}'
    dest.mkdir(parents=True, exist_ok=False)
    copied, missing, total = _copy_regions(dest, region)
    reg_src = registry_dir(root)
    reg_dest = dest / 'registry'
    if reg_src.is_dir():
        shutil.copytree(reg_src, reg_dest)
    else:
        reg_dest.mkdir()
        (reg_dest / 'cells.json').write_text('{}\n')
        (reg_dest / 'features.json').write_text('{}\n')
    meta = {
        'time': stamp,
        'name': name,
        'kind': KIND_WHOLE,
        'region_path': str(region),
        'world_root': str(root),
        'files': copied,
        'bytes': total,
        'missing': missing,
        'registry': True,
    }
    write_backup_meta(dest, meta)
    return dest


def cmd_snap_world(args: argparse.Namespace) -> int:
    """CLI: whole-world + registry snap.

    @param args: CLI args
    @returns: exit code
    """
    try:
        dest = snap_whole_world(
            name=args.name or 'whole-world',
            confirm_logged_out=bool(args.confirm_logged_out),
        )
    except Exception as exc:  # noqa: BLE001 — LogoutGateError + IO
        print(f'error: {exc}', file=sys.stderr)
        return 2
    print(f'{dest.relative_to(ROOT)}: whole-world {len(json.loads((dest / "BACKUP.json").read_text())["files"])} regions + registry')
    return 0


def snap_kind(meta: dict) -> str:
    """Backup kind from meta (legacy snaps without kind are partial).

    @param meta: BACKUP.json body
    @returns: kind string
    """
    return str(meta.get('kind') or KIND_PARTIAL)


def cmd_list(_args: argparse.Namespace) -> int:
    """List region snapshots.

    @param _args: unused
    @returns: exit code
    """
    if not BACKUP_ROOT.exists():
        print('(no backups yet)')
        return 0
    for d in sorted(BACKUP_ROOT.iterdir()):
        if not d.is_dir():
            continue
        meta_p = d / 'BACKUP.json'
        if meta_p.exists():
            m = json.loads(meta_p.read_text())
            print(f"{d.name}: {len(m.get('files', []))} files, "
                  f"{m.get('bytes', 0) / 1e6:.1f} MB, bbox {m.get('bbox')}")
        else:
            print(d.name)
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    """Keep only the newest N snapshots (by directory name / stamp).

    @param args: CLI args
    @returns: exit code
    """
    if not BACKUP_ROOT.exists():
        return 0
    dirs = sorted([d for d in BACKUP_ROOT.iterdir() if d.is_dir()], reverse=True)
    keep = max(0, args.keep)
    for d in dirs[keep:]:
        shutil.rmtree(d)
        print('removed', d.name)
    print(f'kept {min(keep, len(dirs))} snapshots')
    return 0


def sha256_file(path: Path) -> str:
    """Hex digest of a file.

    @param path: file path
    @returns: sha256 hex
    """
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def resolve_snap_dir(snap: str) -> Path:
    """Resolve a snap directory by exact name or unique suffix.

    @param snap: directory name under backups/regions, or a unique trailing name
    @returns: snap directory
    @raises FileNotFoundError: no match
    @raises ValueError: ambiguous suffix
    """
    if not BACKUP_ROOT.is_dir():
        raise FileNotFoundError(f'no backups at {BACKUP_ROOT}')
    exact = BACKUP_ROOT / snap
    if exact.is_dir():
        return exact
    matches = [d for d in BACKUP_ROOT.iterdir() if d.is_dir() and d.name.endswith(snap)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(f'no snap matching {snap!r} under {BACKUP_ROOT}')
    names = ', '.join(sorted(d.name for d in matches))
    raise ValueError(f'ambiguous snap {snap!r}: {names}')


def mcp_world_reachable(timeout: float = 1.5) -> bool:
    """True when the world MCP bridge answers (world likely open).

    @param timeout: connect timeout seconds
    @returns: whether http://127.0.0.1:8765/mcp responds at all
    """
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        'http://127.0.0.1:8765/mcp',
        data=b'{}',
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(64)
        return True
    except urllib.error.HTTPError:
        # HTTP error still means something is listening on the bridge port.
        return True
    except OSError:
        return False


def restore_snap(
    snap_dir: Path,
    *,
    confirm_logged_out: bool = False,
    allow_partial: bool = False,
    restore_registry: bool = True,
    world_root: Path | None = None,
    skip_logout_gate: bool = False,
) -> list[str]:
    """Restore a snap's region files (and registry for whole-world).

    @param snap_dir: snap directory with BACKUP.json
    @param confirm_logged_out: logout gate flag
    @param allow_partial: permit kind=partial|repair (repair-undo only)
    @param restore_registry: also copy ``registry/`` when present (whole-world)
    @param world_root: world root for registry restore
    @param skip_logout_gate: tests only
    @returns: restored ``.mca`` names
    @raises ValueError: partial snap without allow_partial
    @raises logout_gate.LogoutGateError: world not idle
    @raises FileNotFoundError: missing files
    """
    meta_p = snap_dir / 'BACKUP.json'
    if not meta_p.is_file():
        raise FileNotFoundError(f'{snap_dir.name} has no BACKUP.json')
    meta = json.loads(meta_p.read_text(encoding='utf-8'))
    kind = snap_kind(meta)
    if kind == KIND_PARTIAL and not allow_partial:
        raise ValueError(
            f'{snap_dir.name} is a partial region snap (kind={kind}). '
            'Use cell_registry.py undo-repair <snap> for repair backups, '
            'or region_backup.py snap-world / restore for whole-world bake-off pairs.'
        )
    if kind == KIND_REPAIR and not allow_partial:
        raise ValueError(
            f'{snap_dir.name} is a repair snap. '
            'Restore it with: python3 scripts/cell_registry.py undo-repair '
            f'{snap_dir.name} --confirm-logged-out'
        )
    if not skip_logout_gate:
        import logout_gate
        logout_gate.require_logged_out(confirm_logged_out=confirm_logged_out)
    files = list(meta.get('files') or [])
    if not files:
        raise FileNotFoundError(f'{snap_dir.name} lists no files')
    region = Path(meta.get('region_path') or REGION)
    if not region.is_dir():
        raise FileNotFoundError(f'region path missing: {region}')
    restored = []
    for name in files:
        src = snap_dir / name
        if not src.is_file():
            raise FileNotFoundError(f'missing {src}')
        shutil.copy2(src, region / name)
        restored.append(name)
    bad = [n for n in restored if sha256_file(snap_dir / n) != sha256_file(region / n)]
    if bad:
        raise RuntimeError(f'checksum mismatch after copy: {bad}')
    if restore_registry and kind == KIND_WHOLE and (snap_dir / 'registry').is_dir():
        root = Path(world_root) if world_root else Path(meta.get('world_root') or ROOT)
        dest_reg = registry_dir(root)
        if dest_reg.exists():
            shutil.rmtree(dest_reg)
        shutil.copytree(snap_dir / 'registry', dest_reg)
    return restored


def cmd_restore(args: argparse.Namespace) -> int:
    """Copy a whole-world snap back into the live region folder (+ registry).

    Partial snaps are refused; use undo-repair. Logout gate required.

    @param args: CLI args
    @returns: exit code (0 ok, 2 refused / verify fail)
    """
    try:
        snap_dir = resolve_snap_dir(args.snap)
    except (FileNotFoundError, ValueError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    confirm = bool(getattr(args, 'confirm_logged_out', False) or getattr(args, 'force', False))
    try:
        restored = restore_snap(
            snap_dir,
            confirm_logged_out=confirm,
            allow_partial=False,
        )
    except Exception as exc:  # noqa: BLE001
        print(f'error: {exc}', file=sys.stderr)
        return 2
    print(f'restored {snap_dir.name}: {len(restored)} regions')
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry.

    @param argv: optional argv
    @returns: exit code
    """
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('snap', help='copy regions intersecting bbox (partial; not raw-restorable)')
    s.add_argument('--bbox', required=True, help='x0,z0,x1,z1')
    s.add_argument('--name', default='', help='label suffix')
    s.add_argument('--margin', type=int, default=0, help='extra regions on each side')
    s.set_defaults(func=cmd_snap)
    sw = sub.add_parser('snap-world', help='copy every region + registry (bake-off pair)')
    sw.add_argument('--name', default='whole-world', help='label suffix')
    sw.add_argument('--confirm-logged-out', action='store_true',
                    help='required: confirm the Minecraft save is closed')
    sw.set_defaults(func=cmd_snap_world)
    l = sub.add_parser('list', help='list snapshots')
    l.set_defaults(func=cmd_list)
    r = sub.add_parser('restore', help='restore a whole-world snap (refuses partial/repair)')
    r.add_argument('snap', help='snap directory name (or unique suffix)')
    r.add_argument('--confirm-logged-out', action='store_true',
                   help='required: confirm the Minecraft save is closed')
    r.add_argument(
        '--force',
        action='store_true',
        help=argparse.SUPPRESS,  # legacy alias for --confirm-logged-out (still not enough if lock/MCP up)
    )
    r.set_defaults(func=cmd_restore)
    pr = sub.add_parser('prune', help='drop old snapshots')
    pr.add_argument('--keep', type=int, default=5)
    pr.set_defaults(func=cmd_prune)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    raise SystemExit(main())
