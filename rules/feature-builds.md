# Non-house building features

Read before any hall, shed or odd-mass building feature (not houses or
terraces). Roof and fabric rules: `rules/craftsmanship.md`. Houses stay on
`rules/house-builds.md` (the house build path).

## Live path

Every non-house building feature apply uses **`scripts/feature_build.py`** with
a JSON spec at `site/builds/<name>.json`. Relative spec arguments resolve against the explicit
`--world` checkout, independent of cwd; absolute paths remain supported. Applies
record builder kind/path in journal metadata. Reapply runs the current recorded
builder with current inputs; it does not reproduce historical blocks exactly.
`python3 scripts/reapply_feature.py --world <world> <name>` validates and dry-runs;
`--apply` owns one lock through validation, undo and rebuilding. Before undo it
checks current feature readiness and the recorded script identity. It requires a new
landed feature entry with zero mismatches before reporting success. Execution follows `rules/slow-commands.md#execution`.

Provenance is supported for `feature_build` specs and world-owned `build_*.py`
entry points declaring a literal matching `NAME` and applying through
`site_kit.apply` as the feature pass. Script reapply assumes the same `--world`
and dry-run/default, `--apply` CLI contract; arbitrary command text is never run.
Other entry points remain unknown unless they explicitly supply builder metadata.
Filename discovery is labelled inferred and cannot authorize reapply. Missing or
invalid builders stop before undo; a failed rebuild after undo reports the
remaining undone/partial state and recovery route.

