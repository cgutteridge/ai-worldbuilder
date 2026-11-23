# Slow commands: run in the background from the start

## Execution

Use the lock runner for live writes and player-moving captures:

```sh
python3 scripts/site_lock.py --world <world> run <owner> "<task>" -- <command>
```

It claims `site/LOCK.json`, supplies `SITE_OWNER` to the child and releases on
success, failure or interrupt. A fresh lock held by another owner is a stop.
`redo_layer.py --apply` and `reapply_feature.py --apply` own their sequence
locks: call those wrappers directly, without another outer lock runner.

Lock owner names are the whole identity: two sessions that both use `claude` share one lock,
so one session's release (or claim) removes or replaces the other's, and a long sequence then
stops with `World lock not held by claude: None`. `reapply_feature.py --apply` therefore locks as
`<owner>-<pid>` (default `reapply-<pid>`), checks the same registry gate as undo on every touched
cell before it undoes anything, and re-claims the lock between the undo and the rebuild. Give
your own long `site_lock.py run` an owner name no other session uses.

### Recovery: `reapply_feature --apply` stopped after the undo

The stop message says `Undo completed; rebuild did not complete cleanly` and ends with the exact
re-lay command. The plot stays undone until it is run. A lapsed lock is claimed again by the
wrapper; it stops only when another owner holds a fresh lock (the message then says
`Held by <owner> ...`: wait for them to release, then re-lay). Steps:

1. `python3 scripts/site_recover.py --world <world> status` and
   `python3 scripts/site_lock.py --world <world> status`: no unfinished write, lock free.
2. Run the printed line, which has this shape (builder argv as the wrapper resolved it):
   `python3 scripts/site_lock.py --world <world> run <owner> "re-lay <feature>" -- python3 scripts/feature_build.py <spec> --world <world> --apply`
3. Read back: exit 0 and zero mismatches; if not, `cell_registry.py --world <world> reconcile <cell>`.

A stop before the undo (`stopped during registry readiness gate ...`) names the gate and the
blocked cell (for example `needs-prep 51,-36: feature plans: 2 eligible groups unaccounted for`,
each with its `[clear: ...]` command) and has changed nothing: clear that, then retry.

Launch slow commands in the background from the start (or use a tool session
that can be polled); foreground shell calls may time out before they finish.
Keep the whole command's log and final exit status. Wait for completion and
readback before the next live command or a success report. A logging pipeline
must not replace the command's failure status. Failure stops the sequence;
inspect `site_recover.py --world <world> status` for unfinished journal writes,
or `cell_registry.py --world <world> reconcile <cell>` for block drift.

## Typical times

