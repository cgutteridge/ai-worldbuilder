# ukdefra-lidar

England DEFRA 1 m composite DTM + DSM (last return). Covers England only; Scotland, Wales and anywhere else need a different lidar provider.

## Tile grid and bbox (read by the new-world-build skill)

- **Grid:** 5 km squares on the British National Grid (EPSG:27700). Tile corners are multiples of 5000 m in easting and northing.
- **Size:** each tile is one DTM zip and one DSM zip, about 75 MB each (45-73 MB measured). A tile cannot be downloaded in part.
- **Speed:** about 1 minute per tile per product; 4 tiles x DTM and DSM took about 7.5 minutes.
- **Tile count:** a box inside one square needs 1 tile; straddling one grid line needs 2; straddling a corner needs 4. Count with `get/defra_lidar.tiles_for(e0, n0, e1, n1)` (E/N metres).
- **Aligned boxes:** `tiles_for` adds 1 m east and north for bilinear corners, so a box ending exactly on an upper tile edge pulls in the next tile column and row (Sheffield: an intended 2 tiles fetched 6). End an aligned box 1 m inside the east and north edges, e.g. `x1=439998`, not `440000`.
- **Coverage:** sea and some edges have no lidar; shift a coastal box inland.
- **Datum:** Ordnance Datum Newlyn (orthometric), so `y_offset` 0 puts sea level at Y 0.
