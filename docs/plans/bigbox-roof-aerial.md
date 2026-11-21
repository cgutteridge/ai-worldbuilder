# #337 spike: does the aerial separate PV rows from a light flat roof? (2026-11-21)

Source: [industrial-commercial.md](industrial-commercial.md) section 9 item 13 (trial #258). Owner fault: Westquay
South's roof shows long PV rows on a flat light roof; the trial prototype's central grid of detectors did not
match.

## Verdict: go

At the trial aerial's resolution the PV arrays, and the panel rows inside them, are clearly separable from the
roof membrane and the plant. Phase 2 (roof colour and PV layout from the aerial, laid as `daylight_detector`
rows) went ahead.

## Evidence

- **Aerial.** The trial's look-only z19 mosaic for 43,-16 (`mosaic-z19-4x4-260093-175744.png`, section 2 of the
  source plan). Registered to the world frame by a similarity fit over the footprint corners: 5.31 px a block
  (0.19 m/px), rotation 0.46 degrees.
- **By eye.** The crop shows six rectangular arrays on a pale grey-blue roof: NW (grey-blue), two dark blue
  halves in the centre split by a walkway, E, a small NE block and SW. Plant fills the south-east quarter. Inside
  each array the panel grid resolves: about 22 rows over 32 blocks in the SW array (a pitch of about 7.5 px, or
  1.4 m).
- **Colour alone does not separate them.** Blue minus red is 74 on the dark centre panels but only 43-50 on the
  grey-blue NW and SW arrays. The pale north roof reaches 43, and some plant reaches 36.
- **Periodicity does.** A ~4-block luminance window correlated with itself shifted by one pitch (1.0-2.2 m)
  scores 0.64-0.89 on both axes in every array. Plant pipework mostly scores high on one axis only (about 0.3
  on the other); one grey plant patch scored about 0.6 on both but fails the colour test (blue minus red 22).
  Bare roof and edges score at most 0.43 on either axis. Rule: a PV column is periodic on both axes (>= 0.5)
  and blue (blue minus red >= 30).
- **Result on Westquay South** (`w465660355`, label WS, 5,862 roof columns): six arrays, no false positives,
  boxes within about a block of the panel edges after regrowing the window-eroded edges; PV covers 36 % of the
  roof. Measured row pitch 1.6-2.2 m. Roof membrane colour (the modal non-PV colour, so plant and shadow do not
  darken it) is rgb 154,167,197, which reads as `smooth_stone`, the material the spec already uses.

| Array (x0,z0..x1,z1) | Rows along | Row pitch | Periodicity |
| --- | --- | ---: | ---: |
| 5556,-2020..5567,-2009 (NE) | x | 1.6 m | 0.75 |
| 5550,-2006..5565,-1983 (E) | x | 1.6 m | 0.81 |
| 5503,-2004..5518,-1980 (NW) | z | 1.6 m | 0.79 |
| 5521,-1998..5531,-1971 (centre W) | z | 2.2 m | 0.87 |
| 5533,-1997..5543,-1970 (centre E) | z | 2.2 m | 0.86 |
| 5499,-1970..5517,-1940 (SW) | z | 1.6 m | 0.80 |

- **Resolution limit.** The same mosaic downsampled: clean (the same six arrays) at 3.54 and 2.65 px a block
  (0.28 and 0.38 m/px, about z18). From 2.41 px a block (0.41 m/px) arrays start to split, and at 1.33 (0.75
  m/px) they merge. The deriver refuses aerials coarser than 2.5 px a block rather than guess.

## Limits

- Only axis-aligned arrays (boxes on the block grid). The registration handles rotated imagery, but a PV
  array rotated against the block grid becomes its bounding box.
- Arrays whose panels show no gaps between panels in a row (stripes one way only) are not found; add their
  boxes by hand.
- Minecraft rows are one block on, one off (`pitch` 2) by default; the real 1.4-2.2 m pitch cannot be drawn
  exactly, so the rows read as rows but are fewer than the real ones.
