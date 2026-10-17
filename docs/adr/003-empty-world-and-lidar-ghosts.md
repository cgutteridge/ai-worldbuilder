# ADR 003: Empty-world refine and lidar ghosts

**Status:** Accepted (2026-10-17)

## Context

The tool formerly treated refine as clearing unrefined Geocraft (or similar) lumps and often used a pristine heightmap as an evidence path. New worlds start from an **empty** save. Neighbours then have no skyline until their own queue turn, which slows judgement of massing and edges.

## Decision

1. **Empty-world DoD:** A cell is done when evidence-backed features and layered passes (ground, trees, decor) plus masks meet the craft rules — not when “no unrefined Geocraft” remains. Lump-scan must not drive the default queue.
2. **Evidence heights:** DEFRA **composite** DTM/DSM (via `defra_lidar`) are the normal lidar path. Pristine Geocraft is not a catalogue provider and not on the default evidence reading list.
3. **Lidar ghosts:** When a cell is focused, unstarted neighbours within a configurable Chebyshev ring (default 1) get idempotent placeholder applies: composite DTM → ghost ground; composite DSM − DTM → grey ghost features. No trees/decor ghosts in v1. Real `ground_layer` / builders / trees / decor overwrite or refine ghosts later. No per-cell generated Python.

## Consequences

- System tasks 23–27 implement the strip and ghost path; task 25 sweeps stale Geocraft wording after OS docs move (task 18).
- World QUEUE items that still speak of Geocraft lumps need updating when 23–24 land.
- Ghost owners must be distinguishable in STAGES so redo and real applies can replace them safely.
