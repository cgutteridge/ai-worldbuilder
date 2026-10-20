# 03: Post-apply ghost reconcile

**What to build:** After every successful **real** apply (ground, trees, decor, or feature build — not lidar ghost applies), the tool recomputes the ghost frontier and reconciles placeholders for affected cells. Real **ground** apply supersedes ghost-ground STAGES to match overwritten blocks. Real **feature** apply strips ghost-feature blocks inside the feature’s placed volume (including volumes in neighbour cells) and updates ghost-features STAGES so “applied” idempotency does not lie about missing grey mass. Agents no longer need a manual ghost CLI after each apply.

**Blocked by:** 02 (Ghost frontier and split-layer ensure)

**Status:** resolved

- [x] Shared apply path (or equivalent central hook) invokes reconcile after successful non-ghost applies only
- [x] Ground layer completion marks ghost-ground stage terminal/superseded per spec
- [x] Feature apply triggers strip of ghost-feature-owned blocks in footprint intersection and matching stage update
- [x] Reconcile triggers frontier refresh (incremental or full seed scan acceptable for v1)
- [x] Trees and decor applies count as seeds for frontier expansion when reconcile runs
- [x] Tests use mocked apply or fixtures; assert stage transitions and strip plans without requiring Minecraft

**Spec:** [docs/specs/lidar-ghost-v2-and-dryrun-reset.md](../../../docs/specs/lidar-ghost-v2-and-dryrun-reset.md) — Strip and STAGES integrity, refresh triggers, integration seams

## Answer

Shipped on `main` (`ddf4494`, `3193ba6`, `06b629f`): see `scripts/dryrun_reset.py`, `providers/ukdefra-lidar/manage/lidar_ghost.py`, `scripts/site_kit.py` reconcile hook, handoff/marker copy. Dry run 001 Phases 0–2 exercised in world; Phases 3–5 remain queue work.
