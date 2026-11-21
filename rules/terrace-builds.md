# Houses and terraces

Read before any house or Broadlands-type terrace build. Roof and fabric rules:
`rules/craftsmanship.md`.

## Live path

Every house feature apply uses **`scripts/house_build.py`** with a JSON spec at
`site/builds/<name>.json`. Dry run unless `--apply`. Non-house building masses
(SU, halls, sheds, campus lumps) use **`scripts/feature_build.py`** instead —
see `rules/feature-builds.md`. Spec keys (full list in the `house_build` module
docstring):

- `name` — plan / cells-record name
- `ways` — `{osm_way_id: label}` footprints to build
- `roofs` — `{label: slab_material}` (e.g. brick, mud_brick, deepslate_tile).
  The same flat(ish)/pitched rule as features: rise/run up to 1 in 2 may be
  any full block or slab in `config/roof-materials.json`; steeper than that
  needs a triad (full block, slab and stairs). See `rules/feature-builds.md`.
- `groups` — `[{nos, mode: each|group, front: N|S|E|W}]`
- `modifies` — earlier builds this plan may overwrite (explicit list;
  `site_kit.apply` refuses cells owned by builds not listed here)
- optional: `floor_min`, `step_max`, `ledge_stair`, `door_frame`, `keep_edits`,
  `notes`

Write the spec from **this** run's evidence (footprint ways, lidar storeys/roof,
Street View / aerial frontage). Do not copy a peer spec or a per-run build
script. Old one-off scripts: `scripts/legacy/` if present.

House only: no gardens. Lawn is GROUND; trees/shrubs TREES; fences, sheds,
planting DECOR.

## Evidence and layout

- One build for the whole run on the street (shared frontage, roofs, party
  walls), not a separate apply per pair.
- Party walls: squared columns, pitch 4 or 5 so the row keeps the real ~4.5 m
  frontage. Pick wall x's at the midpoints between map centroids and round;
  each house centre should land within ~1.5 of its map centroid.
- Front from evidence / map front edge; floor height from pavement at the
  door (see lidar ground Y).
- Door side alternates in pairs when the street photo shows it; label `hint`
  if unseen.
- Per-house roof material from aerial colour (`roofs` in the spec), picked
  from `config/roof-materials.json` using that flat(ish)/pitched rule.
- Find terrace ends first: `.venv/bin/python scripts/aerial_annot.py x0,z0,x1,z1 out.png`.
- Plot angle: `python3 scripts/geo.py slope --bearing …` or `--points x0,z0,x1,z1`
  (snaps via `terrace.snap_slope`). Frontage pitch: `geo.py party --bbox …`.
- Pass every earlier build that touched the plots in `modifies`.

If something looks wrong, fix the **spec** (or `house_build.py` if the bug is
shared) and re-apply the same build name. Never stack `_b`/`_c` patch builds.

Verify: readback, `osm_footprints.py check`, walks door to first floor and
door to back garden for every house, contact sheet (photo camera, front 3/4,
rear, top).

## `terrace.py` (kept, not an apply path)

`scripts/terrace.py` remains for **`snap_slope`** and related geometry used by
`scripts/geo.py` (plot bearings → even block stairs). It is not a live
feature apply entry point; do not wire new house builds through it.
