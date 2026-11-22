# #244 measurement: front boundaries in 53,-43 after the gating tasks (2026-11-16)

Read-only measurement, no code, world data or live blocks changed. Planner: `decor_layer.py --world southampton-build --cell 53,-43` (dry run) plus a re-plan with the cell's own applied decor mask treated as undone (see "Measuring caveat"). Compared with the 2026-11-09 baseline in the #244 notes. Houses and conventions: [244-boundary-evidence-findings.md](244-boundary-evidence-findings.md).

## Result in one table

| | 2026-11-09 baseline | now (clean re-plan) |
|---|---|---|
| eligible fronts | 59 (31 Kitchener, 28 Sirdar) | 60 (32 Kitchener, 28 Sirdar; one more Kitchener house than the baseline count, cause not traced) |
| fronts with a street-facing boundary | 12 (aerial hedge fragments only) | 39: Kitchener 24 (12 timber fence, 6 brick wall, 6 hedge), Sirdar 15 (hedge) |
| fronts read open (`front: none`, no boundary) | 0 (empty on all 59) | 21: Kitchener 7, Sirdar 13, plus Kitchener 37 whose front line lies in cell 53,-42 |
| front-boundary columns placed | 186 hedge columns (all aerial fragments) | 304: Kitchener 178 (90 fence, 50 wall, 38 hedge), Sirdar 126 (hedge) |
| walls / fences / gates | 0 / 0 / 0 | 6 houses with a wall (50 columns) / 12 with a fence (90 columns) / 0 gates |
| fronts with an entrance opening | route columns only; aerial hedges had no gap | 40 of 60 (every one of the 39 fronts with a boundary; 1 to 4 columns, evidence `door_route` 20, `side_passage` 20) |
| decor blocks for the cell (all roles) | 152 items (150 hedge, 2 iron bars) | 2,716 (hedge, wall, fence, lamps 7; rear and side-back hedges included) |

Placement losses on front boundaries: 6 columns of 310 planned (2 hedge blocked by existing leaves, 2 against a build, 1 fence against a build, 1 on carriageway). Nothing lost to the layer drops the diagnosis feared.

Each open front matches the research decisions exactly: Kitchener 42, 44, 45, 50, 52, 56, 72 and Sirdar 46, 48, 50, 60, 64, 66, 68, 71, 73, 75, 77, 87, 89. Walls sit on 46, 48, 62, 64, 66, 68; hedges on 41, 43, 54, 63 (aerial) and also 39, 51, where the aerial hedge reading beats the fence default. Every other Kitchener front takes the timber fence; every Sirdar front without `none` takes the hedge.

## Measuring caveat

A plain dry run on a cell that is already built reads the cell's own decor mask as other-pass blocks, so hedges (whose blocks match what is live) all fail as "blocked by live or owned block": 166 front hedge columns and 305 blocks in total instead of 2,716. A real rebuild undoes first, so it does not hit this. Numbers above come from re-planning with that mask treated as undone and the cell's own blocks read as air; the plain dry run is kept as a misleading baseline of a tool artefact. A follow-up should make the decor dry run ignore the cell's own mask (or say so).

## What remains

