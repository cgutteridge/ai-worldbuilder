# Housekeeping review: bloat, dead code and stale docs

Follow-up to the seam review (`docs/archive/seam-rationalisation-review.md`, task 356). Those eight follow-ups (371–378) have all landed, so this pass looks wider: dead code, retired subsystems still in the tree, place names in tool code, oversized modules, stale and duplicated docs, and repo weight. Read-only, 2026-11-22. Filed after owner review on 2026-11-22 (section 3 maps letters to ticket ids).

## How this was made

- **Dead-code scan.** An `ast` scan listed every top-level function or class in non-test Python that no other non-test file names. Every "no caller" claim was then checked with `git grep` over the whole repo, `.md`, `.mjs` and `.json` included, excluding `worlds/` and `.worktrees/`.
- **Path scan.** Every repo path named in `rules/`, `.agents/skills/`, READMEs, root docs, `docs/` and `reference/TYPES.txt` was checked. A second pass checked `module.attr` names, `--flags` and snake_case identifiers in the docs against the code. World-relative paths such as `config/world.json` were set aside as valid.
- **Area reads.** Four read-only subagents each read one area in full: builds, blocks and audit; layers, sources, evidence and providers; journal, site, geo, scripts and the bake-off; docs, rules, skills and templates. Their main claims were spot-checked by grep before going in here.
- **Not done.** Nothing was executed against a world. The two runtime claims, 1.3 (import failure) and 1.14 (the `--help` file names), come from reading the code and from file names on disk.
- **Test suite.** Full suite at review time: 2,677 tests, 80 s wall clock. One test failed: the provider-name audit flags the vendor token in the owner's uncommitted drafts `docs/plans/railway.md:16` and `docs/plans/bridges-and-tunnels.md:26`. It will block a full-suite commit until those lines are reworded.

## Owner decisions (2026-11-22)

These settle the open choices in the tickets below. Each ticket follows the decision; the alternatives in its scope are no longer open.

| Topic | Decision | Ticket |
| --- | --- | --- |
| Coverage parcels | Retire the parcel half; keep the registry rollup and holds | T5 |
| Closed bake-off | Freeze in place: keep the files and protocol, drop the `scripts/` shims, stop the suite running its tests by default, mark the skill historical | T15 |
| House margin shelf | Houses follow task 115: clear the margin without filling it; stoop and step cells keep their ground | T18 |
| Feature greeblies and contact-sheet gate | Drop it with the queue code (it only fires for retired QUEUE items) and reword `rules/feature-builds.md:251` | T17 |
| Done plans and specs | Move to `docs/archive/` after harvesting their decisions; do not delete | T14 |
| `docs/snags/` and the dry-run plan | Move to southampton-build | T14 |
| `.cursor/rules/agent-soundboard-flair.mdc` | Delete from the repo; `docs/agents/world-and-live-commands.md` becomes the one cue list | T20 |
| Project skill `task-index-manager` | Rename it (for example `worldbuilder-tasks`), regenerate the stubs and drop the warning in `issue-tracker.md` | T20 |

## Definition of done for every ticket below (the anti-myopia checklist)

Each ticket's scope lists the touch points found here, but grep lists can miss things. Each ticket's check therefore includes:

1. For every name deleted, renamed or moved: `git grep -nw <name> -- ':!worlds' ':!.worktrees'` returns only the new home. Include `.md`, `.mjs`, `.json`, `.txt`, `.ini`, `.mdc`, `.githooks/` and `templates/`, not just `.py`.
2. Docs that index code are updated in the same commit: `lib/README.md`, `scripts/README.md`, `docs/agents/layout.md`, `GLOSSARY.md`, every `rules/*.md` and `.agents/skills/*/SKILL.md` that names the thing, `reference/TYPES.txt`, and `templates/`. If a skill changes, run `node scripts/check-skill-stubs.mjs --write`.
3. World-side data (`worlds/<w>/config`, `site/`, `reference/`) is changed in that world's own repo, in a commit named in the ticket's close note. Grep every world under `worlds/`, not just the active one.
4. Tests that only guarded deleted code go with it, and tests that patch a moved name are retargeted.
5. Code and docs that link to a moved or archived doc are repointed.
6. `python3 scripts/run_tests.py` is green. Once T1 lands, its path audit test passes too.

## 1. Findings

V means verified by grep; J means a judgement call.

### 1.1 Coverage-parcel subsystem looks retired but is still taught (V, plus J on the verdict)

- `lib/journal/coverage.py` (1,426 lines) reads `AREAS_FILE = ROOT / 'config/coverage-areas.json'` (`:70`). `ROOT` is the active world. **No world has that file or a parcel store.** As a result, `build`, `gaps`, `signoff` and `map` have nothing to work on.
- Sign-off leftovers still in the code:
  - `signoff_parcels` (`:1232-1290`, no caller)
  - `parcels_intersecting_columns` (`:698`, no reference)
  - `append_signoff` (a no-op)
  - `load_signoffs`, which returns `{}` but is still imported by `cross_zone.py:15,117` and `cell_sources.py:35,349`
  - the `signed` argument of `parcel_state`
  - `COVERAGE_CHEATSHEET` (`:37-49`), which still lists `signoff`, `--backfill` and `--revoke`
  - the module docstring (`:24-25`, "Done needs … a sign-off")
- Live parts: `registry_rollup_rows`, `registry_rollup_text`, `replace_section` and the registry half of `write_coverage` (`:946-1075`), which write `site/COVERAGE.txt` (present in two worlds), plus holds.
- Still taught or described in:
  - `rules/process.md`: `:16-18`, `:120-137`, `:186`, `:213-214`, including `coverage.py build --area highfield`
  - `rules/handoff-layered-passes.md:214,245`
  - `cell_sources.py:379`
  - `templates/new-world-site/COVERAGE.txt`
  - `docs/adr/006-land-use-classes.md:291`
- `.agents/skills/cell-orchestrator/SKILL.md:18` calls sign-offs retired.
- Verdict: owner decision. Retire the parcel half and keep the rollup, or restore an areas file.

### 1.2 Southampton roads hard-coded into every world's map features (V)

- `lib/sources/ground_sources.py:195-213`: `SUPPLEMENT_ROADS` holds centrelines for cells (49,-42) and (50,-42), including "University Rd". `load_osm` appends them in **any** world whose cells use those numbers.
- No test, rule or doc mentions it.
- Verdict: move the rows into southampton-build data, read them if present, and delete the constant.

### 1.3 Tool code imports Southampton-only world providers (V; the failure path was read, not run)

