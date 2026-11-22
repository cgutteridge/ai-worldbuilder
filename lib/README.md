# `lib/` — shared libraries

Importable tool code, grouped by topic. Bare imports (`import cell_journal`)
still work: `script_env` puts each `lib/<category>/` directory on `sys.path`
ahead of `scripts/` (where CLI wrappers with the same names live).

| Category | What lives here |
| --- | --- |
| `geo/` | World frame, cell maths and segment distances (`cellmath.segment_distance`), OSGB / WGS84 helpers |
| `blocks/` | BlockStore; block IDs and state properties (`block_store.block_id`, `state_props`, `state_prop`); surface and plant recognition (`surface_probe`); MCP / player helpers, atomic JSON |
| `journal/` | Cell journal, registry helpers, registry coverage rollup |
| `sources/` | Place sources, lidar, vector map, OSM footprints |
| `layers/` | Ground / trees / decor passes; shared ownership, mask and probe helpers (`layer_common`); seeded ground dressing (`ground_dressing`); tree voxels, masks |
| `builds/` | House and feature builders, roof geometry, shared door-stoop helpers (`stoop.py`), half-block-cell roof fit (`roof_octant.py`) |
| `site/` | site_kit (canonical `AIR_IDS`; callers name their own air extras/exclusions), lock, handoff, recover, status, cleanup |
| `evidence/` | Evidence / contact sheets, registered aerials |
| `audit/` | Floaters, roof lint, name / stale-term / documentation-path audits |

Colocated `test_*.py` modules are discovered by `scripts/run_tests.py`.

Agent-facing commands stay under `scripts/` (often thin wrappers that run the
library module as `__main__`). Prefer `python3 scripts/<name>.py …` so skills
and rules keep working without path rewrites.
