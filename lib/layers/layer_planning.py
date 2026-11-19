"""Read-only planning after a cell's own element mask would be undone."""
from __future__ import annotations

from dataclasses import dataclass

from site_kit import Plan, PlannedWorld


@dataclass
class UndoPreview:
    """The simulated world and ownership, or the unchanged inputs on conflict."""
    world: object
    masked: dict
    own: set
    conflicts: list


def plan_after_undo(pass_name, cell, world, *, mask_dir=None) -> UndoPreview:
    """Clear only unshared own-mask blocks, atomically refusing a conflicting undo."""
    from pass_mask import MASK_DIR, mask_path, mask_placements, plan_undo, read_mask

    directory = MASK_DIR if mask_dir is None else mask_dir
    path = mask_path(pass_name, cell, directory)
    mask = read_mask(path) if path.exists() else None
    own = set(mask_placements(mask)) if mask else set()
    remaining = {}
    for other in sorted(directory.glob('*.mask.json')):
        if other == path:
            continue
        m = read_mask(other)
        remaining.update({p: f"{m['pass']} {m['cell'][0]},{m['cell'][1]}" for p in mask_placements(m)})
    cleared, conflicts = plan_undo(mask, lambda p: world.block(*p), own & remaining.keys()) if mask else ([], [])
    if conflicts:
        masked = {**dict.fromkeys(own, f'{pass_name} {cell[0]},{cell[1]}'), **remaining}
        return UndoPreview(world, masked, own, conflicts)
    return UndoPreview(PlannedWorld(Plan('undo-preview'), world, ignore=cleared), remaining, own, [])
