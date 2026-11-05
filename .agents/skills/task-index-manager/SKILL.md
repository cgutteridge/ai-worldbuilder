---
name: task-index-manager
description: Manage coding tasks and bugs using tasks/index.json as the single source of truth, through the project's task CLI where there is one. Use when reading or writing tasks, todos, or bugs in a git repo, or when asked to list, update, add, or resolve work items.
---

<!-- Installed from agent-kits local-tasks@0.1.0. Change it in the kit, not here. -->

# Task Index Manager

Part of the `local-tasks` kit. The project's own tracker guide (`docs/agents/issue-tracker.md`) says where the trackers are and adds project rules; read it first if it exists. Where it disagrees with this skill, the project guide wins.

## Rules

- Never infer task state from markdown files. Read `tasks/index.json` first.
- `tasks/index.json` holds active tasks; `tasks/done.json` holds completed ones. Markdown is a reflection, never the source.
- Valid statuses: `todo` | `doing` | `blocked` in `index.json`; `done` only in `done.json`.
- IDs are immutable: never reassign or reuse them.
- `nextTaskId` in `index.json` is the next id for a **new** task. It must stay strictly greater than every id in both files. After adding tasks, set it to the previous value plus the count added, or recompute `max(id across both files) + 1` if unsure. Never lower it when archiving.
- Preserve both `_comment` keys on every write.

## Use the CLI for lifecycle changes

If `scripts/tasks.mjs` exists, use it instead of hand-editing JSON for these operations (it preserves unrelated bytes and refuses invalid transitions):

```sh
node scripts/tasks.mjs list --ready          # ids, priorities, titles of ready todo tasks
node scripts/tasks.mjs show ID               # the whole ticket (falls back to done.json)
node scripts/tasks.mjs status ID doing       # start work
node scripts/tasks.mjs status ID todo --reason "Short explanation"
node scripts/tasks.mjs close ID --sha FULL_SHA --note "Short note"
```

Exit codes: 0 success, 1 invalid files or failed write, 2 invalid arguments or refused lifecycle change, 3 missing task. A project may wrap this as an npm script; use the wrapper if its guide names one.

Adding a task and editing titles, notes, priority or blockers are still done by editing `tasks/index.json` directly, following the schema below.

## Workflow

1. **Read** `tasks/index.json` (and `docs/agents/issue-tracker.md`).
2. **Normalise** any new task: `id` = current `nextTaskId`, status `todo`, then increment `nextTaskId` once per new task.
3. **Update** state through the CLI where it covers the change, otherwise in `index.json` only.
4. **Reflect** into markdown only if the project maintains it, never contradicting `index.json`.

## index.json schema

```json
{
  "_comment": "AGENT: always read this file via the task-index-manager skill before reading or writing any task state.",
  "doneRef": "tasks/done.json",
  "nextTaskId": 31,
  "tasks": [
    {
      "id": 1,
      "title": "Short title",
      "status": "todo",
      "priority": "high",
      "complexity": "m",
      "blockedBy": [],
      "notes": "optional detail"
    }
  ]
}
```

| Field | Values | Meaning |
| --- | --- | --- |
| `priority` | `critical` `high` `medium` `low` | How urgently this needs doing |
| `complexity` | `xs` `s` `m` `l` `xl` | Rough effort estimate |
| `blockedBy` | array of task ids | Cannot start until all listed ids are done |

- `blockedBy: []` means no blockers. A task whose `blockedBy` is non-empty and unresolved has `status: "blocked"`.
- When a blocker is resolved, remove its id from every `blockedBy` list and set those tasks to `todo` if they were `blocked`. Every remaining reference, including one to a missing id, keeps a task out of the ready list.
- Projects may add fields (for example `kind`, tags). Keep them; do not remove fields you do not understand.

## done.json schema

```json
{
  "_comment": "AGENT: completed tasks archive. Read via task-index-manager skill. Do not add active tasks here.",
  "tasks": [{ "id": 1, "title": "Short title", "status": "done", "notes": "optional detail" }]
}
```

## Moving a task to done

- Remove it from `index.json` and append it to `done.json` with status `done` and a note citing the merged commit SHA when the project delivers by commit. `close` does this.
- Never write `done` into `index.json` or any other status into `done.json`.

## Output

After each operation report: new tasks added (id and title); updated tasks (id, old status to new status); next suggested actions.