- Four world-provider shims hard-code a Southampton package: `shims/portals.py:14`, `shims/buildings.py:14`, `shims/tpo_fetch.py:14`, `shims/aerial_mosaic.py:14`.
- On import, each calls `load_world_provider`, which raises `ImportError` when the package is missing (`world_provider.py:53,58`).
- `lib/journal/cell_features.py:51-52` imports `portals` and `buildings` at module level.
- Every path that loads `cell_features` therefore fails in a world without those packages: `cell_registry.py:581` (candidates), `:919`, `:958`, `registry_next.py:113` and `readiness_evidence.py:218`.
- **Only southampton-build has the packages.** Ventnor has a `local-providers.json` without them; Sheffield and Shanklin have no `local-providers.json` at all.
- Other importers: `evidence_sheet.py:404`, `site_handoff.py:357`, `rules/craftsmanship.md:245` (`portals.for_footprint`) and `.agents/skills/redo/SKILL.md:106`.
- `shims/osm_extract.py` already resolves by evidence type, so it is the model.
- Verdict: resolve by catalogue role (several rows share the `poi_register` type), and make the tool-side imports optional. **Highest-value code fix.**

### 1.4 `site_handoff.py` is about 90% retired queue code (V on callers, J on retirement)

- `main()` (`:1550-1570`) dispatches only to `next`, `end`, `status`, `hook-stop` and `hook-start`.
  - `next` repeats `cell_registry.py next`.
  - `status` and the two hook commands read `QUEUE.txt` and `.handoff-pending.json`. No world has these files, and no hook config uses the commands (checked `.claude/settings.json`, `.cursor/`, `.githooks/`).
- No caller anywhere:
  - `next_cell`, `plan_cell_items`, `layers_done`, `brief_text`
  - `next_google_review`, `google_review_item`, `_append_queue_items`, `copy_clipboard`
  - `requeue_plan` (reached only through `next --plan`), `lidar_facts`, `building_facts`, `move_player_creative_view`, `announce_ready`, `lock_status`
- Test-only: `map_building_tag_facts`, `mapped_entrance_facts`, `portal_facts`.
- **Live and worth keeping (about 150 lines):** the cell-gaps block, which skills and rules use. It is `TREES_OVER_ROOF` (`:120`), `_view`, `residual_features` (`:123-146`), `lidar_lumps` and `_inside` (`:238-254`), `resolved_trees_over_roof`, `trees_over_roof_open` and `cell_gaps` (`:414-496`).
- `lib/site/rules_context.py` (293 lines, plus `test_rules_context.py`) has one caller, site_handoff (`:41,1022,1059`). It classifies retired Q-nnn items by keyword (`BROADLANDS`, `HIGHFIELD_Q`, `:105,114`). `rules/rules-context.md` describes it.
- `site_archive.py` still archives `QUEUE` and `ISSUES` (`:9,284-295,339`, `next-id Q|I`) with "SOUTHAMPTON REFINE" headers. Its only automated caller is `site_handoff.cmd_end:1455`. The `REVIEWS` archive is still taught (`rules/process.md:21,70`, template README `:12`) and stays.
- **Risk:** `cmd_end` is the only enforcement point of the feature greeblies and contact-sheet refusal (`:757-873`), which `rules/feature-builds.md:251` relies on. Move that gate into `feature_build`, or drop the rule, before deleting.

### 1.5 Code that is tested but never run, sometimes while a rule says it runs (V)

These are worse than plain dead code: a test or a rule gives false assurance.

| Name | Where | What is wrong | Verdict |
| --- | --- | --- | --- |
| `pass_mask.masked_overlaps` | `lib/layers/pass_mask.py:262` | `rules/handoff-layered-passes.md:220` says it runs before apply; nothing calls it | wire, or reword the rule |
| `decor_plan.resolved_barriers` | `:1112-1142` | 13 test uses; production `decor_layer.place_barriers_from_plan` (`:440-563`) reimplements it | wire (delete the copy) |
| `water_body.water_body_plan_item` | `:324` | named in `rules/water-body-builds.md:28`; `water_body_components` builds the same dict again (`:499-522`) | wire (components calls it) |
| `roof_material.block_id_names` | `:307` | `feature_build.py:3189-3192` re-reads the block-id list inline | wire (or move to `lib/blocks`) |
| `registry_verify.check_uncovered_footprints`, `check_clip_query_coverage` | `:181, :218` | dropped from `run_verify` by `88ac28d`; `ReadinessPolicy` covers them (`cell_readiness.py:326-339`) | delete, with `test_registry_verify.py:74-83,120-161` and `test_plan_ids.py:152-168` |
| `landmark_gate.is_landmark`, `annotate_landmark_candidates`, `tags_by_ref_from_index` | `:42-107` | production uses only `landmark_fields` (`cell_features.py:59`) | delete, with `test_landmark_gate.py:30,51-63` |
| `facade_plan.placed_entrances`, `unresolved_entrances`, `unresolved_entrances_for_cell` | `:193-221` | test-only | delete |
| `street_furniture.map_items`, `merge_items` | `:367-391` | test-only; named in `docs/specs/decor-furniture-footprints.md` | delete and fix the spec |
| `vector_map_clips.has_clips`, `is_under_world`; `osm_cell_extract.landscape_layer_presets`; `garden_sources.role_vetoed` | various | test-only | delete |
| `cellmath.cell_shift`, `filename_cell_token`, `region_of_block` | `:102, 143, 248` | test-only | delete |
| `build_roles.format_roles_block` | `:124` | test-only, yet `cell-orchestrator/SKILL.md:118` tells agents to paste its text "from `rules/build-roles.md`", where it does not appear | add `show --block` to the CLI, or delete it and fix the skill |
| `pre_commit_checks.is_tracker_only_paths` | `:138` | test-only | delete |

### 1.6 Plain dead code (V: no reference anywhere outside its own definition)

- `world_frame.mc_to_wm` (`:668`)
- `cell_readiness.accounted_elsewhere` (`:121`)
- `facade_plan.load_and_validate_facade_plan` (`:582`)
- `site_cleanup.size_of` (`:60`)
- `scripts/tree_review.cover_summary` (`:125`)
- `scripts/ship_branch.ShipBranchError` (`:66`, never raised)
- `trees_layer.cell_ndsm` (`:482`)
- `trees_layer.LAYER_PLAN` (`:49`)
- `decor_layer.warm_decor_columns` and `WARM_BUDGET` (kept alive only as an "old" baseline in `test_decor_layer.py:733-741,830-833`)
- `roof_material.preview_colour` (`:46`, test-only, and the reason `roof_material` imports `site_kit` at module level)
- `scripts/tree_lines_trial.py` (305 lines) and its test (99 lines), the spike for done ticket 323

### 1.7 Place names and history in tool code (V)

