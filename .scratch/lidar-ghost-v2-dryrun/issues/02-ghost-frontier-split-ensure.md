# 02: Ghost frontier and split-layer ensure

**What to build:** Lidar ghosts follow a **frontier**: every cell with real refine started seeds a configurable Chebyshev ring (default **2**) of target cells. For each target, **ghost ground** is ensured only until a real ground layer stage exists; **ghost features** are ensured separately per spec (not tied to whole-cell real-work skip alone). Status output lists frontier membership and per-layer ghost state. Cell focus / raise uses frontier ensure instead of focus-only ring-1 neighbours. Behaviour is verifiable with injected heights and temporary STAGES directories without Minecraft.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] Seed set derived from all cells whose STAGES show real work (ground, trees, decor, feature — not ghost stages)
- [x] Target set is union of ring-N neighbours around each seed; default N=2 via existing env ring knob
- [x] Ensure can apply or plan ghost ground and ghost features independently per cell rules in spec
- [x] `status` (or equivalent CLI) reports frontier cells and per-layer ghost stage state
- [x] Focus / raise path invokes frontier ensure (compatible with `--no-ghosts`)
- [x] Unit tests cover multi-seed frontier union, split layers, and ring depth without live DEFRA or server

**Spec:** [docs/specs/lidar-ghost-v2-and-dryrun-reset.md](../../../docs/specs/lidar-ghost-v2-and-dryrun-reset.md) — Lidar ghost v2 frontier, split ground vs feature lifecycle, configuration

## Answer

Shipped on `main` (`ddf4494`, `3193ba6`, `06b629f`): see `scripts/dryrun_reset.py`, `providers/ukdefra-lidar/manage/lidar_ghost.py`, `scripts/site_kit.py` reconcile hook, handoff/marker copy. Dry run 001 Phases 0–2 exercised in world; Phases 3–5 remain queue work.
