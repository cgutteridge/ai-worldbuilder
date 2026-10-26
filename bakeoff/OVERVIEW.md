# Model bake-offs

> **Closed 2026-10-26.** The current `config/build-roles.json` (Opus 5.5 low designs, Sonnet 5.5 medium builds) is the final choice. No further sittings are planned; this folder is kept as the record.

Framework for comparing bulk-work models on place design/review. Each sitting
names a **designer** and a **builder** (defaults: `config/build-roles.json`;
same id = one session; different ids = hybrid).

**Home:** this folder (`bakeoff/`) — catalogue, config, prompt packs, CLI, skill,
and self-contained `rounds/R-NNN.json` records. The Minecraft checkout stays at
`worlds/bakeoff/` (not under this folder).

| Doc | What |
|-----|------|
| [README.md](README.md) | Operator guide + CLI |
| [MACRO-RESULTS.md](MACRO-RESULTS.md) | Skip-list of models failed on feature-design bake-off |
| [PROTOCOL.md](PROTOCOL.md) | Agreed rules (tiers, scoring, ghost world) |

```bash
python3 bakeoff/model_bakeoff.py --world bakeoff status
python3 bakeoff/model_bakeoff.py --world bakeoff add-tool --id cursor --name Cursor
python3 bakeoff/model_bakeoff.py --world bakeoff add-model --tool cursor --id composer --tier low --label Composer
python3 bakeoff/model_bakeoff.py --world bakeoff open --tool cursor --model composer
# follow sitting_steps from bakeoff/config.json (printed on the open card)
python3 bakeoff/model_bakeoff.py --world bakeoff score R-001 --overall pass --axis massing=pass --axis keep=pass
python3 bakeoff/model_bakeoff.py --world bakeoff usage R-001 --input 12000 --output 3000 --source cursor-ui
```

Sitting sequence: [`sitting_steps` in config.json](config.json). Rules / pad fairness: [PROTOCOL.md](PROTOCOL.md), [packs/v1/FAIRNESS.md](packs/v1/FAIRNESS.md). Skill: `bakeoff/skills/model-bakeoff` (Claude discovers via `.claude/skills/model-bakeoff` symlink).

Active tiers for now: **low** and **mid** only. High / non-bulk models are catalogued with `--no-bulk` and never scheduled.
