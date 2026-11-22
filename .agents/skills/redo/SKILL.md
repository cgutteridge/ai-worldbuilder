---
name: redo
description: >-
  Redesign from scratch at the player's feet: a built feature, or cell trees /
  decor / ground. Use when the user says redo, redo trees, redo decor, redo
  ground, start over, or rebuild from the evidence table while standing somewhere.
  Reapply runs a recorded feature builder without redesign.
---

Player-standing redesign. No confirmation pause. No snag review loop (they are already there). Claim the lock, apply, release.

Cell journal (ADR 004): clean applies land journal entries; undos/clears clear matching ownership. Do not edit retired progress files by hand. After a redo, `cell_registry.py next` / `status` reflect what is left. Snags: `snag` skill (`scripts/issues.py`); a redo of a tagged element is what lets an issue reach fix-landed. Mess in a cell → `reconcile [--fix]`.

## Branch

| User says | Target |
| --- | --- |
| `reapply` | **Recorded builder** for the named feature; see §3 |
| `redo` / start over / rebuild (no layer word) | **Feature** covering their `x,z` |
| `redo trees` | **Trees** for the cell under their feet |
| `redo decor` | **Decor** for that cell |
| `redo ground` | **Ground** for that cell (preserve trees/decor, their masks and completion) |

## 0. Locate

1. Run `python3 scripts/which_world.py --json` to match the open save to its checkout (local `lsof` process visibility required). Proceed only with one verified match; use that same `--world <world>` on every place command. `player_list_online` → player `x,y,z` (floor to blocks); confirm this is the configured player targeted by `mc_player.py`. If they are offline, stop this player-standing redo.
2. `python3 scripts/redo_here.py --world <world> --at <x>,<z> --y <y> --json`
3. Read `cell`, `primary`, `features`, `masks`.

Done when the JSON is in hand.

## Checked execution (every branch)

For feature redesign, prepare evidence and the feature hand-off before live work.
Run each manual feature command below (undo, apply or player move) through the
lock wrapper, from the tool root:

```bash
python3 scripts/site_lock.py --world <world> run <owner> "redo <target>" -- <command>
```

Layer redos (§2) and reapply (§3) own their sequence locks; call their wrappers directly.

Substitute the complete command from the chosen branch for `<command>`, including
its own `--world <world>`. The wrapper supplies `SITE_OWNER` and releases the
lock on success, failure or interrupt. If the lock is refused, stop. Manual feature
steps acquire a lock per command. The layer wrapper holds one lock across its
entire sequence; call it directly, without an outer site_lock run.

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

## Hover (feature redesign)

Always put the player above the target, looking straight down — before the first apply, and again after the last apply if their position changed (e.g. `site_kit.apply` moved them for chunk load).

