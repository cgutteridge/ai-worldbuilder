# Railway trial prototype (task 256), reference only

Throwaway scripts from the 2026-11-22 trial, kept as `.py.txt` so nothing runs by accident. Findings:
[../railway.md](../railway.md). The production builder should be a clean port into `lib/` with tests, not a copy.

| File | Does |
| --- | --- |
| `plot_rail.py.txt` | Reads the railway GeoJSON, converts to MC x,z, writes `rail_mc.json` (way id, name, bridge flag, tags, points). |
| `list_rail.py.txt` | Prints the ways in MC coordinates. |
| `rail_terrain.py.txt` | Caches lidar DTM `terrain` for a list of cells into `terrain.npz`. |
| `rail_plan.py.txt` | Chains ways into tracks, snaps to a rail-legal 4-connected path, fits the integer level profile (DP), builds the corridor columns, writes `plan.json`. |
| `rail_apply.py.txt` | Structure backups in tiles, box fills, rails; run under `site_lock.py run`. |
| `rail_verify.py.txt` | Scans the world and compares rail shapes to the plan. |
| `rail_power.py.txt`, `power_fix.py.txt`, `power_verify.py.txt` | Powered rail and torch pass, retry of misses, and its check. |

Known shortcuts to replace: hard-coded scratchpad and repo paths; way ids and track chains picked by hand;
the junction cells are written twice (last write wins); no journal entry; bridge segments built as embankment.
Extract used: `shims/osm_extract.py --world southampton-build --bbox-mc 6650,-4800,7500,-3800 --layers railway --dir rail_trial_256`.
