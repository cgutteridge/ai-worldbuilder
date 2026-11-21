# Water-body features (ponds)

Read before planning or building a pond / mapped water body. Ground leaves
mapped water and its bank alone (`ground_layer.free_columns`); the water body
is a **feature** on the cell plan, not a ground kind.

## Live path

Every water-body apply uses **`scripts/water_body_build.py`** with a JSON spec
at `site/builds/<name>.json`. Dry run unless `--apply`. Spec keys (full list
in the `water_body` module docstring):

- `name` — plan / cells-record name (reuse `propose-features` / `candidate_plan_id`)
- `outline` — `[[x, z], ...]` water columns from the vector map or aerial
- `water_level` — integer Y of the water surface
- `depth` — water column height including the surface (default 1)
- `floor` — bed under the deepest water (default `dirt`)
- `bank` — `{"material": "<block>", "width"?: 1}` shore ring
- `edge` — optional `{"material": "<block>"}` shoreline rim on bank cells that
  touch water
- `provenance` — shared schema from `lib/site/provenance.py` (#151):
  `{source, confidence, evidence_ids, unresolved_reason}` (no private fields)
- optional: `modifies`, `notes`
- optional: `clip` — `{"cell": [tx, tz]}` for one cell's part of a body over cell lines (below)

`propose-features` / `cell_features.missing_features` include `kind: water_body`
candidates from connected vector-map water columns. Unresolved level/bank/edge
go on the plan item via `water_body_plan_item` + provenance `unresolved_reason`.

## Bodies over cell lines (#321)

A river or pond that crosses a cell line is planned and built **per cell**, not
whole in one home cell (buildings still follow #288). `propose-features --cell
<tx,tz> --record` in each cell the body touches records that cell's clip plan,
id `<ref>_<tx>_<tz>` (`m` for a minus, e.g. `w4019512_63_m45`). The candidate
carries `clip`, the in-cell `area`, and an `outline` of the in-cell water plus
the body's water up to `CLIP_REACH` (8) blocks beyond the cell. A cell with
none of the body's water but some within the **default bank width** (1) gets a
**bank-only** clip (area 0), because the shore can spill over a cell line the
water never crosses. Water that is only farther away (still inside `CLIP_REACH`)
is not proposed as bank-only: at the default width the spec would be refused.
If you later choose a wider `bank.width` that needs a dry neighbour the proposal
skipped, write that clip spec by hand. If a recorded plan's chosen width leaves
the cell untouched, the spec is refused: dismiss that clip plan
(`cell_registry.py dismiss-feature`).

Copy `name` (the clip id), `outline` and `clip` into that cell's spec. A clip
plan id as `name` **requires** `clip` (#353); omitting it builds the neighbour's
water out to the reach and banks that artificial edge. The build writes only the
clip cell; the bank ring is worked out on the whole outline, so real shorelines
get bank and the cell edge gets none. A clipped apply never writes into a
neighbour, so it never makes a finished neighbour's trees or decor stale (the
#234 edge policy). Use the same `water_level`, `depth`, `bank` and `edge` in
every clip of one body so the cells join without a step. `bank.width` must not
exceed `CLIP_REACH`.

### Legacy whole-body plans

A spec **without** `clip` still builds the entire `outline` in one apply (the
pre-#321 whole-pond path, and the offline “join the clips” check). Keep that for
legacy plans and fixtures. Do not reuse a clip plan id (`<ref>_<tx>_<tz>`) as
`name` on a whole-body spec: load refuses it so a copied clip outline cannot
silently bank an artificial edge.

Example:

```json
{
  "name": "water_6200_m5600",
  "outline": [[6200, -5600], [6201, -5600], [6200, -5599], [6201, -5599]],
  "water_level": 42,
  "depth": 2,
  "bank": {"material": "dirt", "width": 1},
  "edge": {"material": "cobblestone"},
  "provenance": {
    "source": "vector_map",
    "confidence": "high",
    "evidence_ids": [],
    "unresolved_reason": ""
  }
}
```
