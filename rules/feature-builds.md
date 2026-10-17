# Non-house building features

Read before any campus / hall / shed / odd-mass building feature (not houses or
terraces). Roof and fabric rules: `site/CRAFTSMANSHIP.txt`. Houses stay on
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

Write the spec from **this** run's evidence (OSM ways, lidar storeys/roof,
Street View / aerial). Do not copy a peer spec or a per-run build script. Do
not extend `house_build` for odd shapes.

Acceptance fixture: Students' Union `site/builds/susu_42.json` (OSM
`w33830305`) dry-runs end-to-end via `feature_build` (no `--apply`).

Facade table, double/coloured doors, greeblies, path-in-footprint, and
evidence contact sheet are separate system tickets — not this module.

If something looks wrong, fix the **spec** (or `feature_build.py` if the bug
is shared) and re-apply the same build name. Never stack `_b`/`_c` patch builds.
