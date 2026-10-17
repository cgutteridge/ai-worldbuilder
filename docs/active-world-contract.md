# Active world + per-world config contract

**Status:** binding for extract (ADR 002)

## System (`ai-worldbuilder`)

| Path | Role |
| --- | --- |
| `active_world.py` | Importable pointer. Sets `WORLD_ROOT` (edit file or env `AI_WORLDBUILDER_WORLD_ROOT`). |
| `TOOL_ROOT` | This checkout (scripts, skills, system providers, system tracker). |
| `config/system-providers.json` | Nationwide+ reference feeds (after migrate). |

Scripts resolve place memory through `WORLD_ROOT`, never through “where the tool repo happens to sit”.

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

1. Point `WORLD_ROOT` at the other world checkout under `worlds/` (edit `active_world.py` or set the env var).
2. Confirm: `python -c "from active_world import WORLD_ROOT, gameworld_id; print(WORLD_ROOT, gameworld_id())"`.
