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

`propose-features` / `cell_features.missing_features` include `kind: water_body`
candidates from connected vector-map water columns. Unresolved level/bank/edge
go on the plan item via `water_body_plan_item` + provenance `unresolved_reason`.

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
