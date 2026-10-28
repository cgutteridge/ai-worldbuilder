---
name: purge
description: >-
  Erase an element (feature, ground, trees, decor), a whole cell, everything
  above the ghost baseline, or the whole project completely, then redesign it
  with no sight of the old build. Use when the user says purge, wipe, scrap,
  nuke, start again from nothing, purge the non-ghost stuff, or a build is too
  wrong to refine. Erase first; design only after the world and journal are empty.
---

Human-invoked clean slate: **erase completely, then design blind**. If most of it is right, use `refine`. If the player is standing at a spot and wants it redone now, `redo` is the quick path; `purge` is for a named scope, any distance away, with the old build gone from every place it could be read.

No region backup or restore exists (ADR 004). The journal Undo saves a structure template before it erases, but treat purge as one-way.

## 0. Scope and consent

1. Scope is one of: **element** (feature id, or ground / trees / decor of a cell), **cell** (all four elements of one cell), **above-ghost** (every cell that still has anything above the lidar ghost — keeps ghost), **project** (the whole active world, including ghost). Resolve element/cell with `cell_registry.py status` / `query`. Resolve above-ghost with `cell_registry.py above-ghost` (totals + per-cell counts; `#135`). Ask if it is ambiguous — phrases like "purge the non-ghost stuff" / "wipe everything above ghost" are **above-ghost**, not project.
2. Say what will go, in numbers: cells, features by name, passes. For above-ghost, quote the `above-ghost` totals (cells / ground / trees / decor / features) and the cell list. Wait for a clear yes. Cell, above-ghost and project scopes need a yes naming that scope. Project scope also needs the player off the server (`dryrun_reset.py` refuses otherwise).
3. Note the why in one line, as a class of failure (evidence / translation / capability / bar, as in `refine` §2), never as geometry. It is for the issue record, not for the redesign.

Done when the user has said yes to a counted scope.

## 1. Erase blocks, deepest first

Claim the lock for every live step: `python3 scripts/site_lock.py run <owner> "purge <scope>" -- <cmd>`. Dry run first when the command has one. Order is the reverse of the build order, and a later pass never outlives its base:

- **Element:** one pass or plan — `element_undo.py --world <name> --cell tx,tz --pass decor|trees|ground --apply`, or `element_undo.py --world <name> <plan> --apply` for a feature.
- **Cell:** one command undoes everything above ghost in fixed order (decor → trees → features → ground); it stops at the first refusal and reports what is left (#134):
  `element_undo.py --world <name> --cell tx,tz` then `--apply` under the lock. Do not hand-run the four passes out of order.
- **Above-ghost:** keep the ghost baseline. Count once with `cell_registry.py above-ghost`, then for each listed cell run the whole-cell undo (#134) under the lock — dry run the cell, then `--apply`. Stop the chain on the first cell refusal; re-run `above-ghost` to see what is left. Do not call `dryrun_reset.py` (that is project scope and wipes ghost).

**Project scope** uses `dryrun_reset.py --dry-run`, then `--confirm-logged-out` (wipes regions and registry, keeps world-frame, providers and reference downloads).

An interrupted step: `site_recover.py status`. Drift left behind: `cell_registry.py reconcile --fix`. Never hand-copy `.mca` files.

Done when the run reported clean readback for every undo, or `dryrun_reset.py` finished.

## 2. Erase the record

The old build must not be readable as a reference. For each purged feature or layer:

1. Retire its build script or `site/builds/<name>.json` out of the working tree: tar to `backups/retired-<what>-<date>.tar.gz`, `git rm` (the `cleanup.md` build-scripts route). Keep the re-run mechanism only: if a generator is shared, leave it, delete the per-element inputs.
2. Archive leftovers named for the element: its `verification/site/*-cells.json` / `-backup.json` / `-preview` files, mask archives (`masks/undone/`), per-element notes in `site/`. Anything an agent could `Read` as "what was there" moves into the same tar or goes.
3. Note purged scope on matching snags (`scripts/issues.py`); do not use retired `cell_registry.py issues`.

For **above-ghost**, retire every feature and layer that the undo removed across the listed cells (one tar per feature/layer, or one dated above-ghost tar if the set is large), then confirm `above-ghost` no longer names those builds.

Done when `grep -ril <element name or id>` over `scripts/`, `site/`, `verification/site/` finds nothing but the tar, and `git status` shows the retirements.

## 3. Prove it is empty

1. `cell_registry.py query` / `status` for the scope shows the elements not done, no feature builds, no passes. For **above-ghost**, `cell_registry.py above-ghost` reports `cells: 0` (ghost-only cells are omitted).
2. `cell_registry.py reconcile <cells>` dry run is clean (every cell that was on the above-ghost list, or the element/cell/project scope).
3. Compare the footprint with the apply-time `before_<plan>` structure (not only reconcile). Undo already fails closed on this (#123: `site_kit.verify_erase_matches_before`); still spot-check: live top / a few columns must match `southampton_refine:before_<plan>` (or the structure named in the build entry's meta), not a pit of air where substrate was. `block_scan_summary` / `view_capture` over the box should show only the ghost/ground base — no residue of the old build.

Residue or a pit vs `before_<plan>` means step 1 or 2 was incomplete (or a pre-#124 undo): prefer `cell_registry.py reconcile --fix` first; if the journal already matches a hollow world, `restore_structure_backup.py <plan>` then reconcile again. Do not design over residue.

Done when all three checks agree. Say so in one line: scope, what was erased, journal view.

## 4. Design blind

The element is now unbuilt, so `cell_registry.py next` will offer it in the normal order (ground, features, trees, decor), or hand it straight to the owner:

- **Feature:** the `redo` skill from its evidence-table step: footprint, heights, openings, roof and greeblies read fresh from the sources, filled in before planning. Do not recover geometry or material choices from the tar, git history or chat.
- **Ground / trees / decor:** run the layer script for the cell (`ground_layer.py`, `trees_layer.py`, `decor_layer.py`; dry run, then `--apply`) from current evidence.
- **Cell / above-ghost:** follow `next`; cell journal ring order and the done-bar apply (`rules/radial-good-enough.md`). Ghost stays; redesign starts from generated ground upward.
- **Project:** after `dryrun_reset`, re-raise the ghost frontier first, then follow `next` from empty registry (ghost was wiped with the rest).

Name designer and builder first (`build_roles.py show`, `rules/build-roles.md`).

Done when the redesign has started from the evidence table, or the user has deferred it and `next` shows the scope as open.

## 5. Lesson

If the why from §0 points at a tool cause (not a one-off), file up to three lessons as `refine` §4 defines them (general, upstream, cheap, new) on the issue as notes or snags. Do not propose or add tool tasks. Otherwise say "no general lesson".
