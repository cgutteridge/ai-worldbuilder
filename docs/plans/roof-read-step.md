# Read step: roof volumes from lidar and the aerial, with trust checks (task #459)

Findings for the owner's review, 2026-11-24. World `southampton-build`. Code: `lib/builds/roof_read.py`,
`lib/builds/designer_ask.py`, `scripts/roof_read.py`, tests `lib/builds/test_roof_read.py`; usage in
`rules/feature-builds.md` ("Read step").

## Dispatch record

Host Claude Code; worker model `claude-opus-5-5` at high effort; model access established by this run (the
orchestrator runs on the same model); budget: the account's 5-hour limit was hit and reset once earlier in
this wave, and no usage failure happened in this run. Build roles (`build_roles.py --world southampton-build
show`): designer `claude-opus-5-5-low`, builder `claude-sonnet-5-5-medium`. The per-building labelling
decision is the designer's: the code makes it through the designer model at low effort, as configured. No
other model family was used.

## The read step in plain words

Inputs: one map way (footprint and tags), the street it faces (from the typology code), lidar surface minus
terrain, the local aerial mosaics and the land-use class row.

1. **Plot frame.** The footprint's long axis, pointing away from the street: `s` runs from the street end
   to the rear, `t` across. Lidar is sampled every 0.5 m along `s` and every metre across, over the plot and
   one plot width either side (the row neighbours), from 1 m in front of the way to 12 m behind it.
2. **Volumes.** Each row is classed: no mass, flat, wall step (steeper than 1 m per metre), falling across
   the plot, or rising/falling along it. Runs become volumes: flat, ridge across the plot (up then down),
   hip (up, a long top, down, with no wall step between), slope along, falling across. Wall-step rows are
   shared between the volumes either side. Mass behind the way counts only when it joins the way (a bin
   store, not a house across the yard). Each volume is fitted with the existing `roof_surface.fit_surface`;
   when no clean shape fits, the profile names the form. Eave and top come from the fit (or percentiles a
   metre inside the mass edge), storeys from the eave by the terrace-body rule, per volume.
