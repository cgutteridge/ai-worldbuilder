# Seam rationalisation review (task #356)

Read-only review of the tool code and `tasks/index.json`, 2026-11-22. Nothing here is filed yet; the owner reviews first. `worlds/` and `.worktrees/` are out of scope (one shallow grep of `worlds/` for importers of `terrace` found none).

How the counts were made: `git grep` plus a small `ast` script that lists, for each name, the files that import or call it outside the defining file, split into production and test files. Name matches can over-count (two modules with a function of the same name), so every "no production caller" claim below was checked by hand. Not executed: no build, dry run or test run was made, so the invalid-block finding in 1.1 is from reading the code, not from running it.

## 1. Bad seams found

Ranked by cost of leaving them. Each has a verdict: **wire** (route everything through the one module) or **delete** (drop the pretence of sharing).

### 1.1 Roof materials (the prompting case)

What exists now, after #355 (catalogue and module), #357 (named roof types) and #366 (house_build wiring):

| Piece | State | Evidence |
| --- | --- | --- |
| `config/roof-materials.json` (36 families, 6 types) and `lib/builds/roof_material.py` | Shared in name. Production callers: `feature_build` (`family_variants`, `roof_material_warnings`), `house_build` (`family_variants`, `roof_blocks`, `roof_class`), `roof_aerial` (`load_catalogue`). | `feature_build.py:161`, `house_build.py:36`, `roof_aerial.py:59` |
| `house_build` | **Already wired by #366.** It resolves blocks through `roof_blocks` / `family_variants` (`house_build.py:92`, `446-450`, `483`, `489`); the old `{mat}_slab` strings are gone. No integration ticket is needed. #356's own note ("house_build still invents `{mat}_slab`") is stale. | `house_build.py:36,92,446-450` |
| `feature_build.block_family_variants` | A one-line wrapper over `roof_material.family_variants` (`feature_build.py:944-956`). Nine uses inside `feature_build`, plus tests that patch it by name (`test_feature_build.py:3615, 3821, 4067-4079`). It only relocates a name. | verdict: **delete** |
| `feature_build.stair()` | Zero callers anywhere (`feature_build.py:929-937`), and it spells `{mat}_stairs` by hand. | verdict: **delete** |
| `feature_build.roof_flat` and two roof-detail kinds | **Still hand-spell `{rm}_slab[type=bottom]`**: `feature_build.py:3367` (flat roof deck), `3771` (`awning_like`), `3774` (`ledge`). #355 migrated the callers of `block_family_variants`; these three never went through it. | verdict: **wire** |
| `terrace.py` `ROOF_FULL` (`terrace.py:105`, used `511`) and `{h.roof}_slab` (`510`) | Only reachable through `Terrace.roofs()`. The `Terrace` and `House` classes have **no production caller**: the only importers are `test_terrace.py` and `geo.py`, and `geo.py` imports just `snap_slope` (`geo.py:39`). `rules/terrace-builds.md:72-77` already says it is "kept, not an apply path". | verdict: **delete** the generator, keep `snap_slope` |
| `outbuildings.py` `ROOF_SLAB` / `ROOF_STAIR` / `ROOF_FULL` (`outbuildings.py:54-56`, used `413-419`) | Live code (decor layers import it). The three tables are exactly the catalogue rows for `spruce` (shed) and `stone_brick` (garage): `spruce_planks / spruce_slab / spruce_stairs` and `stone_bricks / stone_brick_slab / stone_brick_stairs`. | verdict: **wire** (materials only) |

The invalid-block finding (read, not run): the catalogue marks `terracotta`, `black_concrete`, `gray_concrete`, `light_gray_concrete` and `white_concrete` as full-block only (no slab, no stairs), and the `flat_deck` roof type recommends `light_gray_concrete` and `gray_concrete` as alternates (`config/roof-materials.json`, `roof_type_blocks('flat_deck')`). `roof_material_warnings` accepts them for a flat roof. `roof_flat` then writes `light_gray_concrete_slab`, which is not in `config/minecraft-block-ids-26.2.txt` (checked: `smooth_stone_slab` is there; `light_gray_concrete_slab`, `gray_concrete_slab` and `terracotta_slab` are not). The dry run's unknown-block-id line would catch it, but only at dry run. `roof_aerial.nearest_roof_material` filters to families that have a slab (`roof_aerial.py:58`), which is consistent with someone working around this.

