# `lib/` — shared libraries

Importable tool code, grouped by topic. Bare imports (`import cell_journal`)
still work: `script_env` puts each `lib/<category>/` directory on `sys.path`
ahead of `scripts/` (where CLI wrappers with the same names live).

| Category | What lives here |
| --- | --- |
| `geo/` | World frame, cell maths, OSGB / WGS84 helpers |
| `blocks/` | BlockStore, MCP / player helpers, atomic JSON |
| `journal/` | Cell journal, registry helpers, coverage |
| `sources/` | Place sources, lidar, vector map, OSM footprints |
| `layers/` | Ground / trees / decor passes, foliage, masks |
| `builds/` | House and feature builders, terrace, entrances |
| `site/` | site_kit, lock, handoff, recover, status, cleanup |
| `evidence/` | Evidence / contact sheets, registered aerials |
| `audit/` | Floaters, roof lint, name / stale-term audits |

Colocated `test_*.py` modules are discovered by `scripts/run_tests.py`.

Agent-facing commands stay under `scripts/` (often thin wrappers that run the
library module as `__main__`). Prefer `python3 scripts/<name>.py …` so skills
and rules keep working without path rewrites.
