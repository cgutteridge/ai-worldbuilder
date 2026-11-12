# Handoff: research for #244 (Kitchener and Sirdar boundary evidence; no code)

For a Opus-class model. Research only. Do not change code, do not apply anything to the world, do not rebuild 53,-43. The output is findings written into the task and new, small coding tasks that other models can pick up later.

## Why research only

The owner will rebuild 53,-43 once, after every task that changes how it builds has landed (gate recorded on #244: #253, #254, #255, #252, #260 and #244's boundary data). A rebuild now repeats effort. So this session measures and decides; it builds nothing.

## What is already known (2026-11-12)

- Rebuild review of 53,-43 after #240 and #245-247: doors, boundary openings (419 reserved) and routes hold. Road-facing fronts (checked at Kitchener Road 55-59, 6808,38,-5418) read as one broad grey slab with sporadic hedge spots, not a suburban street. Snag S-077 on I-042, owner confirmed.
- Ground: 3,243 columns are aerial paving and 4,611 shade-filled; the guessed-lawn rule converted only 237 (+74 on routes). Decor: 150 aerial hedge items and 2 mapped iron bars; no walls, fences, gates, furniture. 79 of 143 frontages are ineligible ("no street-run frontage evidence").
- `kitchener_road.json` has only rear/side vetoes; `sirdar_road.json` does not exist.
- The saved decor and ground reports for the rebuilt cell are in `worlds/southampton-build/verification/site/highfield_53_m43_*-report.json`. The pre-rebuild copies are in the session scratchpad and are not needed.
- Owner in-world judgement: back gardens are acceptable; the road-facing fronts are the problem. Doors at 65 Kitchener Road are fine.


> Parallel sessions: another research session works the sibling task on the same checkout. Re-read `tasks/index.json` immediately before each edit, make minimal text insertions only (do not re-serialise the file), touch only your own task and your own new tasks, and do not commit; the orchestrator commits.

## Part B: #244 (Kitchener and Sirdar boundary evidence)

Read the task notes first. Decide, per street run, what the boundary evidence supports:

1. Use the registered aerial mosaic (`reference/aerial/kitchener-z19`) and the owner's S-073 Street View screenshot (see `python3 scripts/issues.py --world southampton-build show I-042`) to decide front boundary type, height and material per run: low pale wall, hedge, fence, or open drive. Say which evidence id each decision cites. Labelled inference where the evidence is weaker.
2. List the houses that look open (drives, no boundary) so they get per-house `front: none`.
3. Draft, but do not commit to the world, the `front` and `side_front` entries for `kitchener_road.json` and a new `sirdar_road.json`. Put the draft in the task notes or a docs file, not under `worlds/`.
4. Material fallback (added to #255): note the dominant boundary material per run and which houses depart from it, so the district profile can carry area-dominant defaults with per-house overrides.
5. Pavement versus forecourt (added to #254): on the aerial, estimate public pavement width along each road and forecourt depth behind it, so the ground split has real numbers.

## Rules for this session

- Everything runs against world `southampton-build`; pass `--world southampton-build`. Read-only inspection needs no lock.
- No `--apply`, no `redo_layer.py --apply`, no world file edits. No aerial or street-photo downloads without the owner's authorisation in chat.
- Read `rules/frontage-and-evidence.md` and `rules/evidence-order.md` before judging evidence. Nothing is scattered by default; every element needs map or aerial evidence per property, or is a labelled inference.
- Owner preferences: short answers, issues and tasks by title first, lidar is height truth, generic UK-wide tools (key by land-use class, no Kitchener-only code paths), prefer enhancing existing passes.
- Tracker edits (`tasks/index.json`) happen on `main` by the orchestrator only, never from a `task/*` branch. Check the branch before editing. Tracker and docs edits commit straight to `main`.
- Keep command output bounded. Count, do not list.

## Output: what to record

1. Findings into #252 and #244 notes: the measurements, the decision per street, the evidence ids.
2. New coding tasks in `tasks/index.json`, one per change, each small enough for a cheaper model: title, files, synthetic tests to add, check, and `blockedBy` (usually #254 or #255). Do not duplicate #253-#255 and #260; amend them where a finding belongs there. Likely candidates, to confirm by the evidence:
   - the `front`/`side_front` data for Kitchener and Sirdar, as a world-data task;
   - the pavement/forecourt split in the ground layer, if the numbers support it.
3. Say plainly what was not measured or could not be decided.

## Done means

#244 carries its decisions per street, each coding change is a recorded task with tests and blockers, #244's draft boundary data exists in the notes with cited evidence, and no code or world data was changed.
