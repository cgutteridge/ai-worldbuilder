# Building review

Read when reviewing a built building (I-064 / I-069 items, or the user asks).
One building per review. Skip any building that already has an open feature issue (it is rebuilt there; user, 2026-10-16). Record the worked example in the world's `site/REVIEWS.txt`.

## Landmark street-level check (tier A)

Church, cathedral, chapel, cinema and shopping-centre candidates (`building=church|cathedral|chapel|cinema`, `amenity=cinema`, or `shop=mall`) are landmarks. `propose-features` marks them with `"landmark": true` and a `landmark_class`. Do not build one until a street-level image the owner supplies is recorded for the cell (`python3 scripts/cell_sources.py --cell tx,tz --record SOURCE --result found|partial|none|blocked --note "..."`; SOURCE is a street-level provider id from the cell sources checklist), or pass `--landmark-override "<reason>"` on `feature_build.py --apply` with a non-empty reason. Aerial and tags alone miss facade, steps and canopy detail (a hall mis-tagged as a church).

1. **Read the data, not the label.** Lidar height row (`site/cells/<cell>-LIDAR.txt`), `lidar_audit.py --cell`, and the raw
   height grid of the building. Labels (flat/pitched, storeys) are estimates until the grid and a photo agree.
   Done when you can say what lidar shows for shape, top Y and floor level.
2. **Gather evidence in `rules/evidence-order.md` order.** When the catalogue configures `entrance_register`, run `python3 shims/portals.py --world <world> cell tx,tz` first: it
   gives every entrance's exact position and name (a canopy or door found on the footprint map or the build may be one of them). One street-level photo frame, an aerial sheet
   (`google_aerial.py`), and a second aerial the world configures when its `bbox_mc` covers the building (`rules/reference-token-budget.md`).
   Log each with `cell_sources.py --record`. Done when storeys, roof and openings each name a source.
3. **Compare the live build with the photos side by side.** Game capture from the photo's camera, a top-down capture beside the
   aerial. List matches and mismatches. A build that matches lidar and the footprint map can still be wrong (right plan and height,
   wrong roof colour and windows). Done when each mismatch is named.
   A review run of buildings uses all three: aerial sheet (greeblies, roof colour), a second configured aerial (footprint, roof form) and
   lidar (heights: plinth, plant and wing heights from national lidar composite DTM/DSM via `lidar_heightmap.py`, never guessed).
   Greeblies are the decor step of a feature (`rules/craftsmanship.md`): unusual blocks welcome, plants matched by colour and size.
   Roof levels and their extents come from the lidar cell by cell (3x3 majority filter, rim cells take their neighbour's level), never
   from boxes drawn on a picture; the aerial sheet decides colours and greeblies, not heights (user, 2026-10-16: "you ignored the lidar").
   Before painting or adding to a roof, print the live columns (top block and Y per cell) and compare with the lidar grid; a
   later build can have left pits, holes or lidar-rim bands (a retail block left a wide pit round one edge).
   The aerial sheet shows roof greeblies (user, 2026-10-16): plant boxes, capped box vents, louvred vents, recessed skylights, hollow industrial chimneys, solar panels, lift overruns, parapets. List
   them per roof in this step and write them into the feature spec `details` table
   (`rules/feature-builds.md`); lidar often misses small roof plant — aerial is the source for those.
   A roof detail sits on solid roof; cells over air are clipped. Check the dry-run
   clipped-cell report against the roof outline before rebuilding. Skylights
   replace the roof-top layer and reject the entire box if any cell is off roof.
   Vents are concrete boxes with trapdoor caps, never bar poles. Industrial
   chimneys are hollow open stepped rings, vertical or tapered; photo/spec
   dimensions win and lidar is only a rough check for these hollow stacks.
   Facade greeblies from street photos (drainpipe, sign, canopy, ledge, fascia band) go in the same table.

   From the same aerial (and street level when the cap shows), answer the roof-edge questions already on the designer
   list: edge on the roof? what block? outer only or steps too? Default is none — set `roofs[label].edge` only when
   the photo shows a parapet, coping or flat overhang fringe (`rules/feature-builds.md`, `rules/evidence-order.md`).
4. **Keep or rebuild.** Keep when nothing in the list matters at street scale (`rules/radial-good-enough.md`). Rebuild by
   reusing the feature's recorded builder and spec, clearing only the build's own recorded blocks, so
   trees stay. Claim the lock, dry run, apply with `SITE_OWNER`. Done when readback is 0, the footprint check is OK and entrances match ``facade_plan``.
5. **Check with the user.** When a fix is `fix-landed`, walk it with the `snag` skill review path
   (`scripts/issues.py review` / `verdict`). Steering replies loop back to step 4 and become rules
   (`rules/craftsmanship.md` §2). Only the owner's verdict confirms the issue.
   A feature build changes the building's own cells only: no landings, steps, paving or plants outside the footprint (ground,
   trees and decor are other passes); if a door needs steps, queue it for the ground pass (user, 2026-10-16).
   Fiddling a block or two to make a build sit well is fine (user, 2026-10-16); what is not fine is stacking follow-up fixes.
6. **Record.** Issue state with the user's words, a REVIEWS entry (3 matches, mismatches, token notes), the general lesson in
   the rule file that owns it, commit.

## Lessons that stay with every review

Dated case notes for one place live in that world's docs, not here.

- Print live-minus-lidar per cell before showing the owner, and fix every cell off by 2 or more. A shape read off a picture is only a hint for where to look. Extents come from lidar cells of the feature's own part.
- Follow-up plans set `plan.parent_feature` to their built base and list that parent in `modifies`. Roof and facade greeblies live in the feature spec `details` table. Re-run the build rather than patching by hand.
- One defect is a class: fix it, add the guard, then audit what is already built. No evidence in aerial or lidar for a footprint micro-feature means remove it.
- For entrances, steps and levels, ask the owner to stand where it is wrong. Observe with `player_list_online`, `view_capture` and `block_render_region`. Teleport only on request. Every player move goes through `scripts/mc_player.py` with a non-empty `why`.
- Read the aerial PNG from `google_aerial.py sheet`. Do not read an old build or its script as a reference. A third fix on one building, or a much richer evidence table since the last pass, means delete and rebuild.
- The footprint-map outline of a canopied building may be the roof outline. Fit the footprint and write per-face rules in the building's own frame. A feature changes only the building's own cells.
- Evidence table first, in the script docstring: each fact names its source. One building is one rebuild. Check with numbers, then the owner's own view.
