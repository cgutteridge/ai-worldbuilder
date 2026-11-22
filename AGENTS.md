# ai-worldbuilder: agent entry point

Reusable AI tool for building Minecraft worlds from real-place evidence.
Exactly one **active world** at a time (`active_world.py`; CLIs take required `--world <name>`).

## Start

1. Identify the open save with `python3 scripts/which_world.py` (read-only; no selected world/providers; requires local `lsof` process visibility). Unknown or ambiguous identity fails. Pass `--world <dirname>` on place-touching commands (required; no silent default; e.g. `--world my-world`). Confirm: `python3 -c "from active_world import set_world, gameworld_id; print(set_world('my-world'), gameworld_id())"`.
2. Sweep unmerged branches and leftover worktrees: `python3 scripts/ship_branch.py sweep` (ADR 005).
3. System tracker first when work is **tool** engineering: use `worldbuilder-tasks` to read `tasks/index.json`. Before creating/updating tasks or starting/resuming a task builder, follow the builder-task flag and dispatch gate in [model and effort](docs/agents/model-and-effort.md#task-model-requirement). Parallel system tasks: project skill `parallel-task-wave` (not the world cell-orchestrator).
4. If work is **world** (cell registry, issues, place builds), use the active checkout under `worlds/` — do not put world work in `tasks/`. If unclear tool vs world, **ask**.

## Always

- Platforms: tool commands and skills use POSIX shell + `python3` (macOS/Linux/WSL 2); native Windows is WSL-first. Setup and GDAL/PROJ requirements: [instance setup](docs/minecraft-instance-setup.md#tool-platforms-and-dependencies).
- Place memory lives under the active world's `site/`, `registry/`, and `config/` (inside `worlds/`), not in the tool tracker. Progress SoT is `registry/` via `scripts/cell_registry.py`.
- Tests: colocated `lib/<category>/test_*.py` and `scripts/test_*.py`. Run with `python3 scripts/run_tests.py` or `python3 scripts/run_tests.py --only <stem>`. The closed bake-off under `bakeoff/` is historical and stays out of that default suite; `python3 scripts/run_tests.py --bakeoff` opts in.
- Once per clone: `python3 scripts/install_git_hooks.py` (blocks system-tracker edits on `task/*` branches; runs `scripts/run_tests.py`).
- **Branch gate:** follow [ADR 005](docs/adr/005-branch-gate.md#decision) for task branches, direct-to-main documentation and tracker edits and hook checks.
- **Closing a thread:** after the `done` skill's green bar and before `thread-cleanup`, run `python3 scripts/ship_branch.py sweep`. Treat reported code strays or leftover worktrees as gaps unless the owner accepted them. Tracker or docs-only strays may be merged with `--merge-docs-only` when that matches the owner's intent; never auto-merge code.

## Read when needed

- Finding where something lives (tools, configs, providers, registry, bake-offs, ghost and dry-run commands): [docs/agents/layout.md](docs/agents/layout.md).
- Adding a provider, converting coordinates, running a live-world command or a slow command, or using sound cues: [docs/agents/world-and-live-commands.md](docs/agents/world-and-live-commands.md).
- Reading, adding or changing a tool task, or being asked to publish or fetch a ticket: [docs/agents/issue-tracker.md](docs/agents/issue-tracker.md) (the system tracker is `tasks/index.json`; `scripts/tasks.mjs` lists, shows and updates status; mutate on `main` and commit immediately; `status doing` records `--app` and `--working-in` on the task row; never from a `task/*` worker).
- Starting a session or subagent, or planning an overnight run: [docs/agents/model-and-effort.md](docs/agents/model-and-effort.md) (when a task is a stretch, ask before raising model or effort; overnight skips stretch tasks).
- Glossary or ADR work, or exploring an unfamiliar area: [docs/agents/domain.md](docs/agents/domain.md).
- Triage labels: [docs/agents/triage-labels.md](docs/agents/triage-labels.md).
- Adding or renaming a skill: skills live in `.agents/skills/`; `.claude/skills/` holds generated stubs. Run `node scripts/check-skill-stubs.mjs --write`, then `python3 scripts/run_tests.py --only test_skill_stubs`.
- Review judgement: [CODING_STANDARDS.md](CODING_STANDARDS.md) (mechanical rules live in the hook).
