# Feature-selection checks (smoke)

Not an A/B bake-off. One model (default: **Composer**) answers paper-only prompts: **which OSM feature**, **who to reject**, **exclusive bounds with no fringe**.

| Path | Role |
|------|------|
| `scenarios/FS-NNN.json` | Cue + allowed tools (safe to show the agent) |
| `keys/FS-NNN.json` | Gold answers (**do not open in the sitting chat**) |
| `runs/` | Optional answer dumps from a sitting |

```bash
python3 bakeoff/feature_selection_check.py list
python3 bakeoff/feature_selection_check.py brief FS-001
# …new Cursor chat, model = Composer only, paste the brief, get JSON answer…
python3 bakeoff/feature_selection_check.py grade FS-001 --answer path/to/answer.json
```

Protocol: [PROTOCOL.md](PROTOCOL.md).
