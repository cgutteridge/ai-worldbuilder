# Southampton empty-world dry run 001

**Goal:** Prove **reset → ghost v2 → full feature fan-out for one cell → ground** on the active Southampton world. Composer quality is secondary; **steps and ghost behaviour** are the pass criteria.

**Spec:** [Lidar ghost v2 and dry-run reset](../specs/lidar-ghost-v2-and-dryrun-reset.md)

**Cell:** 48,-44 (focus retained across reset by default)

**Out of scope this run:** trees and decor layered passes (dry run 002).

---

## Preconditions

- Active world: `southampton-build` (reuse checkout; do not create a new world repo).
- Ghost v2 and reset CLI merged and unit tests green.
- You know the save name from world config (launcher loads that world).

---

## Phase 0 — Human: leave the world

1. Stop MCP / map tools so nothing holds the world open.
2. **You quit to the launcher** (not sitting in-world). Reset must not run while you are loaded in the save.

---

## Phase 1 — Reset place memory and chunks

1. Run reset CLI **`--dry-run`**; confirm preserved vs wiped lists match spec (config, reference, caches, current cell; no builds/queue; regions listed).
2. Run reset for real (optional **`--archive-site`** first time).
3. Confirm: `registry/` absent (the first journal write creates it; the report shows `registry.journal_entries: 0`), `python3 scripts/cell_registry.py verify` reports no issues, no `site/QUEUE.txt` / `CURRENT-CELL.json`, no `site/builds/*.json`, no legacy import notes file, lock cleared.
4. Delete overworld regions (if not already done inside reset CLI — one tool should own this).
5. **You reopen the world** and load chunks near 48,-44 so the save is empty flat/generated bedrock shell, not stale geometry.

---

## Phase 2 — Queue the cell

1. From tool repo with active world set: advance queue for 48,-44 (`site_handoff` advance / plan path — same as production).
2. Confirm open items: **all FEATURE gaps** for 48,-44 (University Road, Jubilee, Retail, w171869344, etc.), then **one layered-passes item** for ground/trees/decor — **execute ground only**; leave trees/decor unchecked for this dry run.
3. Raise cell marker if required: `cell_marker.py --raise 48,-44` (ghost v2 refresh should also run from real applies later).

---

## Phase 3 — Feature items (all)

For **each** open FEATURE item in order (queue top to bottom):

1. Session brief / handoff `next` for that Q-id.
2. Evidence read (reference photos — not old build JSON; there should be none).
3. Plan → dry-run guards → apply feature build. Neighbour cells keep **ghost ground** with DSM−DTM top colours for footprint checks; there are no volumetric feature ghosts to strip (task #68).
4. Evidence contact sheet if required by handoff rules.
5. Mark queue item done.
6. **Ghost check:** run lidar ghost **status** for focus 48,-44 with ring 2; note frontier cells, ghost ground vs features, strip/superseded on **every cell the footprint touched**, not only the queue home cell.

Repeat until no FEATURE items remain for this cell.

---

## Phase 4 — Ground pass only

1. Run the layered-passes queue item **ground layer only** (do not apply trees or decor).
2. **Ghost check again:** focus cell should lose ghost ground where real ground applied; neighbours in ring 2 should still show appropriate placeholders until their own real passes; no grey feature ghosts under new feature volumes.

---

## Phase 5 — Pass / fail record

| Check | Pass criterion |
|--------|----------------|
| Reset | No prior build specs or journal entries; world chunks empty at start; reference intact |
| Frontier | After applies, status shows ghosts in ~ring-2 belt around **all** seeded worked cells, not only focus-time neighbours |
| Split layers | Cells with real ground but no feature overlap may still show feature ghosts until feature strip; cells under real features have no ghost feature blocks in footprint |
| Journal | No ghost build left landed in the cell journal where blocks were stripped or region was wiped |
| Queue | All FEATURE items ticked; ground stage recorded; trees/decor explicitly deferred |

Log outcome in world `site/RUN-LOG.md` (one short paragraph + date). If fail, note phase and status JSON snapshot path — do not patch forward without spec change.

---

## Phase 6 — Optional dry run 002 (later)

- Same reset procedure.
- Full layered passes including trees and decor.
- Or multi-cell queue advance — not part of 001.
