# `scripts/` — CLI entry points

Runnable tools agents and hooks invoke as `python3 scripts/<name>.py …`.

Libraries live under [`lib/`](../lib/README.md). Many names here are thin
wrappers around `lib/<category>/<name>.py` so existing skill and rule paths
keep working. Import the library (bare name); do not import a wrapper for its
API.

## Typical CLIs (stay here)

| Area | Examples |
| --- | --- |
| Registry / queue | `cell_registry.py`, `dryrun_reset.py`, `cell_marker.py` |
| Repo hygiene | `run_tests.py`, `pre_commit_checks.py`, `ship_branch.py`, `install_git_hooks.py` |
| Live site | `site_views.py`, `site_patrol.py`, `site_audit.py`, `site_index.py`, `redo_here.py` |
| Audits | `lidar_audit.py`, `tree_review.py`, `door_audit.py`, `airtight.py` |
| Bake-off shims | `model_bakeoff.py`, `feature_selection_check.py` → `bakeoff/` |
| Bootstrap | `script_env.py` (path preamble; import this, do not move it) |

World-provider shims (`buildings`, `portals`, `osm_extract`, `aerial_mosaic`,
`tpo_fetch`) live under `shims/`. `scripts/tpo_fetch.py` remains a deprecated
stub for older world `SOURCES` lines; prefer `python3 shims/tpo_fetch.py`.

## Path bootstrap

`script_env.ensure_import_paths()` loads `lib/*/`, then `shims/`, then
`scripts/`. Tests use the same layout via `run_tests.py`.
