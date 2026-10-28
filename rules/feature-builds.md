# Non-house building features

Read before any campus / hall / shed / odd-mass building feature (not houses or
terraces). Roof and fabric rules: `rules/craftsmanship.md`. Houses stay on
`rules/terrace-builds.md` + `scripts/house_build.py`.

## Live path

Every non-house building feature apply uses **`scripts/feature_build.py`** with
a JSON spec at `site/builds/<name>.json`. Dry run unless `--apply`. Spec keys
(full list in the `feature_build` module docstring):

- `name` — plan / cells-record name
- `ways` — `{osm_way_id: label}` footprints to build
- `storeys` — `{label: int >= 1}` clear storeys (each 3 blocks high); with
  `zones`, this is the facade storey grid count (openings still use
  `STOREY_H=3` from the median-ground datum)
- `roofs` — `{label: {kind: flat|pitched, material: <slab mat>,
  edge?: {block, where: outer|all},
  zone_materials?: {<level_m_AOD>: <mat>}}}`. Absent `edge` = no roof
  edging (default). When set, `block` is the parapet/coping mat;
  `where: outer` skips zone steps, `where: all` edges every step.
  Flat-roof overhang fringe only when `edge` is set (pitched overhang
  unchanged). Optional `zone_materials` overrides `material` per lidar
  plateau (same level keys as `zones`). Dry run prints each zone's mean
  aerial colour and a suggested block from the site_kit colour table
  (suggestion only — not a gate).
- `groups` — `[{nos, mode: each|group, front: N|S|E|W}]`
- `modifies` — earlier builds this plan may overwrite (explicit list;
  `site_kit.apply` refuses cells owned by builds not listed here)
- optional: `wall`, `floor`, `floor_min`, `step_max`, `ledge_stair`,
  `door_frame`, `keep_edits`, `notes`
