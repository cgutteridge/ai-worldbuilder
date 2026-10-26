# ai-worldbuilder

Domain language for the reusable worldbuilder tool. World-specific vocabulary lives in each world repo.

## Worlds and tools

**Worldbuilder**:
The reusable AI tool for building and refining Minecraft worlds from real-place evidence. One checkout; many worlds possible; exactly one world active at a time.
_Avoid_: refine tool, legacy generator refine, Southampton Refine (as the tool name)

**World**:
One game place under construction (offsets, gameworld id, cell registry, issues, feature/tree build scripts, local providers). Lives in its own repository, checked out under the worldbuilder’s gitignored `worlds/` folder.
_Avoid_: site (as the name of the whole place), project, instance (unless meaning the Minecraft launcher instance)

**Active world**:
The single world the worldbuilder is pointed at right now. Chosen by system-level configuration (`active_world.py`), not by which folder you happened to open.
_Avoid_: current cell, current queue item

**System provider**:
A reference feed whose geographic scope is nationwide or wider (UK / England or above), kept in the worldbuilder so every world can use it (`config/system-providers.json`; code under tool `providers/<id>/` when `package` is set).
_Avoid_: global source, default source

**Local provider**:
A reference feed below national scope (city, campus, single site). Kept in that world’s repository (`config/local-providers.json`; tools under world `providers/<id>/` with a catalogue `package` field). When adding a provider, ask local vs system; rule of thumb: below national → local. Loaders resolve `package` against both system and world roots (`cell_sources.provider_package_dir`).
_Avoid_: place source (as the only term)

## Trackers

**System tracker**:
Matt-style task index for worldbuilder engineering (`tasks/`). Lives only in this repo.
_Avoid_: tooling queue, world-queue files (for system work)

**World tracker**:
That world’s cell registry (next work + issues). Never holds system engineering tasks.
_Avoid_: TODO.txt, retired world queue files, system tasks in the world registry

## Cells and registry

**Cell**:
A 128×128 Minecraft block patch on the world grid (`tx,tz`), sized and originated from `config/world-frame.json` via `cellmath`. Work is planned and recorded per cell.
_Avoid_: chunk (unless meaning an MC chunk), tile (unless meaning a map/lidar tile)

**Cell registry**:
`CellRegistry` over the cell journal (`registry/journal.jsonl`), driven by `scripts/cell_registry.py`. Progress, holds, ghost state, ownership and notes are views folded from the journal, not stored files. Paths are per active world — never share with a donor or bake-off save. See [ADR 004](docs/adr/004-cell-journal.md).
_Avoid_: stage-files-as-progress, retired queue/focus files, `*-cells.json`, `registry/cells.json` as a writable record

**Cell journal**:
The single append-only `registry/journal.jsonl`. Each entry is written `begun` (fsynced) before any block write, then `landed` or `mismatched` after readback. `CellRegistry.change(intent)` (Build, Undo, Note) is the only writer; `view()` returns a frozen per-cell `CellView`.
_Avoid_: stage files, apply log, verification JSON as progress SoT

**Reconcile**:
`cell_registry.py reconcile [CELL...] [--fix]`: compares the journal to actual blocks; with `--fix`, re-commits recorded plans over MCP. Replaces cell repair and `check_world_presence`. A "mess in a cell" is fixed this way, never by region restore.
_Avoid_: region restore, repair-by-snapshot

**Recover (interrupted apply)**:
`site_recover.py`: resolves **one** unfinished journal entry (land it if blocks already match the plan, or roll back to the pre-apply structure). Not for general drift — use reconcile for that.
_Avoid_: using recover as a substitute for reconcile

**BlockStore**:
The one real seam under the journal, with `LiveBlocks` (MCP `block_scan_region`), `MemoryBlocks` (tests), and `RegionBlocks` (read-only `.mca` for planning / dry-run after a server save; #111). Live applies and readback stay on MCP. Planners that still say `World` use a duck-typed facade (`planning_world()` for disk, default `World()` for live).
_Avoid_: region backup, snapshot, writing or restoring region files; parsing `.mca` for live apply readback

**Element**:
One of the lasting kinds of place content a finished cell must cover: **feature**, **ground**, **trees**, or **decor**. The concept (not a journal `Build` intent). Journal wire values stay those four strings; code may keep `REAL_PASSES` as the same tuple. Excludes **lidar ghost** (placeholder) and `other` / neutral ownership.
_Avoid_: aspect, strand, real pass (as the concept name), layer (as the superclass — features are not layers), build (the apply intent)

**Feature** (registry):
A named major build (house, bridge, …) with status `proposed|in_progress|built|…`, optional ownership files under `site/builds/`, and the list of cells its volume touches. One **Element**; not a layered pass.
_Avoid_: layer pass, decor prop, queue item

**Registry marker**:
At most one cell may have `marker=up`. That cell is the focus ring (`cell_marker.py --raise` / `--clear`).
_Avoid_: focus file as a separate progress SoT

**In-flight apply**:
A `begun` journal entry with no `landed`/`mismatched` finish. `site_recover` lands or rolls it back; `reconcile` can also surface it. No per-cell stage files.
_Avoid_: stage files as progress

## Evidence

**Type**:
A stable class of evidence question. Named without vendors. Documented in `reference/TYPES.txt`.
_Avoid_: Source family, vendor-as-type naming, aerial-as-vendor

**Provider**:
A feed that fills one or more types. Either a system provider or a local provider.
_Avoid_: Type, source family keyed only by vendor

**World frame**:
How real geography maps into Minecraft: CRS, offsets, Y offset, bounds, anchor. Stored in the world’s `config/world-frame.json`; read via `scripts/world_frame.py`.
_Avoid_: scattering OFFSET_E / SEA_LEVEL / private CRS subprocesses across scripts

**XYZ tile position**:
A Web Mercator slippy-map tile `z/x/y` plus floating-point pixel offsets on a 256-pixel *logical* tile: `east_px` rightward and `south_px` downward from the northwest corner (not northings; independent of downloaded image size). Convert with `scripts/world_frame.py` (`to-wm` / `from-wm` / `batch-wm`).
_Avoid_: inventing a second mercator helper, treating offsets as metres/northings, or conflating this with tile-image fetch / evidence-sheet tools

**Anchor**:
The real-world point mapped to Minecraft (0, 0) on the horizontal plane.
_Avoid_: origin (unless meaning the MC block origin), spawn (unless meaning the player spawn)

**Y offset**:
The constant added when mapping real elevation into Minecraft Y.
_Avoid_: sea level (unless that is the explicit datum chosen)

**Empty world**:
A game save started without a prior city mesh from another generator. Refine means building **Elements** from evidence (features plus layered passes), not clearing leftover vendor lumps.
_Avoid_: third-party generator refine (as the default workflow), lump sweep (as the definition of done)

**Lidar ghost**:
Placeholder **ground** in a cell from national lidar composite terrain (DTM fill + surface). The surface block colour encodes DSM − DTM height per column (`light_gray_concrete` ≈0, `gray_concrete` 1–2, `black_concrete` >2) so agents can check feature location against the footprint. No volumetric feature ghosts. Real ground replaces ghost ground later. Frontier membership comes from the registry (any **Element** started in a cell seeds a Chebyshev ring).
_Avoid_: finished ground pass, real feature build, vendor lump, baseline height sketch, grey DSM−DTM columns
