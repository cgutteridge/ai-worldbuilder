---
name: parallel-task-wave
description: >-
  Run several tasks/index.json tickets in parallel via git worktrees, with one
  orchestrator that reviews then merges and alone updates the tracker. Use when
  the user says kick off tasks in parallel, parallel worktrees, task wave, or
  merge as agents finish — not for a single-ticket implement, and not for a
  world/place build queue.
---

# Parallel task wave (project override)

This file **specialises** the shared skill at `~/.cursor/skills/parallel-task-wave/`
for ai-worldbuilder. Do **not** edit the global copy from this repo.

Follow the global skill for: frontier pick, wave lock, worktree launch, worker
briefs (code-only), merge-as-they-return shape, tracker archive **after** merge,
close checks, and lock release. Pair with `task-index-manager` for tracker I/O.

Leading words: **wave**, **orchestrator**, **worker**, **close**.

## Branch gate (this repo only)

Before merging each returned worker branch, the **orchestrator** runs the
**ship-branch** loop (`.claude/skills/ship-branch/SKILL.md`) on that branch:
tests → review → fix blockers (cap 3) → `python3 scripts/ship_branch.py merge <branch>`.

Workers stay code-only and should use `python3 scripts/run_tests.py --only …`
for tip checks when the path map is non-empty. Full suites are single-flight
(fail-fast lock under the git common dir): a second overlapping full run exits
2, and `ship_branch merge` refuses while the lock is busy — retry when free.
Workers never review, never merge themselves, and never edit
`tasks/index.json` or `tasks/done.json`. Tracker archive remains an
orchestrator hop on `main` after the merge lands.

Owner opt-outs on a returned branch follow ship-branch (`no review` /
`skip review`, `leave on branch` / `don't merge`, ticket `review: skip`).

### Bootstrap

Tickets that introduce the gate itself (branch-gate wave, historically #61–#65)
may use an orchestrator **manual pass** plus `ship_branch.py merge` without the
full Sonnet review loop. Later waves use the full ship-branch path.
