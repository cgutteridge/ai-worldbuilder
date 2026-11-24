# Railway trial (task 256): findings

Spike, 2026-11-22. World `southampton-build`. Plan context: [vibe-at-scale](vibe-at-scale.md) sections 5 and 6.
Study area and rebuild permission: `worlds/southampton-build/site/task-256-railway-trial.md`.
The prototype was throwaway scripts in the session scratchpad (not on a branch); the method below is enough to rewrite it.

**Owner verdict (2026-11-22, in game): yes, the run reads as a railway and the corridor looks natural.** The owner
also raised two points, below: powered rail on straight cells, and junctions left to humans. A cart ride end to end
is not recorded (see Game rules, not checked).

## What was built (since restored to ghost ground; see "Not in the normal workflow yet")

The owner's curve and branch: a double-track main line (South West Main Line, running NNE) and a double-track
branch (West Coastway Line) leaving it near 6830,-4090 and arcing round to about 7415,-4107.

| Track | Source ways (vector map) | Path cells | Rail level |
| --- | --- | --- | --- |
| main P | one way | 465 | 16 flat |
| main Q | two ways | 383 | 16 flat |
| branch U | four ways | 1156 | 16 to 20, four 1-block steps |
| branch V | four ways | 1175 | 16 to 20, four 1-block steps |

Main line clipped at z -4400 (north). South of the junction toward point A the main line widens to four or five
tracks plus yards; it was left out (first version). Bridges, tunnels, road crossings, fences, stations and signals
were left out as the brief said; the branch has two short mapped bridge segments near 7285,-4290 and 7415,-4107
that are built as ordinary embankment.

## Method (what worked)

1. **Lines.** The `railway` preset already exists in `shims/osm_extract.py`. It was not in `CELL_EXTRACT_LAYERS` during the trial; #379 added it.
   One-off extract: `shims/osm_extract.py --bbox-mc 6650,-4800,7500,-3800 --layers railway --dir rail_trial_256`
   gave 53 lines from the Hampshire PBF. Double track is two separate ways, so each way chain is its own track.
2. **Rail-legal snap.** Densify each polyline to 0.25 blocks, take the block cell under each point, and where two
   consecutive cells touch diagonally insert the corner cell nearer the line. Remove loops and backtracks. Result:
   every step is a cardinal move, so every cell is a straight or a 90-degree corner. A diagonal run becomes a staircase.
3. **Profile.** Ground height is the lidar DTM (`terrain` from `lidar_heightmap.heightmap`, block Y already including
   the world y-offset). Target per cell is the average of the cell and the nearest cell on the paired track, median
   smoothed over 9. A dynamic programme picks an integer rail level per cell minimising total distance to the
   target, with a step penalty. A step is allowed only where the cell and both neighbours are collinear (an ascending
   rail needs a straight run), and only 100 cells after the previous step. All four tracks start at one shared level.
4. **Corridor.** Columns within 2.3 blocks of any track cell take the level of the nearest track cell: gravel top,
   dirt fill under. Beyond that, grass side slopes at 2 blocks across per 1 block of height, out to 40 blocks, meeting
   lidar ground. 16,677 columns written.
5. **Apply.** Under `site_lock.py run`: 14 structure backups (`southampton_refine:before_rail256_<tx>_<tz>`, tiles of
   up to 128 by 128), 6,904 box fills, then 3,179 rail writes, all through `block_fill_batch`.
6. **Owner change: powered rail.** Straight cells (including ascending) became `powered_rail[powered=true]` with a
   redstone torch two blocks below the rail (air, rail, ground, torch). Corners stay plain rail because a powered
   rail cannot turn. 1,164 powered rails and 1,165 torches.

## Gradient

Owner decision (2026-11-22): no further research on the real figure. Build best effort from Minecraft's rail
quirks, and **fail loudly when a stretch cannot be made rail-legal**, assuming a human fixes any complex bit if they
care. The trial used **1 in 100 as the minimum run between 1-block steps**, a plausible figure (Wikipedia, Grade
(slope): 1 in 100 for the Settle and Carlisle "long drag", 1 in 80 for a secondary main line ruling grade), kept as a
plain config constant. On this floodplain the ground barely constrains it: the main line is flat and the branch rises
4 blocks over about 1,150 cells.

## Game rules (checked against the live client, Minecraft 26.2)

