---
name: worldbuilder-tasks
description: Manage ai-worldbuilder tool-engineering tasks in tasks/index.json using the project CLI. Use for listing, adding, updating, dispatching or closing system tasks; world cells and snags use the world registry.
---

# Worldbuilder tasks

Read [the project tracker guide](../../../docs/agents/issue-tracker.md) first.
It owns task placement, CLI commands, sizing and spec archival. Before creating,
changing scope or dispatching work, follow its model-policy pointer.

## Workflow

1. Read `tasks/index.json`; use `tasks/done.json` for completed-task checks.
   These JSON files are the source of truth, rather than Markdown reflections.
2. Use `node scripts/tasks.mjs` for operations it supports. Workers may run
   read-only `list`, `show`, `tree` and `check-run`; mutations belong to the
   orchestrator on `main`, committed immediately after each change. See the
   guide for command arguments, claim fields (`--app`, `--working-in`) and the
   dispatch gate.
3. For direct edits (priority/blockers), preserve unknown fields and both
   `_comment` keys. IDs are immutable; `nextTaskId` stays above every active or
   archived ID and is never lowered. New tasks start `todo`.
4. Close only after the branch lands on `main`, citing the full merged SHA.
   The CLI moves the record to `done.json` and unblocks dependents. Any unresolved
   or missing blocker keeps a task out of the ready list.

After a mutation, commit the tracker change on `main`, then report added/updated
IDs, status changes and the next action.
