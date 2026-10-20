# Provider-name audit (#28)

Tooling and process for [task #28](../tasks/index.json): callers should ask for a **reference type** (see `GLOSSARY.md`, `reference/TYPES.txt`), not a vendor id, except inside provider packages and catalogues.

## Commands

```bash
python3 scripts/check_provider_names.py              # fail on new line-level hits
python3 scripts/check_provider_names.py --list       # all current hits (JSON lines)
python3 scripts/check_provider_names.py --update-baseline  # freeze after intentional burn-down
```

Pre-commit runs the check via `scripts/pre_commit_checks.py check`. The frozen key set lives in `config/provider_name_audit_baseline.json` (path + line only, not per-token).

## Classifying a hit

| Class | Meaning | Action |
| --- | --- | --- |
| **(a) Legitimate** | The line must name a provider id, shim, or catalogue field (examples in add-data-source, ADR history, `_Avoid_` glossary lines that forbid a vendor). Allowlisted homes: `docs/archive/`, ADR 001, `scripts/ref_fetch_agent.py` (fetch hosts), `scripts/dryrun_reset.py` (wipe/preserve paths). | Keep; stay on baseline or narrow wording without losing meaning. |
| **(b) Code** | Logic should resolve a **type** via `place-sources` / `cell_sources`, not hard-code a vendor path, import root, or cache dir. | Refactor to type-driven lookup; shrink baseline. |
| **(c) Wording** | Docs, rules, or skills tell an agent to pick a vendor where they mean aerial, vector map, or lidar height. | Reword to type language; keep script filenames when pointing at tools. |

## Burn-down order

1. Agent-facing **skills** and **rules** (class **c**).
2. Shared **scripts** docstrings and user-visible messages (**b** + **c**); module renames only when a seam already exists.
3. **ADRs** and archive docs: prefer type vocabulary; keep historical vendor mentions where the ADR is explicitly about the migration.

After each batch: run tests, then `check_provider_names.py --update-baseline` so fixed lines drop off the frozen list.