- **Gates**: still none (no gate block kind in `decor_plan`; every opening is left as air). The evidence rule (nothing scattered by default) and the research (one iron gate seen on Street View, house unknown, no gate evidence per house) leave no basis for a gate on any of the 39 openings. Needs an owner decision plus per-house evidence, not code.
- **Narrow fronts**: on Kitchener 46, 47, 55, 58, 65 and Sirdar 56 the opening (3 or 4 side-passage columns) leaves a 1 to 3 column boundary. Plausible for 5 to 6 block plots; worth one look in-world.
- **Cell edges**: Kitchener 37, 39, 43, 65 lie partly in the neighbouring cells; their boundaries appear when those cells build.
- **In-world look** at both roads after the rebuild (the owner's gate) stays open. This measurement is against the plan and the registered aerial decisions, not an overlay of the build on the aerial.

## Per-front table (clean re-plan)

### Kitchener Road

| house | front boundary | planned cols | placed cols | opening cols (evidence) | note |
|---|---|---|---|---|---|
| 37 | - | 0 | 0 | 1 (door_route) | front line lies in the neighbouring cell |
| 39 | hedge | 3 | 2 | 1 (door_route) |  |
| 41 | hedge | 17 | 16 | 1 (side_passage) |  |
| 42 | none (open) | 0 | 0 | 0 (-) | open forecourt (front: none) |
| 43 | hedge | 4 | 3 | 1 (door_route) |  |
| 44 | none (open) | 0 | 0 | 0 (-) | open forecourt (front: none) |
| 45 | none (open) | 0 | 0 | 0 (-) | open forecourt (front: none) |
| 46 | brick_wall | 1 | 1 | 4 (side_passage) | narrow: opening takes most of the front line |
| 47 | plank_fence | 2 | 2 | 4 (side_passage) | narrow: opening takes most of the front line |
| 48 | brick_wall | 12 | 12 | 4 (side_passage) |  |
| 49 | plank_fence | 12 | 12 | 4 (side_passage) |  |
| 50 | none (open) | 0 | 0 | 0 (-) | open forecourt (front: none) |
| 51 | hedge | 6 | 6 | 1 (door_route) |  |
| 52 | none (open) | 0 | 0 | 0 (-) | open forecourt (front: none) |
| 53 | plank_fence | 14 | 14 | 1 (door_route) |  |
| 54 | hedge | 4 | 4 | 4 (side_passage) |  |
| 55 | plank_fence | 3 | 3 | 3 (side_passage) |  |
| 56 | none (open) | 0 | 0 | 0 (-) | open forecourt (front: none) |
| 57 | plank_fence | 14 | 14 | 1 (door_route) |  |
| 58 | plank_fence | 3 | 3 | 4 (side_passage) |  |
| 59 | plank_fence | 5 | 5 | 1 (door_route) |  |
| 60 | plank_fence | 8 | 8 | 4 (side_passage) |  |
| 61 | plank_fence | 17 | 17 | 1 (door_route) |  |
| 62 | brick_wall | 10 | 10 | 1 (door_route) |  |
| 63 | hedge | 7 | 7 | 1 (door_route) |  |
| 64 | brick_wall | 9 | 9 | 4 (side_passage) |  |
| 65 | plank_fence | 1 | 1 | 2 (side_passage) | narrow: opening takes most of the front line |
| 66 | brick_wall | 8 | 8 | 1 (door_route) |  |
| 68 | brick_wall | 10 | 10 | 4 (side_passage) |  |
| 70 | plank_fence | 6 | 6 | 1 (door_route) |  |
| 72 | none (open) | 0 | 0 | 0 (-) | open forecourt (front: none) |
| 74 | plank_fence | 6 | 5 | 1 (door_route) |  |

### Sirdar Road

| house | front boundary | planned cols | placed cols | opening cols (evidence) | note |
|---|---|---|---|---|---|
| 46 | none (open) | 0 | 0 | 0 (-) | open forecourt (front: none) |
| 48 | none (open) | 0 | 0 | 0 (-) | open forecourt (front: none) |
| 50 | none (open) | 0 | 0 | 0 (-) | open forecourt (front: none) |
| 52 | hedge | 5 | 5 | 4 (side_passage) |  |
| 54 | hedge | 15 | 15 | 1 (door_route) |  |
| 56 | hedge | 1 | 1 | 3 (side_passage) | narrow: opening takes most of the front line |
| 58 | hedge | 14 | 14 | 1 (door_route) |  |
| 59 | hedge | 5 | 4 | 1 (door_route) |  |
| 60 | none (open) | 0 | 0 | 0 (-) | open forecourt (front: none) |
| 61 | hedge | 6 | 6 | 4 (side_passage) |  |
| 62 | hedge | 9 | 9 | 4 (side_passage) |  |
| 63 | hedge | 11 | 11 | 3 (side_passage) |  |
| 64 | none (open) | 0 | 0 | 0 (-) | open forecourt (front: none) |
| 65 | hedge | 6 | 6 | 1 (door_route) |  |
| 66 | none (open) | 0 | 0 | 0 (-) | open forecourt (front: none) |
| 67 | hedge | 14 | 14 | 1 (side_passage) |  |
| 68 | none (open) | 0 | 0 | 0 (-) | open forecourt (front: none) |
| 69 | hedge | 5 | 5 | 2 (side_passage) |  |
| 71 | none (open) | 0 | 0 | 0 (-) | open forecourt (front: none) |
| 73 | none (open) | 0 | 0 | 0 (-) | open forecourt (front: none) |
| 75 | none (open) | 0 | 0 | 0 (-) | open forecourt (front: none) |
| 77 | none (open) | 0 | 0 | 0 (-) | open forecourt (front: none) |
| 79 | hedge | 10 | 10 | 4 (side_passage) |  |
| 81 | hedge | 6 | 5 | 1 (door_route) |  |
| 83 | hedge | 14 | 14 | 1 (door_route) |  |
| 85 | hedge | 7 | 7 | 1 (door_route) |  |
| 87 | none (open) | 0 | 0 | 0 (-) | open forecourt (front: none) |
| 89 | none (open) | 0 | 0 | 0 (-) | open forecourt (front: none) |