- Tall roofs lean in 3D-tile aerials. At Westquay (30 m) the roof sits within a block or two of the mapped
  footprint, so no `--shift` was needed.
- The aerial is look-only. Only the derived boxes and material go into the world spec; the crops stay
  in the look-only provider's gitignored working folder under the world's `instance/`.

## Phase 2 deliverable

- `scripts/roof_aerial.py derive` (library `lib/evidence/roof_aerial.py`): roof material and `pv_arrays` from any
  registered aerial crop (`--registration`, or `--mosaic Z,TX0,TY0` for a Web Mercator mosaic), with a review
  sheet (`--sheet`) and `--write` into `roofs[label]` of a feature spec.
- `feature_build`: `roofs[label].pv_arrays` (flat roofs) lays `daylight_detector` rows on the finished deck,
  one row every `pitch` blocks across the rows, on a full roof block, never over a detail.
- Check: top-down capture of Westquay South in 43,-16 beside the aerial; owner verdict.

## Live run on Westquay South (2026-11-21, world `southampton-build`)

All writes ran under `site_lock.py run t337`, with the orchestrator's go for each step.

| Seq | Cell | What |
| --- | --- | --- |
| 1686 | 42,-16 | `prepare --evidence-only --apply`: readiness_evidence note; evidence 9b3fb2e6… -> 4af43887… (was seq 1368); no plans, no blocks |
| 1687 | 42,-16 | full `prepare --apply` (owner approved): readiness note, plan list identical to seq 1373; no blocks |
| 1692 | 43,-16 | full `prepare --apply`: readiness note, plan list identical to seq 1367; evidence -> 1ff9ec17…; offered ground redo not run |
| 1693-1694 | 42,-16; 43,-16 | `feature_build site/builds/w465660355.json --apply --overwrite`: 1,050 blocks changed |

- The re-apply's guard held: the old and new plans were identical in 42,-16. The PV boxes were clipped to x >= 5504,
  so the NW and SW arrays stop at the cell edge and are narrower than the aerial's.
- The 1,050 changed blocks: 1,017 `daylight_detector` in six arrays (43,-16 only); 2 `light_blue_stained_glass` at
  (5494,16..17,-1949) in 42,-16, a west-face window found as air (old drift; owner: re-apply whole); 31 old drifts in
  43,-16 restored (30 air -> smooth_stone, 1 double slab -> smooth_stone; x 5529-5532, y 18-45, z -2002..-1980).
- Readback finished `mismatched` with 1,017 positions. Every one is a detector reading `power=10` (set by daylight)
  where the plan said `power=0`; no block was missing. Fixed in code: `same_state` skips `power` on
  `daylight_detector`, and PV rows no longer plan it. A dry-run reconcile with the fix finds 0 positions. The
  `reconcile 43,-16 --fix` that would mark entry 1693 landed was not run (session permission refused; then the
  owner said leave the world as it is), so the entry stays `mismatched` until the owner chooses.
- After seq 1694 the owner hand-edited Westquay in game: a south-west patch (x 5495-5547, z -1983..-1939,
  y 21-46) is now air. That covers 4,100 smooth_stone and 124 detectors in 43,-16, and 546 smooth_stone and 227
  gray_concrete in 42,-16 (interior floors every 3 blocks and roof deck). The journal still lists those blocks under
  w465660355, so **any future re-apply of w465660355, and any `reconcile --fix` that re-commits it, would put
  them back.**
- Capture (north up), beside the aerial crop, taken before the hand edit:
  `instance/google-aerial/337-westquay-roof-aerial-vs-capture.png` in the world checkout (gitignored; look-only aerial).
  The verdict is the owner's.
- Undo: `python3 scripts/element_undo.py --world southampton-build w465660355 --apply` reverts entry 1693 to its
  pre-apply snapshot, which would also undo the owner's hand edit. Then restore the pre-#337 spec, which has no
  `pv_arrays` (the spec's `roofs.WS` without that key). The world's `site/builds/w465660355.json` holds the PV
  version and is not committed in the world repo.