- optional `facade_plan` — per-label plan the designer fills from portals and
  street photos (`lib/builds/facade_plan.py`). Shape:
  `{"SU": {"faces": {"S": {"glazing": [{"storey": 1, "glazed_h": 2, "pier": 1}]}},
  "entrances": [{"id": "57-A", "portal_id": "57-A", "face": "S", "leaves": 1,
  "block": "iron_door", "canopy": {"kind": "covered_area"}, "steps": 0,
  "floor_offset": 0, "cell": [48, -44],
  "provenance": {"source": "register_entry", "confidence": "high",
  "evidence_ids": [], "unresolved_reason": ""}}]}}`.
  Every portal on the footprint must be placed or listed unresolved
  (`provenance.unresolved_reason` non-empty). Provenance is shared
  (`lib/site/provenance.py`). No register → still add photo-read entrances.
  Build places doors from placed entrances only (unresolved are listed, not
  built); door floor y is outside ground + `floor_offset` (I-017). The old
  per-portal `doors` map is refused. Face slots `roof_edge` / `detail` are
  reserved; real roof edge and greeblies stay on `roofs.edge` / `details`.
  Window frames are build-time (#131), not plan fields.
- optional `facades` — legacy per-label per-face window table from street
  photos and the aerial (glazing height, pier width, wall material, bay
  rhythm `WW_BB_WW`). Prefer `facade_plan[label].faces[face].glazing`.
  Give a row list for **every exposed face**, and a row for every storey
  the evidence shows on that face. Where no photo or aerial shows a face,
  copy the nearest read face and say so in `notes`. Dry run prints glazed
  share for every exposed face and storey; faces with no facade rows are
  marked `no evidence read` (report only — not a gate).
  Example: `"facades": {"SU": {"N": [{"storey": 1, "glazed_h": 3, "pier": 1}]}}`
- optional `details` — per-label roof and facade greeblies from the aerial
  sheet and street photos (`rules/building-review.md`). Each row is
  `{kind, at: [x,y,z], size: [sx,sy,sz], source, face?}`:
  roof kinds `plant_room`, `vent`, `louvred_vent`, `skylight`,
  `solar_panel`, `lift_overrun`; facade kinds `drainpipe`, `sign`,
  `canopy`, `ledge`, `fascia_band` (`face` required). Entrance canopy /
  covered area belongs on `facade_plan` entrances. Placed after the
  shell; dry run prints each detail and its source. List what reads at
  street scale, or leave `details` empty / omit it when none are visible.
  Example: `"details": {"SU": [{"kind": "plant_room", "at": [12, 59, 22],
  "size": [3, 2, 2], "source": "aerial NW plant box"}]}`
- optional `exclude` — per-label boxes cut out of that label's map footprint,
  `{"JSC": [[x0, z0, x1, z1], ...]}` (inclusive block coords). Use when the
  vector outline is larger than the real building (lidar shows open ground, or
  separate houses, inside it). Clips only the named label; clipped cells are
  not treated as another building's footprint. Dry run still prints the
  footprint counts, so check `cover` after clipping.
- optional `zones` — per-label lidar roof plateaus **within one footprint**
  (one vector way, several roof heights). Use when a single `storeys`
  count cannot express terraced roofs (e.g. SUSU w33830305 with plateaus at
  41/45/46/47/48/50 m AOD). Example:

  ```json
  "zones": {
    "SU": {
      "from": "lidar",
      "levels": [41, 45, 46, 47, 48, 50],
      "bucket": 4,
      "axis_deg": "auto"
    }
  }
  ```

  Pipeline: 1-block-stair squaring (close then open, no fixed halo) → 5×5
  median DSM → snap to `levels` (or `"auto"` plateaus) → mode-filter →
  bucket on the building's rotated grid so zone edges are straight runs.
  Walls rise from **local** lidar ground so downhill faces gain storeys.
  With `roofs[label].kind == "flat"`, each zone is a flat deck. With
  `kind == "pitched"`, within each zone a lidar ridge becomes stair-slab
  slopes from the zone eave up to the ridge; flat plateaus stay flat.
  Lidar heights are only a guide: the builder regularises them so
  neighbouring columns within each roof zone differ by at most one stair
  (2 half-blocks); intentional steps between distinct zone levels stay. It then
  sets each stair's facing from the upslope and its `shape` from stairs
  at the **same block Y** (the game's neighbour rule — not from height
  differences on the diagonal). `python3 scripts/roof_lint.py` checks
  planned stair shapes against that rule. Inside a zoned mass, an open upper
  stair edge facing a taller roof column gets wall backing at the lower
  stair's Y when that cell would otherwise be air; solid floors and exterior
  air stay as they are. Rebuilt hips and valleys also
  need an in-game check for light holes; a correct neighbour shape alone
  does not prove the roof is sealed. Roof edging is optional via
  `roofs[label].edge` (default none — no parapet, no flat overhang
  fringe). Facades, doors, and roofs reuse the same label keys. Dry run
  prints `footprint osm=N readable=M pad=P cover=C%`.

Write the spec from **this** run's evidence (footprint ways, lidar storeys/roof,
Street View / aerial). Do not copy a peer spec or a per-run build script. Do
not extend `house_build` for odd shapes. Prefer `zones` over a custom
`site_kit` script when one way has several roof plateaus.

Designer / builder models: name both before writing the spec
(`rules/build-roles.md`, `python3 scripts/build_roles.py show`). The JSON spec
is the hand-off when designer and builder differ; the builder applies with
`feature_build.py` and does not redesign massing from scratch.

Acceptance fixture: Students' Union `site/builds/susu_42.json` (vector way
`w33830305`) dry-runs end-to-end via `feature_build` (no `--apply`). For
multi-plateau SUSU, add a `zones` entry as above and confirm 0 guard
failures on dry-run.

**Lidar ghosts (ground only):** After `cell_marker --raise`, frontier cells show
ghost ground with DSM−DTM top colours (not volumetric grey columns). Real features
overwrite ghost ground where they sit (ghost owners yield). Use neighbouring
coloured tops to judge whether the build sits on the right footprint. Before
handoff, `python3 shims/lidar_ghost.py status --cell <home> --ring 2` should show
ghost ground applied on frontier neighbours that still lack real ground.

Greeblies and path-in-footprint are separate system tickets. After apply, run
`python3 scripts/evidence_sheet.py <name>` (portal-facing cameras, top-down,
aerial crop); the build records the sheet path and `site_handoff.py end`
refuses a FEATURE item without it (`rules/frontage-and-evidence.md` §4).

If something looks wrong, fix the **spec** (or `feature_build.py` if the bug
is shared) and re-apply the same build name. Never stack `_b`/`_c` patch builds.
