# ai-worldbuilder: agent entry point

Reusable AI tool for building Minecraft worlds from real-place evidence.
Exactly one **active world** at a time (`active_world.py` / `AI_WORLDBUILDER_WORLD_ROOT`).

## Start

1. Confirm active world: `python3 -c "from active_world import WORLD_ROOT, gameworld_id; print(WORLD_ROOT, gameworld_id())"`.
2. Sweep unmerged branches and leftover worktrees: `python3 scripts/ship_branch.py sweep` (ADR 005).
3. System tracker first when work is **tool** engineering: read `tasks/index.json`. Parallel system tasks: project skill `parallel-task-wave` (not the world cell-orchestrator).
4. If work is **world** (cell registry, issues, place builds), use the active checkout under `worlds/` — do not put world work in `tasks/`. If unclear tool vs world, **ask**.

## Always

- Place memory lives under the active world's `site/`, `registry/`, and `config/` (inside `worlds/`), not in the tool tracker. Progress SoT is `registry/` via `scripts/cell_registry.py`.
- Providers: on add, ask **local vs system** (below national → local). Skills: `evaluate-data-source`, `add-data-source`.
- World frame: active world `config/world-frame.json` via `scripts/world_frame.py`.
- Sound cues: `python3 ~/Projects/agent-soundboard/scripts/play.py <id>` (`done`, `need-you`, `error`).
- Once per clone: `python3 scripts/install_git_hooks.py` (blocks system-tracker edits on `task/*` branches; runs `scripts/run_tests.py`).
- Review judgement: `CODING_STANDARDS.md` (mechanical rules live in the hook).
- **Branch gate (ADR 005):** code goes on `task/*` branches and through the ship-branch skill / `ship_branch.py merge` unless the owner opts out. Tracker-only and docs-only edits commit straight to `main` under the pre-commit checks — no `tracker/*` branches.

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
| Model bake-offs (design/review tiers) | `model-bakeoffs/` + `scripts/model_bakeoff.py` + skill `model-bakeoff` ([docs/model-bakeoffs.md](docs/model-bakeoffs.md)); ghost baseline: regenerate with `lidar_ghost apply` (no restore, see ADR 004) |
| Lidar ghost v2 + dry-run reset | [docs/specs/lidar-ghost-v2-and-dryrun-reset.md](docs/specs/lidar-ghost-v2-and-dryrun-reset.md), [docs/plans/southampton-dryrun-001.md](docs/plans/southampton-dryrun-001.md) |
| Cell registry | active world `registry/` + `scripts/cell_registry.py` (next / verify / reconcile; mess in a cell → `reconcile [--fix]`; interrupted apply → `site_recover.py`; ADR 004) |
| Dry-run reset | `scripts/dryrun_reset.py` (wipes regions + place memory; erases the registry so the journal starts fresh; also clears leftover retired progress files) |
| New world bootstrap | skill `new-world-build`, `templates/empty-world/`, `templates/new-world-site/` (no registry scaffold: `registry/` is created by the first journal write) |

### Provider roots (dual)

Catalogue rows may set `package` (e.g. `providers/<id>`). Resolve it with
`cell_sources.provider_package_dir(id)` / `script_env.resolve_provider_package`:

- **system** → `ai-worldbuilder/providers/<id>/` (`home: system`)
- **world** → `<active-world>/providers/<id>/` (`home: world`)

Merged load via `cell_sources.load_place_sources()` annotates each row with `home`.
See the active world’s `providers/README.md` for local modules.
