# Model bake-offs

Framework for comparing **bulk-work** models on place design/review — **one model per sitting**, records that stand alone so you can stop and resume anytime.

Protocol detail: [PROTOCOL.md](PROTOCOL.md). Short pointer from docs: [../docs/model-bakeoffs.md](../docs/model-bakeoffs.md).

**Feature-selection smoke checks** (not A/B): [checks/feature-selection/](checks/feature-selection/) — Composer paper-only “which way / exclusive bounds”.

## Shape

| Path | Role |
|------|------|
| `catalogue.json` | Tools you use + models available when you have tokens (`low` / `mid` / `high`) |
| `config.json` | Cell, pack, active tiers (`low`+`mid` only for now), interview axes |
| `packs/v1/` | Frozen prompt brief |
| `rounds/R-NNN.json` | **One sitting** — self-contained (tool, model, brief path, cell, checklist, interview, grades) |
| `MACRO-RESULTS.md` | Skip-list: models failed on feature-design bake-off (do not re-try for place build) |
| `checks/feature-selection/` | Paper-only feature pick + bounds (Composer smoke; not the ladder) |

`high` models and anything with `bulk_ok: false` are never scheduled (too dear for bulk).

## Ladder

1. Register tools/models you can actually pick.
2. Run **all low** bulk models for the pilot task (feature design), one sitting each.
3. Open **mid** only if **any** low cold score was not `great`.
4. Optional repair attempts 2–3 on a scored fail/pass round.

## One sitting

```bash
# once per tool/model you have access to
python3 scripts/model_bakeoff.py add-tool --id cursor --name Cursor
python3 scripts/model_bakeoff.py add-model --tool cursor --id composer-2.5 --tier low --label "Composer 2.5"
python3 scripts/model_bakeoff.py add-model --tool cursor --id sonnet --tier mid --label "Claude Sonnet"
python3 scripts/model_bakeoff.py add-model --tool cursor --id opus --tier high --label "Claude Opus" --no-bulk

python3 scripts/model_bakeoff.py status
python3 scripts/model_bakeoff.py next
python3 scripts/model_bakeoff.py open --tool cursor --model composer-2.5
# regenerate the ghost baseline with `lidar_ghost apply` (no region restore, ADR 004) → apply
python3 scripts/model_bakeoff.py score R-001 --mech-gate pass --overall pass \
  --axis massing=pass --axis openings=pass --axis roof_greeblies=pass \
  --axis materials=pass --axis keep=pass --why "ridge a bit low"

# token/cost after the sitting (paste from Cursor usage UI when you can)
python3 scripts/model_bakeoff.py usage R-001 --input 12000 --output 3000 --source cursor-ui

# optional second attempt same model
python3 scripts/model_bakeoff.py repair R-001 --brief "raise ridge one block"
```

Each `open` writes a full round file; you do not need other rounds in context to run or score it.
