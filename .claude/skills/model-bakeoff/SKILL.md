---
name: model-bakeoff
description: >-
  Run or continue a model bake-off sitting (feature_design pilot). Use when the
  user says bake-off, model bake-off, open a round, score a round, or regenerate
  the bake-off ghost.
---

One model per sitting. Records under `model-bakeoffs/`. CLI: `python3 scripts/model_bakeoff.py`.

## Active world

Bake-offs use `worlds/bakeoff/` (Minecraft save `bakeoff`). Agent shells do not keep
exports between calls, so **prefix every** bake-off command with
`AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/bakeoff` (not a one-off `export`). Example:

```bash
AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/bakeoff python3 scripts/model_bakeoff.py status
AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/bakeoff python3 scripts/model_bakeoff.py open --tool cursor --model composer
```

`open` and `repair` refuse if the active world is not `bakeoff`.

## Before acting

1. Read `model-bakeoffs/PROTOCOL.md` and the pack brief + `packs/v1/FAIRNESS.md`.
2. Prefix as above, then `python3 scripts/model_bakeoff.py status` (or `show R-NNN`).

## Sitting sequence

1. **One apply owner** — if the parent takes over re-apply, interrupt the model subagent first.
2. Regenerate the ghost baseline with `lidar_ghost apply` (same world prefix). There is no region restore
   (ADR 004, task 40).
3. Confirm the world is open (MCP up). Ghost may look sparse around the cell, expected.
4. Claim lock; run only the catalogue tool/model for this round.
5. Evidence table → build → apply → fixed cameras → release lock.
6. Human interview one axis at a time → `score` → `usage` paste (or leave null).

Do not score without the interview. Do not invent token counts.
