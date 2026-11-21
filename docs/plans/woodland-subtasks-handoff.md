# Handoff: ship the woodland subtasks (#347-#351) on worktrees

For any agentic AI that can run shell commands and git worktrees (Cursor, Claude Code, Codex, others). You are the **orchestrator**. You launch one worker per task, review and merge each returned branch, and alone update the tracker. Nobody needs a particular model for this wave: every task here is `requiresClaude: false`.

Written 2026-11-21. Read the live tracker before acting; this file may be out of date.

## What this wave is, and is not

Woodland (#318) was split so its code can be built and tested with synthetic data. This wave is the code only. **No task here touches the live Minecraft world.**

Layer ownership, set by the owner, applies to every task:

- **ground** sets the ground level at lidar terrain height (surface blocks only).
- **trees** do only trees.
- **decor** does minor non-feature, non-tree enhancements: plants, leaf litter, understorey bushes.

Out of scope, never start: #318 itself (it needs Claude, a running world and the owner's verdict), any `redo_layer.py`, `cell_registry.py prepare/--apply`, `trees_layer.py --apply`, `ground_layer.py --apply`, `site_lock.py`, or anything that moves the player or writes blocks. If a task seems to need them, stop and hand back.

## Read first

1. `AGENTS.md` (project entry point), `CODING_STANDARDS.md`.
2. `docs/agents/parallel-task-wave.md` and `.agents/skills/parallel-task-wave/SKILL.md` (the wave procedure and the project's settings).
3. `.agents/skills/ship-branch/SKILL.md` (the test, review and merge loop you run on each returned branch).
4. `docs/agents/model-and-effort.md#task-model-requirement` (the dispatch gate).
5. `docs/plans/park-rural.md` sections "Woodland is its own treatment" and "Rough-grass prototype" (the source of the floor and plant shares; they are the owner-accepted starting values).
6. Each task's full text: `node scripts/tasks.mjs show <id>`. The notes are the spec and are firm; do not widen them.

## The tasks

| Id | Title | Edits | Blocked by | Wave |
| --- | --- | --- | --- | --- |
| 347 | Plant vocabulary: teach the surface probe the 26.2 ground-cover plants | `lib/blocks/surface_probe.py` and mirrors | none | 1 |
| 349 | Trees: lidar-tall columns as a second crown source | `lib/layers/trees_layer.py` | none | 1 |
| 350 | Trees: species mix per crown (oak, birch, dark oak) | `lib/layers/trees_layer.py`, `lib/layers/plant_cell_canopy.py` | serialise after 349 (same file) | 2 |
| 348 | Woodland floor blocks in the ground layer | `lib/layers/ground_layer.py`, config | 347 | 2 |
| 351 | Decor: woodland understorey and ground cover | `lib/layers/decor_layer.py`, config | 347 | 2 |

Launch a task when it is unblocked and its file does not clash with a running worker. Wave 1 is 347 and 349 in parallel. Wave 2 starts as each wave-1 branch merges: 348 and 351 after 347; 350 after 349. 348, 350 and 351 touch different files, so they can run together once unblocked.

Already done, do not redo: #345 (trees planning speed-up) and #346 (run budget).

## Preflight (orchestrator)

1. `git status` on the primary checkout is clean and on `main`. Once per clone: `python3 scripts/install_git_hooks.py`.
2. `python3 scripts/ship_branch.py sweep` to see unmerged branches and leftover worktrees; report any code strays to the owner before starting.
3. Acquire the wave lock as the wave skill says (`python3 scripts/pre_commit_checks.py wave-lock-acquire --wave-id woodland-1 --holder <unique-token>`). If it fails, another orchestrator is running: stop.
4. Dispatch gate for each task you are about to start, using your real tool and model:

   ```bash
   node scripts/tasks.mjs check-run 347 --tool cursor --model <your-model>
   ```

   (`--tool claude-code` if you are Claude Code.) Expect "eligible". A refusal leaves the task pending; report why.
5. **Task 347 input check.** The task says to confirm the new plant names from the repo's block data. The repo may not list them (a search found no mention of `leaf_litter`, `short_dry_grass`, `tall_dry_grass` or `bush` in `block_reference.py` or `surface_probe.py`). If the 347 notes do not already contain a confirmed list with its source, do not start 347 and do not let the worker guess names: ask the owner (or a Claude session with the game's block registry) for the list first. 348 and 351 wait on it.

## Launch a worker

For each ready task: branch `task/<id>-<short-slug>` from current `main`, add a worktree (`git worktree add .worktrees/<id>-<slug> -b task/<id>-<short-slug> main` or your host's own worktree), start one isolated agent in it. Give it this brief, filled in:

> You are a worker on task #<id> in the ai-worldbuilder repo, in this worktree on branch `task/<id>-<slug>`.
>
> 1. Run `node scripts/tasks.mjs show <id>` and read the notes. They are the spec. Read `AGENTS.md` and `CODING_STANDARDS.md`.
> 2. Model gate: this task has `requiresClaude: false`, host `<tool>`, model `<model>`. If you discover the task needs a world build step (a live dry run or apply, reading the live world, judging a real cell), stop and hand back; do not do it.
> 3. Write the change and its tests. Tests are colocated (`lib/<category>/test_*.py`) and use synthetic data only. Run only what your diff needs: `python3 scripts/run_tests.py --only <stem>`. Do not run live-world commands, `site_lock.py`, or any `--apply`.
> 4. Put shares, thresholds and palettes in config or named constants as the notes say; never magic numbers in code. Everything seeded per cell and column so reruns are identical.
> 5. Commit **code only** on your branch with clear messages. Never edit `tasks/index.json` or `tasks/done.json`. Never merge, never review, never push.
> 6. Return (under 200 words): branch name, tip SHA, files changed, tests run with results, what you did not do and why, and anything the orchestrator must decide.

Add the per-task cautions from "Per task" below.

## When a worker returns: ship it

Run the ship-branch loop on the returned branch (`.agents/skills/ship-branch/SKILL.md`): focused tests, a fresh-context read-only review, fixes for blockers (cap 3 rounds), then:

```bash
python3 scripts/ship_branch.py merge task/<id>-<slug>
```

- The skill names a Sonnet-class reviewer. On another host use any capable read-only reviewer with a fresh context; the brief is fixed in the skill (diff vs `main`, `CODING_STANDARDS.md`, glossary and ADRs, the ticket text, test coverage). Ask the owner before using any high-cost model.
- `ship_branch.py merge` pushes `main` to its upstream after the full suite passes on main. Use `--no-push` if the owner wants it kept local.
- After the merge lands, archive on `main` only: `node scripts/tasks.mjs close <id> --sha <FULL_MERGE_SHA> --note "<short>"`. Commit the tracker change straight to `main` (tracker-only commits skip the unit suite).
- If tests fail on main after the merge, stop the wave and report; there is no auto-revert.
- A failed or abandoned worker: do not archive; list it in the report.

## Per task

**347 plant vocabulary.** Names come from a confirmed source (see preflight step 5), never guessed. Update `PLANTS` and everything that mirrors it (`ground_layer.STEM_OR_FLOWER` derives from it; also check `foliage.REFINE_PLANTS`, `block_reference` patterns, decor and the readback or audit code that classifies plants). Note which plants are two-block. Probe tests for each plant above grass, dirt and podzol.

**349 lidar second crown source.** A pure function (for example `lidar_fill(ndsm, crowns, cover, ...)`) called from `planning_inputs`, after detected and aerial-fill crowns and before mapped crowns (mapped crowns still win). `MIN_NDSM` (start 3.0 m) and the other parameters are named constants. With no nDSM, output must be unchanged. Report counts crowns by source. Lidar is height truth; the aerial is a guide.

**350 species mix.** Seeded species per crown from a weights table in config keyed by land-use class. Weights (oak 60, birch 25, dark oak 15 in woods; parkland stays oak only) are **placeholders**, not evidence. A single-species config must reproduce today's output exactly. The owner's 2026-10-14 rule (plain oak trunks) must not be silently undone: record in the task notes how it interacts and leave the question for the owner.

**348 woodland floor blocks (ground).** Extend the existing rough-grass step (#319) in `ground_layer.py`. Surface blocks only, at lidar terrain height; never change the height, never place plants. Shares 30% podzol, 10% coarse dirt, 8% moss from config `ground_texture`; grass at the polygon edge. #319 behaviour for meadow, pasture and lawns must be byte-identical in tests. Leave the open question (whether #319's tufts and flowers should move to decor) alone.

**351 decor understorey and ground cover.** In `decor_layer.py`. Read first how decor is gated (frontage inference false blocks planting, which is why rough grass has its own config key) and use a separate woodland key. Bushes from `plant_cell_canopy.bush_voxels`; ferns, short grass, leaf litter and dry grasses only where 347's confirmed list allows. Never on trunks, crown blocks, roads, footprints, water or columns another build owns.

## Close the wave (orchestrator)

1. Every wave branch tip is on `main`, or listed failed or unmerged. Every merged id is in `tasks/done.json` and absent from `tasks/index.json`.
2. No commit unique to a worker branch touched the tracker files; no worktree is still on a wave branch.
3. `python3 scripts/ship_branch.py sweep` reports no code strays. Release the wave lock (`wave-lock-release`).
4. Report to the owner: per task, merged SHA, tests run, reviewer verdict, anything left. Then state plainly: **#318 is next and needs Claude** (it is blocked by 348 to 351). It tunes thresholds and shares against the aerial, runs a ground redo then a trees redo on cells 63,-45 and 46,-46 (never unattended), checks the 8-minute budget, and takes the owner's verdict in game. Issue I-055 (the owner's woodland snags) stays held until #318 is archived.

## Stop and hand back when

- the wave lock or the full-suite lock is held by someone else;
- a worker reports it needs a world build step, a new schema or journal change, or a change to layer ownership;
- a review keeps finding blockers after 3 rounds (leave the branch unmerged and say so);
- plant names for 347 cannot be confirmed from a real source;
- anything in this file contradicts the live tracker or the project docs: follow them, and tell the owner.