Other roof-material leftovers inside the shared module, none a blocker:

- `roof_type_names` and `roof_type_blocks` (`roof_material.py:159, 168`) have **no production caller**; only `test_roof_material.py` and the rules text (`rules/feature-builds.md:78`, `rules/terrace-builds.md:56`) use them, and there is no CLI. The designer is told to call a Python function by hand. Keep (it is designer lookup data) but give it a one-line CLI, or the "shared" types are shelfware.
- The feature dry run still suggests a roof block from `site_kit.nearest_preview_colour` (`feature_build.py:167, 1691`), a 34-row preview table that includes `bed`, `barrel`, `lantern` and `sea_lantern`, not from the catalogue. `roof_aerial.nearest_roof_material` does the same job against the catalogue. Two colour-to-roof tables for one question. Verdict: **wire** the dry run to the catalogue (or to the named types) and delete `nearest_preview_colour`.
- `roof_material.py` imports `site_kit` at module level (`roof_material.py:34`) only for `preview_colour` (`:46`), whose sole callers are tests (`test_roof_material.py:56`). Same for `block_id_names` (`:307`, tests only). Move both into the test or drop the import.
- `roof_class(rise, run)` has one production caller (`house_build`, two call sites). `feature_build` never calls it; it maps `kind: flat|pitched` through `roof_class_for_kind` inside `roof_material_warnings`. Acceptable, but the module docstring's "only slope rule" is true only for materials.

Pitched-roof geometry is the next half-shared seam. `house_build.roof` lazily imports `pitched_roof_states` and `roof_block_y` from the **builder module** `feature_build` (`house_build.py:409`), a 3,978-line file. Roof geometry (`roof_block_y`, `regularise_roof_half_heights`, `roof_ridge_runs`, `pitched_roof_states`, `_roof_stair`, `_roof_slab`) sits at `feature_build.py:959-1300`. The stair-corner rule it uses lives in an audit module (`lib/audit/roof_lint.py:50`). The edge-distance flood that builds the roof heightfield is written twice: `feature_build.py:3379-3400` and `house_build.py:428-442` (including the ring-growing step before it). Verdict: **wire** through a `roof_geometry` module that both builders import, so neither imports the other and the audit module stops being a dependency of builders.

### 1.2 Cell and grid vocabulary (cellmath was extracted, then wrapped again)

`lib/geo/cellmath.py` owns cell maths (`bounds` `:165`, `cell_of` `:153`, `parse_cell` `:114`, `mc_tile_for_cell` `:286`, `N4/N8/DIRS/OPP` `:28-31`). Eleven relocating wrappers remain, several saying "delegates to cellmath":

- `cellmath.cell_bounds` (`:178`, alias of `bounds`)
- `world_frame.parse_cell`, `cell_of`, `cell_bounds` (`world_frame.py:415, 424, 435`; the only external use is `geofabrik_provider.py:180`)
- `coverage.cell_of`, `cell_bbox` (`coverage.py:105, 115`, re-importing with `noqa: F401` at `:65, :101`)
- `pass_mask.cell_bounds` (`:36`), `plant_cell_canopy.cell_bounds` (`:115`), `scripts/cell_marker.cell_bounds` (`:69`, wraps `coverage.cell_bbox`)
- `ground_sources.cell_box` (`:158`, nine importing files) and `ground_sources.mc_tile_for_cell` (`:638`)

Plus single-purpose duplicates: `osm_extraction.cell_bbox_mc` (`:117`), `streetview_sample.parse_cell` (`:53`), `cell_registry.parse_cell_id` (`:88`, this one has its own error type and is arguably fine).

Direction tables are redefined although `cellmath` has them: `feature_build.py:181, 312-314`, `house_build.py:44-48`, `entrance_approach.py:10`, `water_body.py:75`, `garden_sources.py:806`, `roof_lint.py:35-38`. Connected-component finders: `coverage.components` (`:169`, deterministic order, takes a neighbour set), `garden_sources.components` (`:712`), `roof_aerial.components` (`:89`), `ground_layer._components` (`:384`), `water_body._components` (`:455`), and `outbuildings._components` (`:65`), which is a wrapper that only calls `garden_sources.components`. Four of them return in `set.pop()` order, so their output order is not stable; callers must be checked before merging. Verdict: **delete** the wrappers and import from `cellmath`; **wire** neighbour tables and one `components(cells, connect)` into the same home.