- **Plan names:** `highfield_` is defined three times (`ground_layer.py:121-123`, `decor_layer.py:44-46`, `trees_layer.py:101-103`). The `highfield_grounds_` prefix is at `ground_layer.py:170` and `cell_features.py:70`.
- **Area default:** `'highfield'` at `cell_sources.py:737,741`, `site_handoff.py:1012` and `cross_zone.py:199`.
- **Retired pass list:** `RETIRED_GROUND_PASSES` (`ground_layer.py:115-118`, used by `scripts/redo_here.py:22,64`).
- **World-specific base Y:** both builders fill stone from a hard-coded `range(37, b)` (`house_build.py:176`, `feature_build.py:1825`). The base Y should come from world config.
- **Structure namespace:** `site_kit.STRUCTURE_NS` defaults to `'southampton_refine'` (`site_kit.py:54`). That default is copied into the MCP client name `southampton-refine-check` (`lib/blocks/mcp.py:28`) and the example at `structure_nbt.py:38`.
- **Ledger alias:** `cell_sources._LEDGER_ALIASES` (`:474-478`) hard-codes a Southampton world-provider id.
- **Vendor-named crown list:** `trees_layer` (`:393-480,611,685,860-869,1108`). The world file `reference/trees-*-crowns.json` and `rules/foliage.md:70` use the same name.
- **User-Agents:** three provider fetchers name the tool "SouthamptonRefine", which `GLOSSARY.md:9` says to avoid. That text goes to outside services: the photo-archive fetcher `:30`, the street-imagery fetcher `:29` and the lidar tile client `:66`.
- **Docstrings:** Southampton examples in about 12 modules, e.g. `house_build.py:4` (four Southampton streets), `evidence_sheet.py:8-10`, `aerial_registered.py:7-9` (`highfield-user` paths), `osm_footprints.py:12,18`, `frontage_profile.py:11`, `ref_manifest.py:15`.
- **Southampton camera table:** `scripts/site_views.py:46-207` holds 127 Southampton camera views keyed to retired Q-nnn items. Its docstring says "Start empty". Running it with no names captures all 127 in whatever world is open. See 1.17.
- Risk: the southampton-build registry, masks and journal key on `highfield_` names, so that world must keep its prefix through world config.

### 1.8 Five deprecated `scripts/` stubs (V)

- `scripts/{aerial_mosaic,buildings,osm_extract,portals,tpo_fetch}.py` are 19-line runpy stubs that forward to `shims/`. `scripts/README.md:22-24` mentions only one of them.
- These still use the `scripts/` paths:
  - Code: `lib/geo/geo.py:517`, `lib/sources/cell_sources.py:725`, `scripts/ref_fetch_agent.py:4,207`, `scripts/start-ref-fetch.command:3`
  - Rules: `rules/process.md:331`, `building-review.md:13,91`, `craftsmanship.md:243,274`, `evidence-order.md:21`, `rules-context.md:71`
  - Skill: `redo/SKILL.md:106`
  - Plan: `docs/archive/world-cli-arg.md:84`
  - World config: southampton-build `local-providers.json` lines 26/46/66/86/106; ventnor-build line 27

### 1.9 Leftovers from the seam follow-ups and the `scripts/`→`lib/` move (V)

- **Aliases and re-exports:**
  - `ground_sources.py:155-156` keeps `cell_box = cellmath.bounds`, still imported at `trees_layer.py:778,783,836,838`.
  - `coverage.py:64-65` has a stale "imported by lib/layers/foliage.py" comment and a `noqa` re-export, used only by `test_cell_features.py:138-263`.
  - `house_build.py:46` re-exports `entrance_approach` and `entrances_from_blocks`. That re-export is relied on by `test_house_build.py:449-472` (`EntranceApproachTest`, which belongs in `test_entrance_approach.py`), `rules/frontage-and-evidence.md:191` and `rules/handoff-layered-passes.md:300`.
  - `roof_lint.py:37` aliases `_STAIR_DELTA = ROOF_DELTA`, and the docstring at `roof_geometry.py:5` claims a re-export that nothing uses.
- **Duplicates of `cellmath`:**
  - `cell_registry.cell_id` and `parse_cell_id` (`:78-102`; importers `registry_next.py:26,80`, `registry_verify.py:19`).
  - `test_cell_features` uses `site_handoff.ring_cells`.
  - Component finders: `lidar_heightmap._group` (`:188-202`), `garden_sources._run_components` (`:837-855`) and `ground_layer.denoise` (`:468-478`).
  - Direction tables: `decor_plan.CARDINAL` (`:95`), `ground_layer:671,674`, `garden_sources:460,679,1297`, `tree_voxels:111`, `facade_plan._OUTWARD` (`:64`), `window_openings.DIRECTIONS` (`:8`), and `floaters._FACING` (`:11`, which equals `roof_geometry.ROOF_DELTA`).
  - `cell_registry._norm_ref` and `planned_refs` (`:429-443`) only pass through to `plan_refs`. `coverage_warnings` (`:181`) is test-only.
- **Misplaced tests:**
  - Pure roof-geometry classes in `test_feature_build.py`: `PitchedRoofStatesTest` `:3431`, `RidgeHalfStepsTest` `:1993`, `RoofCrestSnapTest` `:2248`, `SharedRoofVariantTest` `:4162` (which tests the private `roof_geometry._roof_slab` and `_roof_stair`), and parts of `RoofRidgeCapsTest` `:3854`. `test_roof_geometry.py` has 4 tests.
  - `BlockFamilyVariantsTest` (`:3410`) tests `family_variants` through feature_build's import.
  - `test_door_frames.py` tests `site_kit` but lives in `lib/builds/`.
- **Dead path inserts:** 13 `lib` files insert a non-existent `lib/shims` path (`parent.parent / 'shims'`), and 26 `lib` test files call `sys.path.insert`. Examples: `test_door_frames.py:8-9`, `test_block_reference.py:8-9`, `test_floaters.py:9-10`, `test_roof_lint.py:13-14`, `test_landmark_gate.py:14-15`. `run_tests.py` already sets the paths.

### 1.10 Parallel vocabularies that survived (V)

- **Barrier kinds** live in three tables:
  - `decor_plan.py:30-45`: `DEFAULT_BLOCK`, `DEFAULT_HEIGHT`, `BARRIER_KINDS`, `PLANK_TONES`
  - `garden_sources.py:45-48`: its own defaults, with `fence` rather than `spruce_fence`
  - `street_furniture.py:223`: a literal copy of `BARRIER_KINDS`
  - There is also an import cycle across tiers: `garden_sources` (sources) imports `decor_plan` (layers) at `:38`, and `decor_plan` imports it back (`:526, :928`).
- **Builder stoop and ground helpers** are copied between `house_build` and `feature_build`:
  - `GROUND` (`house_build.py:40-41` says "Same list"; `feature_build.py:178`)
  - `FACE` and `TOWARD` (`house_build.py:47-48`, inlined again at `:280,:327`; `feature_build.py:315-316`)
  - `stair()`: house goes through the catalogue (`:51`); feature spells `{mat}_stairs` by hand (`:930`, called at `:2002,:2012`)
  - `top_solid` and `step_out` (`house_build.py:334-372`; `feature_build.py:1982-2017`)
  - **They have drifted:** `clear()` in houses still lays the margin stone shelf that task 115 removed from features (`house_build.py:166` vs `feature_build.py:1798`).
