FOLIAGE — STATIC VEGETATION ONLY
Tool-repo rules (ai-worldbuilder/rules/foliage.md).
Updated: 2026-10-15

Trees are pass 3 of a cell (rules/handoff-layered-passes.md §1), after features and
ground, before decor. This file is the single home for the vegetation rules.

------------------------------------------------------------------------
Rules
------------------------------------------------------------------------
 - NEVER place sapling blocks. They grow on random tick, so the world drifts after
   apply: builds stop being idempotent, readback lies, and ground work
   gets undone. Strip stock/vendor saplings in the ground pass; do not replant them.
 - Canopy is static: log + leaves[persistent=true] massing (or hedge/wall), sized from
   the aerial. Placement allowlist: `surface_probe.REFINE_PLANTS`.
 - Trees and decor go only where the live block is air (or a grass stem/flower), so
   undo is exact (mask_undo.py).
 - A tree is never ROOTED (crown centre) in a feature zone: a column the ground layer
   did not surface (building footprint, structure build, bridge; read from
   verification/site/<cell>_ground-cells.json plus footprint-map polygons). Trees inside a
   feature are the feature build's own job, provided they do not overspill its zone.
   Crowns rooted outside may overhang a feature.
 - Crowns on a cell edge: HANDOFF-LAYERED-PASSES §6 (overhang into neighbour air or
   neighbour leaves is kept, recorded in the planting cell's mask; nothing else is
   overwritten). The trunk-owning cell must be prepared, with ground and features
   landed. Leaf-only overhang does not require the neighbouring cell to be prepared.

------------------------------------------------------------------------
One cell
------------------------------------------------------------------------
  python3 scripts/trees_layer.py --cell tx,tz                          # dry run + crown review image
  SITE_OWNER=<lock-owner> python3 scripts/trees_layer.py --cell tx,tz --apply

Check the crown review image and the kinds against the aerial before apply. Undo for
a re-run: scripts/mask_undo.py --cell tx,tz --pass trees (decor first, then trees).
scripts/cross_zone.py report --cell tx,tz lists structure leftovers on open/gap/path
columns near the cell edge.

------------------------------------------------------------------------
Tree review (every cell redo: I-073 step 4, before the trees pass is called done)
------------------------------------------------------------------------
CAUTION (2026-10-16, checked on 48,-45 x6208..6272 z-5696..-5632 with the lidar/live footprints drawn over aerial): national lidar
tree columns hold only about a third of the real crowns and sit ~3-4 blocks SOUTH of the crown seen in aerial; the live trees match aerial
crowns poorly too. So lidar is a rough height hint and a cross-check, never the position source and never a complete inventory. Best aerial is the
position source: read crowns by eye from a cropped, gridded aerial window (64 blocks, 12 px per block) and record them as explicit crowns.
Lidar (DSM kind 2/3 columns) is measured heights for the crowns it has; the aerial PNG sheet shows what is there now. Use both:
  python3 scripts/tree_review.py --cell tx,tz --marks <scratch>/m.json     # or --zone x0,z0,x1,z1
  python3 shims/google_aerial.py sheet --cell tx,tz --marks <scratch>/m.json   # or --at X,Z --radius 60 --zoom 20
Verdicts (crowns are matched by their tops, never by blob, because the live canopy merges crowns):
  MISSING  (red)    lidar crown, no live crown top within 4 blocks. Last column = blocks to the nearest live leaf: 0-4 means
                    a live crown stands there but its top is elsewhere (usually fine), >8 means nothing is there.
  INVENTED (blue)   live crown with no lidar crown near it. Says nothing by itself (lidar misses most crowns): look at aerial, and
                    expect the live crown to be misplaced as often as invented.
  LOW/HIGH (orange) matched, top differs by >= 4 blocks (live plane crowns are 12-17 high, lidar often 4-10: look at
                    aerial shadow length before correcting).
  ROOF (purple)     lidar crown over a roof (kind 3): check aerial that it is a tree overhanging, not a mis-read roof.
Decide each MISSING with aerial showing a real tree (check the distance column) and each INVENTED over lawn/paving/roof:
  - real tree missing: add it to reference/trees-extra-crowns.json ({"tx,tz": [{"x","z","r","evidence"}]}, evidence names
    the aerial sheet + lidar height), re-run the trees pass for that cell (dry run first, I-043 diff). The lidar height
    gives the crown size (height 13 = r 6+ plane tree; 7-10 = r 3.5-5; 3-5 = bush).
  - invented over non-tree ground: remove at the root (trees_layer detection), not by a one-off hand edit.
  - lawn where lidar and aerial both show nothing: leave open (Library Square formal lawn, I-060).
