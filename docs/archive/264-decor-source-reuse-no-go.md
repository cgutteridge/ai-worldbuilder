# #264: reuse source preparation between decor redo preview and apply: no-go (2026-11-14)

Conclusion: **do not implement.** No safe, decor-only saving of at least 15 s per redo exists.
The only reusable part is immutable raster decoding, which lives in the lidar/aerial
providers (outside decor, and shared with other layers); the largest single block is
journal-derived live state that must be recomputed.

## Measurement

`decor_layer.py --world southampton-build --cell 53,-43` (read-only preview; no apply, no block
writes), one run, new `timed_stage('decor source preparation')` around the vector-map index, vector
ground, lidar heightmap and `build_decor_plan` block in `plan_cell`:

| Stage | Seconds |
| --- | --- |
| decor source preparation | 48.993 |
| decor surface warm-up (disk probing) | 1.177 |
| whole command | about 60 |

cProfile of a second run (64 s under the profiler; figures are cumulative and overlap):

| Inside `plan_cell` | Seconds | Nature |
| --- | --- | --- |
| `neighbour_owned` -> `cell_journal.owners` (`_cell_owners` x10) | 20.8 | journal-derived live state |
| `build_decor_plan` | 16.6 | includes lidar and aerial decode below |
| lidar TIFF decode (12 `_load_libtiff`, PIL ImagingDecoder 13.6 s) | about 12 | immutable source files, read in the providers |
| aerial `sample_rgb` | 5.8 | immutable source files |
| `garden_barriers` | 9.2 | source-derived, overlaps the above |

Apply planning runs the same `plan_cell`, so source preparation is repeated in a separate
process on apply. Apply was not run (no live world access in this ticket); its extra cost over
preview is the live snapshot warm-up, already reduced by #263.

## Why no seam

1. About 21 of the 49 s is the journal ownership scan. It reflects journal and mask state at
   plan time. The ticket forbids reusing stale live state, so it cannot be shared.
2. The immutable part (lidar plus aerial decode, about 18 s at best, overlapping) is raster
   decoding inside the provider modules. Preview and apply are separate processes, so reuse needs
   an on-disk derived-data cache keyed by tile file identity. That is a new cache engine in
   shared provider code (vendor-specific provider code, which other installs replace),
   not a narrow decor seam, and would also change ground and trees behaviour.
3. The best case (~18 s) is near the 15 s threshold and unproven: the preview still pays it
   first, and redo preview-then-apply is not guaranteed in one session.

## Possible follow-ups (not filed)

- In-process: the lidar provider's tile `data` call ran 48 times for 12 TIFF loads; check whether repeat
  decodes within one run can share the decoded array (provider-level, helps every layer).
- `cell_journal._cell_owners` takes 19 s over 10 calls (7.2 s own time); an index there would
  speed `neighbour_owned` for every layer without any reuse risk.