3. **One model decision.** The rotated aerial crop (street at the bottom, plot band and numbered volume
   lines drawn) and a five-line lidar table go to the designer model in one call. It answers per volume:
   form, roof type (from the catalogue's types), hidden by trees or shadow, storeys if visible, and bay,
   dormer or none at the front. `--answer` replays a stored answer instead.
4. **Trust checks.** Per volume and attribute, each source is `agree`, `disagree` or `silent` against the
   chosen value; the record names what the value rests on and flags any value resting on one source.
   - Lidar: roughness against the 3x3 median of the volume's own columns (degraded above 0.45 m); a flat
     reading spreading over 1.5 m is not read; green over the volume plus a row disagreement makes smooth
     lidar suspect (the smooth-crown case).
   - Image: leaf green over 25% of the volume, under 0.6 of the local roofs' brightness, or the model's
     "hidden".
   - Row: both neighbours' columns at the same position, only when they agree within 1 m.
   - Tags: `building:levels` and `roof:shape` on the main volume.
   - Front feature: the model against the terrace-body rule (room in the roof gives a dormer, else a bay on
     two storeys or more).
   - Fallback: lidar (height, form), then the image (form, colour; the measured colour class when the model
     is unsure of the roof type), then the row, then the land-use class default.
5. **Outputs.** `read-<way>.json` (volumes, labels, trust, the decision and its usage), the crop, the
   prompt, and a `feature_build` spec proposal using keys that already exist: one `zones` region per volume
   in the way with `surface: "fit"`, `stairs: "slope"`, `finish: "carpet"` when a flat volume keeps its own
   region, `volumes` for a volume beyond the way whose height rests on lidar. A region with under three of
   the builder's roof columns joins its neighbour (the builder refuses an empty region). Output is opt-in
   and goes only under `--out` (refused under `site/builds`).

## Token cost and how it was measured

Every decision went through `designer_ask.ask`: `claude -p` with `--model claude-opus-5-5 --effort low`, no
tools, no MCP servers, a two-sentence system prompt and stream-JSON input carrying the PNG. The CLI's
`result` event reports usage, which the read stores. Figures are that usage, not estimates.

| Building | Input (cache write) | Output (incl. thinking) | Total | List cost | Wall time |
| --- | --- | --- | --- | --- | --- |
| Shop, way 149814018 | 4,684 | 639 | 5,323 | $0.050 | about 30 s for the whole read |
| House, way 166029805 | 4,899 | 173 | 5,072 | $0.043 | similar |
| Under a tree, way 862836598 | 4,498 | 129 | 4,627 | $0.039 | similar |

The CLI's own fixed overhead is about 1.2k tokens (a one-word text call cost 1,231 input tokens). The crop
(about 340 x 540 px) and the table are the rest. My own (worker) session also looked at three aerial crops to
choose the second building; that was test selection, not a labelling decision, and is not counted above.

## Run 1: the shop on the parade (way 149814018, plan w149814013)

Read (designer answer replayed from the recorded call): four volumes, which match the owner's reading.

| Volume | s (m) | Lidar | Eave / top (m) | Model | Storeys | Rests on | Flags |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v1 front | -9.6..-8.1 | flat (profile; too small to fit) | 2.3 / 3.1 | flat | 1 | lidar; colour from the measured colour | row silent (neighbours 3.2 vs 4.3 m); height, storeys on lidar alone |
| v2 main range | -8.1..1.9 | gable, ridge across the plot (fit) | 6.5 / 9.2 | gable_along | 2 | lidar + row agree | image disagrees on ridge direction; front: bay by rule alone |
| v3 rear wing | 1.9..10.9 | falls across (profile; no clean fit) | 5.6 / 7.0 | unsure | 2 | lidar alone | row silent (neighbours 5.7 vs 7.0 m) |
| v4 bin store | 10.9..18.9, beyond the way | flat (fit) | 2.7 / 2.7 | flat, flat_deck | 1 | lidar, image | row silent (no neighbour mass) |

Roughness: v2 0.21 m, v3 0.31 m, v4 0.05 m; v1 has too few columns. No green, no shadow. Roof types: the
model answered `unsure` for v1 to v3 and said why: "Plot splits grey slate left, red tile right". The white
plot band in the crop does straddle a blue-grey roof and a red one, so either the aerial is offset from the
map by about 2 m here or the footprint is: the colour then rests on the measured mean (grey, so `grey_tile`,
`stone_brick`), which agrees with the row. The owner's own reading says the rear wing is red slate and the
neighbour's grey; this read cannot settle that, and says the colour rests on the image alone.

The model's `gable_along` for v2 disagrees with clean lidar and the row (both say the ridge runs across the
plot, parallel to the street); the read keeps lidar and records the disagreement. The model was unsure of
the front feature; the rule gives a bay (rise 2.7 m is under the 3 m attic rise). The owner saw "a bay window
with a dormer-style bit": the rule and the owner half agree.

**Storeys and the #457 note.** The main range reads eave 6.5 m, 2 storeys. `typology_spec` reads 4.2 m (1
storey) for this way. Checked: the axis-aligned `terrace_body.front_strip` cuts 11 columns on a diagonal
(s -9.8 to -6.8) and leaves 3 front-flat columns in the body, but rotating the strip only lifts the body's
15th percentile to 4.9 m. The main cause is that the body sample mixes the rear wing (its own 15th percentile
is 3.5 m) with the main range (6.5 m). Reading eave per volume, as this step does, fixes the storeys; the
typology generator still uses one body per footprint.

Dry run of the proposal (`feature_build.py` on the scratch copy, name `read_149814018`): footprint 105
readable of 113; region 0 flat (rms 0.22 m, 8 of 8 columns), region 1 gable ridge across (0.24 m, 53 of
58), region 2 plane (0.32 m, 31 of 39), the same three shapes as the hand-drawn world spec; stairs slope:
35 kept (rim 16, slope 19), 3 made level blocks; carpet 3 cells, 3 dropped without a solid block below;
bin store `a.v1` 21 columns flat, floor Y 32, roof Y 35, lidar mass 3.0 m (41 polygon columns refused for no
mass); 2,614 cells, no clashes. An earlier proposal of the same read run under the live plan's name gave 8
carpet cells and none dropped; the cause of the difference was not traced.

## Run 2: a detached house under a tree (way 166029805)

Why this building: a different class (residential, `building=house`), not in a row, and the only real
building found where a tree crown covers part of the roof. I scanned 1,620 footprints with aerial and lidar
cover for aerial leaf share and lidar roughness, then looked at the top candidates' aerial crops: on this
house a crown covers the rear right of a hipped grey roof.

