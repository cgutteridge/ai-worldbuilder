# Outline: shared parallel task-wave skill

Parked project notes → shared skill **authored**: `~/.cursor/skills/parallel-task-wave/SKILL.md`.

Not an ai-worldbuilder implementation ticket.

## Where it lives

| Piece | Home |
| --- | --- |
| Skill | Sibling of `task-index-manager` under shared skills (e.g. `~/.cursor/skills/parallel-task-wave/` or `~/.claude/skills/…`), installed like other personal skills — **not** `.claude/skills/` inside one repo |
| Depends on | `task-index-manager` conventions (`tasks/index.json` + `done.json`) |
| Optional helper | Tiny shared script later if checklists get error-prone; default is skill steps + git CLI only |
| Per-repo glue | One short `AGENTS.md` / process line: “parallel system tasks → `/parallel-task-wave` (or whatever name); not the world QUEUE” |

Suggested name: **`parallel-task-wave`** (or `task-wave`). Trigger: “kick off all tasks in parallel”, “wave”, “parallel worktrees for tasks”.

## Roles

**Orchestrator (one chat only)**  
- Lists unblocked frontier from the task index (skip deferred unless asked).  
- Refuses to start if the main checkout is dirty with unrelated WIP.  
- Spawns one worker per task in an isolated worktree/branch.  
- Merges each finished branch into main as it returns.  
- Alone may move tasks index → done (after merge).  
- Runs the close checklist before declaring the wave finished.  
- Cleans worktrees/branches that are fully merged.

**Worker (N chats / subagents)**  
- Own worktree + `task/<id>-…` branch.  
- Implement + test + commit **code only**.  
- **Never** edit `tasks/index.json` or `tasks/done.json`.  
- Return a short summary: branch, commit, files, tests, blockers.

## Close checklist (the “seam”)

Run after merges (or when recovering a crashed wave). Fail the wave if any fail:

1. Every wave branch tip is on `main` (or explicitly listed as failed/unmerged).  
2. Every claimed-done id is in `done.json` and absent from `index.json`.  
3. No commit unique to a worker branch touched the task index files.  
4. No leftover worktrees still attached to wave branches.  
5. (Optional cleanup step) Remove those worktrees; delete local branches fully merged to `main`.

## Explicit don’ts (from the retro)

- Multiple writers on `main`.  
- Workers marking tasks done.  
- Ending the orchestrator chat before the close checklist.  
- Second orchestrator for the same wave.  
- Confusing this with a **world** queue / cell orchestrator (repo-specific warning only).

## Out of scope for v1

- Auto-resolving code merge conflicts.  
- File-overlap heuristics (mention “serialise if two tickets share hot files” in prose only).  
- Requiring Cloud Agents or a git remote.  
- Replacing `task-index-manager`.

## Build order (when authoring the shared skill)

1. Write `SKILL.md` with roles + don’ts + close checklist.  
2. Smoke it on any repo that already uses `tasks/index.json`.  
3. Add one-line pointers from repos that need it (`AGENTS.md`).  
4. Only then consider a tiny shared closer script if agents keep skipping steps.
