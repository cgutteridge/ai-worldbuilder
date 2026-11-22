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

| Track | Source ways (OSM) | Path cells | Rail level |
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

1. **Lines.** The `railway` preset already exists in `shims/osm_extract.py` but is not in `CELL_EXTRACT_LAYERS`.
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
`railway` is not in `CELL_EXTRACT_LAYERS`. A later real ground, trees or decor pass over these cells was free to
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
3. **Size limit: a 2048 by 2048 box around the starting cell.** It is a rough limiting rule, so keep the maths
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
   exist only if the OSM tags say so (`crossing:barrier`, `crossing:light`, `crossing:bell`); their style and
   colours come from Street View as part of that crossing's build. There is no separate style survey and no
   "unstyled" default. A crossing with no such tags is a plain crossing.
10. **Order at a crossing, bridge or tunnel:** the line is planned straight through the span, builds nothing in the
    gap, and records level, direction and tracks at each end of the gap. The crossing, bridge or tunnel is built
    afterwards, reads those records, and refuses ("not ready") until both abutting line features exist.
11. **Trial D reuses the trial corridor.** It already has one OSM level crossing (a node on each of its two tracks, about 4 blocks apart, near
    6980,-4343, cell 54,-34: `crossing:barrier=double_half`, `crossing:light=yes`, `crossing:bell=yes`, `crossing:activation=remote`,
    description "Manually Controlled Barriers monitored with Closed-Circuit Television (MCB-CCTV)"), 19 rail
    switches, and two mapped rail bridges. A hillier stretch can be a later ticket.
12. **Trial corridor removed.** The 14 backup tiles were loaded back and checked; the corridor cells keep ghost ground.
13. **Bridges and tunnels are general work** ([bridges-and-tunnels](bridges-and-tunnels.md)). File one decisions
    ticket now; write the evidence, planner and integration tickets after it closes.
15. **Bridges over rail are not gaps.** A road or path bridge over the line is tagged on the road, not the rails (example:
    the viaduct near MC 1901,-3299, `bridge=viaduct`, with many rails under it). The line runs under it at ground level.
    The evidence ticket (#379) returns these overhead spans; the planner (#381) keeps bushes and slope changes off the
    deck columns and fails if the profile would leave under 5 blocks of clearance. The complex area at about
    50.91436,-1.45871 (MC 1901,-3299) is a yard of 17 sidings, 3 crossovers, 2 spurs and 20 switches with only 2
    through ways, so it suits the unfiled decor sidings ticket and a junction stress test, not trial D.
14. **Not filed yet:** decor sidings, yards and spurs. Note it in D's ticket as the next step and file it once D has
    shown what a real line looks like.

## Ticket plan

Filed 2026-11-22 as #379 to #385 in `tasks/index.json`; the full text for each model is in its `notes`.

`requiresClaude` follows `docs/agents/model-and-effort.md`: true only for build design from evidence, a
representative build, or in-game judgement.

| Ticket | requiresClaude | Depends on | Scope in one line |
| --- | --- | --- | --- |
| #379 A. Railway evidence | false | none | Per-cell "has railway?" test; through line versus decor classification (decision 4); double-track pairing; points for level crossings (with barrier, light, bell tags), bridges, tunnels. Fixtures from the recorded GeoJSON and points. |
| #381 B. Line planner (pure function) | false | A | Snap, level profile, corridor, ballast, powered rail and torches, lidar planting (decisions 3 to 8); the 2048 box with cut and join records; gaps at crossings, bridges and tunnels with end records (decision 10); fails loudly. Synthetic fixtures. |
| #382 C. Line feature integration | false | B | Journal ownership of the footprint, covered-cell marking and the instant skips (decision 2), structure backup, undo, read-back and retry, cell trees and decor skipping the footprint. `MemoryBlocks` tests. |
| #383 X. Crossing feature: spec and builder | false | C | Spec schema for a crossing (and the shared join records), the "not ready" gate, building the road surface, rails and gates/lights from a spec. Proposed addition: decisions 9 and 10 need this mechanism before Claude can design a crossing. |
| #384 D. Real-stretch trial | **true** | C | Build the trial corridor with the production builder and judge it in game (ride a cart, list spots for humans). Notes decor sidings as next step. |
| #385 Y. First crossing feature | **true** | D, X | Claude designs the crossing at about 6980,-4343 from the OSM tags and Street View, builds it, and judges it in game. |
| #380 G. Bridge and tunnel decisions | false | none | Owner conversation; output is glossary, rules and an ADR (see the scope note). |

Not filed: decor sidings (decision 14); the bridge and tunnel evidence, planner, integration and trial tickets
(decision 13).
