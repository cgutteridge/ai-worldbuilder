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
| `redo ground` | **Ground** for that cell (mask-clear trees/decor first; do not redesign them) |

## 0. Locate

1. `player_list_online` → player `x,y,z` (floor to blocks).
2. `python3 scripts/redo_here.py --world <world> --at <x>,<z> --y <y> --json`
3. Read `cell`, `primary`, `features`, `masks`.

Done when the JSON is in hand.

## Hover (every branch)

Always put the player above the target, looking straight down — before the first apply, and again after the last apply if their position changed (e.g. `site_kit.apply` moved them for chunk load).

1. Centre: feature footprint midpoint, or cell centre (`tx*128+64`, `tz*128+64`).
2. Height: top of the target + ~20 (feature top from its cells / lidar; cell: surface + 40 is fine).
3. Move with the required-why CLI (not raw `entity_teleport`). `--why` names the target, e.g. hovering over the feature or cell for the redo. Face a point on the ground at the same `x,z` (look straight down). Spectator or creative:

   ```bash
   python3 scripts/mc_player.py move \
     --why "Hovering over oakdene_court so you can see the redo" \
     --pos x,y,z --facing x,y,z --gamemode spectator
   ```

4. Do not restore their old spot.

Done when they are hovering and looking down at the target.

## 1. Feature (`redo`)

1. **No feature** (`features` empty): tell the user there is no built feature at this column. Stop.
2. **Ambiguous** (`primary` null, several `features`): tell them the names and to stand inside the one they want. Stop.
3. Resolve build roles (`python3 scripts/build_roles.py show` or overrides). Name designer + builder in the session before applying.
4. Claim lock (`scripts/site_lock.py claim <owner> "redo <name>"`) — builder role only when models differ (designer must not apply).
5. **Hover** (above).
6. **Evidence table first** (`rules/building-review.md` B57 / reflection step 1). Do **not** read the old build’s placed blocks or the old script’s material/geometry choices as reference. Keep only the re-run mechanism. Rewrite `scripts/<script>` (or `site/builds/<name>.json` for `feature_build`) from the table. Every row below must be filled in the script docstring before planning:

   | Fact | Source |
   | --- | --- |
   | Footprint | vector map (+ university outline if campus) |
   | Heights / roof levels | lidar (`lidar_parts` / grid), not aerial colour reads |
   | Walls, openings, doors | portal + building photos → `facade_plan` entrances (`portals.py`, `buildings.py`) |
   | Roof colour + **greeblies** | aerial sheet (`google_aerial.py` / `google_window.py`) |

   **Greeblies are mandatory** (`rules/craftsmanship.md` GREEBLIES; building-review). From the aerial sheet, list every plant box, capped box vent, louvre, recessed skylight, hollow industrial chimney, solar panel, lift overrun and parapet worth reading at street scale — or write `greeblies: none visible` in the spec `notes`. A shell-only plan is not a finished redo. For `feature_build`, put them in the spec `details` table (`rules/feature-builds.md`); they rebuild with the building. Heights of plinths/plant from lidar; colours and XY from the aerial sheet. Skylight at means roof-top Y and its entire box must be on roof; it replaces one layer. Vents are concrete boxes with trapdoor caps. Chimneys are hollow open rings, round or square and optionally tapered: size from photos and explicit spec dimensions, with lidar only a rough warning. Facade greeblies (drainpipe, sign, canopy, ledge, fascia) come from street photos into the same table.

   Aerial sheet: run `google_aerial.py sheet` / `google_window.py` and `Read` the PNG under `instance/google-aerial/`. Not seeing the sheet is not a reason to skip greeblies; stop and fix the download.

   If designer ≠ builder: stop after the table + hand-off artefact; builder continues from the next step.
7. **Readiness check, then clear the old build** (`rules/build-workflow.md` step 3: journal clean, ground and door-front levels match lidar, dry run on the current world). Do not undo before it passes. **Clear the old build first.** If the feature has a landed journal entry (it was built before), `SITE_OWNER=<owner> python3 scripts/element_undo.py --world <world> <name> --apply` (via `site_lock.py run`/the claimed lock) *before* applying. A plain re-apply only writes the new plan's cells, so old roof, stair and edit blocks outside it survive. Skip only when `redo_here` shows no landed entry for the plan.
8. Dry run (shell + `details`), then `SITE_OWNER=<owner> python3 scripts/<script> --apply` (or `feature_build.py site/builds/<name>.json --apply`). Readback 0.
9. **Hover** again if the player moved during apply.
10. Release lock. Cue `party-cheer`.

Done when readback is clean, the docstring has a greeblie list (or explicit none), greeblies are applied, they are hovering looking down, and the lock is released. Say what was rebuilt (name + script).

## 2. Trees (`redo trees`)

Cell = `redo_here` `cell`. Claim lock `"redo trees <tx>,<tz>"`. **Hover** (above).

1. If `masks.decor`: `SITE_OWNER=… mask_undo.py --cell tx,tz --pass decor --apply` (clear only; do **not** re-apply decor).
2. If `masks.trees`: `mask_undo.py --cell tx,tz --pass trees --apply`.
3. `trees_layer.py --cell tx,tz` dry run, then `--apply`.
4. **Hover** again if moved. Release lock. Cue `party-cheer`.

Done when trees readback is clean. Decor stays cleared if it was undone.

## 3. Decor (`redo decor`)

Claim lock `"redo decor <tx>,<tz>"`. **Hover** (above).

1. If `masks.decor`: `mask_undo.py --cell tx,tz --pass decor --apply`.
2. `decor_layer.py --cell tx,tz` dry run, then `--apply`.
3. **Hover** again if moved. Release lock. Cue `party-cheer`.

Done when decor readback is clean.

## 4. Ground (`redo ground`)

Clear masks so ground can write; **do not redesign** trees or decor afterward.

Claim lock `"redo ground <tx>,<tz>"`. **Hover** (above).

1. If `masks.decor`: `mask_undo.py --cell tx,tz --pass decor --apply`.
2. If `masks.trees`: `mask_undo.py --cell tx,tz --pass trees --apply`.
3. `ground_layer.py --cell tx,tz` dry run, then `--apply`.
4. **Hover** again if moved. Release lock. Cue `party-cheer`.

Done when ground readback is clean. Trees/decor remain undone until an explicit `redo trees` / `redo decor`.

## Build roles (every redo)

Before designing or applying a feature / trees / decor redo, name designer and
builder (defaults: `config/build-roles.json`; see `rules/build-roles.md`):

```bash
python3 scripts/build_roles.py show
```

Same model for both → one session (evidence table, then apply). Different
models → designer session first (table + hand-off, no apply), then builder
session (apply only). Feature hand-off is `site/builds/<name>.json` via
`feature_build` when the build fits that path. Ghosts are not part of redo AI
roles.

## Guards

- One builder: never apply without a fresh `SITE_OWNER` lock.
- Fail closed on mask conflicts / readback mismatches (`site_recover.py` if a journal entry is left `begun`; `reconcile` if blocks drifted).
- Feature redesign never uses the previous build as evidence.
- Moving the player is allowed for hover (this skill); end cue is always `party-cheer` (`yay` is broken).
