---
name: new-world-build
description: >-
  Stand up a new place-refine world: empty save, world-frame, place sources,
  Y-offset human loop. Use when starting a new city/campus build, not for
  refining the existing Southampton queue.
---

Bootstrap only. Do not invent CRS math beyond `world_frame`. Stop with `need-you` when a prerequisite is missing.

## 0. Defaults

- Create the new world as its **own git repo** under `worlds/<name>/` (gitignored by ai-worldbuilder). Point `active_world.py` at it before writing place files.
- **Empty world** from `templates/empty-world/` (flat, structures off, weather off, MC 26.2 format; see its README.txt) + projection so 1 m ≈ 1 block NSEW. Never start from a generated or Geocraft save. Pristine Geocraft heightmap = Southampton legacy branch only.
- **Anchor** at MC (0, 0) (name it: e.g. Bargate, campus centre).
- Vanilla Overworld Y is **−64…319** — a Y-offset human checkpoint is mandatory.
- Ask bbox size; **recommend ≥ 5 km × 5 km**. Explain a bad offset is expensive to undo.

Done when the user has named place, anchor, and rough bbox.

## 1. World frame

1. Write `config/world-frame.json`: `crs`, `anchor`, `offsets`, `bounds_mc`, `y_offset` (placeholder), elevation plugin.
2. Confirm conversions: `.venv/bin/python scripts/world_frame.py info` and `to-mc` / `to-wgs84` for the anchor.
3. Create the save from the template (refuse if the target exists):

```bash
test ! -e "instance/saves/<WorldName>" && cp -R templates/empty-world "instance/saves/<WorldName>"
```

   The template has no chunks; the game generates them on first open. The in-game name stays `SouthamptonAI` until renamed in the game (Edit World); the folder name is what the tools use.
4. Set `world_name`, `save_path`, `region_path` (`<save>/dimensions/minecraft/overworld/region`) and `map_maker_status` in `config/world.json`. Not a copy of Southampton2023 unless legacy.
5. If a newer Minecraft version is in use than the template's (`Version.Name` in `level.dat`): open the world once in the game to upgrade it, log out, then refresh `templates/empty-world/` (copy without `session.lock`, `players/`, `*.dat_old`, `*.mca`) and commit.

Done when `world_frame.py info` prints the new place and anchor.

## 2. Y-offset checkpoint (human)

1. Ensure lidar coverage for the bbox (DEFRA or stated plugin).
2. Run highs/lows:

```bash
.venv/bin/python shims/lidar_extremes.py --bbox-mc x0,z0,x1,z1 --fetch
```

3. Talk through DTM/DSM min/max and suggested `y_offset` with the user. Lock only after they confirm.
4. Write `y_offset` / `sea_level` into `world-frame.json`.

Play `need-you` and **stop** until the user confirms the offset. Do not apply world edits before that.

## 3. Place sources

1. Author `config/local-providers.json` for the place (required types covered by system + local together: at least `vector_map`, `lidar`, one `aerial`). Reuse system providers; do not copy nationwide feeds into the world repo.
2. Point at worldbuilder `reference/TYPES.txt` and start a place `reference/SOURCES.txt` catalogue.
3. Scaffold place memory from `templates/new-world-site/` into the world checkout `site/` (QUEUE, ISSUES, NOTES, COVERAGE, CURRENT-CELL, cells/, thin stubs for process docs). Do **not** copy craft/process prose into the world — that lives in ai-worldbuilder `rules/`. Do not import Southampton Aerial Review rows or Southampton QUEUE items.

Done when checklist providers have `how` strings and `evaluate-data-source` would not flag an empty type slot that the place needs.

## Out of scope (stop)

- Non-OSGB CRS beyond what `world_frame.transform_epsg` + gdal already do — ask the user; do not invent a new projection stack.
- Non-DEFRA elevation backends — document the gap and `need-you`.
