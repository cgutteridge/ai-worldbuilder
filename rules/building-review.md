# Building review

Read when reviewing a built building (I-064 / I-069 items, or the user asks).
One building per review. Skip any building that already has an open feature issue (it is rebuilt there; user, 2026-10-16). Worked example: Oakdene Court, `site/REVIEWS.txt` 2026-10-16.

1. **Read the data, not the label.** Lidar height row (`site/cells/<cell>-LIDAR.txt`), `lidar_audit.py --cell`, and the raw
   height grid of the building. Labels (flat/pitched, storeys) are estimates until the grid and a photo agree.
   Done when you can say what lidar shows for shape, top Y and floor level.
2. **Gather evidence in `rules/evidence-order.md` order.** For a university building run `scripts/portals.py cell tx,tz` first: it
   gives every entrance's exact position and name (a canopy or door found on the footprint map / the build may be one of them: 42-A, 2026-10-16). One street-level photo frame, an aerial sheet
   (`google_aerial.py`), and a university aerial that covers the building (check `bbox_mc`, `rules/reference-token-budget.md`).
   Log each with `cell_sources.py --record`. Done when storeys, roof and openings each name a source.
3. **Compare the live build with the photos side by side.** Game capture from the photo's camera, a top-down capture beside the
   aerial. List matches and mismatches. A build that matches lidar and the footprint map can still be wrong (Oakdene: right plan and height,
   wrong roof colour and windows). Done when each mismatch is named.
   A review run of buildings uses all three: aerial sheet (greeblies, roof colour), the university aerial (footprint, roof form) and
   lidar (heights: plinth, plant and wing heights from national lidar composite DTM/DSM via `lidar_heightmap.py`, never guessed).
   Greeblies are the decor step of a feature (`rules/craftsmanship.md`): unusual blocks welcome, plants matched by colour and size.
   Roof levels and their extents come from the lidar cell by cell (3x3 majority filter, rim cells take their neighbour's level), never
   from boxes drawn on a picture; the aerial sheet decides colours and greeblies, not heights (user, 2026-10-16: "you ignored the lidar").
   Before painting or adding to a roof, print the live columns (top block and Y per cell) and compare with the lidar grid; a
   later build can have left pits, holes or lidar-rim bands (SU retail: a 5-wide pit round the north edge, 2026-10-16).
   The aerial sheet shows roof greeblies (user, 2026-10-16): plant boxes, vents, louvred vents, skylights, solar panels, lift overruns, parapets. List
   them per roof in this step and write them into the feature spec `details` table
   (`rules/feature-builds.md`); lidar often misses small roof plant — aerial is the source for those.
   Facade greeblies from street photos (drainpipe, sign, canopy, ledge, fascia band) go in the same table.

   From the same aerial (and street level when the cap shows), answer the roof-edge questions already on the designer
   list: edge on the roof? what block? outer only or steps too? Default is none — set `roofs[label].edge` only when
   the photo shows a parapet, coping or flat overhang fringe (`rules/feature-builds.md`, `rules/evidence-order.md`).
4. **Keep or rebuild.** Keep when nothing in the list matters at street scale (`rules/radial-good-enough.md`). Rebuild by
   reusing the cell's builder (`scripts/oakdene_court.py` is the pattern), clearing only the build's own recorded blocks, so
   trees stay. Claim the lock, dry run, apply with `SITE_OWNER`. Done when readback is 0, the footprint check is OK and doors reach.
5. **Check with the user.** When a fix is `fix-landed`, walk it with the `snag` skill review path
   (`scripts/issues.py review` / `verdict`). Steering replies loop back to step 4 and become rules
   (`rules/craftsmanship.md` §2). Only the owner's verdict confirms the issue.
   A feature build changes the building's own cells only: no landings, steps, paving or plants outside the footprint (ground,
   trees and decor are other passes); if a door needs steps, queue it for the ground pass (user, 2026-10-16).
   Fiddling a block or two to make a build sit well is fine (user, 2026-10-16); what is not fine is stacking follow-up fixes.
6. **Record.** Issue state with the user's words, a REVIEWS entry (3 matches, mismatches, token notes), the general lesson in
   the rule file that owns it, commit.

## Pitfalls from the B42 / Jubilee review (2026-10-16)
 - **Numbers before pictures.** Before showing the user, print live-minus-lidar per cell over the building
   (`lidar_audit.py --grid x0,z0,x1,z1 --min 2`; crosses cell edges via `lidar_heightmap.top_at`, B42 at z -5505) and fix every
   cell off by 2 or more. Every visual complaint in that review (3-high strip, pillars, grey triangle) was already a number.
 - **Extents from cells, not drawings.** A shape read off a picture (box, polygon) is a hint for where to look. Lowered roofs, colour
   patches and membrane areas are defined by lidar cells of the feature's own part, so a colour cannot spill onto a neighbour and a
   rim cell cannot stand up as a pillar. Edge (coping) cells are never recoloured.
 - **Follow-up steps of a feature** (`--lower`, shared `MODIFIES`, `site_kit.reset_to_base`, never `built_cells()`):
   roof and facade greeblies live in the feature spec `details` table and rebuild with the
   building (not a separate `--greeblies` flag). See GREEBLIES in `rules/craftsmanship.md`.
   Re-run steps rather than patching by hand.
 - **One defect is a class.** When the user finds one (a door with no jamb, a trench from the door generator, a canopy from a vector-map
   `building=roof` tag that aerial does not show), fix it, then add the guard to `site_kit.apply` or the generator and run an audit over
   everything already built (`scripts/door_audit.py`: 142 of 700 doors unframed, issue / next-task Q-182 era). No evidence in aerial or lidar for a
   footprint micro-feature means remove it.
 - **Ask what is really there.** For entrances, steps and levels the user's knowledge beats every source here. Ask them to stand
   where it is wrong; read their position (`player_list_online`) and look (`view_capture`), say what you observed, then act.
 - **Do not move the user.** Observe with `player_list_online`, `view_capture` and `block_render_region` (server-side, no client).
   Teleport only on request (the user said "you moved me" and "don't move me"). `site_kit.apply` still moves them when they are
   more than 80 blocks from a build, but puts them back exactly where they were afterwards (f4c536c).
   Every player move goes through `scripts/mc_player.py` (scripts import `move_player`; agents use the CLI) with a non-empty
   `--why` / `why=` naming the feature, pass, or place — Minecraft chat shows `[AI-builder] …` before the hop. Do not call
   raw MCP `entity_teleport` / `player_set_gamemode` for the player.
 - **Aerial sheet.** `google_aerial.py sheet` (and `google_window.py`) download tiles to a PNG under `instance/google-aerial/`;
   `Read` that file. `--marks cells.json` paints live cells (tile, membrane, colour patches) on the sheet.


