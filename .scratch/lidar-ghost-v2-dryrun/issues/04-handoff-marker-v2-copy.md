# 04: Handoff and marker copy for v2 ghosts

**What to build:** Session handoff and cell-marker guidance describe **frontier** lidar ghosts (ring around all worked cells, default two hops, split ground vs features, refresh after real applies) instead of v1 “neighbour ghosts on raise only”. Help text and plan snippets stay aligned with the spec so agents running `site_handoff next` or `cell_marker --raise` get correct expectations.

**Blocked by:** 03 (Post-apply ghost reconcile)

**Status:** resolved

- [x] Handoff / plan text updated for frontier ghost behaviour and post-apply refresh
- [x] Cell marker raise/adopt messaging consistent with frontier ensure (still documents `--no-ghosts` / ring env)
- [x] Tests or snapshot checks on handoff strings where the repo already covers handoff / marker copy

**Spec:** [docs/specs/lidar-ghost-v2-and-dryrun-reset.md](../../../docs/specs/lidar-ghost-v2-and-dryrun-reset.md) — Integration seams (cell marker / handoff)

## Answer

Shipped on `main` (`ddf4494`, `3193ba6`, `06b629f`): see `scripts/dryrun_reset.py`, `providers/ukdefra-lidar/manage/lidar_ghost.py`, `scripts/site_kit.py` reconcile hook, handoff/marker copy. Dry run 001 Phases 0–2 exercised in world; Phases 3–5 remain queue work.
