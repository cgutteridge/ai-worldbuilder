# Coding standards (review)

Judgement calls for the **reviewer**, not a second copy of linters or the
pre-commit hook. Mechanical rules belong in `.githooks/` /
`scripts/pre_commit_checks.py`.

## Domain

- Prefer `GLOSSARY.md` terms; do not reintroduce glossary “Avoid” synonyms in
  new APIs or user-facing strings.
- Honour accepted ADRs under `docs/adr/` (especially ADR 002: system tracker vs
  world place memory).

## Placement

- Tool engineering lands in this repo’s `tasks/` and `scripts/` /
  `providers/`. World ISSUES and place memory stay in the active world
  checkout under `worlds/`.
- When a change could be either tool or world work, the patch should make the
  choice obvious (or the ticket should have recorded an ask). Silent dumping of
  world work into the system tracker is a review fail.

## Shape

- Match neighbouring modules: stdlib `unittest`, `pathlib`, thin CLIs, public
  functions with docstrings (`@param` / `@returns` where the file already uses
  them).
- Prefer one clear seam over scaffolding. New helpers earn their keep by being
  called from more than one place or by being the hook/test boundary.
- Provider `package` rows: keep system vs world home coherent with
  `cell_sources.provider_package_dir` / catalogue `home` — do not hard-code a
  single root when both are in play.

## Branch gate and parallel waves

- Code lands on `task/…` and ships via `.claude/skills/ship-branch/SKILL.md` →
  `python3 scripts/ship_branch.py merge <branch>` (ADR 005). Owner opt-outs:
  `no review` / `skip review`, `leave on branch` / `don't merge`, ticket
  `review: skip`.
- Tracker-only and docs-only edits go straight to `main`. Do not invent
  `tracker/*` branches.
- Worker branches commit code and tests only. Tracker archive is an
  orchestrator hop after merge. Waves use `.claude/skills/parallel-task-wave/`
  (project override): orchestrator runs ship-branch before each merge.
- The pre-commit hook enforces tracker ownership on `task/*`; review still
  catches “done” claims that never landed on `main`.
- Start and project `done` run `ship_branch.py sweep` so strays stay visible.
