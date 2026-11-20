# #259 trial: park and rural patch (findings)

Spike, 2026-11-20, world `southampton-build` (save `SouthamptonAI`). Plan: [vibe-at-scale.md section 6](vibe-at-scale.md#6-owner-trials-spikes).
Classes and row shape: [land-use-classes.md section 3](land-use-classes.md#3-defaults-table), `config/land-use-classes.json`.
No tool code changed on main. Prototype code stayed in the session scratchpad (described under "Variant B" so it can be rebuilt).

## Summary

- Both study cells were built end to end through the cell registry (prepare, features, ground, trees, decor), with 0 readback mismatches on every apply.
- With today's defaults, open land is badly wrong in two ways:
  - Shade, brown undergrowth and worn grass become **light grey concrete** ("aerial paving"): 1,096 columns in the park, 2,087 in the farmland.
  - The aerial texture detector **plants rough meadow and pasture as forest**: 170 of 198 park trees stood on open meadow, and 186 of 299 farmland crowns were in open fields.
- A class-gated variant fixed both:
  - unmapped "paving" becomes coarse dirt in the park and grass on rural land;
  - trees are kept only from mapped trees, mapped wood or scrub polygons, or lidar height.
- Owner verdicts:
  - **Park: good enough as a default.**
  - **Farmland: close.** The woods need more density, and plain grass is bland where it is not a lawn. See [Owner verdicts](#owner-verdicts).
- Separately, the ground, trees and decor passes could not see land below MC Y 31: surface probe floor plus prefetch range. Trial #258 hit the same fault. A fix is on a branch from the debug session (`low-ground-probe`), not merged.

## Study areas and build windows

| Area | Point (WGS84 → MC) | Cell | Window | What the evidence shows |
| --- | --- | --- | --- | --- |
| Park: Riverside Park, River Itchen | 50.93559,-1.37029 → 8096,-5709 | 63,-45 | whole cell, 8064..8191 × -5760..-5633 | River across the north third, wooded north bank, an asphalt shared path on the south bank, open rough meadow (`landuse=meadow`) with two worn desire lines, tree clumps to the south. There are no pitches in this cell. |
| Farmland | 50.93895,-1.3266 → 11163,-6109 | 87,-48 | whole cell, 11136..11263 × -6144..-6017 | Rough pasture and meadow, two broadleaved woods, one scrub patch, one mapped hedge and a service lane into the wood. There are no arable fields or gates in this cell. |

Whole cells were chosen because the registry pipeline works per 128 × 128 cell, and one cell costs about 10-20 minutes of apply time (see [Cost](#cost)).

Frontier ghosting was off for every live command (`LIDAR_GHOST_RING=0`), so no neighbouring cell got ghost ground. The default ring of 2 would have ghosted 24 cells around each study cell.

Tree crowns overhang into the air above the neighbouring cells by design. The farmland trees mask touches cells 86,-48, 87,-49, 87,-47, 88,-49 and 88,-48.

## Downloads (owner-authorised, this trial only)

| Source | Files | Size |
| --- | --- | --- |
| Google satellite z19 tiles (the `google_aerial` tile URL and fetch code), park | 16 tiles stitched to `worlds/southampton-build/reference/aerial/cell-63-45-z19/mosaic-z19-4x4-260147-175667.png` | 293,865 bytes downloaded; mosaic 2,217,773 bytes |
| same, farmland | 12 tiles stitched to `reference/aerial/cell-87-48-z19/mosaic-z19-4x3-260211-175660.png` | 208,003 bytes downloaded; mosaic 1,585,903 bytes |
| same, review sheet from `google_aerial sheet --cell 63,-45`, which fetches its own tiles | the 16 park tiles again, written to `instance/google-aerial/63_-45.png` | about 0.3 MB downloaded; sheet 2,224,955 bytes |
| Defra lidar composite 2022 (DTM and last-return DSM), tile SU4515, `environment.data.gov.uk`, via `lidar_extremes --cell 87,-48 --fetch`. Approved by the owner during the trial. | `downloads/defra-lidar/DTM/SU41ne_DTM_1m.tif`, `downloads/defra-lidar/DSM/SU41ne_LZ_DSM_1m.tif` | 66,028,436 + 68,987,998 bytes; took 263 s |

That makes 44 tile requests in all. No other downloads; the park cell already had cached lidar.

## What was built

### Variant A: today's defaults (park cell only, later replaced)

- **River:** a `water_body` feature, `site/builds/river_itchen_63_m45.json`.
  - Surface at Y 12, one below the bank; lidar reads 2-3 m AOD over both the water and the bank.
  - Depth 3, mud bed, grass bank at Y 13 (`rise: 1`), air cleared 3 blocks above.
  - 1,940 water columns, 13,882 blocks.
- **Ground:** 14,114 columns, made of:
  - 10,645 mapped grass;
  - 679 mapped path surface;
  - **1,107 "aerial paving", which became light grey concrete**, mostly along the worn desire lines and bare patches.
- **Decor:** 5 of 6 mapped items (2 benches, 2 picnic tables, 1 bin; 21 blocks).
  - The park row's `bench_per_100m` inferred nothing.
  - A lidar "garage" candidate inside the river was left unresolved, and nothing was built there.
- **Trees:** planned, never applied (see the hang below).
  - 198 crowns, 170 of them on the open meadow, 143 of those at the 7-block maximum radius.
  - Lidar canopy (DSM − DTM ≥ 3 m) supports only 8 of the 170 meadow crowns.

### Variant B: class-gated open land (both cells, in game now)

Two scratch hooks, loaded through a `sitecustomize` on `PYTHONPATH`:

- **Surfaces.** After `ground_layer.classify`, any `paving` column that is not on a mapped way (`vmap.pri < 40`) becomes:
  - `grass` inside a rasterised OSM `natural=wood|scrub` polygon;
  - otherwise the open-land kind of the cell: `dirt` (coarse dirt, worn ground) in the park, `grass` on farmland.
- **Trees.** After `trees_layer.planning_inputs`, a crown is kept only when it is:
  - a mapped tree, or
  - within 2 blocks of a wood or scrub polygon, or
  - over lidar standing height of 2 m or more within 2 blocks.

  Everything else is dropped.
- **Low-ground patch (trial-only).** The same scratch file lowers the `surface_probe.surface_y` floor and `ground_layer.PROBE_Y_RANGE` to Y 5. It is superseded by the `low-ground-probe` branch.

| Cell | Ground | Trees | Decor |
| --- | --- | --- | --- |
| 63,-45 park | 14,122 columns; 1,348 unmapped paving → coarse dirt, 18 → grass | 40 kept (8 mapped, 29 in wood, 3 by lidar), 258 dropped; apply 434 s | 5 mapped items, 21 blocks |
| 87,-48 farmland | 16,384 columns; 1,108 unmapped paving → grass in the open, 1,588 → grass under woods | 113 kept (111 in wood, 2 by lidar), 186 dropped; apply 962 s | mapped hedge, 35 in-cell columns, 2 high, persistent oak leaves (the other 892 hedge columns lie outside the cell) |

All applies landed with 0 mismatches. Registry state is in `registry/journal.jsonl`, journal sequence numbers 1244-1293 for these cells.

No world snags were filed. Every fault seen is tool-wide rather than specific to these cells, so each is a follow-up task below, and the owner accepted the park as built.

Scratch artefacts in the world repo (not committed by this session, because the world checkout is shared with #258):
- the river spec `site/builds/river_itchen_63_m45.json`;
- the two aerial mosaic folders;
- the reports under `verification/site/highfield_{63_m45,87_m48}_*`.

## Owner verdicts

Viewed in game on 2026-11-20 from walking height and from a raised spectator view in each cell. Riverside Park is a flat valley floor, so its "hill" view is a raised spectator view inside the cell.

| Area | Verdict | Owner's words, summarised |
| --- | --- | --- |
| Park (variant B) | **Good enough as a default** | Accepted with the known faults left as follow-ups: the riverside asphalt path reads as a coarse-dirt band with a thin grey strip, and the wood across the river is thinner than the aerial. |
| Farmland (variant B) | **Close, needs fixes** | "Trees probably need a bit more density. The plain grass feels a bit bland; if it's not a lawn maybe it needs grass tufts. The aerial shows mottled ground, not clean grass." The owner asked for ideas that don't mess up real lawns and other short, tidy grass (answered below). |

### Rough grass without spoiling lawns: proposal

1. **Gate by tags and land-use class, never by colour alone.**
   - Rough treatment only where OSM says `landuse=meadow|farmland` (pasture), `natural=grassland|heath|scrub`, `leisure=nature_reserve`, or the class is rural or natural.
   - Lawns stay clean on `landuse=grass` (mown verges), `leisure=pitch|golf_course|garden|playground`, residential, civic and commercial classes, and every private frontage.
   - A `leisure=park` area with no meadow tag gets a light touch only (about 3% tufts).
2. **Let the aerial place it, not random noise.** The z19 texture signal that wrongly spawned trees is a good "rough grass" signal on open land:
   - high local variance → `short_grass` tufts and `tall_grass`;
   - pale or brown pixels → `coarse_dirt` / `rooted_dirt` patches (dry grass, bare earth);
   - dark olive → `tall_grass` or `fern`.

   Seed by cell and column so reruns are stable.
3. **Starting palette (low confidence, owner to tune).**

   | Land | Tufts | Tall grass | Bare patches | Flowers |
   | --- | --- | --- | --- | --- |
   | Meadow | 20-30% `short_grass` | 3-8% `tall_grass` | — | ≤3% `oxeye_daisy`, `cornflower`, `dandelion` |
   | Pasture | 10-15% | ≤2% | ≤5% coarse dirt | — |
4. **Woodland is its own treatment:**
   - dense fill only inside wood polygons, towards the aerial canopy cover (about 80%);
   - mixed species (oak, birch, some dark oak) and varied trunk heights;
   - understorey bushes (persistent leaves at Y+1 to Y+2) and ferns;
   - a `podzol` / `coarse_dirt` / `moss_block` floor, with grass only at the edges.
5. **Everything is labelled inference and removable.** Put it in the existing foliage or decor masks, so a redo strips it and the report counts it, like shrubs and flowers (#265, #266).

## Recommended defaults rows

Values for `config/land-use-classes.json` and the ground and trees passes.

- "Existing key" rows use today's row shape (`frontage`, `planting`, `furniture`).
- "Proposed key" rows need a schema change (new keys `surface`, `trees`, `ground_texture`). They are written here so the follow-up tasks can add them.

### park (`leisure=park|garden|pitch|…`, `landuse=recreation_ground|village_green`)

| Key | Recommended | Evidence |
| --- | --- | --- |
| `frontage.boundary.perimeter` | `null` (keep) | Riverside Park has no perimeter fence along the river or meadow; boundaries only from mapped `barrier=*` |
| `frontage.private_surface` | `keep` | as shipped |
| `furniture` | keep `{bench_per_100m: 1.5}`, but mapped points first | OSM benches, picnic tables and bins were placed exactly. The per-100 m inference placed nothing on 2 mapped paths; check why (follow-up) |
| `planting` | `null` until rough-grass rules exist | — |
| proposed `surface.unmapped_paving` | `dirt` (coarse dirt, worn ground), never concrete; unless within 3 blocks of a mapped way, which then widens to the way's surface | variant B; owner accepted |
| proposed `surface.path_by_tag` | OSM `surface`: `asphalt` → tarmac, `grass` → grass (no path block), `gravel`/`compacted`/`fine_gravel` → gravel, `dirt`/`ground` → coarse dirt; untagged footway in park → gravel | `ground_sources` already maps `surface`; the riverside path is the counter-example (aerial width lost) |
| proposed `trees.sources` | mapped trees, wood or scrub polygons, lidar height ≥ 2 m; no texture-only crowns on open grass | variant B: 258 false crowns removed, owner accepted |
| proposed `ground_texture` | lawn: none; with `landuse=meadow`: meadow palette above | owner request |
| water (river) | water 1 block below lidar bank, depth 3, mud bed, grass bank `rise: 1`, clipped per cell with no bank on clip edges | built; owner accepted |

### rural (`landuse=farmland|meadow|orchard|…`)

| Key | Recommended | Evidence |
| --- | --- | --- |
| `frontage.inference` | `false` (keep) | no house frontage on farmland |
| `frontage.boundary.*` | `null`; boundaries only from mapped `barrier=hedge|fence|wall|gate` | one mapped hedge built well; no field fence evidenced, so no fence default |
| hedge (decor) | persistent oak leaves, height 2 (today's default) | owner did not object; hedgerow trees are not modelled (follow-up) |
| proposed `surface.unmapped_paving` | `grass` | 1,108 columns in the open, accepted visually |
| proposed `trees.sources` | as park | 186 false crowns removed |
| proposed `ground_texture` | pasture palette; meadow palette where `landuse=meadow` | owner: "plain grass feels a bit bland" |

### natural (`natural=wood|scrub|heath|grassland`, `landuse=forest`)

| Key | Recommended | Evidence |
| --- | --- | --- |
| `frontage.*` | as shipped (`inference: false`) | — |
| proposed `trees.fill` | dense fill only inside the polygon, target canopy cover from the aerial (about 80%), mixed species, varied heights | owner: "trees probably need a bit more density"; the woods read as parkland |
| proposed `trees.understorey` | bushes (leaves Y+1..Y+2) about 1 per 12 columns, ferns about 10% | owner verdict; aerial shows a closed canopy |
| proposed `surface.floor` | podzol / coarse dirt / moss mix inside wood, grass at the edge; scrub: grass with bushes | 1,588 "paving" columns under farmland woods were shade, not paving |

## Evidence: what exists UK-wide and where it is thin

| Evidence | UK-wide? | Notes from this trial |
| --- | --- | --- |
| OSM land use (`landuse`, `leisure`, `natural`) | yes; reliable for parks, patchier in the countryside | Both cells had park/meadow/wood polygons. Rural OSM is sparse: the north-east tree belt in the farmland cell is not mapped as wood, so the gate lost it. |
| OSM paths and `surface` | yes; `surface` often missing | The riverside path had `surface=asphalt` and `est_width=1.75`; desire lines were tagged `surface=grass`. Untagged paths need a class default. |
| OSM furniture (benches, picnic tables, bins) | patchy, good in urban parks | 6 points in the park, 5 placed. Farmland has none, as expected. |
| OSM hedges | patchy | one hedge in the farmland cell; most field boundaries are unmapped. |
| OSM water polygons | yes for rivers and lakes | the Itchen polygon spans 12 cells and 22,824 columns. |
| Defra lidar DTM, 1 m | England only | Height source outside England is still unverified: Scotland (Remote Sensing Data portal), Wales (DataMapWales) and Northern Ireland have their own products with different licences and coverage. |
| Defra lidar DSM | England only; **last-return only** in the composite the tool downloads (`lidar_composite_last_return_dsm`) | Last return goes through foliage. Only 6% of the park cell reads ≥ 3 m standing height, even under the wood, so "canopy from lidar" needs the first-return DSM. |
| Aerial z19 (Google, owner-authorised per cell) | yes, but not a keepable licence; bulk use not allowed | Good for crowns and texture; misreads shade, brown undergrowth and worn grass as paving. |

## Cost

**Method.**
- Wall-clock times come from the cell journal (`begin`/`finish` `ts`) and from this session's command logs with `date` stamps.
- Session tokens come from the Claude Code context-window reading (`get_usage`): 169,490 tokens at 13:18 (23 minutes in) and 318,508 at 16:52. That is the conversation size, not billed tokens; with prompt caching, billed input is several times larger.
- The `low-ground-probe` debug session is not included.
- Model: Sonnet 5.5 until about 13:55, then Opus 5.5.

| Step | Park 63,-45 | Farmland 87,-48 |
| --- | --- | --- |
| Aerial fetch and stitch | under 1 min (16 tiles) | under 1 min (12 tiles) |
| Lidar fetch | cached | 4.4 min (2 × ~67 MB) |
| `prepare --apply` (PBF extract + focus ghost) | 2 min | 2 min |
| River `water_body` apply | 24 s | — |
| Ground apply | 33-47 s | 46 s |
| Trees apply | 7.2 min (40 trees) | 16 min (113 trees, about 8.5 s per tree) |
| Decor apply (redo, incl. undo and hover) | about 1 min | about 1 min |
| **Machine time per cell** | **about 11 min** | **about 25 min with the lidar fetch, 20 without** |

- **Session:** 12:55-17:00, about 4 hours. About 1 hour of that was waiting for trial #258's site lock. About 1.5 hours went on three tool faults: the Y-31 floor, the hang my first workaround caused, and the paving and tree faults.
- **Estimate per open-land cell once the follow-ups land:** about 15-20 minutes of machine time and well under 50k context tokens for an agent that only runs the pipeline and reviews renders.

## Follow-up tasks to file

1. **Surface probe blind below MC Y 31.**
   - The fault: `surface_probe.surface_y` stops at Y 31, and `ground_layer.PROBE_Y_RANGE` starts at 31. Any land below about 20 m AOD in `southampton-build` reads as "empty column": ground plans 0 columns, and trees and decor probe nothing.
   - Lowering only the probe floor makes the trees apply crawl, because every read below the prefetch is a separate paced MCP call; that caused the 25-minute hang.
   - Hit by #258 (cell 55,-41) and #259 (cell 63,-45). Fix branch: `low-ground-probe` (debug session), not merged. The orchestrator files the task and decides on merging.
2. **Open land becomes concrete.** Unmapped "aerial paving" (shade, brown undergrowth, worn grass) in park, rural and natural classes becomes light grey concrete.
   - Wanted: a per-class `surface.unmapped_paving`; widen a mapped way to the paving seen beside it; a woodland-floor kind.
   - Scope by land-use class, UK-wide.
3. **Trees on open grass.** The texture-only crown detector and dense fill plant meadow and pasture as forest. Gate crowns on open land to mapped trees, wood or scrub polygons, or lidar standing height (variant B rule).
4. **Woodland treatment.**
   - Dense fill to aerial canopy cover inside wood polygons.
   - Mixed species and heights, an understorey of bushes and ferns, and a podzol/moss floor.
   - Owner: "trees probably need a bit more density".
5. **Rough grass and mottled ground on non-lawn grass**, gated by tag and class as in the proposal above, placed from aerial texture, removable through the masks.
   - Owner request.
   - Needs an owner decision on the palette shares.
6. **First-return lidar for canopy.** Add the Defra first-return DSM (or note when it is unavailable) so lidar standing height can confirm trees. The last-return composite misses most canopy.
7. **Rivers across cells.** Today `water_body` proposes a body only in its home cell, and builds it whole. The Itchen spans 12 cells and 22,824 columns.
   - Wanted: build per cell, clipped, with no bank ring on clip edges, and the plan recorded per cell. The trial used a scratch clip wrapper and a manual `plan_feature` note.
   - Also: river water level from lidar along the reach, not one integer per body.
8. **Park bench inference placed nothing.** `bench_per_100m: 1.5` gave 0 inferred benches along about 250 m of mapped park paths. Find out why (lit paths only? mapped benches suppress it?) and record the rule.
9. **Hedgerow trees and unmapped tree belts.** Rural tree lines along hedges or lanes that OSM does not map as wood are lost by the gate. Consider `natural=tree_row` plus aerial-and-lidar evidence along mapped hedges.
10. **Lidar "outbuilding" candidate over water.** Decor proposed a lidar garage inside the river (8117,-5733). Candidates on mapped water should be rejected.
