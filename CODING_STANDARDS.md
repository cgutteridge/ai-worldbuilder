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

- Tool engineering lands in this repo’s `tasks/`, `lib/`, `scripts/`, and
  `providers/`. Libraries go under `lib/<category>/`; runnable CLIs under
  `scripts/`. World ISSUES and place memory stay in the active world checkout
  under `worlds/`.
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

## Unit tests

- `python3 scripts/run_tests.py` runs against disposable configuration, with no
  selected world required. Both full and focused runs ignore active-world data.
- Test tool behaviour with explicit synthetic inputs, temporary files and provider
  doubles. Do not read a world's builds, registry, evidence, extracted assets or
  local provider implementations. Tests for world-owned providers belong with
  those providers in the world repository.

## Branch gate and parallel waves

Follow [ADR 005](docs/adr/005-branch-gate.md) and the
[project wave settings](docs/agents/parallel-task-wave.md). Review catches
completion claims whose changes never reached `main`.
