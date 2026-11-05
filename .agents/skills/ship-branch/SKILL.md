---
name: ship-branch
description: >-
  Finish a task/* code change: test, review, fix blockers, merge. Use when the
  user says ship branch, merge my branch, finish this ticket, or wants the
  build-review-merge loop after implementing on a task branch.
---

Builder session on a `task/*` branch after the code change is written. Loop:
test → review → fix blockers → re-review (cap 3) → merge. Workers never edit
`tasks/index.json` or `tasks/done.json`. Merge only via
`python3 scripts/ship_branch.py merge <branch>`.

## Opt-outs (owner words or ticket)

| Signal | Path |
| --- | --- |
| `no review` / `skip review` | Build → test → merge (no reviewer). |
| Ticket field `review: skip` | Same as skip review. |
| `leave on branch` / `don't merge` | Stop after build (and tests if you ran them). Report the branch name. Implies no review and no merge. |

Everything else in this skill is automatic once invoked. Ask the owner before
using Opus or any other high-cost model; default models need no ask.

## Loop

Work in the branch worktree. Round counter starts at 0; each fixer dispatch
(including a pre-review test-fix) increments it. Cap is **3**.

### 1. Tests

Prefer a focused suite from the branch diff (same mapping `ship_branch`
uses): map `main...HEAD` changed paths to module stems, then:

```bash
python3 scripts/run_tests.py --only test_foo --only test_bar
```

Fall back to the full suite when the map is empty but code changed, or when
the branch touched `run_tests` / `pre_commit_checks` / `ship_branch` / git
hooks. Docs/tracker/skill-only diffs may skip tip tests. Done when the
command exits 0. On failure → step 3 (fixer), then back here. That fixer hop
counts toward the cap.

### 2. Reviewer (fresh context, read-only)

Dispatch a **read-only** subagent: Sonnet, high effort, new context. It must
not edit files or run mutating git commands.

**Fixed brief** (give all of this; do not invent a different checklist):

1. Diff: this branch vs `main` (`git diff main...HEAD` and the commit log).
2. `CODING_STANDARDS.md` (judgement; mechanical rules are the hook).
3. `GLOSSARY.md` and accepted ADRs under `docs/adr/`.
4. The ticket text from `tasks/index.json` (title + notes for this id).
5. Test coverage for the change (did `run_tests.py` exercise what landed?).

**Return shape** (only):

- `APPROVE` — ready to merge, or
- `CHANGES` — list findings, each tagged **blocker** or **nit**.

Done when the return is exactly one of those verdicts with findings tagged.
Nits do not loop: open a tracker follow-up later (orchestrator on main), or
drop if trivial. Only blockers continue the loop.

### 3. Fixer (same worktree)

On `CHANGES` with blockers, or on test failure: dispatch a **fixer** at the
default model and normal effort in this worktree. Fix blockers only (or the
failing tests). Do not chase nits.

Done when the fixer reports the blockers/tests addressed and exits. Increment
the round counter. If rounds > 3 → **stop** (below). Else re-run tests
(step 1), then reviewer again (step 2).

### 4. Main moved

If `main` advanced while the branch was open, the fixer merges `main` into the
branch (`git merge main` in the worktree). Re-review only when the resolution
changed more than conflict-marker cleanup (real code/docs edits beyond
markers). Marker-only resolution → continue without a fresh review round.

### 5. Merge

On `APPROVE` (or after skip-review with green tests):

```bash
python3 scripts/ship_branch.py merge <branch>
```

Done when that command exits 0. It merges into `main`, re-tests, cleans up the
local branch, and pushes `main` to its upstream (or `origin`). Do not merge by
hand. Use `--no-push` only when the owner asks to keep the merge local.

## Stop (cap or any halt)

Leave the branch **unmerged**. Tell the owner the branch name, last verdict,
and what is left. Play the optional local cue if installed:

```bash
if [ -f "$HOME/Projects/agent-soundboard/scripts/play.py" ]; then
  python3 "$HOME/Projects/agent-soundboard/scripts/play.py" need-you
fi
```

## Model policy

| Role | Model / effort |
| --- | --- |
| Reviewer | Sonnet, high effort, read-only, fresh context |
| Fixer | Default model, normal effort, same worktree |
| Opus / other high-cost | Ask the owner first |

## Tracker

This skill never writes the system tracker. Archiving the ticket is an
orchestrator hop on `main` after merge (see parallel-task-wave / wave owner).

## Check (dry-run verification)

Bootstrapping tickets on `task/*` are merged by the wave orchestrator after a
manual pass — do not burn a full live review cycle just to validate this skill.

Verification performed when authoring:

1. Skill path and frontmatter present under `.agents/skills/ship-branch/` (with a stub in `.claude/skills/`).
2. Commands named above match the repo (`scripts/run_tests.py`,
   `scripts/ship_branch.py merge <branch>`, soundboard `need-you`).
3. Review inputs exist: `CODING_STANDARDS.md`, `GLOSSARY.md`, `docs/adr/`.
4. Mental walk of a trivial noop change: green tests → reviewer APPROVE →
   `ship_branch.py merge` — or CHANGES blockers → fixer (round 1) → re-test →
   re-review, stopping at round 3 with `need-you` if still blocked.
