# Active world + per-world config contract

**Status:** binding for extract (ADR 002)

## System (`ai-worldbuilder`)

| Path | Role |
| --- | --- |
| `active_world.py` | Importable pointer. `set_world(name)` / `set_world_root(path)` select the checkout; `world_root()` raises until selected. Env `AI_WORLDBUILDER_WORLD_ROOT` is **retired** (no fallback). |
| `TOOL_ROOT` | This checkout (`lib/`, `scripts/`, skills, system providers, system tracker). |
| `config/system-providers.json` | Nationwide+ reference feeds. |
| `providers/<id>/` | System provider fetch/review/manage code (`package` field on catalogue rows). |
| `lib/<category>/` | Shared libraries (bare imports via `script_env`). |
| `scripts/` | CLI entry points (often thin wrappers over `lib/`). |
| `shims/` | Thin CLIs into system and world `providers/<id>/`. |

Scripts resolve place memory through `WORLD_ROOT`, never through “where the tool repo happens to sit”.
Catalogue `package` paths resolve via `script_env.resolve_provider_package` /
`cell_sources.provider_package_dir` against **both** `TOOL_ROOT` and `WORLD_ROOT`.

Optional place integrations select a catalogue `role`, independent of provider id
and evidence `types`: `entrance_register` (entrances), `building_register` (building
photos/outlines), `tree_preservation` (protected trees) and `aerial_mosaic` (local
mosaic fetcher). Add the matching `role` to the provider row alongside `package`.
`world_provider.load_world_provider(role, module_file, module_name, optional=True)`
returns no provider when that role is unconfigured; tool queries return empty
extra evidence. A configured role must have one usable package and module;
ambiguity, missing files and provider bugs remain errors. Direct provider CLIs
require their role. Provider homes still control system/world root resolution.

## World (e.g. `worlds/my-world`)

World repos live under `worlds/<name>/` inside the worldbuilder checkout. That
tree is `.gitignore`d; each subdirectory is its own git repo.

| Path | Role |
| --- | --- |
| `config/world.json` | **Gameworld identity** + the save reference. Required: `gameworld_id`. `save_path` points at the save in the standalone Minecraft instance (absolute, or relative to the world root); the region folder is derived. The tool treats the save as read-only; see `docs/minecraft-instance-setup.md`. Also `structure_namespace` (default `worldbuilder`), `player_uuid`, `plan_prefix` (default `cell`), `base_y` (stone-fill floor; default 37), `retired_ground_passes`, `reverted_builds`, `ledger_aliases`, `fetch_hosts`, `fetch_probe`, `work_context_keywords`. |
| `config/world-frame.json` | **World frame**: CRS, horizontal offsets, bounds, cell grid, Y offset. The only home for offsets and projection; nothing of this lives in the Minecraft save. |
| `config/local-providers.json` | Below-national feeds for this place only. Entries with code set `package` to `providers/<id>`. |
| `providers/<id>/` | Local provider fetch/CLI modules (see that world’s `providers/README.md`). |
| `site/` | World tracker: QUEUE, ISSUES, notes. |

Offsets live only in the world frame. The system config does not duplicate them; it points at the world root and reads that file.

## Switch active world

1. Pass `--world <dirname>` for the other checkout under `worlds/` (e.g. `--world bakeoff`).
2. Confirm: `python3 -c "from active_world import set_world, gameworld_id; print(set_world('bakeoff'), gameworld_id())"`.

The bake-off **framework** is a closed historical record under tool-repo `bakeoff/`
(`PROTOCOL.md` kept; the `model-bakeoff` skill is historical). The bake-off
**world checkout** stays at `worlds/bakeoff/` and is selected via
`--world bakeoff` like any other world.

Retired: the environment variable `AI_WORLDBUILDER_WORLD_ROOT` is no longer read.

The cell grid uses `cell.size` and `cell.home: [tx, tz]`. To migrate an older frame, rename `cell.origin` to `cell.home` (keeping its coordinates) and remove `top_of_world`; build limits come from Minecraft, not the frame. Legacy `cell.origin` is rejected with a migration error.
