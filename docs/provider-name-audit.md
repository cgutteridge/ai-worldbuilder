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

## Type-facing seams (post burn-down)

| Need | Call |
| --- | --- |
| Composite lidar world | `lidar_provider.composite_world()` |
| Lidar tile client / helpers | `lidar_provider.tile_client()`, `.mc_box_en`, `.mc_heights`, `.default_heights` |
| Lidar ghosts / ownership | Shared `lib/sources/lidar_ghost.py`; samples through `lidar_provider` (no catalogue role) |
| Lidar query / extremes | `lidar_provider.load_lidar_module('query' / 'extremes')`; stable `shims/lidar_query.py` / `shims/lidar_extremes.py` |
| Lidar readiness tiles / costs | `tile_client(download=False).tile_status(...)` returns missing rows and cached input paths; `LocalEvidence` hashes bytes |
| Vector-map download / cache / clip tree | `vector_map_clips` (`downloads_dir`, `reference_dir`, `vector_map_clip_root`, `project_status`) — type-named `reference/vector_map`, once per world |

Catalogue rows that fill type `lidar` set `modules.world`, `modules.tiles`, `modules.query` and `modules.extremes` (paths under the provider package). The first world lidar row beats system rows; within each home, catalogue order decides. Package resolution and module caching use the chosen file's path, including world overrides of an existing id. Vendor filenames stay inside `providers/` and catalogues. The contract is in [`reference/TYPES.txt`](../reference/TYPES.txt); missing providers raise `NoLidarProviderError`, distinct from uncached tiles.