- **Air-like block sets:** about 20 spellings, e.g. `ground_layer:70`, `road_labels:44`, `lidar_heightmap:56`, `osm_footprints:79`, `floaters.py:12`, `cell_journal.py:78`, `cell_marker.py:49`. `site_kit.AIR_IDS` (`:464`) is the natural base.
- **Block-state and id helpers:**
  - Ground and lidar duplicated raw-read ID extraction. T11 now selects `block_store.block_id(..., policy="read")`, preserving namespaces and missing-to-air defaults.
  - Footprint and kit namespaced ID extraction now use `block_store.block_id`; `site_kit.bid` remains its public import alias.
  - T11 consolidates full-map property parsing into `block_store.state_props` with named compatibility syntaxes. Its sibling `state_prop` preserves the floater checks’ single-property regex lookup.
- **Others:**
  - Plant recognition policies now live together in `surface_probe`: `PLANTS`, `LIDAR_PLANT_PATTERNS` and `GRASS_LIGHT_PATTERNS`, retaining their distinct membership and ordering.
  - Ground and footprint segment distance callers now use `cellmath.segment_distance`; the explicit `along` policy retains each former return contract and arithmetic.
  - The int-but-not-bool check is spelled out about 8 times in `feature_build` (`:1137,1143,1410,1451,1459…`), while `facade_plan._is_int` (`:339`) already exists.
  - Builder names `('house_build', 'feature_build')` are typed out in `builder_registration.py:42,57` and `feature_reapply.py:35-41`.
  - `builder_provenance.py:10` recomputes `TOOL_ROOT` instead of importing it.
  - Git subprocesses now share `scripts/shared_git.py`. Branch shipping opts into hook-environment scrubbing; pre-commit staged-index checks and test-lock lookup retain their inherited environment.

### 1.11 Small shims and vendor leftovers (V)

- `lib/geo/geodetic.py` is a pure re-export of `world_frame`. Its four importers are `geo.py:36`, `osm_extraction.py:19`, `streetview_sample.py:40` and the lidar tile client `:169`.
- `lib/geo/osgb.py` duplicates `world_frame`; both use `int(round())`, so its `offsets=` branch can simply go. Its one new importer is `aerial_annot.py:94`.
- `lidar_provider.py:114-133` falls back to vendor class names, but the fallback is never used. The lidar world module has an unused `CompositeWorld` alias, and `reference/TYPES.txt:51` mentions it.
- The photo-archive fetcher (`:26`) imports coordinate wrappers from the street-imagery provider instead of from `world_frame`.
- `lib/sources/legacy_reference_paths.py` is one constant for one importer, plus an allowlist entry in `provider_name_audit.py:105`. southampton-build still uses the old folder name, so fold the constant into `vector_map_clips` and keep the fallback.
- `config/system-providers.json` keys `catalogue` and `types_doc` are read by nothing.

### 1.12 Oversized modules: split lines that pay

| Module | Lines | Proposed split |
| --- | --- | --- |
| `lib/builds/feature_build.py` | 3,612 | `Build` alone is `:1483-3503`. Candidate homes: spec load and validation (`load_spec`, `_validate_*`, `:956-1470`, reusing `facade_plan._is_int`); lidar roof-zone pure helpers (`:329-930`); doors and entrances (`:2019-2368`); facade, windows and shopfronts (`:2368-3000`, plus the shop-sign colour and straight-run helpers at `:185-313`; `test_street_faces.py` already exists for that slice); details and PV (`:3210-3503`). `Build` keeps orchestration, shell, clear and roof dispatch. The module docstring (`:7-122`) repeats `rules/feature-builds.md:98-170`: keep one copy and point to it. |
| `lib/builds/test_feature_build.py` | 4,168 | Follows the module split; roof-geometry classes move out (1.9). `test_street_faces` imports `test_feature_build` as a fixture; move those shared helpers to a fixtures module. |
| `lib/layers/ground_layer.py` | 2,030 | (a) Shared layer helpers (`:68-71`, `:113-292`) into a layer-common module; decor, trees, mask undo, the float audit and `redo_here` import them from the ground builder today. (b) Ground dressing (`:1024-1577`). |
| `lib/sources/garden_sources.py` | 1,492 | Barrier-line geometry (`:666-1241`) into its own module; keep the names that tests patch importable. |
| `scripts/cell_registry.py` | 1,010 | The read API (`:104-341`: `open_registry`, `view_registry`, `latest_entries`, `latest_cells`, `feature_cells`, `duplicate_plans`, `cells_above_ghost`) has 45 importers but lives in `scripts/`. Move it to `lib/journal/registry_access.py`; `cell_registry` stays the CLI. |
| `lib/site/site_handoff.py` | 1,572 | Not a split: keep the cell-gaps block and delete the rest (1.4). |

Left alone (J):
- `cell_journal` (one writer class by design, ADR 004) and `world_frame` (cohesive) stay as they are.
- `decor_plan`, `trees_layer` and `decor_layer` are cohesive.
- `site_kit` (38 importers; splits only if it keeps re-exporting the moved names) is low priority.
- `ship_branch` is optional: its test-mapping part (`:212-356`) could move out.

### 1.13 Stale paths, names and flags in docs (V)

| Doc | Missing or stale target |
| --- | --- |
| `lib/README.md:13-14` | "foliage" and "terrace" (retired in 375, deleted in 373) |
| `rules/handoff-layered-passes.md:220, 245`; `test_pass_mask.py:1` | `scripts/pass_mask.py` |
| `reference/TYPES.txt:21`; `model-bakeoff/SKILL.md:29`; `new-world-build/SKILL.md:119`; `bakeoff/PROTOCOL.md:101` | `scripts/vector_map_clips.py` |
| `docs/archive/lidar-provider-contract.md:59,73,89`; lidar provider modules `:347`/`:167`; `shims/lidar_tiles.py:5`, `lidar_world.py:5` | `scripts/lidar_provider.py` and friends; the spec's §0 and §4 describe the pre-262 state |
| `docs/archive/pyproj-gdal-point-validation.md:85`; `docs/archive/cell-journal-interface.md:3-5`; `lib/audit/test_floaters.py:1,3`; `test_roof_lint.py:4` | old `scripts/` paths of modules now in `lib/` |
| `rules/building-review.md:42`; `rules/craftsmanship.md:152`; `rules/terrace-builds.md:37` | one-off place scripts; `scripts/legacy/` |
| `rules/process.md:18,121,213` | `config/coverage-areas.json` (1.1) |
| `rules/process.md:55,125,202,209,282,347`; `rules/foliage.md:20`; `rules/cleanup.md:15,32`; `rules/building-review.md:79` (`--marks cells.json`); `purge/SKILL.md:43`; `site_cleanup.py:15` | retired `*-cells.json` build records (ADR 004; `registry_verify.py:67` flags any left over) |
| `rules/process.md:347` | "same check as CI": there is no CI |
| `rules/building-review.md:60` | `--lower` flag (no such flag; the real thing is `<base>-lower` plans) |
| `redo/SKILL.md:105` | `lidar_parts` (no such name) |
| `rules/foliage.md:111-112` | tombstone for the retired tree planner scripts |
| `lib/site/rules_context.py:209,245` | `terrace-builds.md` labelled "generator mandatory" |
| `new-world-build/SKILL.md:121` | says to copy "ISSUES", which the template calls a retired mirror |
| `docs/archive/region-block-reads.md` (about 10 lines) | ten scripts that no longer exist (historical: archive it) |
| `docs/adr/006-land-use-classes.md:36`; `lib/evidence/test_aerial_sources.py:20` | `nonresidential_building`, `registered_rgb` |
| Usage text missing the required `--world`: `feature_build.py:127,131`, `footprint_overlap.py:12-13`, `water_body.py:58`, `roof_lint.py:17-18`, `floater_scan.py:9-12`, `mc_player.py:5-6`, `rules/feature-builds.md:216,250` | every wrapper calls `prepend_world_activation` |
| `build_roles` and `stale_terms_audit`: tool-level, but their wrappers require `--world` | docs disagree: no `--world` in `rules/build-roles.md:12-13`, `feature-builds.md:233`, `rules-context.md:76`, `purge/SKILL.md:69`; `--world` in `cell-orchestrator/SKILL.md:114`, `redo/SKILL.md:97,214`, `docs/agents/model-and-effort.md:53` |
| `feature_build.py:89-90` docstring | leaves out the `chimney` detail kind |
| `.cursor/rules/agent-soundboard-flair.mdc:2,6` | names the project "Southampton Refine" |

