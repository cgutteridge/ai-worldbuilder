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
([model-bakeoffs/PROTOCOL.md](../model-bakeoffs/PROTOCOL.md)).
