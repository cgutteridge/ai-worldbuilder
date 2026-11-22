# ADR 008: Do not scan neighbour cells in one journal pass

**Status:** Accepted (2026-11-14, task #283). Measurement write-up: `docs/archive/283-ownership-one-pass-no-go.md`.

## Context

`neighbour_owned` reads ownership for a cell and its neighbours. Task #283 asked whether one shared pass over the journal, instead of one pass per neighbour, would save at least 5 seconds.

## Decision

Do not implement a shared pass. On the measured cell it saves about 0.3–0.5 seconds, far under the 5 second bar. The visits are only about 3.6% redundant. The time is the per-position loop over millions of positions, not repeated work a shared pass can skip.

## Consequences

- Neighbour ownership stays one cell scan at a time.
- A later speed-up has to change that per-position loop (handle compressed runs as runs, or store each entry's in-cell split when the blob is saved). That is a separate task and needs the same per-cell equivalence care. It is not this shared pass.
