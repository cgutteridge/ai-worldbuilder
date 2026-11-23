# Pitched roof edges on noisy lidar: measurement (task #460, 2026-11-23)

Read-only measurement of West (`west_w34391673`) and David Kiddle (`w146985470`) on `main` after #434 merged.
No code, spec or world change. Scratch spec copies and the counting script live outside the repo.

## Commands

- `python3 scripts/feature_build.py --world southampton-build site/builds/west_w34391673.json` (dry run, no `--apply`), same for `site/builds/w146985470.json`.
- `python3 scripts/roof_lint.py --world southampton-build west_w34391673 w146985470`: "no ridge jogs, 0 stair-shape mismatches". Note it reads the **recorded** (live journal) plans, not a dry run.
- Counting: a scratch script calls `feature_build.Build(spec).build()` in process on a scratch spec copy and wraps `pitched_roof_states` to record its regularised heights, zone levels, flat cells and the columns `stair_ok` refused. Counts read the final plan cell on each roof column. `roof_lint.ridge_jogs` and `stair_shape_mismatches` were also run on each scenario's plan cells.
- Scenarios (scratch copies): **baseline** (spec as committed); **fit** (`zones.<label>.surface: "fit"`); **fit+slope** (`surface: "fit"` and `roofs.<label>.stairs: "slope"`). Neither spec has `regions`, so the fit works per zone level.

Fit lines from the dry run: West zone 47 fits a plane (rms 0.34 m, 865 of 1118 columns); zone 49 is skipped (no shape fits), raw lidar kept. Kiddle: zone 42 plane, 47 flat, 50 flat; zone 44 skipped, raw kept. So the fit is partial on both roofs.

## (a) Full-block cells with a lower orthogonal neighbour, by failing trim condition

"Rise" = the lower neighbour is one block or more lower; "half" = a half-block step (a full block beside a bottom slab, the intended slope pattern, not a defect).

| Group | West base | West fit | West fit+slope | Kiddle base | Kiddle fit | Kiddle fit+slope |
| --- | --- | --- | --- | --- | --- | --- |
| Higher same-zone neighbour, rise (mid-slope or ridge flank) | 88 | 60 | 60 | 19 | 19 | 19 |
| Higher same-zone neighbour, half step (intended) | 24 | 19 | 19 | 48 | 48 | 48 |
| Zone step only, no same-zone drop, rise | 49 | 55 | 55 | 12 | 43 | 43 |
| Zone step only, half | 2 | 0 | 0 | 0 | 0 | 0 |
| Higher neighbour in another zone, rise | 2 | 0 | 0 | 0 | 0 | 0 |
| Three or more drops | 7 | 6 | 6 | 4 | 0 | 0 |
| Opposing drops | 2 | 1 | 1 | 1 | 0 | 0 |
| No level backing (rise 2, half 3) | 5 | 1 | 1 | 1 | 0 | 0 |
| Drop larger than one block (same zone) | 0 | 0 | 0 | 0 | 0 | 0 |
| Flat cell (by design) | 0 | 0 | 0 | 70 | 39 | 39 |
| Stair removed by slope rule (step between volumes) | 0 | 0 | 6 | 0 | 0 | 8 |
| **Total** | 179 | 142 | 148 | 155 | 149 | 157 |
| **Total excluding half steps and flat cells (visible rises)** | 155 | 123 | 129 | 85 | 62 | 70 |

Reading: the trim conditions the task names (three or more, opposing, no backing) account for only 14 West and 6 Kiddle cells in the baseline. Most remaining full-block rises are cells that have a higher same-zone neighbour (the trim never fires next to a higher cell) or a zone step. Fit cuts West from 179 to 142 and Kiddle's trim failures to 0, but Kiddle's zone-step count grows (12 to 43) because the fit makes more layers flat. Examples, West: higher-neighbour (6166,-5631), (6166,-5630), (6167,-5632); three drops (6155,-5631), (6201,-5615); opposing (6178,-5632); no backing (6165,-5634). Kiddle: higher-neighbour (6359,-5531); three drops (6393,-5493); zone step (6397,-5503).

## (b) Stairs with no higher cell in the 8 neighbours, by rule that placed them