Confirmed by reading the placed blocks back with `block_scan_region`:

- Plain rail with an explicit `shape` is accepted through `/fill`-style writes for straight, corner and ascending
  shapes. Of 3,164 distinct rail cells, 3,151 read back exactly as planned. The 13 that did not are all where two
  tracks' staircases occupy the same cell near the junction (the last write wins; see Junctions).
- Powered rail: all 1,164 read back with `powered=true`, i.e. a torch under the ground block does power the rail
  above it, ascending rails included.
- A rail can sit on a gravel bed with a torch cell under the gravel.

Not checked: that a minecart actually travels the whole line, and that a corner immediately after an ascending
rail connects for a cart. Nobody has ridden it yet.

## Cost

| Item | Value | Method |
| --- | --- | --- |
| Wall clock, session start to built and verified | about 50 minutes | session created 08:29, last activity 09:19 (UTC) |
| Ghost ground for 21 corridor cells (18 applied) | about 11 minutes, about 35 s per cell | cell journal timestamps 08:50 to 09:01 |
| Backup of 14 tiles | 16 s | `apply_log.json` |
| Corridor fills (6,904 entries) | 4.7 s | same |
| Rails (3,179 entries) | 2.3 s | same |
| Powered pass (torches and rails) | about 1 s plus a 39-rail and 6-torch fix pass | script output |
| Session tokens | about 207k context at writing (window, not billed total) | `get_usage` context reading; the ghost session is separate and was not measured |
| Model and effort | Sonnet 5.5, session effort `high` | `get_session` |

Blocks changed: corridor fills 22,250; rails 3,173; torches 1,163; powered rails 1,163. All writes are inside the
planned corridor columns; nothing was written outside them. The corridor is 16,677 columns including slopes.

## What looked right and wrong

The owner's verdict was a plain yes (see the top of this file); no specific visual faults were reported beyond
the two points below.

- **Owner, powered rail:** wants powered rail so carts go fast, on straight cells only, with a torch two blocks
  below the rail. Done.
- **Owner, junctions:** junctions are too hard for AI; humans can fix them after a world is built. The builder should
  not try to model a junction.

Observed from the data, not yet judged in game:

- The 4-connected path is longer than the line: the branch is about 1,150 cells for about 850 blocks of true length,
  and about two thirds of branch cells are corners. Only the straight third can be powered, so carts will coast through
  the staircase stretches.
- Paired tracks stay within one level of each other; they differ in only 4 of 1,156 branch cells.
- The first powered pass missed 45 cells (39 rails, 6 torches) at the far end of the branch, around x 7376 to 7414,
  and a retry placed them. Cause not found; a builder should read back and retry.

## Junctions

Where the branch tracks meet the main line the snapped paths share or touch cells, giving the 13 overlap rails.
Per the owner, leave junctions to humans: the builder should stop each track short of the join (or keep them apart)
and note the location, not try a switch.

## Not in the normal workflow yet

The trial wrote to the world directly under the site lock. It was not a journalled feature build: the cell journal
had no entry for the corridor, no feature owned its blocks, `cell_registry.py` does not propose railway features, and
`railway` was not in `CELL_EXTRACT_LAYERS` until #379. A later real ground, trees or decor pass over these cells was free to
overwrite the corridor.

**The trial corridor has been removed (2026-11-22, owner's call, so other things can be built there before the real
trial).** All 14 backup tiles (`southampton_refine:before_rail256_*`) were loaded back under the site lock. A scan
found no rail, powered rail, redstone torch, gravel, grass or dirt left in the backup boxes, and 25 random
corridor columns matched the lidar ghost surface again. The 21 corridor cells keep their ghost ground. The restore
bypassed the journal, as the trial apply did. The backup tiles remain in the save's structures if they are wanted.

## Recommendation

Make railway a **feature** (one Element, like bridges and tunnels), not decor and not a new layer: it moves terrain,
crosses many cells, and a later ground, trees or decor pass should skip the blocks it owns. Decor stays right for
fences and signals later. The method above worked end to end in under a minute of world time, so the cost is the
design effort, not the apply.

## Owner decisions (2026-11-22), to carry into the tickets

None of these may be lost when the tickets are written. Each ticket should quote the ones it depends on.