| Volume | s (m) | Lidar | Eave / top (m) | Model | Storeys | Rests on | Flags |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v1 front edge | -2.8..-1.8 | flat (profile) | 2.4 / 2.5 | flat | 1 | lidar alone | image green 69%; colour unread (class default) |
| v2 house | -1.8..8.7 | hip (profile; no surface fits a hip) | 5.4 / 7.6 | hip, grey_slate | 2 | lidar and image agree on hip; colour image | tag `roof:shape=gabled` disagrees; front: model none, rule bay (disagree) |
| v3 rear, beyond the way | 8.7..10.2 | flat, but lidar spreads 2.2 to 4.5 m | unread | hidden | class default | class default | lidar uneven; image green 37%; model hidden; not proposed |

Leaf share over the whole roof volume is 17%, under the 25% threshold, and roughness 0.24 m: the crown is
caught on the small rear volume (both sources degraded, so the value falls to the class default and the
volume is left out of the proposal), not on the hip itself. The map tag says gabled; lidar and the image
both say hip. The map way is larger than the house: lidar shows no mass over its street-end 6 m (readable
89 of 172 columns).

Dry run (`read_166029805`): one region (the 1-metre front edge had under three roof columns and joined the
house region, so no carpet); surface fit skipped (a hip is not one of the three shapes) and raw heights kept;
stairs: 21 kept (rim 15, no fitted surface 6); 2,409 cells, no clashes.

## Real canopy case (way 862836598, `building=yes`)

A 37-column building wholly under a large tree crown (aerial leaf share 62% of the footprint, native
roughness 0.55 m). Model call 4,627 tokens: both volumes `hidden`, note "Plot mostly covered by trees and deep
shadow". Lidar reads only 2.2 to 4.0 m of mass (so the crown is not in the surface model here), roughness
0.43 m on v2 (just under the threshold). Every attribute is flagged: height and form rest on lidar alone,
colour unread. Dry run: 4 readable columns, 218 cells. This is the honest result: one source left, and the
read says so.

## Staged damage on the shop's real evidence

The control raised no degraded flag. Each case below changes the real lidar or crop over chosen volumes and
replays the same model answer:

| Case | Caught by | Value now rests on |
| --- | --- | --- |
| A canopy over main range, lidar and image | roughness 0.73 to 0.77, green 65 to 93% | row |
| B canopy over rear wing, lidar only | roughness 0.65 to 1.58 | class default (no row there) |
| C shadow over the street end | luminance 0.37 | colour: class default; height still lidar |
| F faint canopy (+2 m), lidar only | roughness 0.48, uneven flat | row |
| G smooth crown (+4 m), lidar and image | green 51% plus row disagreement | row (form from the row too) |
| H smooth crown (+4 m), rear wing, lidar only | roughness 0.53 | class default |
| E canopy over rear wing, lidar and image | roughness, green 81 to 93% | class default |

All seven were caught. The canopy also reshapes the volumes (A splits the main range into three, B the rear
wing into four): the trust flags fire, but the boundaries under a crown are still lidar's.

## Calibration

Native-grid roughness inside 1,620 footprints: median 0.14 m, 90th percentile 0.27 m, 99th 0.47 m (the
task notes had median 0.13 and 90th 0.21 over 150 houses). Per-volume roughness on clean roofs in these runs:
0.05 to 0.31 m. The 0.45 m threshold is kept from the staged prototype.

## Tests

`python3 scripts/run_tests.py --only test_roof_read`: 25 tests, OK (frame, segmentation including the hip and
the empty-yard case, roughness, trust with staged canopy, shadow, smooth crown, both degraded, model
disagreement, tags, answer parsing, the proposal keys, region joining, a plan through `feature_build.Build`,
and the designer call with a runner double). The full suite runs on each commit.

## Limits and follow-ups

- **Aerial-to-map offset.** On the shop the plot band straddles two roofs; colour per volume then mixes two
  roofs. A registration check (the aerial edge against the footprint edge) would tell the read when its
  colour sample is shifted.
- **Boundaries under a canopy.** When lidar is degraded, the volume breaks are still lidar's. The model's
  zones, or the row's breaks at the same position, could set them instead.
- **Hip roofs** have no fitted surface (`roof_surface` has flat, plane and gable): the hip keeps raw heights.
- **Front feature.** Bay or dormer is recorded per building, but `feature_build` has no bay or dormer key;
  only `house_build`'s `body` can build one.
- **Row check** needs both neighbours at the same position; an end-of-terrace or detached house gets none.
- **Typology integration.** `typology_spec` now calls the read per building for commercial (feature-path)
  rows (#474, `lib/builds/row_read.py`); `--no-read` restores the class and district defaults. Houses are not read.
- **Token use** is dominated by the image; a smaller crop would cut it if the labels hold.
