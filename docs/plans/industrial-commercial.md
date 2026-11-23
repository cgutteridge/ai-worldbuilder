# #258 trial: industrial estate, high street and retail big box (2026-11-20)

Status: findings from a supervised spike in `southampton-build`, for the owner and the orchestrator.
Verdicts: industrial defaults accepted; big-box base accepted (roof fault); shop defaults improved but
not yet accepted. Follow-ups: section 9.
Plan: [vibe-at-scale.md](vibe-at-scale.md) section 6. Classes: [land-use classes](../adr/006-land-use-classes.md) (#253).
Owner decisions (2026-11-20, relayed by the orchestrator session): world `southampton-build`; three empty
study cells may be overwritten; z19 aerial authorised for those cells; later the same day the owner
approved three neighbour cells (51,-36, 50,-35, 42,-16) for aerial, lidar ghost and cross-cell buildings.

## 1. Study areas and build windows

| Area | Point (WGS84 -> MC) | Cells written | Build window (what was built) |
| --- | --- | --- | --- |
| Industrial estate (Belgrave Industrial Estate, Portswood) | 50.93113,-1.38412 -> 7129,-5205 | 55,-41 | The estate core inside 55,-41 (x 7082-7167, z -5243..-5121): west shed (units 1-4), east shed (units 7-18), Monarch Laundry (unit 20); estate road and yards. Units 5, 6 and 19 and Regal Auto Sport cross into 55,-42 / 56,-41 and were left out |
| High street (Portswood Road) | 50.92524,-1.39336 -> 6485,-4544 | 50,-36, plus 51,-36 and 50,-35 | The junction: west shop parade (9 buildings), east shop parade (10, red brick), The Victory Centre (church), The Portswood Centre (shopping centre) and its car park. Mountbatten, Betfred, Viceroy and Scope left out (their eaves reach 50,-37) |
| Retail big box (Westquay South / Watermark) | 50.90237,-1.40663 -> 5573,-1993 | 43,-16, plus 42,-16 | Westquay South (one mapped way, many leisure and food units in one structure) and its plaza edge |

Windows are "the named buildings plus their street", not whole cells. The ground pass ran over the
three study cells; the neighbour cells keep lidar ghost ground outside the buildings that cross into them.

## 2. Evidence and downloads

Downloads (owner-authorised, aerial tiles at zoom 19, fetched with the system aerial tile fetcher (`fetch_tile`) through a scratch stitcher). Saved as local, gitignored mosaics
under `worlds/southampton-build/reference/aerial/google-trial-258/`, with no manifest row and no hash
(provider terms: look-only); the readiness check counts them as aerial coverage.

| Cell | File | Tiles | Bytes |
| --- | --- | ---: | ---: |
| 55,-41 | `mosaic-z19-4x3-260126-175678.png` | 12 | 1,516,312 |
| 50,-36 | `mosaic-z19-4x4-260112-175691.png` | 16 | 2,291,760 |
| 43,-16 | `mosaic-z19-4x4-260093-175744.png` | 16 | 1,979,845 |
| 51,-36 | `mosaic-z19-4x4-260115-175691.png` | 16 | 2,431,283 |
| 50,-35 | `mosaic-z19-4x4-260112-175693.png` | 16 | 2,263,974 |
| 42,-16 | `mosaic-z19-4x4-260091-175744.png` | 16 | 2,038,539 |
| Total | 6 files | 92 | 12,521,713 |

No other downloads. Map data came from the existing local Geofabrik Hampshire PBF (`prepare` extracts
it; no fetch). Lidar came from the cached national lidar tile SU41sw.

What the evidence says per class, and whether it exists anywhere in the UK:

| Evidence | Industrial | Commercial / retail | Parking | UK-wide? |
| --- | --- | --- | --- | --- |
| Mapped `building=*` | `industrial` on every Belgrave unit; one way per unit (20) | `retail` on every shop; `church`; one way for Westquay South | n/a | Yes, coverage varies; units often one way per lettable unit |
| Mapped `landuse` / `amenity` | `landuse=industrial` (named estate) | `landuse=retail`; `shop=*` / `amenity=*` on the building | `amenity=parking` + `access` + `parking=surface`, `service=parking_aisle` | Yes, common in towns |
| Mapped `name`, `addr:*` | Company names on most units | Shop names on nearly every shop | rarely | Common in towns, thin in estates |
| Mapped `building:levels`, `height`, `roof:*` | None | None (only Addis Square flats) | n/a | Sparse; do not rely on it |
| Mapped `barrier=*` | One fence, on the residential side | none | none | Sparse |
| Lidar DSM-DTM | 5-6 m sheds | 6-9 m parades with low rear extensions; church 16-18 m; centre 8-10 m; Westquay 30 m | flat | England (national programme), Wales and Scotland have equivalents |
| Aerial | Pale ribbed roofs, yard parking, roller doors not visible from above | Roof colour per parade, awnings faintly | Bay lines and rows visible | Everywhere |

## 3. What was built, and how

Two variants per area, compared from the street:

- **A. Rules only (tier B).** `feature_build.py` minimal specs: footprint, storeys from median lidar
  DSM-DTM (3 m a storey), roof kind and colour from the aerial, one wall block, a glazing default; then the
  standard ground pass (`ground_layer.py`). No per-building design. Specs: `site/builds/w241498208.json`,
  `w241499737.json`, `w241497621.json` (industrial); `w160637268.json`, `w149814013.json`, `w149814666.json`,
  `w1480342683.json` (high street); `w465660355.json` (Westquay South).
- **B. A + class dressing.** A throwaway prototype (scratchpad `dress.py`, not on main) adds per-class defaults
  as a journalled follow-up plan (`Plan.parent_feature`): street-facing wall columns are found by distance to
  the mapped street line, then

  | Class | Dressing tried |
  | --- | --- |
  | industrial | one 4x4 roller shutter (gray concrete) per unit on the yard face; fascia colour band per unit (colour from the company name); yellow loading-bay box in front of each shutter |
  | commercial (shops) | ground-floor glass on the street face; one door; fascia colour band; oak wall sign with the mapped name; trapdoor awning for cafes, restaurants, takeaways and food shops |
  | commercial (big box) | two glazed storeys on the plaza face; fascia band; roof PV arrays (daylight detectors) where the aerial shows them |
  | parking | white bay lines every 3 blocks in 5-deep rows either side of 6-wide aisles (later: along the aisle, #336; the pitch runs the whole aisle, each aisle marks only the car park it runs through, and a bend that cannot hold a straight row is left blank, #442) |

What a player saw (screenshots kept in the session scratchpad, not committed):

| Area | A. Rules only | B. With class dressing |
| --- | --- | --- |
| Industrial | Grouped sheds have the right length, height and roofline, but the default two-storey window grid makes them read as offices | Shutters, fascia colours and yellow bays: reads as a row of trade units (owner: yes) |
| High street | Brick masses with the right rhythm and roof colours, but blank walls: not shops | v1: glass, door, fascia, small sign, single trapdoor awnings: closer (owner: not there yet, notes in section 8). v2 after the owner's notes: doors at pavement level, awnings along the whole frontage, contrasting name sign on the fascia (owner: better, not yet the default; three faults in section 8) |
| Westquay South | One dark 30 m box with small windows: an office or warehouse | Glazed two-storey base and fascia to the plaza, sign, PV on the roof (owner: big box yes; roof does not match the aerial) |

## 4. What carries the vibe

1. **The street face carries the vibe; the mass is nearly free.** Footprint + lidar height + aerial roof colour
   got every building's size, rhythm and roofline right with no design time. What made a player read
   "trade units" or "shops" was only the ground floor of the street face: openings, a fascia band and a
   sign. That is one generic rule per class, not per-building design.
2. **The street face can be found generically.** A footprint column whose outside neighbour is nearer the
   mapped street line is a front column. That worked for 17 units, 19 shops and a big box, at any
   rotation, from map data that exists UK-wide (building ways and highway lines).
3. **Industrial reads from shutters and a colour per unit, not windows.** One roller shutter per unit, a
   fascia colour keyed to the company name and yellow bay boxes passed first time. The default window grid
   actively hurt.
4. **Shops need a terrace-house body, not a commercial box.** The owner's notes: the west parade is three
   storeys with a dormer top floor in the roof, the east parade two storeys with upper bay windows and
   pitched roofs. A UK high street is mostly Victorian/Edwardian houses with a shopfront ground floor, so
   the shop class should reuse the terrace builder's body (storeys from lidar eave and ridge, pitched
   roofs, dormers, bays) with a shopfront ground floor, not `feature_build` boxes. This is the #257 question.
5. **Shop fronts want a straight plane.** On a parade rotated about 35° the stepped face scatters glass,
   jambs and awnings (owner: "square off shop fronts"). The front should be regularised to straight runs
   along the street, as house fronts are.
6. **Signs: name, contrast, room.** Names from map data are on every shop and were liked. A wooden sign reads as
   wood; it needs a contrasting board and wrapped text on the fascia (15 characters a line).
7. **Awnings by shop kind are right, but continuous.** Cafes, restaurants, takeaways and food shops got
   awnings; a single trapdoor reads as a hatch, so the canopy must run the whole frontage.
8. **Landmarks and odd buildings need a street-level look.** `building=church` was a former bingo hall with
   a big facade, steps and a permanent canopy; the aerial and tags could not say so. Civic, church,
   cinema and shopping-centre classes are tier A candidates: one street-level check before building.
9. **Street furniture is part of the high-street vibe.** The owner missed traffic lights, lamp posts, bike
   racks, bollards and the traffic island. These come from the decor layer and mapped points (#260), which
   this trial did not run.
10. **Big box: a glazed base is enough to stop it reading as offices.** The rest of a 30 m box is fine plain.

## 5. Problems found in the tool

Ranked by how much they block non-residential builds at scale.

1. **The ground pass cannot see land below y=31.** `lib/blocks/surface_probe.surface_y` scans down only to
   y=31, and `ground_layer.PROBE_Y_RANGE` starts at 31. In `southampton-build` y is metres AOD + 11, so any
   ground below about 20 m AOD reads as "empty column". 55,-41 (y=14) planned **0** ground columns
   (10,543 empty); Westquay (y=15) would too. That covers Southampton's docks, the Itchen and Test
   valleys and the west of the centre, which is where most industrial and big-box retail sits. Trees and
   decor import the same probe. The trial ran the ground pass through a scratch wrapper with the floor at
   -60 (10,585 columns planned, 0 mismatches). Not a #258 design question: a correctness bug.
2. **One structure, many mapped units.** Map data holds an estate shed or a shop terrace as one way per lettable
   unit (Belgrave: 20 ways; Portswood parades: 19). `propose-features` plans one feature per way, and a
   feature apply must be named after a planned id (`needs-prep: feature <name> has no cell plan`). A
   grouped spec (one shed, one parade) is the cheap tier-B unit, but it covers ways whose plan ids then
   stay "planned". The trial named each grouped spec after one unit's id and dismissed the rest with a
   reason. Needs a first-class group plan (one id, many refs), which `plan_ids_for_candidates` already does
   for houses by street.
3. **Neighbour evidence goes stale when the primary cell builds.** After the west parade landed in 50,-36,
   applies touching 51,-36 were refused, `evidence validation stale (evidence facts changed)`, and the
   same happened to Westquay South and 42,-16. Re-running `prepare --evidence-only` on the neighbour fixed it
   each time. It is the #232 race, but self-inflicted: any cross-cell build must re-prepare every touched
   neighbour immediately before it applies.
4. **The ghost frontier writes far outside the study area.** A feature apply's journal follow-up lays lidar
   ghost ground two cells out (`LIDAR_GHOST_RING` default 2). The first industrial apply ghosted 9 cells
   (53..57,-39 and 57,-40..-43) and spent 388 s doing it. Harmless terrain, but it surprised an owner who
   had said "do not touch any other cell", and it takes most of the apply time. The trial set
   `LIDAR_GHOST_RING=0` for every later apply.
5. **The glazing default reads wrong for sheds, and barely lands on rotated parades.** With no glazing rows,
   `feature_build` still punches a two-storey window grid: an industrial shed looks like an office block.
   With explicit shop-front rows, a parade rotated about 35° to the block grid got 15 glass blocks across
   ten shops, because stepped faces fail the straight-plane rule. The prototype's shop fronts (street face
   found by distance to the mapped street line, solid glass on the stepped face) worked on the same parade.

## 6. Cost

Method: wall-clock minutes from the shell timestamps of each step (logs in the session scratchpad) and
cell journal `begin`/`finish` times. Tokens are this session's context size from the app's usage reading
(`get_usage`, context `tokensUsed`), read at 13:31 (284.5k, 36 min in) and 15:30 (362.7k). The session
started with this trial, so the reading is the trial's own context, but it is context size, not billed
tokens: tool output re-read across turns is counted once. The account's weekly figure moved from 28% to
34% over the same span, but sibling sessions share it, so it is not attributed.

| Step | Wall time | Notes |
| --- | --- | --- |
| Read task, docs, rules, tool help | ~10 min | first time in these tools; a repeat would take ~2 |
| Aerial fetch, 6 cells (92 tiles) | ~1 min total | |
| `prepare --apply` (extract + ghost + validation) | 67-150 s a cell, 6 cells ≈ 11 min | |
| Feature applies, 8 specs (44 mapped buildings) | 45-160 s each ≈ 10 min, plus 6.5 min of ghost frontier on the first | |
| Ground pass, 3 cells | 42-49 s each | with the low-floor workaround |
| Class dressing, 3 areas (prototype) | under 1 min each to plan and apply | |
| Waiting for the shared lock (sibling trial #259) | ~35 min | not tool cost |
| Diagnosing tool problems (section 5) | ~25 min | one-off |
| Owner review with captures | ~1 h, including the v2 rework | |

Per cell, the tier-B path measured here (prepare, grouped specs, ground, dressing) is about **5-8 minutes
of machine time and well under 1k lines of spec**, against roughly 2 minutes and a hand-written spec
**per building** if each of the 44 mapped buildings were its own feature. Agent context for the whole
trial, including diagnosis and review, was about 360k tokens; a repeat run of the same pipeline with the
tool problems fixed should need a small fraction of that (no diagnosis, no prototype writing).
These are single-trial figures (n=1 per area) on the author's machine, not targets.

## 7. Recommended defaults (#253 classes)

Proposed values for the `config/land-use-classes.json` rows and for the builders that will read them. "Trial"
marks what was built and shown to the owner; "owner" marks the owner's own notes; the rest is inference
for the follow-ups to test. Boundaries stay null unless evidenced (#253 rule): the owner's low wall with
railings is one site's evidence and belongs in a district profile (#255), not the class row.

| Class | Body (builder) | Street face | Roof | Ground / private surface | Boundary | Furniture | Evidence that drives it |
| --- | --- | --- | --- | --- | --- | --- | --- |
| industrial | Grouped shed per run of `building=industrial` units; one tall volume, height from lidar; no storey window grid; small office window strip only (trial) | Per unit on the yard/road face: one roller shutter about 4x4, a fascia band coloured from the unit name, yellow loading-bay box in front (trial, owner: yes) | Flat or low pitch, pale (aerial) | `paving` (today) for yards; tarmac where mapped | null; palisade only where `barrier=fence` is mapped | none inferred | `building=industrial`, `landuse=industrial`, unit `name`, lidar, aerial |
| commercial: shop (`building=retail` or `shop=*` in a parade) | Terrace-house body (follow-up): storeys from lidar eave and ridge, pitched roof, dormer top floor and upper bay windows where the ridge says so (owner) | Ground floor squared to the street (owner): glass, door at pavement level, fascia band, contrasting name sign wrapped to 15 characters, continuous awning for cafe/restaurant/fast_food/food shops (trial v2) | Pitched, colour from aerial | `paving` forecourt (today) | null (district profile may add low wall + railings) | lamps, bollards, bike racks, traffic signals and islands from map data points and highway class (#260) | `building=retail`, `shop`, `amenity`, `name`, lidar eave/ridge, aerial roof colour |
| commercial: big box (`building=retail` single way, footprint over about 2,000 columns or lidar over 15 m) | One mass from lidar, plain cladding | Glazed two-storey base on the faces toward pedestrian space or the street, fascia, name sign (trial) | Flat; PV/plant greeblies where the aerial shows them (trial) | `paving` plaza where mapped `highway=pedestrian` | null | benches and bins where mapped | size, lidar, aerial roof, `highway=pedestrian` |
| commercial: civic/landmark (church, cinema, shopping centre, `building=civic`) | Tier A: street-level check before building (owner: the "church" is a former bingo hall with a facade, steps and a canopy) | from that check | from that check | `paving` | from that check | from that check | street-level imagery (owner-authorised) |
| parking (`amenity=parking`, surface) | none | none | none | tarmac (trial: the ground pass already paints it) | null | lamps along aisles (inference, untested) | `amenity=parking`, `parking=surface`, `service=parking_aisle` |
| parking: markings | n/a | n/a | n/a | white bay lines 3 blocks apart in 5-deep rows either side of 6-wide aisles (trial was grid-aligned; rows follow the aisle, #336; the pitch runs the whole aisle and each aisle marks only the car park it runs through, #442; no line in the wedge outside a bend, and none where that bend cannot hold a straight row) | | | `service=parking_aisle` lines |

## 8. Owner verdicts

Given in game on 2026-11-20, standing at the viewpoints named. The last two were relayed by the
orchestrator session, which took over the owner's questions.

| Area | Shown | Verdict |
| --- | --- | --- |
| Industrial (Belgrave Industrial Estate, 7108,-5128 looking south) | Rules only, then industrial dressing | **"Yes, reads right."** Industrial defaults accepted |
| High street v1 (Portswood Road, 6476,-4528 looking north-east) | Rules only, then shop dressing v1 | **"Not there yet."** Notes below |
| High street v2 (west parade, 6504,-4566) | Shop rules v2 | **"It's better"**, but not accepted as the default as it stands. Notes below |
| Westquay South (plaza, 5600,-1925) | Rules only, then big-box dressing | **"Big box yes, but the roof doesn't match aerial pics."** Glazed two-storey base, fascia and name sign accepted as the default; the roof is a recorded fault (the aerial shows long rows of PV on a flat light roof; the build does not match) |

High street v1, the owner's notes (paraphrased closely):

- The coloured fascia blocks should be solid signs; the name signs are nice but anaemic, and wood colour
  does not work. Put the name at the two-block height or on the coloured bar if there is room, in a
  contrasting colour, and watch for running out of space on the sign.
- Square off the shop fronts rather than following the real angle.
- Trapdoor awnings are a nice idea but should run the length of the awning, not be single hatches. The
  Charcoal Grill (west) side is much better and cleaner, though its awnings are also single hatches.
- A door at 6508,-4561 sits in a little frame one block below the ground.
- The "church" is an old bingo hall with a big facade, steps up and a big permanent awning over the steps;
  street view would have shown it.
- Some features were not built (yes: Mountbatten, Betfred, Viceroy and Scope reach 50,-37, which was not
  approved).
- Very little street furniture where the street has traffic lights: lamp posts, bike racks, bollards, a
  traffic island.
- The red-brick shopping centre has a low wall with railings in front.
- West-side shops are three storeys with a dormer-style top floor in the roof; east-side shops are two
  storeys with an upper bay window and pitched roofs, not flat commercial.
- **"Can you improve the process, not do a spot fix."** Hence v2 changed the generic rule, and the
  structural points became follow-ups 5-9.

High street v2, the owner's words: "it's better. but the diagonal awnings on the admiral side don't touch
the wall and signs hovering in space. door near didos isn't framed fully. admiral is just a mess and has
shop windows in the wall down the alley." Three faults:

1. On non-axis-aligned fronts (the Admiral side), awnings are anchored off the wall and name signs float.
   The cause is the same as "square the fronts": placing items one block out from a stepped face.
2. The door near Dido's is not fully framed (the jamb rule assumes a straight run either side).
3. The shop rule was applied to side and alley walls of the Admiral building (shop windows down the
   alley): the "nearer the street line" test accepts side walls that angle toward the road.

Car-park finding (orchestrator, from reviewing the world): the bay lines in 50,-35 were painted onto a
ghost-only cell. `car_park()` clipped to a box spanning two cells and checked only the approved-cell
list, but bay lines are ground-layer work, and 50,-35 had no ground pass (approval there covered the
ghost and the buildings crossing into it). Follow-up the same day (owner: "Run ground pass"): before it, 50,-35
held 558 white blocks from the v2 dressing (557 bay lines on ghost stone at y 26-29, one fascia block); v1 had
been undone. A stock ground dry run planned only 2,642 columns, because 7,344 read as empty below the y=31
probe floor (#315). The low-floor run planned 9,986 (tarmac 2,060, paving 3,493, forecourt 1,283, grass 2,728)
and landed with 0 mismatches (journal seq 1341, LIDAR_GHOST_RING=0, 50,-35 only). The bay lines did **not**
end up on tarmac: of 625 line blocks, 378 touch grass, 192 sit in tarmac and 55 in paving. The ground pass
does not read `amenity=parking` as a surface (the #158 rule keeps land use out of ground paint), so the car
park came out as aerial-coloured grass and tarmac patches, and the grid-aligned bays run across both. The owner
judged the bay lines "a muddle" that "don't match the real car park" and noted there is no grass in the
aerial photo; instruction "Remove lines, file both" (the orchestrator filed the snags; task #336 covers a
real car-park layout). The 557 bay-line blocks were replaced with tarmac (`gray_concrete`) as the journalled
follow-up `w1480342683_carpark_lines_removed` (0 mismatches). 50,-35 now holds 69 white blocks: the ground
pass's 68 road markings and one shop-fascia block. The grass patches were left for their own snag. Rule for the productionised version: dressing that writes
ground-layer things (markings, surfaces) must have a real ground pass under it or refuse, and cell
approvals must say which layers they cover.

## 9. Follow-up tasks to file

For the orchestrator to file in `tasks/` (this session did not run `tasks.mjs`). Ordered by value.

1. **Ground/trees/decor surface probe ignores land below y=31** (bug, section 5.1). Scan to the world's
   minimum Y, or to lidar terrain minus a margin; same for `PROBE_Y_RANGE`; a test with ground at y=14.
   The trial #259 session started a side session "low-ground-probe" after this trial reported the bug; check
   it before filing.
2. **Group feature plans: one structure, many mapped units** (5.2). A grouped plan id with a refs list for
   runs of adjoining `building=industrial|retail` ways (like houses by street), so one spec covers a shed or
   a parade and readiness counts every ref.
3. **Cross-cell builds re-prepare touched neighbours** (5.3; extends #232). Before a feature apply, refresh
   each touched neighbour's evidence check, or stop a cell's own landed feature from staling its
   neighbour's evidence.
4. **Ghost frontier stays inside approved cells** (5.4). Make the ring opt-in for named-cell work, or report
   the cells it will ghost before the apply.
5. **Commercial and industrial street-face defaults in the builder** (sections 3-4, 7). Productionise the
   prototype as class-keyed facade rules: front columns by distance to the mapped street; industrial
   shutter + fascia + bay marks and no storey window grid; shop glass + door at pavement + fascia +
   contrasting wrapped sign + continuous awning by shop kind; big-box glazed base + fascia + roof
   greeblies from the aerial.
6. **Square shop fronts on rotated parades** (owner). Regularise the street face to straight runs before
   placing glazing, doors and awnings.
7. **Shop typology = terrace-house body with a shopfront ground floor** (owner; feeds #257). Storeys and
   dormers from lidar eave and ridge, pitched roofs, upper bay windows; reuse the terrace builder.
8. **Street furniture for commercial streets** (owner; with #260). Traffic signals and crossings from map data
   (`highway=traffic_signals`, `crossing`), lamps, bollards, bike racks, traffic islands; a density per class.
9. **Landmark street-level step** (owner). For civic/church/cinema/shopping-centre classes, nominate as tier A
   and require one street-level check (owner-authorised imagery) before the build.
10. **Car-park markings along the aisle** (parking class, done in #336, spacing fixed in #442). Bay rows are
    oriented to `service=parking_aisle`, not the block grid, and drawn by the ground layer
    (`lib/layers/bay_lines.py`). The pitch is measured along the whole aisle, an aisle marks only a car park its
    centreline enters, and the outer wedge of a bend — or any step that cannot sit on one straight row — gets
    no lines.
11. **Ground-layer dressing needs a ground pass, and approvals name layers** (orchestrator finding,
    section 8). Markings and surfaces refuse on a cell with no landed ground; a cell approval records which
    layers it covers (ghost, buildings crossing in, ground, dressing). Contract (#327): ground-layer
    markings must be written through `Plan.ground_marking` / `Plan.ground_marking_fill`, which flag the
    plan so `site_kit.apply` makes the journal refuse it (nothing written) unless every cell it writes
    into has a landed ground pass; plain `plan.set` is not gated. Consumers: car-park bay lines
    (#336, item 10 here) are drawn by the ground pass itself, so they sit outside this gate. Approvals are written with `cell_registry.py approve <cell> --layers ...` and
    never stand in for the ground pass. The 50,-35 run also shows that a mapped surface car park
    (`amenity=parking`, `parking=surface`) needs a tarmac surface rule before markings make sense; that is an
    exception to the #158 "land use never paints surfaces" rule, so it is the owner's call.
    Done (#338): the rule already existed in `ground_sources`; the grass came from `classify` turning aerial
    lawn, brick, shade and tinted paving inside the polygon to grass. That override now needs a sharp registered
    aerial; recorded as the narrow #158 exception in `rules/craftsmanship.md` and `docs/adr/006-land-use-classes.md`.
12. **Shop rule v2 faults** (owner, section 8): anchor awnings and signs to the wall on non-axis fronts
    (depends on 6); frame doors on stepped faces; apply shop fronts only to the street face, never to side
    or alley walls.
13. **Big-box roof from the aerial** (owner, section 8). Roof form and PV layout should follow the aerial
    (long PV rows on a flat light roof at Westquay South); the prototype's central grid of detectors did
    not match.

## 10. World side effects

All in `southampton-build` (world repo and save), none on the tool's main branch:

- Prepared (full): 55,-41, 50,-36, 43,-16, 50,-35, 51,-36. Prepared evidence-only: 42,-16. Each got a local
  map extract under `reference/vector_map/prepared-*` (no manifest row added) and a lidar ghost.
- Ghost ground laid by the first apply's frontier (problem 4): 53,-39, 54,-39, 55,-39, 56,-39, 57,-39,
  57,-40, 57,-41, 57,-42, 57,-43.
- Feature builds (journal owner `trial258`): `w241498208`, `w241499737`, `w241497621`, `w160637268`,
  `w149814013`, `w149814666`, `w1480342683`, `w465660355`; ground passes `highfield_55_m41_ground`,
  `highfield_50_m36_ground`, `highfield_43_m16_ground`; dressing follow-ups `w241498208_dressing`,
  `w160637268_dressing_v2` (v1 `w160637268_dressing` undone and its plan dismissed), `w465660355_dressing`.
  All read back with 0 mismatches. The high-street dressing painted car-park bay lines onto ghost ground in
  50,-35 (section 8); on the owner's instruction they were replaced with tarmac by
  `w1480342683_carpark_lines_removed`, and 50,-35 then got its own ground pass (`highfield_50_m35_ground`). Specs are in
  `site/builds/`. Each is undoable with `element_undo.py`.
- Unit plan ids covered by a grouped spec were dismissed with the reason "built inside grouped trial #258
  spec <id>" (problem 2). Units left out of the windows stay planned.
- Trees and decor were not run (no shed/shop planting or furniture question in this trial; about 16 min a
  cell). The cells are therefore not DONE in the registry; `next` will offer their remaining features and
  layers. Hold them if the owner wants the trial state kept as is.

## 11. Task 330 implementation and validation (2026-11-20)

Shared industrial and big-box defaults landed in `e748d68` (including its preceding
implementation and fixes), integrated into main through `d7dc48d`. Industrial fronts get
shutters, named-colour fascia and road-bounded loading bays; large retail fronts get a glazed
base, fascia and an attached mapped-name sign. Explicit facade plans take precedence.
Roof recipes are outside this change. Westquay's unsourced trial glazing rows were removed
from its world-owned spec so the shared defaults actually drive the rebuild.

Normal locked journal undo/apply rebuilt all four recorded specs without the prototype:

| Spec | Cells | Landed journal sequence | Readback mismatches |
| --- | --- | --- | --- |
| `w241498208` | 55,-41 | 1386 | 0 |
| `w241499737` | 55,-41 | 1388 | 0 |
| `w241497621` | 55,-41 | 1392 | 0 |
| `w465660355` | 42,-16; 43,-16 | 1396 | 0 |

The corner loading bay originally reached the mapped carriageway in 55,-40; the corrected
shared rule stops at road widths and continuous forecourt, and the final plan stays in 55,-41.
One Belgrave cycle-shop unit is mapped retail and retains small-shop behaviour, rather than
being reclassified as industrial.

The large Westquay undo exposed repeated decoding of an oversized prior snapshot. A
synthetic cache-budget regression caught it; undo now reads each required prior once without
raising the bounded cache limit. The original live undo then completed in 50.714 seconds;
the rebuild completed in 178.089 seconds. Focused suites, full pre-commit suites and fresh
read-only reviews passed. The owner accepted both rebuilt examples in game: Belgrave
("looks good") and Westquay ("yes"). Task 330 is complete; the roof remains outside its scope.
First-person captures were deferred because another active build held the world lock.
