# Non-house building features

Read before any campus / hall / shed / odd-mass building feature (not houses or
terraces). Roof and fabric rules: `rules/craftsmanship.md`. Houses stay on
`rules/terrace-builds.md` + `scripts/house_build.py`.

## Live path

Every non-house building feature apply uses **`scripts/feature_build.py`** with
a JSON spec at `site/builds/<name>.json`. Relative spec arguments resolve against the explicit
`--world` checkout, independent of cwd; absolute paths remain supported. Applies
record builder kind/path in journal metadata. Reapply runs the current recorded
builder with current inputs; it does not reproduce historical blocks exactly.
`python3 scripts/reapply_feature.py --world <world> <name>` validates and dry-runs;
`--apply` owns one lock through validation, undo and rebuilding. Before undo it
checks current feature readiness and the recorded script identity. It requires a new
landed feature entry with zero mismatches before reporting success. The caller
backgrounds the whole live command and checks its final exit status.

Provenance is supported for `feature_build` specs and world-owned `build_*.py`
entry points declaring a literal matching `NAME` and applying through
`site_kit.apply` as the feature pass. Script reapply assumes the same `--world`
and dry-run/default, `--apply` CLI contract; arbitrary command text is never run.
Other entry points remain unknown unless they explicitly supply builder metadata.
Filename discovery is labelled inferred and cannot authorize reapply. Missing or
invalid builders stop before undo; a failed rebuild after undo reports the
remaining undone/partial state and recovery route.

Dry run unless `--apply`; `--apply` refuses a plan that already has a landed build (`element_undo.py` it first, or `--overwrite`). Spec keys
(full list in the `feature_build` module docstring):

- `name` — plan / cells-record name
- `ways` — `{osm_way_id: label}` footprints to build
- `storeys` — `{label: int >= 1}` clear storeys (each 3 blocks high); with
  `zones`, this is the facade storey grid count (openings still use
  `STOREY_H=3` from the median-ground datum)
- `roofs` — `{label: {kind: flat|pitched, material: <slab mat>,
  edge?: {block, where: outer|all},
  zone_materials?: {<level_m_AOD>: <mat>},
  material_regions?: [{level: <m_AOD>, polygon: [[x,z], ...]}]}}`.
  Absent `edge` = no roof
  edging (default). When set, `block` is the parapet/coping mat;
  `where: outer` skips zone steps, `where: all` edges every step.
  Zoned flat decks resolve slab-family material names to their full-block form
  (for example deepslate_tile becomes deepslate_tiles); unzoned flat roofs
  retain slabs. Dry run lists unknown planned IDs with counts and the vendored
  Minecraft 26.2 client-blockstate list used to check them.
  Flat-roof overhang fringe only when `edge` is set (pitched overhang
  unchanged). Optional `zone_materials` overrides `material` per lidar
  plateau (same level keys as `zones`). A pitched roof with `zones` may also
  declare `material_regions` from photo/aerial evidence when a lidar zone cuts
  across a roof. Each row assigns its polygon's roof columns to a declared
  plateau's material, resolved through `zone_materials` and then the default
  `material`. This applies to stairs, slabs and full blocks alike; roof geometry
  and snapped evidence zones are unchanged. `level` is a finite number from
  `zones[label].levels` (for `auto`, an observed plateau). It need not have any
  columns snapped to it when explicitly declared. `polygon` has at least three
  distinct finite `[x,z]` vertices in MC coordinates; the existing even-odd
  rasterizer tests block centres and clips membership to the prepared footprint.
  Regions with no roof columns, unknown levels or overlapping assigned columns
  are rejected. Unassigned columns retain the legacy zone-based material; no
  geometry heuristic guesses roof ownership. Keep adjacent lower decks out of
  the main roof's polygon. Flat roofs do not accept `material_regions`.
  Dry run prints each material region's label, plateau and assigned column count,
  then each zone's mean
  aerial colour and a suggested block from the site_kit colour table
  (suggestion only — not a gate).
  Material choice uses one slope rule (`lib/builds/roof_material.py`,
  catalogue `config/roof-materials.json`). Flat(ish) means rise/run up to
  1 in 2 (`<= 0.5`): any catalogue full block or slab. Pitched means steeper
  than that: the material must be a triad (full block, slab and stairs).
  `kind: flat` is flatish and `kind: pitched` is pitched. The rule gates
  materials only; lidar flattening stays its own detection step. A pitched
  material with no triad keeps the full-block fallback, and the dry run lists
  a warning without rewriting the spec. Names outside the catalogue, including
  wall and floor materials, keep their historical full/slab/stairs names.
  Pick the roof by **type** from what the aerial shows (colour and pattern),
  then write the recommended family into the spec: `roof_type_blocks(<type>)` in
  `lib/builds/roof_material.py` returns the triad (flatish types return the full
  block and slab) and one to three alternates, from the catalogue's `types`
  section. Types are generic, never tied to a place:
  `red_clay_tile` (brick; alt mud_brick), `grey_slate` (deepslate_tile; alt
  deepslate_brick, polished_deepslate), `grey_tile` (stone_brick; alt andesite,
  tuff), `brown_tile` (mud_brick; alt spruce, dark_oak), `weathered_copper`
  (weathered_cut_copper; alt oxidized_cut_copper, cut_copper) and `flat_deck`
  (smooth_stone; alt light_gray_concrete, gray_concrete). Thatch has no vanilla
  triad, so it is not a type; use a flatish block or a near triad and say so.
  The aerial is hazier and lighter than the vanilla textures, so judge pale
  grey against `grey_tile` and dark blue-grey against `grey_slate`.