### 1.3 Aerial and mosaic paths (this is what produced the stale `MOSAIC` imports)

`lib/evidence/aerial_coverage.py` is the mosaic source (`Mosaic.pixels`, `mosaics()`, `covering_mosaic`, `samples`, `cell_image`). Fixes #370 and `ae6a19b` repointed stale `from ground_sources import MOSAIC` imports there. Remaining parallel paths:

- Registered user aerials (`*.registration.json`) are found by **three separate globs**: `ground_sources.AERIAL_USER_GLOB` (`:511`) with its own image loader and matrix sampler (`registered_aerials`, `registered_rgb`, `:516-566`), `evidence_sheet.py:187` (own glob, own `except Exception: continue`), and `aerial_registered.py` (the library the CLI uses; `roof_aerial` uses it). `ground_sources` applies the matrix itself instead of calling `aerial_registered.to_px`.
- `aerial_annot.py` parses the mosaic filename with its own regex, has its own pixel projection, a hard-coded Portswood default path (`aerial_annot.py:22-23`), runs at import time from `sys.argv`, has no test and no importer. `aerial_coverage.covering_mosaic` and `Mosaic.pixels` do the same job.
- `bakeoff/model_bakeoff.py:2399-2405` has its own projection and a hard-coded `susu` mosaic default. The bake-off is closed (`bakeoff/OVERVIEW.md`), so leave it.
- `feature_build.sample_columns_rgb` uses only `aerial_coverage.samples`; ground and decor use `ground_sources.aerial_rgb`, which layers registered aerials over the mosaic. A roof colour suggestion and the ground classifier can therefore disagree on the same column.

Verdict: **wire** one discovery-and-sampling interface (a mosaic and a registered image both expose `pixels(points)` and `image()`), rewrite `aerial_annot` on it, and have `ground_sources` and `evidence_sheet` call it. Delete the three globs.

### 1.4 Retired tree code kept as live-looking modules

- `lib/layers/foliage.py` (407 lines): production callers exist for only two things, the `REFINE_PLANTS` set (imported by `land_use.py:176, 427`) and `corridor_columns_in_cell` (called only from the retired planners below). `tree_voxels`, `expand_tree`, `plan_tree`, `canopy_audit`, `format_canopy_audit`, `assert_canopy_replant_ok`, `affected_parcels`, `plan_columns_xz`, `is_canopy_block`, `strip_corridor_canopy_overhang` have no production caller; `expand_tree`, `plan_tree`, `assert_canopy_replant_ok`, `strip_foliage_above_surface` and `is_refine_foliage` are exercised only by tests (`test_foliage.py`, `test_ground_layer.py:969,1034`). Its own `tree_voxels` is a second tree generator beside `plant_cell_canopy.tree_voxels`.
- `lib/layers/plant_cell_canopy.py` (524 lines): `rules/foliage.md:111-112` says the module's CLI is **retired, do not use**. The code agrees: `parcels_in_cell` and `columns_for_parcels` just `raise SystemExit` (`:40-47`), yet `build_plan` (`:356`) and `plan_hand` (`:421`) still call them, and a Highfield-only area name and plan prefix stay hard-coded (`:50-62`). The live parts are `tree_voxels`, `bush_voxels`, `crown_voxels`, the species tables and `RNG_SEED`, imported by `trees_layer.py:44` and `decor_layer.py:858`.

Verdict: **delete** the retired planner, CLI wrapper and dead foliage functions; keep the voxel generators under a name that says what they are (tree voxels), not "plant cell canopy".

### 1.5 Parallel vocabularies: tree species, hedge leaves, wood tags, building class

