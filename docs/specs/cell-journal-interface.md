# Cell journal interface (task 41; ADR 004)

Code: `lib/journal/cell_journal.py`, `lib/blocks/block_store.py`; the active world's registry is
`cell_registry.open_registry()` / `view_registry()`. Tests: `lib/journal/test_cell_journal.py`,
`lib/blocks/test_block_store.py`. `site_kit.apply` / `site_kit.undo` record through it (task 42).

## Types

```python
reg = CellRegistry(world_root, blocks, ghosts=None, *, cell_size=None, ring=2)

reg.change(intent, apply=None, *, cascade=False) -> Receipt   # the ONLY writer
reg.begin(intent) -> Ticket                                   # lower-level; ticket.finish(readback)
reg.view(cells=None) -> {cell: CellView}   reg.cell(c)        # frozen views, pure fold of the journal
reg.owned(within, exclude=(), *, real_only=False) -> {pos: (plan, state)}   # replaces built_cells(within=)
reg.owners(cells, exclude=(), *, real_only=False)             # every owned position in whole cells
reg.recorded(plan) -> {pos: state} | None                     # latest live entry's plan (old *-cells.json)
reg.touched(passes=ELEMENTS) -> {plan: {(x, z)}}              # Elements; REAL_PASSES is an alias
reg.entries(cells=None, plan=None) -> tuple[EntryView]        # read-only entry listing (recovery, indexes)
reg.ticket(seq) -> Ticket                                     # re-open a begun entry: finish() or abandon()
reg.reconcile(cells=None, *, fix=False, apply=None) -> Drift
reg.follow_ups(cells=None) -> tuple[Build]    reg.ensure(apply) -> [Receipt]
reg.frontier(ring=None)  reg.seeds()  reg.marker_cell()  reg.reset()

Build(plan, pass_, cells, modifies=(), protect=None, strict=False, mask=None, cell=None, payload=None, meta=None)
Undo(target, strict=True, meta=None)   # target = plan name, or (cell, pass_); meta is journalled
Note(cell, kind, body='', ref='')  # kinds: marker hold coverage coverage-dismiss review (legacy: issue close)
Writes(plan, pass_, cells, kind, intent, seq)   # what `apply` must write; intent.payload = caller's Plan
Receipt(seq, status, planned, mismatches, sample, follow_ups, followed)
CellView(cell, ground, ghost, trees, decor, features, in_flight, marker, hold, notes, done, left)
```