1. Centre: feature footprint midpoint, or cell centre (`tx*128+64`, `tz*128+64`).
2. Height: top of the target + ~20 (feature top from its cells / lidar; cell: surface + 40 is fine).
3. Move with the required-why CLI (not raw `entity_teleport`). `--why` names the target, e.g. hovering over the feature or cell for the redo. Face a point on the ground at the same `x,z` (look straight down). Spectator or creative:

   ```bash
   python3 scripts/mc_player.py --world <world> move \
     --why "Hovering over the feature so you can see the redo" \
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
   | Heights / roof levels | lidar (`scripts/lidar_heightmap.py` / grid), not aerial colour reads |
   | Walls, openings, doors | street photos; optional `entrance_register` + `building_register` catalogue roles → `facade_plan` entrances (`python3 shims/portals.py --world <world>`, `python3 shims/buildings.py --world <world>`) |
   | Roof colour + **greeblies** | aerial sheet (`python3 shims/google_aerial.py --world <world>` / `python3 shims/google_window.py --world <world>`) |

   **Greeblies are mandatory** (`rules/craftsmanship.md` GREEBLIES; building-review). From the aerial sheet, list every plant box, capped box vent, louvre, recessed skylight, hollow industrial chimney, solar panel, lift overrun and parapet worth reading at street scale — or write `greeblies: none visible` in the spec `notes`. A shell-only plan is not a finished redo. For `feature_build`, put them in the spec `details` table (`rules/feature-builds.md`); they rebuild with the building. Heights of plinths/plant from lidar; colours and XY from the aerial sheet. Skylight at means roof-top Y and its entire box must be on roof; it replaces one layer. Vents are concrete boxes with trapdoor caps. Chimneys are hollow open rings, round or square and optionally tapered: size from photos and explicit spec dimensions, with lidar only a rough warning. Facade greeblies (drainpipe, sign, canopy, ledge, fascia) come from street photos into the same table.

   Aerial sheet: run `python3 shims/google_aerial.py --world <world> sheet --cell tx,tz` (located cell), or `python3 shims/google_window.py --world <world> crop --at x,z` (feature centre) and `Read` the PNG under `instance/google-aerial/`. Not seeing the sheet is not a reason to skip greeblies; stop and fix the download.

   If designer ≠ builder: stop after the table + hand-off artefact; builder continues from the next step.
7. **Readiness check, then clear the old build** (`rules/build-workflow.md` step 3: journal clean, ground and door-front levels match lidar, dry run on the current world). Do not undo before it passes. **Clear the old build first.** If the feature has a landed journal entry (it was built before), `python3 scripts/element_undo.py --world <world> <name> --apply` through the lock wrapper *before* applying. A plain re-apply only writes the new plan's cells, so old roof, stair and edit blocks outside it survive. Use `python3 scripts/cell_registry.py --world <world> query --feature <name>` to establish whether a landed entry needs undo; `redo_here` only locates the target. Skip undo only when no landed entry exists.
8. Dry run with `python3 scripts/<script> --world <world>` (or `python3 scripts/feature_build.py --world <world> <absolute-spec-path>`), then run the same command with `--apply`. Resolve the spec to its absolute path under the chosen world first. Require exit 0 and readback 0 mismatches.
9. **Hover** again if the player moved during apply.
10. Check command completion and lock release as above, then cue `party-cheer`.

Done when readback is clean, the docstring has a greeblie list (or explicit none), greeblies are applied, they are hovering looking down, and the lock is released. Say what was rebuilt (name + script).

## 2. Layers (trees, decor, ground)

Use `cell` from Locate. Select only the requested layers: `trees`, `decor`,
`ground`, or a comma-separated combination. Inspect the dry run first:

```bash
python3 scripts/redo_layer.py --world <world> --cell tx,tz --layers trees,decor
```

Require `ok: true` and inspect `pending`, the proposed sequence. Dry run checks
save identity, readiness and relevant masks; it leaves blocks and the player
unchanged. This previews operations, not an assertion that a pre-undo geometry
plan will equal the post-undo plan.

Run the same selection with `--apply`, in the background from the start:

```bash
python3 scripts/redo_layer.py --world <world> --cell tx,tz --layers trees,decor --apply
```

The caller retains the log and final exit status. The wrapper runs its children
synchronously with streamed output, holds one lock, stops remaining child
processes before releasing it, and does not detach its own worker. Background
launch is not completion; wait for the exit status and final JSON.

- Layers apply in `ground -> trees -> decor` order regardless of argument order.
  Trees redo first undoes existing decor, then existing trees. Only requested
  layers are reapplied: with `--layers trees`, decor stays undone/pending and is
  reported in `left_undone`. Include decor only when requested.
- Ground-only redo (`--layers ground`) retains trees/decor masks, ownership and
  completion (#217). A protected-mask conflict stops the redo; resolve the ground
  plan while preserving those layers.
- The explicit `world.json` player_uuid must be online. Before any undo, the
  wrapper checks the configured player's move to spectator hover looking down;
  it verifies that view again after all requested applies. Missing/offline player
  or failed movement/view stops with nonzero status. Direct layer CLIs retain
  their own existing behavior; these viewing checks belong to redo.
- After required undos, each layer is previewed and validated against that state,
  then its live apply re-plans and performs the existing journal/readback checks.
  It never silently retries, skips changed blocks, or rolls back the sequence.

On nonzero exit, read `failed`, `completed`, `left_undone`, and `pending` before
reporting what remains. An unfinished journal write uses
`python3 scripts/site_recover.py --world <world> status`; drift uses
`python3 scripts/cell_registry.py --world <world> reconcile tx,tz`. Later stages
stop after a conflict, mismatch, movement failure or interrupt. A successful
apply followed by a failed final view is still a failed redo; report landed
layers without claiming the view succeeded.

Done when exit is zero, final JSON has `ok: true`, every requested stage
succeeded, final hover was checked and the lock released. Report any cleared
but omitted decor explicitly. The wrapper cues success only then; cue failure
cannot turn a landed build into a failed apply.

## 3. Reapply (recorded feature builder)

Use the named feature, or `primary` from Locate for a player-standing request.
An ambiguous or absent primary needs a named feature before proceeding.
Inspect `features[].builder` from `redo_here`: kind, path, source and availability.
Only recorded provenance authorizes reapply; an inferred filename is not a record.
Reapply reruns the current builder with current inputs without redesign. It is
separate from the evidence-led feature redesign in §1 and does not promise exact
historical replay. Player hover belongs to the player-standing redesign workflow,
not this named reapply command.

```bash
python3 scripts/reapply_feature.py --world <world> <name>
python3 scripts/reapply_feature.py --world <world> <name> --apply
```

Inspect the dry run, then background the entire live command while retaining its
log and final status. Call directly: the wrapper holds one lock through builder
validation, undo, apply and journal outcome verification. Relative spec paths
belong to the selected world's checkout; absolute paths are supported. It never
migrates an invalid spec, adds `--overwrite`, or executes journal command text.

No recorded builder means use redo (redesign from evidence). Missing or invalid
specs need their reported problem resolved before undo; preflight failures do not
need interruption recovery. An undo failure stops rebuilding. After a successful
undo, any failed build reports the potentially undone/partially rebuilt state:
inspect `site_recover.py --world <world> status` for unfinished writes or the
registry's `reconcile` command for drift. No automatic rollback or retry.

Done when exit is zero and a live reapply has a new landed feature entry with
zero mismatches and its lock released. Dry-run success only validates the current
builder; it does not establish that a rebuild occurred.

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
