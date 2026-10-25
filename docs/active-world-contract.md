# Active world + per-world config contract

**Status:** binding for extract (ADR 002)

## System (`ai-worldbuilder`)

| Path | Role |
| --- | --- |
| `active_world.py` | Importable pointer. Sets `WORLD_ROOT` from env `AI_WORLDBUILDER_WORLD_ROOT` (required; no silent default). |
| `TOOL_ROOT` | This checkout (scripts, skills, system providers, system tracker). |
| `config/system-providers.json` | Nationwide+ reference feeds. |
| `providers/<id>/` | System provider fetch/review/manage code (`package` field on catalogue rows). |
| `shims/` | Thin CLIs into system `providers/<id>/`. |

Scripts resolve place memory through `WORLD_ROOT`, never through “where the tool repo happens to sit”.
Catalogue `package` paths resolve via `script_env.resolve_provider_package` /
`cell_sources.provider_package_dir` against **both** `TOOL_ROOT` and `WORLD_ROOT`.

## World (e.g. `worlds/southampton-build`)

World repos live under `worlds/<name>/` inside the worldbuilder checkout. That
tree is `.gitignore`d; each subdirectory is its own git repo.

| Path | Role |
| --- | --- |
| `config/world.json` | **Gameworld identity** + runtime paths. Required: `gameworld_id` (Minecraft world / save identity). May also carry `save_path`, `region_path`, `structure_namespace`, etc. Legacy `world_name` is accepted as `gameworld_id`. |
| `config/world-frame.json` | **World frame**: CRS, anchor, horizontal offsets, Y offset, bounds. |
| `config/local-providers.json` | Below-national feeds for this place only. Entries with code set `package` to `providers/<id>`. |
| `providers/<id>/` | Local provider fetch/CLI modules (see that world’s `providers/README.md`). |
| `site/` | World tracker: QUEUE, ISSUES, notes. |

Offsets live only in the world frame. The system config does not duplicate them; it points at the world root and reads that file.

## Switch active world

1. Set `AI_WORLDBUILDER_WORLD_ROOT` to the other world checkout under `worlds/` (e.g. `export AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/bakeoff`).
2. Confirm: `python -c "from active_world import WORLD_ROOT, gameworld_id; print(WORLD_ROOT, gameworld_id())"`.

The bake-off **framework** (CLI, rounds, skill) lives under tool-repo `bakeoff/`;
the bake-off **world checkout** stays at `worlds/bakeoff/` and is selected via
`AI_WORLDBUILDER_WORLD_ROOT` like any other world.
