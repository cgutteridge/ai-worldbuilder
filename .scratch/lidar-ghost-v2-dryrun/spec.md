# Lidar ghost v2 and empty-world dry-run reset

**Status:** resolved

Canonical published spec: [docs/specs/lidar-ghost-v2-and-dryrun-reset.md](../../docs/specs/lidar-ghost-v2-and-dryrun-reset.md)

Dry run checklist: [docs/plans/southampton-dryrun-001.md](../../docs/plans/southampton-dryrun-001.md)

(Same content as the docs spec — edit the docs copy; keep this file’s Status line in sync for the issue tracker.)

## Answer

Shipped on `main` (`ddf4494`, `3193ba6`, `06b629f`): see `scripts/dryrun_reset.py`, `providers/ukdefra-lidar/manage/lidar_ghost.py`, `scripts/site_kit.py` reconcile hook, handoff/marker copy. Dry run 001 Phases 0–2 exercised in world; Phases 3–5 remain queue work.