`lib/audit/stale_terms_audit.BANNED_TERMS` (`:17`), enforced by a test, could carry the newly retired names (`plant_cell_canopy`, `scripts/terrace.py`, `block_family_variants`, `oakdene_court`). That would have caught the foliage tombstone.

### 1.14 Overlapping and broken `site_*` and undo CLIs (V on refs, J on verdicts)

- **`site_views`:**
  - It writes to the **tool** repo's `verification/site/views` (`:43`), while every other tool writes under the world. `site_cleanup.py:84` cleans only the world copy. The tool-root folder holds 29 orphan PNGs (6.6 MB, gitignored).
  - `--help` is taken as a file-name prefix (`:218`); orphan files named `--helpb35-*.png` show it has happened.
  - Its table is Southampton-only (1.7).
  - Fix: views come from world config, output goes under `ROOT`, flags are rejected.
  - Touch points: `site_patrol.py:146`, `.gitignore:17-18`, `rules/cleanup.md:13`, `site_cleanup.py:83`, `scripts/README.md:16`, `test_mc_player.py:65`.
- **`site_audit`** is a cruder copy of `cell_registry reconcile`: it compares the latest plan per build, not ownership. Keep only its "dark walkable" check, or fold that into reconcile. Touch points: `rules/process.md:71`, `scripts/README.md:16`.
- **`site_index`** writes `BUILDS-INDEX.txt` (only southampton-build has one), which repeats journal entries. Delete it, or make it a `cell_registry` subcommand. Touch points: `rules/process.md:22`.
- **`mask_undo` vs `element_undo`:** `element_undo` already routes trees and decor through `mask_undo`. Keep `mask_undo` as a library and delete its CLI.
  - Docs to repoint: `layer_redo.py:141`, `ground_layer.py:40,1917`, `rules/process.md:85,190`, `rules/foliage.md:17,35`, `rules/handoff-layered-passes.md:252`, `scripts/trees_replan.py:4`.
- Left alone: `redo_here` vs `redo_layer` (one finds the target, the other executes), `site_walk`, `site_recover`, `site_cleanup`, `site_status` (after 1.4) and `site_pages`.
- CLIs that no doc, skill, rule or hook names: `contact_sheet`, `frontage_profile`, `footprint_overlap`, `site_walk`, `aerial_registered`, `stale_terms_audit`. The libraries are live; only the wrappers are in question. Leave unless the owner wants them gone.

### 1.15 Duplicated and contradictory agent guidance (V on text, J on verdicts)

All agent-facing docs together come to about 77k tokens. Each overlap is a drift risk and a reading cost.

1. **`builderTask` rules** (renamed from `requiresClaude` by task 386; re-check line numbers) are stated in `docs/agents/model-and-effort.md:5-25`, restated in `issue-tracker.md:22-42`, and partly repeated in `parallel-task-wave.md:5-18` and `ship-branch/SKILL.md:26`. Keep one home.
2. **Branch gate** is stated in AGENTS.md:19, `CODING_STANDARDS.md:45-59` (almost the ADR word for word), `adr/005:15-35` and the ship-branch skill.
   - AGENTS.md:19 says docs-only means `is_docs_only_path` "minus markdown under rules/, .agents/, .claude/".
   - But `ship_branch.is_docs_only_path` counts all `.md`; the "minus" is only the hook's fast path (`pre_commit_checks.py:155`).
   - Fix: separate "may commit to main" from "hook skips the unit suite", and cut CODING_STANDARDS to a link.
3. **Running the queue, two models:** `rules/process.md:323-353` (§6) chains `cell_registry.py next` in the same chat, while the cell-orchestrator skill builds each task in a fresh session. Rewrite §6 to defer to the skill.
4. **Sound cues in five places with different cue ids:** `docs/agents/world-and-live-commands.md:10`, `redo/SKILL.md:58,229`, `ship-branch/SKILL.md:109-110`, `minecraft-instance-setup.md:118`, `process.md:340`, and the always-applied, personal-path file `.cursor/rules/agent-soundboard-flair.mdc`. Keep one home.
5. **Lock wrapper and background runs** are explained in 8 files: `slow-commands`, `build-workflow`, `world-and-live-commands`, and the skills new-world-build, cell-orchestrator, redo (34 lines), purge and refine. Skills should link to `slow-commands.md` and keep only the command line.
6. **The house-build path** is described in `rules/terrace-builds.md:6-12`, `craftsmanship.md:221,224` and `feature-builds.md:3-5`.
7. **Misleading name:** `rules/terrace-builds.md` is the `house_build` rulebook; rename it to `house-builds.md`. Touch points: `rules-context.md:30,79`, `craftsmanship.md:99`, `feature-builds.md:5`, `lib/site/rules_context.py:105,209,245` (or gone with 1.4), `test_rules_context.py:39,44`.
8. **`rules/handoff-layered-passes.md` is half history** (300 lines). §3 says "proposed", and §7, §9, §13 ("HISTORICAL") and §14 are about Southampton 48,-44. Cut about 70 lines and move the §10-12 decisions into an ADR.
9. **Small contradiction:** the refine skill's §4 done-when allows "a task", but the same section says builders never add tasks.
10. **Largest files and what to cut** (tokens ≈ bytes ÷ 4):
    - `rules/process.md` (6.4k): the §1 file map (duplicates `layout.md`), §6 and §7, and parcel coverage.
    - `rules/craftsmanship.md` (5.1k): campus blocks 241-283.
    - `rules/feature-builds.md` (4.2k): the SUSU fixture prose.
    - `sherlock/SKILL.md` (4.1k): "Ask Mycroft" is about 600 tokens of flavour.
    - `redo/SKILL.md` (3.8k): its evidence table duplicates `rules/evidence-order.md`.
    - `rules/building-review.md` (3.5k): dated review reflections at 53-129.
11. **Skill name collision:** `task-index-manager` shares its name with a personal skill, and `issue-tracker.md:60` already warns about it. Rename the project copy, or accept the warning.

