# Feature-selection check protocol

## Goal

See whether a bulk model can **select the right building** and **state its exclusive bounds** — without designing massing, openings, or applying blocks.

## Out of scope

- A/B model ladders (use `bakeoff/rounds/` for that)
- Apply / ghost restore
- Minecraft-readable dilation (±1–2 fringe). Scoring treats outer pad as fail.

## One sitting

1. Pick a scenario (`list` / `brief FS-NNN`).
2. Open a **fresh** chat with the check-target model only (Composer for v1).
3. Paste the brief. Do **not** attach `keys/`.
4. Agent returns one JSON object (schema in the brief).
5. Save the JSON and `grade` it. Record pass/fail in notes or `runs/`.

## Pass bar

| Field | Pass when |
|-------|-----------|
| `osm_way_id` | Exact gold way |
| `reject_ways` | Contains every gold required reject (extras OK) |
| `bbox_mc` | IoU vs OSM raster bbox of that way ≥ `min_bbox_iou` (default 0.85) **and** Chebyshev outer pad vs OSM ≤ `max_pad` (default 0) |
| `dilated` | Must be `false` |

Overall pass = all of the above.
