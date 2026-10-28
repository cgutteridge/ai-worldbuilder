---
name: new-world-build
description: >-
  Stand up a new place-refine world: empty save, world-frame, place sources,
  Y-offset human loop. Use when starting a new city/campus build, not for
  refining an existing place's cell registry.
---

Bootstrap only. Do not invent CRS math beyond `world_frame`. Stop with `need-you` when a prerequisite is missing. Starting order: home location, projection, offsets 0/0, home cell, blank save, spawn, lidar, Y offset.

## 0. Home and defaults

- Create the new world as its **own git repo** under `worlds/<name>/` (gitignored by ai-worldbuilder). Point `active_world.py` at it before writing place files.
- **Empty world** from `templates/empty-world/` (flat, one bedrock layer at the world floor Y −64, structures off, weather off, MC 26.2 format; see its README.txt). Never start from a generated city-mesh save or a third-party heightmap export; heights come from the lidar provider only.
- **Start with the home location.** Ask the user for the place they want to start at (we used the Highfield campus). Everything below follows from it. Name it (e.g. Bargate, campus centre).
- Ask bbox size; **recommend ≥ 5 km × 5 km** around home. Explain a bad offset is expensive to undo.
- Vanilla Overworld build range is **Y −64…319** (384 blocks, limit 320). Terrain plus buildings plus trees in the bbox must fit in it; that is what the Y checkpoint (section 2) is for.

Done when the user has named the home place and a rough bbox.

## 1. World frame

### 1a. Projection

Pick a **projected CRS in metres** with low scale error at home, so 1 m ≈ 1 block north-south and east-west and block coordinates can be navigated by hand. Recommend one and let the user confirm:

| Place | Recommend |
| --- | --- |
| Great Britain | `EPSG:27700` (OSGB36 National Grid), `"grid": "OSGB36"` |
| Ireland | `EPSG:2157` (ITM) |
| Netherlands | `EPSG:28992` |
| France | `EPSG:2154` (Lambert-93) |
| Elsewhere | the local UTM zone for home (`EPSG:326xx` north / `327xx` south, or the national ETRS89 UTM zone in Europe) |

Never use web mercator (`EPSG:3857`, about 1.6× too big at 51°N), a feet-based CRS, or latitude/longitude. Check the scale at home is within about 0.9996 to 1.0004 (`pyproj.Proj(crs).get_factors(lon, lat).meridional_scale`). Beyond the table, ask the user; `world_frame` converts any EPSG pair via pyproj, so no new projection code is needed.

### 1b. Offsets: default 0, 0

Set `offsets` easting and northing to **0 and 0**, so Minecraft x = easting and z = −northing (north is −Z) and the F3 screen shows the projected coordinates. Keep 0, 0 as long as every coordinate in the bbox stays well inside Minecraft's ±30,000,000 limit (world border default ±29,999,984); that holds for OSGB, ITM, UTM and the other recommended grids. Only if a CRS puts home near or past that limit, set the offsets to home's easting and northing rounded to the nearest 1000 m and say why. Also test once in the game (fly to home and look around): if rendering jitters at large coordinates, fall back to the rounded offsets. Existing worlds keep their offsets (Southampton is 436248, 109608).

### 1c. Home cell

Record the **home cell**: the cell containing the home point (`world_frame.py to-cell --mc x,z`), written as `cell.origin` in `world-frame.json` (it is the cell the build starts from and rings out of). Keep `cell.size` at 128. Set `bounds_mc` from the bbox corners converted with `world_frame.py` (use the key meanings in `world-frame.json.example`); the home cell must be inside it.

### 1d. Write and check

1. Write `config/world-frame.json`: `crs`, `grid`, `offsets`, `bounds_mc`, `cell`, `y_offset` (placeholder 0 until section 2), `elevation_plugin`.
2. Confirm conversions: `python3 scripts/world_frame.py --world <name> info` and `to-mc` / `to-wgs84` for the home point. For XYZ tile codes and pixel offsets use `to-wm` / `from-wm` (or `batch-wm`); do not add a second mercator path.
3. Set `gameworld_id` and `save_path` in `config/world.json`: `save_path` is a new folder inside your standalone Minecraft instance's `saves/` (see `docs/minecraft-instance-setup.md`; the region folder is derived). Then create the blank save (refuses if it exists; the only write this tool makes under `saves/`):

