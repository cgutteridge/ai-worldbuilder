# ADR 005: Branch gate for code changes

**Status:** Accepted (2026-10-22).

## Context

System-tracker work was landing on mixed branches and merges without a single
path through tests and review. Tracker edits on `task/*` were already blocked
by the pre-commit hook (`scripts/pre_commit_checks.py`); code still needed a
clear merge ritual so “committed on a branch” is not mistaken for “on main”.

## Decision

1. **Code on `task/*`.** Tool code and tests land on `task/<id>-…` branches (or
   an equivalent task branch). They go through the **ship-branch** gate
   (`.agents/skills/ship-branch/SKILL.md`): tests, optional Sonnet review,
   blocker fixes (cap 3), then `python3 scripts/ship_branch.py merge <branch>`.
   The owner may opt out with `no review` / `skip review`, `leave on branch` /
   `don't merge`, or ticket field `review: skip`.
2. **Tracker and docs on main.** Tracker-only and docs-only edits commit
   straight to `main` under the existing pre-commit checks. Do **not** invent
   `tracker/*` branches for system-tracker archive hops.
3. **Sweep on start and done.** Agents run `python3 scripts/ship_branch.py sweep`
   from Start and from the closing-a-thread rule (both in AGENTS.md; the project
   `done` skill that first carried it was removed, see the note below), so unmerged
   branches and leftover worktrees stay visible. Sweep may merge
   tracker/docs-only strays on request; it never merges or deletes code strays
   on its own.
4. **Waves.** Parallel waves use the project rules
   (`docs/agents/parallel-task-wave.md`, with the generic skill
   `.agents/skills/parallel-task-wave/SKILL.md`): workers stay code-only; the
   orchestrator runs ship-branch before each merge and alone archives the
   tracker after the branch is on `main`.
5. **Pre-commit.** No hook change required for this ADR: tracker path edits on
   `task/*` remain refused; main may edit `tasks/`. Verified against
   `scripts/pre_commit_checks.py` when accepting this policy.

## Consequences

- Merge is always via `ship_branch.py merge` (or an explicit owner opt-out), not
  an ad-hoc `git merge` that skips tip/main tests.
- A successful merge pushes `main` to its upstream (or `origin`) unless
  `--no-push` is set; a failed push leaves the local merge in place.
- Unmerged code branches accumulate until the owner reviews or deletes them;
  sweep only reports them.
- Bootstrap tickets that introduced the gate (#61–#65) used orchestrator manual
  pass + merge; later waves use the full ship-branch path.

## Amendment (2026-11-05)

The project `done` and `parallel-task-wave` override skills were replaced. The
sweep-on-close rule now lives in AGENTS.md ("Closing a thread"), and the wave
gate lives in `docs/agents/parallel-task-wave.md`, read by the generic
`parallel-task-wave` skill installed from the `local-tasks` kit. The decision
itself is unchanged.
