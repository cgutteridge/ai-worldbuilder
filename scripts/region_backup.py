#!/usr/bin/env python3
"""Sparse region-file backups for Southampton Refine.

Copies only .mca files that intersect a Minecraft XZ bbox (plus optional
margin in regions). Does NOT snapshot the whole 1.6G world.

  # before a build at SO17 3SF-ish
  python3 scripts/region_backup.py snap --bbox 6900,-5400,7000,-5300 --name so173sf-q003
  python3 scripts/region_backup.py list
  python3 scripts/region_backup.py restore 2026-10-18-172524-bakeoff-ghost-3x3
  python3 scripts/region_backup.py prune --keep 5

``restore`` copies snap ``.mca`` files back into the live ``region_path``.
Quit the Minecraft world first (title screen or exit). If the MCP bridge
still answers, restore refuses unless ``--force``.

Policy: rules/process.md § backups.
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
BACKUP_ROOT = ROOT / 'backups' / 'regions'


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


def cmd_snap(args: argparse.Namespace) -> int:
    """Snapshot regions for a bbox.

    @param args: CLI args
    @returns: exit code
    """
    x0, z0, x1, z1 = map(int, args.bbox.split(','))
    cells = regions_for_bbox(x0, z0, x1, z1, margin=args.margin)
    stamp = time.strftime('%Y-%m-%d-%H%M%S')
    name = args.name or 'snap'
    dest = BACKUP_ROOT / f'{stamp}-{name}'
    dest.mkdir(parents=True, exist_ok=False)
    meta = {
        'time': stamp,
        'name': name,
        'bbox': [x0, z0, x1, z1],
        'margin': args.margin,
        'region_path': str(REGION),
        'files': [],
    }
    missing = []
    total = 0
    for rx, rz in cells:
        src = REGION / f'r.{rx}.{rz}.mca'
        if not src.exists():
            missing.append(src.name)
            continue
        shutil.copy2(src, dest / src.name)
        total += src.stat().st_size
        meta['files'].append(src.name)
    meta['bytes'] = total
    meta['missing'] = missing
    (dest / 'BACKUP.json').write_text(json.dumps(meta, indent=2) + '\n')
    print(f'{dest.relative_to(ROOT)}: {len(meta["files"])} regions, '
          f'{total / 1e6:.1f} MB' + (f', missing {missing}' if missing else ''))
    return 0


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


def cmd_restore(args: argparse.Namespace) -> int:
    """Copy a snap's ``.mca`` files back into the live region folder.

    Quit Minecraft (or at least leave the save) before restoring. Swapping
    ``.mca`` files while the world is open causes chunk-load failures and
    stale client geometry.

    @param args: CLI args (``snap``, ``force``)
    @returns: exit code (0 ok, 2 refused / verify fail)
    """
    try:
        snap_dir = resolve_snap_dir(args.snap)
    except (FileNotFoundError, ValueError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    meta_p = snap_dir / 'BACKUP.json'
    if not meta_p.is_file():
        print(f'error: {snap_dir.name} has no BACKUP.json', file=sys.stderr)
        return 2
    meta = json.loads(meta_p.read_text(encoding='utf-8'))
    files = list(meta.get('files') or [])
    if not files:
        print(f'error: {snap_dir.name} lists no files', file=sys.stderr)
        return 2
    region = Path(meta.get('region_path') or REGION)
    if not region.is_dir():
        print(f'error: region path missing: {region}', file=sys.stderr)
        return 2
    if mcp_world_reachable() and not args.force:
        print(
            'error: Minecraft MCP bridge is reachable (world likely open). '
            'Quit to title / close the save, then restore again. '
            'Override only if you know the region files are idle: --force',
            file=sys.stderr,
        )
        return 2
    restored = []
    for name in files:
        src = snap_dir / name
        if not src.is_file():
            print(f'error: missing {src}', file=sys.stderr)
            return 2
        dst = region / name
        shutil.copy2(src, dst)
        restored.append(name)
    bad = []
    for name in restored:
        src_h = sha256_file(snap_dir / name)
        dst_h = sha256_file(region / name)
        if src_h != dst_h:
            bad.append(name)
    if bad:
        print(f'error: checksum mismatch after copy: {bad}', file=sys.stderr)
        return 2
    print(f'restored {snap_dir.name}: {len(restored)} regions → {region}')
    if args.force and mcp_world_reachable():
        print('warning: --force used while MCP was reachable; reload the world before trusting chunks')
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry.

    @param argv: optional argv
    @returns: exit code
    """
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('snap', help='copy regions intersecting bbox')
    s.add_argument('--bbox', required=True, help='x0,z0,x1,z1')
    s.add_argument('--name', default='', help='label suffix')
    s.add_argument('--margin', type=int, default=0, help='extra regions on each side')
    s.set_defaults(func=cmd_snap)
    l = sub.add_parser('list', help='list snapshots')
    l.set_defaults(func=cmd_list)
    r = sub.add_parser('restore', help='copy a snap back into the live region folder')
    r.add_argument('snap', help='snap directory name (or unique suffix)')
    r.add_argument(
        '--force',
        action='store_true',
        help='restore even if the MCP bridge is reachable (dangerous)',
    )
    r.set_defaults(func=cmd_restore)
    pr = sub.add_parser('prune', help='drop old snapshots')
    pr.add_argument('--keep', type=int, default=5)
    pr.set_defaults(func=cmd_prune)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    raise SystemExit(main())
