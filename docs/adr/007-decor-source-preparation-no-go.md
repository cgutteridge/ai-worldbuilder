# ADR 007: Do not cache decor source preparation between preview and apply

**Status:** Accepted (2026-11-14, task #264). Measurement write-up: `docs/archive/264-decor-source-reuse-no-go.md`.

## Context

A decor redo preview and the later apply each run `plan_cell`, so source preparation is paid twice. Task #264 asked whether at least 15 seconds of that work could be saved for decor only.

## Decision

Do not implement a decor-only cache of source preparation. No safe saving of at least 15 seconds exists.

On the measured cell, about 21 of the 49 seconds is the journal ownership scan. That reflects journal and mask state at plan time, and the ticket forbids reusing stale live state, so it cannot be shared between preview and apply. The immutable part (lidar and aerial decode, about 18 seconds, overlapping) lives in the lidar and aerial providers, which ground and trees share as well. Caching it would be a new on-disk cache in shared provider code, not a narrow decor seam. That best case sits near the 15 second bar and was not proven: the preview still pays the decode first, and a redo is not guaranteed to preview and apply in one session.

## Consequences

- Preview and apply stay separate processes and recompute journal-derived state.
- A later speed-up belongs in the providers (repeat decodes within one run) or in the journal ownership index. Those are separate tasks, not a decor cache.
