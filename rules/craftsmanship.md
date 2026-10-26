CRAFTSMANSHIP — MINECRAFT BLOCK CRAFT
Tool-repo rules (ai-worldbuilder/rules/craftsmanship.md).
Updated: 11 October 2026 (Europe/London)

How to place blocks so houses read as careful Minecraft builds, not as
jagged footprint-map extrusions or stock lidar carpet roofs. Accuracy still wins for
*where* and *what*; this file is *how*.

MATERIAL RULE (user, 2026-10-15): never PLACE plain minecraft:dirt in a build (it can turn into grass
over time); use coarse_dirt. Reading dirt as ground when scanning is fine.

Sources distilled for this project (do not re-fetch every session):
  - Minecraft Wiki: Tutorial:Roof construction guidelines
  - MineSpecs: How to Build a Roof That Actually Looks Right (2026)
  - Project fails: I-009 (pavement façades); lattice/carpet roofs (legacy generator);
    Q-014/Q-016/Q-017 stair roofs on shallow pitches (fixed Q-017b: 1:2
    slabs + envelope carpet clear)

This file is the single home for how blocks are placed. There is no separate
rule-file copy.

------------------------------------------------------------------------
1. FOOTPRINT — GUIDE, NOT PRISON
------------------------------------------------------------------------
 Keep the house on the surveyed plot. Never on pavement/kerb (I-009).

 Footprint-map polygons are ragged at 1 m. At house scale, a 1:1 stair-step wall on
 every diagonal reads as noise. Prefer what a human would place:
  - Axis-align long wall runs; square corners.
  - Nudge/straighten ±1–2 blocks when street photo + aerial still agree.
  - Match party walls with the neighbour so semis meet cleanly.
  - Skip one-block map dents that make holes or knife-edge zigzags.

 Wiki note: non-axis buildings only look convincing at large scale. For
 Broadlands-sized semis, square the mass; do not diamond-trace the raw polygon.

 site_kit footprint guard still blocks pavement builds. It is not a licence
 to demand pixel-perfect footprint walls.

------------------------------------------------------------------------
2. WALLS AND OPENINGS
------------------------------------------------------------------------
 Airtight outer shell: no sky/air holes at corners, eaves, party walls,
 bay joins, or roof/wall junctions. Interior void only for floors + stair.

 Windows (always):
  - Punch openings from a solid wall plane — never float glass in a gap.
  - Every pane (or pane column) has opaque blocks left and right.
  - Sill under (slab or upside-down stair); lintel/head above.
  - Prefer glass *panes* inset one step if the wall is ≥2 thick; if the
    wall is 1 thick, still keep solid flanks and a coherent surround.
  - No glass as a corner block; no 1-wide glass strip with air beside it.

 Window rhythm (user steering, Oakdene Court, 2026-10-16):
  - Count the blocks on the face and write the bay rhythm from the photo, e.g. an 8-cell front = WW_BB_WW
    (W window 2 wide x 1 high, B balcony 2 wide, _ brick pier 1 wide; the corner cells are the end piers).
    Windows are 2x1 pairs, never single-block panes.
  - Sharp corners get no windows, unless a photo or aerial confirms the building definitely has them. That includes the outer
    corners of a stepped diagonal wall (cells exposed two ways).
  - Stepped diagonal walls (stairs) take windows on the flat tread between two corners: one full GLASS BLOCK, flush, every
    few treads. Never glass panes on a diagonal (a thin slit at the corner), and never the straight-wall pairs there.
    A stepped wing with no tread run long enough stays brick; say so in the note rather than forcing glass onto corners.
  - A face you cannot see is inferred, not left blank: give every straight face the same rhythm as the one in the photo.
  - Balconies are open: white parapet at the face, plain back wall, no glass. Only the entrance face has them.
  - A blank flank at each run end is wasted: the corner cell already is the pier.

 Doors: solid surround, facing the numbered street, walkable stoop.
 University buildings: put a door at every portal (see ENTRANCES below), not wherever looks best.

 Depth (good-enough, not decoration chase):
  - Bays/porches are structural bumps from the ground, not stick-on frames.
  - A one-block reveal (frame vs fill) beats a flat brick sheet.

