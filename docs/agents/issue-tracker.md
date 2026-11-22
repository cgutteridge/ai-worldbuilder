# Issue tracker

There is one work tracker for tool engineering, and world work has its own.

| Work | Where it is tracked |
| --- | --- |
| Tool engineering (scripts, libraries, skills, providers, process) | `tasks/index.json` (active: `todo`, `blocked`, and `doing` if an orchestrator sets it; no skill here requires it) and `tasks/done.json` (completed). Read and change them through the `worldbuilder-tasks` skill. |
| World work (cell registry, issues, place builds) | The active world's own repo under `worlds/` (ADR 002). Never add it to `tasks/`. |

If it is unclear whether work is tool or world, ask (ADR 002).

## When a skill says "publish to the issue tracker"

For tool work, add a task to `tasks/index.json` through the `worldbuilder-tasks` skill. Put a specification in `docs/specs/` or `docs/plans/` and link it from the task notes. Do not create ticket files. When that ticket is done and no open ticket still cites the file, move it to `docs/archive/` (history, not current guidance). Place history, including a world's snags and dry-run notes, belongs in that world repo.

## When a skill says "fetch the relevant ticket"

Read the task by id from `tasks/index.json` (or `tasks/done.json` if it is not in the active index). The user will normally pass the id.

Tasks carry no triage label, only a status. Workers on `task/*` branches never edit the tracker; the orchestrator on `main` does (ADR 005).

## Tracker edits: main, then commit at once

Every change under `tasks/` (add, status, update, close, priority/blocker edits) is made on `main` and committed immediately in its own tracker commit. Do not leave tracker edits uncommitted, park them on a `task/*` branch, or batch them with unrelated code. The pre-commit hook already refuses tracker edits on worker branches.

## Claiming a task (`status doing`)

`status ID doing` writes who took the task and where they are working onto that row in `tasks/index.json`:

| Field | Meaning |
| --- | --- |
| `claimedBy` | `cursor`, `claude`, or `codex` (the app holding the work) |
| `workingIn` | Absolute path of the main checkout or the worktree |

The CLI insists on `--app` and `--working-in`. This is for coping with parallel sessions and leftover worktrees, not a runner gate. Builder tasks still need the separate `--tool` / `--model` / budget options from [model and effort](model-and-effort.md#task-model-requirement).

When returning a task to `todo` (cancel), the CLI clears `claimedBy` and `workingIn`. Pass `--branch NAME` when work was left half-done so the cancel notes record that branch.

## Builder-task flag on creation and updates

Follow [task model requirement](model-and-effort.md#task-model-requirement)
for classification, flag changes and dispatch. The CLI requires
`--builder-task true|false` on `add` and scope `update`; the pre-commit hook
validates staged flags, including tracker-only commits.

## Sizing tasks

A task should be achievable, self-contained and shippable: one branch, its own tests,
and an end state that can merge on its own. If it cannot be described that way, split it.

A task with a substantial amount of non-builder work that also has some builder work
is split in two: a non-builder task (any model; fixtures and tests) and a builder task
blocked by it. The builder task then holds only the steps that test the model's
decisions. A task that is almost all builder work with a little code stays one builder
task. Say in the builder task's notes which non-builder task it follows.

## Task CLI

`scripts/tasks.mjs` (a project-maintained extension of the `local-tasks` kit;
Node only, no dependencies) lists, shows and changes tasks without disturbing
unrelated tracker bytes:

```sh
node scripts/tasks.mjs list --ready
node scripts/tasks.mjs show ID
node scripts/tasks.mjs add --title "Title" --priority medium --complexity s --builder-task false --notes "Details"  # or --notes-file PATH
node scripts/tasks.mjs update ID --builder-task true --notes "Updated scope"  # or --notes-file PATH
node scripts/tasks.mjs check-run ID --tool cursor --model sonnet --external-model-budget available
node scripts/tasks.mjs status ID doing --app cursor --working-in /path/to/checkout-or-worktree
# Builder tasks also need the confirmed runner options:
node scripts/tasks.mjs status ID doing --app cursor --working-in /path/to/wt --tool cursor --model sonnet --external-model-budget available
node scripts/tasks.mjs status ID todo --reason "Short explanation"
node scripts/tasks.mjs status ID todo --reason "Paused" --branch task/123-slug
node scripts/tasks.mjs close ID --sha FULL_SHA --note "Short note"
```

Run mutations on `main` only, then commit them immediately (see above). The
pre-commit hook rejects tracker edits on `task/*` branches. Read-only `show`,
`list` and `check-run` are also available to workers. Priority/blocker edits
remain direct edits through the `worldbuilder-tasks` skill. This tracker has no
triage tags, so `list --ready` lists every `todo` task with no `blockedBy`,
marking builder tasks. Readiness alone does not establish runner eligibility:
run `check-run` before dispatch.

The local CLI and its project extensions are tested by `scripts/tasks.test.mjs`,
run through `scripts/test_tasks_cli.py` in `scripts/run_tests.py`. Keep project
policy here rather than changing the installed generic kit skills. The first
`close` may leave a stray comma line in `tasks/done.json`; it is valid JSON, so
do not hand-fix it.

## `.scratch/`

`.scratch/` is not a tracker. It is empty. Early specs and tickets that lived there were removed in commit `12dff3b`, and the specification for that work is in `docs/specs/`. The only thing a skill may still create there is a wayfinding map, below, and only when a wayfinding effort is under way.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a file with one **child** file per ticket.

- **Map**: `.scratch/<effort>/map.md` (the Notes / Decisions-so-far / Fog body).
- **Child ticket**: `.scratch/<effort>/issues/NN-<slug>.md`, numbered from `01`, with the question in the body. A `Type:` line records the ticket type (`research`/`prototype`/`grilling`/`task`); a `Status:` line records `claimed`/`resolved`.
- **Blocking**: a `Blocked by: NN, NN` line near the top. A ticket is unblocked when every file it lists is `resolved`.
- **Frontier**: scan `.scratch/<effort>/issues/` for files that are open, unblocked, and unclaimed; first by number wins.
- **Claim**: set `Status: claimed` and save before any work.
- **Resolve**: append the answer under an `## Answer` heading, set `Status: resolved`, then append a context pointer (gist + link) to the map's Decisions-so-far in `map.md`.
