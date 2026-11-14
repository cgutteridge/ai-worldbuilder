# #244 research: front boundary evidence for Kitchener Road and Sirdar Road (cell 53,-43)

Research only, 2026-11-12. No code, world data, or rebuild changed. Draft data: `docs/plans/244-draft/kitchener_road.json` and `sirdar_road.json` (not under `worlds/`; both load through `garden_sources.role_spec`).

## Evidence used

| id | what | limits |
|---|---|---|
| `aerial-kitchener-z19-mosaic-z19-5x5-centre-260122-175674-png` | registered z19 mosaic, ~5.3 px per block, viewed at 14-30 px per block | a pale line is a wall, a close-boarded fence or a rendered edge: material cannot be told apart from above; a thin timber fence can vanish; roofs lean 1-3 blocks |
| `registry/issues/img/S-073-reference-kitchener-road.png` (owner screenshot, 67 Kitchener Rd, Aug 2025) | one ground-level frame at the west end (near Portswood Rec), looking east | **not in the manifest, so it has no evidence id yet** (task below). One frame: roughly 8 fronts, west end only. No Sirdar Road view exists. |
| Vector-map footprints with `addr:*` and the `kitchener_road.json` vetoes | numbering and geometry only | the vector-map road centreline sits about 3 blocks south of the painted centre line here; footprint outlines are fine |

Nothing was downloaded. The two `reference/photos/streetview/` sets belong to cells 48,-43 and 53,-42, not these roads.

## Finding 1: the diagnosis needs correcting on material

The 2026-11-09 diagnosis said "low pale wall on the pavement line". The Street View frame shows something else at the west end: **weathered close-boarded timber fence, about 1 to 1.2 m**, gravel and planting behind, one timber picket run and one wrought-iron gate with a hedge, bins at the gap. Pale lines on the aerial can be that same weathered timber. Only 68, 66, 64 and 62 (aerial close-up) read as a low brick/rendered wall with piers and shrubs behind it. So the run default for Kitchener is a one-block timber fence, with brick wall and hedge as per-house exceptions. Fence default height in code is 2; the draft sets `height: 1` explicitly (a one-block fence reads about 1 m).

## Finding 2: eligibility is a data problem

In the saved decor report (`highfield_53_m43_decor-report.json`, `stats.decor_plan.frontages`) Kitchener has 31 eligible and 22 ineligible frontages and Sirdar 28 and 25, all ineligible ones "no street-run frontage evidence". The eligible 59 are the houses that sit in the cell; the 47 ineligible ones are mostly in neighbouring cells (Kitchener 15-38, 67, 76; Sirdar 32-57 evens/odds, 70-82). A street convention with a `front` makes them eligible, which also matters for the neighbouring cells' builds.

## Decision per street run

Run = one side of one street inside the cell. "Open" = no boundary line visible and vehicles parked on the forecourt.

### Kitchener Road (31 eligible fronts)

| run | decision | evidence | confidence |
|---|---|---|---|
| default, both sides | `fence`, height 1, spruce fence (timber); side dividers `side_front` the same | Street View frame, labelled inference beyond the west end | low to medium |
| north side 68, 66, 64, 62 | `brick_wall` height 1 | aerial close-up: low wall at pavement line with shrubs behind; 64 also has a drive gap beside it | medium |
| north side 48, 46 | `brick_wall` height 1 | aerial: pale low line at pavement, lawn behind | low |
| 41, 43, 54, 63 | `hedge` height 1 | aerial: dark shrub/hedge strip at front | low |
| **open: 42, 44, 45, 50, 52, 56, 72** | `front: none` | aerial: cars on paved/gravel forecourt, no boundary line (44 and 42 have the widest paved forecourt) | low |
| all others (39, 47, 49, 51, 53, 55, 57, 58, 59, 60, 61, 65, 70, 74) | run default | no per-house reading possible | inference |

Counts: 7 open (23%), 6 wall, 4 hedge, 14 default fence.

