# Land-use classes: one design for #253, #254, #255, #240, #252, #260

Status: design for owner review, 2026-11-11. Read-only pass: no code changed, no live-world command, no
aerial opened. Parent plan: `docs/plans/vibe-at-scale.md`. Items marked **UNVERIFIED** were not checked.

Owner constraints carried through: anywhere in the UK with other people's providers; vibe over 1:1; few
tokens per cell (classification is code, zero tokens; a district profile is written once); classes now,
refined with experience; an unknown class never invents fences; evidence ladder unchanged; residential
behaviour stays as today, expressed as one row.

## 1. Classes and tag mapping

Eleven classes: `residential`, `commercial`, `industrial`, `civic`, `park`, `rural`, `natural`, `water`,
`transport`, `parking`, `unknown`. Road and path corridors are not land use: they keep their parcel kind
and `highway` value (which #260 reads).

**Building tags** (value of `building`; a building tag is the most specific evidence and always wins):

| Class | `building` values | Other tags on the building |
| --- | --- | --- |
| residential | `coverage.HOMES` (house, semidetached_house, detached, terrace, residential, apartments, bungalow, dormitory), cabin, static_caravan | |
| commercial | retail, commercial, office, supermarket, hotel, kiosk | any `shop`, `office`, `craft`, `tourism`; any `amenity` not in the civic or transport lists |
| industrial | industrial, warehouse, manufacture, service | |
| civic | university, college, school, kindergarten, hospital, church, chapel, cathedral, religious, mosque, temple, synagogue, public, civic, government, fire_station | `amenity` in the civic area list below |
| park | sports_hall, stadium, pavilion, grandstand | |
| rural | farm, farm_auxiliary, barn, cowshed, stable, sty | |
| transport | train_station, transportation, hangar | `amenity=bus_station`, `ferry_terminal` |
| parking | parking | |
| (inherit) | `coverage.OUTBUILDINGS`, `yes`, anything unlisted | see rule below |

A building with no class of its own inherits the class of the smallest *use* polygon containing its
centroid. If there is none: `residential` when it has a house number (this is today's eligibility, see
risk 1), otherwise `unknown`. Other tags on the building are tested before the `building` value, so a
`building=house` with `shop=*` is commercial, as `garden_sources.nonresidential_building` decides today.

**Area tags** (non-building polygons). Two kinds, because the cached campus clip holds 214 `landuse=grass`
polygons that would otherwise beat the campus they sit in:

- *Use* tags say what the land is for. Among use polygons covering a column, the smallest wins.
- *Cover* tags say what grows or lies there. A cover polygon sets the class only where no use polygon
  covers the column. Cover is still read by ground and trees exactly as today.

| Class | Use tags | Cover tags (class only where no use polygon) |
| --- | --- | --- |
| residential | `landuse=residential` | |
| commercial | `landuse=retail`, `commercial`; `amenity=marketplace`, `fuel`; polygons with `shop` or `tourism` | |
| industrial | `landuse=industrial`, `quarry`, `landfill`, `port`, `depot`; `man_made=works`, `wastewater_plant`; `power=plant`, `substation` | |
| civic | `amenity=university`, `school`, `college`, `kindergarten`, `hospital`, `clinic`, `place_of_worship`, `townhall`, `library`, `community_centre`, `fire_station`, `police`, `courthouse`, `grave_yard`; `landuse=education`, `religious`, `cemetery`, `institutional` | |
| park | `leisure=park`, `garden`, `pitch`, `playground`, `track`, `golf_course`, `sports_centre`, `stadium`, `common`, `dog_park`; `landuse=recreation_ground`, `village_green`, `allotments` | `landuse=grass`, `flowerbed` |
| rural | `landuse=farmland`, `farmyard`, `meadow`, `orchard`, `vineyard`, `plant_nursery`, `greenhouse_horticulture`, `animal_keeping` | |
| natural | `leisure=nature_reserve` | `natural=wood`, `scrub`, `heath`, `grassland`, `wetland`, `shrubbery`, `beach`, `sand`, `shingle`, `bare_rock`, `scree`, `mud`; `landuse=forest` |
| water | `natural=water`, `bay`; `landuse=reservoir`, `basin`; `waterway=riverbank`, `dock` | |
| transport | `landuse=railway`; `amenity=bus_station`, `ferry_terminal`; `highway=services`, `rest_area`; any `aeroway` | |
| parking | `amenity=parking` (not `parking=underground`), `motorcycle_parking` | |
| unknown | `landuse=military`, `construction`, `brownfield`, `greenfield` (listed so it is a decision, not an omission) | |

Ground paint (#338): `amenity=parking` is the one land-use polygon that paints a ground surface, a narrow
exception to the #158 rule that the class keys (`landuse=residential`, `retail`, `commercial`, `industrial`,
and the rest of this table) stay out of ground paint. A surface car park (not `parking=multi-storey`,
`underground` or `rooftop`) is tarmac at the car-park priority, and the mosaic aerial does not turn it to grass;
covered parking paints nothing. Every other class remains unread by the ground layer.

One polygon with several keys: first match in the order `amenity`, `leisure`, `landuse`, `natural`, then
the rest. A value not in the table is `unknown` and is counted in the classifier report so new rows are
driven by what trials meet.

## 2. Unmapped land

Every classified thing carries `{class, reason, ref}`. Three ways to be `unknown`:

- `not_checked`: no vector-map clip that asked for `landuse` covers the column. The answer is "fetch",
  never a guess. Buildings still classify from their own tags.
- `unmapped`: checked, no use or cover polygon, and not a plot grown from a classified building. No
  inheritance from neighbours and no majority vote.
- `unlisted`: a tag value the table does not know.

A garden plot grown from a building takes that building's class; this is how residential works where no
`landuse=residential` polygon exists. A residual gap inside a use polygon takes that polygon's class (it is
mapped, just not as a parcel). For `unknown`, observed evidence still builds (mapped barriers, aerial
hedges, lidar or traced outbuildings); nothing is inferred and the table refuses to load an `unknown` row
that sets a boundary, planting or furniture value.

