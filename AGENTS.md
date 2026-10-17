# ai-worldbuilder: agent entry point

Reusable AI tool for building Minecraft worlds from real-place evidence.
Exactly one **active world** at a time (`active_world.py` / `AI_WORLDBUILDER_WORLD_ROOT`).

## Start

1. Confirm active world: `python3 -c "from active_world import WORLD_ROOT, gameworld_id; print(WORLD_ROOT, gameworld_id())"`.
2. System tracker first when work is **tool** engineering: read `tasks/index.json`.
3. If work is **world** (QUEUE, ISSUES, place builds), use the active checkout under `worlds/` — do not put world work in `tasks/`. If unclear tool vs world, **ask**.

## Always

- Place memory lives under the active world's `site/` and `config/` (inside `worlds/`), not in the tool tracker.
- Providers: on add, ask **local vs system** (below national → local). Skills: `evaluate-data-source`, `add-data-source`.
- World frame: active world `config/world-frame.json` via `scripts/world_frame.py`.
- Sound cues: `python3 ~/Projects/agent-soundboard/scripts/play.py <id>` (`done`, `need-you`, `error`).

## Where things live

| Topic | Home |
| --- | --- |
| Active world pointer | `active_world.py` |
| World checkouts | `worlds/<name>/` (gitignored; own git repos) |
| System tracker | `tasks/` |
| System providers | `config/system-providers.json` (code under `providers/<id>/` after #20) |
| Evidence types | `reference/TYPES.txt` |
| Shared scripts | `scripts/` (cross-provider glue; local provider CLIs are shims into the active world) |
| Craft / process rules | `rules/` (process, craftsmanship, foliage, layered passes, cleanup, …) |
| Skills | `.claude/skills/` |
| New world bootstrap | skill `new-world-build`, `templates/empty-world/`, `templates/new-world-site/` |

Local (place) provider tools live in the **active world** under `providers/<id>/`,
indexed by `config/local-providers.json` `package` fields. See that world’s
`providers/README.md`.
