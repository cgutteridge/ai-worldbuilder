# Vibe at scale: plan for large UK areas with few tokens

Status: draft for owner review, 2026-11-10 (sherlock run, revised after an independent review that
corrected the first draft: several issues already have merged fixes). Records owner direction of
2026-11-10: the tool must handle anywhere in the UK; process large areas with minimal tokens; the result
should give the *vibe* of an area in Minecraft, not a 1:1 duplicate; classes now, refined with experience.
Related rules: `rules/radial-good-enough.md`, `rules/frontage-and-evidence.md`, `rules/evidence-order.md`.

Provider assumption (owner, 2026-11-10): other people using the toolkit install their own providers
(lidar, aerial, vector map, street imagery). The toolkit therefore asks for a reference *type*, never a
vendor (`docs/provider-name-audit.md`, #28), and nothing in this plan may assume a particular lidar vendor or country.
Today the type seam covers the lidar `world` and `tiles` modules (`lib/sources/lidar_provider.py`, catalogue
rows with `types: ["lidar"]`), but `lidar_ghost` and `lidar_query`, imported by ground, cell preparation and
the registry, are shims hard-wired to the one system lidar package, and a locally installed lidar provider never beats the system row; task #262 closes both gaps. Whether a
non-lidar fallback (coarser terrain data) is needed where no lidar exists is unverified; a trial in such an
area should say what it used.

## 1. Principles

1. **Vibe over duplicate.** Street-readable shape, colour, rhythm and silhouette first. A detail that does
   not change what a player sees from a street or from a hill is "good enough" and is not chased.
2. **Generic first, refined later.** Every mechanism is keyed by a land-use or building class with an
   `other/unknown` default (#253). A class we have not refined degrades to a plain default, never to a
   wrong or invented one (evidence ladder unchanged: observed, then area evidence, then labelled inference).
3. **Cost is a design input.** A cell is built by rules plus a small per-district profile, not by designing
   each building. Per-building design is spent only on landmarks the owner names.
4. **Learn from a real area before generalising.** Each new kind of place gets a short owner-run trial
   (a spike) on a study area before its builder is productionised. Findings go back into this plan.
5. **Keep what scales.** Reliability, correctness bugs and timing are scale problems and stay on the
   list; fidelity chasing on single buildings is parked.

## 2. Tiers of detail

| Tier | What | How it is built | Token cost |
| --- | --- | --- | --- |
| A. Landmark | A few named buildings per district (owner nominates) | Designed spec (`feature_build.py`), refined by snags | High, rare |
| B. Typology | Houses, terraces, shops, sheds, warehouses | Rules from footprint, lidar height, vector-map tags (`building`, `building:levels`, `roof:shape`) and the district profile; builds on `house_build.py` and `feature_build.py` with generated minimal specs, not a third builder | Low, per building |
| C. Massing | Dense background, far areas | Block-plus-simple-roof from footprint and lidar | Lowest |
| Linear | Roads, paths, rail, water edges | Corridor builders (section 5 for rail) | Low |
| Cover | Land use: lawns, fields, woodland, parks, yards | Class table (#253) read by ground, trees and decor | Low |

Unknown today (the trials measure it): how much of a real district tier B can cover well, and the cost
in minutes and session tokens per cell. No target figure is set until a trial measures one. Token use is
read from the session usage figure before and after a trial, and the method is stated in the findings.

## 3. District vibe profile (new idea, small)

One short file per district: palette per building class, usual roof forms and storeys, boundary conventions,
street furniture density and lamp spacing, and a list of nominated landmarks. Written once from a study of
the area (aerial plus a few street hints), reused by every cell in it, refined after owner review.
Task #255 (after #254, because a profile overrides class defaults). Precedence, most specific first:
per-house override, street convention (`site/gardens/<street>.json`, read today only by
`garden_sources.load_convention`), district profile, land-use class default (#253).

## 4. Where every open issue and task stands

An open issue with a merged fix is **fix landed, awaiting rebuild and review**: the code is done, the
element in the world has not been rebuilt since (or the owner has not yet judged it). Those go to the builds
handoff; they are not tool work. The cause text in several issues is pre-fix history.

### Issues (world, southampton-build)

| Issue | Verdict | Evidence |
| --- | --- | --- |
| Roof built flat where the real building is pitched (I-009) | Fix landed (#125); West confirmed. Kiddle annex (S-067) still flat after the 2026-10-29 rebuild: no diagnosed cause | Candidate, unverified: `zone_ridge_half_steps` keeps a zone flat when its lidar rise is under 1 m (`flat_tol_m`); the annex's measured rise is not known |
| Pitched roofs built from full blocks (I-019), roof stairs ignore neighbours (I-022), stairs use the lower roof's material (I-023), floors inside the roof space (I-037) | Fix landed (#149, #169, #170, #205, #207, #209); awaiting rebuild of West and Kiddle and owner review | Code uses `roof_lint.stair_shape_from_neighbours`, bottom-half slabs, floors below `zone_eave` |
| Decor on an overhanging roof (I-038), trees on a roof (I-046), decor blocks in the roadway (I-026) | Roof overhang: #206 skipped the column (veto); #301 replaces it: trees and decor stand on the block at lidar terrain Y (`surface_probe.ground_at`), never the roof. Unplaced only as `no ground at terrain` or `feature above terrain`; awaiting merge, then a trees and decor redo of 48,-44 and 48,-45 and review. Roadway: fix landed (#172, #173, #200, #201) | Fix logs, #301 |
| Doors at the wrong height (I-017), no porch (I-021) | Fix landed (#145, #146, #204; #174); awaiting SUSU rebuild and review | I-017's guard refuses entrances whose live ground differs by more than 3, so some need the ground fixed first |
| Windows ignore facade_plan rows (I-030), panes without frames (I-013), no roof or facade detail (I-006) | Fix landed (#192; #131, #235; #132); awaiting rebuild and review | Fix logs |
| Skylights are glass boxes (I-033), roof greeble over the roof edge (I-025) | Fix landed (#194; #171, #210); awaiting rebuild of SUSU, r3176751 and EEE; review | Seven snags across three buildings, not one landmark; S-060 (no skylights after #194) may mean the guard is too strict |
| Houses drop out of the cell's obligations (I-012) | Fix landed (#154); the owner recheck is `status`/`next` listing the University Road houses | Fix log |
| Ground built before features leaves footprints (I-040) | Open. S-070 awaits recheck after clips are re-extracted. S-075 (interior air dropped by the earlier-owner clash filter where an older ground build owns paving) has a stated cause and no task: #261 | Snag text; not measured across other plans |
| Void beside SUSU/Jubilee walls (I-015) | Open, cause recorded, no fix logged | Cause text |
| Suburban streets lack frontages, boundaries, entrances (I-042) | Open: #240, then #254 and #255 | Tasks |
| Decor places no street furniture (I-008) | Open: #260. Candidate cause for missing points: the furniture preset failed under GDAL's default schema (#237, needs owner input) | Task #237 notes; unverified for the 2026-10-27 observation |
| Pond built as ground (I-014) | Open; a water-body feature exists (#163); awaiting rebuild | Fix log |
| Decor apply sat silent 20+ minutes (I-044) | Diagnosed 2026-11-09; #229 | Task |

Nothing here is proposed for wontfix. The earlier suggestion to wontfix the skylight issues is withdrawn.

### Tasks (tool)

| Task | Verdict |
| --- | --- |
| #229 timing measurement | Keep, includes I-044; trials add session-token notes |
| #240 residential frontage | Keep; finish its slices and stop; further generality goes to #253 and #254 |
| #244 sparse front boundaries in 53,-43 | Reshape: blocked by #255; its profile part overlaps #255, its aerial-hedge opening part overlaps #240 |
| #245 site_lock macOS crash, #246 house_build refuses feature-path spec, #247 undo checks readiness, #248 builder provenance | Keep: reliability for long unattended runs |
| #249 landed-door audit | Keep, reworded: extend the existing `scripts/door_audit.py` (jamb and lintel on live blocks, no tests today) with the landed-plan checks; the rebuild half is world work |
| #252 planting by land use, #253 class table, #254 wire to class defaults | Keep (added 2026-11-10) |
| #232, #233, #234, #236, #237, #238 (blocked, need owner input) | Keep blocked; cell-edge and concurrent-prepare policy (#232, #234) matter most for scale and should be decided soon |

### New tasks

#255 district vibe profile; #256 railway trial; #257 typology buildings trial; #258 industrial and commercial
trial; #259 park and rural trial; #260 street furniture inference; #261 house interior air under older
ground paving; #262 provider-neutral lidar ghost and query. The roof recipe draft was withdrawn: the code already follows the game's stair rule.

## 5. Railways: real rails for long runs

Goal: long straight and gently curving runs are real Minecraft rails (the vibe players expect), with
ballast and a levelled corridor, from railway lines in the map and lidar ground.

Game rules the design depends on, **all unverified until trial #256 confirms them on the current client**:
rails need a solid block below; a rail slopes only along a straight north, south, east or west run, one
block of rise per block of run; curves are 90-degree corners and flat; there is no true diagonal rail, so
a diagonal run is a staircase of corners.

Proposed method (to be proven by #256):
1. Take the map centreline, smooth it, and snap it to a rail-legal path: long cardinal runs joined by
   90-degree corners; gentle bends become staircase corners. Where the lines come from needs settling:
   `railway` is in `CELL_EXTRACT_LAYERS` since #379. The line planner is still a later ticket.
2. Take a ground profile along the path from lidar, smooth it, and clamp it to a gentle gradient (real
   railways are far flatter than roads; the exact figure is to be taken from a source, not assumed): level
   the corridor, cut or fill with grass side slopes, one ascending rail where a step is needed.
3. Bed: ballast and rails, optional second track for double lines.
4. Later versions, not the first trial: bridges and tunnels from `bridge`/`tunnel` tags, road crossings,
   fence lines, stations, signals, overhead wires.

## 6. Owner trials (spikes)

Each trial is a short supervised build in a study area the owner picks, in the named world, under the site
lock, in a scratch region or an area the owner accepts being rebuilt. The deliverable is a findings file in
this directory, a measured cost and a recommendation (tool deliverables, so they sit in `tasks/`); scratch
effects, snags and rebuilt cells belong to the world's own repo, and standing up a new world is separate
world work. Each starts `blocked` with "NEEDS USER INPUT" until the owner names the study area.

| Trial | Study area to pick | Question it answers |
| --- | --- | --- |
| #256 railway | At least 300 blocks with a curve and a cutting or embankment | Does the rail-legal path plus levelling look right, and what does it cost |
| #257 typology buildings | One terraced street, one semi-detached or detached street, one shop parade | How much of a district rules alone cover, versus per-building specs |
| #258 industrial and commercial | An industrial estate or retail park, and a high street | Which defaults carry the vibe |
| #259 park and rural | A park with paths and pitches, and a rural patch with fields, hedges and woodland | Class defaults for boundaries, surfaces, planting, water |

## 7. Order of work

1. Builds (owner and builds thread): rebuild West, David Kiddle, SUSU and Jubilee, EEE, r3176751, Retail,
   decor in 48,-44 and 48,-45; then `sherlock` checks the fixes. Cheapest way to clear most of the backlog.
2. Tool, in parallel: #229 (timing), #253 (class table), #245, #246, #247, #249 and #261.
3. Owner names study areas; trials run one at a time (they share the live world).
4. After the class table and first trials: #254 (blocked by #240), then #255, #252 and #260.
5. After each trial: update this plan and file implementation tasks from the findings (for example the
   railway corridor builder after #256); do not file them earlier.
