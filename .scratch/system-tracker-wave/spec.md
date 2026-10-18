# Spec: System-tracker parallel wave

Status: wontfix

**Parked (2026-10-18):** This is not a worldbuilder project feature. Parallel task waves belong in a **shared** agent skill (sibling to `task-index-manager`), not a repo CLI. See `SHARED-SKILL-OUTLINE.md` in this folder. Thin project glue only: one `AGENTS.md` pointer when that skill exists — do not implement tickets under this scratch path.

Approved test seam (historical): one **system-tracker wave closer** CLI — superseded by shared-skill checklist steps.

---

## Problem Statement

When several agents implement open system-tracker tasks in parallel, work finishes on branches but the wave often fails to close: tracker updates collide, merges stop early, finished branches never land on main, and worktrees/branches are left behind. The human has to reconstruct what happened from chats and git archaeology.

## Solution

Introduce a single **wave** workflow for system-tracker work: one orchestrator owns the tracker and merges; workers only commit code on isolated worktrees/branches; a **wave closer** command verifies the wave is actually done (or reports exactly what is left). Agents and humans use the same closer so “done” means the same thing.

## User Stories

1. As a human driving the worldbuilder, I want to kick off all unblocked system-tracker tasks in parallel, so that extract and tooling work finishes faster without me babysitting each chat.
2. As a human, I want each worker to commit on its own branch, so that unfinished work cannot dirty main.
3. As a human, I want only one orchestrator chat to merge and update the system tracker, so that `tasks/index.json` is not clobbered by concurrent writers.
4. As a human, I want a wave to refuse to start if main is dirty with unrelated WIP, so that Cloud or worktree bases do not miss required commits.
5. As a human, I want deferred tasks (e.g. long-horizon refresh) skipped by default, so that a “kick everything” wave does not pull in non-extract work.
6. As an orchestrator agent, I want a clear list of unblocked system-tracker tasks, so that I launch exactly the frontier.
7. As an orchestrator agent, I want blocked tasks to stay idle until their blockers are on main and marked done, so that dependency order is preserved.
8. As an orchestrator agent, I want each worker brief to name branch, worktree, task id, and “do not edit the system tracker”, so that workers cannot race the tracker.
9. As a worker agent, I want an isolated worktree on a dedicated branch, so that I never fight another agent for the same files on disk.
10. As a worker agent, I want to commit only my task’s code and tests, so that my branch stays reviewable and mergeable.
11. As a worker agent, I want to leave `tasks/index.json` and `tasks/done.json` untouched, so that the orchestrator can archive the task once in one merge hop.
12. As a worker agent, I want a standard return summary (branch, commit, files, test result, blockers), so that the orchestrator can merge without reading my full transcript.
13. As an orchestrator agent, I want to merge each finished branch into main as soon as it returns, so that a session crash cannot leave finished work unmerged.
14. As an orchestrator agent, I want merge conflicts on the system tracker files to be expected and resolved by keeping the union of completions, so that I do not drop another task’s done state.
15. As an orchestrator agent, I want real code conflicts (shared scripts/tests) called out and resolved deliberately, so that both features’ behaviour survive (e.g. two FEATURE gates both remain).
16. As an orchestrator agent, I want to refuse starting a second orchestrator for the same wave, so that duplicate kickoffs do not double-merge.
17. As a human, I want the wave closer to report any branch tip that is not an ancestor of main, so that I know exactly what still needs merging.
18. As a human, I want the wave closer to report any task id still in the active index that the wave claimed done, so that tracker drift is visible.
19. As a human, I want the wave closer to report leftover worktrees for the wave, so that disk and branch locks get cleaned.
20. As a human, I want the wave closer to fail if a worker commit touched the system tracker, so that tracker ownership stays enforceable.
21. As an orchestrator agent, I want a cleanup step that removes wave worktrees and deletes merged local task branches, so that the next wave starts clean.
22. As a human, I want cleanup to be safe when a worktree is still checked out or dirty, so that force-remove is explicit and reported rather than silent.
23. As a human, I want partitioning guidance when two open tasks both edit the same hot files, so that I sequence them instead of parallelising a guaranteed conflict.
24. As an orchestrator agent, I want optional file-overlap hints before launch, so that I can warn or serialise colliding tickets.
25. As a human, I want the wave skill/docs to use glossary terms (system tracker, worldbuilder, active world), so that agents do not confuse this with the world QUEUE cell-orchestrator.
26. As a human, I want this workflow to apply only to the system tracker, so that world place builds keep their own lock and queue rules (ADR 002).
27. As an agent reading AGENTS.md, I want a short pointer to the wave workflow, so that “kick off all tasks in parallel” has one canonical how.
28. As a human, I want a dry-run mode of the wave closer, so that I can inspect postconditions without deleting worktrees.
29. As a human, I want the closer exit code non-zero when anything remains open, so that scripts and agents can stop on incomplete waves.
30. As a human, I want a successful closer to print a one-screen summary of merged task ids and commits, so that I can return to the sound cue knowing what landed.
31. As an orchestrator agent, I want to archive each merged task into done.json only after its branch is on main, so that “done” never means “committed somewhere else”.
32. As a human recovering from a crashed wave, I want to re-run the closer with the same wave id/branch prefix, so that I can finish merges and cleanup without redoing worker work.
33. As a worker agent, I want isolation even when Cursor worktree paths differ (repo `.worktrees/` vs Cursor worktrees), so that either layout is acceptable if registered with git.
34. As a human, I want leftover mislabeled worktrees (folder name ≠ branch) still detected by the closer, so that naming mistakes do not hide orphans.
35. As a human, I want documentation of what not to do (multiple writers on main, workers marking tracker done, ending the chat before closer), so that the retro failure modes do not repeat.