### Sirdar Road (28 eligible fronts), aerial only

There is no street-level view of Sirdar Road, so everything here is aerial-led inference. Sirdar reads **more open than Kitchener**: wide paved or gravel forecourts used for parking, fewer visible boundaries.

| run | decision | evidence | confidence |
|---|---|---|---|
| default | `hedge`, height 1; `side_front` hedge | aerial: where a boundary shows at all it reads as a dark hedge or shrub strip (54, 56, 61, 63, 65, 69, 79, 81); no clear walls | low |
| **open: 46, 48, 50, 60, 64, 66, 68, 71, 73, 75, 77, 87, 89** | `front: none` | aerial: vehicles on forecourt, wide paved or sandy forecourts (46, 48, 50), no line | low |
| the rest (52, 58, 59, 62, 67, 83, 85) | run default | undecided | inference |

Counts: 13 open (46%), 15 default hedge. Not decidable from above: fence versus hedge on 52, 58, 62, 83, 85, whether open forecourts have a low wall under parked cars, and whether any gate exists.

### Material fallback for #255 (dominant material per run)

- Kitchener: timber fence 1 m dominant (Street View, 7 of 8 visible fronts at the west end), exceptions brick wall (68, 66, 64, 62, 48, 46) and hedge (41, 43, 54, 63); one wrought-iron gate seen with a hedge at the west end.
- Sirdar: hedge dominant where a boundary shows (aerial), no walls identified; 46% open.
- Implication for the profile: the dominant material is a property of the street run, not the district, in this study area. The #255 profile should hold the dominant per street run (or per land-use class and block of frontage) with per-house overrides, which is what the draft files express. The test in #255 (dark-hedge district, per-house light fence) still holds.

## Pavement and forecourt (for #254)

Eyeballed from 14 and 30 px per block crops, about plus or minus 1 block. Not a systematic measurement (see task for a tool).

| quantity | Kitchener | Sirdar |
|---|---|---|
| public pavement, kerb to front boundary | 2 to 2.5 blocks (about 1.5 to 2 on the south side near 41-45) | about 2 to 2.5 (kerb is clear in the north-side views, 1.5 on the south at 65/63) |
| forecourt depth, boundary to house front wall | 3 to 5 on the north side (62-68), 5 to 9 on the south side east end (37-45) | about 3 to 6, deeper at 46-50 (6 to 8) |
| carriageway kerb to kerb | about 11 blocks (the aerial includes parked cars and double markings) | about 11 |
| centreline to house front (vector map, perpendicular) | 9.4 to 14.1, median 11.3 | 9.7 to 12.1, median 11.0 |

- `garden_sources.PAVEMENT = 3.0` blocks of footway between kerb and boundary is about 0.5 to 1 block too wide for both streets, so the front boundary lands slightly into the forecourt. Make it a per-class value in the class table (suburban residential 2.5).
- Dominant forecourt surface: Kitchener fronts are gravel and paved with shrub planting; lawn is rare in front (a few at 46, 48, 63). Sirdar fronts are mostly paved, gravel or sand with cars; lawn is rare (about 67, 69, 81). That supports ground treating the forecourt as paving, gravel and planting beds rather than lawn, and it is why the guessed-lawn rule converts so little (237 columns).
- Pavement strip surface is pale grey; forecourt surfaces are warmer or darker (gravel, brick paviour). Distinct blocks per surface are reasonable.

## Not measured or not decided

- No systematic per-frontage measurement of pavement or forecourt (eyeballed on 8 crops).
- Houses 33-38, 15-31 (Kitchener) and Sirdar 32-57 evens, 70-82 lie in other cells; not assessed.
- Whether open forecourts truly have no boundary (a car may stand behind a low wall with a wide gate).
- Brick versus render versus timber wherever the aerial shows a pale line.
- Gates: one iron gate seen on Street View; none placed in the draft (no gate evidence per house, and gates are not built today).
- Sirdar has no street-level reference at all. Ask the owner for two more screenshots (east end of Kitchener, e.g. near 52, and Sirdar near 87 or 60) before the Sirdar defaults are trusted.
- Existing vetoes on Kitchener 33-51, 55 and 59 set `side_front: none` as well as `rear` and `side_back`. The note says the aerial showed hedges running into Portswood Rec at the rear, so the `side_front` veto looks too broad; with a convention it would leave 47, 49, 51, 55, 59 without front side dividers. The draft keeps them. Decision for the data task: remove `side_front: none` from those overrides unless the owner knows otherwise.

