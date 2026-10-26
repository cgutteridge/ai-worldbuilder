# ai-worldbuilder: agent entry point

Reusable AI tool for building Minecraft worlds from real-place evidence.
Exactly one **active world** at a time (`active_world.py` / `AI_WORLDBUILDER_WORLD_ROOT`).

## Start

1. Set the active world (required; there is no silent default): `export AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/southampton-build` (or another checkout under `worlds/`). Confirm: `python3 -c "from active_world import WORLD_ROOT, gameworld_id; print(WORLD_ROOT, gameworld_id())"`.
2. Sweep unmerged branches and leftover worktrees: `python3 scripts/ship_branch.py sweep` (ADR 005).
3. System tracker first when work is **tool** engineering: read `tasks/index.json`. Parallel system tasks: project skill `parallel-task-wave` (not the world cell-orchestrator).
4. If work is **world** (cell registry, issues, place builds), use the active checkout under `worlds/` — do not put world work in `tasks/`. If unclear tool vs world, **ask**.

## Always

- Place memory lives under the active world's `site/`, `registry/`, and `config/` (inside `worlds/`), not in the tool tracker. Progress SoT is `registry/` via `scripts/cell_registry.py`.
- Providers: on add, ask **local vs system** (below national → local). Skills: `evaluate-data-source`, `add-data-source`.
- World frame: active world `config/world-frame.json` via `scripts/world_frame.py` (canonical CRS / MC / cell / XYZ). Preferred XYZ CLI: `to-wm` / `from-wm` / `batch-wm` — logical 256-px `east_px` (right) and `south_px` (down) from the tile NW corner; lat/lon needs no world; MC needs `--frame` or the active world. Do not reimplement slippy-map maths elsewhere. Tile-image fetch / evidence sheets (`google_aerial sheet`, etc.) stay separate.
- Live-world commands (baseline, `--apply`, capture): `python3 scripts/site_lock.py run <owner> "<task>" -- <cmd>` claims the lock, sets `SITE_OWNER`, and always releases (even on failure/interrupt). Prefer it to claim/release by hand.
- Slow commands (baseline over 10 min, `--apply` ~2 min, vector-map load 1-2 min): run in the background from the start; typical times in [rules/slow-commands.md](rules/slow-commands.md).
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
| Provider CLIs | `shims/` (system + world-provider entry points); code under each `providers/` tree |
| Evidence types | `reference/TYPES.txt` |
| Place SOURCES | active world `reference/SOURCES.txt` (URLs / ToS / fetch how) |
| Shared libraries | `lib/<category>/` (bare imports via `script_env`; see [`lib/README.md`](lib/README.md)) |
| CLI entry points | `scripts/` (agent/hook commands; often thin wrappers over `lib/`; see [`scripts/README.md`](scripts/README.md)) |
| Craft / process rules | `rules/` (process, craftsmanship, foliage, layered passes, cleanup, …) |
| Skills | `.claude/skills/` |
| Model bake-offs (design/review tiers) | `bakeoff/` + `bakeoff/model_bakeoff.py` + skill `model-bakeoff` ([bakeoff/OVERVIEW.md](bakeoff/OVERVIEW.md)); ghost baseline: regenerate with `lidar_ghost apply` (no restore, see ADR 004) |
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
