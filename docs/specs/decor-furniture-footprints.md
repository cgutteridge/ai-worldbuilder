# Multi-block furniture footprints in decor (task #305)

Original design for task #306; ground support and reporting updated by #444. Written for task #306 (picnic tables, issue I-008, snag S-033), which builds from this note without further design questions. Claims were checked by reading the code on 2026-11-19; the one data check (item 2) was run on the Hampshire extract.

## 0. What exists

- `place_furniture` (`lib/layers/decor_layer.py`) takes one `ground_at` top per item; every recipe voxel is `(x, top+dy, z)`. Recipes in `lib/sources/street_furniture.py` are `(dy, block)` pairs with no `dx`/`dz`, so a table with a bench each side cannot be written.
- `lib/layers/outbuildings.py` (#251) and `place_outbuildings` already answer the multi-column questions for sheds: a footprint is all or nothing; it is refused (not halved) when it crosses the cell edge, and when any column is on a route/clearance/opening (`keep_clear`), a mapped way, a house, or the carriageway. Each column's ground is its own lidar terrain. Task #444 adds a shared `level_and_shift` helper for level furniture support; #446 applies it to sheds and garages separately. Hedges, fences and walls continue to follow terrain.
- Kinds to add: **`picnic_table`** (rectangular, from the map and designer) and **`picnic_table_round`** (designer only; no map tag distinguishes a round one).

## 1. Footprint format (question 1)

A new table `FOOTPRINTS: dict[str, list[tuple[int, int, int, str]]]` in `street_furniture.py`, rows `(dx, dz, dy, block)`, anchored at the **centre column** `(0, 0)` in the canonical orientation (rotation 0). `RECIPES` is unchanged: single-column kinds keep their `(dy, block)` stacks. Add:

- `FURNITURE_KINDS = frozenset(RECIPES) | frozenset(FOOTPRINTS)`; every `in sf.RECIPES` test that means "is street furniture" becomes `in sf.FURNITURE_KINDS` (`decor_plan.py` lines about 395, 600, 613, 695; `street_furniture.py` `designer_items`; the decor plan's map-furniture loop keeps a triple when `kind in FURNITURE_KINDS`). `KINDS` gains the two names.
- `footprint(kind, rotation=0) -> list[(dx, dz, dy, block)]`: for a `RECIPES` kind it returns `[(0, 0, dy, block), ...]`, so `place_furniture` has **one code path** for every kind. For a `FOOTPRINTS` kind it rotates the offsets (section 2).
- Every footprint column is within one block of the anchor (`|dx|, |dz| <= 1`, a 3 x 3 box). This is a contract, not a coincidence: `decor_columns` warms the footprint plus the one-block shift range (radius 2 for current multi-block recipes). A test asserts the radius so a bigger kind has to change the warm-up too.

Blocks (rotation 0, long axis along x):

| Kind | Rows `(dx, dz, dy, block)` | Voxels |
| --- | --- | --- |
| `picnic_table` | `(-1..1, 0, 1, spruce_slab[type=top])`, `(-1..1, ±1, 1, spruce_slab[type=bottom])` | 9 |
| `picnic_table_round` | `(0, 0, 1, spruce_slab[type=top])`, `(±1, 0, 1)` and `(0, ±1, 1)` as `spruce_slab[type=bottom]` | 5 |

A table top at table height (top slab, no leg) over a half block of air is an intended overhang, the exception to the roof-slab support rule.

## 2. Rotation (question 2)

- `rotation` is degrees clockwise seen from above (north is -z), one of 0, 90, 180, 270. One quarter turn maps an offset `(dx, dz)` to `(-dz, dx)` (east `(1, 0)` becomes south `(0, 1)`). The rectangle is 180-degree symmetric, so only 0 and 90 differ; the round table is symmetric under all four. The recorded value is the one given (mod 360), not collapsed.
- **Map point:** no direction exists to read. Checked with `ogr2ogr` on the world's downloaded regional map extract, Highfield bounding box, `other_tags LIKE '%picnic_table%'`: 30 points, tags seen are `covered`, `access`, `bench`, `colour`, `material`, `lit`, `check_date`, none `direction`. So a map table is `rotation 0`, `rotation_source: default`. (The map furniture triple is `(x, z, kind)`; honouring a future tag means widening it, which this task does not do.) `access=private` is present on many; furniture is not filtered by access.
- **Designer item:** row in `reference/decor-furniture.json`: `{"x", "z", "kind": "picnic_table", "rotation": 90?, "note"?}`. `x, z` is the centre of the whole table (the bench-table-bench middle), `rotation` is optional and defaults to 0. `rotation` that is not an integer multiple of 90 makes the row unresolved with that reason (the same handling as a missing x, z), never a silent 0.

## 3. Ground height (question 3)

Every furniture footprint stands on **one level** (ground spread limit **0**). Measure `ground_spread` as the highest minus lowest `ground_at` top over all columns, rather than each column against the anchor. Missing ground keeps the existing reasons (`no ground at terrain`, `feature above terrain`, `no surface`).

When the original anchor fails only on level, try all eight adjacent anchors, nearest first (cardinals before diagonals, ties by x then z). Every candidate must pass every clearance, cell, surface and ownership guard. A successful move is reported as `placed_at`; mapped `x,z` stay unchanged. Road-edge furniture retains its existing kerbside search.

If no adjacent anchor is level, fill low columns at the **original anchor** up to the highest ground, using each low column's local surface block. Never cut terrain; never add more than **2 blocks per column**. Support fill and the raised recipe both pass the voxel guards. Steeper spots remain unplaced with `uneven ground` and `blocked_column`. Hedges, fences and walls are excluded.

Separate cell caps allow at most ceil(30% of final placed items) needing a 1-block fill and ceil(10%) needing a 2-block fill, each at least one. Items without fill count in the denominator. Place unfilled items first, then rank filled items by maximum depth and blocks added, smallest first. Recheck both caps whenever rejecting an item changes the denominator; remove rejected support and furniture together, and report `ground fill cell cap`. These are deterministic planning decisions; no builder model is involved.

Task #444 measurement (2026-11-23, Southampton cell 48,-44): the round table at
6174,-5594 has terrain tops 52,52,53,53,52 (spread 1); the table at
6178,-5592 has five tops at 53 (spread 0). Keep limit 0: it matches the owner's
one-level requirement and the dry run places all 39 accepted furniture items
with supported spread 0. The first table moves west to 6173,-5594; the second
stays mapped. Neither requires fill. The world-owned dry report is
`verification/site/highfield_48_m44_decor-dry-report.json`. This is deterministic
planning against the saved world, without live apply; in-game snag review remains
the owner's check.

## 4. Filters (question 4)

Run on **every footprint column**, whole item refused on the first failing column (reason as today): `cell_contains` (section 8), `way`, `near1` (unless `allow_against_build`), carriageway (`column_ground_kind in ROAD_KINDS`), ground, and the voxel check (not air, `owned`, `masked`).

`reserved_gap` (route, clearance, entrance opening) also applies **per column**, matching outbuildings: a bench over a door opening is as bad as a shed over one. It currently runs at the caller (`plan_cell`, about lines 696-699) on the anchor only. Change: `place_furniture` takes `keep_clear: set = frozenset()`, applies it per column with the existing reason `entrance opening or route reserved`, and `plan_cell` stops pre-splitting `all_furniture`. For single-column kinds the result is identical.

Differences from `place_outbuildings`, and why: no plot rule (furniture carries no plot owner and is not plot-bound); the house test is `near1` (footprints dilated by one, so a table does not stand against a wall) rather than `houses`, unchanged for all furniture.

New for multi-block kinds: `place_furniture` takes `barrier_voxels` (the `found` dict from `place_barriers_from_plan`, which `plan_cell` already has) and refuses a multi-block item that would replace a planned barrier block, reason `overlaps a planned barrier` (a single column keeps today's "furniture wins" behaviour and the `overlap_positions` stat).

## 5. Report rows (question 5)

Placed, multi-block (extra keys over today's row `x, z, kind, source, blocks, on_terrain, moved_to_terrain, note, plan_id`):
`{'x', 'z' (anchor), 'kind', 'source', 'plan_id', 'blocks': n, 'rotation': 90, 'footprint': [[x, z], ...], 'on_terrain', 'moved_to_terrain' (any column)}`. Every row gains `ground_spread` (0 for single-column or level support, null when ground cannot be measured). A moved item carries `original_ground_spread`. Filled rows carry `original_ground_spread` and `ground_fill: {columns: [{column: [x,z], blocks, block}], blocks_added, max_depth}`; their supported `ground_spread` is 0. Single-column rows have no `rotation` or `footprint`. The dry-run stats include `furniture_ground_fill` with `items_placed`, `filled_by_1`, `filled_by_2`, both shares and `refused_for_cap`.

Refused: `dplan.mark_unplaced(row, reason)`, plus measured `ground_spread` (null if unavailable); cap refusals also carry `required_ground_fill`; the copy keeps `rotation` and gains `blocked_column: [x, z]` (the first failing column). Reasons reused: `outside cell` (anchor), `on path/road/water column`, `against build`, `on carriageway`, `entrance opening or route reserved`, `no ground at terrain`, `no surface`, `feature above terrain`, `blocked by live or owned block`. New: `crosses the cell edge` (wording as outbuildings), `uneven ground`, `overlaps a planned barrier`, and **`overlaps another furniture item`**: today `pos in found` shares the message `blocked by live or owned block`, which is wrong when the blocker is another table. Check order inside the voxel loop: `pos in owned` gives `feature above terrain`, else `pos in found` gives `overlaps another furniture item`, else `blocked by live or owned block`. This also corrects two single-column items at one position. Provenance and `furniture_by_provenance` need no change.

## 6. Round table (question 6)

A plus: one top-slab table column with four bench slabs at north, south, east and west; the four corners of the 3 x 3 box stay empty. It is symmetric, so rotation does not change the voxels. It is a separate kind rather than a flag, so every lookup stays by kind, and the designer picks it from the aerial (an aerial table that is clearly circular).

## 7. Blocks and block states under rotation (question 7)

Both recipes use only **rotation-invariant** states (`slab[type=...]`), so rotating offsets alone is correct; no `facing` is involved (a bench of stairs would need `facing` on the outward side and was not chosen). A test asserts that no `FOOTPRINTS` block carries `facing`, `axis`, `half` or `rotation`, so a later kind that needs one must add a rotator (rewrite `facing` by the same quarter turns) at that point; fence and wall connection states must never be written in a recipe (the engine connects them, as `street_lamp` already relies on).

Slab gotcha: `rewrite_bottom_half_slabs` (`site_kit.py`) turns bottom-half slabs into full blocks unless the cell is in `plan.keep_slab_cells`. `plan_cell` already keeps outbuilding roof slabs (`roof_slabs`, about line 753). **Furniture bench slabs must be added the same way**, or every bench becomes a full block. Top-half slabs are untouched.

## 8. A footprint column outside the cell (question 8)

Refused whole, reason `crosses the cell edge`, as outbuildings (`not all(cell_contains(...))`): evaluated for every column, where today it is the anchor only. An anchor outside the cell stays `outside cell`. The owning cell is the one containing the anchor; a table straddling an edge is therefore refused by that cell and the designer moves the anchor inward. `pass_mask.clip_to_cell` stays a backstop and should never fire for furniture.

## 9. Plumbing (question 9)

- `street_furniture.py`: `FOOTPRINTS`, `FURNITURE_KINDS`, `footprint()`; `classify`: `tags.get('leisure') == 'picnic_table'` gives `'picnic_table'`; `EXTRACT_JOBS` WHERE adds `OR other_tags LIKE '%"leisure"=>"picnic_table"%'` (the points layer has `other_tags=yes`; the `LIKE` form was run on the extract above); `designer_items` accepts any `FURNITURE_KINDS` kind and parses `rotation` (invalid gives `rotation_error`); the decor plan's map-furniture loop accepts the kind (rotation 0).
- `decor_plan.py`: `_furniture_item(..., rotation=None)` sets `item['rotation']` only for multi-block kinds, plus `rotation_source` (`default` for map, `designer` for a designer row); `seen_furniture` stays keyed on the anchor `(x, z)`, first source wins (map before designer, as today), so two tables at one centre are one; an overlap with a nearby one is caught at placement by `overlaps another furniture item`; `build_decor_plan` map and designer loops, the `role` at the photo-observation line, and `lidar_objects` use `FURNITURE_KINDS`; a designer row with `rotation_error` becomes an unresolved row; `resolved_furniture` copies `rotation`.
- `decor_layer.py`: `place_furniture` as above (signature adds `keep_clear`, `barrier_voxels`); `plan_cell` passes them and deletes the anchor-only `reserved_gap` split; `keep_slab_cells` for bench slabs; the report rows and `stats['furniture_report']` carry `rotation` and `footprint` (the dry-run JSON and printed rows pick them up from the row dict).

## 10. Aerial / photo observation path (question 10)

A `designer_observations` row for a lidar lump gains the same optional `rotation`; `lidar_objects` passes it to `_furniture_item`. The anchor is the observed `cell`, which for a multi-block kind means the centre of the object, the column the label stands on. Lidar stays a guide: the lump gives `lidar_cells` and `lidar_height` hints only and never sizes or rotates the table. A labelled lump still vetoes map and designer points on any of its columns, which removes a map table duplicated by the label.

`rules/handoff-layered-passes.md` (about lines 256-272): replace the bullet "One classified object places one existing furniture recipe at the observed column on the actual ground, subject to ..." with: one classified object places its kind's recipe at the observed column (the anchor) on the actual ground; a multi-block kind (`picnic_table`, `picnic_table_round`) places its whole footprint centred there at the optional `rotation`, every footprint column passing the guards or the whole item listed unplaced with a named reason. Add the kinds and the `rotation` field to the observation example.

## 11. Inference (question 11)

`infer_street_furniture` emits only `street_lamp` and `bench` (its `kept` and `summary` are keyed by those two) and stays that way: a guess never creates a multi-block kind. An inferred bench that lands on a table's footprint loses at placement (`overlaps another furniture item`; inferred rows sit last in plan order). A test checks that the inferred kinds are a subset of `RECIPES`.

## 12. Tests for #306 (question 12)

- `lib/sources/test_street_furniture.py`: `classify` gives `picnic_table` for `leisure=picnic_table`; `EXTRACT_JOBS` mentions it; `footprint` rotations (rectangle swaps its axis at 90, round unchanged, `RECIPES` kinds give `[(0, 0, dy, block)]`); radius within 1; no state key other than `type` on any footprint block; `designer_items` parses `rotation`, invalid gives `rotation_error`; a map triple whose kind is in `FURNITURE_KINDS` stays on the decor plan (rotation 0).
- `lib/layers/test_decor_layer.py`: rectangular table placed on lawn (9 voxels) and round (5); report row shape (`rotation`, `footprint`); refused on a carriageway when only a bench column is tarmac; refused when one column is in `way`, in `near1`, in `keep_clear` (reason `entrance opening or route reserved`), crosses the cell edge, uneven ground (one column two higher), owned (`feature above terrain`), two overlapping tables (`overlaps another furniture item`), over a planned barrier voxel; rotation 90 changes the footprint; bench slabs reach `plan.keep_slab_cells`; every existing single-column test unchanged.
- `lib/layers/test_decor_plan.py`: map `picnic_table` row (`vector_map`, high, rotation 0); designer row with rotation (`aerial`, medium); same anchor from map and designer is one row; invalid rotation unresolved; observation with rotation; `resolved_furniture` carries `rotation`; inference emits no multi-block kind.

## Files to change

`lib/sources/street_furniture.py`, `lib/layers/decor_plan.py`, `lib/layers/decor_layer.py`, `rules/handoff-layered-passes.md`, the three test files above. Optional: the extract schema `config/osmconf.ini` (`leisure` in the points `attributes`; not needed, `other_tags` suffices). World work, not this task: re-extract existing worlds' `reference/vector_map` (their `downloads/osmconf.ini` copies are not tracked), the designer re-read of 48,-44, the decor redo.

## Not decided here (left open for the owner)

Whether decor furniture reads need finer than z20 aerial (a separate small spike). Legs under the table top, or bench stairs with a facing, if the look is wrong in the world after #306.
