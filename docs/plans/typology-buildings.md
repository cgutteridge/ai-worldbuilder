# Typology buildings: rule-built streets versus per-building specs (trial #257)

Status: findings from a live trial, 2026-11-22, world `southampton-build`. Plan context:
[vibe-at-scale.md](vibe-at-scale.md) sections 2 and 6. The generator under test is the #413 rule-based spec
generator (`lib/builds/typology_spec.py`, `scripts/typology_spec.py`). No generator or builder code was
changed; every relaxation below lives in a scratch emitter outside the repo.

## Summary

1. **As shipped, the #413 generator built nothing on real streets.** 0 of 18 estate buildings, 0 of 42 on
   the terrace street and 0 of 42 on the parade street produced a spec. Every footprint was refused as
   `odd footprint`: the generator accepts only filled axis-aligned rectangles, and real streets are rotated.
2. **With six relaxations the rules covered all three street types** (14 estate houses, 20 terrace houses,
   15 parade shops built): the output is the same minimal-spec shape the builders already read. The owner
   judged the estate and the terrace "read as the real streets and are good", with two defects that point at
   the builders and the terrace recipe, not at the rules (roof layer, single terrace chimney).
3. **Cost per building falls to about one to two minutes of build time and almost no authoring tokens**,
   against roughly 6k session tokens and a few minutes of authoring per hand-written feature spec (see Cost).
   Build time itself is the same on both paths because the same builders run.
4. **Recommendation:** relax the generator gates (section Recommendation), add the district profile file,
   and turn roof/facade detail (I-006) and glazing into class recipes. Keep a designer for landmarks, odd
   footprints, mixed roof forms and anything on a cell edge.

## Method

- **Study streets** (owner, `site/task-257-typology-trial.md`): estate Begonia Road (cell 49,-48), terrace
  Northcote Road west side (cell 54,-41), shop parade Portswood Road (cell 51,-37).
- **Rule path:** a scratch emitter (`relaxed_emit.py`, session scratchpad) writes the same spec shape as
  `typology_spec._emit` (a `house_build` spec for houses; a `feature_build` spec per shop), then the existing
  builders apply it under the site lock. It reuses the generator's own `load_buildings`, `_front` and street
  code.
- **Hand path:** a full hand-written `feature_build` spec (facade_plan entrances and glazing, roof edge,
  details) for three of the parade shops, built over the rule-built version. For houses the "current path" is
  `house_build` with a hand-written minimal spec, which is the same spec shape the generator emits, so for
  houses the difference between the paths is only who writes the spec.
- **Minutes:** wall-clock from the apply log (start and end stamps of each `--apply` under
  `site_lock.py run`). Authoring minutes are wall time between starting to write the specs and the first clean
  dry run, including reading the schema docs and the aerial.
- **Tokens:** `get_usage` context-window figure read before and after a stage in this one session
  (`tokensUsed`). It is context growth, not billed tokens, and it includes tool output and my own messages, so
  it is an upper bound for a stage and not exact per building.
- Evidence: the local Hampshire vector-map extract (footprints, tags, streets), lidar via the cached tiles, and the
  aerial for each cell (aerial sheet for reading, Hampshire Hub mosaic for coverage).

## What the rules covered

| Street | Class | Real buildings seen | Built by rules | Notes |
| --- | --- | --- | --- | --- |
| Begonia Road | semi-detached houses (tag `house`) | 18 on street, 16 in cell | 14 (6 semis, 2 detached) | 2 dropped for cell edge; no `building:levels` or `roof:shape` tag on any |
| Northcote Road (west) | terrace houses (tag `house`) | 42 both sides, 21 on the west side | 20 | one group, one roof over all 20; 1 dropped for cell edge |
| Portswood Road | shops (`retail`, `shop=*`) | 42 on street | 15 in cell 51,-37 | 4 more are in cell 52,-37, which has no map clip, so apply refused them |

