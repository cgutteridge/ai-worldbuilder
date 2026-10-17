---
name: redo
description: >-
  Redesign from scratch at the player's feet: a built feature, or cell trees /
  decor / ground. Use when the user says redo, redo trees, redo decor, redo
  ground, start over, or rebuild from the evidence table while standing somewhere.
---

Player-standing redesign. No confirmation pause. No `human-confirm-fix` (they are already there). Claim the lock, apply, release.

## Branch

| User says | Target |
| --- | --- |
| `redo` / start over / rebuild (no layer word) | **Feature** covering their `x,z` |
| `redo trees` | **Trees** for the cell under their feet |
| `redo decor` | **Decor** for that cell |
| `redo ground` | **Ground** for that cell (mask-clear trees/decor first; do not redesign them) |

## 0. Locate

1. `player_list_online` → player `x,y,z` (floor to blocks).
2. `.venv/bin/python scripts/redo_here.py --at <x>,<z> --y <y> --json`
3. Read `cell`, `primary`, `features`, `masks`.

Done when the JSON is in hand.

## Hover (every branch)

Always put the player above the target, looking straight down — before the first apply, and again after the last apply if their position changed (e.g. `site_kit.apply` moved them for chunk load).

1. Centre: feature footprint midpoint, or cell centre (`tx*128+64`, `tz*128+64`).
2. Height: top of the target + ~20 (feature top from its cells / lidar; cell: surface + 40 is fine).
3. `entity_teleport` to that `x,y,z` with `facing` a point on the ground at the same `x,z` (look straight down). Spectator or creative.
4. Do not restore their old spot.

Done when they are hovering and looking down at the target.

## 1. Feature (`redo`)

1. **No feature** (`features` empty): tell the user there is no built feature at this column. Stop.
2. **Ambiguous** (`primary` null, several `features`): tell them the names and to stand inside the one they want. Stop.
3. Claim lock (`scripts/site_lock.py claim <owner> "redo <name>"`).
4. **Hover** (above).
5. **Evidence table first** (`rules/building-review.md` B57 / reflection step 1). Do **not** read the old build’s placed blocks or the old script’s material/geometry choices as reference. Keep only the re-run mechanism. Rewrite `scripts/<script>` from the table. Every row below must be filled in the script docstring before planning:

   | Fact | Source |
   | --- | --- |
   | Footprint | OSM (+ university outline if campus) |
   | Heights / roof levels | lidar (`lidar_parts` / grid), not Google |
   | Walls, openings, doors | portal + building photos (`portals.py`, `buildings.py`) |
   | Roof colour + **greeblies** | Google sheet (`google_aerial.py` / `google_window.py`) |

   **Greeblies are mandatory** (`site/CRAFTSMANSHIP.txt` GREEBLIES; building-review). From the Google sheet, list every plant box, vent, louvre, skylight, solar panel, lift overrun and parapet worth reading at street scale — or write `greeblies: none visible`. A shell-only plan is not a finished redo. Place them with `campus.roof_greeblies` in the same script (same apply, or `--greeblies` before unlock). Heights of plinths/plant from lidar; colours and XY from Google.

   Google viewing: browser may refuse a new `file://` path — overwrite a sheet filename already opened under `instance/google-aerial/`, or serve that dir over `http://127.0.0.1` and open the http URL. Not seeing the sheet is not a reason to skip greeblies; stop and fix viewing.

6. Dry run (shell + greeblies), then `SITE_OWNER=<owner> .venv/bin/python scripts/<script> --apply` (and `--greeblies` if split). Readback 0.
7. **Hover** again if the player moved during apply.
8. Release lock. Cue `party-cheer`.

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

## Guards

- One builder: never apply without a fresh `SITE_OWNER` lock.
- Fail closed on mask conflicts / readback mismatches (`site_recover.py` if a stage is left applying).
- Feature redesign never uses the previous build as evidence.
- Moving the player is allowed for hover (this skill); end cue is always `party-cheer` (`yay` is broken).
