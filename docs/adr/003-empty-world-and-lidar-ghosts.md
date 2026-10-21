# ADR 003: Empty-world refine and lidar ghosts

**Status:** Accepted (2026-10-17). Ghost **behaviour** in §3 is partially superseded by [spec: Lidar ghost v2 and dry-run reset](../specs/lidar-ghost-v2-and-dryrun-reset.md) (frontier ring, split layers, strip). Empty-world DoD and evidence heights in §1–2 remain authoritative here.

## Context

The tool formerly treated refine as clearing unrefined vendor-import lumps and often used a pristine heightmap as an evidence path (removed in task #38: no pristine baseline reads remain). New worlds start from an **empty** save. Neighbours then have no skyline until their own queue turn, which slows judgement of massing and edges.

## Decision

1. **Empty-world DoD:** A cell is done when evidence-backed features and layered passes (ground, trees, decor) plus masks meet the craft rules — not when “no unrefined vendor import” remains. The lump-scan tool is deleted (task #38).
2. **Evidence heights:** National **composite** DTM/DSM (lidar provider package) are the normal lidar path. A pristine import save is not a catalogue provider and is never read as evidence: tools needing ground or surface heights use the lidar provider only.
3. **Lidar ghosts:** When a cell is focused, unstarted neighbours within a configurable Chebyshev ring (default 1) get idempotent placeholder applies: composite DTM → ghost ground; composite DSM − DTM → grey ghost features. No trees/decor ghosts in v1. Real `ground_layer` / builders / trees / decor overwrite or refine ghosts later. No per-cell generated Python.

## Consequences

- System tasks 23–24 landed (empty-world DoD + composite lidar evidence). Task 25 swept stale
  import-default / pristine-as-lidar wording in rules/. Tasks 26–27 are the ghost path.
- World QUEUE items that still speak of vendor-import lumps as DoD remain world work.
- Ghost owners must be distinguishable in STAGES so redo and real applies can replace them safely.