Passes: Elements `feature ground trees decor` (GLOSSARY **Element**), plus `ghost-ground` and `other`
(`other` owns blocks with no progress meaning: cell markers, clean-ups). Code: `ELEMENTS` /
`REAL_PASSES` (alias). Legacy `ghost-features` lines may still appear in old journals
(ownership yields) but new `ghost-features` Builds are refused (task #68). `Ticket.abandon(reason)` finishes a begun entry `abandoned` (rolled back by a
recovery tool; it owns nothing). Legacy journal `issue` / `close` notes are kept in the fold but do
not block `done` (ticket #121); snags live in `registry/issues.json` via `scripts/issues.py`.
`apply` is `callable(Writes) -> None`:
write `Writes.cells` over MCP and nothing else (no readback, no registry, no flush needed).
`ghosts` is `callable(cell, 'ground') -> {pos: state} | {} | None` (None: cannot plan).
`BlockStore`: `read(pos)`, `read_many(positions)`, `flush()`. `MemoryBlocks` (tests), `LiveBlocks` (MCP `block_scan_region`).

`CellView`: `ground` none|ghost|generated; `ghost.ground` whether ghost ground is expected now;
`features` of `(id, 'in-progress'|'built')`; `marker` bool; `hold`; `notes`
(open coverage warnings, reviews, issues); `done` and `left` use the old DONE rule and wording, plus
`in-flight: <plan>`. Old `planned` feature status is gone (a feature exists once a Build for it is begun).

## Journal

`<world>/registry/journal.jsonl`, append-only, one JSON line each, `seq` strictly increasing. Plan content
lives in `registry/blobs/<hash>.json.zst` (Python 3.14+) or `<hash>.json.gz` (older), written and fsynced
before the `begin` line. The body is column run-length encoded, `{"rle": [[x, z, y0, y1, state], ...]}`
sorted by x, z, y, and `<hash>` is the sha256 prefix of that body. Plain `<hash>.json` blobs holding the old
`{"x,y,z": state}` dict still read. A `.zst` blob cannot be read on Python < 3.14, so open a registry with an
interpreter at least as new as the one that wrote it. Corrupt or malformed blobs raise `JournalError`. Lines: `begin`
(build/undo), `finish` (`landed` | `mismatched`), `note`, `repair` (informational).

Build `begin` lines also store an optional **`prior`** blob key: `read_many` over the planned positions at
begin time. Undo erase values are under-owner state when an earlier owner remains, else that prior snapshot,
else air. Old begins without `prior` keep under-owner-else-air.

## Invariants

1. Views are a pure fold; two registries on one file, or a cold rebuild, return equal views.
2. `begin` is fsynced before the caller writes any block. A failed begin means no blocks were written
   (the half-written line is truncated away; a failure of that rollback is reported in the error).
3. A finish failure leaves the entry `begun`; so does a strict/mask readback mismatch (`Mismatch`).
4. A torn last line (no newline) is ignored and removed by the next append. A bad line elsewhere, a seq gap or
   a missing blob is a `JournalError`.
5. Ownership is a private projection: later build wins, air/light cancels, the build's own name and
   `modifies` are skipped, ghost owners yield (a real build never clashes with them; a ghost Build over real
   work is a `Clash`). Begun entries claim ownership (conservative), undone entries do not. Landed real
   ground drops ghost-ground owners only above the real top in columns that ground plan wrote; substrate
   below and uncovered ghost columns stay owned (so undo of a feature can restore ghost, not air).
6. Flags set only on `landed`: ground clears trees/decor and sets `ghost.ground` false for the cell (view);
   ownership supersession is per-column as in (5). Trees/decor need generated ground (`Refused` at begin);
   beginning a feature that was built clears trees/decor in its cells.

7. A landed real feature strips ghost features in the cells it touches (view flips at once); ghost blocks
   still owned inside its footprint columns and Y span become a `ghost_strip_*` air Build in `follow_ups()`.
8. Ghost features need ghost or real ground; ghost ground is refused over real ground.
9. Frontier = cells with any Element started plus Chebyshev `ring` around each. `follow_ups()` lists strips, then
   ghost ground, then ghost features for frontier cells that lack them (needs `ghosts`; positions owned by
   Element builds are dropped from planner output). It is derived: applying a follow-up removes it.

## Ordering and usage

```python
rec = reg.change(Build(plan.name, 'ground', plan.cells, modifies=mods, strict=True, cell=cell,
                       payload=plan), apply)       # begin(fsync) -> apply -> flush -> read back -> finish
reg.change(Undo('house_12'), apply)                # erases to under-owner, else prior blob, else air
reg.change(Undo((cell, 'trees')), apply)
reg.change(Note(cell, 'marker', 'up'))             # single marker; 'none' clears; hold: body '' clears
reg.ensure(apply)                                  # or change(..., cascade=True): apply follow-ups
reg.reconcile([cell])                              # report only (flushes the store first)
reg.reconcile([cell], fix=True, apply=apply)       # resolve begun, re-commit drift, upgrade mismatched
```

`change` flushes the store (`LiveBlocks.mark_written()` first, if present) between `apply` and readback.
Direct `begin` users must flush before reading back. Guards that read the world (footprint, doors, roofs)
stay in `site_kit.apply`; the registry only runs `protect`, the clash rule and the pass rules.

## Errors (all subclass `CellJournalError`; nothing is swallowed)

| Error | When | State left |
| --- | --- | --- |
| `Refused(reason, detail)` | bad pass/plan, trees without ground, protect hit, ghost rules, no `apply`, bad note | nothing journalled |
| `InFlight(cells, seqs)` | a `begun` entry covers a touched cell | nothing journalled |
| `Clash(clashes)` | real earlier owner not in `modifies` | nothing journalled |
| `Mismatch(seq, mismatches, sample)` | strict/mask readback failed | entry stays `begun` |
| `JournalError` | journal/blob I/O, corruption | begin: no blocks written; finish: entry `begun` |

## Reconcile

Kinds: `in-flight`, `missing` (owned block absent), `foreign` (owned position holds another block),
`ghost-missing` (only with a `ghosts` planner), `ghost-stale`. `fix=True` needs `apply`. Begun entries are read
back and landed, re-writing the whole recorded plan first if it does not match; drift is re-written from the
current owner of each position; mismatched entries whose owned blocks read back become `landed`; follow-ups
are applied. Recorded plans hold block cells only: signs and entities are not re-committed.

## Plan -> Build (site_kit.apply, task 42)

`Build(plan.name, pass, plan.cells, modifies, strict, mask summary, cell, payload=plan, meta={structure,
pending_mask, caller meta})`; stage `feature-*`/default -> `feature`, `ground-layer`/`ground`, `trees`, `decor`,
`other`. Guards stay in `site_kit.apply` (clash report from `owned(real_only=True)`). `site_kit.McpWriter` is the
`apply` callable: block cells over MCP, plus the payload Plan's signs and entities (never journalled, so
`reconcile` re-commits block cells only). The ghost planner is `lidar_ghost.ghost_planner()`.

## Not here (task 43 and later)

No region backup/restore and no pristine reads (ADR 004 a, b). Ownership is rebuilt per cell
from plan blobs; a SQLite index can go behind `owned()` if ghost-heavy worlds make that slow.
