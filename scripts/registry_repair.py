"""Per-cell repair: backup impacted regions + registry slice, wipe, replay (ticket #32).

  python3 scripts/cell_registry.py repair 48,-44 --confirm-logged-out
  python3 scripts/cell_registry.py undo-repair <snap-dir> --confirm-logged-out

Live wipe/replay need Minecraft; backup / undo / flag-clear / replay planning are
pure and unit-tested. Failed replay steps clear that step and all later flags so
``next`` picks the work up again.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, Callable

import cellmath
import region_backup as RB
from cell_registry import RegistryStore, cell_id, parse_cell_id
from script_env import ROOT  # noqa: E402

# Replay / clear order (later steps depend on earlier).
STEP_ORDER = ('ground', 'ghost-ground', 'ghost-features', 'features', 'trees', 'decor')


def _out_dir(world_root: Path) -> Path:
    return world_root / 'verification' / 'site'


def impacted_regions(
    cell: tuple[int, int],
    *,
    store: RegistryStore | None = None,
    world_root: Path | None = None,
) -> list[tuple[int, int]]:
    """Region indices touched by a cell and its registered feature footprints.

    Spills: feature ``*-cells.json`` under verification/site and pass masks when
    present. Always includes the cell's own regions.

    @param cell: (tx, tz)
    @param store: registry
    @param world_root: world root for masks / cells.json
    @returns: sorted unique (rx, rz)
    """
    root = Path(world_root) if world_root else Path(ROOT)
    reg = store or RegistryStore(root)
    cid = cell_id(*cell)
    regs = set(cellmath.regions_for_cell(cell))
    # Feature ownership footprints
    out = _out_dir(root)
    for feat in reg.features_in_cell(cid):
        fid = feat['id']
        for path in out.glob(f'{fid}*-cells.json'):
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                continue
            cells = data if isinstance(data, list) else data.get('cells') or []
            for entry in cells:
                if isinstance(entry, (list, tuple)) and len(entry) >= 3:
                    x, _y, z = int(entry[0]), int(entry[1]), int(entry[2])
                    regs.update(cellmath.regions_for_bbox(x, z, x, z))
                elif isinstance(entry, dict) and 'x' in entry and 'z' in entry:
                    x, z = int(entry['x']), int(entry['z'])
                    regs.update(cellmath.regions_for_bbox(x, z, x, z))
        for other in feat.get('cells') or []:
            regs.update(cellmath.regions_for_cell(parse_cell_id(other)))
    # Trees / decor masks
    masks = root / 'site' / 'masks'
    if masks.is_dir():
        for pass_name in ('trees', 'decor'):
            mp = masks / f'{pass_name}-{cell[0]}_{cell[1]}.mask.json'
            if not mp.is_file():
                continue
            try:
                mask = json.loads(mp.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                continue
            for pos in mask.get('placements') or mask.get('cells') or []:
                if isinstance(pos, (list, tuple)) and len(pos) >= 3:
                    regs.update(cellmath.regions_for_bbox(int(pos[0]), int(pos[2]), int(pos[0]), int(pos[2])))
    return sorted(regs)


def export_cell_registry(cell: tuple[int, int], *, store: RegistryStore) -> dict[str, Any]:
    """Deep copy of one cell record plus features that touch it.

    @param cell: (tx, tz)
    @param store: registry
    @returns: ``{cell, features, marker_was_up}``
    """
    cid = cell_id(*cell)
    c = dict(store.cell(cid))
    feats = {f['id']: {k: v for k, v in f.items() if k != 'id'} for f in store.features_in_cell(cid)}
    return {
        'cell_id': cid,
        'cell': c,
        'features': feats,
        'marker_was_up': c.get('marker') == 'up',
    }


def import_cell_registry(
    cell: tuple[int, int],
    slice_: dict[str, Any],
    *,
    store: RegistryStore,
) -> None:
    """Restore a previously exported cell slice into the registry.

    @param cell: (tx, tz)
    @param slice_: from :func:`export_cell_registry`
    @param store: registry
    """
    cid = cell_id(*cell)
    rec = dict(slice_.get('cell') or {})
    # Write via public setters where possible so invariants hold.
    ground = rec.get('ground', 'none')
    store.set_ground(cid, 'none')
    if ground != 'none':
        # ghost+features needs ghost first
        if ground == 'ghost+features':
            store.set_ground(cid, 'ghost')
            store.set_ground(cid, 'ghost+features')
        else:
            store.set_ground(cid, ground)
    if rec.get('trees') and store.cell(cid).get('ground') == 'generated':
        store.set_trees(cid, True)
    if rec.get('decor') and store.cell(cid).get('ground') == 'generated':
        store.set_decor(cid, True)
    if rec.get('hold'):
        store.set_hold(cid, rec['hold'])
    else:
        store.set_hold(cid, None)
    marker = rec.get('marker') or 'none'
    store.set_marker(cid, marker, replace_existing=True)
    for fid, feat in (slice_.get('features') or {}).items():
        store.register_feature(
            fid,
            name=feat.get('name') or fid,
            cells=list(feat.get('cells') or [cid]),
            status=feat.get('status') or 'planned',
        )


def clear_flags_from_step(
    cell: tuple[int, int],
    step: str,
    *,
    store: RegistryStore,
) -> None:
    """Clear registry flags for ``step`` and every later step so ``next`` retries.

    @param cell: (tx, tz)
    @param step: one of STEP_ORDER / ``feature``
    @param store: registry
    """
    cid = cell_id(*cell)
    key = 'features' if step in ('feature', 'features', 'ghost-features') else step
    if key == 'ghost-ground':
        key = 'ground'
    try:
        idx = STEP_ORDER.index(key) if key in STEP_ORDER else STEP_ORDER.index('features')
    except ValueError:
        idx = 0
    later = STEP_ORDER[idx:]
    # Features are "after" ground/ghost and before trees/decor in the repair sense.
    reopen_features = any(s in later for s in ('ground', 'ghost-ground', 'features', 'ghost-features'))
    if reopen_features:
        for feat in store.features_in_cell(cid):
            if feat.get('status') == 'built':
                store.set_feature_status(feat['id'], 'in-progress')
    if 'ground' in later or 'ghost-ground' in later:
        store.set_ground(cid, 'none')  # also clears trees/decor via RegistryStore
        return
    if 'trees' in later and store.cell(cid).get('ground') == 'generated':
        store.set_trees(cid, False)
    if 'decor' in later and store.cell(cid).get('ground') == 'generated':
        store.set_decor(cid, False)


def replay_plan(cell: tuple[int, int], *, store: RegistryStore) -> list[dict[str, Any]]:
    """Ordered replay steps from the current registry state (clipped to this cell).

    @param cell: (tx, tz)
    @param store: registry
    @returns: list of step dicts
    """
    cid = cell_id(*cell)
    c = store.cell(cid)
    steps: list[dict[str, Any]] = []
    ground = c.get('ground') or 'none'
    if ground in ('ghost', 'ghost+features'):
        steps.append({'kind': 'ghost-ground', 'cell': cid})
        if ground == 'ghost+features':
            steps.append({'kind': 'ghost-features', 'cell': cid})
    elif ground == 'generated':
        steps.append({'kind': 'ground', 'cell': cid})
    for feat in store.features_in_cell(cid):
        if feat.get('status') == 'built':
            steps.append({'kind': 'feature', 'cell': cid, 'feature': feat['id']})
    if c.get('trees'):
        steps.append({'kind': 'trees', 'cell': cid})
    if c.get('decor'):
        steps.append({'kind': 'decor', 'cell': cid})
    return steps


def backup_cell_repair(
    cell: tuple[int, int],
    *,
    store: RegistryStore | None = None,
    world_root: Path | None = None,
    region_dir: Path | None = None,
    backup_root: Path | None = None,
    confirm_logged_out: bool = False,
    skip_logout_gate: bool = False,
) -> Path:
    """Backup impacted ``.mca`` files and the cell's registry slice (kind=repair).

    @param cell: (tx, tz)
    @param store: registry
    @param world_root: world root
    @param region_dir: live region folder
    @param backup_root: backups/regions
    @param confirm_logged_out: logout gate
    @param skip_logout_gate: tests
    @returns: snap directory
    """
    import logout_gate

    if not skip_logout_gate:
        logout_gate.require_logged_out(confirm_logged_out=confirm_logged_out)
    root = Path(world_root) if world_root else Path(ROOT)
    reg = store or RegistryStore(root)
    region = Path(region_dir) if region_dir else RB.REGION
    broot = Path(backup_root) if backup_root else RB.BACKUP_ROOT
    regs = impacted_regions(cell, store=reg, world_root=root)
    stamp = time.strftime('%Y-%m-%d-%H%M%S')
    dest = broot / f'{stamp}-repair-{cell[0]}_{cell[1]}'
    dest.mkdir(parents=True, exist_ok=False)
    names = [f'r.{rx}.{rz}.mca' for rx, rz in regs]
    copied, missing, total = RB._copy_regions(dest, region, names)
    slice_ = export_cell_registry(cell, store=reg)
    (dest / 'registry-cell.json').write_text(json.dumps(slice_, indent=2, sort_keys=True) + '\n')
    meta = {
        'time': stamp,
        'name': f'repair-{cell[0]}_{cell[1]}',
        'kind': RB.KIND_REPAIR,
        'cell': cell_id(*cell),
        'region_path': str(region),
        'world_root': str(root),
        'files': copied,
        'bytes': total,
        'missing': missing,
        'marker_was_up': slice_.get('marker_was_up'),
    }
    RB.write_backup_meta(dest, meta)
    return dest


def undo_repair(
    snap: str | Path,
    *,
    confirm_logged_out: bool = False,
    world_root: Path | None = None,
    skip_logout_gate: bool = False,
) -> dict[str, Any]:
    """Restore region files and the saved registry cell slice from a repair snap.

    @param snap: snap directory name or path
    @param confirm_logged_out: logout gate
    @param world_root: world root
    @param skip_logout_gate: tests
    @returns: report dict
    """
    snap_dir = Path(snap)
    if not snap_dir.is_dir():
        snap_dir = RB.resolve_snap_dir(str(snap))
    meta = json.loads((snap_dir / 'BACKUP.json').read_text(encoding='utf-8'))
    if RB.snap_kind(meta) != RB.KIND_REPAIR:
        raise ValueError(f'{snap_dir.name} is not a repair snap (kind={RB.snap_kind(meta)})')
    restored = RB.restore_snap(
        snap_dir,
        confirm_logged_out=confirm_logged_out,
        allow_partial=True,
        restore_registry=False,
        world_root=world_root,
        skip_logout_gate=skip_logout_gate,
    )
    root = Path(world_root) if world_root else Path(meta.get('world_root') or ROOT)
    store = RegistryStore(root)
    slice_path = snap_dir / 'registry-cell.json'
    cell_s = meta.get('cell') or (json.loads(slice_path.read_text())['cell_id'] if slice_path.is_file() else None)
    if not cell_s:
        raise ValueError('repair snap missing cell id')
    cell = parse_cell_id(cell_s)
    if slice_path.is_file():
        import_cell_registry(cell, json.loads(slice_path.read_text(encoding='utf-8')), store=store)
    return {'cell': cell_s, 'regions': restored, 'snap': snap_dir.name}


def wipe_cell_to_air_plan(cell: tuple[int, int]) -> list[tuple[int, int, int, int, int, int]]:
    """Inclusive column boxes to fill with air above bedrock (y=1..319) for one cell.

    Does not talk to Minecraft — callers apply via site_kit / MCP.

    @param cell: (tx, tz)
    @returns: list of (x0,y0,z0,x1,y1,z1) fill boxes (one per column strip batch)
    """
    x0, z0, x1, z1 = cellmath.bounds(cell)
    # One big box per cell: air from y=1 (above bedrock) through build limit.
    return [(x0, 1, z0, x1, 319, z1)]


def run_repair(
    cell: tuple[int, int],
    *,
    confirm_logged_out: bool = False,
    dry_run: bool = False,
    world_root: Path | None = None,
    region_dir: Path | None = None,
    apply_wipe: Callable[[list], None] | None = None,
    apply_step: Callable[[dict], None] | None = None,
    raise_marker: Callable[[tuple[int, int]], None] | None = None,
    skip_logout_gate: bool = False,
) -> dict[str, Any]:
    """Backup, wipe cell columns, replay from registry, re-raise marker if needed.

    @param cell: (tx, tz)
    @param confirm_logged_out: logout gate
    @param dry_run: plan only (still may require confirm for live region backup)
    @param world_root: world root
    @param region_dir: region folder
    @param apply_wipe: called with wipe boxes (live)
    @param apply_step: called with each replay step dict
    @param raise_marker: re-raise marker callback
    @param skip_logout_gate: tests
    @returns: report
    """
    root = Path(world_root) if world_root else Path(ROOT)
    store = RegistryStore(root)
    slice_before = export_cell_registry(cell, store=store)
    marker_was_up = slice_before.get('marker_was_up')
    steps = replay_plan(cell, store=store)
    wipe = wipe_cell_to_air_plan(cell)
    report: dict[str, Any] = {
        'cell': cell_id(*cell),
        'dry_run': dry_run,
        'steps': steps,
        'wipe_boxes': len(wipe),
        'marker_was_up': marker_was_up,
    }
    if dry_run:
        report['impacted_regions'] = impacted_regions(cell, store=store, world_root=root)
        return report

    dest = backup_cell_repair(
        cell,
        store=store,
        world_root=root,
        region_dir=region_dir,
        confirm_logged_out=confirm_logged_out,
        skip_logout_gate=skip_logout_gate,
    )
    report['backup'] = dest.name

    wipe_fn = apply_wipe or default_apply_wipe
    step_fn = apply_step or default_apply_step
    wipe_fn(wipe)

    for step in steps:
        try:
            step_fn(step)
        except Exception as exc:
            clear_flags_from_step(cell, step['kind'], store=store)
            report['failed_step'] = step
            report['error'] = str(exc)
            report['flags_cleared_from'] = step['kind']
            raise

    if marker_was_up:
        marker_fn = raise_marker or default_raise_marker
        marker_fn(cell)
        report['marker_raised'] = True
    return report


def default_apply_wipe(boxes: list[tuple[int, int, int, int, int, int]]) -> None:
    """Fill wipe boxes with air via site_kit (live Minecraft).

    @param boxes: from :func:`wipe_cell_to_air_plan`
    """
    from site_kit import Plan, apply

    plan = Plan('registry_repair_wipe')
    for x0, y0, z0, x1, y1, z1 in boxes:
        plan.fill((x0, y0, z0), (x1, y1, z1), 'minecraft:air')
    apply(plan, dry=False, modifies=())


def default_apply_step(step: dict[str, Any]) -> None:
    """Replay one registry step by invoking the usual layer / ghost / feature tools.

    @param step: from :func:`replay_plan`
    @raises RuntimeError: when the step cannot be applied
    """
    kind = step['kind']
    cell = parse_cell_id(step['cell'])
    if kind == 'ground':
        import ground_layer as gl
        # Prefer a public apply entry when present; otherwise refuse clearly.
        if hasattr(gl, 'apply_cell'):
            gl.apply_cell(cell, dry=False)
            return
        raise RuntimeError(
            f'ground replay for {step["cell"]}: run ground_layer.py --cell {step["cell"]} --apply '
            'manually (no apply_cell helper)'
        )
    if kind in ('ghost-ground', 'ghost-features'):
        import lidar_ghost as lg
        lg.ensure_cell(cell, dry=False)
        return
    if kind == 'feature':
        fid = step['feature']
        raise RuntimeError(
            f'feature replay for {fid}: apply site/builds/{fid}.json via the feature build script, '
            'then retry repair or clear flags'
        )
    if kind == 'trees':
        raise RuntimeError(
            f'trees replay for {step["cell"]}: run trees_layer.py --cell {step["cell"]} --apply'
        )
    if kind == 'decor':
        raise RuntimeError(
            f'decor replay for {step["cell"]}: run decor_layer.py --cell {step["cell"]} --apply'
        )
    raise RuntimeError(f'unknown repair step {kind!r}')


def default_raise_marker(cell: tuple[int, int]) -> None:
    """Re-raise the wool marker for ``cell``.

    @param cell: (tx, tz)
    """
    import cell_marker as cm
    cm.cmd_raise(cell[0], cell[1], dry=False)
