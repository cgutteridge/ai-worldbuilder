# Model bake-offs

Framework for comparing bulk-work models on place design/review (**one model per sitting**).

**Home:** [`model-bakeoffs/`](../model-bakeoffs/) — catalogue, config, prompt packs, and self-contained `rounds/R-NNN.json` records.

| Doc | What |
|-----|------|
| [model-bakeoffs/README.md](../model-bakeoffs/README.md) | Operator guide + CLI |
| [model-bakeoffs/PROTOCOL.md](../model-bakeoffs/PROTOCOL.md) | Agreed rules (tiers, scoring, ghost world) |

```bash
python3 scripts/model_bakeoff.py status
python3 scripts/model_bakeoff.py add-tool --id cursor --name Cursor
python3 scripts/model_bakeoff.py add-model --tool cursor --id composer --tier low --label Composer
python3 scripts/model_bakeoff.py open --tool cursor --model composer
# the ghost baseline is regenerated with `lidar_ghost apply` (no restore, ADR 004;
# the no-restore sitting flow is pending task 48)
# apply, interview…
python3 scripts/model_bakeoff.py score R-001 --overall pass --axis massing=pass --axis keep=pass
python3 scripts/model_bakeoff.py usage R-001 --input 12000 --output 3000 --source cursor-ui
```

Sitting sequence and pad fairness: [model-bakeoffs/PROTOCOL.md](../model-bakeoffs/PROTOCOL.md), [packs/v1/FAIRNESS.md](../model-bakeoffs/packs/v1/FAIRNESS.md). Skill: `model-bakeoff`.

Active tiers for now: **low** and **mid** only. High / non-bulk models are catalogued with `--no-bulk` and never scheduled.
