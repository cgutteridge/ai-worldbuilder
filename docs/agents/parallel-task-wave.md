# Parallel task wave settings

The ai-worldbuilder side of the `parallel-task-wave` skill (installed from the `local-tasks` kit).

## Model gate before launch

Apply the [task model requirement](model-and-effort.md#task-model-requirement)
before each dispatch, including retries and handoffs. Copy its required ticket
flag, model/host, budget evidence and policy link into the worker brief.

## Gate before each merge

Before merging each returned worker branch, the **orchestrator** runs the
**ship-branch** loop (`.agents/skills/ship-branch/SKILL.md`) on that branch:
tests, review, fix blockers (cap 3), then `python3 scripts/ship_branch.py merge <branch>`.

## Test and run rules for workers

The orchestrator copies these worker rules into each worker's brief, because the
generic skill's brief does not include them. Workers stay code-only and should use `python3 scripts/run_tests.py --only ...`
for tip checks when the path map is non-empty. Full suites are single-flight
(fail-fast lock under the git common dir): a second overlapping full run exits
2, and `ship_branch merge` refuses while the lock is busy; retry when free.
Workers never review, never merge themselves, and never edit
`tasks/index.json` or `tasks/done.json`. Tracker archive remains an
orchestrator hop on `main` after the merge lands.

## Wave lock

Use `python3 scripts/pre_commit_checks.py wave-lock-acquire` and `wave-lock-release`
as the skill describes.

## Opt-outs

Owner opt-outs on a returned branch follow ship-branch (`no review` /
`skip review`, `leave on branch` / `don't merge`, ticket `review: skip`).

## Bootstrap

Tickets that introduce the gate itself (branch-gate wave, historically #61 to #65)
may use an orchestrator manual pass plus `ship_branch.py merge` without the
full review loop. Later waves use the full ship-branch path.