Skill stubs are in sync (13 of 13). redo, refine and purge are well split; their overlap is execution boilerplate (item 5).

### 1.16 Place-specific content in tool rules, skills and templates (V)

| File | Lines | What |
| --- | --- | --- |
| `rules/craftsmanship.md` | 35, 55, 75, 97, 104, 152, 157, 241-283 | Broadlands, Oakdene, university portals and buildings register |
| `rules/building-review.md` | 13, 15, 20, 42, 53-129 | university aerial, a one-off script, `highfield_susu_west`, three dated case notes |
| `rules/feature-builds.md` | 3, 178, 237-240 | SUSU as the acceptance fixture |
| `rules/process.md` | 44, 112, 214 | "Origin: Highfield campus centre", `--area highfield` |
| `rules/handoff-layered-passes.md` | 63, 146-151, 234-256 | `highfield_48_m44_*` |
| `rules/foliage.md` | 42, 69, 112 | 48,-45, Library Square |
| `rules/frontage-and-evidence.md` | 22, 227, 257 | Broadlands Road, university aerial |
| `rules/evidence-order.md` | 14, 16, 20, 21 | campus, university portals |
| `rules/rules-context.md` | 30, 70-71 | Broadlands, university register |
| `rules/terrace-builds.md` | 3, 10, 36 | "Broadlands-type", a vendor imagery name |
| other rules | `radial-good-enough.md:22`, `reference-token-budget.md:10`, `build-workflow.md:5` | small mentions (keep the `slow-commands.md:28-29` timing example) |
| skills | `redo:76,104`, `new-world-build:15`, `model-bakeoff:30` | oakdene, university outline, Highfield |
| `templates/new-world-site/` | six "Moved to ai-worldbuilder rules/…" stubs (CLEANUP, CRAFTSMANSHIP, FOLIAGE, HANDOFF-LAYERED-PASSES, PROCESS, RULES-CONTEXT), plus `ISSUES.txt` | a new world has no old paths to resolve; also `README.txt:8-13,22`, `NOTES.txt:9-10` |

Verdict: move the university register guidance and dated history into southampton-build, and make examples generic (`<street>_<numbers>`). Leave ADRs 001 and 002 and `GLOSSARY.md:9`, which name Southampton as history or as a term to avoid.

### 1.17 Plans, specs and world material in `docs/` (V)

Several plans are **linked from code or rules**. Repoint those links before archiving:
- `rules/process.md:313` points to the 284 plan.
- `roof_aerial.py:17` points to `bigbox-roof-aerial.md`.
- `decor_plan.py:47` and `garden_sources.py:784` point to the 244 findings plan.
- `land_use.py:7`, `config/land-use-classes.json` and `config/provider_name_audit_baseline.json` point to `land-use-classes.md`.

| Doc | Tickets | Verdict |
| --- | --- | --- |
| `244-boundary-evidence-findings.md` | 244 and 254–274, done | move the owner decisions at `:83,87,99` (side dividers open by default, slatted panels, fence material) into a rule or the land-use table, then archive |
| other 244 plans and `244-draft/*.json` | done | archive |
| `264-decor-source-reuse-no-go.md`, `283-ownership-one-pass-no-go.md` | done | move each no-go decision into an ADR or rule, then archive |
| `land-use-classes.md` | done | the design the code implements: make it an ADR |
| `252-research-handoff`, `263-264-decor-preparation`, `284-preflight-blocker-commands`, `285-stale-ground` (its rule already lives in `process.md:151`), `bigbox-roof-aerial`, `split-next-new-cell-vs-overnight`, `shared-minecraft-instance`, `park-rural`, `seam-rationalisation-review` | done | archive |
| `world-cli-arg` (103), `first-return-lidar-canopy` (320), `woodland-subtasks-handoff` (347–351) | name their done tickets in their own text | archive |
| `323-unmapped-tree-lines.md` | 323 done | input to open 360: link it from 360, keep until 360 closes |
| `southampton-dryrun-001.md` | none | place history: move to southampton-build |
| `industrial-commercial` (333), `vibe-at-scale` (257), `claude-task-splits-2026-11-21` (326, 358, 360), owner drafts (`railway.md`, `bridges-and-tunnels.md`, `railway-prototype/`) | live | keep |
| specs: `cell-journal-interface` (superseded by ADR 004), `decor-furniture-aerial-zoom-spike` (308), `pyproj-gdal-point-validation` (85); audits: `312-rotated-window-policy`, `region-block-reads` | done | archive |
| spec `lidar-provider-contract` | 262 done | rewrite to current state, or archive |
| specs `decor-furniture-footprints`, `road-name-labels`, `lidar-ghost-v2-and-dryrun-reset` | cited by code or rules | keep |

Other material:
- `docs/snags/` (one Southampton note and 6 PNGs, 10.5 MB) is world work under ADR 002: move it to southampton-build.
- The bake-off photos are about 14 MB.
- `.git` is 93 MB; removing files from the tree does not shrink the history.

## 2. Open-task interactions

| Ticket | Interaction | Proposal |
| --- | --- | --- |
| Railway follow-ups (379–385; trial 256 closed) | The planner (381), line integration (382) and crossing mechanism (383) add new feature code beside the builders. | Point them at `cellmath`, the barrier module (T8), the builder helpers (T18) and the world-provider roles (T3); add no private block, cell or tag tables. Re-run the provider-name audit on the railway plans if not already green. |
| Typology buildings (257), shop typology (333) | Both extend `feature_build` and `house_build`, which T13 splits and T18 de-duplicates. | Land T18, then T13, before either starts builder code. |
| Woodland (318), tree belts (360) | Both tune ground dressing in `ground_layer:1024-1577`, which T12 moves. 360's evidence input is the 323 plan, which 360 does not link. | Run them in sequence with T12, not in parallel. Add the 323 plan link to 360's notes. |
| House roofs: real-spec validation (358) | Notes still mention "terrace.py/outbuildings.py sharing" (terrace.py is deleted). T18 changes the house `clear()` and stoop. | Rewrite that phrase. Do T18's golden recording before 358's dry run, or after it closes. |
| River levels (326), Itchen join (352) | World work. | None. |

## 3. Proposed tickets

Filed 2026-11-22 as below. Ticket notes refer to each other by T-number; this table maps them.

| Letter | Ticket |
| --- | --- |
| T1 | #391 |
| T2 | #392 |
| T3 | #393 |
| T4 | #394 |
| T5 | #395 |
| T6 | #396 |
| T7 | #397 |
| T8 | #398 |
| T9 | #399 |
| T10 | #400 |
| T11 | #401 |
| T12 | #402 |
| T13 | #408 |
| T14 | #403 |
| T15 | #404 |
| T16 | #405 |
| T17 | #406 |
| T18 | #407 |
| T19 | #409 |
| T20 | #410 |
| T21 | #411 |
| T22 | #412 |

Sequencing notes were added to #257, #318, #333, #358, #360 and #381–#383 (section 2).

