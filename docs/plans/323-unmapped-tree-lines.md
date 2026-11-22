# Spike #323: tree lines along hedges and lanes that the map does not mark

Spike, 2026-11-21, world `southampton-build` (save `SouthamptonAI`), cell **87,-48**: the farmland cell of the #259 trial, described in [park-rural.md](park-rural.md).
Branch `task/323-unmapped-tree-lines`. Prototype: [`lib/layers/tree_lines.py`](../../lib/layers/tree_lines.py). The one-off trial command that drove the dry runs below has been removed; the counts are the record. `lib/layers/trees_layer.py` was not edited, because the paused woodland wave owns it (#349, #350).

## Verdict

**Go, with conditions.** Evidence along mapped hedges and lanes can place unmapped tree lines and belts. In the live build, the hedge got 6 crowns and the unmapped north-east belt got 21, with **0 on open pasture**. The trial ran the normal designer-then-builder workflow on the configured models (Opus 5.5 low designs, Sonnet 5.5 medium builds). **First-return standing height is the decisive evidence. Aerial darkness alone is unsafe**, because it reads the shadow of tall trees as canopy. Results: [Build trial](#build-trial).

Conditions for production:

1. **The seam is real.** The trees reader still uses last-return standing height. In this cell, the #317 source gate drops hand-read crowns that the designer adds through the ordinary `reference/trees-extra-crowns.json` path wherever they fall in a gated class (park, rural, natural). Here, 21 of 62 were dropped, all of them in the north-east belt inside `landuse=grass` or at the meadow edge. A designer cannot fix this from evidence alone. The gate needs a source for line and belt evidence, or first-return standing height, or both. [Seam test](#seam-test-last-return-reader).
2. **Wire first-return into the trees reader.** With first-return standing height in place of last-return, the gate kept every designer line crown and `lidar_fill` grew from 37 to 90 candidates. It still kept 0 crowns on pasture. The owner approved the SU41ne first-return tile during the trial; it is now in the world cache (one 7 s download, 71,199,322-byte TIF).
3. **Aerial darkness is a fallback only.** It is calibrated per cell: the threshold sits halfway between the mapped-wood and open-grass medians. But in a summer aerial, tall trees cast a dark shadow about 10 blocks wide on the grass north of them, and darkness reads it as canopy. v1 of the trial was built from darkness and placed about 20 crowns in that shadow band, and the designer did not catch it. The prototype now ignores darkness whenever first-return is cached.

## What is wrong today

| Finding | Evidence |
| --- | --- |
| The trees pass plants nothing on the mapped hedge | 48 hedge columns in the cell. Last-return standing height (DSM − DTM) has a median of **0.0 m**, and only 3 of 48 columns reach 2 m. The hedge sits on columns with no land-use polygon, so the gate does not apply there. Nothing **detects** the trees. |
| The aerial cover test is inverted in rural shade | Mean `cover_grid` score: open meadow **0.93**, dark north-east belt **0.16**, mapped west wood 0.42. The loose texture test passes bright pasture and fails shaded canopy. So the detector proposes crowns over the pasture, and the gate correctly drops them (149 dropped in the baseline dry run). The same test misses the belt. |
| Aerial luminance separates canopy from grass | Mean luminance of the cell mosaic: north-east belt 84, mapped wood 80, hedge 93, meadow 109, north meadow 134. |
| Last-return lidar misses canopy | 504 of 16,384 columns reach 2 m of standing height, although about half the cell is canopy in the aerial. This matches #320. |
| The north-east belt is in `landuse=grass` (park class) | The gate keeps a crown there only beside wood, scrub or forest polygons, or where last-return standing height is at least 2 m. Mostly neither holds, so the gate drops it (the red crosses in the baseline review image). |

Mapped land use in the cell: scrub to the north-west, `landuse=grass` to the north-east (this holds the belt), `natural=wood` in the west and along the lane east of x 11216, and meadow across the south and centre. The lane and its verges are a strip with no polygon.

## First-return versus last-return on 87,-48

Standing height (DSM − DTM) from the SU41ne 2022 composites, with arrays sampled by the existing `lidar_query`:

| Zone | First-return median, share ≥ 2 m | Last-return median, share ≥ 2 m |
| --- | --- | --- |
| Mapped hedge, 48 columns | 0.47 m, 14 columns | 0.0 m, 3 columns |
| NE belt over the lane's end and south verge (x 11214-11262, z -6100..-6085) | 13.75 m, 72% | 0.0 m, 13% |
| Dark band north of it (x 11210-11258, z -6132..-6114) | 0.0 m, 19% | 0.0 m, 0% |
| Mapped west wood | 15.45 m, 91% | 0.0 m, 8% |
| Open meadow (two windows) | 0.0 m, 0% | 0.0 m, 0% |
| Whole cell, columns ≥ 2 m | 4,104 | 436 |

The dark band is the belt's shadow: it lies directly north of the 13-20 m crowns and holds no first-return height. As `rules/foliage.md` warns, lidar crowns sit a few blocks south of the aerial crowns; here the offset looks like 3-6 blocks.

## The prototype

`tree_lines.line_evidence` uses only explicit arrays, and its unit tests use synthetic scenes:

- **Corridor:** columns within 4 blocks of a mapped `barrier=hedge` column or a lane's edge. Lanes are `highway=service|track|unclassified|tertiary|bridleway`. Footpaths are left out, because they cross pasture as often as they follow a boundary.
- **Canopy evidence:** first-return standing height of at least 2 m when its tiles are cached. Otherwise either last-return height of at least 2 m, or aerial luminance at or below the cell's calibrated threshold (here: wood median 85.7, open median 106.3, threshold 96.0; refused below 200 reference columns of each).
- **Belt:** canopy grown outward from corridor seeds through 4-connected canopy columns, up to 32 steps. It may pass through mapped-wood canopy, since a strip of wood separates the lane from this belt, but it places no crowns inside wood, scrub or forest polygons: those already pass the gate. It never enters a mapped carriageway (all `road_lines`, including service lanes that `OSMGround.road` leaves out), water, a building footprint, or a non-gated class.
- **Crowns:** greedy placement at least 5 blocks apart, best evidence first, in a deterministic order. The radius comes from standing height using the foliage guide (default r 3).
- **Admission (trial CLI):** after the #317 gate, hand-off crowns within 2 blocks of belt evidence are added as aerial-read crowns, so they get evidence-led rooting. A crown off the evidence is refused with a reason. So a hand-off cannot put trees on open pasture.

`propose` on 87,-48, corridor 1,707 columns:

| Version | Evidence | Belt columns | Crowns proposed (hedge / lane / belt) | Last-return gate keeps | First-return gate keeps |
| --- | --- | --- | --- | --- | --- |
| v1 | darkness plus last-return | 1,067 | 62 (7 / 26 / 29) | 18 | not measured |
| v2 | first-return | 850 | 53 (9 / 29 / 15) | 26 | 53 |

## Seam test: last-return reader

The seam was the trees layer's standing-height grid, which returned `mc_heights()['diff']`, last-return DSM minus DTM. Two dry runs, neither of which writes to the world:

| Path | Crowns from the hand-off | Dropped by the #317 gate | Trees planted in the cell |
| --- | --- | --- | --- |
| Baseline trees pass | none | 149 detector crowns (pasture, correctly) | 21 |
| Ordinary `trees-extra-crowns.json` path, unchanged code (trial seam subcommand) | 62 | 170, so **21 of the hand-off crowns** | 41 |
| Prototype admission after the gate (trial plan subcommand) | 62 offered, 48 admitted, 14 refused as duplicates of retained crowns | 149 | 64 |

Those three ran before any trial build, with the v1 hand-off. After v2 landed, `seam --reader last|first`, with and without the v2 hand-off, gave these counts. Planted counts are left out, because the trial trees now occupy the same places.

| Trees-pass reader | `lidar_fill` | `aerial_fill` | Gate drops (no hand-off) | Extra drops from the v2 hand-off | Retained crowns on pasture |
| --- | --- | --- | --- | --- | --- |
| Last-return (today) | 37 | 1 | 149 | 7 | 0 |
| First-return (`--reader first`) | 90 | 29 | 138 | **0** | **0** (97 pasture detector crowns still dropped) |

So, with today's reader:

- Verge and hedge crowns survive the ordinary path, because their columns are unclassified.
- North-east belt crowns are dropped.

`tree_lines.standing_height` is the reader a fix would use. It never downloads. It returns first-return height (`max(dsm_first - dtm, 0)`, not the provider's first-minus-last `canopy` layer) only when every tile is cached, and says which surface it read. `tree_lines.gated_crowns` applies the gate's own rule (≥ 2 m within 2 blocks) to either surface. `propose` reports both keep counts per crown.

## Build trial

Run through the ordinary roles from `python3 scripts/build_roles.py --world southampton-build show`: `tool=claude-code designer=claude-opus-5-5-low builder=claude-sonnet-5-5-medium (designer then builder)`. There were two sessions, the designer first and then the builder, as `rules/build-roles.md` requires when the models differ.

- **Designer:** given the ordinary trees inputs: the trees dry run and its review image, the cached cell aerial, lidar, the vector map, `rules/build-roles.md`, `rules/foliage.md` (tree review and radius guide) and `rules/evidence-order.md`. The prototype proposal was added as one more evidence sheet. It wrote an evidence table and a hand-off in the extra-crowns format. Aerial sheets were not downloaded, because download authorisation was not in scope; the cached registered cell mosaic was the aerial.
- **Builder:** refreshed validation (`prepare --apply`), dry-ran, then applied from the hand-off under the site lock. It did not redesign.

| | v1 (darkness plus last-return) | v2 (first-return) |
| --- | --- | --- |
| Designer | `claude-opus-5-5`, effort low: 71,639 tokens, 6 tool uses, 79 s | `claude-opus-5-5`, effort low: 73,042 tokens, 7 tool uses, 281 s |
| Designer result | kept 59 of 62 proposed crowns, dropped 3, added 0 | kept 50 of 53, dropped 3 (lane surface, 2 on open verge), radii shrunk where crowns overlapped |
| Builder | `claude-sonnet-5-5`, effort medium. First run: 65,392 tokens, 7 tool uses, 103 s; refused, because stage `other` cannot bypass readiness, so nothing was written. Resumed run: 69,139 tokens, 5 tool uses, 174 s | `claude-sonnet-5-5`, effort medium: 76,751 tokens, 20 tool uses, 159 s, including about 90 s waiting for #337's lock |
| Live result | seq 1684: 46 crowns, 6,239 blocks, 0 mismatches | v1 undone at seq 1688 (6,239 blocks); **seq 1690: 31 crowns, 5,557 blocks, 0 mismatches** |
| Zones (v2) | — | hedge 6 crowns, NE belt 21, west lane south verge 4, **open pasture 0**. 3 refused for overhanging a neighbour cell; 16 skipped (trunk or crown space blocked, mostly by entry 1292's trees) |

- **Token measurement:** the `subagent_tokens` value on each Agent task notification in Claude Code, recorded per run. The harness does not split input, output and cache, and it does not say whether a resumed run's figure is cumulative. Total for both iterations: designers 144,681 and builders 211,282 (resumed builder counted once at 69,139). The orchestrating worker session (Opus 5.5, high) is not in these totals: its context stood at 322k tokens at wrap-up, read with the app's `get_usage`.
- **Elapsed time:** from the first designer start (17:42:02 UTC) to v2 landing (17:59:59) took 17 min 57 s of wall clock, or about 16.5 min without the lock waits. Agent run time was 796 s in total.
- **Verdict:** go with conditions (above). Seen with a server-side top-down render (`block_render_region`): continuous canopy along the lane and hedge and over the NE belt, open grass on the pasture. The owner has not yet viewed it in game.

### Why an additive build, not a trees-pass re-apply

The live cell's trees are in an inconsistent state that predates this spike:

- Entry 1292, `highfield_87_m48_trees` (17,915 blocks, trial259), is still standing. A block scan finds 17,029 oak leaves and 959 oak logs in the cell.
- Entry 1650 re-applied `highfield_87_m48_trees` on 2026-11-21 as `claude-318` (3 crowns, 556 blocks) and replaced the trees mask. The mask now holds only those 556 blocks.

So `mask_undo --pass trees` would remove 556 blocks and leave entry 1292's trees in place. A trees re-apply would replace the mask again and could not restore entry 1650 exactly. The trial therefore applied **only the admitted line crowns**, planned by `trees_layer.plan_cell` with its ordinary rules. They went in as their own journal entry, `trial323_tree_lines_87_m48` (stage `other`), which:

- leaves the existing trees and decor entries and masks alone;
- does not change layer progress or stale any other layer;
- can be removed exactly by plan name.

Crowns whose leaves would cross the cell edge were refused, so only 87,-48 changes. Frontier ghosting was off for the live applies (`LIDAR_GHOST_RING=0`), and the journal follow-ups did nothing. Stage `other` was tried first and refused by the journal, because it is reserved for clean-ups and markers. The trees pass under its own plan name, with no pass mask, is the legitimate route. A side effect: `element_undo --cell 87,-48 --pass trees` goes through the trees mask and does **not** include this build. Undo it by plan name.

## World changes and undo

Net change: one live journal entry, **seq 1690** (`trial323_tree_lines_87_m48`, trees pass, cell 87,-48, 5,557 blocks of oak log and persistent oak leaves). Entry 1684 (v1) was applied and then undone by seq 1688. The registry validation of 87,-48 was refreshed with `prepare --apply` (registry only). One file was downloaded with the owner's approval: `downloads/defra-lidar/DSM_FIRST/SU41ne_FZ_DSM_1m.tif`. Running `element_undo` also moved the player briefly and put them back.

Undo the trial:

```sh
python3 scripts/element_undo.py --world southampton-build trial323_tree_lines_87_m48            # dry run: 1 plan, 5557 blocks
LIDAR_GHOST_RING=0 python3 scripts/site_lock.py --world southampton-build run <owner> "undo trial323" -- \
  env LIDAR_GHOST_RING=0 python3 scripts/element_undo.py --world southampton-build trial323_tree_lines_87_m48 --apply
```

The cached first-return tile can stay. It is an optional provider product (#320), and nothing requires it.

## Recommendation

1. **Wire first-return standing height into the trees reader** as a follow-up after #349 and #350 land. It must handle optional first-return, missing samples, and providers without it (#320). On 87,-48 this alone made the gate keep every line crown and raised `lidar_fill` from 37 to 90, with 0 crowns on pasture. `tree_lines.standing_height` is a working reader to start from.
2. **Then add a line/belt tree source to the gate**, keyed by land-use class (`trees.sources` gains `lines`; config, not code). Use `tree_lines.line_evidence` as the reader, and admit crowns after the gate as this prototype does. It still matters where first-return is missing, old or leaf-off, and for hedge trees on unclassified verges that nothing detects.
3. **Fix the inverted cover test on rural classes:** use calibrated darkness, or leave aerial fill off in gated classes. This is separate from tree lines, but it is why the detector plants pasture and misses belts.
4. **Repair the 87,-48 trees mask drift** (entry 1292's blocks are outside the trees mask) before the next trees redo there.

## Reproduce

The one-off trial command has been removed. Its subcommands were propose, seam, plan and lines-plan on cell 87,-48. The counts above are the record.

Trial artefacts are under the world's `verification/site/trial323_87_m48_tree_lines-*`: the proposal JSON and PNG, the designer's hand-off and evidence table, and the admission and lines records.
