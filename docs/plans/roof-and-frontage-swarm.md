# Roof and frontage swarm: handoff (2026-11-23)

For the orchestrator of a small parallel wave. Origin: the Papa John's read-versus-build analysis and live
rebuild (task #447 session). Use the `parallel-task-wave` skill and `docs/agents/parallel-task-wave.md`.
Talk to the owner by task **title** first, number second.

## What is already true

- On `main` (and `origin/main`): the ridge-clip fix (`zones.levels: auto` plus `regions` no longer flattens a ridge;
  `ridge_clip_levels` in `lib/builds/feature_roof_zones.py`) and the opt-in `zones[label].surface: "fit"` key
  (`lib/builds/roof_surface.py`, `fit_clean_heights`; flat, plane or gable per region; documented in
  `rules/feature-builds.md`).
- Papa John's (map way 149814018, plan `w149814013`, cell 50,-36 crossing into 51,-36) is live with those fits and a
  grey tile roof (`stone_brick`). The aerial over its footprint is blue-grey, not red. The owner's hand edits were
  erased on request.
- Owner verdict on the live roof: notably better; flat front roof looks good; still stairs where blocks belong.

## The tasks

| Task | Title | Builder task | Main files | Wave |
| --- | --- | --- | --- | --- |
| #434 | feature_build roofs: octant fit or fixed stair rotation for pitched feature roofs (notes hold the live review) | no | `lib/builds/roof_geometry.py` (`pitched_roof_states`), `lib/builds/roof_octant.py`, `feature_roof_zones.py`, tests | 1 |
| #457 | Typology generator: row front direction wrong on a rotated parade | no | `lib/builds/typology_spec.py`, its tests | 1 |
| #458 | reapply_feature --apply loses the world lock between undo and rebuild | no | `lib/builds/feature_reapply.py`, `lib/site/site_lock.py`, `scripts/reapply_feature.py`, `rules/slow-commands.md` | 1 |
| #455 | feature_build flat roofs: carpet finish (different roof material, no extra height) | no | `lib/builds/feature_build.py` (flat deck), `feature_spec.py`, `roof_material.py`, `config/roof-materials.json`, `rules/feature-builds.md` | 2 |
| #456 | feature_build: add flat volumes outside the map way (bin store) | no | `feature_roof_zones.py`, `feature_build.py` footprints, `feature_spec.py`, `rules/feature-builds.md` | 2 (after #455, or one worker does both) |
| #459 | Read step: label roof volumes from image and lidar profile, with cross-source trust checks | **yes** | new module(s), `typology_spec.py` or a read script; uses a model on an image | 3, after #455 to #457 land |

Wave 1 touches disjoint files, so run #434, #457 and #458 in parallel, each in its own worktree. #455 and #456
both edit `feature_spec.py` and the `rules/feature-builds.md` key list, and #455 shares `feature_build.py` with #434:
start them only after wave 1 has merged. #459 is large and depends on the others; do not start it in the first wave.
The solid-glass window policy (#447 branch `task/447-solid-glass-windows`) has since merged and #447 is back to todo
with roof `flat_tol` default, storeys and bay, doors and the live parade left; those overlap this swarm, so read #447's
notes before dispatch and keep the new tasks from duplicating it. On Papa John's the dry run now selects 26 solid-glass
windows (30 to 50 per cent per face) where it selected 0, but still says "shell too low for a fascia band".

## Owner steers (apply to every task)

- Generic and UK-wide, no street or shop-specific code. Fixtures stand in for Papa John's; never name it in code.
- Image identifies zones, lidar gives heights and flats, a model makes a small per-building decision from the image,
  tokens used sparingly.
- No single source is always right: trees can hide lidar and the overhead image. Check each attribute against more
  than one source and say where a volume rests on one.
- Stairs only on the edge of a roof layer or on a real slope; blocks inside a flat layer and at a one-block step
  between flat layers. The house rim rule (`roof_octant.RIM_OPTIONS`, #414) over-applies, and so does the terrace-edge
  rule in `pitched_roof_states`. The owner wants an octant-style way to map stairs and slabs neatly, not necessarily
  the same code.
- Carpet is a flat-roof trick only: a different roof material with no extra height, on the wall's top course (the
  shopfront fascia). Never on slopes; needs a solid block below.

## Evidence and prototypes

World folder `worlds/southampton-build/site/`: `task-447-papa-johns-read-vs-build.md` (findings, follow-up list) and
`task-447-papa-johns-staged-test/` (`smooth_roof.py` with `RULE=none|interior|climb|shape`, `step_rule.py`,
`staged.py`, `reconcile_proto.py`, `fit_dry.py`, block maps `roof_compare*.png`). Scratch code, not repo code: read it,
do not import it. Numbers worth keeping: Papa John's fits are flat (0.23 m RMS), gable ridge across the axis (0.24 m,
ridge 30.26 m) and plane falling south (0.32 m); live roof 26 stairs, 40 slabs, 39 blocks.

## Worker brief (copy into each)

Worktree under `.worktrees/`, branch `task/<id>-<slug>` from current `main`. Code and tests only. Never edit
`tasks/index.json` or `tasks/done.json`, never review, never merge. Run `python3 scripts/run_tests.py --only <stem>`.
Commits run the full suite, which is single-flight under the git common dir: if the commit says "already running",
wait for no `scripts/run_tests.py` process and retry (a small retry loop works). Do not run `git merge main` on a
branch when `main` changed `tasks/`: the hook refuses a merge commit carrying tracker edits; `git rebase main` instead.
Document every new spec key in `rules/feature-builds.md`; add a test through `Build` (`feature_build`) and not only
the pure function; keep new options opt-in so existing builds do not change. Dry-run on the real spec
(`python3 scripts/feature_build.py --world southampton-build site/builds/w149814013.json`) and report counts; no
live apply from a worker.

## Orchestrator checklist

1. Check the model gate (`docs/agents/model-and-effort.md`) before each dispatch; #434 is complexity `l`, so ask the
   owner before raising model or effort; #459 is a builder task and needs an eligible runner.
2. Claim with `scripts/tasks.mjs status <id> doing ...` on `main`; commit tracker changes immediately.
3. For each returned branch run the ship-branch loop (tests, Sonnet read-only review, fix blockers, cap 3, then
   `python3 scripts/ship_branch.py merge <branch>`). Reviewers keep catching the same things: a new option breaking a
   consumer of the old one, undocumented keys, misleading dry-run messages, tests that pass vacuously.
4. Archive tasks only after the merge lands. `ship_branch merge` pushes `main` unless `--no-push`; confirm with the
   owner if unpushed commits from other threads would go with it.

## Live-world protocol (owner-gated; say so when a real build test is needed)

Several build threads share the world and take turns. A fresh site lock held by another owner is a stop; wait, never
take it over. Run live writes through `python3 scripts/site_lock.py --world southampton-build run <owner> "<task>" --`
and put undo plus rebuild in one command (`reapply_feature --apply` lost its lock between undo and rebuild once,
which is task #458). Expect 429 rate limits, and do not teleport the owner's player: ask them to stand where the view
is needed. Registry gates: cell 50,-36 and its neighbour 51,-36 needed `prepare` before the rebuild; evidence-only
prepare for a neighbour records no plans; recording or dismissing feature plans for other buildings is the owner's
call. Stale ground in 50,-36 is offered, never run automatically.
**Shout for a real build test** after #434, #455 and #456: rebuild `w149814013` and ask the owner to look near
6518, 39, -4568 (roof), the street end 6512, 36, -4574 (frontage) and the rear (bin store).

## Acceptance per task

- #434: the stairs-and-blocks rule documented and tested (rim, slope, flat layer, one-block step, rotated gable);
  isolated-interior-step count on the live roof before and after; owner's in-game check.
- #457: `typology_spec.py --ways 149814018` gives front W and storeys 2; a test for a row on a rotated street.
- #458: a test that reproduces the lock lapse; the wrapper holds or re-claims the lock across undo and rebuild; the
  recovery path written in `rules/slow-commands.md`.
- #455: carpet finish with the support check, deck top course takes wall or fascia material, dry run no longer says
  "shell too low for a fascia band" on a flat front strip.
- #456: a flat volume beyond the way is built, ownership and overlap rules kept; dry run shows the bin store.
- #459: recorded runs on Papa John's and one other building with token cost, labels and trust flags; owner review.

## Known open items

No real canopy case exists yet (the hand-read crowns never overlap footprints; the highest aerial green share inside
any of 155 houses is 9%); staged damage tests only. One roughness outlier (6744,-5516) sits outside aerial coverage.
Papa John's shopfront (fascia, windows, door, sign) is currently bare brick.
