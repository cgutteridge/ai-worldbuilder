LAYERED PASSES AND MASK UNDO
Tool-repo rules (ai-worldbuilder/rules/handoff-layered-passes.md).
Written 2026-10-14; registry cut-over 2026-10-21. Pass order for empty-world refine;
ring focus and holds live in the active world's **registry/** (marker + hold fields).
`site/cells/*-STAGES.json` is the apply journal only (site_kit / site_recover).

------------------------------------------------------------------------
1. PASS ORDER (per cell, in this order, never out of order)
------------------------------------------------------------------------
 1. MAJOR FEATURES — buildings, and anything large that gets its own thing:
    bridges, tunnels, other big structures. Each is its own build with its own
    undo (region snapshot + origin check, see §4). Not mask-undone.
 2. GROUND — redo every ground-level block not covered by a major feature.
    Includes the above-ground strip of all non-air blocks not owned by a neighbour, because trees and decor may only
    go on air. Clipped to the cell bounds. Not mask-undone (rerun or snapshot).
 3. TREES — placed only on air. Writes a mask (§3).
 4. DECOR — hedges, street furniture and other minor things we are sure work.
    Placed only on air. Writes a mask (§3). Hedges live here, not in ground.

 DEFINITION OF DONE (I-057 / ADR 003 empty-world): a cell is done when evidence-backed feature
 builds cover mapped buildings, ground/trees/decor are applied, and the trees mask follows the
 overhang rule (§6; masks carry rule 'overhang-v1'). Not “no unrefined vendor lump left” — vendor
 lumps alone do not keep a cell open. `cell_registry.py next` / `site_handoff.cell_gaps`
 report missing feature builds, layer passes, and trees-mask currency; a finished cell with an
 old tree mask is reopened for a trees re-run.

Gardens (user, 2026-10-14): a house feature is the house only. Gardens belong to the later
passes: lawn/paving = GROUND, trees and shrubs = TREES, fences, hedges, sheds, planting and
other small things = DECOR. Feature builds must not lay garden cells.

Rule: a later pass may not change a block an earlier pass owns. Trees and decor
check the live world before every write.

