---
name: new-world-build
description: >-
  Stand up a new place-refine world: empty save, world-frame, place sources,
  Y-offset human loop. Use when starting a new city/campus build, not for
  refining an existing place's cell registry.
---

Bootstrap only. Use the platform setup in `docs/minecraft-instance-setup.md` (Windows: WSL). Sound cues are optional; skip them when agent-soundboard is not installed. Do not invent CRS math beyond `world_frame`. Stop with `need-you` when a prerequisite is missing. Starting order: home location, projection, offsets 0/0, home cell, blank save, spawn, lidar, Y offset.

## 0. Home and defaults

- Create the new world as its **own git repo** under `worlds/<name>/` (gitignored by ai-worldbuilder). Copy `templates/new-world-repo/.gitignore` to its root before adding files. Point `active_world.py` at it before writing place files.
- **Empty world** from `templates/empty-world/` (flat, one bedrock layer at the world floor Y −64, structures off, weather off, MC 26.2 format; see its README.txt). Never start from a generated city-mesh save or a third-party heightmap export; heights come from the lidar provider only.
- **Start with the home location.** Ask the user for the place they want to start at (we used the Highfield campus). Everything below follows from it. Name it (e.g. Bargate, campus centre). **Pin the exact point**: agree a specific lat/lon (or grid reference), check it with `world_frame.py to-mc`/`to-wgs84` and an aerial or map, and have the user confirm it before the frame is written; the home cell comes from this point.
- **Bbox: explain it, then ask.** Say this to the user in plain words every time, before asking for a size:
  - Lidar comes as **fixed tiles**; the grid, tile size, download size and speed depend on the country, so **read them from the lidar provider that covers the home location**, never from memory. Find it in the merged catalogue (`cell_sources.load_place_sources()`: a `lidar` row whose `scope` covers the home point, system or world) and read the README in its `package` directory (`providers/<id>/README.md`). If no lidar provider covers the home location (anywhere outside its scope, including the rest of the UK or abroad), stop: the user and their AI must first configure one with `evaluate-data-source` then `add-data-source` (a world-local provider is fine), and write its grid and bbox notes in its README.
  - A tile can't be downloaded in part, so the bbox doesn't choose how much data you get; it chooses **which tiles** download now. Explain this to the user in plain words.
  - The bbox has two jobs: it picks the first download, and it is the area the Y-offset check scans for lowest ground and highest roof (section 2). It does not limit the world: later cells fetch the neighbouring tiles when first needed.
  - Apply the provider's README rules for aligned boxes (for example, ending a box just inside a tile edge so it does not pull in the next tile) and compute the tile count with the provider's `tiles` module (`tiles_for` or equivalent). Tell the user the count, the rough download size and that the first download is slow.
  - Offer both options with their trade-offs, and give a recommendation for this place:
    - **Tile-aligned box (1 tile):** fastest start (one tile's download), but the Y check only sees that tile; extremes outside it are missed, and a wrong Y offset is expensive to undo.
    - **Wider box (2 or 4 tiles):** the min/max is more trustworthy and neighbours are cached for later, at the cost of several times the download and any sea or empty tiles in it.
    - Prefer the aligned box when the high and low points are known to be inside it; prefer the wider box for unfamiliar terrain (cliffs, valleys, tall structures).
  - Smallest sensible box: home plus the highest and lowest land expected. A 5 km box is a starting point, not a rule.
  - **Coastal homes:** sea has no lidar and nothing to build, so shift the box inland rather than centring it on the shore.
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

Set `offsets` easting and northing to **0 and 0**, so Minecraft x = easting and z = −northing (north is −Z) and the F3 screen shows the projected coordinates. Keep 0, 0 as long as every coordinate in the bbox stays well inside Minecraft's ±30,000,000 limit (world border default ±29,999,984); that holds for OSGB, ITM, UTM and the other recommended grids. Only if a CRS puts home near or past that limit, set the offsets to home's easting and northing rounded to the nearest 1000 m and say why. Check for rendering jitter (blocks or the hand shaking at large coordinates) only once the first ghost exists (section 2b): an empty bedrock floor shows nothing. If it jitters, fall back to the rounded offsets and redo the frame before anything real is built. Existing worlds keep their offsets (Southampton is 436248, 109608). The [live coordinate check](../../../docs/large-coordinate-check.md) passed near (442000, −115000) on Minecraft 26.2; multi-million coordinates still need the same per-world visual check.

### 1c. Home cell

Record the **home cell**: the cell containing the home point (`world_frame.py to-cell --mc x,z`), written as `cell.home` in `world-frame.json` (it is the cell the build starts from and rings out of). Keep `cell.size` at 128. Convert the bbox corners to MC with `world_frame.py to-mc`; they go to the Y checkpoint (section 2) and the first lidar fetch, not into the frame file (nothing reads a bounds key). The home cell must lie inside the bbox.

### 1d. Write and check

1. Write `config/world-frame.json`: `crs`, `grid`, `offsets`, `cell`, `y_offset` (placeholder 0 until section 2), `elevation_plugin`.
2. Confirm conversions: `python3 scripts/world_frame.py --world <name> info` and `to-mc` / `to-wgs84` for the home point. For XYZ tile codes and pixel offsets use `to-wm` / `from-wm` (or `batch-wm`); do not add a second mercator path.
3. Set `gameworld_id` and `save_path` in `config/world.json`: `save_path` is a new folder inside your standalone Minecraft instance's `saves/` (see `docs/minecraft-instance-setup.md`; the region folder is derived). Then create the blank save (refuses if it exists; the only write this tool makes under `saves/`):

```bash
python3 scripts/new_blank_world.py --world <name>
python3 scripts/check_instance.py --world <name>
```

   The template has no chunks; the game generates them on first open. The in-game name (`LevelName` in `level.dat`) is set to the folder name; the folder name is what the tools use.
4. **Move spawn to home** (needs the game open on the world; if it is not open yet, say so and do it in section 2b once the user has opened it). The template's spawn is a leftover from another world, and with 0, 0 offsets (0, 0) is nowhere useful. With the world open, set the world spawn and put the player at the home point (MCP `level_set_spawn_point`, then teleport), so the first time the game opens it is at the right place.
5. Not a copy of Southampton2023 unless legacy.
6. If a newer Minecraft version is in use than the template's (`Version.Name` in `level.dat`): open the world once in the game to upgrade it, log out, then refresh `templates/empty-world/` (copy without `session.lock`, `players/`, `*.dat_old`, `*.mca`; keep the single bedrock layer at Y −64 in `world_gen_settings.dat`) and commit.

Done when `world_frame.py info` prints the new place, offsets and home cell, and `check_instance.py` passes.

## 2. Y-offset checkpoint (human)

`y_offset` is the block Y that real height 0 m maps to (Y = height + `y_offset`). Default **0**: sea level (the lidar datum, e.g. Ordnance Datum Newlyn in Britain) is Y 0. Change it only when the area needs it, for example a site 1000 m above sea level.

1. Ensure lidar coverage for the bbox (national composite plugin or whatever `world-frame.json` names).
   The first fetch is slow: slow; take the per-tile time from the lidar provider's README (England: about 1 minute per tile per product, so 4 tiles x DTM and DSM took about 7.5 minutes). Run it in the background, tell the user the tile count first, and relay the `[i/N]` progress lines as they appear. Note the elapsed time it reports.
2. Run highs/lows:

```bash
python3 shims/lidar_extremes.py --world <name> --bbox-mc x0,z0,x1,z1 --fetch
```

3. Fit the range: with `y_offset = 0` the lowest ground (DTM min) must be at least −64 and the highest DSM (roofs, trees, towers) at most 319. If the area sits outside that, pick `y_offset` so the lowest ground lands a little above −64 (about −50, leaving room for foundations and cellars) and the highest surface stays below 319. If the total relief (DSM max minus DTM min) is over about 350 m, the bbox is too tall: shrink it. Also check the lidar datum is not ellipsoidal (heights above the ellipsoid are tens of metres off sea level).
4. Show the user the arithmetic, not just the tool's answer: DTM min + `y_offset` ≥ −64 and DSM max + `y_offset` ≤ 319, with the actual numbers. The tool suggests an integer offset with lowest ground at or above −50 and highest surface at or below 315, aiming ground near 64 when it fits. If `suggested_y_offset` is null, its `y_offset_fit` explains that no offset fits those margins; shrink the survey area or discuss the margins before choosing an offset. Lock only after they confirm.
5. Write `y_offset` into `world-frame.json`.

Play `need-you` and **stop** until the user confirms the offset. Do not apply world edits before that.

## 2b. First ghost at the home cell

Once `y_offset` is locked, give the world its first ground: lidar ghost around the home cell, so the user has real terrain to look at (the Y fit, jitter, and where home is) instead of bare bedrock.

1. Run it live under the site lock, in the background (ground pass about 25-55 s a cell; ring 1 is 9 cells, so roughly 5-9 minutes; relay progress):

```bash
python3 scripts/site_lock.py --world <name> run <owner> "first ghost" -- python3 shims/lidar_ghost.py --world <name> apply --cell <home cell> --ring 1
```

   Use `--ring 0` for the home cell alone (just the home cell, fastest) when the user only wants a quick look.
2. Then set the world spawn at the home point and put the player there (the spawn step in 1d), with the game open on the world.
3. Ask the user to look around: does the terrain match the place (sea level, the hill, the shoreline), is the highest ground under the build limit, and does anything jitter? Fix `y_offset` or the offsets now if not; nothing real is built yet.

Done when the ghost is placed, the player stands at home on real terrain, and the user has looked around.

## 3. Place sources

1. Author `config/local-providers.json` for the place (required types covered by system + local together: at least `vector_map`, `lidar`, one `aerial`). Reuse system providers; do not copy nationwide feeds into the world repo.
2. Point at worldbuilder `reference/TYPES.txt` and start a place `reference/SOURCES.txt` catalogue.
3. **vector_map (once per project, world-local):** choose a Geofabrik extract id from the [published index](https://download.geofabrik.de/technical.html), then run `python3 scripts/scaffold_vector_map.py --world <name> --extract <id> --bbox-wgs84 west,south,east,north`. This creates the regional provider, its GDAL `osmconf.ini`, and the catalogue row only after the complete bbox fits inside the published clipping polygon (including holes and coastal notches). It fetches index metadata, never the PBF; `--index-file <index-v1.json>` supports offline use. It refuses to replace an existing vector_map choice. After downloading, clip a known building or road near home with `python3 shims/osm_extract.py --world <name> --at X,Z --cell --place PLACE` and inspect the output before relying on it. Before downloading, tell the user the filename, source and size and wait for a yes. Download the regional source into this world's `downloads/` (cache), extract place clips into `reference/vector_map/<place>/` (use the place name for the clip folder) via the local vector_map provider shim. Shared across cells; do not symlink or `osm_root=` into another world. Contract: `scripts/vector_map_clips.py`.
4. Scaffold place memory:
   - `templates/new-world-site/` → world checkout `site/` (ISSUES, NOTES, COVERAGE, REVIEWS, cells/, thin stubs for process docs).
   - Registry: nothing to copy. `registry/` is created by the first journal write (`templates/new-world-registry/README.txt`, ADR 004).
   Do **not** copy craft/process prose into the world — that lives in ai-worldbuilder `rules/`. Do **not** create retired progress files under site/ — `cell_registry.py verify` treats them as leftovers. Progress starts empty; tools populate the journal.

Done when checklist providers have `how` strings, running the `evaluate-data-source` skill over the catalogue finds no empty type slot the place needs (`vector_map`, `lidar`, `aerial`), and vector_map clips (if required) land under this world's `reference/vector_map/` via the download/cache contract.

## 4. Close

Run this once at the end, instead of repeating open items in every message:

1. `python3 scripts/world_frame.py --world <name> info` and `python3 scripts/check_instance.py --world <name>` pass.
2. The world repo is committed and has the `.gitignore` from `templates/new-world-repo/` for `downloads/` and other caches.
3. Every tool gap or workaround met during the run is filed in `tasks/index.json` via the `task-index-manager` skill (tool gaps only; never world work), or the user has said not to.
4. List anything still open (e.g. the user's check at home) in one short list and ask once.

## Out of scope (stop)

- A CRS outside the recommended table or beyond what `world_frame.transform_epsg` (pyproj) already does — ask the user; do not invent a new projection stack.
- Elevation backends other than the default national plugin — document the gap and `need-you`.
