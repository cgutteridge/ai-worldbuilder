# Bridges and tunnels: scope

Scoping note, 2026-11-22. Not a design and not a task. It comes out of the railway trial ([railway](railway.md)):
the owner ruled that bridges and tunnels are not a railway job, because the `bridge` and `tunnel` tags cover roads,
paths, rail and water carriers, so one general piece of work serves all of them.

## Where things stand today

Bridges and tunnels are already named in the pass order as **major features** with their own build and undo
(`rules/handoff-layered-passes.md` section 1; "Feature" in `GLOSSARY.md`). Two questions in that rules file are still
open: whether they need a separate pass type (line 140), and whether the feature keeps the crown rather than yielding to
it (lines 176 to 177, "to be decided when we do the first bridge").

What the code does with them now:

- **Nobody builds a deck.** The ground layer leaves a mapped bridge's columns free and puts ground at lidar terrain height
  under it; the deck is "a structure's, never ground" (`lib/layers/ground_layer.py`, task 298). With no structure
  builder, a highway tagged as a bridge is not surfaced at all (`ground_sources.py` records its columns as `deck`
  and returns before laying any road surface), so the road has a gap there. Railways are not extracted yet, so
  rail bridges are not handled either way.
- **Tunnels are half handled.** A tunnel is not surfaced as a road, and the ground above it is ground as normal
  (task 298; `lib/sources/ground_sources.py`). Nothing builds the bore or portals.
- **Other code already routes round them:** road labels skip tunnels and keep clear of decks
  (`lib/layers/road_labels.py`), the water-level pass raises short bridge and culvert dips
  (`lib/builds/water_level.py`), and `lib/layers/ground_float_audit.py` reports bridge columns no real ground owns.
- **Evidence exists.** The vector map gives `bridge`, `tunnel`, `layer`, `bridge:structure`, `man_made=bridge` and the carrier
  way. Composite lidar marks tall solid outside any footprint as kind 4 "structure" (`lib/sources/lidar_heightmap.py`),
  which is usable evidence for deck height.

## What a general feature would cover

| Piece | Bridge | Tunnel |
| --- | --- | --- |
| Carrier | road, path, rail, canal or aqueduct deck surface | the same carriers, underground |
| Evidence | tags, deck height from lidar structure columns, what is crossed (road, rail, water, nothing) | tags, portal positions where the way goes underground |
| Build | deck, parapets or railings, piers and abutments, approach ramps, clearance underneath | portals, cut approaches, a bored tube with a roof; the surface above stays ground |
| Crossing rule | clearance from what is crossed (road, rail or water level) | cover over the bore |
| Meets railway | the line feature builds nothing in the gap and records level, direction and tracks at each end; the bridge feature includes the rails across and joins those records | same |

## Already decided (owner, 2026-11-22, from the railway work)

- Each bridge, tunnel and level crossing is **its own feature**, Claude-designed from evidence like any feature. It
  MAY include rail lines, and records which abutting railway features it joins (cell, level, direction, tracks).
- It is built **after** the abutting line features and refuses ("not ready") until both exist. The line is planned
  through the span but builds nothing in the gap. Details: [railway](railway.md), decisions 9 and 10.
- A level crossing's gates, lights and bell come from the map tags; style and colours come from Street View as part of
  the crossing's own build. No separate survey step.
- The rail feature trumps the road in its footprint (decision 7 there). The first crossing to build is the one at
  about 6980,-4343.
- Only one bridge and tunnel ticket is filed now: the decisions ticket below. Evidence, planner and integration
  tickets wait for it.

## Decisions needed before any build task

### Owner interview in progress (2026-11-22, task #380)

Provisional answers, awaiting confirmation of the complete design:

- One structure feature type with `kind: bridge|tunnel` (owner chose option 1).

The remaining questions below are still open; this is not an accepted design.

1. **One feature type or two.** Bridge and tunnel share the carrier and the cross-cell handling but differ in what they
   dig and what they leave above. Leaning one type with a `kind`.
2. **The open pass-order rule** (crown versus yield) above. Settle it in the glossary and rules, not in code.
3. **Rules or per-structure design.** A small set of rule-built types (beam road bridge, footbridge, rail viaduct,
   culvert or underpass, simple tunnel) with defaults, as the vibe plan does for building typologies, versus a Claude
   design for each structure. Leaning rules first, per-structure design only for landmarks.
4. **Multi-cell spans.** Long structures cross cell edges, and these are not mask-undone, so ownership and undo need the
   same footprint treatment as other big features.
5. **Failure rule.** Following the railway ruling: best effort, fail with a clear report where a structure cannot be made
   to work, and leave it for a human.

## Proposed split (owner's rule: Claude only for real build decisions and in-game judgement)

| Task | builderTask | Notes |
| --- | --- | --- |
| A. Evidence list: for each mapped bridge or tunnel, carrier kind, what it crosses, cells, length, layer, deck height from lidar | false | Fixtures from recorded GeoJSON and a recorded height sample. |
| B. Decisions: feature type, pass-order rule, rule-built types, ownership across cells; record in glossary, rules and an ADR | false | Owner conversation, then docs. No build step. |
| C. Rule-based planner (pure function): deck, parapets, piers, ramps, tunnel bore and portals | false | Synthetic fixtures. Depends on A and B. |
| D. Feature integration: journal ownership, backup, undo, readback, and the hole in road and rail surfaces closed | false | Depends on C. Includes the hook the railway builder calls. |
| E. Build trial on real structures, judged in game | **true** | One road bridge over a road or rail, one river bridge, one footbridge, one tunnel. Depends on D. |

Candidates for E already extracted: the railway trial area has two mapped rail bridge segments near 7285,-4290 and
from 7415,-4107 to 7474,-4020 (what each crosses is not yet checked).

Nothing in the tracker covers this today. Do not file A to E until the owner agrees the scope.