## 3. Defaults table

One file in the tool, `config/land-use-classes.json`, holding the mapping and the defaults together so a
new class is one edit. One pure module, `lib/sources/land_use.py` (tags to class, table load and
validation, precedence in section 4). Row shape:

```json
{"id": "residential",
 "match": {"building": ["house", "..."], "use": {"landuse": ["residential"]}, "cover": {}},
 "frontage": {"inference": true, "requires": ["identified_property", "front_segment"],
              "boundary": {"front": null, "side_front": null, "side_back": null, "rear": null, "perimeter": null},
              "private_surface": "grass", "opening": "door_route"},
 "planting": {"shrubs": {"per_columns": 6},
              "flowers": {"share": 0.08, "kinds": ["poppy", "dandelion"]},
              "cover": {"share": 0.12, "kinds": ["short_grass", "fern"]}, "pond": null},
 "furniture": null}
```

| Column | Values | Meaning |
| --- | --- | --- |
| `frontage.inference` | bool | may labelled inference run for this class at all |
| `frontage.requires` | list | conditions on the property before inference (today's residential pair) |
| `frontage.boundary.<role>` | null, or `{type, height, block?}` | class default per role; null is "no default", never a fence. `perimeter` is the edge of a non-house parcel |
| `frontage.private_surface` | `grass`, `paving`, `keep` | what *guessed* ground in the private extent becomes. Never touches observed or mapped surfaces (#158 unchanged) |
| `frontage.opening` | `door_route`, `street_gate`, `none` | how an entrance gap is reserved |
| `planting` (#265, #266, #339) | null, or `{shrubs, flowers, cover, pond}` | null means no planting inference; no other keys allowed |
| `planting.shrubs` | null, or `{per_columns: integer >= 1}` | target one shrub per this many guessed-lawn columns of the plot |
| `planting.flowers` | null, or `{share, kinds}` | `0 < share <= 0.2`; `kinds` is a list of 1-2 names from `foliage.REFINE_PLANTS` |
| `planting.cover` | null, or `{share, kinds}` | `0 < share <= 0.3`; `kinds` is a list of 1-3 different names from `foliage.REFINE_PLANTS` (#339) |
| `planting.pond` | null | reserved until #252 decides a rule |
| `furniture` (#260) | null, or `{bench_per_100m}` | null means none inferred |

The file also has a top-level `highways` map (lamp spacing per `highway` value) owned by #260.

Only residential ships planting rules: shrubs `per_columns: 6`, flowers `share: 0.08` with
`[poppy, dandelion]`, cover `share: 0.12` with `[short_grass, fern]`. These are low-confidence starting guesses for the owner to tune. Other rows,
including `unknown` and every class that allows no inference, keep `planting: null`.
`land_use.planting_rule(class_id, kind)` reads the rule or returns `None`; there is no residential
branch in the ground planner. `classify(guessed_out=...)` exposes only guessed lawn that remains grass
after paving conversions and denoising, without changing its existing return value.

Shrubs (#265) run in the ground pass on guessed-lawn `grass_block` columns within one column (diagonals
included, `SHRUB_EDGE_REACH_SQ`) of the plot's house wall or any boundary segment (#339), so open lawn
stays bare. They are ordered by distance to the house wall, then the boundary, with `(x,z)` ties. The
target is `floor(columns/per_columns)`; the edge limit and clearance can reduce it. Each shrub is one persistent oak leaf at ground Y + 1; every third shrub
gets a second leaf at Y + 2. Footprints, entrance routes, openings, tree/decor mask columns and
neighbour-owned columns stay clear; shrubs leave a one-column gap from barriers and other shrubs.
The ground report records each placement as `shrub inference (#265)`, source `inference`, confidence
`low`. Existing foliage strip helpers remove the leaves on redo.

Flowers (#266) reuse those rules and guessed columns. Beds use `grass_block` within two columns of
the house wall or a front/side boundary; routes, openings, footprints, tree/decor mask columns and
shrub columns are excluded. Shrubs are planned first. Each plot targets `round(share * guessed lawn)`
flowers, capped by eligible space, with a seeded ranking keyed by plot id and `(x,z)`. A separate
plot seed picks one of the rule's kinds for the whole plot. Flowers sit at ground Y + 1 and carry
`flower inference (#266)`, source `inference`, confidence `low`, in the same per-plot ground report.

Cover (#339) varies the edge band with ground plants. After shrubs and flowers, each plot targets
`round(share * guessed lawn)` single-block plants from the rule's `kinds` (residential: short grass tufts and
ferns, picked per column from a seeded rng) within two columns of the house wall or a boundary
(`COVER_EDGE_REACH_SQ`), on columns that are still air: shrub, flower, route, opening, footprint and mask columns
are excluded. Each is labelled `cover inference (#339)`, source `inference`, confidence `low`, and the foliage
strip helpers remove it on redo.
The ground pass preserves a matching flower on rerun; the existing foliage strip removes it on redo.

Shipped values. Residential is exactly today: inference allowed with the two conditions, every boundary
null (a missing convention never authorises a fence), guessed lawn kept, door route. Commercial,
industrial, civic, transport and parking: `private_surface: paving` (today's forecourt rule for every
non-house), boundaries null. Park, rural, natural, water: `private_surface: keep`, boundaries null; rural,
natural, water and unknown have `inference: false`. **No class ships with a fence default**: a UK-wide
fence kind per class is not evidenced. Boundary kinds arrive from a district profile (area evidence) or,
later, from trial findings (#258, #259) edited into the class row.

Adding a row: append the object; `test_land_use` fails if a tag value maps to two classes, a required
column is missing, or `unknown` infers anything. A world never overrides the table; it uses a profile.

## 4. District key and precedence

A district is a named area listing its cells, one file per district in the world:
`site/districts/<slug>.json` with `name`, `cells` (list of `[tx, tz]` or inclusive rectangles), `evidence_ids`,
and sections keyed by class id with the same shape as the table's `frontage`, `planting`, `furniture`,
plus `palette`, `roofs`, `storeys` (stored and validated, read by nobody until #257). A cell belongs to at
most one district; the loader refuses overlaps. Not a vector-map place polygon: `place` is not fetched, it would
tie the key to one map vendor, and UK suburbs are usually mapped as points, not areas (**UNVERIFIED**,
general knowledge). A cell straddling two real districts goes to one; accepted for vibe.

Precedence is resolved per field (for boundaries, per role) by one function, `land_use.resolve`. The first
layer that *speaks* wins; `none` is speaking (an explicit open frontage) and stops the search:

1. per-house override (`overrides.<number>` in `site/gardens/<street>.json`)
2. street convention (same file)
3. district profile, section for the parcel's class
4. class default

Observed evidence (mapped barrier, aerial hedge unless vetoed, designer trace, mapped furniture point) is
above all four, as today. `resolve` returns the value and the layer; the layer goes into the provenance
scope (`property`, `street`, `district`, `class`) so reports show why. Layers 3 and 4 are labelled
inference and only run when the class row allows inference and its `requires` hold. Which
`provenance.SOURCES` value a district-derived item uses is **UNVERIFIED** (the list was not read).

## 5. Do landuse polygons reach each cell?

The task premise ("osm_cell_extract reads leisure only") is wrong. Verified by reading code and the cached
clips of `southampton-build`:

- `CELL_EXTRACT_LAYERS` already includes `landuse`. Its preset is provider-owned
  (`landuse IS NOT NULL`, in both world providers and the `geofabrik_provider` scaffold).
- Cells prepared with the standard step have it: the `prepared-53--42`, `53--44` and `54--42` clips hold
  `landuse=residential`, `allotments`, `leisure=park`. Older clips do not: cell 53,-43
  (`highfield-cell-53-43`) is `buildings+highways` only, as are `oldtown` and `portswood-x-broadlands`.
- `OSMIndex.areas` keeps every non-building polygon, so `ParcelBuilder` sees them. `ground_sources.load_osm`
  keeps a polygon only when one of its *vertices* is inside the cell box, so a large landuse polygon can be
  dropped on the ground and decor path (read, not run). The Southampton provider extracts with `-spat`
  (whole polygons, far vertices); the scaffold adds `-clipsrc`.
- Not fetched by the standard step: `amenity` polygons (parking, school, university, hospital, place of
  worship), `natural` other than wood, scrub and water, `aeroway`. `ground_sources` reads
  `amenity=parking` and `natural=wetland/grassland/shrubbery`, which today arrive only from ad hoc extracts.

Changes (#253): (a) move `landuse` into the tool-owned presets so a third-party provider cannot lack it;
(b) add a tool-owned preset `landclass` (multipolygons: the `amenity` and `natural` values of section 1)
to `CELL_EXTRACT_LAYERS`; (c) `OSMIndex` records per-layer checked extents from clip names, as
`_query_bbox` does for buildings, giving `not_checked`; (d) the classifier reads `OSMIndex.areas`, never
`load_osm`. Keys held only in `other_tags` (`tourism`, `shop`, `aeroway`, `power`) are used when a polygon
arrives for another reason and are not fetched for themselves. Whether other providers' GDAL schema
exposes `amenity` and `natural` as columns is **UNVERIFIED** (see #237). World work, separate: re-extract
old cells.

## 6. ParcelBuilder and the classifier

No new parcel map. `land_use.py` is pure: tags in, class out. Rasterising stays where it is:

- `ParcelBuilder.areas` already walks polygons smallest first and sets umbrellas aside. That loop becomes
  one function, `coverage.area_classes(idx, bbox)`, returning `{column: (class, ref)}` under the use and
  cover rule; `ParcelBuilder.areas` calls it. `UMBRELLA` is kept for parcel drawing (an umbrella is still
  not a parcel) but its polygons now give gaps and buildings a class.
- Every saved parcel gains `meta.land_use`. `foliage` and `cross_zone` already read parcels with
  `load_area`, so #252 gets classes with no new reader.
- Frontage keeps its own plots: `garden_sources.plots_for` sets `info[i]['land_use']` from the building
  tags plus `area_classes`, replacing the `nonresidential` flag.

Found, not fixed here: two plot growers already exist (`ParcelBuilder.gardens`, reach 45, corridor bands;
`plots_for`, margin 60, road width plus pavement). They can disagree at plot edges. Unifying them is its
own task; this design adds no third.

## 7. Order of work and task-note edits

1. **#253** now, in parallel with #240 (no shared files except the rules doc).
2. **#240** finishes three code slices and stops: replace the lossy `(type, block, height)` garden tuple
   with items that carry provenance (first, #254 needs the layer to survive); opening when the front
   boundary is an aerial hedge; bound the `entrance_approach` scan by footprint. Headroom is world review.
3. **#254**, then **#255**, then **#260**; **#252** measurement any time, its code after #254.

Edits to make in `tasks/index.json` (orchestrator, on `main`; not made by this pass):

- **#253**: replace "Unverified: whether osm_cell_extract delivers landuse polygons per cell (it reads
  leisure only)" with the findings of section 5. Replace the class list with the eleven of section 1 and
  the use and cover rule. Add: table and mapping in `config/land-use-classes.json`, module
  `lib/sources/land_use.py`, `coverage.area_classes` shared with `ParcelBuilder.areas`, `meta.land_use` on
  parcels, presets (a) and (b), checked extents. Add file `lib/sources/osm_footprints.py`. Add: no class
  ships a fence default.
- **#254**: add: generalise `residential_frontage` to `frontage(meta, ..., row)` with the old name kept as
  a wrapper; `nonresidential` replaced by the class; segments for non-house parcels with role
  `perimeter`; ground's forecourt rule reads `private_surface`; deliver `land_use.resolve` with layers 1,
  2 and 4 (3 is empty until #255). State the two behaviour changes of risks 1 and 2.
- **#255**: replace "a named area listing its cells or a vector-map place polygon; state which" with "a named
  area listing its cells, `site/districts/<slug>.json`, one district per cell". Drop "Unverified: whether
  vector-map place polygons exist". The loader plugs into `land_use.resolve` as layer 3.
- **#240**: add the stop line of step 2; "trees in routing" closes as documented behaviour (trees are not
  moved; rule already says so); further generality is #254.
- **#252**: add 254 to `blockedBy` (both edit the ground layer); rules live in the table's `planting`
  section and read the class from `meta.land_use`.
- **#260**: spacing comes from `land_use.resolve` (profile, else the table's `highways` map); benches only
  where the adjoining class row has a `furniture` section (ships for `park` only).

## 8. Tests (synthetic inputs only, `python3 scripts/run_tests.py --only <stem>`)

- **#253** `test_land_use`: one building and one area per class; unlisted value is `unknown/unlisted`;
  `building=house` inside `landuse=retail` is residential; `building=yes` inside retail is commercial;
  two nested use polygons, smallest wins; `landuse=grass` inside `amenity=university` is civic; grass alone
  is park; no landuse clip is `not_checked`; checked and empty is `unmapped`; a large polygon with no
  vertex in the cell still classifies; table validation (duplicate tag, missing column, `unknown` with a
  boundary refused). `test_coverage`: parcels carry `meta.land_use`, parcel ids and digests unchanged.
  `test_osm_cell_extract`: `landuse` and `landclass` presets installed into an empty provider.
- **#254**: existing residential tests pass unedited; a strip per class plus unknown; class default with a
  synthetic fence row builds, labelled `class`; the same row under `unknown` is refused; `front: none`
  beats a class default; forecourt paving for commercial, guessed lawn kept for park; perimeter segment
  with an opening on the street side; outbuilding on a non-house strip still places.
- **#255**: profile fills a role the street convention is silent on; street convention beats profile;
  override beats both; observed barrier beats all; missing section infers nothing; class absent from the
  profile falls to the class default; overlapping districts refused; palette sections validated, unread.
- **#240**: gap survives final joins; aerial-hedge front gets an opening; provenance survives from garden
  item to decor report; scan stays inside the footprint.
- **#252**: shrub, flower and pond counts per plot for residential; a park parcel uses its own row; a
  class with `planting: null` gets none; route and opening stay clear.
- **#260**: lamp spacing per highway class; profile spacing beats the table; mapped point wins; nothing on
  routes, openings or carriageway; benches only beside `park`; every inferred item labelled.

## 9. Risks

1. **Residential drift.** Today a `building=yes` with a house number is frontage-eligible wherever it
   stands. Under the smallest-polygon rule the same building inside `landuse=retail` becomes commercial.
   This follows the owner's rule but is a change; the #254 test pins it and the owner should confirm.
2. **Forecourt paving.** Guessed lawn between road and building stops becoming paving in park, rural and
   natural land. Intended, but visible in rebuilt cells.
3. **Map quality varies across the UK.** Sparse landuse mapping gives large `unmapped` areas, which build
   observed evidence only. That is the designed degradation, but trials should report the unknown share.
4. **Cell-granular districts** misplace streets on a district edge.
5. **Old clips** silently read as `not_checked` until re-extracted; `status` should say so
   (**UNVERIFIED** whether cell readiness can surface it today).
6. **Coarse classes.** Allotments under park, cemeteries under civic, caravan sites under commercial are
   first guesses; the unlisted and per-class counts in the classifier report are how they get refined.
7. **Coverage areas.** `southampton-build` has no `config/coverage-areas.json` in its checkout, so
   `meta.land_use` on saved parcels has no consumer there until an area is built; frontage does not
   depend on it.

Not verified in this pass: anything by running code (all findings are from reading source and cached
clips); clips of worlds other than `southampton-build`; the remaining #240 slices against current code
beyond the task notes; `trees_layer.py` and `foliage.py` internals for #252.
