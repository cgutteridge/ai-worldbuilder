# Slow commands: run in the background from the start

Foreground shell calls time out at 2 minutes by default (10 maximum). Anything below that
may run past it: start it with `run_in_background` (or poll its log) instead of waiting
and being moved to the background mid-run.

| Command | Typical time | Basis |
| --- | --- | --- |
| Ghost baseline: `lidar_ghost apply --cell X,Z --ring 1` (9 cells; ground then features) | over 10 min; 5-9 min for the journalled ground passes alone, more with features and prep. Larger rings scale per cell | Measured: cell journal `begin`/`finish` timestamps, `worlds/bakeoff/registry/journal.jsonl`. Ground pass 25-55 s per cell (median ~38 s, 9-cell sittings 5-6 min), ghost-features 5-30 s per cell. Whole-command figure is as reported at the R-015 sitting (2026-10-26) |
| Feature/house `--apply` (`feature_build.py <spec> --apply`) | about 2 min end to end | Approximate, reported at R-015. The journalled block write alone is 6-36 s (median ~13 s) per plan; the rest is prep, preview and readback |
| First vector-map footprint load (`osm_footprints`, first guard/frontage query in a dry run or apply) | 1-2 min | Approximate, reported at R-015; not re-timed (building the index object is lazy: 0.4 s) |
| `site_lock.py run` | adds nothing | Wraps any of the above; use it for live commands |

Dry runs without live reads are seconds. Figures are for the bake-off world on the
author's machine; re-measure from the journal (`begin` to `finish` `ts`) rather than
guessing if a pass matters.