Titles come first; ids come from `tasks.mjs add`. Every ticket includes the definition-of-done checklist. All are filed with `builderTask: false` (no AI model in the process), per `docs/agents/model-and-effort.md`. None needs a live world unless noted.

**Wave 1 (no blockers, disjoint files):** T1, T2, T3, T6, T8, T16, T20.
**Owner decisions:** taken (see "Owner decisions" above); no ticket waits on the owner.

### T1. Docs: fix every stale path, name and flag, and add a path audit test
- priority: high; complexity: m
- Scope:
  - Fix every row of 1.13 except the coverage rows (T5).
  - Add the newly retired names to `stale_terms_audit.BANNED_TERMS`.
  - Add a unit test that fails when a tool-root path named in `rules/`, `.agents/skills/`, READMEs, root docs, `docs/agents/`, `docs/specs/` or `reference/TYPES.txt` does not exist. It skips world-relative paths and placeholders.
- Check: the new test passes, and it fails on a fixture doc that names a missing path. `stale_terms_audit` passes.

### T2. Move Southampton supplement roads out of `ground_sources` into world data
- priority: high; complexity: s
- Scope: 1.2. Add an optional world file read by `load_osm`, migrate the rows into southampton-build, and delete the constant.
- Check: a fixture world without the file gets no extra roads. With the file, the features are the same as before.

### T3. World-provider shims: resolve by catalogue role, and make tool imports optional
- priority: high; complexity: m–l
- Scope:
  - 1.3: the four shims, `cell_features.py:4,51-52,636`, `site_handoff.py:357,400` (or gone with T17), `evidence_sheet.py:174,256,404`, `world_provider.py:4,8,34`, `scripts/test_redo_here.py:52,68`.
  - Rules: `building-review.md:13,91`, `craftsmanship.md:243,245,274`, `evidence-order.md:21`, `rules-context.md:71`.
  - Skill: `redo/SKILL.md:106`.
  - World `local-providers.json` rows gain a role.
- Check: in a fixture world with no such providers, `import cell_features` succeeds and `cell_registry.py next` runs without the import failure. Southampton behaviour is unchanged on a fixture.

### T4. Tested-but-unrun code: wire it or delete it
- priority: high; complexity: m
- Scope: every row of 1.5, per its verdict. Fix the rule, spec and skill text that names each one.
- Check: every row has either a production caller with a test or no remaining reference.

### T5. Coverage parcels: retire the parcel workflow, keep the registry rollup (decided: retire)
- priority: medium; complexity: m
- Scope:
  - 1.1. Delete areas, build, gaps, signoff, map, the parcel registers, the sign-off leftovers, `AREAS_FILE` and their tests.
  - Make `load_signoffs` callers in `cross_zone` and `cell_sources` stop depending on it.
  - Keep the rollup and holds.
  - Rewrite the rules listed in 1.1, the cheatsheet and the docstring, and decide what happens to `templates/new-world-site/COVERAGE.txt`.
- Check: `coverage.py --help` lists only live subcommands, and no rule teaches one that is gone.

### T6. Delete the five deprecated `scripts/` provider stubs
- priority: medium; complexity: s
- Scope: 1.8. Repoint every listed doc, code string and world JSON line to `shims/`, then delete the stubs.
- Check: no `scripts/<stub>.py` path appears in the tool repo or any world.

### T7. Delete plain dead code (1.6)
- priority: medium; complexity: s
- Scope:
  - Every item in 1.6. Rewrite `test_decor_layer`'s baseline without `warm_decor_columns`.
  - Drop `roof_material`'s module-level `site_kit` import if nothing else needs it.
  - `tree_lines_trial` goes with its plan (T14 archives the plan).
- Check: checklist item 1 for each name.

### T8. One barrier vocabulary in `lib/sources`, and break the sources-to-layers import
- priority: medium; complexity: s–m
- Scope: the barrier part of 1.10. One module owns `BARRIER_KINDS`, defaults, plank tones, `barrier_kind` and the `fence` alias. `decor_plan`, `garden_sources`, `street_furniture` and `decor_layer` (`:479-480,1031`) import it.
- Check: fixture garden and street plans are identical before and after, and `BARRIER_KINDS =` is defined once.

### T9. Finish the cellmath move and drop compatibility re-exports
- priority: medium; complexity: s–m
- Scope:
  - The 1.9 "aliases and re-exports" and "duplicates of cellmath" items, including the `cell_registry` cell-id copies and pass-throughs.
  - Before swapping each component finder, check whether its callers depend on order.
  - Repoint the two rules that say `house_build.entrance_approach`.
- Check: component sets match on 4- and 8-connected fixtures. No `noqa: F401` re-export of a moved name remains.

### T10. Place names out of tool code
- priority: medium; complexity: m
- Scope:
  - 1.7 except `site_views`, which is T19.
  - One `plan_name(cell, layer)` with the prefix from world config; the area default, retired passes and the base Y (`range(37, …)`) also from world config.
  - The structure namespace default must be neutral, without changing an existing world's namespace.
  - Rename the crown list to type language, migrating the world file.
  - Generic User-Agent and docstrings.
  - Other touch points: `mask_undo.py:27`, `trees_layer.py:40,904`, `decision_record.py:9`, `restore_structure_backup.py:7-8,75`, `rules/handoff-layered-passes.md:63,238`, `rules/foliage.md:70`, about 32 test lines.
- Check: `git grep -niE "highfield|portswood|broadlands|southampton|susu" lib scripts shims providers` returns only world-config readers. southampton-build keeps its plan names, structure namespace and base Y through its config (fixture).

### T11. One air-block base, one block-state parser, one block-id helper
- priority: low; complexity: s–m
- Scope: the air sets, block-state and id helpers, plant substrings, segment distance and `_git` helper items of 1.10. Air sets become one base in `site_kit` plus named extras, and `state_props()` goes in `block_store`.
- Check: a test asserts each former set equals the base plus its named extras. Planner fixtures are unchanged.

### T12. Split `ground_layer` into a layer-common module and ground dressing
- priority: medium; complexity: m
- Scope: 1.12 rows (a) and (b). Retarget the test patches of `neighbour_owned_states`, `load_masked_positions`, `owned_air_ceilings` and `ignored_builds`. Sequence against 318 and 360.
- Check: golden ground plans for fixture cells are byte-identical. No other module imports helpers from `ground_layer`.

### T13. Split `feature_build` into spec, roof-zone, doors, facade and details modules
- priority: medium; complexity: l; blockedBy: T18
- Scope:
  - The 1.12 rows for `feature_build` and its test file, plus the misplaced roof tests from 1.9.
  - Behaviour-preserving: record golden plans first for each fixture family.
  - Keep one copy of the spec text (rule or docstring).
- Check: golden plans are byte-identical. `feature_build.py` is under about 1,200 lines. `house_build` imports nothing private from it.

