# Audit: region-file block reads (task #39)

Scope: every place the repo touches `.mca` files, region dirs, `site_kit.World` or `logout_gate`,
and what to do about the read-only `World` parser. Audited at `main` 5ae903f by reading code
and docs only. No live MCP call was made and no Minecraft save was touched.

## 1. Inventory

Targets: **live** = the active world's save (`config/world.json` `region_path` under
`AI_WORLDBUILDER_WORLD_ROOT`); **pristine** = `pristine_region_path`
(untouched vendor import); **snapshot** = `backups/regions/<stamp>-<name>/`; **bakeoff** = the
bake-off save (`ghost_region_path`, or a separate world checkout with its own `world.json`).
Fates: stays / MCP (replace via the seam) / T38 (deleted by task 38, pristine baseline) /
T40 (deleted by task 40, region backup/restore). Line numbers are for `main` 5ae903f.

### 1a. The reader and its plumbing (`scripts/site_kit.py`)

| file:line | op | purpose | target | fate |
| --- | --- | --- | --- | --- |
| site_kit.py:21,311 `REGION`, `resolve_region` | read config | pick region dir | live (active world) | stays (moves behind the seam) |
| site_kit.py:396-480 `World._chunk` | **read** (`read_bytes`, zlib, NBT) | parse chunk sections | any dir passed in | stays, as the one guarded reader |
| site_kit.py:482 `block`, 497 `uniform_air` | read | block state; empty-section skip | any | stays |
| site_kit.py:506 `World.light` | read (re-parses region bytes) | stored BlockLight/SkyLight | live | stays (only caller: site_audit.py:53) |
| site_kit.py:373 `ensure_region_matches_open_world`, 340 `mcp_reported_gameworld` | list (`saves/*/session.lock`), exists | refuse apply when region target is not the MCP-open save | live/bakeoff | stays. **Imports `region_backup.mcp_world_reachable`: breaks when T40 deletes region_backup** (follow-up F1) |
| site_kit.py:936-938 (`apply`) | MCP `server_save_all_worlds`, `sleep(3)`, then `readback(plan)` | flush, then verify every planned cell | live | stays |
| site_kit.py:1052 `readback` | read, one `World.block` per planned cell | post-apply verification | live | MCP/seam |
| site_kit.py:546, 769, 1053 (`World()` defaults) | read | door/feature checks, door_frame, readback | live | MCP/seam |

### 1b. `World(...)` readers elsewhere (all read-only)

| file:line | purpose | target | fate |
| --- | --- | --- | --- |
| scripts/site_recover.py:98-103, 114 | `_matches_snapshot`: compare live vs a region snapshot | live + snapshot | snapshot half **T40**; live half MCP. Recovery readback (l.114, after `_flush` at l.77-79) stays via seam |
| scripts/lump_scan.py:277-281 | `find_lumps(World(), World(pristine))` | live + pristine | pristine half **T38**; live half MCP |
| scripts/osm_footprints.py:229-231 | `World(PRISTINE)` ground reference | pristine | **T38** |
| scripts/osm_footprints.py:260-262, 346-348 | street profile / surface probe | live | MCP |
| scripts/coverage.py:319, 1130, 1206, 1282 | pristine street surface; live comparisons | pristine + live | pristine **T38**; live MCP |
| scripts/lidar_heightmap.py:304-322 (`pristine_path`, `pristine_world`; globs `r.*.mca`) | pristine heightmap and lump report | pristine (and the pristine clone under `backups/`) | **T38** |
| scripts/lidar_audit.py:152-166 | live heightmap vs reference | live + reference | live MCP; reference **T38** |
| scripts/tree_review.py:131-136 | live vs pristine/reference trees | live + pristine | pristine **T38**; live MCP |
| scripts/airtight.py:15,60; door_audit.py:15,20; example_probe.py:12,37; floater_scan.py:28,122; geo.py:525-527; site_audit.py:14,44 (+ `light` l.53); site_patrol.py:20,43,65; site_walk.py:12,84; surface_probe.py:7 | one-off probes and audits | live | MCP (CLI diagnostics; lowest urgency) |
| scripts/cell_marker.py:43,266,312; clear_floating_leaves.py:21,89; decor_layer.py:29,206,227; feature_build.py:50,245 (default `world=None` -> `World()`); foliage.py:12; ground_layer.py:43,577; house_build.py:32,92; mask_undo.py:27,90,98; plant_cell_canopy.py:51,382,439; strip_cell_above_ground.py:26,65; trees_layer.py:38,410,439; trees_replan.py:19,36 | build planners: read live terrain/neighbours before planning | live | MCP/seam (these are the hot callers: planning reads many columns) |
| scripts/entrances.py:10 (`PlannedWorld`) | overlay of a plan over a world | wrapper | stays (takes any `.block`) |
| the lidar provider's synthetic world class (`DefraWorld`) | **not** a region reader: synthetic `block()` from lidar tiles | n/a | stays; already a second implementation of the `block()` contract |
| tests: test_entrances.py:10, test_trees_replan.py:15, test_cell_marker.py:67, test_defra_world.py:173, test_feature_build.py:79 | local stub `World`/`StubWorld` classes with `block()` | memory | stays; evidence the duck-typed seam already exists |
| tests: test_site_kit_region.py:37, test_site_kit_ground.py:6, test_site_kit.* | `World()` against tmp dirs/mocks | tmp | stays, rewritten to the seam |

