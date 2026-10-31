---
name: redo
description: >-
  Redesign from scratch at the player's feet: a built feature, or cell trees /
  decor / ground. Use when the user says redo, redo trees, redo decor, redo
  ground, start over, or rebuild from the evidence table while standing somewhere.
---

Player-standing redesign. No confirmation pause. No snag review loop (they are already there). Claim the lock, apply, release.

Cell journal (ADR 004): clean applies land journal entries; undos/clears clear matching ownership. Do not edit retired progress files by hand. After a redo, `cell_registry.py next` / `status` reflect what is left. Snags: `snag` skill (`scripts/issues.py`); a redo of a tagged element is what lets an issue reach fix-landed. Mess in a cell → `reconcile [--fix]`.

## Branch

| User says | Target |
| --- | --- |
| `redo` / start over / rebuild (no layer word) | **Feature** covering their `x,z` |
| `redo trees` | **Trees** for the cell under their feet |
| `redo decor` | **Decor** for that cell |
| `redo ground` | **Ground** for that cell (preserve trees/decor, their masks and completion) |

## 0. Locate

1. Establish the explicit world checkout from its `config/world.json` and the open save; keep that same `--world <world>` on every place command. If the match is unclear, resolve it before proceeding. `player_list_online` → player `x,y,z` (floor to blocks); confirm this is the configured player targeted by `mc_player.py`. If they are offline, stop this player-standing redo.
2. `python3 scripts/redo_here.py --world <world> --at <x>,<z> --y <y> --json`
3. Read `cell`, `primary`, `features`, `masks`.

Done when the JSON is in hand.

## Checked execution (every branch)

Prepare evidence and the feature hand-off before live work. Run each live
command below (undo, apply or player move) through the lock wrapper, from the
tool root:

```bash
python3 scripts/site_lock.py --world <world> run <owner> "redo <target>" -- <command>
```