### T14. Archive done plans and specs; move world material out of the tool repo
- priority: low; complexity: s (docs only, plus the code comments that link to plans)
- Scope:
  - The 1.17 table.
  - Repoint the code, config and rule links first.
  - Move the four decision sets into ADRs or rules, and make `land-use-classes.md` an ADR.
  - Move `docs/snags/` and the dry-run plan to southampton-build.
  - Decided: done plans and specs move to `docs/archive/`, not deleted. Add one line at the top of `docs/archive/` saying it is history, not current guidance.
- Check: every file left in `docs/plans/` is named by an open ticket or is an owner draft, and T1's path test passes.

### T15. Freeze the closed bake-off in place (decided: freeze)
- priority: low; complexity: s–m
- Scope:
  - The bake-off is about 3,000 lines of code, 64 tests (2.4%), two `scripts/` shims, about 14 MB of photos and a skill.
  - The real cost is coupling: it imports `site_lock`, `cell_registry`, `site_kit`, the private `evidence_sheet._capture_view`, `mc_paced`, `world_frame` and `atomic_json`, so every refactor of those must keep a closed experiment green.
  - Decided: freeze. Keep the files and `bakeoff/PROTOCOL.md`, drop the two `scripts/` shims, stop `run_tests.py` discovering `bakeoff/` by default (keep an explicit opt-in), and mark the skill and `bakeoff/OVERVIEW.md` historical.
  - Touch points: `run_tests.py:5-12,70-82,204-209`, `ship_branch.py:278-311`, AGENTS.md:17, `docs/agents/layout.md:20`, `scripts/README.md:19`, `docs/active-world-contract.md:41`, `rules/build-roles.md:79`, the skill and its stub.

### T16. Small geo and vendor shims
- priority: low; complexity: s
- Scope: everything in 1.11.
- Check: checklist item 1 for every name, and the provider-name audit passes with a smaller allowlist.

### T17. Retire the handoff queue: keep cell gaps, delete the rest of `site_handoff` and `rules_context`
- priority: medium; complexity: l
- Scope:
  - 1.4. Move the cell-gaps block to `lib/journal/cell_gaps.py`.
  - Delete the rest of `site_handoff.py` and `scripts/site_handoff.py`, `rules_context.py` and its test, and the QUEUE and ISSUES parts of `site_archive` (`test_site_archive.py:80-155`).
  - Decided: drop the greeblies and contact-sheet gate (`:757-873`) with the queue code; reword `rules/feature-builds.md:251` so it promises no enforcement.
  - Repoint: `site_status.py:14,139,171-173,246`, `site_pages.py:329,353-354`, `test_site_recover.py:282-292`, `test_cell_features.py:15,121-123`, `test_mc_player.py:66`, `cell-orchestrator/SKILL.md:18,120`, `rules/handoff-layered-passes.md:30`.
  - Rewrite: `rules/process.md:351,361`, `rules/feature-builds.md:251`, `rules/rules-context.md` (the whole file), `rules/cleanup.md:39`, `scripts/issues.py:5`, `issue_log.py:209`, `coverage.py:30`, `geo.py:325`.
- Check: the cell-gaps tests pass from their new home, and no doc names a removed subcommand.

### T18. One stoop and ground helper module for house and feature builders
- priority: medium; complexity: m
- Scope:
  - The builder part of 1.10: `GROUND`, `FACE`, `TOWARD`, a catalogue-based `stair`, `top_solid` and `step_out(setter, prefetch)` go into `lib/builds/stoop.py`.
  - Record golden house and feature plans first.
  - Decided: houses follow task 115. `house_build.clear()` clears the margin without the stone fill; stoop and step cells at doors keep their ground. This is the one intended behaviour change in this ticket.
  - Also: one builder-name constant and `builder_provenance` importing `TOOL_ROOT`.
- Check: golden plans are identical apart from the house margin, where a fixture house on a slope plans no stone outside its footprint except stoops (mirroring 115's regression test).

### T19. Tidy the `site_*` and undo CLIs
- priority: low; complexity: s–m
- Scope: 1.14. Fix `site_views` (world-config views, output under `ROOT`, reject flags), and fold or delete `site_audit` and `site_index`. Delete the `mask_undo` CLI and repoint its docs to `element_undo`.
- Check: `site_views --help` prints help, and no doc names a removed CLI.

### T20. Agent guidance: one home per rule, and resolve the contradictions
- priority: medium; complexity: m (docs only)
- Scope:
  - 1.15 items 1-6 and 9-10: `builderTask`, the branch gate (and the AGENTS.md:19 wording), queue §6, sound cues (one home in `docs/agents/world-and-live-commands.md`; delete `.cursor/rules/agent-soundboard-flair.mdc`), lock and background boilerplate, the house-build path, the refine contradiction.
  - Item 11, decided: rename the project skill `task-index-manager` (for example `worldbuilder-tasks`). Run `node scripts/check-skill-stubs.mjs --write`, update every reference (`docs/agents/issue-tracker.md`, AGENTS.md, other skills), and drop the clash warning at `issue-tracker.md:60`.
  - Cut the oversize items in item 10 that do not depend on T5 or T17.
  - Make usage text and docs agree on `--world` (1.13), or drop `--world` from the two tool-level wrappers.
- Check: each topic is stated in one file and linked from the others. The agent-facing token total goes down (report before and after).

### T21. Move place history out of tool rules, skills and templates
- priority: medium; complexity: l (splittable per file)
- Scope:
  - 1.16 and 1.15 items 7-8.
  - Rename `rules/terrace-builds.md` to `house-builds.md`.
  - Cut the history in `handoff-layered-passes.md` and move its decisions into an ADR.
  - Move the university register, campus and dated review notes into southampton-build.
  - Make the acceptance fixture generic.
  - Delete the six template redirect stubs and `ISSUES.txt`.
- Check: `git grep -niE "highfield|portswood|broadlands|oakdene|susu|university" rules .agents templates` returns only deliberate generic examples, and the moved text exists in the world repo.

### T22. Test-tree hygiene
- priority: low; complexity: s
- Scope: delete the dead `lib/shims` path inserts and the redundant `sys.path.insert` calls in `lib` tests, and move misplaced tests (`test_door_frames` to `lib/site/`, `EntranceApproachTest` to `test_entrance_approach.py`). The roof-test moves happen in T13.
- Check: the test count is unchanged, and `git grep -n "parent.parent / 'shims'" lib` returns nothing.

## 4. Suggested order

1. **Wrong behaviour and drift guard:** T3, T2, T1, T4.
2. **Mechanical, parallel (disjoint files):** T6, T7, T8, T16, T20, T22. T9 and T11 touch shared modules, so run them one after another.
3. **Retirements (decided):** T5, T17 (after T3), T15.
4. **Place names:** T10 and T21 (T21 after T20, since both edit rules).
5. **Big behaviour-preserving moves:** T18, then T13; T12. Schedule them around 257, 333, 318 and 360. T19 and T14 any time.

## 5. Remaining gaps

- Per-module test timing was not measured. The suite total is 80 s.
- World repos were read only to check whether files and providers exist, not reviewed.
- The owner drafts (`railway.md`, `bridges-and-tunnels.md`, `railway-prototype/`) were not reviewed.