------------------------------------------------------------------------
2. WHY AIR-ONLY MATTERS
------------------------------------------------------------------------
Trees and decor only place into air (a leaf may also merge into a neighbour tree's leaf, §6). So undo of a tree or decor pass is exact:
the position was air before, so setting it back to air restores it. No need to
remember old blocks. This is the invariant the whole undo scheme depends on, so
the planner must check it at plan time AND again at apply time (the world can
change between the two).

------------------------------------------------------------------------
3. MASK FILE FORMAT (proposed)
------------------------------------------------------------------------
Path: verification/site/masks/<pass>-<tx>_<tz>.mask.json
One file per pass per cell. Fields:

  pass        "trees" | "decor"
  cell        [tx, tz]
  plan        kit plan name that wrote it, e.g. highfield_48_m44_trees
  created     ISO timestamp
  bbox        [x0, y0, z0, x1, y1, z1]   inclusive; a trees mask may reach past the cell edge (§6)
  offset      [ox, oy, oz] = bbox minimum corner (positions below are relative to it)
  size        [sx, sy, sz]
  palette     list of block ids placed; palette[0] is always "minecraft:air"
  data        run-length list [palette_index, run] over the box in x, then z, then y order
              (air = index 0, so only placed blocks need listing)
  count       number of placed (non-air) blocks
  sha256      of the placed (x, y, z, block) list, for integrity
  air_only    true (the planner asserted air at every placed position)

Undo for one mask, in order:
  a. Verify integrity: recompute sha256 from the file and the live world.
  b. Conflict check: for every placed position, live block must still equal the
     palette block. If any differ, STOP and report; do not undo partially.
  c. Undo: write "minecraft:air" at every placed position, except positions another
     mask also lists (merged tree crowns, §6): the other cell still owns those.
Undo passes run in reverse order of the passes that wrote them.

------------------------------------------------------------------------
4. MAJOR FEATURES: UNDO IS NOT THE MASK
------------------------------------------------------------------------
Buildings, bridges and tunnels are big and do not write masks. Their undo is a region
snapshot taken before the build, with a fingerprint of a few known blocks inside the
box recorded in the stage file. Before any restore:
  - check the fingerprint at the planned origin (this is what the last restore lacked:
    the structure file was 121x15x89 but the restore box was 94x15x85, and nothing
    checked the origin);
  - check the restore's bounds equal the snapshot's bounds;
  - after the restore, compare the fingerprint again and look at the view.

------------------------------------------------------------------------
5. APPLY JOURNAL (not progress SoT)
------------------------------------------------------------------------
site/cells/<tx>_<tz>-STAGES.json records in-flight applies for recovery:
  status      applying | applied | applied-with-mismatches | rolled-back | …
  plan / mask file / snapshot path / fingerprint
Progress SoT is `registry/cells.json` + `registry/features.json`. A cell is done when
the registry says DONE (layers + features + no open warnings/issues), readback was clean,
patrol found no clashes, and a visual check against aerial/photos passed.

------------------------------------------------------------------------
6. CLIPPING AND NEIGHBOURS
------------------------------------------------------------------------
TREES (user, 2026-10-15, replaces the old clip rule): a tree belongs to the cell its trunk is in.
Its crown may overhang into neighbouring cells, where the world block is AIR or a LEAF owned by a
neighbour's trees mask (crowns merge). Overhang is recorded in the PLANTING cell's own trees mask
(that mask may cross the cell edge) and never overwrites any other block: not a neighbour's trunk,
roof, wall, ground, decor or feature. Trunks are always in-cell. One check does it: trees_layer.may_plant.
Undo of a trees mask removes its overhang too, but leaves a block that another mask also lists.
Ground and decor still clip to the cell bounds (decor_layer, ground_layer); their masks never cross an edge.

------------------------------------------------------------------------
7. REVIEW NOTES ON THE IDEA
------------------------------------------------------------------------
Good:
  - Exact undo, because placement is air-only.
  - Undo is cheap: it writes air over the masked positions only.
  - Masks make it obvious what each pass changed.

Needs handling:
  - A later ground redo could overwrite a tree or decor block. The conflict check in
    step 3b catches it. Ground redo is only allowed after trees/decor are undone, or
    it must skip masked positions (decide when we first run it).
  - Trees rely on the ground under them. If ground is redone after trees, trunks can
    float. Hence the fixed order.
  - Major features are not mask-undoable (§4). Keep their snapshots until the cell is
    verified.
  - The air-only check happens at plan time and apply time; the world can change in
    between (the lesson from the restore).

------------------------------------------------------------------------
8. OPEN QUESTIONS
------------------------------------------------------------------------
  - Should ground redo skip masked positions, or require undo first?
  - Keep old masks after a cell is verified, or delete them to save space?
  - Do bridges and tunnels need a separate pass type, or are they major features with
    their own snapshots (current assumption: the latter)?

------------------------------------------------------------------------
9. NEXT STEPS
------------------------------------------------------------------------
  1. Read-only: confirm the 48,-44 stage state (patrol, visuals).
  2. Write the mask writer and reader as one small module with tests (dry-run only).
  3. Run the ground pass on 48,-44 with a clipped dry run and preview, then ask.
  4. Trees pass with masks. Decor pass with masks.
  5. Each pass ends with a verification step before the next starts.

------------------------------------------------------------------------
10. DECISIONS (2026-10-14, from user)
------------------------------------------------------------------------
GROUND STEP
  - Replace the top non-air block in each column with the surface block.
  - Treat flowers and grass stems (short grass, tall grass, fern) as AIR for this
    step (stock stems are not ground surface). Grass BLOCK is not air.
  - Then clear the vertical column above the surface of all non-air blocks not owned by a neighbour, EXCEPT any
    block that belongs to a neighbouring cell's tree or decor mask. Those positions
    are protected even when they overhang our columns.
    [Interpretation to confirm: "clear a vertical column of air above" = remove
    all non-air blocks not owned by a neighbour above the new surface in our columns, skipping neighbour
    mask positions. Pure air is left as it is.]
  - Ground does NOT touch any column owned by a major feature step.
  - If this cell's trees and decor already exist, they must be re-run after the
    ground step. Ground may therefore corrupt THIS cell's trees and decor, but never
    a neighbour's.
  - Done with tools (scripts and checks), not by hand or AI judgement.

MAJOR FEATURES AND TREES
  - Recommended rule: a major feature wins. Trees and decor are never placed inside
    a major feature's volume. If any non-air block not owned by a neighbour, or a tree, already sits in a major
    feature's footprint, the major feature step clears it as part of its own
    footprint. A tree whose crown only overhangs a major feature is removed by the
    major feature step, unless the feature is a bridge or tunnel, which keeps the
    crown (to be decided when we do the first bridge).
  - Please confirm or change this.

NOTHING ELSE CHANGES: pass order in §1 stands.

------------------------------------------------------------------------
11. FEATURE REGISTRY (proposal, 2026-10-14)
------------------------------------------------------------------------
Purpose: when a major feature runs or re-runs, any tree or decor of another cell that
overlaps it must be re-run too, without anyone remembering to do it.

Registry file: site/REGISTRY.json (one list of entries, machine-written by the
feature tool; never edited by hand).
  entry = {
    "feature":     "<major feature build name>",
    "feature_cell":[tx, tz],
    "dep_cell":    [tx, tz],          the cell whose trees/decor overlap
    "dep_pass":    "trees" | "decor",
    "positions":   count of overlapping non-air blocks,
    "status":      "queued" | "run" | "done",
    "created":     ISO timestamp
  }

Feature step (each major feature gets this, in order):
  1. Fingerprint + origin check (§4).
  2. Clear the vertical path above its footprint: non-air blocks not owned by a
     neighbour are removed; any block owned by a neighbour's tree or decor mask is
     left in place and counted.
  3. Each neighbour-owned block inside the feature volume gives a registry entry for
     (dep_cell, dep_pass) with status "queued".
  4. Run the queued dep passes now, or leave them queued, as the operator chooses.
     A dep pass re-run first undoes its own mask (§3 undo), then re-plans with the new
     feature volume excluded. Its trees and decor never enter a feature volume.

Ground step follows the same rule: it skips feature-owned columns, and records a
dep pass for any neighbour-owned block it cannot clear.

DECIDED 2026-10-14 (user): leave it. A feature never removes a neighbour's block;
the neighbour's tree/decor is re-run and drops the overlapping blocks itself.

Was the open decision:
  When a neighbour's tree crown sits inside a new feature volume, the feature may NOT
  remove it (it belongs to the neighbour). Recommended: the feature leaves it, the
  registry queues the neighbour's tree re-run, and the re-run drops the overlapping
  leaves from its own plan. The alternative is to let the feature remove it, which
  breaks the rule that a cell never damages a neighbour's deployment.

------------------------------------------------------------------------
12. DECISIONS (2026-10-14, user, round 2)
------------------------------------------------------------------------
Historical note (ticket #33): QUEUE.txt / CELL-HOLDS.jsonl are retired. Equivalent
today: `cell_registry.py hold <cell> --reason …` and `cell_registry.py next`.

GROUND REDO IS A BLOCKING HOLD
  - Hold the cell in the registry until ground redo, trees and decor are verified.
  - coverage.py reports a held cell as status=held, never done, until the hold clears.

GROUND AND MASKS
  - Ground should not touch a masked tree/decor position at all. If a ground plan
    does, that is a fault: undo that one mask first (§3), then re-run that pass
    after the ground. Undo is only for the case where ground hits a mask.
  - scripts/pass_mask.py masked_overlaps() finds these positions before apply.

KEEP MASKS
  - Keep the current trees and decor mask per cell (one file per pass per cell,
    overwritten on re-run). No history of older masks.

CONFIRMED §10 (user, 2026-10-14)
  - The §10 decisions are approved as written: ground step, major features and
    trees, and the interpretation marked [to confirm] in the ground step.
  - (Earlier "£10" was a mis-typed "§10", not a spend. No spend approved.)

------------------------------------------------------------------------
13. HANDOVER (2026-10-14, end of session) — HISTORICAL
------------------------------------------------------------------------
Kept as a worked example of the first ground pass on 48,-44. Do not reopen
QUEUE/CELL-HOLDS paths; use the registry. Snapshot path and footprint override
notes below remain useful archaeology.

  - Ground layer APPLIED to cell 48,-44 (plan highfield_48_m44_ground).
  - Region snapshot: backups/regions/2026-10-14-024133-highfield_48_m44_ground
  - Footprint guard overridden for THAT cell only (7 polished_diorite blocks);
    do not copy the override elsewhere.

WHAT EXISTS IN CODE (still true)
  - scripts/ground_layer.py / trees_layer.py / decor_layer.py + registry_hooks
  - scripts/pass_mask.py; scripts/coverage.py write (registry rollup + holds via registry)
  - Apply journal: site/cells/*-STAGES.json for in-flight recovery only

------------------------------------------------------------------------
14. 2026-10-14 (user): 48,-44 DONE; ALL OTHER CELLS RESTART
------------------------------------------------------------------------
48,-44 is the first done cell (ground from first principles: ground_layer.py + ground_sources.py;
trees_layer.py; decor_layer.py; mask_undo.py). Every other cell starts from scratch for
ground, trees and decor; features are done as needed.
When doing ground and decor for a cell, REVIEW THE NEIGHBOURING CELLS first (edge paths,
roads, hedges, shade-bridged paths and noise filters all read evidence across the cell edge).
Old ground scripts are retired: backups/retired-ground-scripts-2026-10-14.tar.gz.