Dry run unless `--apply`; `--apply` refuses a plan that already has a landed build (`element_undo.py` it first, or `--overwrite`).
This file is the spec reference; `lib/builds/feature_spec.py` validates these keys:

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
  `red_clay_tile` (cinnabar_brick, chosen by look; alt cut_copper, brick, mud_brick), `grey_slate`
  (tuff_brick, chosen by look; alt deepslate_tile, deepslate_brick,
  polished_deepslate), `grey_tile` (stone_brick; alt andesite,
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
- optional `signs` — per-label shopfront / street-face sign options
  (`lib/builds/sign_options.py`, catalogue `config/sign-options.json`).
  Shape: `{"A": {"wood": "dark_oak", "fascia_colour": "black",
  "text_colour": "white"}}`. `wood` is a wall-sign / hanging-sign family
  (default `oak`). `fascia_colour` overrides the hashed concrete band
  (default unchanged; any catalogue dye including black, gray and cyan).
  `text_colour` is a dye judged against the **board** map colour, not the
  fascia; omit it to pick the better of black and white. A low-contrast
  wood/text pair is refused at load (same catalogue gate style as roof
  materials). Class recipes and world data supply palette values (#431);
  this key only accepts them. Both the shop and big-box name sign paths
  honour the same keys. Example:

  ```json
  "signs": {
    "A": {
      "wood": "dark_oak",
      "fascia_colour": "black",
      "text_colour": "white"
    }
  }
  ```

- optional `details` — per-label roof and facade greeblies from the aerial
  sheet and street photos (`rules/building-review.md`). Each row is
  `{kind, at: [x,y,z], size: [sx,sy,sz], source, face?}`:
  roof kinds `plant_room`, `vent`, `louvred_vent`, `skylight`,
  `solar_panel`, `lift_overrun`, `chimney`; facade kinds `drainpipe`, `sign`,
  `awning_like`, `ledge`, `fascia_band` (`face` required). Optional
  `wood` on `sign` (wall-sign block; the label's `signs.wood` when the
  row omits it) and `fascia_colour` on `fascia_band` (concrete instead of
  terracotta; the label's `signs.fascia_colour` when the row omits it).
  A `text_colour` on a detail `sign` is refused when it clashes with that
  board. Name lettering stays on the street-face sign. Entrance
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
  Example: `"details": {"main": [{"kind": "plant_room", "at": [12, 59, 22],
  "size": [3, 2, 2], "source": "aerial NW plant box"}]}`
- optional `exclude` — per-label boxes cut out of that label's map footprint,
  `{"wing": [[x0, z0, x1, z1], ...]}` (inclusive block coords). Use when the
  vector outline is larger than the real building (lidar shows open ground, or
  separate houses, inside it). Clips only the named label; clipped cells are
  not treated as another building's footprint. Dry run still prints the
  footprint counts, so check `cover` after clipping.
- optional `zones` — per-label lidar roof plateaus **within one footprint**
  (one vector way, several roof heights). Use when a single `storeys`
  count cannot express that. Example:

  ```json
  "zones": {
    "main": {
      "from": "lidar",
      "levels": [41, 45, 46, 47, 48, 50],
      "bucket": 4,
      "axis_deg": "auto"
    }
  }
  ```

  Optional `zones[label].regions`: `[[[x, z], ...], ...]`, polygons in MC block
  coordinates (at least three distinct finite vertices each; a column is inside when
  its centre is, even-odd). The image says where a roof zone is (a hipped front, a flat
  rear extension) and lidar says how high: each region's level is the 20th percentile of
  the median DSM inside it, rounded to whole metres, so a ridge still rises from that
  eave and a flat plateau stays flat (`roofs[label].flat_tol_m` sets the rise below which
  a zone stays flat; default 1.5, lower it for a low hip). Columns outside every region
  keep their lidar zone. An empty polygon (no readable columns) or overlapping polygons
  refuse. `material_regions[].level` must name a declared or auto plateau. With `levels: auto` plus `regions`, auto plateaus no region-resolved zone uses are left out of the
  ridge clip (the rise is limited to the gap to the next used level) so they cannot flatten a ridge; `material_regions`
  may still name them.

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
  differences on the diagonal). `python3 scripts/roof_lint.py --world <world>` checks
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
(`rules/build-roles.md`, `python3 scripts/build_roles.py --world <world> show`). The JSON spec
is the hand-off when designer and builder differ; the builder applies with
`feature_build.py` and does not redesign massing from scratch.

Acceptance check: a multi-plateau spec dry-runs end-to-end via `feature_build`
(no `--apply`). Add a `zones` entry as above and confirm 0 guard failures on
dry-run. Keep the fixture in tests or in the world repo, not as a named place
in this rule.

**Lidar ghosts (ground only):** After `cell_marker --raise`, frontier cells show
ghost ground with DSM−DTM top colours (not volumetric grey columns). Real features
overwrite ghost ground where they sit (ghost owners yield). Use neighbouring
coloured tops to judge whether the build sits on the right footprint. Before
handoff, `python3 shims/lidar_ghost.py status --cell <home> --ring 2` should show
ghost ground applied on frontier neighbours that still lack real ground.

Greeblies and path-in-footprint are separate system tickets. After apply, run
`python3 scripts/evidence_sheet.py --world <world> <name>` (portal-facing cameras, top-down,
aerial crop), and record the sheet path in the build review
(`rules/frontage-and-evidence.md` §4). This is review guidance, without an automated handoff gate.

If something looks wrong, fix the **spec** (or `feature_build.py` if the bug
is shared) and re-apply the same build name. Never stack `_b`/`_c` patch builds.

## Railway line features

A through railway is a named feature, `railway_<tx>_<tz>` (negative cell coordinates
use the usual `m` prefix), containing its rails, ballast, formation, side slopes
and low planting. It uses the ordinary feature journal, backup, ownership and
undo paths. Service/decor lines remain outside this mechanism; junctions between
through lines are laid by rule (below) and finished by a person.

The per-cell proposal and preparation paths check the smallest complete railway
cell extract first. They return immediately when it has no through line or when
a landed railway box already covers the whole cell. A partially covered boundary
cell can start the next box. Planning uses whole, unclipped ways for a 2048 by
2048 box centred on the starting cell, the explicit point and road extracts,
cached lidar terrain/surface arrays and applicable joins from earlier features.
There are no implicit downloads or live planning reads.

Create a spec from those explicit local inputs:

```sh
python3 scripts/railway_build.py --world WORLD --cell TX,TZ \
  --railway-extract PATH --points-extract PATH --roads-extract PATH
```

This writes `site/builds/railway_<tx>_<tz>.json` and refuses to overwrite an
existing spec. Then review/dry-run the saved spec with
`python3 scripts/railway_build.py --world WORLD PATH`. The same command with
`--apply` uses the normal site lock and readiness gates. A planned failure,
invalid saved geometry or conflicting line plans refuses before block writes.

The schema and public adapters live in `lib/builds/railway_feature.py`:
`kind: railway`, `name`, starting `cell`, `plans` containing JSON-able
`railway_plan.plan_line` results, optional earlier `exclude_boxes` and `modifies`.
Each plan records rails, torch supports, torches, corridor columns, surfaces,
planting, continuous profiles, gaps and joins; `clear_to` retains the surface
clearance evidence, capped below overhead decks. The registry verifies the
saved spec rather than merely checking that its file exists.

Every straight or ascending rail is powered with a torch two blocks below it;
corners remain ordinary rails. The block immediately below a powered rail must
not fall into its torch cavity: use hidden stone where the formation is gravel
or absent, retain a compatible nonfalling road carrier, and keep the surrounding
ballast gravel. `railway_feature.place_rails` is the shared placement seam.

Railway metadata records the boxes, line ids, gap end records and outgoing joins
on the feature's build entry. A join records rail Y, direction and track index;
box cuts also record the next cell, constituent track way ids and step-spacing
history. Continuations match those members and the directed boundary segment,
because an extract can add member ways and change the aggregate line id or track
ordering. The first uncovered rail must retain the recorded boundary level. The line leaves
crossing/bridge/tunnel gaps empty for their own features. A road/path bridge over
the line is overhead evidence, not a railway gap; its deck is preserved and does
not become planting. Deck heights come from `lib/sources/structure_evidence.py`
(`measured`, `inferred` or `unknown`; never the terrain beneath or tree columns
over a deck); see `docs/plans/bridges-and-tunnels.md`. The planner takes them as
`structures=` (`structure_evidence.by_osm_id`; `railway_build.py` builds them from the
road and rail extracts). A measured underside replaces the lidar surface as the
clearance ceiling. An unknown height, or an inferred one while
`railway_plan.INFERRED_DECK_USABLE` is false, becomes an `overhead` gap (end records,
`where` and `reason` for a human) and the rest of the corridor still plans. A usable
height under 5 blocks of clearance still fails.

**Junctions (#452).** Where line plans in one feature share rail columns, the planner
(`railway_feature.plan_feature`, using `railway_plan.junction_gaps`) keeps the main
line's rails: main is the plan with more tracks, then more rail columns, then the
lowest `line_id`. Every other claimant of a column loses it, including a column that
three lines share (the best ranked of them keeps it). Each side track's lost rail
columns, plus any surviving rail run shorter than `railway_plan.JUNCTION_MIN_RUN`
columns that has nothing to connect to (junction gaps or a dead end on both sides;
runs beside a box join or a structure gap are kept), become a `junction` gap: `source`
is the main line's id (joined with `+` if several), `where` is its first column, and
`ends` carry rail Y, direction and track index like every other gap. The side line is
re-planned with these gaps, so its profile, ground and planting already leave them
empty. Columns that corridors write differently go to one owner (`share_footprints`):
the plan with a rail there, else the main line. Fails loudly, with coordinates, when
an overlap covers a box join or a line lies wholly inside the others. `build()` keeps
refusing hand-made specs whose plans still share a rail column or write a shared column
differently. Metadata lists each junction gap under `junctions` (side line, main line,
`where`, `last`, ends) so a person can find and finish it.

The feature reserves every written footprint column from later ground, trees
and decor, including cuts below the old terrain. Ground must not refill above
buried rails; trees and decor must not place into that footprint. These
reservations and coverage marks are derived from active landed journal entries,
so ordinary whole-feature undo clears them while retaining the planned feature.

Applies request sparse structure-backup tiles, each at most 128 blocks per edge,
without changing legacy backup defaults for other builders. Undo checks the
recorded prior blocks against all tiles; interrupted tiled rollback restores only
the journalled positions, preserving unrelated blocks inside a tile. Readback
rescans the plan and retries only mismatches, at most twice within the same begun
entry and original backup. A persistent miss fails and leaves the entry available
for normal recovery. `element_undo.py` and recorded-builder `reapply_feature.py`
use the same feature paths.
