# Spike: do decor furniture reads need finer than z20 aerial? (task #308)

Docs only. Nothing was downloaded: every image below was already on disk in the active world (`worlds/southampton-build/instance/google-aerial/`, `instance/streetview/`; read 2026-11-19). Source is the owner's question after #306 (issue I-008, snag S-033).

## Answer

**No.** The picnic tables the 2026-10-27 designer reading missed are clearly readable at z20, and four of them are still recognisable at z19. The designer did see them: the evidence note for 48,-44 says "garden picnic tables on grass (not placeable)". The miss was a vocabulary and rule gap (no `picnic_table` kind; lawn rule F-3), not image resolution. #306 has since added the kinds. A finer zoom would cost about 3 to 4 times the tiles for a cell, and no finer zoom is cached, so its benefit could not be tested.

## What was compared

Two zooms exist in the cache for these sites: z19 (the per-cell sheet, about 5.3 px per block) and z20 (the `at_*` and `p_*` sheets, about 10.6 px per block, native tiles). No z21 or z22 image exists anywhere in the world checkout (searched file names, evidence and registry text). So "z20 against a finer zoom" could not be done; I compared z19 against z20 to see how fast legibility falls with resolution, and noted what z20 still leaves ambiguous.

Positions are read off the drawn MC grid and are good to about 2 blocks.

| Site (cell) | z19 (5.3 px/block) | z20 (10.6 px/block) | Finer than z20 |
| --- | --- | --- | --- |
| Four picnic tables on the garden lawn beside the path, W of the old SUSU frontage, about x6173 to 6178, z-5604 to -5592 (48,-44) | Seen: four pale squares on the grass; a table with a bench each side is just resolvable, orientation roughly readable | Clear: table top plus two benches each, orientation and spacing readable; all four unambiguous | Not compared (no cached image) |
| Cafe seating row on the brick plaza W of SUSU, about x6204 to 6226, z-5578 to -5574 (48,-44) | Seen as a dark row of about 6 fused groups; parasols and people merge | Six table groups with benches or chairs, parasols and people, separable | Not compared |
| Bike stands in the courtyard E of the plaza, about x6240 to 6243, z-5588 to -5581 (48,-44) | Two N-S rows visible as speckle; individual bikes blur | Individual bikes along two rows plus a third short row; the 22 entries were counted from this | Not compared |
| Bench by the young courtyard tree, about x6235, z-5583 (48,-44) | Not separable from the tree shadow | Dark bench readable beside the trunk | Not compared |
| Two picnic tables on the lawn S of the stepped entrance, about x6182 to 6185, z-5709 to -5705 (48,-45) | Two small light specks; type guessable only once you know they are there | Clear: both tables with benches, one slightly rotated; neither is in `decor-furniture.json` for 48,-45 | Not compared |
| Pale rectangular objects with people on the NW lawn, about x6145 to 6159, z-5676 to -5665 (48,-45) | Not compared | Ambiguous: rectangular pale shapes with people seated or lying on them, so a picnic rug or a table; no round tables seen | Not compared; the one place a finer zoom might change a read |
| Roof terrace tables, 48,-44 (about x6165 to 6180, z-5545 to -5530) | Not compared | Clear dark tables on a deck; on a roof, so not furniture for the ground decor layer | Not compared |
| Street view, 48,-44 (8 cached shots at 4 points, yaws 0 and 90) | Not applicable | Not applicable | Shows what aerial cannot: lamp columns and heads, a wheeled bin, a bin cluster with barriers and bollards, a signboard. No picnic tables (the lawn is inside the garden, out of view of the paths) |

The street-view cache also holds two shots in 48,-43 and four in 53,-42; those sites have no furniture in `decor-furniture.json` and were not compared.

Not compared at all: the round picnic tables the owner described on the north-west lawn. They were not located in any cached image (see the NW lawn row); the plaza tables in S-033 correspond to the cafe row above.

## What the findings say

- z20 is enough to find, count and orient picnic tables, benches, cafe table groups and bike stands. Even z19 finds the tables; z20 adds the bench pair and orientation that #305's `picnic_table` footprint wants.
- What z20 does not give is small, thin or vertical furniture: the designer noted "no lamps/bins/signs discernible" for 48,-44. Street view does give it. The remedy there is the cached street view (or the street-level type generally), not a finer aerial.
- The 48,-45 reading shows the same pattern: tables visible at z20 and absent from the reference file.

## Cost of a finer zoom

Tile counts use the tool's own geometry (`shims/google_aerial.py` `tile_range`, run read-only for cell 48,-44, bbox x6144..6271, z-5632..-5505). Ground resolution at this latitude: z19 0.19 m, z20 0.094 m, z21 0.047 m, z22 0.024 m per pixel.

| Request | z19 | z20 | z21 | z22 |
| --- | --- | --- | --- | --- |
| Whole cell 48,-44 (tiles) | 12 (4 x 3) | 42 (7 x 6) | 132 (12 x 11) | 484 (22 x 22) |
| One 30 x 30 block window (radius 15) at the lawn tables | 2 | 6 (2 x 3) | 16 (4 x 4) | 36 (6 x 6) |
| One 60 x 60 block window (radius 30) | 6 | 12 | 36 (6 x 6) | refused (11 x 11) |

- The sheet tool caps a sheet at 6 tiles a side (its ToS note: keep grids small, never loop over many cells). z21 covers about 72 blocks a side at that cap, so a whole cell needs at least four sheets; z22 cannot cover a 60-block window in one sheet.
- Fetch size: the working PNG sheets in the cache run about 110 to 135 KB per tile (for example 1598 KB for the 12-tile z19 cell sheet). The raw bytes the provider sends per tile were not measured (nothing was fetched); treat the working-sheet figure as an upper bound on cache size, not as bandwidth.
- Today's reading uses 4 z20 overlays for 48,-44 (about 36 to 64 tiles with overlap). A z21 re-read of the same four windows would be about 4 x 36 = 144 tiles, 3 to 4 times as many; a targeted z21 pass on three small windows (lawn tables, cafe row, bike stands) is about 48 tiles.
- Provider limits: the aerial this tool uses is a look-only, non-keepable working image (its provider row says not to commit or hash it into `reference/`), and the tool's guidance is a handful of tiles per request, not bulk. Whether the provider serves real imagery at z21 over this campus is **not verified** (it may return upscaled or blank tiles); only a one-tile request would show, which needs the owner's go-ahead. The keepable aerial types in `SOURCES.txt` stop at z19 (0.19 m) and lower, so they are coarser than the z20 working image.

## Recommendation

**No** to finer than z20 as part of the decor reading. Not a new task.

Instead (for the builds thread, not this task):
- Redo the 48,-44 and 48,-45 decor reading at z20 with an explicit list to tick: picnic tables (rectangular), tables on lawn and plaza, bike stands, benches, lamps and bins (street view for the last two).
- Keep the cached z20 sheets as the reading source and the street-level type for vertical furniture.

Optional, owner's call only: if the NW-lawn pale objects (table or rug, round or rectangular) still matter after the redo, one bounded probe of at most 16 tiles at a finer zoom would settle it. That is outside this spike and needs explicit approval before any fetch.
