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
| Storeys | `streetlevel` (Street View, Rightmove, campus building photo, other-side photos) | `vector_map` `building:levels` | `lidar` estimate (label it) |
| Roof form, ridge axis | `aerial` (look at the roof) | `lidar` interior columns, wide buildings only | same-terrace hint |
| Roof colour, material | `aerial` | `streetlevel` | hint (default tile) |
| Walls, openings, doors, bays | `streetlevel` (incl. campus building photo) | `aerial` (wall colour, wings) | same-terrace hint |
| Where a university building's entrances are, and their names | `poi_register` portals (`scripts/portals.py`), exact position | portal photo / `streetlevel` | `vector_map` `entrance` nodes, same-building hint |
| Ground surface: lawn, paving, drive, car park, path | `aerial` | other `aerial`, `vector_map` surface/landuse | Geocraft default |
| Trees, canopy cover | `aerial` cover + `lidar` tree columns (kind 2, 3) | other `aerial` | none: never invent crowns |
| Building missing from OSM or lidar | `streetlevel` or `aerial` show it: build it anyway | `lidar` UNMAPPED lump | |

Provider examples (Southampton): keepable aerial = hampshire-hub / CCO; look-only aerial = google-satellite; streetlevel = kartaview, commons, google-streetview, rightmove, uos-buildings; lidar = ukdefra-dtm/dsm, geocraft-pristine. Full catalogue: `reference/SOURCES.txt`.

## Cautions
- Aerial lean: a tall roof sits a few blocks off its ground footprint. Judge a
  roof on the roof, a wall on streetlevel.
- Lidar products differ: Geocraft's pristine heightmap is filtered (cars and street
  furniture already gone). Unfiltered DEFRA DSM is noisier — ignore transitory
  returns (cars, trains) but keep street furniture where practical. DSM−DTM
  (`lidar_query.py --layers diff`) helps find walls and other standing objects.
  Rim reads low; ground_y is too low for a building on a bank
  (`rules/geocraft-accuracy.md`, `site/GEOCRAFT.txt`). Newer photos win; record the
  mismatch.
- A portal gives the door's position and name, not its look (door type, canopy, steps): read that from its photo or streetlevel.
  A building with no portal can still have doors.
- Some aerial and streetlevel providers are look-only: view, write down what they show,
  never save, hash or commit them (`reference/SOURCES.txt`, place-sources `look_only`).
- Never researched: private interiors; back gardens come from aerial,
  not from photos over fences (`rules/frontage-and-evidence.md`).