1. **Prototype kept for reference** in [railway-prototype/](railway-prototype/README.md) as `.py.txt` files. The
   production builder is a clean port into `lib/` with tests, not a copy.
2. **Railway planning is part of the normal per-cell workflow**, not a manual extract. A cell with no railway is
   skipped at once. A cell already covered by an earlier railway feature is skipped at once. Otherwise a railway
   feature is planned starting from that cell. (This replaces the earlier idea of a separate corridor extract.)
3. **Size limit: a 2048 by 2048 box around the starting cell.** *Superseded by decision 17 (512 by 512); kept for
   the record.* It is a rough limiting rule, so keep the maths
   simple. A line that leaves the box is cut where it leaves; both features record the join (cell, level, direction,
   tracks) and the next feature starts at the recorded level. Ghost ground is needed across the box (about 16 to 25
   cells; roughly 10 to 15 minutes at the trial's 35 seconds a cell).
4. **Through line = `railway=rail` with no `service` tag**, whatever `usage` says. Sidings, crossovers, yards,
   depots, miniature, disused and turntables are best-effort **decor**: laid only where they make a clean unbroken
   run, skipped otherwise, never tied into junctions or the main line. Junctions are left to humans.
5. **The main line is a feature; the rest is decor.** Powered rail on every straight cell (ascending included) with
   a redstone torch two blocks below the rail; corners stay plain rail.
6. **Best effort, fail loudly.** Use Minecraft's rail quirks. Where a stretch cannot be made rail-legal, fail with a
   clear message and leave it for a human. No gradient research: the step spacing is a config constant, default 100.
7. **The rail feature trumps the road.** Within its footprint it lays its own ground and uses the road data to pick the
   surface where a road crosses (not the ground layer). The cell trees and decor passes skip the footprint.
8. **The line feature also owns the planting in its footprint.** Lidar decides it: where the surface model is above the
   ground model, bushes (gaps in the grass); elsewhere grass; no tall trees.
9. **Each level crossing, bridge and tunnel is its own feature.** It is Claude-designed from evidence, like any
   feature, and it MAY include rail lines, recording which abutting railway features it joins. Gates, lights and bell
   exist only if the map tags say so (`crossing:barrier`, `crossing:light`, `crossing:bell`); their style and
   colours come from Street View as part of that crossing's build. There is no separate style survey and no
   "unstyled" default. A crossing with no such tags is a plain crossing.
10. **Order at a crossing, bridge or tunnel:** the line is planned straight through the span, builds nothing in the
    gap, and records level, direction and tracks at each end of the gap. The crossing, bridge or tunnel is built
    afterwards, reads those records, and refuses ("not ready") until both abutting line features exist.
11. **Trial D reuses the trial corridor.** It already has one mapped level crossing (a node on each of its two tracks, about 4 blocks apart, near
    6980,-4343, cell 54,-34: `crossing:barrier=double_half`, `crossing:light=yes`, `crossing:bell=yes`, `crossing:activation=remote`,
    description "Manually Controlled Barriers monitored with Closed-Circuit Television (MCB-CCTV)"), 19 rail
    switches, and two mapped rail bridges. A hillier stretch can be a later ticket.
12. **Trial corridor removed.** The 14 backup tiles were loaded back and checked; the corridor cells keep ghost ground.
13. **Bridges and tunnels are general work** ([bridges-and-tunnels](bridges-and-tunnels.md)). File one decisions
    ticket now; write the evidence, planner and integration tickets after it closes.
15. **Bridges over rail are not gaps.** A road or path bridge over the line is tagged on the road, not the rails (example:
    the viaduct near MC 1901,-3299, `bridge=viaduct`, with many rails under it). The line runs under it at ground level.
    The evidence ticket (#379) returns these overhead spans; the planner (#381) keeps bushes and slope changes off the
    deck columns and fails if the profile would leave under 5 blocks of clearance. Deck heights come from the structure
    evidence (#450, [bridges-and-tunnels](bridges-and-tunnels.md)): a measured underside is the ceiling; a deck whose
    height is unknown, or only inferred while `railway_plan.INFERRED_DECK_USABLE` is false (the default), is an
    `overhead` gap with end records for a human, not a failure (#451). A usable height under 5 blocks still fails. The complex area at about
    50.91436,-1.45871 (MC 1901,-3299) is a yard of 17 sidings, 3 crossovers, 2 spurs and 20 switches with only 2
    through ways, so it suits the unfiled decor sidings ticket and a junction stress test, not trial D.
16. **Junction rule (#452, decided in the #384 grilling 2026-11-23).** Through lines that share rail columns in one
    feature: the main line (more tracks, then more rail columns, then lowest line id) keeps its rails; each side track
    leaves a `junction` gap over its overlapping columns and over any rail stub the overlap orphans, with end records
    and a `where` coordinate listed in the feature metadata for a person. Specified in
    [feature-builds](../../rules/feature-builds.md) ("Railway line features"). Shared columns of three lines go to the
    best ranked; the planner fails loudly only when an overlap covers a box join or a line is wholly overlapped.
14. **Not filed yet:** decor sidings, yards and spurs. **Study area chosen by the owner (2026-11-22):** the yard around
    50.91436,-1.45871 (MC 1901,-3299, cell 23,-32), which also has the viaduct over rail (decision 15). Probe extracts
    are in the world repo under `reference/vector_map/rail_probe_complex/`. Note it in D's ticket as the next step and file it once D has
    shown what a real line looks like.
17. **Planner box 512 by 512 (#464, owner decision 2026-11-23, replaces decision 3).** Trial D showed a 2048 box
    makes one feature span 42 cells and about 437,000 columns (apply read-back alone would take about 77 minutes),
    and needs ghost ground and evidence across all of them. `railway_evidence.PLANNER_BOX` is now 512, centred on
    the starting cell (the cell centre plus or minus 256, so 3 by 3 cells are fully inside and 5 by 5 are touched).
    The cut-and-join rule of decision 3 is unchanged, but it is now the normal path: a corridor like trial D (about
    690 by 510) needs five chained features. Rules, all in `railway_plan` and `railway_feature`:
    - **Live and spent joins.** A join is spent once its `next` column lies inside any landed box. Only live joins
      lead a new feature, so a chain grows from either end and a join that is still inside a later box is ignored.
      Two live joins of one track inside one box (a hole between two built features) are refused with both
      coordinates; so is a track that runs from an earlier box into new ground with no recorded join (a start cell
      whose box misses the join). Start from the cell of the live join instead.
      When the hole is shorter than the box, any start cell whose box includes one join also includes the other, so
      choosing another start cell does not resolve it; that hole is a job for a person.
    - **Re-entering tracks.** A track that leaves the box and re-enters it (a U or S bend across the edge), with or
      without an incoming join, is refused with the exit and re-entry coordinates: its second visit would lie inside
      this box and be planned by no feature. Choose a start cell whose box holds the whole bend.
    - **Proposals.** A cell on the edge of a landed box is only partly covered. It is proposed only when a through
      line has a block in the uncovered part of the cell.
    - **Gaps are found on the visit plus a 64-block margin** (`railway_plan.EDGE_MARGIN`). A crossing, bridge, tunnel
      or overhead gap wholly inside the box is recorded as before. One wholly outside belongs to the neighbouring
      feature. One that starts exactly at the cut is the next feature's: its first end record is the earlier
      feature's last rail (`from_join: true`, with that rail's cell, level and direction). One that straddles the
      box edge, or ends on the last inside column with no abutting rail, **fails loudly** with the edge coordinates.
      A crossing outside the box plus margin is another feature's and needs no road tags; the roads and points
      extracts must cover the box plus 64 blocks.
    - **Junctions.** A junction that begins on an incoming join is allowed (same first end record). A junction that
      covers any other cut fails loudly with coordinates. A junction gap touching or overlapping a structure gap
      becomes one `junction` stretch (`source` joins both ids, `also` lists the structure kinds) for a person to finish.
    - **Choosing another start cell.** A failure that names a box edge is cured by moving the box: `railway_build.py
      --cell TX,TZ` takes any cell whose box (centre plus or minus 256) holds the join's next column and the whole
      gap or junction; step the start cell by one or two cells away from the edge the message names. In the trial D
      replay from cell 54,-34, cell 56,-34 failed (a bridge straddled its south edge at 7461,-4033) and cell 56,-35
      worked.

## Trial D (#384): production line feature in the live world

Written 2026-11-24, half way through the trial: feature 1 of the chain is built and ridden; features 2 to 5 are not
built. Verdict first: **not good enough yet, one planner fix away.** The owner rode feature 1 and called the rail
"not bad", then "otherwise good". The only defects were four straight rails that should be corners, all at junction
joins (#480), and one of them is the first cell of the main line. Under the pass bar a main-line defect means "not
good enough yet". Nothing derailed or stalled in what the owner rode, and the bridge, crossing and junction gaps were
expected.

### What was built

Feature `railway_54_m34`, start cell 54,-34, planning box 512 by 512 (decision 17), applied 2026-11-24 with the
production builder through the journal. Five lines, nine tracks, 2,850 rails (powered rail on straight cells), 1,284
torches, about 100,826 planned positions of which 17,185 changed (the rest were already correct). Spec:
`worlds/southampton-build/site/builds/railway_54_m34.json`. Read-back checked all 100,826 positions: 0 mismatches
after one retry round of 37 positions; the journal's write positions equal the planned positions and no guard
positions were added. I did not separately scan for blocks changed outside the footprint.

Chain joins recorded for the next features: main line ends at cell 56,-34 going east at level 19 (two tracks);
branch ends at 54,-36 going north at level 16; the two spur lines start at 53,-32 going south at level 16. Cell
56,-34 cannot be a start cell because a bridge gap straddles its edge.

### Ride result

The owner rode a cart from the main-line start at 6817,-4078 and along the spurs. Per-track coordinates were not
recorded cell by cell; the owner reported only the items below. No derail or stall was reported on any ridden
stretch. The east end of the main line (x 7198 to 7231) and the plan 1 far end (6975 to 6986, -4524 to -4543) were
not confirmed ridden.

### Human-fix list (coordinates)

Defects (fixed by #480 plus a re-apply of feature 1, not by hand):

| Cell | Built as | Should be |
| --- | --- | --- |
| 6805,16,-4047 | east-west | north-west corner (plan 3 spur meets plan 4) |
| 6807,16,-4061 | north-south | north-west corner (plan 1 main line starts; plans 2 and 3 arrive) |
| 6817,16,-4078 | north-south | north-west corner (start of the main line; plan 4 arrives) |
| 6825,16,-4088 | north-south | north-west corner (plan 0 track 1 start; plan 2 arrives) |

Expected gaps for a person or a later feature:

| Where | Kind | Cells |
| --- | --- | --- |
| 6872,-4194 to 6873,-4195 and 6875,-4193 to 6876,-4194 (plan 0); 6857,-4199 to 6858,-4201 and 6860,-4198 to 6861,-4200 (plan 1) | footbridge (way 28019377), deck height unknown | 3, 3, 4, 4 |
| 6976,-4342 to 6983,-4345 and 6976,-4337 to 6982,-4340 | level crossing | 11, 10 |
| 6975,-4524 to 6977,-4529, 6979,-4525 to 6982,-4531, 6979,-4536 to 6982,-4542, 6983,-4537 to 6986,-4543 | overhead decks (ways 153798212, 746773187), height unknown | 8, 10, 10, 10 |
| 6817,-4078 to 6822,-4092 and 6826,-4103 (plan 1 track 1); 6796,-4034 to 6807,-4061 (plan 3 track 0); 6807,-4061, 6825,-4088, 6805,-4047 (single cells) | junction side tracks | 20, 1, 39, 1, 1, 1 |

The two mapped rail bridge spans (near 7285,-4290 and 7415,-4107) lie beyond feature 1's box and are not built.

### What it cost

| Item | Value | Method |
| --- | --- | --- |
| Models and efforts | main thread Sonnet 5.5, effort `high`; implementation and review subagents Sonnet | `get_session`; subagent briefs |
| Wall clock | about 19 hours session time (2026-11-23 15:48 to 2026-11-24 10:58 UTC), mostly waiting on world access, a usage limit pause and tickets | `get_session` timestamps; not active time |
| Session tokens | not measured; no billed total was read | `get_usage` was not called, so none is claimed |
| First apply, 2048 box (abandoned) | about 1 hour, stopped by the owner, nothing landed | apply log; cause: unordered batching made about 4,031 paced scans instead of 516 (#464) |
| Second apply, 512 box, 24 leaf mismatches | landed, then rolled back | readback report; cause: oak leaves planted without `persistent` (#469) |
| Third apply, 512 box, clean | about 4 minutes: readiness 22 s, surface warm-up 52 s, backup 11 s, journal begin 75 s, writes 13 s, flush 3 s, readback 51 s plus retry | result report `timings_seconds` |
| Ghost ground | applied in two runs (29 and 20 cells) for the corridor and its margin; 11 corridor cells had been missed | scratchpad logs; the ticket's claim that the corridor had ghost ground was wrong |

### What looked right and wrong

Right: the line follows the real alignment; corners inside runs and next to bridge and crossing gaps are correct (a scan
of all 2,850 rails against their path neighbours found only the four cells above); powered rail and torches landed
as planned; the cut-and-join chain works as designed across plans; the junction rule left side tracks as gaps with
end records.

Wrong or costly: the four junction corners; a read-back that treated leaf properties as mismatches; an apply
prefetch that scaled with scan count, not volume; the readiness gate refusing a railway for missing aerial
(#454); the lock wrapper losing the lock for the builder (#478).

A height comparison with the old 2048 plan found only 94 rails differing, all by +2 and all at the east end of the
main line (x 7198 to 7231, z -4349 to -4330, join level 19 against 17). The probable cause is the profile fit near
the clipped box edge; not confirmed. The owner should look at that stretch next ride.

### Process fixes this trial caused

Merged: #450 structure evidence, #451 deck gaps, #452 junction gaps, #453 apply stage name, #454 railway readiness
without aerial, #464 box 512 and ordered scan batching, #469 leaf state. Filed: #468 supported wait for a busy
suite, #478 live apply wrapper, #479 dry-run lists every readiness gap, #480 corner shape at junction join cells.
No world snags were filed: the defects are tool defects with tickets.

### Ready for decor sidings and yards?

**Not yet.** After #480 merges and feature 1 is re-applied, the main line has no known defect, and the answer
becomes yes for the first clean runs. The brief for the study area (yard around 50.91436,-1.45871, MC 1901,-3299,
cell 23,-32; 17 sidings, 3 crossovers, 2 spurs, 20 switches, a viaduct) should say: best-effort decor, clean unbroken
runs only, never tied into junctions, reuse the cell journal, readiness gate and the new apply wrapper (#478), and
keep the powered-rail rule. Features 2 to 5 of this trial are still to be built and ridden before the final
Trial D verdict.

## Ticket plan

Filed 2026-11-22 as #379 to #385 in `tasks/index.json`; the full text for each model is in its `notes`.

`builderTask` follows `docs/agents/model-and-effort.md`: true only for build design from evidence, a
representative build, or in-game judgement.

| Ticket | builderTask | Depends on | Scope in one line |
| --- | --- | --- | --- |
| #379 A. Railway evidence | false | none | Per-cell "has railway?" test; through line versus decor classification (decision 4); double-track pairing; points for level crossings (with barrier, light, bell tags), bridges, tunnels. Fixtures from the recorded GeoJSON and points. |
| #381 B. Line planner (pure function) | false | A | Snap, level profile, corridor, ballast, powered rail and torches, lidar planting (decisions 3 to 8); the planning box (2048 then, 512 since decision 17) with cut and join records; gaps at crossings, bridges and tunnels with end records (decision 10); fails loudly. Synthetic fixtures. |
| #382 C. Line feature integration | false | B | Journal ownership of the footprint, covered-cell marking and the instant skips (decision 2), structure backup, undo, read-back and retry, cell trees and decor skipping the footprint. `MemoryBlocks` tests. |
| #383 X. Crossing feature: spec and builder | false | C | Spec schema for a crossing (and the shared join records), the "not ready" gate, building the road surface, rails and gates/lights from a spec. Proposed addition: decisions 9 and 10 need this mechanism before Claude can design a crossing. |
| #384 D. Real-stretch trial | **true** | C | Build the trial corridor with the production builder and judge it in game (ride a cart, list spots for humans). Notes decor sidings as next step. |
| #385 Y. First crossing feature | **true** | D, X | Claude designs the crossing at about 6980,-4343 from the map tags and Street View, builds it, and judges it in game. |
| #380 G. Bridge and tunnel decisions | false | none | Owner conversation; output is glossary, rules and an ADR (see the scope note). |

Not filed: decor sidings (decision 14); the bridge and tunnel evidence, planner, integration and trial tickets
(decision 13).
