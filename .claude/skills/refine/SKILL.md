---
name: refine
description: >-
  Repair an element (feature, ground, trees, decor) or cell that is nearly right,
  and learn from it. Use when the user says refine, fix this, almost, not quite,
  or points at a built thing that is close but wrong. Diagnoses the cause, fixes
  in place, proposes general tool tasks. For a clean-slate rebuild use purge.
---

Human-invoked repair. The old build **is** the reference here (the opposite of `purge` / `redo`): keep what is right, change what is wrong, find out why it went wrong.

Two outputs, always both: **the fix** and **the lesson** (a general cause, and zero to three tool tasks). The lesson serves every build type, so it is stated as a class of failure, never as this place's geometry.

Cell journal rules (ADR 004) apply: fix with a new named build that declares `modifies=()`; never edit a BUILT plan to patch the world. Snags that stay open: `snag` skill (`scripts/issues.py add`).

## 0. Scope

1. Target = what the user named, else `redo_here.py --at <x>,<z> --json` for the player's column (`player_list_online`).
2. Ask one question only if the target or the complaint is unclear; otherwise state the complaint back in one line.
3. If most of the element is wrong, say so and offer `purge` instead of refining.

Done when the target (element + cell) and the complaint are one line each.

## 1. Look (cheap evidence only)

1. `cell_registry.py query --feature <id>` / `--cell tx,tz`: what the journal says is there, and which script/plan built it.
2. One `view_capture` of the problem, beside the evidence already on disk: `site/cells/<tx>_<tz>-SOURCES.txt` and the manifest. Fetch nothing new (`rules/reference-token-budget.md`); a missing source is itself a finding.
3. `cell_registry.py reconcile <cell>` (dry run) to separate "built wrong" from "drifted from what was built".

Done when you can point at each defect on the capture and say whether the plan, the evidence or the blocks differ from what they should.

## 2. Diagnose: what, then why

List every defect, then give each **one** cause class. Walk the classes in order; the first that fits wins:

| Class | Meaning | Usual home of the fix |
| --- | --- | --- |
| **Drift** | blocks differ from the recorded plan | `reconcile --fix` |
| **Evidence** | a source was missed, stale, misaligned or mis-read (the table row is wrong) | the evidence step / source config |
| **Translation** | evidence was right, the build misread or mis-scaled it | the generator or its inputs |
| **Capability** | the tool cannot express this at all | a generator / `lib/` change |
| **Bar** | it meets the rules, the human wants more | rule or done-bar, or accept (`rules/radial-good-enough.md`) |

Then ask of each cause: *would another build type hit this too?* (houses, terraces, bridges, trees, decor, other worlds). Write the answer next to it. One word per defect is enough; the cause line is where the thinking goes.

Done when every defect has a class, a one-line why, and a yes/no/maybe on "other build types".

## 3. Fix

1. Smallest change that removes the defects: patch the input or generator for the cause class, then re-run the existing path (`feature_build`, the layer script, or a new `modifies=()` build). Prefer fixing the cause over adding a branch for this case.
2. Dry run first. Then lock and apply: `python3 scripts/site_lock.py run <owner> "refine <name>" -- <cmd>`. Readback clean.
3. Verify with what already exists: readback, `roof_lint.py --all` after pitched roofs, `osm_footprints.py check`, one `view_capture` against the first one. Writing a new test or fetching new imagery needs a reason the existing checks cannot see this defect.
4. Hover the player over the target if they are on the server (`mc_player.py move --why …`, as in `redo`).

Done when readback is clean and the defects from §2 are gone on the new capture, or each survivor is an open issue.

## 4. Lesson

Turn the causes into tool tasks, and only those that pass all of these:

- **General:** names a failure class and the build types it touches; no street names, building ids or coordinates in the title or notes (those go in the issue).
- **Upstream:** changes a generator, shared `lib/` function, evidence step or rule so the defect cannot recur. Prefer extending an existing check or rule over a new one; a new special case for one building type is a rejection.
- **Cheap:** its proof is a unit test on `MemoryBlocks` or an existing audit. No new live-world test, no new fetches.
- **New:** `tasks/index.json` and `tasks/done.json` searched for the same cause first; if found, add a note to that task instead.

Present at most three, each as: title, cause class, which build types it helps, rough size, how it would be proven. Say "no general lesson" when that is the truth. Add the approved ones with `task-index-manager` (tracker-only edit, straight to `main`); place-specific leftovers go to the `snag` skill (`scripts/issues.py`).

Done when each defect is covered by a landed fix, a task, an issue, or "accepted", and the user has the task list to approve.

## 5. Check with the human

When a place fix is `fix-landed`, walk it with the `snag` skill review path. Then ask once about the task list.
