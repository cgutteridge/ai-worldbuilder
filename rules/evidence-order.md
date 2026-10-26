# Evidence order: what to trust for each question

Read before judging how a building, roof, surface or tree should look. One home
for the ranking; other rules point here. Types: `reference/TYPES.txt`. Providers:
`config/system-providers.json` plus the active world's `config/local-providers.json`
and that world's `reference/SOURCES.txt`.

Use the first **type** that answers the question and move on (good enough:
`rules/radial-good-enough.md`). Name the provider id in the note. Label anything
below the first row `hint` or `estimate`.

| Question | Best | Then | Last resort |
| --- | --- | --- | --- |
| Footprint, plan | `vector_map` polygon, and lidar roof columns where they agree (`poi_register` campus outline cross-checks) | `aerial` roof outline | other `aerial` |
| Top height, roof surface Y | `lidar` (`lidar_query.py` / heightmap) | none: do not guess | |
| Storeys | `streetlevel` (walk photos, listing exteriors, campus building photo, other-side photos) | `vector_map` `building:levels` | `lidar` estimate (label it) |
| Roof form, ridge axis | `aerial` (look at the roof) | `lidar` interior columns, wide buildings only | same-terrace hint |
| Roof colour, material | `aerial` | `streetlevel` | hint (default tile) |
| Walls, openings, doors, bays | `streetlevel` (incl. campus building photo) | `aerial` (wall colour, wings) | same-terrace hint |
| Where a university building's entrances are, and their names | `poi_register` portals (`scripts/portals.py`), exact position | portal photo / `streetlevel` | `vector_map` `entrance` nodes, same-building hint |
| Ground surface: lawn, paving, drive, car park, path | `aerial` | other `aerial`, `vector_map` surface/landuse | same-terrace / nearby hint |
| Trees, canopy cover | `aerial` cover + `lidar` tree columns (kind 2, 3) | other `aerial` | none: never invent crowns |
| Building missing from vector map or lidar | `streetlevel` or `aerial` show it: build it anyway | `lidar` standing height with no footprint polygon | |

Provider ids for this place live in `reference/SOURCES.txt`, place-sources catalogues, and the generated `site/cells/<cell>-SOURCES.txt` checklist — not in build rules.

## Cautions
- Aerial lean: a tall roof sits a few blocks off its ground footprint. Judge a
  roof on the roof, a wall on streetlevel.
- Lidar heights come from the active world's **composite** DTM + last-return DSM
  (lidar shim + `lidar_query.py` / `lidar_heightmap.py`). Unfiltered
  DSM is noisier than a filtered product: ignore transitory returns (cars, trains)
  but keep street furniture where practical. DSM−DTM (`lidar_query.py --layers
  diff`) finds walls and other standing objects. Rim reads low; ground_y is too
  low for a building on a bank. Newer photos win; record the mismatch.
  Heights and surfaces come from the lidar provider only; a baseline or import save is never read as evidence (ADR 003).
- A portal gives the door's position and name, not its look (door type, canopy, steps): read that from its photo or streetlevel.
  A building with no portal can still have doors.
- Some aerial and streetlevel providers are look-only for *durable* evidence: download a
  working copy if the tool does (e.g. aerial PNG under `instance/`), read it,
  write down what it shows — do not hash or commit into `reference/` (`reference/SOURCES.txt`,
  place-sources `look_only`). Prefer the URL download path over a browser pane.
- Never researched: private interiors; back gardens come from aerial,
  not from photos over fences (`rules/frontage-and-evidence.md`).