## Decision: side dividers at the front are open by default (2026-11-14)

The convention files now set `side_front` to `{"type": "none"}` by default (world commit 209c457), superseding the line above that kept front side dividers. After in-world review of 53,-43 (rebuilt frontage), front side dividers read as sideways stubs off the front line; the street-facing frontage is the evidenced boundary. A divider appears only where a house number carries its own `side_front` override or a `side_passage` opening applies (#275). The AI-set `side_front` vetoes (task #267) are therefore moot, not the problem; remove nothing further.

## Draft data

See the two JSON files in `docs/plans/244-draft/`. They carry `evidence_ids`, a run-level `provenance`, and per-house provenance inside each `front` spec (the code reads provenance from the role spec). `SV` id `photos-streetview-kitchener-road-owner-s073-67-kitchener-rd-2025-08-png` is a proposed id: it must be registered before the data task lands.

## Owner review, 2026-11-12

Kitchener Road: timber fence is the confirmed modal front boundary; low hedges and low brick walls occur on some properties. This upgrades the Kitchener default from labelled inference to owner-confirmed local knowledge (record as owner observation, not an evidence id). Sirdar Road: owner checked in-world and confirms it has more open forecourts than Kitchener and accepts the draft default (hedge where a boundary shows, 13 open houses) as an OK default. Per-house reads on both streets stay low-confidence.

## Owner decision, 2026-11-12: fence panel material

Modal Kitchener Road boundary is close-boarded timber fence panels (S-073 frame), about 1 m high. Panels are plank blocks, default `oak_planks`; slatted/picket runs use the normal fence block. Tone variants where the street shows them (dark, reddy, pale). The weathered grey of the S-073 fences has no exact Minecraft plank, so the nearest tone is chosen per run.

Plank tone reference (Minecraft Java, from memory; confirm in-world before relying on it):

| tone | plank block | note |
|---|---|---|
| default / mid warm brown | `oak_planks` | owner default |
| dark | `spruce_planks`, `dark_oak_planks` | spruce is brown, dark oak near black-brown |
| reddy | `jungle_planks`, `acacia_planks`, `mangrove_planks` | jungle pinkish tan, acacia orange, mangrove deep red |
| pale | `birch_planks` | cream |
| white / painted | `pale_oak_planks` | near white, much lighter than birch; only for painted or white-looking fences (owner, 2026-11-13) |

Open checks: whether `decor_plan` places a plank barrier (barrier kinds and `JOINABLE` list only name brick_wall, iron_bars, spruce_fence), and whether the draft's Kitchener default should change from `fence` to a plank panel. Draft data not edited yet.

### Owner decision, 2026-11-12: plank tone

Keep oak as the default panel tone; dark, reddy and pale planks only where evidence shows a distinct tone.

### Shelf panels (#272)

Plank-style fence columns are placed as wooden shelves, `<wood>_shelf[facing=<dir>]` (wood from the tone table; the server confirms all 12 shelf woods are in `minecraft:wooden_shelves`). Facing is the outward side: the unowned neighbour (street, pavement, path) wherever one exists, else the neighbouring plot, facing away from the plot that owns the fence (labelled `fallback: private-private`). A column with no outward evidence stays a solid plank (labelled). Whether a shelf reads well from the street is still the owner's in-world review.

Shelf geometry (client asset `template_shelf_body`, north-facing; others are rotations): back board 3/16 thick flush with the block edge opposite the facing, plus 2/16 lips at the top and bottom, 5/16 deep in all. `facing=<out>` therefore puts the board on the inner (garden) edge of the boundary column with the open side towards the street. Boundary columns are the plot's outermost own columns, so the board stands up to about one block inside the true line; shifting the run one column outward would land it on the line but puts it on pavement / neighbour land (road and owned-column vetoes, #270 drive-gap edges), so it is not done yet. Fence kinds: `type: fence` with a plank block (`<wood>_planks`) is a shelf panel run (`plank_fence`); `type: fence` with no block, or a fence block (`spruce_fence`, `oak_fence`, ...), stays the normal fence kind. Only the first uses shelves.

### Thin closers at diagonal steps (#274)

#273 made each panel run face one way but tied the stepped board lines together with solid plank cubes, which read as chunky crates beside the shelves. The closers are now **cross shelves**: a shelf in the step column turned onto the perpendicular axis, same wood, so nothing solid remains in a run that has outward evidence.

Reasoning from the shelf geometry. A run facing north has its back boards (3/16) on the south edge of every column. On a staircase the boards of two neighbouring rows sit a block apart in z, so a closer must carry a board across that gap. A shelf facing east or west has its board on the west or east edge of the column and running the full 16/16 in z, which is exactly a riser. Take run columns q=(1,0) and p=(2,1) touching at a corner, closer c=(1,1) (the inner candidate, the one further from the facing): a west-facing shelf puts its board on c's east edge. That board meets p's board end at x=2 and q's board along its underside at z=1, so the line is continuous. The open front of the cross shelf looks along the run's inner side (into the garden), the same side as the run's lips, so from the street only the thin board is seen.

Side choice: the cross shelf's board goes on the edge that touches the next column of the run, so it faces away from that neighbour; neighbours that are ordinary shelves are preferred to other closers, ties go east, west, south, north (`_closer_facing`). The same conversion replaces the old solid plank at a face-to-face step along the facing axis (the column opposite the facing).

Why not the thin fence block: a fence post sits in the middle of the column (6/16 to 10/16) and does not touch the shelf boards at the column edges, so it would leave a visible gap; the cross shelf closes the step with board contact on both sides, so the fence is not used. Residual case: when the inner candidate is blocked (road, other-owned) the other candidate is used; its cross board then meets only one side, leaving up to 13/16 of a block open at the other. Never added on road, pavement, path or other-owned columns, and closers go through the same opening clearance as other panel columns, so entrance openings (#270) stay open.

Mixed facings near x 6799-6803, z -5418..-5416 on 53,-43 were not a grouping fault: the side-front run of plot 688 there faces east throughout; the north-facing pieces are its cross closers. The run facing is constant; only the closers differ, by design.

### Straight panel runs, one closer per step (#276)

After #274 the oak runs still looked like a saw blade from above: the vector-map boundary is pixel-jagged, so `straighten` leaves a one-row step every couple of columns, and each step took two closers. Two changes, panel runs only (brick, hedge, iron and picket runs are untouched):

1. One closer per step. `panel_runs` added a bridge column at a corner join even when the other orthogonal bridge was already in the run (it only stopped on a hit in its first-tried order), giving a second sideways piece beside the step column. It now skips a corner that is already joined face to face. A jog of k rows has k closers, all on one board edge (a chain shares the side of its end closer, `closer_face`), so every board meets the next along an edge.
2. Straight fit (`flatten_panel_run`, tolerance `PANEL_DRIFT = 2`). Along each straight stretch (the corners `simplify` finds) the run holds one row while the boundary's outermost columns stay within 1 of each other, at the innermost of them, then jogs. So the fence is never outside the boundary and never more than 1 column inside it, and a jog appears only once the line has drifted 2 columns (a 27 degree edge: one 2-row jog per 4 columns). Stretches steeper than about 31 degrees, shorter than 3 columns, with an unclear inner side, or whose fit would land on a house footprint keep their original cells. Opening columns get no fitted cell; the run now waits for `keep_clear` so openings (#270/#275) stay open.
