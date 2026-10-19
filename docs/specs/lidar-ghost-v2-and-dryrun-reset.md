# Lidar ghost v2 and empty-world dry-run reset

**Status:** ready-for-agent  
**Supersedes in part:** [ADR 003](../adr/003-empty-world-and-lidar-ghosts.md) §3 (focus-only, ring 1, combined ground+features lifecycle).

Canonical copy for the issue tracker: [.scratch/lidar-ghost-v2-dryrun/spec.md](../../.scratch/lidar-ghost-v2-dryrun/spec.md).

## Problem Statement

Empty-world refine needs a **repeatable dry-run loop**: wipe the Minecraft build and all **agent place memory** (feature specs, stages, queue) while **keeping evidence and world frame**, then run one cell end-to-end. Placeholders must give a believable skyline **near worked cells** without grey lidar mass **fighting real feature geometry** (including features whose home cell is elsewhere). Today’s lidar ghosts only run on **cell focus**, use **ring 1**, apply **ground and features together**, and do not **strip feature ghosts** when a real feature footprint overlaps a neighbour cell. There is no **standard reset** procedure or tool, so each dry run risks stale STAGES, stale build JSON, or chunks left loaded while regions are deleted.

## Solution

1. **Dry-run reset** — A guarded CLI resets the active world for another rehearsal: delete overworld region chunks (after the player has left the world), re-scaffold place memory from the new-world site template while **preserving** world frame, providers, reference catalogue, downloaded caches, and default **current cell focus**; **clear** builder lock and holds; **drop** legacy Geocraft notes and all feature/build decisions.
2. **Lidar ghost v2** — Maintain a **frontier** of cells that should show placeholders: any cell with **real refine started** (ground, trees, decor, or feature apply) seeds a **Chebyshev ring** (default **2**) of neighbours. Each target cell gets **ghost ground** until **real ground** exists there, and **ghost features** until **real feature volume** overlaps that cell. **Strip** ghost-feature blocks in overlap volumes and **update STAGES** so bookkeeping matches blocks (no “visual off, stages still applied”). After **every successful apply** that counts as real work, **refresh** the frontier and reconcile ghosts.
3. **Acceptance** — [Southampton dry run 001](../plans/southampton-dryrun-001.md) is the behavioural proof after unit tests guard core rules.

## User Stories

1. As a worldbuilder maintainer, I want a single reset command with a dry-run mode, so that agents and I do not drift on what gets deleted each rehearsal.
2. As a worldbuilder maintainer, I want reset to refuse while the world is live or site-locked, so that we do not corrupt chunks or concurrent applies.
3. As a worldbuilder maintainer, I want reset to preserve DEFRA and reference downloads, so that dry runs stay fast and reproducible.
4. As a worldbuilder maintainer, I want reset to preserve Y offset and projection, so that I never re-run the human offset checkpoint for the same world.
5. As a worldbuilder maintainer, I want reset to clear feature specs and STAGES, so that wrong facade or massing decisions do not carry into the next run.
6. As a worldbuilder maintainer, I want reset to empty the world queue, so that the next session re-queues from evidence rather than stale queue prose.
7. As a worldbuilder maintainer, I want an optional archive of the previous site tree, so that I can diff what the last agent decided without keeping it live.
8. As a refine agent, I want neighbour cells within two hops of any worked cell to show DTM ground ghosts, so that I can judge massing and edges before those cells’ queue turn.
9. As a refine agent, I want ghost ground to disappear when real ground is applied in that cell, so that terrain authority stays with the ground layer pass.
10. As a refine agent, I want grey DSM−DTM ghosts only where no real feature has claimed that volume, so that I am not misled by lidar lumps over a half-built building.
11. As a refine agent, I want a feature built in cell A to strip ghost features in cell B when the footprint crosses the border, so that multi-cell features behave correctly.
12. As a refine agent, I want trees or decor applies in a cell to expand the ghost ring around that cell, so that the skyline buffer follows actual refine progress, not only cell marker focus.
13. As a refine agent, I want ghost stages to reflect whether blocks are still present, so that idempotent ensure does not skip re-apply after a region wipe or partial strip.
14. As a refine agent, I want ghost refresh after each real apply, so that the frontier updates without manually re-running a ghost CLI.
15. As a refine agent, I want ring depth configurable via environment default, so that pilots can tune 1 vs 2 without code changes.
16. As a test author, I want unit tests for frontier union, split lifecycles, and strip bookkeeping, so that regressions are caught without booting Minecraft.
17. As the Southampton pilot user, I want dry run 001 to run all auto-queued features for cell 48,-44 then ground only, so that the path exercises real applies and ghost interaction without trees/decor duration.
18. As the Southampton pilot user, I want a written checklist that includes quit world → reset → rejoin, so that chunk regeneration is reliable.
19. As a worldbuilder maintainer, I want Geocraft legacy site notes removed on reset, so that agents are not steered toward lump-refine semantics.
20. As a refine agent, I want current cell focus retained across reset by default, so that dry runs do not re-negotiate 48,-44 every time.
21. As a refine agent, I want builder lock cleared on reset, so that the next session can claim site ownership cleanly.
22. As a worldbuilder maintainer, I want ADR 003 to point at this spec for ghost behaviour, so that rules and code stay aligned without rewriting the whole ADR immediately.

## Implementation Decisions

### Dry-run reset CLI

