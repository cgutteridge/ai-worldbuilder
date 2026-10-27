# Designer and builder roles

Every **feature**, **trees**, or **decor** place task must name which model
**designs** and which model **builds**. Lidar ghosts are mechanical
(`lidar_ghost apply`) and do not use these roles.

## Defaults

Tool-repo defaults: [`config/build-roles.json`](../config/build-roles.json).

```bash
python3 scripts/build_roles.py show
python3 scripts/build_roles.py resolve --tool cursor --builder composer --json
```

Override per task (orchestrator / redo prompt) or bake-off `open --designer` /
`--model`. When the tool matches the config tool and designer is omitted, the
config designer is used. When the tool differs, omitted designer equals the
builder so both roles stay named without borrowing another host's models.

## Behaviour

| Situation | What happens |
| --- | --- |
| Designer id ≠ builder id | Two sessions: designer writes the evidence table + hand-off (no apply); builder is sole `SITE_OWNER` apply owner and does not redesign from scratch |
| Designer id = builder id | One session that still names both roles: evidence table first, then apply |
| Ghosts / ground ghost frontier | No AI roles — scripts only |

Hand-off artefacts:

- Non-house features → `site/builds/<name>.json` via `scripts/feature_build.py`
  ([feature-builds.md](feature-builds.md))
- Houses / terraces → house/terrace build path as today
- Trees / decor → evidence table, then `trees_layer` / `decor_layer` apply

## Designer questions (from aerial / street level)

The designer already reads aerial and street-level evidence before writing the
hand-off. Fold these into that pass — they are not a separate step:

- Storeys, wall material, openings and door type (as today)
- **Facade plan with entrances** ([feature-builds.md](feature-builds.md)).
  Write `facade_plan` per label. The brief lists every portal on the footprint
  with its position and photo when it has one — those are inputs, not a build
  stage. For each portal: place an entrance (face, leaf count, door block,
  canopy or covered area, steps, `floor_offset` against outside ground) **or**
  declare it unresolved with `provenance.unresolved_reason` (shared
  provenance schema). Record `provenance.source` / `confidence` /
  `evidence_ids` on every entrance. No portal register? Still add entrances
  from street-level photos (`photo_read`). Unresolved entrances stay in the
  plan and show on the cell view via their `cell` / `cells`.
- **Facades on every exposed face.** Prefer glazing under
  `facade_plan[label].faces[face].glazing` (same row shape as legacy
  `facades`). For each face with exposed wall (`N`/`S`/`E`/`W`), one row per
  storey the street photos and aerial show. Do not stop at the frontage.
  Where no evidence shows a face, copy the nearest face you did read and say
  so in the spec `notes` (e.g. "E copied from N — no street view"). Dry run
  lists every exposed face/storey glazed share and marks missing faces
  `no evidence read` ([feature-builds.md](feature-builds.md)).
- Roof form, colour and material — check the lidar profile before calling a roof flat
- Edge on the roof? what block? outer only or steps too?
  Default **none**: leave `roofs[label].edge` out unless the aerial (or a
  street-level cap) shows a parapet, coping or flat overhang fringe. When set,
  `{block, where: outer|all}` — `outer` skips zone steps; `all` edges every
  step ([feature-builds.md](feature-builds.md),
  [evidence-order.md](evidence-order.md)).
- **Roof and facade detail** ([building-review.md](building-review.md)). From
  the aerial sheet, list plant rooms, vents, louvred vents, skylights, solar
  panels and lift overruns that read at street scale; from street photos, list
  drainpipes, signs, ledges and fascia bands (entrance canopy / covered area
  lives on the facade_plan entrance, not as a free-floating detail). Write
  each into the spec `details` table (`kind`, `at`, `size`, `source`; facade
  rows need `face`). Heights of large roof plant from lidar; XY from the
  aerial / photo. Omit `details` (or leave it empty) only when none are
  visible — say so in `notes`.

Bake-off sittings use the same defaults on `open` when `--designer` is omitted;
hybrid scoring and ghost baseline stay bake-off-only
([bakeoff/PROTOCOL.md](../bakeoff/PROTOCOL.md)).

## Guard refusals and what a builder may change

The offline plan step (`feature_build.build`, `house_build.build`) runs the same
door-frame and roof-ridge guards as the live apply (`site_kit.guard_door_frames`,
`guard_roof_jogs`), with the same refusal text (feature and house builds; layer passes such as trees/decor/ground only meet the guards at the live apply). The designer must get a clean
offline plan before hand-off; a refusal there is the designer's to fix.

If a guard still refuses in the builder's live dry run or apply:

- The builder may change **only the refused element** (the door and its
  jambs/lintel, or the ridge that jogs). Nothing else in the designer's plan
  moves; anything wider goes back to the designer.
- Record it in the hand-off artefact (the `site/builds/<name>.json` spec or the
  house/terrace/layer hand-off) and in the task notes: which guard, which
  element, what was changed, and why the offline plan did not catch it.
- A real exception (free-standing door, genuine offset roof) is an explicit
  `allow_unframed_doors` / `roof_jog_ok` reason, never a silent edit.
