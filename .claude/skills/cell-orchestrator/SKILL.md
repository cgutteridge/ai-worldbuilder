---
name: cell-orchestrator
description: Run the Southampton Refine cell registry as a thin orchestrator. Builds each next task in a fresh session so the main context stays small. Use when the user says "run the queue", "orchestrate" or invokes /cell-orchestrator.
---

You are a thin orchestrator. You do not build anything yourself. Your context must stay small.

## Source of truth

Progress is the **cell journal** (`registry/journal.jsonl` via `cell_registry.py`) plus `site/ISSUES.txt`.

```bash
.venv/bin/python scripts/cell_registry.py next
```

That command is read-only and deterministic: open issues first, then the next undone item (marker cell if not DONE, else nearest undone non-held cell). Order inside a cell: ground → features → trees → decor. `next` does **not** raise the marker; the build task does.

Do **not** use retired queue / aerial-review / focus files, coverage sign-offs, or `site_handoff.py` queue writing as progress sources. A handoff is just a fresh session that runs `next` again. Mess in a cell → `cell_registry.py reconcile [--fix]`.

## Stop point

Default: stop after ONE task from `next` has finished. The user may widen it ("whole cell", "N items", "N cells", "until HH:MM", "overnight"). Never go past the stop point without asking.

## Overnight / unattended mode

Used when the user says "overnight" or "nobody is watching". Then: do not ask questions. If a cell fails or looks risky, record it as a PROBLEM in the run log, retry at most once, leave the world at its last good state (`site_recover.py` for an interrupted apply; `reconcile` if blocks drifted), and carry on with the next task. Stop the whole run if the lock is held fresh by someone else, two cells in a row fail, the same tool/API error repeats, or the next task is ambiguous. Never touch hand-made cells, commit, or edit shared scripts unattended. Respect the stated end time or cell count.

## Keep the human informed (every cell, every mode)

After each cell is verified, write one block to the human run log, then move the marker:

`.venv/bin/python scripts/site_status.py log --cell <tx,tz> --did "<what was built, with counts>" --look "<x,z: what to check>" [--look ...] [--problem "<anything open or failed>"]`

Give at least one concrete `--look` coordinate per cell. When the run ends, run `site_status.py morning --since "<run start>"`, put a one-paragraph summary at the top of `site/RUN-LOG.md`, and give the user the same in your final message. Finish by refreshing the human pages: `.venv/bin/python scripts/site_pages.py`.

## Preflight (every task)

1. Run `cell_registry.py next` (and optionally `issues`). Read `site/LOCK.json`. Do not read retired queue / aerial-review / focus files.
2. If LOCK.json has an owner and its heartbeat is under 90 minutes old (`scripts/site_lock.py` STALE_MIN), STOP and tell the user who holds it. Never clear, steal or edit the lock by hand.
3. If the lock is stale, or `.venv/bin/python scripts/site_recover.py status` shows an in-flight/rolled-back entry, run `site_recover.py recover <cell> --apply` for that cell first (dry run without `--apply`; never on a fresh lock), then continue. For block drift without an in-flight entry, use `cell_registry.py reconcile`.
4. Check for a running session already on this task.

## Per task

0. Take the JSON from `cell_registry.py next`. Kinds: `issue`, `layer` (ground|trees|decor), or `feature`.
1. Raise the marker for the target cell if needed (`cell_marker.py --raise tx,tz` under SITE_OWNER) — `next` does not.
2. **Build roles** (features, trees, decor — not ghosts): resolve designer + builder before spawning anyone:

   ```bash
   python3 scripts/build_roles.py show
   # overrides: resolve --designer … --builder … --tool …
   ```

   Paste `scripts/build_roles.py resolve …` output (or `format_roles_block` text from `rules/build-roles.md`) into every agent prompt so both models are named. Same designer and builder → **one** blocking agent that still does evidence table then apply. Different models → **two** blocking agents in order: (1) designer only — evidence table + hand-off artefact, no apply; (2) builder only — claim lock, apply from the hand-off, readback, release. Hand-off: non-house feature → `site/builds/<name>.json` (`feature_build`); trees/decor → evidence table then layer script. Ground ghost apply stays mechanical (`lidar_ghost`) — no designer/builder.
3. Build: blocking general-purpose Agent(s) per the roles above, foreground, one at a time. Prompt names the task JSON, points at `rules/` (+ `rules/build-roles.md` for feature/trees/decor), and says: claim the lock with a unique owner (builder only), SITE_OWNER, readback 0 mismatches, patrol, clean apply lands the journal entry, release the lock, report in under 200 words.
4. When the agent(s) return: verify. Check the lock is released, `site_handoff.cell_gaps` for that cell is empty (or the issue closed), and registry status moved. Do not read the agent's transcript.
5. Report to the user in a few lines. Then stop at the stop point.

Snags go to ISSUES via `cell_registry.py add-issue` (reopens the target). Clean rebuilds auto-close matching issues.

## Stop and ask when

- the lock is held, or a patrol fails, or the brief flags real risks
- the next task is ambiguous
- anything needs the user's eyes (`human-confirm-fix`)

## Context hygiene

Keep notes to one line per task. If this session grows past a comfortable size, tell the user it is safe to start a fresh orchestrator session that runs `next` again.
