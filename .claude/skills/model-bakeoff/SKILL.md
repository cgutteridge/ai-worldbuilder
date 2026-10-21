---
name: model-bakeoff
description: >-
  Run or continue a model bake-off sitting (feature_design pilot). Use when the
  user says bake-off, model bake-off, open a round, score a round, or restore
  the bake-off ghost.
---

One model per sitting. Records under `model-bakeoffs/`. CLI: `python3 scripts/model_bakeoff.py`.

## Before acting

1. Read `model-bakeoffs/PROTOCOL.md` and the pack brief + `packs/v1/FAIRNESS.md`.
2. `python3 scripts/model_bakeoff.py status` (or `show R-NNN`).

## Sitting sequence

1. **One apply owner** — if the parent takes over restore/re-apply, interrupt the model subagent first.
2. Quit the Minecraft world (title / close save).
3. Restore into the bakeoff save (config `ghost_region_path`), not SouthamptonAI:
   `python3 scripts/model_bakeoff.py restore-ghost --confirm-logged-out`
   (or `region_backup.py restore <snap> --region <ghost_region_path> --confirm-logged-out`;
   list snaps via `region_backup.py list`).
4. Rejoin (MCP up). Ghost may look sparse around the cell — expected.
5. Claim lock; run only the catalogue tool/model for this round.
6. Evidence table → build → apply → fixed cameras → release lock.
7. Human interview one axis at a time → `score` → `usage` paste (or leave null).

Do not score without the interview. Do not invent token counts.
