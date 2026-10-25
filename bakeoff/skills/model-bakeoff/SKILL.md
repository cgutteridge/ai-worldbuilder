---
name: model-bakeoff
description: >-
  Run or continue a model bake-off sitting (feature_design pilot). Use when the
  user says bake-off, model bake-off, open a round, score a round, or regenerate
  the bake-off ghost.
---

Every sitting names designer + builder (`open --model <builder> [--designer …]`).
Defaults from `config/build-roles.json` when `--designer` is omitted (see
`rules/build-roles.md`). Different ids → hybrid sitting; same id → one session.
Records under `bakeoff/`. CLI: `python3 bakeoff/model_bakeoff.py`.

## Active world

Bake-offs use `worlds/bakeoff/` (Minecraft save `bakeoff`). Agent shells do not keep
exports between calls, so **prefix every** bake-off command with
`AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/bakeoff` (not a one-off `export`). Example:

```bash
AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/bakeoff python3 bakeoff/model_bakeoff.py status
AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/bakeoff python3 bakeoff/model_bakeoff.py open --tool cursor --model composer
```

`open` and `repair` refuse if the active world is not `bakeoff`.

vector_map footprints come from `worlds/bakeoff/reference/vector_map/` (same
download → cache → project reference story as any world; see
`scripts/vector_map_clips.py`). Never pass `osm_root=` pointing at
`southampton-build` or another world. If clips are missing, copy a populated
`reference/vector_map/` into bakeoff (real files) — recovery only, not a live
symlink or cross-world read.

## Before acting

1. Read `bakeoff/PROTOCOL.md` and the pack brief + `bakeoff/packs/v1/FAIRNESS.md`.
2. Prefix as above, then `python3 bakeoff/model_bakeoff.py status` (or `show R-NNN`).

## Sitting sequence

Canonical steps live in `bakeoff/config.json` (`sitting_steps`). `open` / `show`
render them onto the round checklist and sitting card — do not copy the list here.
World prefix as above; no region restore (ADR 004).

Do not score without the interview. Do not invent token counts.