- Tree species: `land_use.TREE_SPECIES = (oak, birch, dark_oak)` (`:40`), `plant_cell_canopy.SPECIES` (six names with log and leaves, `:165-172`), `SPECIES_CROWN_RADII` and `SPECIES_TRUNK_WIDTH` (`:175-176`), `trees_layer.SPECIES_HEIGHTS` (`:77`, a missing key is a `KeyError` at `:659`), `foliage.LOG_SPECIES` (eight names, `:15`). Adding a species edits at least five places, with different name sets. `style` and `species` are also one overloaded argument (`plant_cell_canopy.py:179-215`).
- Hedge / default leaf block spelled out separately in `decor_plan.py:28`, `garden_sources.py:43`, `foliage.py:26`, `plant_cell_canopy.py:59`, as a bare literal at `ground_layer.py:1544` and `decor_layer.py:929`, and four times in `terrace.py`.
- Wood and scrub map-feature tags: `ground_layer.WOOD_FLOOR_TAGS` (`:101-104`), `decor_layer.WOODLAND_DECOR_TAGS` (`:686-690`), an inline test in `tree_lines.py:394`, the wood source in `land_use` and the `forest` list in `config/land-use-classes.json:404`. The land-use table is meant to own tag meaning.
- Building class: `garden_sources.NON_RESIDENTIAL_BUILDINGS` and `nonresidential_building` (`:191-200`) is a second residential-or-commercial table next to `config/land-use-classes.json`. It has **no production caller** (only `test_garden_sources.py:220-224`); `meta_class` (`:208-216`) keeps a legacy `nonresidential` flag fallback only because of it.
- Land-use class keys exist twice: the `CLASSES` tuple (`land_use.py:25`) and the ids in the JSON, checked against each other by the validator. Cheap to leave, noted for completeness.
- Plant-name substrings: `surface_probe.PLANTS` (`:20-27`), `lidar_heightmap._PLANT` (`:58-60`, a different, hand-kept list) and `foliage.REFINE_PLANTS` (`:19-24`, a deliberate placement subset).

Verdict: **wire** species (one table: log, leaves, crown, trunk, heights; `land_use` validates against it) and the wood tag set (read from the land-use table); **delete** the unused building-class table.

### 1.6 Smaller items (not filed; fold in if the owner wants)

- `lib/geo/geodetic.py` is a pure re-export of `world_frame` ("compatibility re-exports"), four importers (`geo.py:36`, `osm_extraction.py:19`, `streetview_sample.py:40`, the lidar tile client (`:169`)). `lib/geo/osgb.py` is a thin wrapper over `world_frame`; its `offsets=` branch rounds separately from `world_frame.osgb_to_mc`. Verdict: delete `geodetic`, shrink `osgb`.
- Air-like block sets defined eight times with small differences: `floaters.py:12`, `cell_journal.py:78`, `ground_layer.py:68`, `road_labels.py:44`, `site_kit.py:464`, `lidar_heightmap.py:56` (adds `light`), `osm_footprints.py:79` (adds `light` and `water`), `cell_marker.py:49`. Some differences are deliberate; a shared base plus named extras would show which.
- Builder names `('house_build', 'feature_build')` are typed in `builder_registration.py:42, 57` and `feature_reapply.py:36, 41`, and the provenance call sites (`feature_build.py:3956`, `house_build.py:577`). One constant would do.
- `lib/sources/legacy_reference_paths.py` holds one constant (the vector-map reference name) for one importer (`vector_map_clips.py:23`) and needs an allowlist entry in `provider_name_audit.py:105`.
- `shims/lidar_tiles.py` and `shims/lidar_world.py` have no production importer (only `test_lidar_provider_integration.py:147`); their docstrings say to use `lidar_provider.tile_client()` / `composite_world()`, which production does.
- `facade_plan.py` validates face slots `roof_edge` and `detail` that nothing reads (`:15, 77, 481-492`); `roofs[label].edge` and `details` are the real homes. Two homes for one fact, one inert.
- Stale text that tells an agent to run something that does not exist: `rules/craftsmanship.md:99` and `rules/rules-context.md:30` say build with `scripts/terrace.py` (there is no such script); `lib/site/rules_context.py:245` still hints "terrace.py generator". `rules/terrace-builds.md` is really the `house_build` rulebook under a misleading name.
- Place memory in tool code, outside this review's scope: `terrace.BROADLANDS_N_SLOPE` (`terrace.py:32`) and the Broadlands camera tables in `scripts/site_views.py:155-170`.
- Accepted by design, not a finding: the ~90 two-line `scripts/*.py` runpy wrappers over `lib/` (documented layout) and the provider `shims/` for historical names.

## 2. Open-task interactions

Nine active tickets in `tasks/index.json`. None needs closing. Proposals only; nothing applied.