| Command | Typical time | Basis |
| --- | --- | --- |
| Ghost baseline: `lidar_ghost apply --cell X,Z --ring 1 --include-focus` (9 cells; ground then features) | over 10 min; 5-9 min for the journalled ground passes alone, more with features and prep. Larger rings scale per cell | Measured: cell journal `begin`/`finish` timestamps, `worlds/bakeoff/registry/journal.jsonl`. Ground pass 25-55 s per cell (median ~38 s, 9-cell sittings 5-6 min), ghost-features 5-30 s per cell. Whole-command figure is as reported at the R-015 sitting (2026-10-26) |
| Feature/house `--apply` (`feature_build.py <spec> --apply`) | about 2 min end to end | Approximate, reported at R-015. The journalled block write alone is 6-36 s (median ~13 s) per plan; the rest is prep, preview and readback |
| Trees + decor layer `--apply` sequence (`trees_layer.py`, `decor_layer.py`) | about 16 min combined | Reported in the redo-session retro (#219); aggregate observation only, with no separate per-layer timings. Run either apply in the background from the start |
| First vector-map footprint load (`osm_footprints`, first guard/frontage query in a dry run or apply) | 1-2 min | Approximate, reported at R-015; not re-timed (building the index object is lazy: 0.4 s) |
| First lidar fetch for a new world (`lidar_extremes --fetch`; DTM + DSM tiles) | about 1 min per tile per product (45-73 MB each); 4 tiles x 2 products took about 7.5 min and 495 MB | Measured from file times in `worlds/ventnor-build/downloads/defra-lidar/` (Ventnor, 2026-10-28, 18:50:21 first catalogue lookup to 18:57:41 last tile). Sea-edge tiles are smaller (42-46 MB) and quicker. Ran before the progress output existed, so start is the first catalogue lookup, not an exact timer |
| Bottom-half slabs kept in an apply (`McpWriter`, pitched-roof low steps) | one `block_set_state` per slab at about 1.1 s; 500 slabs about 9 min | Approximate, from the paced MCP interval (#209); not re-timed on a live apply |
| `site_lock.py run` | adds nothing | Wraps any of the above; use it for live commands |

Dry runs without live reads are seconds. Figures are for the bake-off world on the
author's machine; re-measure from the journal (`begin` to `finish` `ts`) rather than
guessing if a pass matters.


## Decor stage measurements (#229, 2026-11-11)

Two locked `redo_layer.py --world southampton-build --cell 53,-43 --layers decor --apply`
runs on the already-built cell in `SouthamptonAI`, using the existing backup, undo,
readiness and readback gates. Both planned 2,583 positions in 630 boxes. No optimisation
was applied. These numbers describe decor in this cell, not trees or other worlds.
Timers use `perf_counter` wall seconds and print a flushed start line before each stage,
including failures. `*-result.json` retains apply timings; redo also reports orchestration
stages. Nested stages are inclusive: do not add surface probing to planning, or player
movement/fills to writer total.

| Stage (seconds) | Run 1 | Run 2 |
| --- | ---: | ---: |
| layer planning (includes surface warm-up) | 184.027 | 180.585 |
| decor surface warm-up | not separately timed | 140.235 |
| identity and readiness | 5.769 | 5.246 |
| surface warm-up | 3.356 | 3.174 |
| site_lock.check | 0.001 | 0.001 |
| open_registry | 0.000 | 0.000 |
| structure backup | 0.323 | 0.350 |
| registry.begin guard reads and journal write | 8.650 | 8.136 |
| player hop | 0.014 | 0.004 |
| fills, slabs, signs and entities | 1.142 | 1.143 |
| writer total | 2.274 | 2.255 |
| flush | 3.115 | 3.148 |
| readback | 2.842 | 2.623 |
| journal finish | 0.012 | 0.014 |
| journal follow-ups | 0.001 | 0.003 |

Run 2's complete redo took 305.382 seconds in its measured stages: preflight 5.749,
hover before 4.506, undo 20.776, preview 50.471, apply 219.417, hover after 4.463.
Both applies read back with **zero mismatches**, landed, restored the decor mask and
released the lock. Apply journal entries: 865 and 869. Logs and result copies live in the
world's `verification/timing-task-229-2026-11-11/` (world-owned, separate from this repo).
The separate decor-snapshot timer was added for run 2, so its 140.235-second measurement
has n=1; total planning has n=2 (184.027 and 180.585 seconds, a 3.442-second range).

I-044/S-076: the suspected 20-minute pre-write stall was **not reproduced**. The slowest
measured stage was planning, specifically live surface probing in run 2. Journal-begin
guard reads plus its fsynced write took 8.650/8.136 seconds; structure backup took
0.323/0.350 seconds, and lock/open-registry checks each took about a millisecond or less.
These data do not prove which step caused the earlier incident. The new start/elapsed
lines identify it if it recurs. No CPU profile or per-request trace was collected.

Ranked follow-ups (estimates are untested scenarios, not measured savings):

1. **Reduce decor surface-probe cost** (#263). It consumed 140.235/180.585 seconds
   of planning in run 2. Test grouping nearby column scans under the existing volume
   and memory bounds, and measuring scan counts/parse time. A 25–50% reduction in this
   stage would save **35–70 seconds per apply**; the absolute ceiling is 140 seconds.
   Keep snapshots fresh, preserve unknown/unloaded handling and compare identical
   placements. The measurements identify the stage; the best grouping is unverified.
2. **Avoid repeating source-only preparation in redo preview and apply** (#264).
   Preview cost 50.471 seconds, including only 0.974 seconds of disk surface probing;
   live planning outside surface probing cost 40.350 seconds. Reusing validated,
   immutable source/plot preparation could save **20–40 seconds per redo** if half to
   most of the overlapping work is reusable. Exact overlap is unmeasured. Re-check
   source/journal identities and take fresh live block reads; never reuse stale live
   state or bypass readiness. Investigate this narrower seam before removing preview.
3. **Avoid repeated guard probing only if subsequent measurements justify it.** The
   apply's extra warm-up is 3.174–3.356 seconds; begin is 8.136–8.650 seconds, including
   readiness and journalling. Their combined absolute ceiling is about 12 seconds;
   safe attainable savings are unknown. No fix task filed from these data.
4. **Fewer readback scans / larger fill boxes are low priority here.** Readback is
   about three seconds and the fills stage about one second. The 630 boxes already
   travel in a batch, so they are not 630 paced round trips. Even eliminating those
   stages entirely saves only about four seconds; no task filed for them.

The timing task changes diagnostics only. Optimisations and any frontage/headroom
world review are separate work.
