# Houses: footprint, frontage and evidence

Read before planning or applying any house, plot or streetscape build.

## What a house build is
A house feature (pass 1 of a cell, `rules/handoff-layered-passes.md` §1) is
the house only, built on every side:
- Walls on the footprint map as a guide (Minecraft-readable, not a jagged 1:1
  trace; see `rules/craftsmanship.md` §1), a real roof form (ridge direction
  and type from aerial, Y from LIDAR), windows and doors on all sides, bays,
  porches, chimney stacks as plain massing.
- Inside: one floor per storey and one stair so it is walkable. No rooms,
  partitions or furniture. Private interiors are never researched.
- No gardens in the house build. Gardens belong to the later passes: lawn and
  paving = ground, trees and shrubs = trees, fences, walls, hedges, sheds =
  decor. Garden layout comes from the aerial only (no peering photos).
  Boundaries come from `scripts/garden_sources.py` (map barrier tags, then the
  per-street convention `site/gardens/<street>.json`, then aerial hedge strip,
  then marked inference), read by `decor_layer.py`.

Why whole houses: I-009, five façade builds put a brick shell on the pavement
of Broadlands Road, about 6 blocks in front of the real houses. Readback, walk
and patrol all passed. All five were reverted.

## Residential frontage (shared description)
`garden_sources.residential_frontage` gives ground and decor one description per
property: private frontage `extent`, `boundary` segments, entrance `opening`, `route` to
the door, and `connection` status with provenance. Ground and decor read it; neither
builds a second one.
- Evidence order: explicit property observation, then street-run evidence, then labelled
  same-street inference. Inference needs an identified residential property (street and
  number) plus a `front` segment. A missing convention alone never authorises a fence.
- `front: none` (convention or per-house override) is an explicit open frontage and wins.
- The door is `observed` (mapped) or `inferred` (nearest house column to the street foot).
  An inferred door gives a plausible route, reported `inferred`, never `verified`.
- Ground keeps guessed lawn as lawn inside private frontage (no forecourt paving there).
- Decor drops inferred barrier columns and furniture inside the opening, and final
  `join_pieces` may not close it. A mapped boundary across an opening is kept and listed
  under `reserved.conflicts` in the decor plan.
- A route through the house is reported `blocked` with endpoints and pass; the house is
  not altered to force access.
- Landed doors: `garden_sources.landed_approaches` reads the cell's landed, not undone feature
  plans (read-only) and `entrance_approach.entrances_from_blocks` rebuilds door, steps and the
  outward connection from the recorded blocks. The route then starts at the outer end of the feature's
  own steps (`connection.status` `built`; the door position is `generated`, never `observed`). No
  recorded door means no endpoint: the frontage falls back to a labelled `inferred` entrance.
- Ground paves *guessed* ground on the route (observed lawn, gravel and paving stay) and the path
  is exempt from denoise. Decor keeps the route and the feature's clearance columns free of
  barriers and furniture (furniture dropped there appears in `unplaced`). Blocked connections go to
  the ground stats as `frontage_blocked`. Trees are not moved or cleared by either pass.
- Feature owns door, threshold and external steps (`house_build.entrance_approach` describes
  them read-only, rebuilt from current geometry); ground owns the onward route from its outer
  end; decor owns boundary and gate placement. Clearance is shared planning data, not
  ownership of air.

## Garden outbuildings (decor)
Sheds and garages in a garden are decor (`lib/layers/outbuildings.py`, #251); attached garages inside a
house footprint stay with the feature pass. Evidence, in order: a mapped footprint tagged in
`coverage.OUTBUILDINGS` (high), a raw lidar nDSM blob 1.6-4.5 m high off buildings, canopy and green aerial
(medium), a designer trace in `reference/decor-furniture.json` with kind `shed`/`garage`, `cells` and an
evidence id (medium). Never from aerial colour alone, never a default scatter. One squared rectangle,
ground from lidar, top clamped to the nDSM height. Refused, with an unplaced reason, on a route, clearance
column, entrance opening, carriageway or across a plot line.

## 1. Survey first
- `python3 scripts/osm_footprints.py frontage --bbox x0,z0,x1,z1`: real
  `addr:street` + number, front-wall MC x,z, setback, surface profile to the road.
- `python3 scripts/osm_footprints.py plots --bbox …`: a first-guess plot per
  house (JSON + PNG). Plot sides continue the party walls.
- Name builds by real street + numbers, e.g. `broadlands_rd_20_22`.
- Work a whole run of adjacent houses per build (a terrace between two gaps),
  so neighbours meet cleanly at party walls and roof steps.

## 2. Placement
- Walls sit on the footprint map within about 1-2 blocks after squaring. Never
  at "the last solid block before the pavement" (I-009).
- Doors face the street they are numbered on.
- Lidar composite Y is the massing authority (ground, eaves, ridge) via
  `lidar_heightmap.py` and the active world's lidar provider in place-sources.
- `site_kit.apply()` refuses building-like blocks far outside footprint-map polygons.
  Set `plan.outside_footprint_ok` only for a genuinely unmapped structure and
  name its evidence id. Never to shove a house onto the kerb.
- A building visible in imagery but missing from the vector map or lidar is built too
  (PROCESS §2d-i).
- Lidar is a dated snapshot: if newer photos clearly show a change, follow the photo and
  record the mismatch (PROCESS §2d-i).

## 3. Describe the evidence before planning
3-5 lines per property group in REVIEWS or the place notes: storeys, bays and
porches, opening rhythm, materials, roof form (aerial), front boundary line
(street photo). Name the photo or aerial id for each. Same-terrace typology
may be used as a hint for side and rear elevations; label it `hint`.
What to trust per question (storeys, roof, walls, ground): `rules/evidence-order.md`.

## 4. Check the result against the evidence, not against itself
Readback, walk and patrol prove the blocks match the plan, not that the plan
matches the real place. Done needs:
- `osm_footprints.py check --build <name>` passes.
- A client view from the reference photo's camera (manifest `bbox_mc`) beside
  the photo, and a top-down view beside the aerial sheet (`google_aerial.py`; the university aerial too where it
  helps), in the contact sheet.
- 3 concrete matches or mismatches written in REVIEWS.

## 5. Stop instead of patching
The stop rule is in `rules/process.md` §2: a geometry fix build means revert
and re-survey, never _b/_c patches. If a next-task item tells you to repeat a
method that contradicts the evidence, fix the queue item and log an ISSUE.
