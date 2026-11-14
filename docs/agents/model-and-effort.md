# Model and effort for delegated work

Leading words: **stretch** (a task that needs more model or effort than the default), **overnight** (no human to ask).

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
