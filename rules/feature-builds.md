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
  edge?: {block, where: outer|all}}}`. Absent `edge` = no roof edging
  (default). When set, `block` is the parapet/coping mat; `where: outer`
  skips zone steps, `where: all` edges every step. Flat-roof overhang
  fringe only when `edge` is set (pitched overhang unchanged).
- `groups` — `[{nos, mode: each|group, front: N|S|E|W}]`
- `modifies` — earlier builds this plan may overwrite (explicit list;
  `site_kit.apply` refuses cells owned by builds not listed here)
- optional: `wall`, `floor`, `floor_min`, `step_max`, `ledge_stair`,
  `door_frame`, `keep_edits`, `notes`
- optional `facades` — per-label per-face window table from the photo
  (glazing height, pier width, wall material, bay rhythm `WW_BB_WW`).
  Dry run prints glazed share per storey per face for photo compare.
  Example: `"facades": {"SU": {"N": [{"storey": 1, "glazed_h": 3, "pier": 1}]}}`
- optional `doors` — per-portal map from the university register
  (`scripts/portals.py`): `{"42-A": {"leaves": 1|2, "block": "iron_door"}}`.
  Pick `block` with `block_reference.py` from the portal photo. `leaves: 2`
  places mirrored hinges and stays framed (`door_frame_issues`). Dry run
  prints each beside the portal name. Absent → legacy front/rear spruce.
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
  Roof edging is optional via `roofs[label].edge` (default none — no
  parapet, no flat overhang fringe). Facades, doors, and roofs reuse the
  same label keys. Dry run prints `footprint osm=N readable=M pad=P cover=C%`.

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
