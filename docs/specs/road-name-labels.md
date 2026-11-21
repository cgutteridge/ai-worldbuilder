# Road-name labels (task #341)

Optional dressing after decor. Generic tool behaviour (any UK place), not world work.
A cell is done without these labels. They are not an Element, they write no blocks, and
they do not change an earlier pass's ownership.

## What a label is

One vanilla `text_display` (Java 1.21.5 or newer). The text is an SNBT compound, not a
JSON string:

```
text:{text:"<name>",color:white}
```

Also set: `billboard:center`, `background:0`, `text_opacity:127b`, `view_range:4.0f`
(about 256 blocks, so a name stays readable from the next cell), and a scale of 2
(`transformation.scale` is `[2,2,2]` with identity rotations).

Tags: `wb_label` and `wb_cell_<tx>_<tz>` (the cell that contains the label). Example:
`wb_cell_48_-44`.

The entity sits on the road centreline at lidar ground Y + 3. Lidar ground is the
terrain height of that column (the surface block's Y). Chunks must be loaded before
the summon; the apply path moves the player over the cell when they are too far, then
puts them back.

## Which roads

Every named highway on the vector map (`name` tag), including a named footway. Skip a blank name.
Skip a tunnel (`tunnel=yes` or a negative `layer`). A bridge or raised way
(`bridge=yes` / `viaduct`, or `layer` above 0) is still part of the network, but a
label is not placed on it or under it.

One named way is not labelled once per cell it crosses. A short way (up to 100 blocks)
gets a single label at its midpoint. A longer way gets labels about every 80 blocks,
never closer than 60 along that way, and the gap from either end stays within about
100. Where two differently named ways meet, each keeps a label about 8 blocks off the
crossing, and those two may sit closer than 60 (up to 24 blocks apart). The same name
never gets that exemption. Spacing is measured in XZ across cell boundaries: a second
way with the same name just over the edge does not add another label inside 60 blocks.

## Clear air

The three blocks above the lidar surface, including the label's own block, must be
air. Leaves, trunks, and any other block count as blocked, so a label does not sit
in a tree. Those blocks are read from the saved world after a flush. A missing
chunk is not air: an area that was never saved is skipped, rather than labelled
as if it were open sky. A column within 6 blocks of a bridge or raised centreline is blocked even
when the air is clear, so a label does not hang under a deck.

If the chosen point is blocked, walk along that way up to 20 blocks either side and
take the nearest clear point. If none is clear, skip that label. A slide cannot
create a second label: the landed spot still has to respect the spacing rules above.

Asking for one cell still accounts for labels just outside it (the cell, plus 80
blocks). Labels further away are not summoned and are too far to change this cell's
spacing.

## Apply and undo

`python3 scripts/road_labels.py --cell tx,tz` prints the plan. It does not summon.

`--apply` (world lock held) kills this cell's existing `wb_label` entities, then
summons the plan. Running it again replaces the cell's labels rather than stacking
copies. `--undo` only kills. The tool kills through `entity_query` (`@e[tag=wb_label]`,
then keeps entries whose own tags include the cell tag) and one `entity_kill` per UUID,
because the world server does not always register `command_execute`. The same kill by
hand is:

```
kill @e[tag=wb_label,tag=wb_cell_<tx>_<tz>]
```

No block fill, no mask, no journal build entry. The owner can record a sign-off with
`cell_registry.py approve tx,tz --layers labels`. That approval does not stand in for
ground, trees, or decor, and `labels` is not part of the definition of done.