- `groups` — `[{nos, mode: each|group, front: N|S|E|W}]`
- `modifies` — earlier builds this plan may overwrite (explicit list;
  `site_kit.apply` refuses cells owned by builds not listed here)
- optional: `wall`, `floor`, `floor_min`, `step_max`, `ledge_stair`,
  `door_frame`, `keep_edits`, `notes`. Unzoned floor is the median lidar DTM
  block Y under the footprint; `floor_min` can raise it, never lower it. DTM
  cache is required. Group mode keeps the lowest unzoned floor in that group;
  zoned masses retain local ground and are not flattened to the group floor.
- optional `facade_plan` — per-label plan the designer fills from portals and
  street photos (`lib/builds/facade_plan.py`). Shape:
  `{"SU": {"faces": {"S": {"glazing": [{"storey": 1, "glazed_h": 2, "pier": 1}]}},
  "entrances": [{"id": "57-A", "portal_id": "57-A", "face": "S", "leaves": 1,
  "block": "iron_door", "awning_like": {"kind": "covered_area"}, "steps": 0,
  "floor_offset": 0, "cell": [48, -44],
  "provenance": {"source": "register_entry", "confidence": "high",
  "evidence_ids": [], "unresolved_reason": ""}}]}}`.
  Every portal on the footprint must be placed or listed unresolved
  (`provenance.unresolved_reason` non-empty). Provenance is shared
  (`lib/site/provenance.py`). No register → still add photo-read entrances.
  Build places doors from placed entrances only (unresolved are listed, not
  built); door floor y follows outside ground + `floor_offset` for the transition, capped below the roof so both leaves and lintel fit. Missing outside ground falls back to lidar floor. The live rim never sets shell height. The old
  per-portal `doors` map is refused. Face slots `roof_edge` / `detail` are
  reserved; real roof edge and greeblies stay on `roofs.edge` / `details`.
  Window frames are checked against completed openings at build time.
  `plan_doors` reads each placed entrance's `awning_like` and queues its cover
  until after shell, roof and detail placement. `awning` and `covered_area`
  use roof-material bottom-half slabs at door top + 1, one block wider on each
  side than the actual leaves, projecting outward over the step-out by `depth`
  blocks (default 1 when omitted or 0). `covered_area` uses the same geometry
  with a larger specified depth; it does not carve walls. `porch_roof` adds
  wall-material posts at the two outer front corners, from step-out ground
  to the slab underside. Building-mass columns are skipped; the door approach
  stays walkable. Kind `none` and unresolved/unplaced entrances add no cover.
  Dry run prints `door <id> cover <kind> depth <n>` for each placed cover.