## Implementation Decisions

- Scope is **system tracker waves only** (worldbuilder `tasks/`). World tracker / cell-orchestrator / site lock are out of band and must not be reused as the mechanism.
- **Roles**: one orchestrator (merge + tracker + closer); N workers (code on branches). Workers never write the system tracker.
- **Isolation**: each worker runs in a git worktree on `task/<id>-…` (or equivalent) branched from current main after a clean-tree check.
- **Merge hop**: orchestrator merges with an explicit merge commit (or fast-forward when possible) as each worker returns; do not batch “merge everything at the end” as the only path.
- **Tracker hop**: after a successful merge (or in the same orchestrator step), move the task from active index to done archive once; resolver for index conflicts keeps all valid done moves.
- **Wave closer** is the single approved test/automation seam: one CLI entry point that inspects git + system tracker + worktree list and prints machine-readable enough status for agents (human-readable summary + non-zero exit on failure).
- Closer checks (minimum):
  1. Claimed-done task ids are in done archive and absent from active index.
  2. Each wave branch tip is an ancestor of main, or listed under an explicit failed/unmerged set.
  3. No worktrees remain whose path or branch matches the wave’s naming pattern (unless dry-run).
  4. Commits unique to each worker branch do not modify system tracker files (detect ownership violations).
- Optional **cleanup** subcommand or flag removes matching worktrees and deletes local branches that are fully merged to main; default dry-run when destructive.
- **Orchestrator skill** (thin, like cell-orchestrator): launch workers, merge on return, update tracker, run closer; refuse duplicate orchestrators for an active wave id if a simple lock/marker is cheap—prefer documenting “one orchestrator chat” if a lock is heavy.
- **AGENTS.md / process pointer**: short “parallel system tasks → wave skill + closer” without duplicating world queue rules.
- Naming: use glossary **system tracker**, **worldbuilder**; avoid calling this “the queue” or “cell orchestrator”.
- Do not require a git remote or Cloud Agents; local worktrees are enough. Cloud remains optional when a remote exists.
- Immediate leftover from the retro (#22 merge) is orthogonal cleanup already partly done; this spec is the lasting workflow, not a one-off merge ticket.

## Testing Decisions

- Good tests assert **external behaviour** of the wave closer: given a fixture git repo (or temp clone) with planted branches, worktrees metadata, and tracker JSON, the command’s exit code and reported unfinished items match expectations. Do not assert internal helper structure.
- Prefer **one seam**: the wave closer CLI. Skill text is verified by following it in a dry run, not by unit-testing markdown.
- Prior art: existing script CLIs under the worldbuilder `scripts/` tree with colocated `unittest` modules (handoff, cell sources, footprint overlap style) — follow that pattern for the closer.
- Cases to cover: all green; unmerged branch; tracker still listing a done id; worker commit that touched tracker files; leftover worktree; dry-run does not delete; cleanup removes only wave-matched worktrees.
- Avoid testing Cursor Task/subagent APIs; treat worker launch as outside the automated seam.

## Out of Scope

- Changing how the world QUEUE, site lock, or cell-orchestrator works.
- Auto-partitioning or auto-resolving semantic code conflicts between tickets (hints only).
- Requiring GitHub/GitLab remotes or Cloud Agents.
- Replacing the Matt system tracker format (`tasks/index.json` + `done.json`).
- One-off merge of already-finished historical branches except as manual cleanup outside this feature.
- Playwright/UI or Minecraft runtime tests.

## Further Notes

- Retro trigger (2026-10-17): parallel worktree wave left `#22` committed on a branch but unmerged; almost every merge conflicted on the system tracker; duplicate orchestrator chats appeared; worktrees left attached and sometimes mislabeled.
- Approved seam (human, same session): system-tracker wave closer postconditions only.
- Issue tracker for this effort: local markdown under `.scratch/system-tracker-wave/` (see `docs/agents/issue-tracker.md`).
- Next natural skill: `/to-tickets` against this spec.
