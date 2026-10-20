# 01: Active-world dry-run reset

**What to build:** A maintainer can reset the **active world** for another empty-world rehearsal: see exactly what will be deleted or kept (`--dry-run`), run a guarded reset that refuses when the site is locked or the game world is up (unless `--force`), optionally archive the current place memory tree, re-scaffold place memory from the new-world site template while **keeping** current-cell focus and **not** touching world frame, providers, reference, or downloaded caches, clear builder lock and holds, and delete overworld region chunk files so the save can regenerate empty after the player reopens the world.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] `--dry-run` prints preserved paths (config, reference, caches, current-cell focus) and wiped paths (queue, builds, STAGES, regions, lock, holds) without writing
- [x] Default run refuses when site lock is held; refuses when MCP reports world up unless `--force` with explicit warning
- [x] Optional archive moves prior site tree under site archive with timestamp before scaffold
- [x] Live run scaffolds from new-world site template; Geocraft legacy site notes are not retained
- [x] Overworld region files removed using save path from active world config
- [x] Unit tests cover dry-run output and lock refusal (MCP check mocked or stubbed)

**Spec:** [docs/specs/lidar-ghost-v2-and-dryrun-reset.md](../../../docs/specs/lidar-ghost-v2-and-dryrun-reset.md) — Dry-run reset CLI

## Answer

Shipped on `main` (`ddf4494`, `3193ba6`, `06b629f`): see `scripts/dryrun_reset.py`, `providers/ukdefra-lidar/manage/lidar_ghost.py`, `scripts/site_kit.py` reconcile hook, handoff/marker copy. Dry run 001 Phases 0–2 exercised in world; Phases 3–5 remain queue work.
