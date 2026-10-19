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
- `storeys` — `{label: int >= 1}` clear storeys (each 3 blocks high)
- `roofs` — `{label: {kind: flat|pitched, material: <slab mat>}}`
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

Write the spec from **this** run's evidence (OSM ways, lidar storeys/roof,
Street View / aerial). Do not copy a peer spec or a per-run build script. Do
not extend `house_build` for odd shapes.

Acceptance fixture: Students' Union `site/builds/susu_42.json` (OSM
`w33830305`) dry-runs end-to-end via `feature_build` (no `--apply`).

**Lidar ghost v2 (cross-cell):** After `cell_marker --raise`, frontier cells may
show grey DSM−DTM placeholders. A feature whose footprint crosses a cell border
must not leave that grey mass under the real shell: `site_kit.apply` pre-strips
ghost-feature blocks inside the planned volume before fill; `feature_build` keeps
plan cells where only `ghost_*_features` owned the column and adds ghost owners
to `modifies` on live apply. Before handoff, run
`python3 shims/lidar_ghost.py status --cell <home> --ring 2` and confirm every
touched cell has `ghost-features` stripped/superseded where the build sits.

Greeblies and path-in-footprint are separate system tickets. After apply, run
`python3 scripts/evidence_sheet.py <name>` (portal-facing cameras, top-down,
aerial crop); the build records the sheet path and `site_handoff.py end`
refuses a FEATURE item without it (`rules/frontage-and-evidence.md` §4).

If something looks wrong, fix the **spec** (or `feature_build.py` if the bug
is shared) and re-apply the same build name. Never stack `_b`/`_c` patch builds.
