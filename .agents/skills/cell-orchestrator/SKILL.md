---
name: cell-orchestrator
description: Run the worldbuilder cell registry as a thin orchestrator. Builds each next task in a fresh session so the main context stays small. Use when the user says "run the queue", "orchestrate" or invokes /cell-orchestrator.
---

You are a thin orchestrator. You do not build anything yourself. Your context must stay small.

## Source of truth

Progress is the **cell journal** (`registry/journal.jsonl` via `cell_registry.py`). Snags live in `registry/issues.json` (`scripts/issues.py`) and never block a cell or steer `next`.

```bash
python3 scripts/cell_registry.py --world <world> next
```

That command is read-only and deterministic: the next undone item (marker cell if not DONE, else nearest undone non-held cell). Prepared-cell order: ground → features → trees → decor → marker-clear. Unprepared cells return `needs-prep` before a build. `next` does **not** raise the marker; the build task does.

Do **not** use retired queue / aerial-review / focus files, coverage sign-offs, `site/ISSUES.txt` I-nnn lines, or `site_handoff.py` queue writing as progress sources. A handoff is just a fresh session that runs `next` again. Mess in a cell → `python3 scripts/cell_registry.py --world <world> reconcile [--fix]`.

## Stop point

Default: stop after ONE task from `next` has finished. The user may widen it ("whole cell", "N items", "N cells", "until HH:MM", "overnight"). Never go past the stop point without asking.

## Overnight / unattended mode

Used when the user says "overnight" or "nobody is watching". Keep a run-local set of excluded cells.
For `needs-prep`, write the cell and all reasons to the run log, add it to that set, and request
`next` again with one `--exclude-cell tx,tz` per excluded cell. A readiness skip is not a build
attempt or failure. Keep exclusions in the run context: they never become permanent holds or snags.
When `next` returns `blocked`, report that unfinished cells remain but none are runnable and stop.
A null task means the existing registry universe is complete/empty; do not invent new cells.
Preparation, missing tiles and aerial acquisition remain explicit human-directed work.

For a build failure, log a PROBLEM, retry at most once, recover an interrupted apply with
`site_recover.py`, or reconcile block drift, and exclude that cell before continuing. Stop the
whole run on a fresh lock held by another owner, two consecutive failed cells, a repeated
API/tool error, a repeated patrol flag across features, an ambiguous task, or the stated time/
cell/item limit. Never touch hand-made cells, commit, or edit shared scripts unattended.

## Keep the human informed (every cell, every mode)

After each cell is verified, write one block to the human run log, then move the marker:

`python3 scripts/site_status.py --world <world> log --cell <tx,tz> --did "<what was built, with counts>" --look "<x,z: what to check>" [--look ...] [--problem "<anything open or failed>"]`

Give at least one concrete `--look` coordinate per cell. When the run ends, run `python3 scripts/site_status.py --world <world> morning --since "<run start>"`, put a one-paragraph summary at the top of `site/RUN-LOG.md`, and give the user the same in your final message. Finish by refreshing the human pages: `python3 scripts/site_pages.py --world <world>`.

## Preflight (every task)

1. Run `python3 scripts/cell_registry.py --world <world> next` with this run’s exclusions. Read `site/LOCK.json`. Do not read retired queue / aerial-review / focus / ISSUES.txt files.
2. If LOCK.json has an owner and its heartbeat is under 90 minutes old (`scripts/site_lock.py` STALE_MIN), STOP and tell the user who holds it. Never clear, steal or edit the lock by hand.
3. If the lock is stale, or `python3 scripts/site_recover.py --world <world> status` shows an in-flight/rolled-back entry, run `python3 scripts/site_recover.py --world <world> recover <cell> --apply` for that cell first (dry run without `--apply`; never on a fresh lock), then continue. For block drift without an in-flight entry, use `python3 scripts/cell_registry.py --world <world> reconcile`.
4. Check for a running session already on this task.

## Named-cell preparation

Before starting a cell outside queue order, run:

```bash
python3 scripts/cell_registry.py --world <world> check --cell <tx,tz>
python3 scripts/cell_registry.py --world <world> prepare --cell <tx,tz>
```

Both are read-only. An explicitly requested `prepare --apply` claims/releases the world lock,
uses the local PBF, records missing stable feature plans, and applies a cached-lidar ghost only
for the cold focus cell. It verifies each result and preserves partly built cells. Missing PBF,
lidar or full aerial coverage remains a blocker. Tile downloads require the source, filename and
size to be stated and human authorization; preparation never fetches implicitly. The marker moves
at build start. Marker raising can ensure a ghost frontier; report that separate scope/cost.

## Cell completion gate

Use `check`/`status`/`verify` for effective completion, rather than the journal's map-free `done`
flag. A cell needs current validated evidence, landed features and fresh trees/decor, marker down,
and resolved in-flight work. Legacy validation/layers require preparation/review. Ground redo
preserves trees/decor completion; successful trees generation requires decor again. Newly landed
or rebuilt features invalidate trees/decor across their touched cells.

## Per task

0. Take the JSON from `next`. Handle `needs-prep` and `blocked` as above. Kinds `layer`
   (ground|trees|decor) and `feature` are build work. `marker-clear` means content is finished:
   clear the marker under the world lock, then verify effective completion. Recheck readiness
   before each apply; direct builders also enforce the shared policy.
1. Raise the marker for the target cell if needed (`python3 scripts/cell_marker.py --world <world> --raise tx,tz` under SITE_OWNER) — `next` does not.
2. **Build roles** (features, trees, decor — not ghosts): resolve designer + builder before spawning anyone:

   ```bash
   python3 scripts/build_roles.py --world <world> show
   # overrides: resolve --designer … --builder … --tool …
   ```

   Paste `scripts/build_roles.py resolve …` output (or `format_roles_block` text from `rules/build-roles.md`) into every agent prompt so both models are named. Same designer and builder → **one** blocking agent that still does evidence table then apply. Different models → **two** blocking agents in order: (1) designer only — evidence table + hand-off artefact, no apply; (2) builder only — claim lock, apply from the hand-off, readback, release. Hand-off: non-house feature → `site/builds/<name>.json` (`feature_build`); trees/decor → evidence table then layer script. Ground ghost apply stays mechanical (`lidar_ghost`) — no designer/builder.
3. Build: blocking general-purpose Agent(s) per the roles above, foreground, one at a time. Prompt names the task JSON, points at `rules/` (+ `rules/build-roles.md` for feature/trees/decor), and says: claim the lock with a unique owner (builder only), SITE_OWNER, readback 0 mismatches, patrol, clean apply lands the journal entry, release the lock, report in under 200 words.
4. When the agent(s) return: verify. Check the lock is released, `site_handoff.cell_gaps` for that cell is empty, and registry status moved. Do not read the agent's transcript.
5. Report to the user in a few lines. Then stop at the stop point.

Snags go to issues via the `snag` skill (`scripts/issues.py add`); they never block a cell. A rebuild does not close an issue: the owner confirms it in review. End a run with `python3 scripts/issues.py --world <world> digest`.

## Stop and ask when

- the lock is held, or a patrol fails, or the brief flags real risks
- the next task is ambiguous
- anything needs the user's eyes (`snag` skill review when an issue is `fix-landed`)

## Context hygiene

Keep notes to one line per task. If this session grows past a comfortable size, tell the user it is safe to start a fresh orchestrator session that runs `next` again.
