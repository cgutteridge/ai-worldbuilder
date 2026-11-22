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
- `roofs` — `{label: roof_material}` (e.g. brick, mud_brick, deepslate_tile).
  The same flat(ish)/pitched rule as features: rise/run up to 1 in 2 may be
  any full block or slab in `config/roof-materials.json`; steeper than that
  needs a triad (full block, slab and stairs). See `rules/feature-builds.md`.
- `roof_fit` — optional `{label: "columns"|"octant"}` (default `columns`). `octant`
  designs a pitched roof over a rectangular footprint as a solid volume of half-block
  cells (each block is 2 x 2 x 2), with the surface a plane through the footprint's fitted
  rectangle, then fits each block to the closest of air, full block, bottom or top slab and
  bottom stair (four facings, five shapes) by cells matched. A stair's corner shape comes
  from the same-height neighbour rule inside the fit, whatever the stair material, so
  corners come out of the design. It needs `roof_pitch` above 0.5 and `mode: each`. A
  footprint that is not close to a rectangle, or a fitted ridge that would jog, falls back
  to `columns` with a printed note. The dry run prints how many half-block cells it missed.
- `roof_pitch` — optional `{label: rise_per_run}`, finite numbers from 0 to 1.
  Missing labels default to 0.5. At or below 0.5 the existing half-block slab
  rings remain unchanged; above 0.5 the roof rises inward at the supplied ratio,
  snapped down to half-block heights, with stairs, explicit corner shapes,
  solid underlay and slab or opposed-stair ridge caps. The overhanging eave
  ring starts at wall-top level as a bottom slab (shallow pitch) or a stair
  (steep pitch), with no full block under it. A footprint that is a rotated
  rectangle (drawn square to an angled road: at least 3 degrees off the grid and
  filling 75% of its fitted rectangle) takes its pitched heights from the fitted
  axes, giving a straight ridge with hips; other shapes use the grid edge
  distance. If the rotated roof would step its ridge sideways (about one in ten
  sizes), the roof falls back to the grid distance, so it never refuses a roof the
  grid distance would plan. Full-only flatish
  materials use full blocks instead of inventing a slab variant. Pitched
  materials without a catalogue triad are refused with shared suggestions.
  `mode: group` labels must have equal effective pitches; the first label's
  material covers the shared roof, as before. Different pitches need `mode: each`.
- `groups` — `[{nos, mode: each|group, front: N|S|E|W}]`
- `modifies` — earlier builds this plan may overwrite (explicit list;
  `site_kit.apply` refuses cells owned by builds not listed here)
- optional: `floor_min`, `step_max`, `ledge_stair`, `door_frame`, `keep_edits`,
  `notes`

Write the spec from **this** run's evidence (footprint ways, lidar storeys/roof,
Street View / aerial frontage). Do not copy a peer spec or a per-run build
script.

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
  Name the roof **type** first (`red_clay_tile`, `grey_slate`, `grey_tile`,
  `brown_tile`, `weathered_copper`, `flat_deck`); `roof_type_blocks(<type>)` in
  `lib/builds/roof_material.py` gives the family and alternates (see
  `rules/feature-builds.md`). Cut copper is a type choice from the aerial, never
  a default for a street.
- Find terrace ends first: `.venv/bin/python scripts/aerial_annot.py x0,z0,x1,z1 out.png`.
- Plot angle: `python3 scripts/geo.py slope --bearing …` or `--points x0,z0,x1,z1`
  (snaps via `geo.snap_slope`). Frontage pitch: `geo.py party --bbox …`.
- Pass every earlier build that touched the plots in `modifies`.

If something looks wrong, fix the **spec** (or `house_build.py` if the bug is
shared) and re-apply the same build name. Never stack `_b`/`_c` patch builds.

Verify: readback, `osm_footprints.py check`, walks door to first floor and
door to back garden for every house, contact sheet (photo camera, front 3/4,
rear, top).