| Roof | Scenario | Stairs total | Without higher cell around | Rule |
| --- | --- | --- | --- | --- |
| West | baseline | 760 | 27 | terrace edge (#309): 27; ridge pair 0; hip eave 0 |
| West | fit | 627 | 20 | terrace edge 20 |
| West | fit+slope | 497 | 20 | terrace edge 20 |
| Kiddle | baseline | 231 | 1 | terrace edge 1 |
| Kiddle | fit | 160 | 0 | none |
| Kiddle | fit+slope | 84 | 0 | none |

All stairs are bottom half. These are the I-048 leftovers (the earlier read counted 16 on the 2026-11-21 plan; 27 is by a slightly wider test on the current plan). A stair with no higher neighbour cannot be a slope, hip or lidar-bump stair, so the terrace-edge rule (#309) is the candidate that was open in the task notes and is confirmed here: it is the only source. Example West cells: (6171,-5637), (6172,-5637), (6197,-5632), (6192,-5614), (6200,-5614), (6195,-5609); Kiddle (6390,-5479). Slope rule behaviour: West refuses 131 stairs (flat layer 66, step between volumes 58, no fitted slope 7) but keeps 456 with "no fitted surface" because zone 49 has no fit; Kiddle refuses 76 (step between volumes 67, flat layer 5, no fitted slope 4) and keeps 33 with no fitted surface. The slope rule does not touch terrace-edge stairs that sit on the rim or in unfitted zones.

## (c) Brick cells on a zone edge with no brick orthogonal neighbour (S-118)

| Roof | Scenario | Brick cells | On a zone edge | Edge and no brick neighbour | Any isolated brick |
| --- | --- | --- | --- | --- | --- |
| West | all three | 903 | 135 | 0 | 0 |
| Kiddle | all three | 235 | 6 | 0 | 0 |

The dry-run plan has no isolated bricks, so the ragged edge in S-118 is not isolated single bricks in the current plan. Weaker proxies (identical in all scenarios, since zone 49 and 44 stay raw): West 10 brick cells with at most one brick neighbour (e.g. (6160,-5639), (6162,-5626), (6170,-5622), (6174,-5635), (6185,-5628)) and 5 non-brick cells with three or more brick neighbours (holes: (6171,-5633), (6188,-5611), (6202,-5601), (6204,-5605), (6207,-5595)); Kiddle 6 and 0. The live roof was last built on 2026-11-22 (seq 1725 West, 1718 Kiddle), so S-118 may reflect a live edit, the older plan, or full-block steps on the edge (count (a), zone step only) rather than lone bricks; not verified in game. No picture is committed (coordinates above instead).

Lint on scenario plans: ridge jogs 0 everywhere; stair-shape mismatches 0 except West fit (1), which should be looked at if the fit is adopted.

## Recommendation

Surface fit alone helps modestly (West 179 to 142, Kiddle trim failures to 0, 27 to 20 flat-ish terrace stairs on West, 1 to 0 on Kiddle) and `stairs: "slope"` adds a further 131 (West) and 76 (Kiddle) stairs turned into blocks, but both zones that carry the ragged edge (West 49, Kiddle 44) do not fit, so neither switch fixes S-118 or the dominant "higher same-zone neighbour" rises. Recommend (ii) a fix task rather than (i) alone: (1) let the terrace trim fire beside a higher same-zone cell when the higher cell is a half-block step; (2) handle three or more drops and opposing drops as slabs or corner stairs; (3) trim with missing level backing as slabs; (4) fall back to the nearest-zone fit or `regions` for zones 49 and 44. Each needs a fixture through `Build` (lidar grid with that edge) and a before and after count from this report's script. Switching the fit on for West and Kiddle is a separate world step with the owner's OK first (West has no regions, so check strips).

Any change to `pitched_roof_states` also serves `house_build` (`house_build.py` lines about 772 and 778), so it needs a house dry run and the `house_build` tests. Relevant tasks: #462 (make the #434 rule the default, house roofs included) overlaps the stairs-on-slope question; #463 (rotated hip roofs) for rotated zones.

Check-line figures for the later rebuild: S-024 and S-069 (West) visible rises (a) at or below 155 (baseline), target at or below 123 with fit; S-068 (Kiddle) at or below 85; S-118 count (c) is already 0, so use the proxies (10 and 5); S-088 the 27 stairs of (b) are all terrace-edge (#309) stairs.