Record counts and decisions as a journal note on the cell (e.g. "tree review: lidar 12 crowns, 2 missing added, 0 invented over
non-tree"). Cells whose neighbours' crowns overhang the edge: review them together (--zone across the edge).

------------------------------------------------------------------------
Redo from aerial (user, 2026-10-16: the trees pass reads the z19 mosaic; aerial is the position source)
------------------------------------------------------------------------
Done for 48,-45, 48,-44, 49,-45, 49,-44 and 48,-46 (Library Square zone x6150..6349 z-5787..-5583; 2026-10-16). Crowns are read BY EYE from gridded aerial crop PNGs
(`google_window.py`; tiles downloaded, not browser HTML) and stored in reference/trees-google-crowns.json:
  {"tx,tz": {"evidence", "zones": [[x0,z0,x1,z1]], "crowns": [{"x","z","r","note"}]}}
Inside a zone these crowns replace the detector; outside it the detector/dense-fill generation and
lawn/feature-zone restrictions stay unchanged. Evidence-led rooting applies to aerial-sheet crowns,
mapped tree/tree_row crowns, and explicitly listed extra crowns. Their hard surface alone is not a
reason to reject them: gravel, paved courtyards, plazas and pavements can hide a small planting pit.
Existing explicit courtyard/feature-zone exceptions remain; real ownership, masks and collisions still
protect structures. Rooting does not replace the ground block to make a tree legal.

Check the trunk ground footprint, not the crown centre or canopy footprint: mapped carriageway/water,
live water and waterlogged ground reject a root. Missing surface or unknown water status leaves it
unplaced with a review reason. Detector/dense crowns retain their material restrictions and never
shunt; the common road/water safety check still applies to their trunks. Leaves may overhang roads,
water and neighbouring cells under the existing air/ownership/mask rules, including unprepared neighbours.

For an evidenced crown, test the accepted pre-shunt position first. Only a carriageway/water conflict
permits one horizontal block of adjustment: test its four orthogonal neighbours, never diagonals,
chained adjustments or a wider search. Each candidate repeats trunk, cell-boundary, feature-zone or
explicit exception, collision and mask checks. Among valid adjacent candidates, choose the smallest
squared distance to that retained crown's original observation, then stable (x,z) order. A blocked trunk
on otherwise legal ground is not a reason to move. No legal adjacent candidate means unplaced with a reason.

Existing lidar/crown snapping is a separate stage; keep its reports alongside rooting reports so the
movements cannot conceal each other. Each retained crown carries its own observation even when several
crowns snap to one peak. Plan/report JSON retains trees/skipped and adds rooting from/to/observed_crown,
reason and candidate attempts; crown_snaps/mapped_snaps remain separate. The crown review image draws
a yellow line for each root adjustment. Console summaries count adjustments and exclusion reasons;
full per-item details are in the printed world-local verification report.

Radius guide: r>=6 plane tree, 3.5-6 mid tree, 2-3.5 small round, <2 bush (leave bushes to decor).
  1 python3 shims/google_window.py --world <world> crop --at X,Z --half 32x16 --ppb 13
    Read the cached gridded crop PNG; read centre and radius from the grid.
  2 Write crowns into reference/trees-google-crowns.json (zones = the part read).
  3 python3 scripts/trees_replan.py --world <world> --cell tx,tz --out plan.json
    python3 shims/google_window.py --world <world> plan --at X,Z --half 32x16 --plan plan.json
    Compare planned crowns and skipped reasons against the evidence; inspect both snap and root adjustments.
  4 Use the checked redo workflow (.agents/skills/redo/SKILL.md): inspect
    python3 scripts/redo_layer.py --world <world> --cell tx,tz --layers trees
    then run the same selection with --apply in the background and check its final exit/result.
    The wrapper owns its lock; trees-only redo leaves cleared decor pending. Include decor only if requested.

Retired and deleted (do not look for them): build_cell_foliage.py, the "foliage last" cadence and the aerial
canopy pass plant_cell_canopy.py with HAND_CROWNS (Q-064; its 48,-45 result was
reverted 2026-10-14). Trees come only from trees_layer.py; voxel shapes live in lib/layers/tree_voxels.py.
