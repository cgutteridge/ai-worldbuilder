# Handoff: research for #252 (bushes, flowers, ponds; no code)

For a Sonnet-class model. Research only. Do not change code, do not apply anything to the world, do not rebuild 53,-43. The output is findings written into the task and new, small coding tasks that other models can pick up later.

## Why research only

The owner will rebuild 53,-43 once, after every task that changes how it builds has landed (gate recorded on #244: #253, #254, #255, #252, #260 and #244's boundary data). A rebuild now repeats effort. So this session measures and decides; it builds nothing.

## What is already known (2026-11-12)

- Rebuild review of 53,-43 after #240 and #245-247: doors, boundary openings (419 reserved) and routes hold. Road-facing fronts (checked at Kitchener Road 55-59, 6808,38,-5418) read as one broad grey slab with sporadic hedge spots, not a suburban street. Snag S-077 on I-042, owner confirmed.
- Ground: 3,243 columns are aerial paving and 4,611 shade-filled; the guessed-lawn rule converted only 237 (+74 on routes). Decor: 150 aerial hedge items and 2 mapped iron bars; no walls, fences, gates, furniture. 79 of 143 frontages are ineligible ("no street-run frontage evidence").
- `kitchener_road.json` has only rear/side vetoes; `sirdar_road.json` does not exist.
- The saved decor and ground reports for the rebuilt cell are in `worlds/southampton-build/verification/site/highfield_53_m43_*-report.json`. The pre-rebuild copies are in the session scratchpad and are not needed.
- Owner in-world judgement: back gardens are acceptable; the road-facing fronts are the problem. Doors at 65 Kitchener Road are fine.


> Parallel sessions: another research session works the sibling task on the same checkout. Re-read `tasks/index.json` immediately before each edit, make minimal text insertions only (do not re-serialise the file), touch only your own task and your own new tasks, and do not commit; the orchestrator commits.

## Part A: #252 (bushes, flowers, garden ponds)

Read the task notes first (`node scripts/tasks.mjs show 252`) and `docs/plans/land-use-classes.md` (planting section). Measure each, for front gardens in 53,-43, with per-plot counts:

1. **Bushes.** Do shrubs visible on the aerial reach the canopy mask, or are they lost? Files: `lib/layers/trees_layer.py` (`bush_voxels`, `MIN_CROWN_R`). Count aerial shrub patches per front garden against placed bush voxels.
2. **Flowers.** Does guessed lawn in private frontage get any flowers? Files: `lib/layers/foliage.py` (flower list), `lib/layers/ground_layer.py`. Count flower blocks in front gardens in the live world or the ground report.
3. **Ponds.** Is any garden pond visible on the aerial inside a plot? If none, say so and stop; ponds then need no task. If some, propose a labelled inference rule and its evidence.

Each measurement ends with: the number, what drives it, and a yes/no on whether code is needed.

## Rules for this session

- Everything runs against world `southampton-build`; pass `--world southampton-build`. Read-only inspection needs no lock.
- No `--apply`, no `redo_layer.py --apply`, no world file edits. No aerial or street-photo downloads without the owner's authorisation in chat.
- Read `rules/frontage-and-evidence.md` and `rules/evidence-order.md` before judging evidence. Nothing is scattered by default; every element needs map or aerial evidence per property, or is a labelled inference.
- Owner preferences: short answers, issues and tasks by title first, lidar is height truth, generic UK-wide tools (key by land-use class, no Kitchener-only code paths), prefer enhancing existing passes.
- Tracker edits (`tasks/index.json`) happen on `main` by the orchestrator only, never from a `task/*` branch. Check the branch before editing. Tracker and docs edits commit straight to `main`.
- Keep command output bounded. Count, do not list.

## Output: what to record

1. Findings into #252 and #244 notes: the measurements, the decision per street, the evidence ids.
2. New coding tasks in `tasks/index.json`, one per change, each small enough for a cheaper model: title, files, synthetic tests to add, check, and `blockedBy` (usually #254 or #255). Do not duplicate #253-#255 and #260; amend them where a finding belongs there. Likely candidates, to confirm by the measurements:
   - bush voxel rule keyed by land-use class, if shrubs are lost;
   - flower beds on guessed private lawn, keyed by class, if none are placed;
3. Say plainly what was not measured or could not be decided.

## Done means

#252 carries measured findings, each coding change is a recorded task with tests and blockers, #244's draft boundary data exists in the notes with cited evidence, and no code or world data was changed.
