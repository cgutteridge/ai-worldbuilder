# Large-coordinate live check — 2026-10-28

Task #180 checked Minecraft 26.2 / Fabric Loader 0.19.3 / MCP 1.1.0 on the
owner's existing `ventnor-build` save, explicitly authorised as disposable.
The frame uses EPSG:27700 and offsets 0/0. This is a check at hundreds of
thousands of blocks, not a certification of every projected CRS or distance.

## Results

- Placed a 21×21 stone platform at Y=63, x=441990…442010,
  z=−115010…−114990, and a 5-high wooden wall. Readback from the fill tools:
  441 platform blocks and 55 wall blocks changed.
- Teleported the player to (442000.5, 64, −115000.5). The client reported
  the expected coordinates and rendered the test scene. The owner looked
  around and reported “world looks fine”; no visible jitter was reported.
- Spawned a pig at (442002, 67, −115003), enabled normal AI/gravity, and
  observed it land at Y=64 with `onGround=true` and full health. After a
  velocity impulse it moved to (441996.5108, 64, −114997.7250). A temporary
  `NoAI` test setup initially prevented movement; enabling AI resolved it.
- `world_frame.py --world ventnor-build to-cell --mc 442000,-115000`
  returned `3453,-899`, matching floor division by the 128-block cell size.
- Flushed via the server save API and found
  `dimensions/minecraft/overworld/region/r.863.-225.mca`, matching
  floor division by the 512-block region size. No save files were written
  directly by the test.

The server greeting still said `SouthamptonAI`; flushed files confirmed the
actual active save was `ventnor-build`. Use the save/client evidence rather
than the greeting to identify the open world. The site lock was corrected to
Ventnor after this discrepancy was found. The test platform remains in the
owner-authorised disposable save for inspection.

## New-world guidance

Keep the 0/0 offset default for coordinates of this order. At each new home,
check the first real terrain for jitter and movement before committing to
expensive builds. Larger CRS coordinates (including multi-million UTM
northings) were not tested here. If the client jitters, use home coordinates
rounded to the nearest 1000 m as offsets and recreate the initial ghost before
building further, as described in the new-world-build guide.
