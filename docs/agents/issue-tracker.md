# Issue tracker

There is one work tracker for tool engineering, and world work has its own.

| Work | Where it is tracked |
| --- | --- |
| Tool engineering (scripts, libraries, skills, providers, process) | `tasks/index.json` (active: `todo`, `blocked`, and `doing` if an orchestrator sets it; no skill here requires it) and `tasks/done.json` (completed). Read and change them through the `task-index-manager` skill. |
| World work (cell registry, issues, place builds) | The active world's own repo under `worlds/` (ADR 002). Never add it to `tasks/`. |

If it is unclear whether work is tool or world, ask (ADR 002).

## When a skill says "publish to the issue tracker"

For tool work, add a task to `tasks/index.json` through the `task-index-manager` skill. Put a specification in `docs/specs/` or `docs/plans/` and link it from the task notes. Do not create ticket files.

## When a skill says "fetch the relevant ticket"

Read the task by id from `tasks/index.json` (or `tasks/done.json` if it is not in the active index). The user will normally pass the id.

Tasks carry no triage label, only a status. Workers on `task/*` branches never edit the tracker; the orchestrator on `main` does (ADR 005).

## Claude requirement on creation and updates

Read [task model requirement](model-and-effort.md#task-model-requirement) before
creating, updating or dispatching a system task. Every active task must have an
explicit boolean `requiresClaude`; the pre-commit hook checks staged flags even
on tracker-only commits. Classify from the whole required scope and acceptance
checks. Use the CLI's required `--requires-claude true|false` on `add` and `update`;
scope updates must reassess it, not blindly copy the old value. Direct edits to
priority/blockers must preserve it; direct scope edits must reassess it as well.
Archival retains it. Removing a true requirement needs a recorded scope reason.

## Task CLI

`scripts/tasks.mjs` (a project-maintained extension of the `local-tasks` kit;
Node only, no dependencies) lists, shows and changes tasks without disturbing
unrelated tracker bytes:

```sh
node scripts/tasks.mjs list --ready
node scripts/tasks.mjs show ID
node scripts/tasks.mjs add --title "Title" --priority medium --complexity s --requires-claude false --notes "Details"  # or --notes-file PATH
node scripts/tasks.mjs update ID --requires-claude true --notes "Updated scope"  # or --notes-file PATH
node scripts/tasks.mjs check-run ID --tool cursor --model sonnet --external-model-budget available
node scripts/tasks.mjs status ID doing
# Claude tasks need the same confirmed runner options on status ID doing:
node scripts/tasks.mjs status ID doing --tool cursor --model sonnet --external-model-budget available
node scripts/tasks.mjs status ID todo --reason "Short explanation"
node scripts/tasks.mjs close ID --sha FULL_SHA --note "Short note"
```

Run mutations on `main` only, because the pre-commit hook rejects tracker edits
on `task/*` branches. Read-only `show`, `list` and `check-run` are also available
to workers. Priority/blocker edits remain direct edits through the
`task-index-manager` skill. This tracker has no triage tags, so `list --ready`
lists every `todo` task with no `blockedBy`, marking Claude-required tasks.
Readiness alone does not establish runner eligibility: run `check-run` before
dispatch, including when task status is managed without `status doing`.

The kit copy of `task-index-manager` has the same name as the personal one in `~/.claude/skills`; Claude Code may load the personal skill instead of the project one, so the CLI guidance here may not be what an agent sees until the personal skill is removed; check with `/skills`.

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