```bash
python3 scripts/new_blank_world.py --world <name>
python3 scripts/check_instance.py --world <name>
```

   The template has no chunks; the game generates them on first open. The in-game name stays as in `level.dat` until renamed in the game (Edit World); the folder name is what the tools use.
4. **Move spawn to home.** The template's spawn is a leftover from another world, and with 0, 0 offsets (0, 0) is nowhere useful. With the world open, set the world spawn and put the player at the home point (MCP `level_set_spawn_point`, then teleport), so the first time the game opens it is at the right place.
5. Not a copy of Southampton2023 unless legacy.
6. If a newer Minecraft version is in use than the template's (`Version.Name` in `level.dat`): open the world once in the game to upgrade it, log out, then refresh `templates/empty-world/` (copy without `session.lock`, `players/`, `*.dat_old`, `*.mca`; keep the single bedrock layer at Y −64 in `world_gen_settings.dat`) and commit.

Done when `world_frame.py info` prints the new place, offsets and home cell, and `check_instance.py` passes.

## 2. Y-offset checkpoint (human)

`y_offset` is the block Y that real height 0 m maps to (Y = height + `y_offset`). Default **0**: sea level (the lidar datum, e.g. Ordnance Datum Newlyn in Britain) is Y 0. Change it only when the area needs it, for example a site 1000 m above sea level.

1. Ensure lidar coverage for the bbox (national composite plugin or whatever `world-frame.json` names).
2. Run highs/lows:

```bash
.venv/bin/python shims/lidar_extremes.py --bbox-mc x0,z0,x1,z1 --fetch
```

3. Fit the range: with `y_offset = 0` the lowest ground (DTM min) must be at least −64 and the highest DSM (roofs, trees, towers) at most 319. If the area sits outside that, pick `y_offset` so the lowest ground lands a little above −64 (about −50, leaving room for foundations and cellars) and the highest surface stays below 319. If the total relief (DSM max minus DTM min) is over about 350 m, the bbox is too tall: shrink it. Also check the lidar datum is not ellipsoidal (heights above the ellipsoid are tens of metres off sea level).
4. Talk through DTM/DSM min/max and the suggested `y_offset` with the user. Lock only after they confirm.
5. Write `y_offset` into `world-frame.json`.

Play `need-you` and **stop** until the user confirms the offset. Do not apply world edits before that.

## 3. Place sources

1. Author `config/local-providers.json` for the place (required types covered by system + local together: at least `vector_map`, `lidar`, one `aerial`). Reuse system providers; do not copy nationwide feeds into the world repo.
2. Point at worldbuilder `reference/TYPES.txt` and start a place `reference/SOURCES.txt` catalogue.
3. **vector_map (once per project):** download the regional source into this world's `downloads/` (cache), extract place clips into `reference/vector_map/<place>/` via the local vector_map provider shim. Shared across cells; do not symlink or `osm_root=` into another world. Contract: `scripts/vector_map_clips.py`.
4. Scaffold place memory:
   - `templates/new-world-site/` → world checkout `site/` (ISSUES, NOTES, COVERAGE, REVIEWS, cells/, thin stubs for process docs).
   - Registry: nothing to copy. `registry/` is created by the first journal write (`templates/new-world-registry/README.txt`, ADR 004).
   Do **not** copy craft/process prose into the world — that lives in ai-worldbuilder `rules/`. Do **not** create retired progress files under site/ — `cell_registry.py verify` treats them as leftovers. Progress starts empty; tools populate the journal.

Done when checklist providers have `how` strings, `evaluate-data-source` would not flag an empty type slot that the place needs, and vector_map clips (if required) land under this world's `reference/vector_map/` via the download/cache contract.

## Out of scope (stop)

- A CRS outside the recommended table or beyond what `world_frame.transform_epsg` (pyproj) already does — ask the user; do not invent a new projection stack.
- Elevation backends other than the default national plugin — document the gap and `need-you`.