------------------------------------------------------------------------
3. ROOFS — PITCH IS ARITHMETIC
------------------------------------------------------------------------
 Evidence first: ridge direction and roof *type* (gable/hip/valley) from
 the aerial; eaves/ridge Y from LIDAR. Then pick a Minecraft pitch that
 matches the rise÷run, not a default stair carpet.

 Standard pitches (span S = wall width + overhang both sides; prefer odd S):

  Ratio   Angle (approx)   Surface pattern
  1:2     ~22.5°           Bottom/top slab alternate (shallow)
  1:1     45°              Stairs ascending inward
  2:1     ~63°             Full block + stair per column (steep; rare here)

 Which pitch (one rule):
  - Victorian/Edwardian terraces and semis (Broadlands Rd type): slate at
    ~40°, built as 45° `deepslate_tile_stairs`, ridge parallel to the street.
    Build them with `scripts/terrace.py` (rules/terrace-builds.md); do not
    hand-roll roofs.
  - Genuinely shallow roofs (modern, ~22°): 1:2 slab courses, no stairs on
    the skin.
  - Anything else: pick the ratio from the aerial and LIDAR rise÷run.
  The old "default to 1:2 slabs" rule read as flat roofs on Broadlands and is
  withdrawn for the terraces above.

 Hard rules:
  - Stairs on the skin are right only at ~45°; slabs only at ~22°. A stair
    skin on a shallow roof is the fault.
  - Full blocks are **wall-plate / gable / hip fill**, not the visible pitch.
    A stepped pyramid of full blocks (or stone/carpet stripes from a generator) is
    the beginner / generator tell — replace it.
  - Leftover generator blocks on a processed column are a fault; who clears what is in
    HANDOFF-LAYERED-PASSES §1 (a feature clears its whole volume, ground
    clears the rest).
  - One pitch per house (wings/bays match the main).
  - Overhang ≥1 block past the wall on eaves (and gable ends). Flush roofs
    look like a hat glued on a box.
  - Roof is at least two layers thick where stairs are the skin: solid or
    upside-down stair underlay so the attic is not see-through.
  - Seal light holes: stairs leave gaps; fill with upside-down stairs,
    slabs, or a flat ceiling course under the pitch. Airtight from outside.
  - Chimney passes **through** the roof (swap roof blocks for full blocks
    where it crosses); stack rises above the ridge — not a block parked
    beside the pitch.
  - Ridge: odd span → single ridge line; cap with slab or deliberate beam.
    Even span → accept a 2-wide flat ridge capped with slabs, or widen
    the plan by one.

 Hips: stair auto-mitre only looks clean at 1:1. At 1:2 (slabs) cut the
 hip diagonal by hand with full blocks and die the slab courses into it.

 Clear leftover carpet/"stone roof" and undercroft where the real house
 replaces them (legacy Southampton lump notes in the active world site/).

------------------------------------------------------------------------
3a. STREETS — RULES OF THUMB, NO SURVEY (user, Q-044, 12 Oct 2026)
------------------------------------------------------------------------
 Roads are boring: no aerial crops, colour profiles or per-column surveys
 unless a street plainly breaks these rules. Live ground/street apply is
 scripts/ground_layer.py (preview + --apply via site_kit). The rules below
 are the palette and corridor rules; parcel ids come from the vector map / cell sources.
  - Keep the mapped road corridor (route is trusted; house builds already sit against
    footprint edges). Every column at lidar ground Y (`lidar_query` / DTM).
  - Stock gray_terracotta → gray_concrete; a terracotta cell with any
    non-terracotta N4 neighbour → smooth_stone kerb, flush. Junction and
    driveway mouths stay asphalt (dropped kerb).
  - Stock polished_diorite, and leftover stone inside a road parcel →
    light_gray_concrete pavement at the adjoining street Y.
  - Rear lanes / alleys: gravel 60% / coarse_dirt 28% / dirt_path 12%
    within 1.5 of the map centreline, grass_block verge on the rest.
  - Service ways go by map way tags (user, Q-045; scripts/build_broadlands_ring1_lanes.py):
    service=driveway / parking_aisle (untagged) and surface=asphalt → gray_concrete
    over the whole parcel, no kerb; asphalt alley → gray_concrete within width/2 +
    verge; highway=path dirt → coarse_dirt / dirt_path within 1 + grass. Check the
    tags first — most "lanes" near flats and shops are driveways.
  - Same gray_concrete tarmac for campus car parks (map amenity=parking, bay markings
    white_concrete), parking aisles, and access roads — scripts/ground_layer.py
    (the live ground pass; this section is its palette).
  - A column whose live top is not ground (carpet over brick / standing structure)
    is an unrebuilt building in the street: write it unchanged, drop only the carpet,
    lay no street there (clearing holes the building).
  - Clear unowned blocks above (road-label signs, orphan posts);
    re-solidify the bed down to the lowest cell any superseded streetscape
    pass wrote (their trims left air: gravel falls, readback sees "structure").
  - Never write cells owned by house builds; put old streetscape passes and
    the reverted I-009 façades (+ facade_revert) in modifies.

