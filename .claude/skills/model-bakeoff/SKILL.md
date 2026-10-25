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
Records under `model-bakeoffs/`. CLI: `python3 scripts/model_bakeoff.py`.

## Active world

Bake-offs use `worlds/bakeoff/` (Minecraft save `bakeoff`). Agent shells do not keep
exports between calls, so **prefix every** bake-off command with
`AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/bakeoff` (not a one-off `export`). Example:

```bash
AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/bakeoff python3 scripts/model_bakeoff.py status
AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/bakeoff python3 scripts/model_bakeoff.py open --tool cursor --model composer
```

`open` and `repair` refuse if the active world is not `bakeoff`.

vector_map footprints come from `worlds/bakeoff/reference/vector_map/` (same
default `OSMIndex` path as any world). Never pass `osm_root=` pointing at
`southampton-build` or another world.

## Before acting

1. Read `model-bakeoffs/PROTOCOL.md` and the pack brief + `packs/v1/FAIRNESS.md`.
2. Prefix as above, then `python3 scripts/model_bakeoff.py status` (or `show R-NNN`).

## Sitting sequence

Canonical steps live in `model-bakeoffs/config.json` (`sitting_steps`). `open` / `show`
render them onto the round checklist and sitting card — do not copy the list here.
World prefix as above; no region restore (ADR 004).

Do not score without the interview. Do not invent token counts.
