# Pack v1 — fairness notes (does not change BRIEF.md)

Frozen brief stays in [BRIEF.md](BRIEF.md). These notes interpret rule 4 so bake-off sittings score the **model**, not craft defaults.

## Readable massing ≠ fixed +2 halo

BRIEF: prefer Minecraft-readable massing over jagged 1:1 OSM walls.

That means square away **one-block stair-steps** on the outline so walls read as straight runs. It does **not** mean dilate the OSM footprint by a fixed 2-block envelope. A uniform pad is a craft/system bias (see R-002 interview).

When you square a footprint, print (and put in round `notes` if useful):

```text
footprint osm=N readable=M pad=P cover=C%
```

`pad` should stay small relative to `osm` unless evidence forces a larger readable box.

## Ghost look

The 3×3 ghost baseline may look sparse or empty outside the cell. That is expected after `lidar_ghost apply` — not a reason to chase false “chunk failed” bugs before apply. Sitting order (including the clean-baseline check) lives in `sitting_steps` in [../../config.json](../../config.json), not here.

## Google aerial sheets

If `python3 -m http.server 8765` fails with address already in use, reuse the existing server (`http://127.0.0.1:8765/…`) instead of starting another.

## One apply owner

One agent owns apply for the sitting. If the parent takes over recovery (restore / re-apply), interrupt or idle the model subagent first so two writers do not race.
