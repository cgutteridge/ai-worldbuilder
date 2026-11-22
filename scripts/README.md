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
| Repo hygiene | `run_tests.py`, `pre_commit_checks.py`, `ship_branch.py`, `install_git_hooks.py` (shared subprocess helpers: `shared_git.py`; hook environment scrubbing is opt-in) |
| Live site | `site_views.py`, `site_patrol.py`, `redo_here.py`, `element_undo.py` |
| Feature builds | `house_build.py`, `feature_build.py`, `reapply_feature.py`, `register_builder.py`, `builder_audit.py`, `roof_aerial.py` (flat-roof colour + PV rows from an aerial) |
| Audits | `lidar_audit.py`, `tree_review.py`, `door_audit.py`, `airtight.py` |
| Bootstrap | `script_env.py` (path preamble; import this, do not move it) |

World-provider shims (`buildings`, `portals`, `osm_extract`, `aerial_mosaic`,
`tpo_fetch`) live under `shims/`; invoke them as `python3 shims/<name>.py --world <world> …`.

The closed bake-off CLIs stay in `bakeoff/` (`model_bakeoff.py`,
`feature_selection_check.py`), not under `scripts/`.

## Path bootstrap

`script_env.ensure_import_paths()` loads `lib/*/`, then `shims/`, then
`scripts/`. Tests use the same layout via `run_tests.py`.

## Reapplying features

`reapply_feature.py --world WORLD FEATURE` validates the current recorded or
explicitly registered builder and previews its plan. With `--apply`, it validates
before undo, then undoes and rebuilds under one world lock. It runs the current
builder and spec; it does not promise exact historical blocks.

For an older feature without a builder record, first inspect its build and spec.
`builder_audit.py --world WORLD` lists the latest landed build sequence. After
verifying which supported spec builder belongs to that exact entry, preview an
explicit registration:

```sh
python3 scripts/register_builder.py --world WORLD FEATURE --seq BUILD_SEQ \
  --builder house_build --spec site/builds/FEATURE.json \
  --reason "How this builder and spec were verified for this build"
```

Use `--builder feature_build` for that spec schema. Relative spec paths belong to
the world's root; absolute paths remain absolute. The command checks the spec
schema, feature name and live build sequence. The reason records your verification;
the command cannot establish historical block equality. Add `--apply` to append
the registration under the world lock. Registration changes no blocks and preserves
unknown historical provenance; a normal reapply can then use the registered builder.
It refuses to replace an existing builder record or registration.

Both direct spec builders refuse `--apply` over an existing landed build unless
`--overwrite` is explicit. That override leaves old blocks outside the new plan
in place. Prefer `reapply_feature.py --apply` for an undo-first rebuild.
