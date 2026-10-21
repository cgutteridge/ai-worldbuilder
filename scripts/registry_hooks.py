"""Side-effect writers that keep the cell registry in sync with build commands.

Ticket #30: the registry is updated by code as a side effect of applies — not by
LLM-followed rules. Call these hooks from ground/trees/decor layers, site_kit.apply,
cell_marker, lidar_ghost, mask_undo, strip_cell_above_ground, and site_recover.

Failed applies must not call the clean-success hooks. Undos and strips clear the
matching flags in the same step as the block removal.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

import cellmath
from cell_registry import RegistryError, RegistryStore, cell_id

LAYER_GROUND = 'ground'
LAYER_TREES = 'trees'
LAYER_DECOR = 'decor'


def default_store(root: Path | None = None) -> RegistryStore:
    """Active-world registry store (overridable in tests).

    Honour ``AI_WORLDBUILDER_REGISTRY_ROOT`` when ``root`` is omitted so unit
    tests can redirect writes away from the live world checkout.

    @param root: optional world root
    @returns: RegistryStore
    """
    if root is None:
        env = os.environ.get('AI_WORLDBUILDER_REGISTRY_ROOT')
        if env:
            root = Path(env)
    return RegistryStore(root)


def cells_touched_by_positions(positions: Iterable[tuple[int, int, int]]) -> list[str]:
    """Unique sorted cell ids covering block positions.

    @param positions: (x, y, z) iterable
    @returns: list of ``tx,tz`` strings
    """
    cells = {cell_id(*cellmath.cell_of(x, z)) for x, _y, z in positions}
    return sorted(cells, key=lambda s: tuple(int(p) for p in s.split(',')))


def on_layer_clean(
    cell: tuple[int, int],
    layer: str,
    *,
    store: RegistryStore | None = None,
) -> None:
    """Record a clean layer apply (ground / trees / decor).

    @param cell: (tx, tz)
    @param layer: ground | trees | decor
    @param store: optional store (tests)
    @raises RegistryError: on invariant refusal
    """
    reg = store or default_store()
    cid = cell_id(*cell)
    if layer == LAYER_GROUND:
        reg.set_ground(cid, 'generated')
    elif layer == LAYER_TREES:
        reg.set_trees(cid, True)
    elif layer == LAYER_DECOR:
        reg.set_decor(cid, True)
    else:
        raise RegistryError(f'unknown layer {layer!r}')


def on_layer_cleared(
    cell: tuple[int, int],
    layer: str,
    *,
    store: RegistryStore | None = None,
) -> None:
    """Clear a layer flag after undo / strip / recover rollback.

    @param cell: (tx, tz)
    @param layer: ground | trees | decor
    @param store: optional store
    """
    reg = store or default_store()
    cid = cell_id(*cell)
    if layer == LAYER_GROUND:
        # Back to none: trees/decor cleared by set_ground.
        reg.set_ground(cid, 'none')
    elif layer == LAYER_TREES:
        reg.set_trees(cid, False)
    elif layer == LAYER_DECOR:
        reg.set_decor(cid, False)
    else:
        raise RegistryError(f'unknown layer {layer!r}')


def on_feature_begin(
    feature_id: str,
    *,
    name: str | None = None,
    cells: list[str] | None = None,
    store: RegistryStore | None = None,
) -> None:
    """Mark a feature in-progress when a live apply begins.

    Registers the feature if absent. Does not invent cells when none given.

    @param feature_id: build slug (plan name)
    @param name: display name (defaults to feature_id)
    @param cells: optional initial cell list
    @param store: optional store
    """
    reg = store or default_store()
    existing = reg.feature(feature_id)
    cell_list = list(cells) if cells is not None else list((existing or {}).get('cells') or [])
    if existing is None:
        if not cell_list:
            # Defer registration until the plan has at least one cell.
            return
        reg.register_feature(
            feature_id,
            name=name or feature_id,
            cells=cell_list,
            status='in-progress',
        )
        return
    if cell_list and cell_list != list(existing.get('cells') or []):
        reg.register_feature(
            feature_id,
            name=name or existing.get('name') or feature_id,
            cells=cell_list,
            status='in-progress',
        )
    else:
        reg.set_feature_status(feature_id, 'in-progress')


def on_feature_clean(
    feature_id: str,
    positions: Iterable[tuple[int, int, int]],
    *,
    name: str | None = None,
    store: RegistryStore | None = None,
) -> None:
    """Mark a feature built after a zero-mismatch readback; overwrite cells from footprint.

    @param feature_id: build slug
    @param positions: plan block positions
    @param name: optional display name
    @param store: optional store
    """
    reg = store or default_store()
    cells = cells_touched_by_positions(positions)
    if not cells:
        raise RegistryError(f'feature {feature_id!r} clean apply has no footprint cells')
    existing = reg.feature(feature_id)
    reg.register_feature(
        feature_id,
        name=name or (existing or {}).get('name') or feature_id,
        cells=cells,
        status='built',
    )


def on_feature_mismatches(
    feature_id: str,
    positions: Iterable[tuple[int, int, int]],
    *,
    name: str | None = None,
    store: RegistryStore | None = None,
) -> None:
    """Keep a feature in-progress after a mismatched readback; still refresh cells.

    @param feature_id: build slug
    @param positions: plan block positions
    @param name: optional display name
    @param store: optional store
    """
    reg = store or default_store()
    cells = cells_touched_by_positions(positions)
    if not cells:
        return
    existing = reg.feature(feature_id)
    reg.register_feature(
        feature_id,
        name=name or (existing or {}).get('name') or feature_id,
        cells=cells,
        status='in-progress',
    )


def on_marker_raise(cell: tuple[int, int], *, store: RegistryStore | None = None) -> None:
    """Record the raised marker (clears any prior marker in the same write).

    @param cell: (tx, tz)
    @param store: optional store
    """
    (store or default_store()).set_marker(cell_id(*cell), 'up', replace_existing=True)


def on_marker_clear(cell: tuple[int, int], *, store: RegistryStore | None = None) -> None:
    """Clear the marker flag for a cell.

    @param cell: (tx, tz)
    @param store: optional store
    """
    (store or default_store()).set_marker(cell_id(*cell), 'none')


def on_ghost_ground_applied(cell: tuple[int, int], *, store: RegistryStore | None = None) -> None:
    """Ghost ground laid: none/ghost → ghost (leaves ghost+features alone).

    @param cell: (tx, tz)
    @param store: optional store
    """
    reg = store or default_store()
    cid = cell_id(*cell)
    current = reg.cell(cid).get('ground', 'none')
    if current in ('none', 'ghost'):
        reg.set_ground(cid, 'ghost')
    # ghost+features / generated: real work owns the cell; do not demote.


def on_ghost_features_applied(cell: tuple[int, int], *, store: RegistryStore | None = None) -> None:
    """Ghost features laid: promote ghost → ghost+features.

    @param cell: (tx, tz)
    @param store: optional store
    """
    reg = store or default_store()
    cid = cell_id(*cell)
    current = reg.cell(cid).get('ground', 'none')
    if current == 'none':
        reg.set_ground(cid, 'ghost')
        current = 'ghost'
    if current in ('ghost', 'ghost+features'):
        reg.set_ground(cid, 'ghost+features')


def on_ghost_features_stripped(
    cells: Iterable[tuple[int, int]],
    *,
    store: RegistryStore | None = None,
) -> None:
    """After stripping ghost features: ghost+features → ghost on each cell.

    @param cells: cells whose ghost features were stripped
    @param store: optional store
    """
    reg = store or default_store()
    for cell in cells:
        cid = cell_id(*cell)
        if reg.cell(cid).get('ground') == 'ghost+features':
            reg.set_ground(cid, 'ghost')


def on_feature_reopened(feature_id: str, *, store: RegistryStore | None = None) -> None:
    """After a recover rollback of a feature apply, keep status in-progress.

    @param feature_id: build slug
    @param store: optional store
    """
    reg = store or default_store()
    if reg.feature(feature_id):
        reg.set_feature_status(feature_id, 'in-progress')


def on_stage_key_clean(
    cell: tuple[int, int],
    stage_key: str,
    *,
    plan_name: str | None = None,
    positions: Iterable[tuple[int, int, int]] | None = None,
    meta: dict | None = None,
    mismatches: int = 0,
    store: RegistryStore | None = None,
) -> None:
    """Dispatch registry updates after site_kit.apply finishes a stage.

    @param cell: primary cell
    @param stage_key: STAGES key (ground-layer, trees, decor, feature-*, ghost-*, *-undo)
    @param plan_name: plan name (feature id for feature stages)
    @param positions: plan positions for feature footprint
    @param meta: apply meta (kind=ghost|undo, pass=…)
    @param mismatches: readback mismatch count
    @param store: optional store
    """
    meta = meta or {}
    if meta.get('kind') == 'ghost':
        if mismatches:
            return
        if stage_key == 'ghost-ground':
            on_ghost_ground_applied(cell, store=store)
        elif stage_key == 'ghost-features':
            on_ghost_features_applied(cell, store=store)
        return

    if meta.get('kind') == 'undo' or stage_key.endswith('-undo'):
        if mismatches:
            return
        pass_name = meta.get('pass') or stage_key.replace('-undo', '')
        if pass_name in (LAYER_TREES, LAYER_DECOR):
            on_layer_cleared(cell, pass_name, store=store)
        return

    if stage_key in ('ground-layer', 'ground'):
        if mismatches == 0:
            on_layer_clean(cell, LAYER_GROUND, store=store)
        return

    if stage_key == LAYER_TREES:
        if mismatches == 0:
            on_layer_clean(cell, LAYER_TREES, store=store)
        return

    if stage_key == LAYER_DECOR:
        if mismatches == 0:
            on_layer_clean(cell, LAYER_DECOR, store=store)
        return

    if stage_key.startswith('feature-'):
        fid = plan_name or stage_key[len('feature-'):]
        pos = list(positions or ())
        if mismatches:
            on_feature_mismatches(fid, pos, store=store)
        else:
            on_feature_clean(fid, pos, store=store)
        return