Substitute the complete command from the chosen branch for `<command>`, including
its own `--world <world>`. The wrapper supplies `SITE_OWNER` and releases the
lock on success, failure or interrupt. If the lock is refused, stop. These manual
steps acquire a lock per command; the future layer wrapper (#223) will hold one
across the sequence.

Start undo/apply commands **in the background from the start**, retaining their
logs and final exit status; see `rules/slow-commands.md`. Wait for each to finish
before starting the next step. Background launch is not completion: check the
process exit status and readback before reporting success.

Keep failures visible: run commands directly, with no `tail`/logging pipeline
that can replace their exit status. A failed undo, dry run, apply or hover stops
the remaining steps. Report which steps landed and which remain undone; use
`python3 scripts/site_recover.py --world <world> status` for a journal entry left
`begun`, or `python3 scripts/cell_registry.py --world <world> reconcile tx,tz` for
drift. Neither automatic retry nor `--skip-changed` is part of redo. Cue
`party-cheer` only after success; a missing sound cue does not change a successful
build result.

Done when every step has a checked result and its lock has been released.

## Hover (every branch)

Always put the player above the target, looking straight down — before the first apply, and again after the last apply if their position changed (e.g. `site_kit.apply` moved them for chunk load).

1. Centre: feature footprint midpoint, or cell centre (`tx*128+64`, `tz*128+64`).
2. Height: top of the target + ~20 (feature top from its cells / lidar; cell: surface + 40 is fine).
3. Move with the required-why CLI (not raw `entity_teleport`). `--why` names the target, e.g. hovering over the feature or cell for the redo. Face a point on the ground at the same `x,z` (look straight down). Spectator or creative:

   ```bash
   python3 scripts/mc_player.py --world <world> move \
     --why "Hovering over oakdene_court so you can see the redo" \
     --pos x,y,z --facing x,y,z --gamemode spectator
   ```

4. Check the move command's exit status, then read the player's actual position,
   gamemode and viewing direction from client/server perception. Confirm they are
   above the selected target looking down before any undo or apply. A command
   returning successfully alone is insufficient; if the view cannot be confirmed,
   stop the redo. After the last apply, check again and repeat the move if needed.
   Leave them overhead.

Hover is a requirement of this player-standing redo workflow. Direct
`trees_layer.py`, `decor_layer.py` and `ground_layer.py` applies remain independent
of player position (owner decision #221).

Done when they are hovering and looking down at the target.

## 1. Feature (`redo`)

1. **No feature** (`features` empty): tell the user there is no built feature at this column. Stop.
2. **Ambiguous** (`primary` null, several `features`): tell them the names and to stand inside the one they want. Stop.
3. Resolve build roles (`python3 scripts/build_roles.py --world <world> show` or overrides). Name designer + builder in the session before applying.
4. Prepare the feature redesign below; only the builder runs live commands when models differ.
5. **Hover** (above) before the first undo or apply, using checked execution.
6. **Evidence table first** (`rules/building-review.md` B57 / reflection step 1). Do **not** read the old build’s placed blocks or the old script’s material/geometry choices as reference. Keep only the re-run mechanism. Rewrite `scripts/<script>` (or `site/builds/<name>.json` for `feature_build`) from the table. Every row below must be filled in the script docstring before planning:

   | Fact | Source |
   | --- | --- |
   | Footprint | vector map (+ university outline if campus) |
   | Heights / roof levels | lidar (`lidar_parts` / grid), not aerial colour reads |
   | Walls, openings, doors | portal + building photos → `facade_plan` entrances (`python3 shims/portals.py --world <world>`, `python3 shims/buildings.py --world <world>`) |
   | Roof colour + **greeblies** | aerial sheet (`python3 shims/google_aerial.py --world <world>` / `python3 shims/google_window.py --world <world>`) |

   **Greeblies are mandatory** (`rules/craftsmanship.md` GREEBLIES; building-review). From the aerial sheet, list every plant box, capped box vent, louvre, recessed skylight, hollow industrial chimney, solar panel, lift overrun and parapet worth reading at street scale — or write `greeblies: none visible` in the spec `notes`. A shell-only plan is not a finished redo. For `feature_build`, put them in the spec `details` table (`rules/feature-builds.md`); they rebuild with the building. Heights of plinths/plant from lidar; colours and XY from the aerial sheet. Skylight at means roof-top Y and its entire box must be on roof; it replaces one layer. Vents are concrete boxes with trapdoor caps. Chimneys are hollow open rings, round or square and optionally tapered: size from photos and explicit spec dimensions, with lidar only a rough warning. Facade greeblies (drainpipe, sign, canopy, ledge, fascia) come from street photos into the same table.

   Aerial sheet: run `python3 shims/google_aerial.py --world <world> sheet` / `python3 shims/google_window.py --world <world>` and `Read` the PNG under `instance/google-aerial/`. Not seeing the sheet is not a reason to skip greeblies; stop and fix the download.

   If designer ≠ builder: stop after the table + hand-off artefact; builder continues from the next step.
7. **Readiness check, then clear the old build** (`rules/build-workflow.md` step 3: journal clean, ground and door-front levels match lidar, dry run on the current world). Do not undo before it passes. **Clear the old build first.** If the feature has a landed journal entry (it was built before), `python3 scripts/element_undo.py --world <world> <name> --apply` through the lock wrapper *before* applying. A plain re-apply only writes the new plan's cells, so old roof, stair and edit blocks outside it survive. Use `python3 scripts/cell_registry.py --world <world> query --feature <name>` to establish whether a landed entry needs undo; `redo_here` only locates the target. Skip undo only when no landed entry exists.
8. Dry run with `python3 scripts/<script> --world <world>` (or `python3 scripts/feature_build.py --world <world> <absolute-spec-path>`), then run the same command with `--apply`. Resolve the spec to its absolute path under the chosen world first. Require exit 0 and readback 0 mismatches.
9. **Hover** again if the player moved during apply.
10. Check command completion and lock release as above, then cue `party-cheer`.

Done when readback is clean, the docstring has a greeblie list (or explicit none), greeblies are applied, they are hovering looking down, and the lock is released. Say what was rebuilt (name + script).

## 2. Trees (`redo trees`)

Cell = `redo_here` `cell`. **Hover**, then use checked execution for the following steps:

1. If `masks.decor`: `python3 scripts/mask_undo.py --world <world> --cell tx,tz --pass decor --apply` (clear only).
2. If `masks.trees`: `python3 scripts/mask_undo.py --world <world> --cell tx,tz --pass trees --apply`.
3. `python3 scripts/trees_layer.py --world <world> --cell tx,tz` dry run, then the same command with `--apply`. Require exit 0 and readback 0 mismatches.
4. Check **Hover** again, then command completion and lock release.

Done when trees readback is clean and the final overhead view is confirmed.
Report that decor needs redo after tree generation; if its mask was undone,
it stays cleared. Apply decor only when the user requested it too.

## 3. Decor (`redo decor`)

**Hover**, then use checked execution for the following steps:

1. If `masks.decor`: `python3 scripts/mask_undo.py --world <world> --cell tx,tz --pass decor --apply`.
2. `python3 scripts/decor_layer.py --world <world> --cell tx,tz` dry run, then the same command with `--apply`. Require exit 0 and readback 0 mismatches.
3. Check **Hover** again, then command completion and lock release.

Done when decor readback is clean and the final overhead view is confirmed.

## 4. Ground (`redo ground`)

Preserve trees and decor, including their masks, ownership and completion
(owner rule #217). Ground redo does not grant permission to overwrite them.
**Hover**, then use checked execution for the following steps:

1. `python3 scripts/ground_layer.py --world <world> --cell tx,tz` dry run with the existing masks in place. If guards report a clash, stop and report the protected positions; leave trees/decor intact.
2. Run the same command with `--apply`. Require exit 0 and readback 0 mismatches.
3. Check **Hover** again, then command completion and lock release.

Done when ground readback is clean, trees/decor remain protected with their
completion unchanged, and the final overhead view is confirmed.

## Build roles (feature redesign only)

Before designing or applying a feature redo, name designer and
builder (defaults: `config/build-roles.json`; see `rules/build-roles.md`):

```bash
python3 scripts/build_roles.py --world <world> show
```

Same model for both → one session (evidence table, then apply). Different
models → designer session first (table + hand-off, no apply), then builder
session (apply only). Feature hand-off is `site/builds/<name>.json` via
`feature_build` when the build fits that path. Ghosts are not part of redo AI
roles. Trees, decor and ground plans are script-generated; their redos do not
require a designer/builder hand-off.

## Guards

- One builder: never apply without a fresh `SITE_OWNER` lock.
- Fail closed on mask conflicts / readback mismatches (`site_recover.py` if a journal entry is left `begun`; `reconcile` if blocks drifted).
- Feature redesign never uses the previous build as evidence.
- Moving the player is allowed for hover (this skill); end cue is always `party-cheer` (`yay` is broken).
