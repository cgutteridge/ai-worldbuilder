# #283: one pass over the journal for the neighbour cells: no-go (2026-11-14)

Conclusion: **do not implement.** A shared pass saves about 0.3-0.5 s, far under the 5 s bar.

## Measurement (read-only, `neighbour_owned((53,-43))`, southampton-build, throwaway world copy)

Unprofiled: `neighbour_owned` 12.5 s in total; `_cell_owners` 9.5 s of that over 9 cells (not 10);
the other ~3 s is journal sync and building the 4.34M-position result set.

Split of `_cell_owners`: entry iteration negligible (24 distinct entries); blob decode about 1 s
(2 real decodes, 34 of 36 `_blob` calls were cache hits); the per-position loop (`block_id` cancel
check, `_cell_of` filter, `_Owner` allocation, dict store) is the rest, roughly 8 s.

A shared pass would visit 4,528,845 distinct (entry, position) pairs instead of 4,696,837 visits:
3.6% redundant. Under cProfile `_cell_owners` took 20.5 s (about 2x inflation), with 4.5M `block_id`
calls (4.1 s) and 4.7M `_cell_of` calls (3.6 s).

## What the cost really is

The 4.5M-position Python loop. Options, each a separate task needing the same per-cell equivalence
care: handle the compressed runs per run instead of per position; or store each entry's in-cell split
when the blob is saved. Not filed. (#282's cache and this task's shared pass were both wrong guesses
because the profile was read as repeated work; it is per-position work.)
