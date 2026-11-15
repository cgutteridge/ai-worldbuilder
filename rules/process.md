REFINE PROCESS — HOW A PLACE BUILD IS RUN
Tool-repo rules (ai-worldbuilder/rules/). Derived from the Newtownton
process (Minecraft Magic), stripped of districts / vibe / whimsy.
Goal: real-world accuracy. Place memory stays in the active world's site/.

------------------------------------------------------------------------
1. WHERE THINGS LIVE
------------------------------------------------------------------------
 registry/journal.jsonl  cell journal (SoT); progress/ghost/ownership are views
 scripts/cell_registry.py  next / status / hold / verify / reconcile
 site/NOTES.txt         overview, accuracy rules (active world; read first)
 site/ (optional legacy lump-notes file when the world has one; not the live lidar path; heights = national composite)
 rules/process.md       this file (tool repo): cadence, reviews, checklists
 rules/rules-context.md which rules/docs to read per work context (anti-reread)
 rules/*.md             craft and process rules in the tool repo (AGENTS.md entry)
 site/COVERAGE.txt      radial focus + generated rollup (coverage.py write;
                        registry-native + optional parcel tables)
 config/coverage-areas.json    areas tracked by scripts/coverage.py
 registry/issues.json   live snags / issues (scripts/issues.py; never block a cell)
 registry/issues-closed.json   archived confirmed/wontfix issues; pictures in issues/img-closed/
 site/REVIEWS.txt       review log
 site/BUILDS-INDEX.txt  generated list of kit builds (scripts/site_index.py)
 site/archive/          closed notes / older reviews
 site/LOCK.json         who holds the live world (scripts/site_lock.py)
 scripts/site_kit.py    plan / preview / apply / readback (journals via CellRegistry)
 scripts/site_recover.py  land or roll back ONE interrupted apply
 scripts/world_frame.py CRS / OSGB / WGS84 / MC / cells / WM tiles (canonical)
 scripts/osgb.py        thin wrapper → world_frame (legacy OFFSET_* shape)
 scripts/geo.py         WGS84 + probe Y + slopes + survey bboxes (see README)
 verification/site/     <build>-backup/-cells/-result/-preview, views/, audits/
 reference/photos/      ground photos (local, gitignored content)
 reference/aerial/      aerial / ortho crops (local, gitignored content)
 reference/LOGGING.txt  how to log refs (dedupe, token budget, privacy)
 reference/manifest.jsonl  append-only index of every fetched asset
 config/world.json      gameworld_id + save_path (save lives in the standalone Minecraft instance; read-only to the tool)
 config/world-frame.json         CRS, offsets, Y offset, bounds (sole home; not in the save)

 Retired progress files (do not recreate; `cell_registry.py verify` fails if
 present): old queue / focus / holds / retired coverage-done files / aerial-review /
 handoff pending files under site/. Wipe via:
 python3 scripts/dryrun_reset.py --confirm-logged-out

Naming: builds are <street>_<numbers>_<thing> for properties (e.g.
broadlands_rd_20_22), <place>_<thing> otherwise. Modifications
of a built thing get a new name and declare `modifies=()`. Never edit a
BUILT plan in place to “fix” the world.

------------------------------------------------------------------------
2. THE CADENCE
------------------------------------------------------------------------
 Every build phase   frontage + plots survey (osm_footprints.py frontage /
                     plots; describe photo + aerial in 3–5 lines) → plan → preview → dry-run
                     guards (footprint + roof ridge-jog on the plan in site_kit.apply)
                     → apply → readback → walk → osm_footprints.py check →
                     python3 scripts/roof_lint.py --all (recorded *-cells.json;
                     must exit 0 after pitched-roof builds) → client view from the
                     photo's camera beside the photo + top-down beside the aerial
                     (contact sheet) → notes.
                     Craftsmanship (rules/craftsmanship.md): airtight shell,
                     flanked glass, squared footprint, roof pitch/overhang —
                     reject lattice/carpet roofs and jagged 1:1 footprint walls in
                     preview, not after apply.
                     Readback/walk/patrol = blocks match the PLAN; only the
                     photo/footprint comparison shows they match THE REAL PLACE.
 Stop rule          a new build that needs a fix build for its own geometry
                     (blocked doors, wall in road) → revert + re-survey; never
                     stack _b/_c patches (I-009).
 Every session start GLITCH PATROL when refining built cells:
                     scripts/site_patrol.py sweep (+ views N when useful).
                     Log in REVIEWS.txt; file snags via scripts/issues.py.
 Every 3 phases      scripts/site_audit.py: drift from archived cells,
                     dark walkable cells. Log REVIEWS; file snags.
 After a place done  FULL REVIEW of that place against photos / aerial.
                     Fix or schedule high issues before starting the next.

------------------------------------------------------------------------
2a. UNDO AND REPAIR (no region backups, ADR 004)
------------------------------------------------------------------------
 Region-file backup and restore were removed (ADR 004, task 40). Do not copy,
 snap or restore .mca files by hand.

 1. Kit builds: site_kit structure backup (verification/site/*-backup.json)
    stays the undo for planned kit applies; `site_recover.py` uses it for an
    interrupted apply.
 2. Mask undo for the layered passes (trees, decor): scripts/mask_undo.py.
 3. Mess in a cell (blocks drifted from the journal): 
    `python3 scripts/cell_registry.py reconcile [tx,tz] [--fix]`.
 4. Interrupted apply (journal entry still `begun`):
    `python3 scripts/site_recover.py status` then `recover <tx,tz> --apply`.

 Always `server_save` / save the world before reading region files so they
 match what you think you are editing.

------------------------------------------------------------------------
2b. REFERENCE PASS (before / beside builds)
------------------------------------------------------------------------
 Follow reference/LOGGING.txt:
 - grep manifest before any download;
 - append one JSONL line per new file; set useful once;
 - open mosaics/contact sheets first; full-size Read only for a named fact;
 - never re-Read useful=no assets.
 Scope: houses (all sides, roof, floors + stair) and streetscape; gardens come
 from the ground/trees/decor passes. Street photos for fronts; aerial for
 roofs, rears and back gardens; same-terrace hints labelled `hint`. Interior
 rules: rules/frontage-and-evidence.md.

------------------------------------------------------------------------
2c. SPATIAL STRATEGY — RADIAL, NO GAPS
------------------------------------------------------------------------
 Rebuild ALL of an area. Do not leave weird unprocessed holes.

 Origin (current focus): Highfield campus centre — see site/COVERAGE.txt
 (OSGB E442400 N115200 → MC X6152 Z-5592; home cell tx=48 tz=-44).
 Expand outward in Chebyshev rings.

 128×128 MC cells (aligned with sparse mc_tiles), ring order per
 rules/radial-good-enough.md. When a session ends mid-cell, leave IN FLIGHT in
 COVERAGE.txt.

 PARCEL COVERAGE (Q-038, scripts/coverage.py): every block column of an
 area (config/coverage-areas.json) is exactly one parcel — property (vector-map
 footprint + grown garden), road, path (rear lanes / alleys / twittens),
 open (landuse / leisure / amenity polygons), gap (residual: verges,
 corners, unlabelled ground, edges of special areas), no_osm (no clip).
 Touched = columns in verification/site/*-cells.json (reverted I-009
 builds excluded). DONE = registry marks the cell DONE (layers + features + no open warnings),
 or for parcel tables 100% columns touched. Sign-offs are retired (#31).
 Leftover unowned structure (carpet roof, exposed stone band / undercroft, stock
 pavement in gardens, or any exposed block above the build floor that no build
 wrote) still blocks a clean parcel row — write it in the plan to keep on purpose.
   After every build:  coverage.py write                 (registry + parcels)
                       coverage.py gaps --area <a>       (gaps + 1-3 wide seams)
   Before planning:    cell_registry.py next / status --cell tx,tz
                       coverage.py status --cell tx,tz --list
                       .venv/bin/python scripts/coverage.py map --area <a>
   New ground / map:   coverage.py build [--area A --cells tx0,tz0,tx1,tz1]
 The generated block in COVERAGE.txt is written by coverage.py — never hand-edit
 it. Chat memory is not enough; the registry + rollup prove what was processed.

 Revisits of processed ground: see rules/radial-good-enough.md.

------------------------------------------------------------------------
2e. CELL CADENCE (128² active patch, layered passes)
------------------------------------------------------------------------
 One ACTIVE CELL at a time (registry marker up), on the lowest incomplete
 ring. Cell done = registry DONE (rules/radial-good-enough.md), not perfection on every shed.
 Pass order and undo: rules/handoff-layered-passes.md (features, ground, trees, decor).
 Do not stack extra passes over the same ground (I-035).
 Build order (#286): `next` builds a cell's planned features first (by id) on the lidar ghost ground, then the ground once, fitted around them (ground surfaces a feature's air-only margin below that air), then trees and decor; a cell with no planned features gets its ground first.
 Stale ground (#285): a feature landed after the cell's last ground makes the ground stale; `next` offers `ground-redo` with the cause and `clear:` command (redo_layer), never runs it, and the cell is not DONE until it lands.
 Next work: `python3 scripts/cell_registry.py next` (issues beat layers).

 Lidar ghosts: docs/specs/lidar-ghost-v2-and-dryrun-reset.md. After
 `cell_marker --raise`, ring-2 frontier cells get **ghost ground** only (DTM), with
 top colour from DSM−DTM (`light_gray_concrete` ≈0, `gray_concrete` 1–2,
 `black_concrete` >2) so you can check feature location against the footprint.
 There are no volumetric feature ghosts and no ghost-feature strip on apply.
 Real ground supersedes ghost ground. `feature_build` dry-run must still list full
 shell cells (it must not drop columns owned only by ghost ground).

 1. Activate — raise the hazard-stripe marker (REQUIRED, first action on every new
    cell; it sets the registry marker = FOCUS, stays up across chats):
    `python3 scripts/cell_marker.py --raise tx,tz`.
 1b. Sources pass (once per new cell, before the build batch). Goal: a durable guide,
     not a token bonfire in every later chat.
       python3 scripts/cell_sources.py --cell tx,tz --write
     → site/cells/<tx>_<tz>-SOURCES.txt (committed). Then: fetch any missing aerial
     mosaic / street refs listed there; `ref_manifest.py add` with useful=yes/maybe;
     fill CHARACTER + TOKEN NOTES in the file; judge imagery once (mosaic/contact
     sheet, not dozens of full-size photos). One good source is enough: use it and
     build; do not hunt for a perfect one. Street View via the fixed pano-URL +
     thumbnail-URL method is logged in reference/SOURCES.txt (Street View: download PNG contact
     sheet via streetview_sample; kept frames go to gitignored photos).
     SOURCE CHECKLIST in that file lists checklisted provider ids for the cell (from place-sources).
     Log each attempt, hit or miss:
       python3 scripts/cell_sources.py --cell tx,tz --record SOURCE --result found|partial|none|blocked --note "..."
     Never retry a fresh TRIED source for the same cell without good cause (the
     script refuses unless --retry-reason is given). Evidence older than ~6 months
     is STALE: `--todo` lists it and `--record` may refresh without --retry-reason.
     `cell_sources.py --cell tx,tz --todo` lists TODO + STALE (exit 1 if any).
 2. Review neighbours — ground, trees and decor read evidence across the cell edge
    (paths, roads, hedges, canopy), so first check neighbours' registry status, masks
    and the aerial across the edge for continuity.
      cell_registry.py status --cell tx,tz
      coverage.py status --cell tx,tz --list ; coverage.py gaps --area <a>
    Light character pass: note distinctive local features as snags (`scripts/issues.py`).
 3. Plan — `cell_registry.py next` picks the next layer or feature (snags never steer next).
 4. Features — each its own build, normal cadence (§2); registry marks built on clean apply.
 5. Layered passes — live flow per cell: claim lock; mask_undo.py (decor then trees) if
    masks exist; ground_layer.py --apply; trees_layer.py --apply; decor_layer.py --apply;
    patrol sweep; clean applies land journal entries (CellRegistry); release lock.
    Dry-run first and check verification/site/<plan>-kinds.png against the aerial.
    Python: .venv/bin/python. Vegetation rules: rules/foliage.md.
 6. Cell complete — registry DONE (layers + features + no open coverage warnings). Only then
    cell_marker.py --clear tx,tz and move on: next cell on the ring, or ring r+1 when the
    ring is done. Cell order: north centre of the ring (north is smaller tz), then clockwise.
    Holds: `cell_registry.py hold`. Snags do not block DONE.

 Focus: the registry marker is the single source of truth for the active cell.
 ONLY cell_marker.py raises/clears it; never hand-edit registry JSON for focus.
 Raise refuses while another cell holds focus or any other cell_marker_*-cells.json
 exists, so there is at most one ring. Moving focus = --clear old, then --raise new.
 `cell_marker.py --status` shows focus + strays; --stray-clear tx,tz removes a leftover
 ring; --adopt tx,tz is a one-off bootstrap for an already-raised ring.

 Cell boundary marker (scripts/cell_marker.py): one flat black/yellow wool square on
 the cell bbox outer edge, ~30 blocks above the highest ground sampled in the cell
 (alternating like hazard tape). Raise replaces air only. Teardown clears *-cells.json
 plus any black/yellow wool still on the cell perimeter. Never a wool colour fill.
 Markers are excluded from coverage touched stats (cell_marker_* builds).

 Before builds in cells outside config/coverage-areas.json:
   coverage.py build --area highfield --cells tx,tz,tx,tz

------------------------------------------------------------------------
2d. GOOD ENOUGH — see rules/radial-good-enough.md (the one home for this rule)
------------------------------------------------------------------------
 Default bar is NOTES §8 (done-bar). Prefer finishing the next contiguous cell over
 polishing one property forever.

------------------------------------------------------------------------
2d-i. NEWER THAN MAP / LIDAR (user rule, 2026-10-15, I-047)
------------------------------------------------------------------------
 Vector map, lidar, aerial and street-level photos come from different years and can disagree. If ANY
 photo source (aerial, look-only streetlevel, shared photo archives) shows a building that is absent
 from lidar or from the footprint map, build it anyway: house only, usual conventions, trace the
 footprint from the aerial and record source + year in the build docstring and a
 journal note on the cell. Check osm_extract.py first for ways outside the map clip.
 Missing footprint feature builds are driven via residual_features
 (empty-world DoD / ADR 003).

 LIDAR IS A DATED SNAPSHOT TOO (user, 2026-10-16). Lidar can disagree with newer (or older)
 photos on footprint, height or roof (extension, new roof, demolition). Lidar stays the
 default for ground / eaves / ridge Y; where a photo clearly shows a later change, follow
 the photo and record source + year and the lidar mismatch in the build docstring and a
 journal note on the cell. Do not "fix" the photo to match lidar.
 Some lidar products filter 1-2 m features (cars, street furniture, bins, signs, low walls)
 as noise; national lidar composites are noisier — strip transitory hits, keep street furniture where
 practical (`rules/evidence-order.md`). Absence from lidar proves nothing for them; add them in
 the decor phase when reference materials show them.

 PRIORITY RULE OF THUMB (user, 2026-10-16). Code / tooling changes come before most other
 tasks. Fixing existing things comes before new builds. Order a session's pick: code
 changes, then fixes to what is built, then new builds / new cells.

------------------------------------------------------------------------
3. FULL PLACE REVIEW CHECKLIST
------------------------------------------------------------------------
 Integrity   audit drift, readback holes, floating attachables, broken
             multi-block items (doors, beds).
 Footprint   plan matches surveyed / aerial outline within ~1–2 blocks after
             Minecraft squaring (footprint-map shape is a prior; craftsmanship:
             rules/craftsmanship.md). Legacy lump notes live in the active world site/.
 Frontage    house fronts on the footprint line (osm_footprints.py
             check = OK); doors face their own street; names = real addr:street.
 Height      lidar Y for massing; photos for storeys / relative eaves.
 Roads       centreline/route OK; width/kerbs vs aerial — not stock edges.
 Openings    doors and windows in the right places and sizes (street side);
             glass flanked L/R with sill + head (rules/craftsmanship.md).
 Roof        form from aerial; pitch per CRAFTSMANSHIP §3 (terrace stairs /
             shallow slabs); overhang; sealed skin — not lattice/carpet.
 Materials   palette reads as the real elevations from the public street
             (generator brick/carpet is placeholder only).
 Access      public routes stay walkable; no sealed dark voids on routes.
 Whole       every house: all elevations, roof form, floors + stair. Gardens
             and boundaries are ground/trees/decor (HANDOFF-LAYERED-PASSES §1).
 Privacy     rules/frontage-and-evidence.md (floors + stair only, no interiors).
 Done-bar    meets NOTES §8 per property (not chimney pots / garden furniture).
 Evidence    client view from reference photo camera beside the photo;
             top-down view beside the aerial;
             3 written matches/mismatches in REVIEWS.
 Records     issue notes and reference/manifest match the world.
 Backups     kit structure backup present.

Outcome: each finding → a snag on an issue (`scripts/issues.py` / `snag` skill).
“Replace what does not match evidence” is a valid action.

------------------------------------------------------------------------
4. WHAT MAY BE REPLACED WITHOUT ASKING
------------------------------------------------------------------------
 - Anything built by this kit (verification/site/*-cells.json) may be
   repaired or replaced after a review. Record as a modification build
   with its own backup; log the reason on the issue fix log.
 - Leftover vendor or generator terrain/buildings may be refined when
   evidence shows they are wrong. Back up first. Prefer local edits over
   regenerating wide areas.
 - Still careful with stocked containers (move contents, don’t destroy).

------------------------------------------------------------------------
5. MANY THREADS, ONE WORLD
------------------------------------------------------------------------
 - ONE builder at a time. Claim site/LOCK.json with scripts/site_lock.py
   before any world change or player-moving capture. site_kit.apply()
   refuses without it (env SITE_OWNER must match). Stale after 90 min.
 - Player moves: import `mc_player.move_player(why=…)` from scripts, or
   `python3 scripts/mc_player.py move --why "…" …` from an agent. Why is
   required and is spoken in Minecraft as `[AI-builder] …` before the
   hop. No raw MCP player teleport.
 - Apply path: site_kit.apply() journals `begun` before any live write and
   `landed`/`mismatched` after readback (CellRegistry). An unfinished `begun`
   entry keeps the cell from DONE.
 - Interrupted apply: `python3 scripts/site_recover.py status`, then
   `recover <tx,tz> --apply` (land if world matches plan, else roll back to the
   structure backup). Never with a fresh lock held by someone else.
 - Mess in a cell: `python3 scripts/cell_registry.py reconcile [tx,tz] [--fix]`.
 - Preflight blockers (#284): every `needs-prep` blocker prints `[clear: <command>]`
   (`check --cell` prints `clear:` lines). Run that command; never hand-edit blocks
   or the journal. Mismatched apply: `reconcile <cell> --fix` first. If it says still
   mismatched, re-apply the plan (`reapply_feature.py <plan> --apply`, or `redo_layer.py`
   for a layer). A plan's redo or undo may pass its own mismatch; other plans stay
   blocked until it lands. A mismatch wholly superseded by later landed plans blocks
   nothing. Rule: docs/plans/284-preflight-blocker-commands.md.
 - Work comes from `cell_registry.py next` (issues, then layers/features). Finish the
   task, release the lock.
 - Offline work may run in parallel (design, photo markup, previews of
   region files) but must not apply builds.
 - The files are the memory. Leave nothing important only in a chat.

------------------------------------------------------------------------
6. SESSION START / END
------------------------------------------------------------------------
 Start  User message: "Follow the instructions given by running:
        python3 scripts/cell_registry.py next". The agent runs that command, then
        begins the task immediately; that message is the go-ahead, so do not ask
        "start?". Then site_lock.py status/claim; read rules/rules-context.md
        for the matching context. Check server status and sole MCP client.
        Reference imagery: use files already on disk (manifest). Do NOT ask the user
        to start a fetch daemon at handoff. If a needed Hub tile is missing, either
        continue on street/local refs or give ONE Terminal one-liner
        (`aerial_mosaic.py … --add`), never a chore every session.
 New chat first?  Before a new registry task or live build: if this thread already
        finished an item (lock released) or is dominated by prior build/debug, do not
        silently continue. Tell the user briefly that a new chat is wiser and start
        with `cell_registry.py next`. Stay in-thread only for a short same-item
        follow-up (one patrol fix right after a build they just asked for).
        Chain rule (§7) is the exception.
 Need help  When you don't trust your own judgement (an unclear geometry call, a
        result that does not match the photo or aerial, a risky live edit you can't
        verify), call the user in rather than looping on retries: run `say "I need
        help"` outside any command sandbox, say in one or two lines what needs
        deciding, then stop. Do not chain further attempts.
 End    release the lock; save the world; update ISSUES / REVIEWS / COVERAGE;
        regenerate BUILDS-INDEX;
        python3 scripts/site_cleanup.py due (if it prints CLEANUP DUE: rules/cleanup.md);
        python3 scripts/run_tests.py (stdlib unit tests incl. roof_lint on all
        *-cells.json; same check as CI); then TOKEN NOTES
        (2–5 lines in REVIEWS or place CHANGE LOG: what wasted tokens
        this stage, what to change next — reference/LOGGING.txt §8);
        then hand off: summary in chat + creative viewpoint of the main change
        (`site_handoff.py` view helpers if useful). Open a new chat and start with
        `cell_registry.py next` — that is the progress source, not a retired queue.

------------------------------------------------------------------------
7. TOKEN BUDGET
------------------------------------------------------------------------
 - Chain rule (the one home): if an item is done, needs no user check (readback/patrol
   clean, nothing for human eyes, no "awaiting user visual check") and context is under
   ~100K tokens, release the lock and run `cell_registry.py next` in the same chat
   instead of `end`. Otherwise one item per chat: if context passes ~250K tokens or a
   user check is needed, write progress into the files and hand off (site_handoff.py end).
 - Delegate research-heavy or bounded subtasks (cell source-finding and neighbour
   review, audits across many files) to a helper that returns a short brief, so the
   main context stays small. Subtasks write results to repo files and return a
   one-line summary.
 - Review views through the contact sheet; open full-size only for detail.
 - Batch anything only the user can do into ONE request; do not poll.
 - Good enough: rules/radial-good-enough.md.
 - End of build work: coverage.py status --write (never hand-edit
   PROCESSED CELLS). Parcels are done at 100% touched; coverage sign-offs are retired.