- Windows use `facade_plan[label].faces[face].glazing` only. Give a row list
  for every exposed face, and a row per evidenced storey: `{storey, glazed_h,
  pier, wall?, rhythm?}` (`rhythm` uses `W/B/_`). Copy an evidenced face only
  with a note naming the source. Dry run reports glazed share on every face
  and marks missing rows `no evidence read`. It also counts panes, bounded solid glass
  and omissions by reason (corner pier, incomplete frame, protected entrance, missing
  outside ground or below-ground sill). Doors/glazing cannot terminate an opening.
  Panes remain on coherent straight planes; stepped treads and potential extra pane
  connections use solid glass within the existing shell. Sharp corners stay opaque
  unless that glazing row lists `corner_glazing: [{at: [x,z], provenance: {...}}]`
  with resolved `photo_read`/`aerial` provenance. This explicit exception uses solid
  glass and never permits a missing sill/lintel or replacing door framing.
  The old top-level table is refused.
- optional `details` — per-label roof and facade greeblies from the aerial
  sheet and street photos (`rules/building-review.md`). Each row is
  `{kind, at: [x,y,z], size: [sx,sy,sz], source, face?}`:
  roof kinds `plant_room`, `vent`, `louvred_vent`, `skylight`,
  `solar_panel`, `lift_overrun`, `chimney`; facade kinds `drainpipe`, `sign`,
  `awning_like`, `ledge`, `fascia_band` (`face` required). Entrance
  `awning_like` / covered area belongs on `facade_plan` entrances
  (kinds `none` / `awning` / `covered_area` / `porch_roof`; not a tree
  canopy; legacy key `canopy` and kind value `canopy` are refused).
  Placed after the shell; dry run prints each detail and its source.
  `vent` fills its specified box with gray concrete and caps it with iron
  trapdoors. Only drainpipes use iron bars. `skylight.at` is roof-top Y:
  glass replaces one roof layer and nothing is placed above it. Its entire
  horizontal box must be on roof at that level; any off-roof cell rejects the
  whole skylight and prints label, position and offending cells. Other roof
  detail cells over air are clipped. Each horizontal
  cell needs a full block, slab or stair directly below the row; unsupported
  columns (including caps) are omitted and the dry run reports label, kind,
  position and clipped cell count. Other details do not count as roof support.
  For `chimney`, `at: [cx, base_y, cz]` names the centre block and first
  layer above the roof. Specify `base_diameter`, `top_diameter` (integers >=3
  so a hollow bore fits), `height` (>=1), `shape: round|square`, and optional
  `material` (default white_concrete), `rim: <block>` and
  `band: {offset: <layer>, block: <block>}`. `size` is derived. Tapers use
  stepped one-block rings; the bore is air and the top stays open. Even
  diameters centre half a block toward +X/+Z. The entire base must have solid
  roof support or the stack is skipped. Size stacks from photos and explicit
  spec dimensions; lidar is weak on hollow open stacks. Dry run prints planned
  height/top against lidar as a rough warning, never a dimension override.
  List what reads at street scale, or leave `details` empty / omit it
  when none are visible.
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
  Empty readable zones refuse with the feature label, 3-cell minimum and
  3 m DSM-minus-DTM mass threshold. A single plateau is valid when mass survives.
  Walls rise from **local** lidar ground so downhill faces gain storeys.
  With `roofs[label].kind == "flat"`, each zone is a flat deck. With
  `kind == "pitched"`, within each zone a lidar ridge becomes stair-slab
  slopes from the zone eave up to the ridge; flat plateaus stay flat.
  Lidar heights are only a guide: the builder regularises them so
  neighbouring columns within each roof zone differ by at most one stair
  (2 half-blocks); intentional steps between distinct zone levels stay. It then
  caps a one-wide ridge or 1x1 hip apex with a bottom-half slab, backed by
  a solid block below. A two-wide ridge uses two stairs facing each other
  at `roof_block_y(h)`, including odd half-heights. This lowers an even
  one-wide cap by half a block; an odd two-wide pair peaks half a block
  above the previous slabs. Ridge runs must be rectangular equal-height
  N4 maxima, bounded across their width by lower roof cells in the same
  zone. Missing cells and zone boundaries do not count as lower roof;
  2x2 squares, L/T shapes, runs with higher neighbours at an end, and
  decks three or more cells wide keep their existing states. The same
  cap rules apply to footprint-based Chebyshev hips. The builder then
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
