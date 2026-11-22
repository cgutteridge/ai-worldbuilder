# Handoff: sparse decor outside house fronts (#244)

For a stronger model (Opus). Diagnose first, then write the findings and a proposed fix into task #244 in `tasks/index.json`. Do not change code in this session unless the owner says so; a cheaper model can build the fix once the diagnosis is on the task.

## The complaint

Owner review of cell 53,-43 (world `southampton-build`), 2026-11-08: "very little decor outside house fronts". Streets: Kitchener Road and Sirdar Road. Not yet measured. Related: #240 (shared frontage between ground and decor, partly done), issue I-042 (suburban streets lack distinct frontages, boundaries and entrance connections), snag S-073 (sparse Kitchener Road).

## What is true now (2026-11-09)

- Every house plan in cells 53,-43 and 53,-42 was rebuilt with door fixes (#243, closed). Doors are present, two-high and level with the ground outside, so the entrances and routes the decor layer reads from landed doors now exist.
- Ground and decor were redone on 53,-43 and 53,-42 after the rebuilds. Decor 53,-43: 2,397 blocks verified. Decor 53,-42: 442 blocks in 168 boxes verified, with 575 unplaced items (by reason: outside cell 275, on road/path/water or no surface 209, against build 67, on carriageway 17, above lidar ground 7).
- Frontage routes: none reported blocked after the rebuild (earlier two were, now fixed).
- Garden boundaries come from `scripts/garden_sources.py` in this order: map barrier tags, per-street convention `site/gardens/<street>.json`, aerial hedge strip, then marked inference. Read `rules/frontage-and-evidence.md` first. Key rules: a missing convention alone never authorises a fence; inferred barrier columns and furniture inside the entrance opening are dropped; `front: none` is an explicit open frontage.

## Questions to answer, in order

1. **Measure.** For each frontage in 53,-43, how much decor is placed in the front yard (fences, walls, hedges, gates, furniture, bins, bins' absence, planters)? Compare against the aerial (`reference/aerial/...`, mosaics for the cell) and against the street-run convention. A table by frontage is enough. Is "sparse" true everywhere or only on some streets?
2. **Where is it lost?** Candidates, check each against the decor plan and stats rather than guessing:
   - evidence: no per-street convention file for Kitchener/Sirdar under `site/gardens/`, so inferred boundaries are never authorised;
   - the decor layer drops candidates (see the `unplaced` reasons above, `reserved.conflicts` in the decor plan, and `against build` and `on carriageway`);
   - the front yard is too small after the doors and routes were reserved (clearance columns);
   - the layer is correct and the real frontages are simply plain (check the aerial and the owner's expectation).
3. **What should a front yard contain here?** This is a design question for the owner: confirm what they expect (low walls, hedges, gates, bins, planting) before proposing code. Quote the evidence for each element.
4. **Propose the fix** with the layer and function to change, the tests to add, and a verification plan (counts per frontage before and after, plus an in-world look).

## Files to read

- `rules/frontage-and-evidence.md`, `docs/agents/domain.md`
- `lib/sources/garden_sources.py` (`residential_frontage`, `landed_approaches`)
- `lib/layers/decor_layer.py` and its tests
- `lib/layers/ground_layer.py` (routes and guessed lawn in private frontage)
- Task #240 in `tasks/index.json`, issue I-042 and snag S-073 (`python3 scripts/issues.py --world southampton-build show I-042`)
- Decor outputs: `worlds/southampton-build/verification/site/highfield_53_m43_decor-*.json`

## Running things

- Everything runs against world `southampton-build`; pass `--world southampton-build`.
- Read-only inspection needs no lock: decor dry run is `python3 scripts/redo_layer.py --world southampton-build --cell 53,-43 --layers decor` (no `--apply`).
- Do not apply anything without the owner's go-ahead. If you do apply: `redo_layer.py` claims the lock itself, so do not wrap it in `site_lock.py run`. It currently crashes on exit with a `PermissionError` and leaves a lock behind (task #245); release it with `python3 scripts/site_lock.py --world southampton-build release <owner>` and check `python3 scripts/site_recover.py --world southampton-build status` is clean.
- Move the player over the cell before an apply so chunks load: `python3 scripts/mc_player.py --world southampton-build move --why "..." --pos x,y,z --facing x,y,z --gamemode spectator`. One decor apply sat silent for over 20 minutes (issue I-044, undiagnosed).
- Downloads (aerial tiles) need the owner's authorisation in chat.

## Branch and tracker rules

Code goes on `task/*` branches via `scripts/ship_branch.py merge`. Tracker and docs edits go straight to `main`. Tool work lives in `tasks/index.json`; world work (issues, snags, place builds) lives under `worlds/`. If unsure which, ask.

## Known traps

- Rebuilding a house plan: check the spec's `notes` first. Feature-path specs (notes begin "FEATURE PATH") must use `scripts/feature_build.py`; house-path specs use `scripts/house_build.py`. Using the wrong one destroys the build (task #246).
- Check cell readiness (`cell_registry.py --world W check --cell tx,tz`) before undoing any plan (task #247).
- Owner preferences: short answers, issues and tasks by title not number, lidar is height truth, no wall of text.

## Done means

Findings written into #244 (measurements, where decor is lost, proposed fix and tests), owner agreement on what a front yard should contain, and, if code was changed, a merged branch with tests and a verified in-world check.
