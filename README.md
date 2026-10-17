# ai-worldbuilder

Reusable AI tool for building and refining Minecraft worlds from real-place evidence.

One checkout can address many world repos under gitignored `worlds/`; exactly one world is **active**, selected by `active_world.py` (or env `AI_WORLDBUILDER_WORLD_ROOT`).

System tracker: `tasks/`. This repo has no world QUEUE/ISSUES.

## Active world

```bash
python3 -c "from active_world import WORLD_ROOT, gameworld_id; print(WORLD_ROOT); print(gameworld_id())"
python3 scripts/world_frame.py info
```

Contract: `docs/active-world-contract.md`. First world checkout: `worlds/southampton-build`.

## Layout

- `active_world.py` — importable active-world pointer
- `worlds/` — nested world git repos (gitignored; see `worlds/README.md`)
- `scripts/` — shared tooling (reads `WORLD_ROOT` for place files)
- `config/system-providers.json` — nationwide+ feeds
- `reference/TYPES.txt` — evidence types
- `rules/`, `.claude/skills/`, `templates/` — shared craft and agent skills
- `tasks/` — system tracker
