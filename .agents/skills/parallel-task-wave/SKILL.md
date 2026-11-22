---
name: parallel-task-wave
description: >-
  Run several tasks/index.json tickets in parallel via git worktrees, with one
  orchestrator that merges and alone updates the tracker. Use when the user says
  kick off tasks in parallel, parallel worktrees, task wave, or merge as agents
  finish — not for a single-ticket implement, and not for a world/place build queue.
---

<!-- Installed from agent-kits local-tasks@0.2.0. Change it in the kit, not here. -->

# Parallel task wave

Part of the core tier of the `local-tasks` kit. Ship a **wave** of local-tracker tickets (`tasks/index.json`) with isolated workers and one **orchestrator**. Pair with `worldbuilder-tasks` for all tracker reads/writes.

**Project settings.** If `docs/agents/parallel-task-wave.md` exists, read it first. It adds project rules (for example a review-and-merge gate run before each merge, or a project wave-lock command) and wins over this skill where it is stricter.

Leading words: **wave**, **orchestrator**, **worker**, **close**.

## When this applies

- Repo has `tasks/index.json` (+ usually `tasks/done.json`).
- User wants more than one unblocked ticket implemented at once.
- Isolation is git **worktree** + branch per ticket (Cloud Agents optional if a remote exists).

Use a normal single-ticket flow when only one task is in play. If the repo has a separate place/world build queue, leave that to its own orchestrator — this skill is tracker + git only.

## Roles

| Role | Count | Owns |
| --- | --- | --- |
| **Orchestrator** | exactly one chat | frontier pick, worktree launch, merge into main, tracker archive, **close**, cleanup |
| **Worker** | one per ticket | implement, test, commit **code** on its branch |

Workers leave `tasks/index.json` and `tasks/done.json` untouched. The orchestrator archives a task only **after** that task’s branch is on `main`.

## Orchestrator steps

### 1. Preflight

Completion: main checkout is clean enough to branch from, the frontier list is written down, and this chat holds the wave lock.

1. Read the tracker via `worldbuilder-tasks`.
2. Build the **frontier**: status `todo` (or equivalent ready), empty/resolved `blockedBy`, not deferred unless the user said to include deferred.
3. `git status` on the primary checkout. If unrelated WIP would be missing from worker bases, stop and tell the user — stash/commit/elsewhere first.
4. **Wave lock** — acquire before launch. If the project settings name a wave-lock command, use it. For example, a repo that ships `scripts/pre_commit_checks.py`:

   ```bash
   python3 scripts/pre_commit_checks.py wave-lock-acquire \
     --wave-id "<short-id>" --holder "<this-chat-id-or-unique-token>"
   ```

   If acquire fails, another orchestrator is already waving — **stop**. Same `--holder` may re-acquire (crash recovery). Elsewhere: create a gitignored `.wave-lock` JSON with `wave_id` + `holder` yourself and refuse if a different holder already owns it.

### 2. Launch workers

Completion: every frontier ticket has a worktree, branch, and running worker with a brief that forbids tracker edits.

For each frontier ticket `id`:

1. Branch `task/<id>-<short-slug>` from current `main`.
2. Add a worktree (repo `.worktrees/…` or Cursor worktrees — either is fine if `git worktree list` shows it).
3. Start one isolated agent/chat in that worktree.
4. Brief must include: task id + title + notes, branch name, **code-only commits**, return summary shape below, and that the orchestrator owns the tracker.

If two tickets clearly share the same hot files, prefer serialising them in this wave rather than parallelising a guaranteed conflict.

### 3. Merge as each returns

Completion: each successful worker’s tip is an ancestor of `main`, and that id is archived in the tracker.

When a worker returns:

1. Merge its branch into `main` (merge commit fine; fast-forward fine).
2. Resolve tracker-file conflicts by **keeping every valid completion** (union of done moves). Resolve real code conflicts so both behaviours survive when both belong.
3. Via `worldbuilder-tasks`, move that task from `index.json` → `done.json` only after the merge landed (`node scripts/tasks.mjs close ID --sha FULL_SHA --note "..."` where the CLI exists).
4. Unblock dependents; launch newly unblocked frontier tickets the same way if the user asked to keep going.

If a worker failed, record it as unmerged/failed for the close step; do not archive it.

### 4. Close

Completion: every close check passes, or the orchestrator reports the failing checks and stops calling the wave done.

Run this before ending the chat (also safe to re-run after a crash):

1. **Branches** — every wave branch tip is on `main`, or listed as failed/unmerged.
2. **Tracker** — every id this wave claimed done is in `done.json` and absent from `index.json`.
3. **Ownership** — commits unique to each worker branch do not touch `tasks/index.json` or `tasks/done.json` (if they did, treat as a process failure; fix on main, do not rely on the worker’s tracker edit).
4. **Worktrees** — no worktree remains checked out on a wave branch (mislabeled folder names still count if the branch matches).
5. **Cleanup** — remove merged wave worktrees; delete local wave branches fully merged to `main`. Prefer dry-run first when unsure; force-remove only when the user accepts it.

Exit the wave only when 1–4 are green (and 5 done or explicitly deferred by the user).

6. **Release the wave lock** after a green close (or when the user abandons the wave):

   ```bash
   python3 scripts/pre_commit_checks.py wave-lock-release --holder "<same-token-as-acquire>"
   ```

   Do not leave a stale lock that blocks the next wave. Only the acquiring holder may release.

## Worker brief (paste into each worker)

```
You are a wave worker for task #<id>: <title>
Branch: task/<id>-<slug>
Worktree: <path>

Implement and test this ticket only.
Commit on this branch; code and tests only.
Do not edit tasks/index.json or tasks/done.json — the orchestrator archives after merge.

When finished, return:
- Branch
- Commit hash
- Files changed
- Tests run + result
- Blockers / follow-ups
```

## Guardrails

- One orchestrator chat per wave — enforced by `.wave-lock` (see preflight). Workers never merge to main themselves unless the user explicitly said so (default: orchestrator merges).
- Archive only after merge — “committed on a branch” is not done.
- Close before the orchestrator chat ends; release the wave lock on the way out.
- Skip deferred tickets unless asked.
- No git remote required; local worktrees are enough.
- When the repo has a `task/*` pre-commit hook, workers cannot commit tracker edits; still treat a worker that stages `tasks/` as a process failure.

## Out of scope

- Replacing `worldbuilder-tasks` or changing tracker schema.
- Auto-resolving semantic merge conflicts.
- Place/world build queues, site locks, or cell-style orchestrators.
- A mandatory closer binary (skill steps + git are enough for v1).
