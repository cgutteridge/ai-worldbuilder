# ADR 010: Layered-pass ground, masks and neighbour re-runs

**Status:** Accepted (2026-10-14, user). Moved out of `rules/handoff-layered-passes.md` §§10–12 by task #411. Pass order stays in that rule. Bridges and tunnels are major features; their volume is [ADR 009](009-structure-features.md).

## Context

Trees and decor place only into air and record a mask. Ground replaces the surface and clears the column above it. A later ground redo can hit those masks, and a major feature can overlap a neighbour's trees or decor. The pass rule stated the order; these are the decisions for the conflicts.

## Decision

### Ground step

- Replace the top non-air block in each column with the surface block.
- Treat flowers and grass stems (short grass, tall grass, fern) as air for this step. Stock stems are not ground surface. A grass block is not air.
- Then clear the vertical column above the surface of all non-air blocks not owned by a neighbour, except any block that belongs to a neighbouring cell's tree or decor mask. Those positions stay protected even when they overhang this cell's columns. Confirmed reading: remove all non-air blocks not owned by a neighbour above the new surface in this cell's columns, skipping neighbour mask positions. Leave pure air as it is.
- Ground does not touch any column owned by a major feature.
- If this cell's trees and decor already exist, re-run them after the ground step. Ground may therefore replace this cell's trees and decor, and never a neighbour's.
- Do the work with scripts and checks, not by hand.

### Major features and trees

A major feature wins. Trees and decor are never placed inside a major feature's volume. If any non-air block not owned by a neighbour, or a tree, already sits in a major feature's footprint, the major feature step clears it as part of its own footprint. A tree whose crown only overhangs a major feature is removed by that step, except a bridge or tunnel, which keeps the crown outside its own volume (ADR 009).

### Cross-cell tree and decor after a feature

When a major feature runs or re-runs, any tree or decor of another cell that overlaps it must be re-run too, without anyone remembering to do it.

Progress and ownership live in the cell journal (`registry/journal.jsonl` via `cell_registry.py`). Neighbour work shows up through `cell_registry.py next` and issues after the feature lands.

Feature step, in order:

1. Clear the vertical path above its footprint. Non-air blocks not owned by a neighbour are removed. Any block owned by a neighbour's tree or decor mask is left in place and counted.
2. Note or issue the neighbour cells whose masks overlap, so trees and decor re-run.
3. A dependency pass first undoes its own mask, then re-plans with the new feature volume excluded. Its trees and decor never enter a feature volume.

Ground follows the same rule: it skips feature-owned columns, and records a dependency pass for any neighbour-owned block it cannot clear.

A feature never removes a neighbour's block. The neighbour's tree or decor pass re-runs and drops the overlapping blocks itself.

### Ground redo, holds and masks

- Hold the cell in the registry until ground redo, trees and decor are verified. `cell_registry.py hold <cell> --reason …` is the hold. `coverage.py` reports a held cell as held, never done, until the hold clears.
- Ground must not touch a masked tree or decor position. If a ground plan does, that is a fault: undo that one mask first, then re-run that pass after the ground. Undo is only for the case where ground hits a mask. `lib/layers/pass_mask.py` `masked_overlaps()` finds these positions before apply.
- Keep the current trees mask and the current decor mask per cell (one file per pass per cell, overwritten on re-run). Do not keep a history of older masks.

## Consequences

- `rules/handoff-layered-passes.md` keeps pass order, the mask format and the air-only undo. It points here for the decisions above.
- Ground, trees and decor code comments that used to say "§10" mean this ADR.
