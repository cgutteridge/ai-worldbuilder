# Model bake-offs

Framework for comparing bulk-work models on place design/review (**one model per sitting**).

**Home:** [`model-bakeoffs/`](../model-bakeoffs/) — catalogue, config, prompt packs, and self-contained `rounds/R-NNN.json` records.

| Doc | What |
|-----|------|
| [model-bakeoffs/README.md](../model-bakeoffs/README.md) | Operator guide + CLI |
| [model-bakeoffs/MACRO-RESULTS.md](../model-bakeoffs/MACRO-RESULTS.md) | Skip-list of models failed on feature-design bake-off |
| [model-bakeoffs/PROTOCOL.md](../model-bakeoffs/PROTOCOL.md) | Agreed rules (tiers, scoring, ghost world) |

```bash
python3 scripts/model_bakeoff.py status
python3 scripts/model_bakeoff.py add-tool --id cursor --name Cursor
python3 scripts/model_bakeoff.py add-model --tool cursor --id composer --tier low --label Composer
python3 scripts/model_bakeoff.py open --tool cursor --model composer
# follow sitting_steps from model-bakeoffs/config.json (printed on the open card)
python3 scripts/model_bakeoff.py score R-001 --overall pass --axis massing=pass --axis keep=pass
python3 scripts/model_bakeoff.py usage R-001 --input 12000 --output 3000 --source cursor-ui
```

Sitting sequence: [`sitting_steps` in config.json](../model-bakeoffs/config.json). Rules / pad fairness: [PROTOCOL.md](../model-bakeoffs/PROTOCOL.md), [packs/v1/FAIRNESS.md](../model-bakeoffs/packs/v1/FAIRNESS.md). Skill: `model-bakeoff`.

Active tiers for now: **low** and **mid** only. High / non-bulk models are catalogued with `--no-bulk` and never scheduled.