Tag coverage on these streets: `building:levels` present on 0 of 42 terrace houses and 0 of 18 estate
houses, and on 6 of 42 parade buildings. `roof:shape` present on 0 of 42 and 0 of 18, and 1 of 42 on the
parade. The tags the generator needs are mostly absent, so a rule route must default them from the district
profile.

### Where the shipped generator stops

| Gate in `typology_spec.py` | Result on these streets | Relaxation used in the trial |
| --- | --- | --- |
| `_box`: filled axis-aligned rectangle, at least 3 by 3 | 100% refusal. Fill ratios 0.48 to 0.94 on the estate, 0.55 to 0.84 on the terrace, 0.29 to 0.59 on the parade (shops rotated near 45 degrees) | R1: houses any shape with bounding-box fill of at least 0.45; shops any footprint of at least 20 cells (`feature_build` takes the footprint as drawn; `house_build` fits rotated rectangles, #415) |
| `_levels`: needs `building:levels` | refused (tag absent) | R2: default 2 storeys for houses and shops |
| `_lidar_agrees`: lidar within 4 m of levels x 3 | not reached; would also refuse where the cache is missing | R3: skipped |
| `no district profile` | the world has no `site/districts/*.json` | R4: scratch profile (grey deepslate tile roofs, from the aerial: grey tile hipped roofs, a few terracotta) |
| `_relation` and `_mark_partial`: rows need a full shared side | rotated walls never share a full side, so no row would form | R5: footprints within one block of each other form a row |
| `_front`: cardinal facing the nearest street, ambiguous on diagonal frontage | parade is on a diagonal road | R6: nearest street direction even when the axes are close |

Rotated footprints are the main finding: footprint shape, not tags, is what stops the shipped rules. Tags are the
second gate.

## What still needs a designer

- **Mixed roof forms and colours.** The aerial shows hipped grey slate and terracotta tile mixed on the
  estate and in alternating pairs on the terrace; the parade mixes flat and pitched roofs. The rules apply one
  form and one material per district class.
- **Chimneys.** `house_build` plans one chimney per roof unit and has no spec option for it (see Chimneys).
- **Shopfront character.** Rule-built shops are plain brick boxes with flat grey roofs and the builder's
  default entrance and awning. Fascias, signs, roof plant, solar arrays and roof forms came only from the hand
  spec.
- **Buildings on a cell edge.** A building whose eave reaches a neighbouring cell cannot be applied until that
  cell has map and aerial evidence and a fresh validation (see Cell edges).
- **Rooflines of long rows.** A terrace built as one `group` gets one continuous roof (see Terrace).
- **Landmarks and odd footprints,** as the plan already says.

## Cost

Rule path, measured. Apply times are wall-clock from the logs. "Authoring" for the rule path is running the
scratch emitter, which cost one command and a dry run per street (about 1k to 2k tokens each); the emitter
itself was written once.

| Path | Street | Buildings built | Apply minutes | Minutes per building | Tokens per building |
| --- | --- | --- | --- | --- | --- |
| Rule | Begonia Road (west side, 7) | 7 | 25.4 (17.6 min of it in journal follow-ups) | 3.6 | under 1k (batch) |
| Rule | Begonia Road (east side, 7) | 7 | 7.4 | 1.1 | under 1k (batch) |
| Rule | Northcote Road west (one spec) | 20 | 17.8 | 0.9 | under 1k (batch) |
| Rule | Portswood Road shops (15 specs) | 15 | 18.9 for 19 attempted, 4 refused; the first took 4.4 | about 1.0 | under 1k (batch) |
| Hand full spec | parade shop, small hairdresser (w122706815) | 1 | 0.7 build, 0.4 undo | 0.7 | about 6k |
| Hand full spec | parade shop, retail unit (w122706803) | 1 | 1.2 build, 0.3 undo | 1.2 | about 6k |
| Hand full spec | parade shop, convenience store (w122706797) | 0 | undo refused (stale neighbour cell 51,-36) | n/a | about 6k authored, not built |

How these figures were taken:

- Hand tokens: `tokensUsed` went from 277,989 to 296,317 across writing the three hand specs, two dry-run
  fixes and reading one aerial, an increase of 18.3k for three shops, about 6k each. That includes reading the
  `facade_plan` schema and the aerial, a cost shared by all three, so it is an upper bound per shop.
- Whole rule-path stretch: `tokensUsed` grew from 164,009 to 246,938 (83k) from before the first emitter to
  after the estate and terrace were built and the parade was queued. That total includes the download
  approvals, the cell-readiness retries and four refused applies. It is about 1.7k per building over the 49
  buildings built, almost all of it friction, not per-building work.
- Authoring minutes for the hand specs were about 6 minutes of wall time for three (about 2 each).
- Scaling: 20 hand-written shops would cost about 120k tokens and about 40 minutes of authoring before any
  build time. The rule path for the same 20 is one command, then the same build time.
- Not measured: a hand-written full house spec. For houses the hand path is the same `house_build` minimal
  spec the generator emits, so its authoring cost is the cost of writing five to ten lines of JSON per street
  group and checking fronts and materials against the aerial.

### Anomaly worth a ticket of its own

The first estate apply (7 houses) spent 17.6 of its 25.4 minutes in "journal follow-ups"; the second took
7.4 minutes in total, with journal follow-ups at 0.005 s. The terrace, 20 houses, spent 1.1 minutes there. I
did not find the cause; another session was using the same world during the first apply.

## In-game verdicts (owner)

- **Estate, Begonia Road:** "yes, they read as the real streets and are good," except that the roof material
  appears to be applied one layer below the roof (Roof layer).
- **Terrace, Northcote Road:** "looks great," except for one chimney right in the middle of the whole group
  (Chimneys). The owner had not yet commented on the terrace's single continuous roof when this was recorded.
- **Shop parade, Portswood Road:** not yet judged. Rule-built shops are flat-roofed brick boxes; two of them
  were rebuilt from hand specs for comparison (w122706815, w122706803).

## Findings on the defects

### Roof layer

Both defects sit in the builder, not in the generator or the scratch emitter. `house_build.roof()` plans a
"two-layer skin" for every non-eave pitched roof cell: after placing the stair or slab at the roof height
`y`, it also sets a full block of the roof material at `y - 1` (`self.set(x, y - 1, z, family_variants(rm)['full'])`).
Cells beside the eave sit at the wall-top level, so the extra block lands in the top course of the wall. A
live column at x 6347, z -6134 to -6123 of the west semi shows full `deepslate_tiles` blocks at y 63 as well as
y 64, where the wall course should be brick. This fits the owner's "one layer below the roof", but I have not
confirmed that it is the exact spot the owner means.

- Rule-built houses: affected, because they use `house_build`'s pitched roof (`roof_pitch` 0.6 from the
  generator's `PITCHED_RISE`).
- Hand-spec houses: the same, since the current house path is `house_build` too, so a hand spec would not
  avoid it. Only the `roof_fit: octant` route (#415) designs the roof volume separately.
- `feature_build` pitched roofs use lidar zone heights and `pitched_roof_states` through a different path; I
  did not check whether it has the same extra layer.
- Roof height also does not follow lidar in `house_build`: at x 6347, z -6128, lidar surface was Y 66 and the
  built roof Y 64.5. The docstring already says roofs may deviate from lidar.
- Effect on the conclusions: the rules are not at fault, so a roof recipe belongs in the builder. A class
  recipe that only names a material and pitch will inherit the defect until the builder is fixed.

### Chimneys

The central chimney on the terrace comes from `house_build.roof()`. It adds one chimney per roof unit
(`chim.append((best, b))`, the deepest column nearest the unit's centre). The generator marks a terrace
`mode: group`, which means one roof over all houses, so there is one roof unit and one chimney. The scratch
emitter inherited the same grouping. The estate used `mode: each` and got a chimney per house. `house_build`
has no spec option for chimneys.

- What a terrace recipe should do: a stack per party wall or per pair of houses, on the ridge at the wall
  line. That needs one roof per house or per pair, or a joined roof with stacks placed along the ridge at the
  party walls. The Northcote aerial shows roofs per house or pair.
- Hand-spec path: `feature_build` `details` kind `chimney` is only an industrial hollow stack (base and top
  diameter at least 3, a height), so the hand path has no domestic stack either. My hand shop spec with a
  domestic chimney was rejected (`base_diameter must be an integer >= 3`).

### Terrace single roof

`mode: group` joined all 20 houses under one continuous roof; from the street it reads like one long barn,
with a single ridge and no breaks per house. The generator uses `group` for any terrace. A recipe should
join roofs only where the real row does, which the aerial shows as pairs.

### Cell edges

Parts of three streets cross a cell edge, and a building whose eave reaches the next cell is refused until
that cell is prepared. In the trial: two Begonia Road houses at the north edge (cell 49,-49 had 0.0% map
coverage and 54% aerial), one terrace house at the south end, four parade shops in cell 52,-37 (no map clip),
and an undo refused for a shop that straddles 51,-36 ("plan identity changed"). I trimmed the specs to avoid
neighbour cells instead of preparing them. Whole streets cross edges, so a rule route has to plan across
cells.

## Recommendation

1. **Relax the generator.** In order of effect: (a) accept any footprint and let the builder fit it
   (`house_build` fits rotated rectangles, `feature_build` takes the footprint as drawn); (b) default
   `building:levels` from the district profile instead of refusing; (c) treat the lidar check as advisory;
   (d) group rows by adjacency within one block, not by a full shared side; (e) allow a front from the
   nearest street when the axes are close; (f) take rows from a street, not from a connected component.
2. **Write the district profile.** The world had none (`site/districts/` does not exist), so every building
   stopped at "no district profile" before the other gates. #255 is the prerequisite for any rule route.
3. **Terrace recipe:** one roof per house or per pair with a stack per party wall; do not use `mode: group`
   for whole rows; alternate roof colour per pair where the aerial shows it.
4. **Shop recipe:** a class default for a shopfront (ground glazing of height 3, upper storeys of height 2
   with piers), an entrance with awning, and a roof form chosen from the aerial (flat with PV rows, or pitched).
5. **Roof and facade detail (I-006) and glazing become class recipes: yes.** Both are the same few numbers per
   class (glazed height, pier width, rhythm, roof edge) and they are the difference between the plain shop
   boxes and the hand-built unit. Roof detail needs the builder fixes first (roof layer, chimney per stack).
6. **Keep a designer** for landmarks, odd footprints, mixed roof forms and buildings across cell edges.

Implementation tasks from these findings are not filed here; per the task they are filed after the owner has
read this page.

## Scratch effects (world repo `southampton-build`)

- Specs under `site/builds/`: `houses_begonia_road_w207092534.json`, `houses_begonia_road_w207092535.json`,
  `houses_northcote_road_w176446882.json`, and 19 shop specs named by way id; hand specs for w122706803 and
  w122706815 replaced the rule specs in place. Both kinds are kept under `site/builds-hand-trial/`.
- Registry: `plan_feature` notes recorded for every candidate group in cells 49,-48 and 51,-37 (including
  Iris Road, Honeysuckle Road and the parade's other shops) and the cell 54,-41 plans, plus evidence-only
  prepares of 49,-49 and 51,-36.
- Local data fetched, with owner approval: vector-map extracts for cells 54,-41 and 51,-37
  (`reference/vector_map/typology-trial/`, 53,288 and 45,184 bytes, cut from the vector-map file on disk), three
  aerial sheets under `instance/google-aerial/` (2,295,220, 1,716,190 and 2,381,596 bytes) and three
  Hampshire Hub mosaics under `reference/aerial/typology-trial/` (about 1 MB each).
- A first terrace apply aborted because a parallel `site_lock.py run` with the same owner name released the
  lock; use a distinct owner per command when running two at once.