------------------------------------------------------------------------
4. ANTI-PATTERNS (REJECT IN PREVIEW / REVIEW)
------------------------------------------------------------------------
  ✗ Walls on pavement or at "last solid before road" (I-009)
  ✗ Jagged 1:1 footprint wall trace with missing corner teeth
  ✗ Lattice / carpet / striped full-block roof left in place
  ✗ Any leftover generator block on (or straddling) a processed plot
  ✗ Stair skin where the aerial shows a shallow pitch, or slabs where it shows ~45°
  ✗ Full-block underlay pyramid reading as the roof surface
  ✗ Roof flush with walls (no overhang)
  ✗ Single-layer stair skin with sky visible through gaps
  ✗ Floating or unflanked glass
  ✗ Glass (or any block) set on a bottom slab: the slab leaves a half-block gap and it floats. Put a full block under it (Oakdene lantern)
  ✗ Relying on ``block_fill`` to place bottom slabs as a roof membrane — fills often leave those cells air. Prefer full blocks for membranes; ``site_kit.apply`` rewrites bottom slabs to full blocks unless ``plan.keep_bottom_slabs``
  ✗ Chimney sitting on top of the roof without cutting through
  ✗ Sapling blocks anywhere (rules/foliage.md)

------------------------------------------------------------------------
5. CHECK BEFORE MARKING A PROPERTY DONE
------------------------------------------------------------------------
 Add to the usual PROCESS done list:
  [ ] Shell airtight from street and aerial angles (no lattice/carpet roof)
  [ ] No leftover generator blocks in the feature's volume (HANDOFF-LAYERED-PASSES §1)
  [ ] Roof pitch matches §3 (terrace stairs / shallow slabs); pitched overhang ≥1;
      flat / zoned roof edging default none (set `roofs[].edge` only from aerial)
  [ ] Every window has sill, head, and solid L/R flanks
  [ ] Footprint squared for Minecraft, still on the plot (check OK)
  [ ] Contact views still used for evidence — craftsmanship is extra,
      not a substitute for photo/aerial match

 Good enough: rules/radial-good-enough.md. Do not chase ridge cresting or
 trapdoor gutters either. Readable massing + sealed shell + correct street
 character is the default.

HOUSE-ONLY RUNS (vector-map footprints, slab roofs): scripts/house_build.py + site/builds/<name>.json is the only house feature apply path (spec keys in its docstring). List earlier builds in modifies; site_kit.apply refuses unlisted owners.
Never copy a peer spec or a per-run build script. Write the spec from this run's evidence. Old copies: scripts/legacy/.

NON-HOUSE FEATURE RUNS (campus / halls / sheds / odd masses): scripts/feature_build.py + site/builds/<name>.json is the only non-house building feature apply path (spec keys in its docstring; rules/feature-builds.md). Explicit storeys + flat|pitched roof. Do not extend house_build for odd shapes. List earlier builds in modifies.

