# Where things live

Read this when you need to find where a tool, config or record is kept. `AGENTS.md` only routes here.

| Topic | Home |
| --- | --- |
| Active world pointer | `active_world.py` |
| World checkouts | `worlds/<name>/` (gitignored; own git repos) |
| Minecraft instance + saves | standalone, outside this repo; `world.json` `save_path` references the save (read-only to the tool; only `scripts/new_blank_world.py` creates one); setup: [docs/minecraft-instance-setup.md](../minecraft-instance-setup.md), check: `scripts/check_instance.py` |
| System tracker | `tasks/`; project skill `worldbuilder-tasks` |
| Place memory | World `site/NOTES.txt` (read first), `site/REVIEWS.txt`, generated `site/COVERAGE.txt`; reference logging in `reference/LOGGING.txt`, assets indexed in `reference/manifest.jsonl` |
| World views | Optional `config/views.json` eye/look cameras; `scripts/site_views.py` writes `verification/site/views/` |
| System providers | `config/system-providers.json` + `providers/<id>/` (tool root) |
| World providers | active world `config/local-providers.json` + `providers/<id>/` |
| Provider CLIs | `shims/` (system + world-provider entry points); code under each `providers/` tree |
| Evidence types | `reference/TYPES.txt` |
| Place SOURCES | active world `reference/SOURCES.txt` (URLs / ToS / fetch how) |
| Shared libraries | `lib/<category>/` (bare imports via `script_env`; see [`lib/README.md`](../../lib/README.md)) |
| Feature building | `feature_build` orchestrates shell and roofs; `feature_spec`, `feature_roof_zones`, `feature_doors`, `feature_facade` and `feature_details` own the other planning seams. Spec reference: [`rules/feature-builds.md`](../../rules/feature-builds.md). |
| Block and geometry helpers | `site_kit.AIR_IDS` is the air base; domain filters name extras/exclusions. `block_store` owns block IDs and state properties; `surface_probe` owns plant recognition policies; `cellmath.segment_distance` owns segment projection. Repo commands use `scripts/shared_git.py` for git subprocesses. |
| CLI entry points | `scripts/` (agent/hook commands; often thin wrappers over `lib/`; see [`scripts/README.md`](../../scripts/README.md)) |
| Craft / process rules | `rules/` (process, craftsmanship, foliage, layered passes, cleanup, …) |
| Skills | `.agents/skills/` (canonical); `.claude/skills/` holds generated pointer stubs for Claude Code. After adding or renaming a skill run `node scripts/check-skill-stubs.mjs --write` |
| Closed model bake-off (historical) | `bakeoff/` ([OVERVIEW.md](../../bakeoff/OVERVIEW.md), [PROTOCOL.md](../../bakeoff/PROTOCOL.md)); skill `model-bakeoff` is historical. Default tests skip this tree (`python3 scripts/run_tests.py --bakeoff` opts in) |
| Lidar ghost v2 + dry-run reset | [docs/specs/lidar-ghost-v2-and-dryrun-reset.md](../specs/lidar-ghost-v2-and-dryrun-reset.md) |
| Accepted decisions | `docs/adr/` (ADR 006 is the land-use class design) |
| Archived plans, specs and audits | `docs/archive/` — history, not current guidance ([README](../archive/README.md)) |
| Decision record | `scripts/decision_record.py` (`evidence add`, `phase add`): per-phase references available / consulted → `registry/decisions/`, `registry/evidence/`; `site_pages.py` renders element, evidence and cell pages on every write |
| Cell registry | active world `registry/` + `scripts/cell_registry.py` (next / verify / reconcile; mess in a cell → `reconcile [--fix]`; interrupted apply → `site_recover.py`; ADR 004) |
| Dry-run reset | `scripts/dryrun_reset.py` (wipes regions + place memory; erases the registry so the journal starts fresh; also clears leftover retired progress files) |
| New world bootstrap | skill `new-world-build`, `templates/empty-world/`, `templates/new-world-site/` (no registry scaffold: `registry/` is created by the first journal write) |

Linked tool worktrees always use the main checkout's `worlds/` (their own copy is ignored to keep one catalogue); do not link or remove anything under `worlds/` to set up or clean up a tool worktree.

## Provider roots (dual)

Catalogue rows may set `package` (e.g. `providers/<id>`). Resolve it with
`world_provider` resolves optional entrance/building, tree-preservation and aerial-mosaic
integrations by catalogue role; see `docs/active-world-contract.md`.

`cell_sources.provider_package_dir(id)` / `script_env.resolve_provider_package`:

- **system** → `ai-worldbuilder/providers/<id>/` (`home: system`)
- **world** → `<active-world>/providers/<id>/` (`home: world`)

Merged load via `cell_sources.load_place_sources()` annotates each row with `home`.
See the active world’s `providers/README.md` for local modules.
