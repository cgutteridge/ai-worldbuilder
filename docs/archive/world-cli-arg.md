# Required `--world` instead of env

Tracker: **#103 Replace AI_WORLDBUILDER_WORLD_ROOT with required `--world` CLI**.
Design locked 2026-10-26. Implement on `task/world-cli-arg` (stack on `task/scripts-lib-split` if that branch is still unmerged).

## Decisions (locked)

- **No default, no env fallback.** Place-touching commands must get `--world <name>` where `<name>` is a directory under `worlds/` (e.g. `southampton-build`, `bakeoff`).
- **World-free CLIs** (no `--world`): `scripts/run_tests.py`, `scripts/ship_branch.py`, `scripts/pre_commit_checks.py`, `scripts/install_git_hooks.py`.
- Minecraft `save_path` / `region_path` stay in each world's `config/world.json` (see `docs/active-world-contract.md`); CLIs do not take a separate save path.

## Core seam (`active_world.py`)

Stop resolving the world at import time.

- `WORLD_ROOT: Path | None = None` until selected.
- `set_world(name: str) -> Path` — resolve `TOOL_ROOT / "worlds" / name` (must exist, no `..`); set `WORLD_ROOT`; refuse unknown names with a short list of `worlds/*` dirs.
- `set_world_root(path: Path) -> Path` — absolute checkout for tests / disposable worlds only (not a CLI flag).
- `world_root() -> Path` — return `WORLD_ROOT` or raise: `no world selected; pass --world <name> (a directory under worlds/)`.
- Remove all reads of `AI_WORLDBUILDER_WORLD_ROOT` from production code (keep a one-line note in the contract that the env is retired).

`scripts/script_env.py`: do **not** bind `ROOT` from env at import. After `set_world` / `set_world_root`, call `script_env.bind_world()` to set `ROOT` and `WORLD_PROVIDERS`. Add helpers:

- `add_world_arg(parser)` — required `--world`
- `activate_world_from_args(args)` — `set_world` + `bind_world` + clear dependent caches (`world_frame`, `site_kit` region/config, `mc_paced` if cached)

Eager modules that today do `REGION = Path(_CFG['region_path'])` at import (`lib/site/site_kit.py`, `lib/geo/world_frame.py`, `lib/blocks/mc_paced.py`) become **lazy** (read config on first use / after bind) so importing the library before `--world` is parsed does not crash.

```mermaid
flowchart LR
  CLI["scripts/X.py main"] -->|"--world name"| setWorld["active_world.set_world"]
  setWorld --> bind["script_env.bind_world"]
  bind --> libs["lib/* uses ROOT"]
  libs --> cfg["worlds/name/config/world.json"]
  cfg --> save["save_path / region_path"]
```

## CLI wiring

**Place-touching entry points** (real scripts + thin wrappers that `runpy` into `lib/`): parse `--world` first, activate, then run. Shared pattern in wrapper `main()` and in eager scripts (`cell_registry`, `dryrun_reset`, `cell_marker`, `redo_here`, `tree_review`, …):

```text
python3 scripts/cell_registry.py --world southampton-build next
python3 scripts/site_kit.py --world bakeoff …
python3 bakeoff/model_bakeoff.py --world bakeoff status
```

Bake-off: drop `AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/bakeoff` prefix; require `--world bakeoff` (and keep the existing gameworld_id guard).

Shims that need a place (`tpo_fetch`, world providers via `world_provider`): same `--world` before load.

**World-free:** remove the “env unset → skip tests” gate from `scripts/pre_commit_checks.py`; always run the disposable-world suite. Provider-name audit is tool-repo-only (already uses `TOOL_ROOT`).

## Tests

- `scripts/unit_test_world.py`: call `set_world_root(temp)` (+ `bind_world`) instead of patching the env var; purge/rebind if modules already imported.
- Update `scripts/test_active_world.py`, bake-off world-guard tests, and any test that set `AI_WORLDBUILDER_WORLD_ROOT`.
- Add focused tests: unknown `--world` name fails; `worlds/<name>` resolves; `world_root()` before select raises; save/region still from `world.json`.

## Docs / skills (explicit list)

Replace env prefix / “export WORLD_ROOT” with `--world <name>` in:

- `AGENTS.md`, `docs/active-world-contract.md`, `worlds/README.md`
- Bake-off: `bakeoff/README.md`, `bakeoff/PROTOCOL.md`, `.agents/skills/model-bakeoff/SKILL.md`, `bakeoff/config.json` sitting-step strings, `bakeoff/OVERVIEW.md` if it mentions the env
- Legacy copy if still present: `model-bakeoffs/PROTOCOL.md` (or delete/redirect if already superseded by `bakeoff/`)
- Historical audit prose that agents still read: `docs/archive/region-block-reads.md` (update “how to select world” lines only)
- Round artefact docstrings that teach the old prefix (e.g. `bakeoff/rounds/R-015/r015_susu.py`); leave `tasks/done.json` history alone

`.claude/skills/` and `rules/` had no `AI_WORLDBUILDER_WORLD_ROOT` hits when this was filed; re-grep at the end of the branch to confirm.

## Full touch inventory (completion gate)

Do not call the work done until a repo-wide search for `AI_WORLDBUILDER_WORLD_ROOT` is empty outside `tasks/done.json` and intentional “retired” notes in the contract.

| Surface | What changes |
| --- | --- |
| `active_world.py` | `set_world` / `set_world_root` / lazy `WORLD_ROOT`; no env read |
| `scripts/script_env.py` | `bind_world`, `add_world_arg`, `activate_world_from_args`; no eager ROOT |
| Eager config caches | `lib/site/site_kit.py`, `lib/geo/world_frame.py`, `lib/blocks/mc_paced.py` → lazy |
| Place CLIs | Every `scripts/*.py` entry that is not in the world-free set; thin wrappers parse `--world` before `runpy` |
| Bake-off CLI + tests | `bakeoff/model_bakeoff.py`, `bakeoff/test_model_bakeoff.py` — drop `BAKEOFF_WORLD_PREFIX` |
| System provider CLIs | Modules under `providers/<id>/` that `from script_env import ROOT` at import and are run via `shims/` (aerial sheet, street-level sample, lidar get/review/manage, photo fetch helpers) — parse `--world` before binding (or via shared shim preamble in `shims/_provider_shim.py`) |
| World shims | `shims/tpo_fetch`, `buildings`, `portals`, `osm_extract`, `aerial_mosaic` |
| Tests | `scripts/unit_test_world.py`, `scripts/test_active_world.py`, `scripts/test_run_tests.py`, `scripts/pre_commit_checks.py` / its tests — no env gate |
| Docs listed above | Env → `--world` |

**Not required to rewrite:** every `from script_env import ROOT` call site inside libraries (they keep working once the CLI has activated). Completion is “env string gone + place CLIs require `--world` + suite green”.

## Out of scope

- Renaming world checkouts; changing `world.json` schema; editing archived `tasks/done.json` notes
- Making libraries take an explicit `world_root=` on every function (keep module-level `ROOT` after activate)
- Rewriting old round JSON history beyond living templates (`bakeoff/config.json` sitting steps) and agent-facing PROTOCOL/SKILL
