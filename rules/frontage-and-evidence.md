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

## Land-use class (#253)
Each building and open column has one land-use class from `lib/sources/land_use.py` and the table
`config/land-use-classes.json`: residential, commercial, industrial, civic, park, rural, natural,
water, transport, parking, unknown. Read it as follows.
- A building's own tags win. A building with none takes the smallest use polygon at its centroid,
  else residential when it has a house number, else unknown. So a house number inside
  `landuse=retail` classifies as commercial only when the building tag is `yes` or an outbuilding;
  `building=house` stays residential.
- Use polygons (what the land is for): smallest wins. Cover polygons (grass, flowerbed, natural
  cover) set the class only where no use polygon covers the column.
- Exception for furniture (#322): a park-class use polygon (`leisure=park`, `garden`, `common`, ...)
  is an umbrella. Columns inside it that the smallest-polygon rule would give to a nested rural or
  natural polygon (`landuse=meadow`, `orchard`, `leisure=nature_reserve`) read as park for
  `decor_plan.land_class_columns`, so a path through them takes park furniture (bench density).
  Wood and other cover polygons already lose to the park use polygon. `landuse=grass` is cover, not an
  umbrella. Buildings and the coverage rule itself are unchanged.
- Unknown carries a reason: `not_checked` (no clip that asked for landuse covers it, so fetch),
  `unmapped`, or `unlisted` (a tag value the table does not know). No neighbour inheritance.
- The class never paints surfaces and never authorises a fence: every boundary default is null.
  Rural, natural, water and unknown allow no inference. Parcels carry it as `meta.land_use`.
  Frontage, ground and decor read the table's frontage row (#254), see below.

## Frontage by class (#254)
`garden_sources.frontage(meta, ..., row)` is the one description for every class
(`residential_frontage` is the old name, kept as a wrapper). Residential is the residential row of
`config/land-use-classes.json`; other classes read their own row: `requires` (a non-house class
needs only a street-facing `front` or `perimeter` edge, no house number), `private_surface`
(`grass` and `keep` leave guessed lawn, `paving` lets ground pave it), `opening` (`door_route`,
`street_gate`, `none`) and `pavement` (public pavement width, suburban residential 2.5 blocks).
Rural, natural, water and unknown allow no inference: never eligible, no fence, no opening; the
`keep` ones still keep guessed lawn in their front extent. Non-house parcels name their edges to
neighbours `perimeter`; `front` stays the street edge. `unknown` plots keep the residential edge roles,
so street-convention side and rear fences still apply there (street evidence, not class inference).
The street band is now road half-width plus the class pavement (2.5, was 3.0), so residential plot
edges and `front` cells can shift by up to half a block.
- Precedence per field is `land_use.resolve`: per-house override, street convention, district
  profile, class default. `none` speaks and stops the search. A null class
  default never authorises a fence; no class ships one. The winning layer is `scope.layer`
  (`property`, `street`, `district`, `class`, or `aerial`) and the class is `scope.land_use`.
- Observed evidence (mapped barrier, aerial hedge unless vetoed) still sits above all four.
- Behaviour changes pinned by tests: a `building=yes` with a house number inside `landuse=retail`
  is commercial; guessed lawn between road and building stays lawn in park, rural and natural land.
- Ground splits public pavement from private forecourt: the strip outside the front boundary
  (class pavement width) paves guessed lawn; paving behind the boundary, in the front extent, is
  `forecourt` (andesite), another block. Seen lawn, gravel, brick and the aerial colour rules are
  unchanged. The front boundary and entrance opening are decor's, as before.

## District profiles (#255)

`site/districts/<slug>.json` is area evidence in the world repository, reusable across cells.
The key is a named area listing its cells, independent of any provider; never use vector-map `place`
polygons. Each cell belongs to at most one district. `garden_sources.load_district(cell)` validates
all files and refuses intersecting districts, including intersections outside the requested cell.
Cell rectangles are inclusive, with ordered `min` and `max` integer pairs; repeated cells within
the same district are harmless. A cell straddling real districts must be assigned to one.

Synthetic format example (not a real profile):

```json
{
  "name": "Example district",
  "cells": [[0, 0], {"min": [1, -2], "max": [3, -1]}],
  "evidence_ids": ["example-area-study"],
  "classes": {
    "residential": {
      "frontage": {
        "boundary": {"front": {"type": "hedge", "density": 0.54}},
        "materials": {"hedge": {"block": "spruce_leaves[persistent=true]", "height": 1}},
        "runs": {
          "timber-fronts": {
            "street": "Example Road",
            "numbers": ["2", "4", "6"],
            "boundary": {"front": {"type": "fence", "density": 0.77}},
            "materials": {"fence": {"block": "oak_planks", "height": 1}}
          }
        }
      },
      "furniture": {"lamp_spacing": {"residential": 28, "primary": 40, "path": null}},
      "palette": ["bricks", "stone"],
      "roofs": ["gable"],
      "storeys": {"min": 1, "max": 3}
    }
  }
}
```

- `name`, a nonempty `cells` list, nonempty `evidence_ids` and `classes` are required. Class ids
  are the land-use table ids. A missing class/section/role says nothing: fall through to the class
  default. Unknown ids are errors; rural, natural, water and unknown cannot carry inference sections.
- `frontage.boundary` uses the table's role names: front, side_front, side_back, rear, perimeter.
  A role is null (no boundary) or `{type, block?, height?, density?}`; type is `none`, `hedge`,
  `fence`, `brick_wall` or `iron_bars`. Heights are integer blocks, 1–8. Missing block/height uses
  the same-kind material ladder, then existing builder defaults. Missing density is 1.
- `density` is the share of whole property-role frontages, from 0 to 1. A stable hash of district,
  street, house number, class and role selects a frontage, so reruns agree. It never scatters holes
  along a boundary or removes a street/per-house boundary. A density miss supplies no boundary
  inference and never vetoes an observation.
- `materials` carries dominant block and optional height per boundary kind, derived from the cited
  study. `runs` is keyed by a human-readable run/frontage-block name. Each names its street and
  optionally its house numbers (strings); omit numbers for the whole street in this district.
  Street names use the convention slug normalization. Two runs matching the same property are an
  error. Run boundary/material entries override the class section by role/kind. Use separate numbered
  runs where the two sides differ. Do not assume one material describes every street in a district.
- `land_use.resolve` remains the one ladder: per-house override > street convention > district
  profile > class default. It resolves presence by role and fills omitted material fields only from
  the same kind. An exact per-house light fence keeps its own block and height. District/class
  inference requires the class to allow it; district boundaries also require the table's frontage
  conditions (identified property and street-facing segment for residential).
- Profiles are **area inference**, never `observed`: boundary items have `source: inference`,
  `scope.layer: district`, evidence ids and a reason. A profile cannot set observation provenance.
  Mapped barriers and furniture points, per-property observations and aerial hedge evidence retain
  priority. District `none` and density misses cannot veto aerial observations; explicit property
  and street `none` vetoes keep their existing behaviour.
- `furniture.lamp_spacing` maps vector-map highway class values to spacing in blocks (1–1000); null explicitly
  disables lamp inference for that highway. `garden_sources.lamp_spacing` consumes this through
  `land_use.resolve`, falling back to the table's optional `highways` map. It returns the winning
  layer and inference provenance. Lamp placement remains #260; no lamp scatter is added by #255.
  `bench_per_100m` (0–100) may be stored for that task, with no placement consumer yet.
- `palette` is a nonempty list of block ids; `roofs` is a nonempty list from flat, gable, hip,
  mansard, shed; `storeys` is `{min, max}` (ordered integers, 1–100). All are optional or null,
  stored and validated, read by nobody until the typology trial. Optional `planting` follows the
  class table shape and validation; profile planting also has no consumer in this ticket. Profiles
  do not change ground surfaces, palettes, roofs, storeys or planting rules.

Plank tone table (choose a distinct tone only when the run's evidence supports it):

| Tone | Plank block | Use |
| --- | --- | --- |
| Default / mid warm brown | `oak_planks` | Default panel tone |
| Dark | `spruce_planks`, `dark_oak_planks` | Brown to near black-brown |
| Reddy | `jungle_planks`, `acacia_planks`, `mangrove_planks` | Pinkish tan, orange, deep red |
| Pale | `birch_planks` | Cream |
| White / painted | `pale_oak_planks` | Painted or white-looking fences only |

Close-boarded panels use `type: fence` plus a plank block. They resolve to `plank_fence` and
are placed as matching facing shelves (solid plank fallback when no outward direction is known).
The existing panel helpers join steps and neighbouring fronts with cross shelves, respecting
openings and ownership. `JOINABLE` covers older barrier kinds; panel joining is separate. Normal
slatted/picket runs use fence blocks. This format does not change either builder path.

Owner decisions for front boundaries (task #244, reviewed in world on 2026-11-12 and 2026-11-14):

- The default fence material is a close-boarded timber panel: `type: fence` with `oak_planks` at
  height 1 (about 1 m). A slatted or picket run uses the normal fence block. Use another tone from
  the table above only where that street's evidence shows a distinct tone.
- Front side dividers are open by default. A convention sets `side_front` to `{"type": "none"}`
  unless a house number carries its own `side_front` override or a `side_passage` opening applies.
  A divider on the street-facing side reads as a sideways stub off the front line. The evidenced
  front boundary is the street line.
- Shelf panels keep their open slots. That slatted look is accepted: do not add a back layer or
  full planks at fence height. Reopen only if a later review objects.

## Residential frontage (shared description)
`garden_sources.frontage` (formerly `residential_frontage`) gives ground and decor one description per
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
- Feature owns door, threshold and external steps (`entrance_approach.entrance_approach` describes
  them read-only, rebuilt from current geometry); ground owns the onward route from its outer
  end; decor owns boundary and gate placement. Clearance is shared planning data, not
  ownership of air.

Garden boundaries are plan items with their original `provenance`, `scope` (street,
property and winning layer) and `reason`. A hedge material does not imply aerial evidence:
a street convention is labelled inference, a property override retains its own evidence,
and an aerial strip is labelled aerial. Decor's plan and placed-barrier report retain
these fields. Existing convention JSON remains readable; evidence ids can be given at
street or per-role/per-property level, with an optional shared `provenance` record.

A detected front aerial hedge gets the same entrance opening as a convention boundary.
The gap is reserved after straightening and through the final join gate, together with
the onward route and feature clearance. Explicit `none` wins. Entrance reconstruction
stops at mapped footprint columns, including neighbouring buildings; it cannot mistake
a building's solid blocks for an external landing. Trees stay in place. Checking actual
headroom, walking access and judging street appearance remain world review after a
rebuild; the tool's synthetic tests do not confirm those observations.

## Garden outbuildings (decor)
Sheds and garages in a garden are decor (`lib/layers/outbuildings.py`, #251); attached garages inside a
house footprint stay with the feature pass. Evidence, in order: a mapped footprint tagged in
`coverage.OUTBUILDINGS` (high), a raw lidar nDSM blob 1.6-4.5 m high off buildings, canopy and green aerial
(medium), a designer trace in `reference/decor-furniture.json` with kind `shed`/`garage`, `cells` and an
evidence id (medium). Never from aerial colour alone, never a default scatter. One squared rectangle,
ground from lidar, top clamped to the nDSM height. Refused, with an unplaced reason, on a route, clearance
column, entrance opening, carriageway or across a plot line (a footprint with at least half its columns in one
plot counts as inside it). To get a shed where lidar finds none, trace it: a `shed`/`garage` row with `cells` and
`evidence_ids` in `reference/decor-furniture.json`.

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