- New worldbuilder script (thin CLI): reads **active world** save path from world config; deletes overworld **region** chunk files only (not a full template recopy unless documented escape hatch for corruption).
- ** Preconditions:** refuse by default if site lock is held or MCP reports the game world up; allow `--force` with explicit warning text.
- **`--dry-run`:** print wipe list (regions, site paths to replace, paths preserved) without writes.
- **Site merge policy:** copy new-world site template over place memory; **preserve** current-cell focus file content; **clear** lock file and cell-holds journal; **do not** touch world config, local providers, or reference trees.
- **Optional:** `--archive-site` moves the existing `site/` tree to `backups/<timestamp>-dryrun-site` before scaffold (not under `site/`, so the move is valid).
- Document human steps: player must **leave the world** before region delete and **reopen** after so chunks regenerate empty.

### Lidar ghost v2 — frontier

- **Seed set:** all cells where **real work started** is true — any non-ghost stage in applying/applied/verified/applied-with-mismatches state, including ground layer, trees layer, decor layer, and feature builds (same notion as today’s real-work predicate, extended if new stage keys appear).
- **Target set:** union over seeds of cells within Chebyshev distance **N** of each seed (default **N=2**, env-configurable, same variable name as today for ring depth).
- **Exclude from ghost apply:** cells where real work policy says placeholders are inappropriate (whole-cell skip when real work started remains for **ensure** short-circuit; per-layer rules below refine that).

### Split ground vs feature lifecycle

- **Ghost ground:** ensure/plan/apply while the cell lacks an applied **real ground layer** stage (ghost-ground alone does not count). Real ground apply overwrites ghost ground blocks via existing ignored-build owners.
- **Ghost features:** ensure/plan/apply while the cell has ghost ground (or real ground) and no **real feature** overlap has been recorded for that cell. Overlap is defined by feature build footprint / registry coverage in world block space, including blocks applied from a feature whose STAGES home cell is different.
- **Do not** use the stricter rule “any real pass clears feature ghosts” for v1 of this spec (ground-only may leave feature ghosts until feature overlap strip runs).

### Strip and STAGES integrity

- When a real feature apply succeeds, run a **strip pass** for ghost-feature-owned blocks inside the feature’s placed volume (intersection with ghost feature owner), then update ghost-features stage to a terminal state that **does not** satisfy ghosts-complete idempotency (e.g. superseded / stripped / partial) so ensure can reason about missing mass.
- When real ground completes, mark ghost-ground stage superseded or equivalent; blocks already overwritten by ground apply.
- **Never** leave “no blocks but stages say applied” as the steady state after strip or reset; region wipe + reset clears site STAGES; strip must update stages when blocks are removed in-world.

### Refresh triggers

- After each successful **real** apply (feature build, ground layer, trees layer, decor layer — not ghost applies), invoke frontier recompute and incremental ensure for affected cells (at minimum: seed cell + ring around it; implementation may recompute full seed set from all STAGES for correctness first, optimize later).
- Cell marker **raise/adopt** may still call ensure, but must not be the **only** trigger.

### Integration seams

- **Lidar ghost module** (provider manage package behind shim): add frontier helpers, per-layer ensure, strip helper, stage transitions; extend CLI status to report per-layer ghost state and frontier membership.
- **Cell marker / handoff:** raise remains compatible; handoff copy should describe v2 frontier behaviour, not only focus neighbours.
- **Apply path:** central apply wrapper or shared post-apply hook in site kit layer invokes ghost refresh + feature strip (feature builds must call strip; ground layer invokes ground stage transition).
- **Ground layer / feature build / cell features:** continue treating ghost owners as overwritable; feature registry used for overlap detection.

### Configuration

- Default ring **2** for new behaviour (document breaking change from ADR 003 default 1); env override keeps backward tuning.

## Testing Decisions

- **Principle:** test **observable behaviour** — which cells get plans, skip reasons, stage transitions after strip, frontier membership — using injected height fixtures and temporary STAGES directories (existing lidar ghost test style). Do not assert internal helper names.
- **Modules under test:** lidar ghost library, reset CLI (dry-run output, refuse when lock file present), post-apply hook behaviour via small integration tests with mocked apply.
- **Prior art:** existing lidar ghost unit tests (neighbour ring, real-work skip, ensure idempotency, ghost owners in ground layer ignored builds).
- **Primary checkpoint:** [Southampton dry run 001](../plans/southampton-dryrun-001.md) — human quit/reopen, reset, all 48,-44 features, ground pass; verify with ghost status JSON and views that ring-2 belt behaves and overlap strips grey mass. Unit tests gate merges; dry run proves end-to-end.

## Out of Scope

- Trees/decor **ghost** placeholders (still no lidar trees/decor ghosts).
- Trees/decor **passes** in dry run 001 (deferred to dry run 002).
- New world repository per dry run (reuse active world checkout; optional dated archives only).
- Full rewrite of ADR 003 (pointer amendment only).
- Automating “player leave world / rejoin” in Minecraft.
- Bake-off 3×3 region snap as the default reset baseline (orthogonal to lidar ghost tool).
- Stricter “any real pass clears all feature ghosts in cell” unless strip proves insufficient in practice.

## Further Notes

- **Order of work:** implement ghost v2 + reset script **before** executing dry run 001; the dry run exists to validate ghosts, not v1 focus-only behaviour.
- **System tracker:** optional follow-up ticket in tasks index after spec lands; `.scratch` holds AFK-ready spec.
- **Glossary:** use **lidar ghost**, **empty world**, **world tracker**, **world frame**, **active world** per tool glossary.