| Ticket | Interaction | Proposal |
| --- | --- | --- |
| #256 Trial: railway curve and branch | Spike, prototype lives outside the tree. Risk is only that the follow-up implementation tasks it files add a new block table or a new cell/aerial helper. | Add one line to its notes: reuse `cellmath` and the aerial interface; no private tables. No blocker. |
| #257 Trial: rule-built typology buildings | Says pitched roofs use `pitched_roof_states` "as it is today", and wants per-district palette and roof form. The function is about to move (task D). Flat decks from the `flat_deck` type hit the invalid slab path (task A). Class recipes must key by land-use class, not residential only. | Rewrite two phrases in the notes: roof geometry comes from the roof geometry module once it lands; palettes pick catalogue roof types, not free strings. Soft-order after task A (do not choose concrete flat decks before it). |
| #318 Woodland | Notes still say "blocked by #348-#351"; all four are done and `blockedBy` is empty, so the text is stale. Its remaining work tunes species weights and may add species, which today means editing five tables (1.5) and sits on a retired module (1.4). | Rewrite the stale blocker sentence. Add `blockedBy` on tasks G and H so species tuning happens once, on the single table. |
| #326 River levels trial | World work. Overlaps #352 (its own note says do them together). No seam interaction. | None for this review. |
| #333 Shop typology | Blocked by #257. Says "reusing the terrace builder (scripts/house_build.py)"; the name points at the generator task C deletes. Dormers are roof geometry (task D). | Rewrite "terrace builder" to "house_build / feature_build". Add task D as `blockedBy` once filed. |
| #352 Itchen join check | World work. No seam interaction. | None. |
| #356 This review | Notes say house_build still invents `{mat}_slab`; #366 fixed that. | Close after the owner files the tasks below, with a note that house_build was wired by #366. |
| #358 House roofs: real-spec validation | Notes still wait on "the roof-selection work (#357)", which is done. Its dry run depends on the pitched geometry that task D will move (behaviour-preserving, golden-tested). | Remove the #357 wait from the notes. Run its dry run after task D if D lands first; otherwise do not hold it. |
| #360 Tree belts: evidence tuning | Builds on done #367/#368; thresholds live in the land-use table. No new seam. | None. |

Done tickets worth re-reading: #355's "Task 2" deferral is what left the catalogue with one caller for a day; #366 closed it. #357 added `roof_type_blocks` without a caller.

## 3. Proposed tasks

Eight tasks, all `builderTask: false`, all doable with synthetic fixtures and unit tests (no live world). Task ids are assigned by `tasks.mjs add`; `nextTaskId` is 371 at the time of writing, so adding them in the order below gives A=371 through H=378. `blockedBy` is given by letter; translate when filing.

Order and rank: A first (it is the only one that can write invalid blocks), then B, C, D, E, F, G, H. A, C, E and F have no blockers and can run in parallel.

### A. Feature roofs: write flat-roof and roof-detail slabs through the roof catalogue

- priority: high; complexity: s; builderTask: false; blockedBy: none
- Scope: `roof_flat` (`feature_build.py:3367`) and the `awning_like` and `ledge` detail kinds (`:3771, 3774`) hand-spell `{rm}_slab[type=bottom]`. Make them use `roof_material.family_variants`: slab where the family has one, the full block where it does not (the same fallback `house_build` uses). Delete the `block_family_variants` wrapper (nine uses, `feature_build.py:944`) and the unused `stair()` (`:929`); import `family_variants` directly and repoint the tests that patch `F.block_family_variants` (`test_feature_build.py:3615, 3821, 4067-4079`) at the new name. Check whether `roof_aerial.nearest_roof_material`'s "has a slab" filter (`roof_aerial.py:58`) can then be dropped. Do not change roofs for materials that already work.
- Check: a synthetic flat roof with `light_gray_concrete`, `terracotta` and `smooth_stone` plans only block ids present in `config/minecraft-block-ids-26.2.txt`; the existing golden `_roof_material_migration_plan` test (`test_feature_build.py:4001-4131`) still passes unchanged for brick, spruce and deepslate_tile; `grep -n "block_family_variants\|{rm}_slab" lib/builds/feature_build.py` returns nothing; `python3 scripts/run_tests.py --only test_feature_build` passes.

### B. Outbuildings: take shed and garage roof blocks from the roof catalogue

- priority: medium; complexity: s; builderTask: false; blockedBy: A
- Scope: replace `ROOF_SLAB`, `ROOF_STAIR` and `ROOF_FULL` (`outbuildings.py:54-56`) with one `ROOF_FAMILY = {'shed': 'spruce', 'garage': 'stone_brick'}` and read full, slab and stairs from `roof_material.family_variants`. Keep the gable geometry (`pitched_roof`, `roof_row`) as is; it is a different, simpler shape and not part of this task. Leave `WALL_BLOCK` and `DOOR_BLOCK` alone.
- Check: record `outbuildings.voxels` output for a synthetic shed and a synthetic garage (odd and even width, flat and pitched) before the change and assert it is identical after; `grep -n "ROOF_FULL\|ROOF_SLAB\|ROOF_STAIR" lib` finds none; `python3 scripts/run_tests.py --only test_outbuildings` passes.

