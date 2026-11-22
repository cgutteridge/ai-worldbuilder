# Model and effort for delegated work

Leading words: **stretch** (a task that needs more model or effort than the default), **overnight** (no human to ask).

## Task model requirement

Every active system task carries a boolean `builderTask`. Set it explicitly
when creating a task and reassess it whenever its scope or acceptance checks change.
Keep it through status changes and archival. Completed legacy tasks may omit it.

**Builder task** (`builderTask: true`): the task's process uses an AI model as the
builder. That means any required work where a model interprets place evidence into a
build design or spec, runs a representative build dry run or apply, or judges the built
result in game. This includes spikes, prototypes and trials, even when most of the task
is tool code. Builder tasks use the same models and effort as real build runs (see
`scripts/build_roles.py` and `rules/build-roles.md`), because they test the model's
decisions, not just the code.

**Non-builder task** (`builderTask: false`): no AI model is used in the process. It
may still touch Minecraft (a scripted apply, a read-back scan, a live check with no model
decision), and any model can do the coding. Tool engineering that can be completed with
synthetic fixtures, code review or source research alone is non-builder. Running unit
tests of build code does not make a task a builder task.

If scope grows to include builder work, set the flag before that work starts; clearing
it requires a recorded reason explaining why no AI model is used any more. Sizing rules
for splitting tasks by this flag are in [issue-tracker](issue-tracker.md#sizing-tasks).

**Dispatch gate — single task, wave, retry and unattended run.** Read the current
ticket before claiming it, creating its worktree or starting/resuming its builder.
For `builderTask: true`, establish access to the selected Claude Sonnet or Opus
model. Claude Code can run it directly. Cursor can also take the task when Claude
access and sufficient **external-model budget for the planned work** are confirmed.
Record the model, host and current budget evidence in the dispatch brief/run log;
an installed model name or configured default alone is not evidence of access or
budget. Unknown or exhausted budget leaves the task pending. Budget/access failure
mid-task stops further work; report the reason and hand off to an eligible runner.
Recheck on retry or handoff. Never silently substitute another model family.

Local identifier verification for #344 (2026-11-21): installed `cursor-agent`
version `2025.09.12-4852336` maps its `sonnet` command to `sonnet-4` and its
`opus` command to `opus`; its `--model` help also advertises
`sonnet-4-thinking`. These identifiers already pass the gate and now have
synthetic CLI coverage. No version-first Claude identifiers were found in this
runner, so no additional identifier format was added. This checks local naming
only, without a model call; it does not establish current access or budget.

Run the read-only gate using the actual intended runner, for example:

```sh
node scripts/tasks.mjs check-run 323 --tool claude-code --model sonnet
node scripts/tasks.mjs check-run 323 --tool cursor --model sonnet --external-model-budget available
```

The CLI checks caller-supplied capabilities; it cannot query a host's account or
credit balance. Supply `available` only after establishing the budget above.
`check-run` also refuses blocked/completed tasks. Pass the same runner options to
`status ID doing` when claiming a flagged task. A refusal changes no task state;
leave it pending and report why (preserve unrelated holds and blockers).

Representative build trials use the normal designer/builder models and effort
from `scripts/build_roles.py --world <world> show` and `rules/build-roles.md`,
including separate roles when configured. `sonnet` above illustrates family
eligibility only; the gate does not select or override the build configuration.
Record actual models/efforts, tokens (and measurement method), elapsed time and
build verdict in the findings. A numerical detector experiment alone does not
replace the required build-agent trial. The flag does not authorize a cost or
effort increase; the existing stretch and high-cost approval rules still apply.

## When a task is a stretch

Default is the session's own model at medium effort; mechanical work goes cheaper. A task is a **stretch** when any one holds:

- it changes a schema or the journal, with a migration;
- it changes planner geometry or logic that spans layers (decor, ground, trees);
- the spec is ambiguous or the task is a diagnosis, not an implementation;
- it is the first pass at something new, or a mistake reaches the live world or is hard to undo.

A tracker `complexity` of `l` is a stretch by default.

## Owner present

Before starting a session or a subagent for a stretch, say in one line what you want (model or effort), why, and what it costs (more tokens, slower), then wait for a yes. Use the answer in `start_session` (`model`, `effort`). Do the same the other way for mechanical work on a costly model. Never raise either unasked.

## Overnight (run-all-night, loop, scheduled, no one to answer)

Start only tasks the default (or the ceiling the owner named for the run) can do well. Skip every stretch task and leave it `todo`. Never run a stretch task on a weaker model, and never raise the level unasked. End with a skipped list: task, why it is a stretch, the model and effort it needs, so the owner can start those by hand.