### 1c. Non-reader `.mca` / region-dir operations (for completeness, none to be analysed)

| file:line | op | purpose | target | fate |
| --- | --- | --- | --- | --- |
| scripts/region_backup.py:65-83 `_copy_regions` (`glob('*.mca')`, `shutil.copy2`) | list + **copy** | snap | live -> snapshot | **T40** |
| region_backup.py:110-111, 146-169 (`snap`, `snap-world`, `copytree`) | copy | bbox/whole-world snap | live -> snapshot | **T40** |
| region_backup.py:225-249 (`list`, `prune`, `rmtree`) | list/**delete** | snapshot housekeeping | snapshot | **T40** |
| region_backup.py:290 `mcp_world_reachable` | HTTP POST to 127.0.0.1:8765 | liveness probe | n/a | **T40 deletes it; site_kit and logout_gate still call it. Relocate (F1)** |
| region_backup.py:340-367 (`shutil.move` of `site/*`) | move | ghost restore resets ownership files | place memory | **T40** |
| region_backup.py:379-440 `restore` (`shutil.copy2` into region, checksum, `copytree`/`rmtree` registry) | **write/overwrite** | restore .mca | snapshot -> live or bakeoff | **T40** |
| scripts/registry_repair.py:221-245 (`RB._copy_regions`) + 232-235 gate | copy | pre-repair backup | live -> snapshot | **T40** |
| scripts/model_bakeoff.py:849-877 (`rb.restore(region_dir=...)`), 467 | write (via region_backup) | bakeoff ghost restore | snapshot -> bakeoff | **T40** |
| scripts/decor_layer.py:233, ground_layer.py:636, mask_undo.py:123 | subprocess `region_backup.py snap` | pre-apply snap | live -> snapshot | **T40** |
| scripts/geo.py:315 | message text naming `region_backup.py snap` | hint | n/a | **T40** (text) |
| scripts/site_stages.py:25, 60-66 (`SNAPSHOTS.glob`) | list dirs | find newest snapshot dir for a stage | snapshot | **T40** |
| scripts/dryrun_reset.py:64-115 `load_region_dir`/`list_region_files` (`glob('*.mca')`) | list | plan wipe | live | **stays** (no read of content) |
| scripts/dryrun_reset.py:222-230 `delete_regions` (`p.unlink()`) | **delete** | dry-run wipe of all .mca | live | **stays**, the only remaining region writer; needs `logout_gate` |
| scripts/dryrun_reset.py:246-251 | `logout_gate.require_logged_out` | guard that wipe | live | stays |
| scripts/logout_gate.py (whole file, uses `session.lock`, MCP probe) | guard | gate region rewrites | live/bakeoff | stays only for dryrun_reset; drop the `region_backup` import (F1) |
| scripts/registry_verify.py:168-195 | exists (`is_file` on `r.X.Z.mca`) | `--check-world` presence check | live | stays (no content read); candidate for the seam later |
| scripts/nbt_min.py:104 (`open(path,'wb')`, gzip) | write | NBT writer for structure/level files, not .mca | n/a | stays; not a region writer |
| scripts/lidar_heightmap.py:311-314 | exists/glob `r.*.mca` | locate the pristine clone | pristine | **T38** |
| tests: test_region_backup.py, test_registry_repair.py:133,144, test_dryrun_reset.py:43, test_logout_gate.py, test_site_cleanup.py:32 | write fake `r.0.0.mca` bytes in tmp | fixtures | tmp | **T40** (first three region_backup/repair), dryrun_reset + logout_gate + cleanup stay |

Searched and found **no** other writers: no `open(..., 'wb')`, `os.remove`, `rename` or `shutil`
call under `providers/` or `shims/` touches a region path. `the lidar provider's manage/lidar_ghost.py`
uses `site_kit.apply` only. `scripts/restore_structure_backup.py`, `site_archive.py`, `atomic_json.py`,
`cell_registry.py` write JSON/NBT-structure files, never `.mca`. `.claude/skills` and docs only name the tools.

Summary of what remains after T38 and T40: **one reader** (`World`, live save only), **one region
deleter** (`dryrun_reset.delete_regions`, guarded by `logout_gate`), one `exists()` presence check.

## 2. What MCP offers (from the repo only)

Wrappers: `scripts/mcp.py` (`Client`, JSON-RPC over HTTP, has a `tools/list` subcommand) and
`scripts/mc_paced.py` (`Paced`: **60 requests/minute shared by all local clients**, default
1.1 s between calls; `text()`, `xyz()`, `box()`). Servers: `minecraft_world` :8765, `minecraft_view` :8766 (`.mcp.json`).

Tool names that appear in the repo: `block_fill_batch` (write, <=8000 fills/call), `block_entity_set_nbt`,
`entity_summon`, `entity_teleport`, `server_save_all_worlds`, `player_get_info`, `player_set_gamemode`,
`player_send_message`, and `block_render_region` (rendered iso image, server-side; used in
rules/building-review.md for review pictures, not for state).

**There is no documented or used MCP tool that returns block states.** Nothing in the repo calls a
`block_get`/`block_scan`-style tool, and the `minecraft_world` server was unreachable
(ECONNREFUSED) for this audit, so `tools/list` could not be checked. Whether one exists, and whether
it is bulk (box -> palette + indices) or per-position, is the first thing to learn (step 1 below).

What readback needs:

* Block state (id plus properties) at every `plan.cells` position, compared with `same()`.
  `plan.cells` is the whole planned volume, in practice tens of thousands to hundreds of thousands
  of positions per apply (a cell is 128x128 columns; `site_recover` also reads whole-cell plans).
* Planners (`ground_layer`, `trees_layer`, `decor_layer`, ...) read whole columns (top-down scans of
  up to ~100 Y levels, skipping empty sections via `uniform_air`) across a cell plus margins.
* Speed budget: at 60 requests/minute, per-position MCP calls are not viable (100k positions would
  take ~28 hours). Only a **bulk box read** with palette-style output (or a server-side compare of
  "planned vs actual") is viable. A 128x128x40 cell via a box read is ~16 requests/cell if the tool
  caps at one section column batch; unknown until measured.
* Properties like `waterlogged` are already ignored in `same()`, so palette strings need only be
  faithful for planned properties.

## 3. Options weighed

| criterion | A. all reads via MCP, no region parsing | B. one guarded reader behind `BlockStore` (LiveBlocks + MemoryBlocks) |
| --- | --- | --- |
| correctness after flush | best: live state, no lag; no flush needed | needs `server_save_all_worlds` first (already done in `apply` and `site_recover`); stale otherwise |
| speed, full-cell readback | unknown, rate-limited at 60/min; only OK with a bulk read tool that does not yet exist in the repo | fast: local file parse, `World._chunk` caches per chunk; seconds per cell |
| owner's "direct region-file read" worry | removed entirely | reduced to one small, open-for-read-only, flush-guarded, identity-checked class; no writer shares its code |
| logout-gate coupling | none | none needed: reads do not need a logged-out world (`logout_gate` protects rewrites only); keep reads off the gate and remove the `region_backup` import |
| tests | needs a fake MCP | `MemoryBlocks` already matches what five test files stub by hand |
| delivery risk | blocked on an unverified MCP capability | nothing blocked; swap the backend later |

## 4. Recommendation

**Choose B, shaped so A is a later one-class swap.** Introduce `BlockStore` as the only block-read
interface: `read(pos) -> state | None`, `read_many(positions) -> Mapping[pos, state]`, `flush()`.
Ship `MemoryBlocks` (dict, for tests and planned overlays) and a single `LiveBlocks`. Today
`LiveBlocks` wraps the current region parser, with three guards that answer the owner's concern:

1. **Read-only by construction**: opens files with `open(path,'rb')` only, takes no path from the
   caller except the resolved active-world/bakeoff region, and cannot be pointed at a snapshot or
   pristine dir (those callers are being deleted by T38/T40). `World(PRISTINE)` and `World(snap)` go away.
2. **Flush-before-read**: `flush()` calls `server_save_all_worlds`, waits, and drops the chunk cache.
   `read` refuses with a clear error if a write (`apply`) happened since the last `flush()`. This
   replaces the ad hoc `time.sleep(3)` and makes "stale region files mislead" (site_recover.py l.148) a rule.
3. **Identity check**: `ensure_region_matches_open_world` runs at construction.

When a bulk MCP block-read tool is confirmed, `LiveBlocks` gains an MCP backend and the region parser is
deleted; callers do not change. Per-position MCP is rejected on speed alone. Do not start by promising
"all MCP": the repo has no evidence such a tool exists.

## 5. First concrete steps

1. **Probe MCP (read-only, manual, when the server is up):** run `python3 scripts/mcp.py tools/list`
   against :8765 and record names and schemas of any block read tool, its box limit and cost per call
   in a short note. Decision gate for the later backend swap; not blocking the steps below.
2. **Relocate the MCP probe (F1):** move `mcp_world_reachable` out of `region_backup.py` into a tiny
   `scripts/mcp_probe.py`; point `site_kit.mcp_reported_gameworld` and `logout_gate` at it. Do this
   before T40 lands or after it breaks both.
3. **Add `scripts/block_store.py`** with `BlockStore` (Protocol), `MemoryBlocks`, `LiveBlocks`
   (wrapping the current `World` code, plus `uniform_air` and `light` as optional methods). Keep
   `site_kit.World` as a thin alias for one release.
4. **Switch the hot callers:** `site_kit.readback` -> `read_many`, then `site_recover`, then the
   planners via their existing `world=` parameter (`feature_build`, `house_build`, `trees_layer`,
   `ground_layer`, `decor_layer`). One-off probes (airtight, door_audit, ...) go last or stay on the alias.
5. **Delete** `World(region)` pristine/snapshot constructor use after T38 and T40 merge; remove the
   `region` argument from the public class.

## 6. Test plan (through the `BlockStore` interface)

Shared contract suite parametrised over `MemoryBlocks` and `LiveBlocks(fixture_region)`:

* `read(pos)` returns the stored state with sorted properties; unloaded chunk returns `None`;
  missing section returns `minecraft:air`; uniform single-palette section returns palette[0].
* `read_many(positions)` equals `{p: read(p)}`, keeps the input keys, handles an empty iterable, and
  parses each chunk once (assert one file read per region via a counting `open`).
* Negative coordinates and chunk/region borders (x=-1, 15/16, 511/512) resolve to the right chunk.
* `flush()` clears the cache: edit the fixture region bytes, then `read` is stale until `flush()`.
* A write via `apply` (fake `Paced`) followed by `read` without `flush()` raises; with `flush()` passes.
* Guard tests: `LiveBlocks` rejects a region dir that is not the active/bakeoff target; it never
  opens a file with a write mode (patch `builtins.open`/`Path.write_bytes` to fail the test on write);
  `ensure_region_matches_open_world` mismatch aborts.
* Callers: `readback(plan, store=MemoryBlocks(...))` counts mismatches; `site_recover` flush-then-read
  order (inject `flush`, `world_factory` as today); one planner (`trees_layer.plan_cell`) runs on `MemoryBlocks`.
* Fixture: reuse a tiny hand-built `.mca` (from the existing `test_site_kit_ground.py` setup) rather
  than a real save.
* If step 1 finds a bulk MCP read: run the same contract suite against `McpBlocks` with a recorded
  response fake; no live-world test in CI.

## Follow-ups

* F1: `site_kit` and `logout_gate` import `region_backup.mcp_world_reachable`; relocate before T40.
* F2: **answered 2026-10-22** (task #49 live bakeoff world, MCP `:8765`). See §7 below. Follow-up: task #56 LiveBlocks MCP backend.
* F3: after T38/T40, `config/world.json` keys `pristine_region_path` and `ghost_region_path` and
  the pristine clone under `backups/` become unused; remove with those tasks.

## 7. Live MCP block-read probe (task #49, bakeoff save)

Server: `minecraft_world` MOTD `cgutteridge - Bakeoff`, mod 1.1.0, paced at **60 requests/minute**
(`mc_paced.Paced` default interval 1.1 s). Region identity checked with
`AI_WORLDBUILDER_WORLD_ROOT` pointing at the bakeoff world checkout (its `config/world.json`
`region_path`).

### Tools (schemas from `python3 scripts/mcp.py tools/list`)

| tool | role | limits |
| --- | --- | --- |
| `block_get_state` | one position → id, properties, light, hardness, block-entity flag | 1 block / call |
| `block_scan_region` | box → list of matching blocks (id + properties + light) | **volume ≤ 65 536**; **results ≤ `limit`** (default **1024**) |
| `block_scan_summary` | box → non-air count, bounds, material histogram | volume ≤ **1 048 576**; no per-block states |
| `block_get_top_y` | one (x,z) column heightmap Y | 1 column / call |
| `block_render_region` | PNG render (review eyes, not state) | view/step/scale; not for readback |
| `block_fill_*` / `structure_*` | writes / structure I/O | unchanged; not read tools |

`block_scan_region` returns verbose text (one YAML-ish record per block). There is **no**
palette + index encoding. Without `match_block_id`, every non-filtered block in the box is listed
until `limit`.

### Timing (cell 47,-44: xz 6016..6143, −5632..−5505; wall time includes pace wait)

| call | volume | limit | wall |
| --- | --- | --- | --- |
| `block_scan_region` 128×1×128 at y=50 | 16 384 | 1024 | **~0.05 s** (truncated to 1024 hits; ~178 KB text) |
| `block_scan_region` 128×4×128 (y 48..51) | 65 536 | 65536 | **~1.5 s** (full box; ~11 MB text) |
| `block_scan_summary` 128×64×128 (y 0..63) | 1 048 576 | n/a | **~1.2 s** (histogram only) |
| `block_get_state` one block | 1 | n/a | **~1.1 s** (paced) |

A full cell column set at ~100 Y would need ~**25** `block_scan_region` tiles (65 536 each) →
~**25–30 s** at the shared 60/min pace, plus tens of MB of text to parse. Exact within each tile;
not sparse (AABB of a plan still returns every block in the box).

### Decision gate (ADR 004 “Block read path”)

Bulk MCP read **exists and is exact** inside the 65 536-volume / `limit` caps, so a later
`LiveBlocks` MCP backend is viable (file task #56). Keep today’s region-file `LiveBlocks` until
that lands: payload size, tiling, and lack of sparse/palette output still make the region reader
the cheaper default for planners and full-cell readback. Per-position `block_get_state` stays
rejected on speed alone.

### Reconcile live check (same sitting)

Throwaway `other` plan `reconcile_live_t49` (4×4 orange_concrete at 6128..6131, 50, −5546..−5543
in cell 47,-44): (1) dry `reconcile` / `reconcile 47,-44` exit 0 clean; (2) break one block to
stone → dry exit 1, `foreign` want/got sample, journal unchanged, lock unchanged; (3)
`reconcile 47,-44 --fix` re-fills, exit 0, rerun clean, journal `repair` line; (4) crash
`Ticket.finish` mid-apply → `site_recover status` + `reconcile` show in-flight; after lock
release, `recover 47,-44 --apply` lands; (5) `--fix` with another owner’s lock → SystemExit
(“World lock not held by …”). Cleaned with `CellRegistry.reset()` + air fill of the patch.