### C. Delete the terrace generator; keep `snap_slope`

- priority: medium; complexity: s; builderTask: false; blockedBy: none
- Scope: `Terrace`, `House`, `ROOF_FULL`, `line_x`, `passage_xs` and the helpers only they use (`terrace.py`, about 700 of 749 lines) have no production caller. Move `snap_slope` and `_FRIENDLY` to `lib/geo/geo.py` (its only importer), move the three `snap_slope` tests with it, drop `BROADLANDS_N_SLOPE` and the generator tests, delete `lib/builds/terrace.py` and `test_terrace.py`. Fix the text that points at the non-existent `scripts/terrace.py`: `rules/terrace-builds.md:72-77`, `rules/craftsmanship.md:99`, `rules/rules-context.md:30`, `lib/site/rules_context.py:245` and its test, `scripts/site_views.py:163`. Do not touch world repos.
- Check: `grep -rn "import terrace\|from terrace" lib scripts` returns nothing; `snap_slope` tests pass from their new home; `python3 scripts/run_tests.py` is green; `grep -rn "scripts/terrace.py" rules lib` returns nothing.

### D. Shared roof geometry module for house and feature builders

- priority: medium; complexity: m; builderTask: false; blockedBy: A
- Scope: move `roof_block_y`, `regularise_roof_half_heights`, `RoofRidgeRun`, `roof_ridge_runs`, `pitched_roof_states` and the `_roof_stair` / `_roof_slab` state helpers (`feature_build.py:959-1300`) into `lib/builds/roof_geometry.py`, together with the stair-corner rule from `roof_lint.stair_shape_from_neighbours` (the audit then imports it from there). Add one `edge_distance_field(own, blocked)` that replaces the two copies of the ring-grow and Chebyshev flood (`feature_build.py:3379-3400`, `house_build.py:428-442`). `house_build` stops importing the builder module (`house_build.py:409`). Behaviour must not change; `feature_build` keeps thin imports only where tests need them.
- Check: golden plans for a feature roof (pitched, with zones) and a house roof (flatish, pitched square, L-shape, group) recorded before the move are byte-identical after; `grep -n "from feature_build import" lib/builds/house_build.py` returns nothing; `roof_lint` tests still pass; `python3 scripts/run_tests.py --only test_house_build`, `--only test_feature_build` and `--only test_roof_lint` pass.

### E. One cell and grid vocabulary: delete the cellmath wrappers

- priority: medium; complexity: m; builderTask: false; blockedBy: none
- Scope: delete the relocating wrappers listed in 1.2 (`cellmath.cell_bounds`, `world_frame.parse_cell/cell_of/cell_bounds`, `coverage.cell_of/cell_bbox` and their `noqa` re-imports, `pass_mask.cell_bounds`, `plant_cell_canopy.cell_bounds`, `cell_marker.cell_bounds`, `ground_sources.cell_box` and `mc_tile_for_cell`) and point every caller at `cellmath` (nine files import `cell_box`). Replace the redefined `N4/N8/DIRS/OPP` tables with `cellmath`'s. Then replace the component finders (`garden_sources.py:712`, `roof_aerial.py:89`, `ground_layer.py:384`, `water_body.py:455`, `outbuildings.py:65`) with `coverage.components(cells, connect)`; before merging each, check whether the caller depends on result order and sort at the call site if it does. Split the component step into its own task if the first part gets large. Task G also edits `plant_cell_canopy.py` and deletes its `cell_bounds`; if G lands first, skip that wrapper here.
- Check: `git grep -nE "def (cell_box|cell_bounds|cell_bbox|cell_of|parse_cell|mc_tile_for_cell|_?components)\b" lib scripts` lists only the `cellmath` and `coverage` definitions (plus `cell_registry.parse_cell_id`, kept for its error type); a fixture test shows `components` gives the same sets for 4- and 8-connected input as each removed copy; `python3 scripts/run_tests.py` is green.

### F. Aerial sources: one discovery and sampling path for mosaics and registered images

