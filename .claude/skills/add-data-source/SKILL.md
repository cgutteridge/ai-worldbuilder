---
name: add-data-source
description: >-
  Wire a chosen evidence provider into the system or local catalogue: provider
  row, SOURCES section, checklist how, manifest tag, host allowlist, fetch stub.
  Use after evaluate-data-source accepts a source, or when the user says add a
  data source. Always ask local vs system if not already decided.
---

Ship config + docs first; fetch code only when keepable imagery or vectors need it.

## 0. Preconditions

- Verdict from `evaluate-data-source` (or the user already named type + provider id).
- **Ask local vs system** if unclear. Rule of thumb: below national (UK / England) → local.
- Read `reference/TYPES.txt`, `config/system-providers.json`, active world `config/local-providers.json`, and that world's `reference/SOURCES.txt` header.

Done when type slot(s), home (local|system), and proposed `id` are agreed.

## 1. Config row

Add a provider object to `config/system-providers.json` (system) or the active world's `config/local-providers.json` (local):

- `id`, `types: [...]`, `scope` (`level` + `description` unless worldwide)
- `keep` / `look_only`, `licence`, `tags`, `kind` (manifest kind or null)
- `checklist: true` only if every cell should attempt it; then `how` is required
- Aliases only when migrating an old key
- `package`: relative path under the tool or world repo (e.g. `providers/ukdefra-lidar`) when fetch/review/manage code lives there

Done when `cell_sources.SOURCE_FAMILIES` (reload) would list it if checklisted:
`.venv/bin/python -c "import cell_sources as c; print(sorted(c.SOURCE_FAMILIES))"`

## 2. Catalogue + trail

1. Add a section under the matching type in `reference/SOURCES.txt` (URL, ToS, fetch).
2. If keepable files: host allowlist entry the fetch script will use; pick a manifest `source` tag matching `tags`.
3. Point at a template script: `aerial_mosaic.py`, `kartaview_fetch.py`, `tpo_fetch.py`, `osm_extract.py`, `defra_lidar.py`.

Done when an agent can find the provider from TYPES → system/local providers → world SOURCES without chat history.

## 3. Dry-run record

For one open cell:

```bash
.venv/bin/python scripts/cell_sources.py --cell TX,TZ --record <id> --result none --note "wiring dry-run"
```

(Use `--retry-reason` if already tried.) Remove the test line from the JSONL if it would confuse builders, or leave a `partial` with note `provider wired, not yet useful`.

Done when `--record` accepts the id and `--todo` still makes sense.