## Reflection on the 48,-44 review (2026-10-16): what went wrong and the approach we keep
What cost the most time, in order: (1) building from part of the evidence and being corrected (a whole building black from three frontage photos; tall
1x2 window slots; doors chosen by habit; a pavilion bigger than its photo; floating balustrades); (2) stacking follow-up patches on a
building instead of rebuilding it (a dozen plans on B42 made pits, pillars, holes and clashes); (3) scripts that read the world they
were changing, so a re-run chose different spots (entrances); (4) judging from previews and renders when the user's own view or a number
would have settled it.

The approach:
1. **Evidence table first, in the script docstring**: for each fact (footprint, heights, materials per face, doors, roof features) name the
   source: vector map footprint, lidar grid cell by cell, aerial z20, campus aerial, every portal photo at 800 px (`scripts/portals.py`), block reference for the
   block choice (`scripts/block_reference.py`). All relevant references, before the first block. A photo shows ONE face: say which.
2. **One building = one script** (lidar bands -> per-zone materials -> doors from the portal register -> clear the old build -> keep trees).
   A second fix to the same building means rebuild it whole, never another follow-up. Per-face rules need positions as well, because a
   stepped outline has steps facing every way.
3. **Re-runnable**: plan from footprint-map polygons, lidar and recorded states, never from blocks the script itself placed; to re-run, put back
   the previous own record first (restore, then plan).
4. **Check with numbers, then show**: live-minus-lidar grid, `scripts/airtight.py`, door frame guard, footprint guard; then the user's
   own view (read their position, capture, never move them). Renders are rough shapes, not colours.
5. **Only the building's own cells**: ground, steps, trees and plants are other passes; queue what a door needs.
6. **Simple**: few big features that read at street scale (solar array, fans, fascia), not individual vents and seams.
Open items: the Prayer Room corner of the West Building (Q-quoted in the queue), the facade of the other B40 faces, B38/B40 are still the
older highfield_susu_west builds and should get the same one-script rebuild.


## Rebuild beats patch: SU retail B57 (user, 2026-10-16: "much much better; starting over is often much cheaper")
The retail had been fixed five times in a row (front turned, glazing redone, door moved, canopy shifted). Deleting it and rebuilding from the
evidence table alone, in one pass, got it accepted at the first look. What made the difference:
1. **Do not read the old build or its script as a reference** (user steered mid-task). They carry its wrong guesses forward. Keep only the
   mechanism that restores its own recorded blocks (re-runnable rule), plus lidar-derived levels. A redo starts from the table in
   "Reflection on the 48,-44 review" step 1. Third fix on one building = delete and restart, not patch.
   **Many new references since the feature was last worked on = start over too** (user, 2026-10-16): when photos, portal entries, outlines or
   aerial reads have piled up since the last pass, the old build rests on a thinner evidence table; delete it and rebuild from the current one
   rather than patching, even if no single fix has been made yet. Same logic as the Library Square trees: the aerial redo replaced the whole zone.
2. **The footprint-map outline of a canopied building is the ROOF outline.** The old build put the walls on the outline and the canopy outside it, so it
   came out ~4 blocks too big. Walls stand where the glazing is: the aerial sheet shows an upstand line where canopy meets wall (here 3.8 in from the edge),
   and the portal sits behind the roof edge, not on it. Check portal-to-outline distance before choosing the wall line.
3. **Work in the building's own frame.** Fit the footprint (PCA of its cells: centre, long axis, angle), compute (along, across) for every cell,
   and write per-face rules on that: face = nearest rectangle side, front rhythm = a table by `t` across the face, roof features measured
   as (along, across) rectangles and lines off the aerial sheet. A 24-degree rectangle then needs no per-direction exposure rules.
4. **One building = its own `shells()`** when it is not a plain lidar-band part (open canopy, set-back walls): only write the cells you mean,
   keep the Part over the whole footprint so clearing, deck and greeblies still work.
5. **Entrances on a stepped front**: no straight stretch is long enough for a normal door cut. Take two flat (single-exposure) treads cells
   nearest the portal, steel jambs at both ends, the sign band as the lintel, open passage.
6. **Numbers then pictures**: live tops vs lidar, no air in exposed wall columns, then the user's own view. Iso render (`block_render_region`)
   is useful; its `front`/`side` views are flattened projections and mislead.
7. **Practical**: `Read` the aerial PNG from `google_aerial.py sheet` (one image, no browser).
   Before teleporting the user, check the target is standing room (air at y+1, y+2) with `World`: the first spot I picked was inside another
   building.
