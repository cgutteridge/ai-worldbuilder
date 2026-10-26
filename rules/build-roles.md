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

Bake-off sittings use the same defaults on `open` when `--designer` is omitted;
hybrid scoring and ghost baseline stay bake-off-only
([bakeoff/PROTOCOL.md](../bakeoff/PROTOCOL.md)).

## Guard refusals and what a builder may change

The offline plan step (`feature_build.build`, `house_build.build`) runs the same
door-frame and roof-ridge guards as the live apply (`site_kit.guard_door_frames`,
`guard_roof_jogs`), with the same refusal text. The designer must get a clean
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