LIDAR HEIGHT REPORT: scripts/lidar_heightmap.py --cell tx,tz — national lidar composite DTM/DSM where cached
(`rules/evidence-order.md`), never the live world. Diagnostic lump rows are not a queue driver (ADR 003).
Per column: h (lidar surface), base (ground), kind (ground/roof/tree/tree-over-roof). Report: footprint, ground,
eave, ridge, storeys (estimate, 3 blocks each), flat/pitched, ridge axis, trees over roof, footprint tags under it
(none = UNMAPPED). Read before planning; use for storeys/roof instead of guessing. --write keeps
verification/site/lidar/<tx>_<tz>.npz. Audit: scripts/lidar_audit.py --cell tx,tz. Tables: site/cells/<tx>_<tz>-LIDAR.txt.
Storeys from ridge height (eave is ~4); map building:levels shows as 'lvl' when mapped.


DOORS ARE FRAMED (user, 2026-10-16)
 A door has a jamb each side and a lintel above, at both door heights. site_kit.apply refuses a
 plan whose door lacks one (plan.allow_unframed_doors for a genuinely free-standing door). scripts/door_audit.py lists
 the old ones (142 of 700 doors on 2026-10-16): see the matching issue / next-task item.


ENTRANCES (university register, user, 2026-10-16)
 reference/portals/portals.json lists every university entrance with its real position (the door, not the photo), name
 and, for 80, a photo. scripts/portals.py cell tx,tz | building N | near x,z. The feature queue item and
 cell_features.py print the portals on each feature's footprint.
  - portals.door_specs(cells) or portals.for_footprint(rings) give the positions.
  - The portal fixes where the door is and what it is called (a "Main" entrance gets the canopy/stoop, a "Rear" or
    "Lower ground" one is plainer, one "Concourse entrance" can be a wide opening). Its photo decides door type: look
    at it once (look-only, no saving) when the door is a visible feature of the face.
  - A portal placed on a face the build does not have (footprint map off, wing not built) means the footprint is wrong:
    check the wall there before moving the door.

GREEBLIES ARE THE DECOR STEP OF A FEATURE (user, 2026-10-16)
 Roof and site detail (plant, vents, hatches, fans, panels, seams, planting beside the building) is applied after the
 feature's shell, as part of that feature, not as a separate pass.
 - Use unusual blocks freely for flavour (iron trapdoors, tinted glass, mushroom stem, terracotta, quartz, blackstone):
   pick by colour and texture read off aerial, not by habit. Look before choosing: `block_reference.py nearest R,G,B [--flat --opaque]`
   lists the closest blocks to a colour, `describe <block>` gives one text line (colours, texture, opacity, shape), and
   images (`one`, sheets in instance/block-reference/) are only for choosing between close candidates
   (panes, bars, walls and fences change shape with neighbours: `block_reference.py one <block>` draws them joined).
 - Plants: match species colour and size from aerial (and streetlevel when visible). Grass and flowers come in two heights
   (short_grass / tall_grass, small flower / double-height flower); bushes are multi-block clumps of leaves
   (see rules/foliage.md for the allowlist and where air is required).
 - Heights come from lidar (plinth, plant yard, wing tops); colours and positions from aerial and streetlevel.
   Where sources conflict, make it look nice and realistic in Minecraft terms.
 - Placement rule: never place a greeblie over slab-type blocks (slabs, carpets, stairs, trapdoors...); it hovers. Coping slabs
   along a roof edge are the usual case. This is not "keep off the edge": a full block at the edge is fine.

BUILDING PICTURES (university register, user, 2026-10-16)
 reference/buildings/buildings.json: for each university building its number, name, a photo (247 of 370, saved at 800 px in
 reference/photos/buildings/<number>.jpg) and the university's own outline (232). scripts/buildings.py cell tx,tz | building N |
 near x,z. The feature queue item and cell_features.py print the picture of each feature's footprint.
  - Look at the photo once, before planning a campus building: walls (material, bay rhythm), openings (glazing, doors),
    storeys, roof edge and colour. Write what it shows in the build docstring next to the source id.
  - Storeys: the photo is a photo (rules/evidence-order.md), so it ranks above map levels and lidar for campus buildings.
  - The outline is a cross-check on the footprint map (a missing wing, a wrong corner). Disagreement is a note, not an
    override: lidar columns and the aerial sheet decide, and heights are always lidar.
  - Many buildings have no photo and several photos show one face or a later extension (the Union has a 2025 shot): say
    which face you saw. A missing photo is not evidence of anything.