- priority: medium; complexity: m; builderTask: false; blockedBy: none
- Scope: in `lib/evidence/`, give mosaics (`aerial_coverage.Mosaic`) and registered images (`aerial_registered`) one shared small interface (`pixels(points)`, `image()`), with one discovery function for each kind. Move `registered_aerials` and `registered_rgb` out of `ground_sources.py:511-566` to use it (`ground_sources.aerial_rgb` keeps its mosaic-then-registered layering by calling it). `evidence_sheet.py:187-205` drops its own glob. Rewrite `aerial_annot` as a function-based tool on `covering_mosaic` and `Mosaic.pixels`: no import-time `sys.argv`, no hard-coded Portswood path, with a test. Leave `bakeoff/` alone (closed).
- Check: synthetic 2x2-tile PNG mosaic and a synthetic `.registration.json` fixture; `git grep -n "registration.json" lib` shows the glob in exactly one place; `ground_sources.aerial_rgb` returns the same colours as before on the fixture (registered wins where it covers); `aerial_annot` crop is produced for a fixture bbox with no default mosaic path in the source; `python3 scripts/run_tests.py` is green.

### G. Retire dead tree and foliage code

- priority: medium; complexity: m; builderTask: false; blockedBy: none
- Scope: delete the retired planner in `plant_cell_canopy.py` (`parcels_in_cell`, `columns_for_parcels`, `build_plan`, `plan_hand`, `volume_clear`, `read_mask`, `write_mask_overlay`, the hand-crown list, the Highfield constants, `main`) and `scripts/plant_cell_canopy.py`; rename what remains (`tree_voxels`, `bush_voxels`, `crown_voxels`, species tables, `RNG_SEED`) to a module named for tree voxels and update `trees_layer`, `decor_layer` and `scripts/test_live_read_sweep.py`. Delete the foliage functions with no production caller (1.4); keep `REFINE_PLANTS` where `land_use` can reach it without importing a placement module (for example beside `surface_probe.PLANTS`). Update `rules/foliage.md:15, 111-112` accordingly. This task does not change which species exist.
- Check: voxel output of `tree_voxels` for oak (round and plane), birch and dark_oak, and of `bush_voxels`, is identical to a recording made before the move; `git grep -n "parcels_in_cell\|columns_for_parcels\|build_cell_foliage" lib scripts` finds nothing; `python3 scripts/run_tests.py` is green; `scripts/plant_cell_canopy.py` is gone and no doc names it as usable.

### H. One tree-species table, and the wood-tag set read from the land-use table

- priority: medium; complexity: m; builderTask: false; blockedBy: G
- Scope: one table keyed by species (log, leaves, crown radii, trunk width, trunk heights), used by the tree voxel generator, `trees_layer` (`SPECIES_HEIGHTS`) and `land_use` (`TREE_SPECIES` is derived from it, `species_weights` validated against it). Split `style` from `species` in the generator signature. One `DEFAULT_LEAVES` / hedge block constant replaces the six spellings in 1.5. `ground_layer.WOOD_FLOOR_TAGS`, `decor_layer.WOODLAND_DECOR_TAGS` and the inline test in `tree_lines.py:394` read the wood and scrub tags from `land_use` (or one shared tuple) instead of retyping them. Delete `garden_sources.NON_RESIDENTIAL_BUILDINGS` / `nonresidential_building` and the legacy `nonresidential` fallback in `meta_class` if the tests that set the flag can use a land-use class instead. Must land before #318 tunes species.
- Check: adding a fixture species (for example `spruce`) is one table row and appears in `land_use` validation, tree voxels and heights with no other edit (test does this on a copy of the table); the existing species and woodland tests (#350, #348, #351) pass unchanged; `git grep -nE "oak_leaves\[persistent=true\]" lib` finds one definition; `python3 scripts/run_tests.py` is green.

### Not filed (candidates if the owner wants more, from 1.6)

Delete `geodetic.py` and shrink `osgb.py`; one air-block base set with named extras; one `SPEC_BUILDERS` constant; fold `legacy_reference_paths` into `vector_map_clips`; delete the `lidar_tiles` / `lidar_world` shims or give them a caller; remove the inert `roof_edge` and `detail` face slots from `facade_plan`; a one-line CLI for `roof_type_blocks`; point the feature dry-run roof-colour suggestion at the catalogue types and delete `nearest_preview_colour` (could join task A, but it changes dry-run output, so keep it separate); drop the test-only `site_kit` import from `roof_material.py`.
