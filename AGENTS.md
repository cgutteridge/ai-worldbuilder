# ai-worldbuilder: agent entry point

Reusable AI tool for building Minecraft worlds from real-place evidence.
Exactly one **active world** at a time (`active_world.py` / `AI_WORLDBUILDER_WORLD_ROOT`).

## Start

1. Confirm active world: `python3 -c "from active_world import WORLD_ROOT, gameworld_id; print(WORLD_ROOT, gameworld_id())"`.
2. System tracker first when work is **tool** engineering: read `tasks/index.json`. Parallel system tasks: shared skill `parallel-task-wave` (not the world cell-orchestrator).
3. If work is **world** (QUEUE, ISSUES, place builds), use the active checkout under `worlds/` — do not put world work in `tasks/`. If unclear tool vs world, **ask**.

## Always

- Place memory lives under the active world's `site/` and `config/` (inside `worlds/`), not in the tool tracker.
- Providers: on add, ask **local vs system** (below national → local). Skills: `evaluate-data-source`, `add-data-source`.
- World frame: active world `config/world-frame.json` via `scripts/world_frame.py`.
- Sound cues: `python3 ~/Projects/agent-soundboard/scripts/play.py <id>` (`done`, `need-you`, `error`).
- Once per clone: `python3 scripts/install_git_hooks.py` (blocks system-tracker edits on `task/*` branches; runs `scripts/run_tests.py`).
- Review judgement: `CODING_STANDARDS.md` (mechanical rules live in the hook).

## Where things live

| Topic | Home |
| --- | --- |
| Active world pointer | `active_world.py` |
| World checkouts | `worlds/<name>/` (gitignored; own git repos) |
| System tracker | `tasks/` |
| System providers | `config/system-providers.json` + `providers/<id>/` (tool root) |
| World providers | active world `config/local-providers.json` + `providers/<id>/` |
| Provider CLIs | `shims/` (system) and `scripts/` shims (world); code under each `providers/` tree |
| Evidence types | `reference/TYPES.txt` |
| Place SOURCES | active world `reference/SOURCES.txt` (URLs / ToS / fetch how) |
| Shared scripts | `scripts/` (cross-provider glue) |
| Craft / process rules | `rules/` (process, craftsmanship, foliage, layered passes, cleanup, …) |
| Skills | `.claude/skills/` |
| New world bootstrap | skill `new-world-build`, `templates/empty-world/`, `templates/new-world-site/` |

### Provider roots (dual)

Catalogue rows may set `package` (e.g. `providers/ukdefra-lidar`). Resolve it with
`cell_sources.provider_package_dir(id)` / `script_env.resolve_provider_package`:

- **system** → `ai-worldbuilder/providers/<id>/` (`home: system`)
- **world** → `<active-world>/providers/<id>/` (`home: world`)

Merged load via `cell_sources.load_place_sources()` annotates each row with `home`.
See the active world’s `providers/README.md` for local modules.
