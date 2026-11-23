# Bridges and tunnels: feature design (#380)

Accepted owner design, 2026-11-22, confirmed after the complete interview. See
[ADR 009](../adr/009-structure-features.md). Follow-up tickets remain
drafts and must not be added to the tracker without the owner's instruction.

## Decisions

- **One structure feature type**, with `kind: bridge|tunnel`. Each mapped structure
  is its own named major feature; it is not a ground, trees or decor pass.
- **Three-dimensional ownership.** Its footprint covers every changed structural
  block, excavation and required clear-air position across all touched cells.
  A tunnel can run below ground, with entrances and approaches partly above it.
  Ground can remain under a bridge and over a tunnel; later passes must preserve
  the structure and its clearance rather than reserve an entire vertical column.
- **Whole-structure undo.** One feature, one logical backup and one undo across
  all cells. Large backups may be tiled by the existing structure-backup path.
  Every affected cell refers to the same feature. These are not mask-only undos.
- **Ordinary structures use recipes.** Beam road bridges, footbridges, simple rail
  viaducts, culverts/underpasses and simple tunnels use evidence-driven recipes.
  Claude designs distinctive landmarks and unusual structures individually.
- **Usable rough fallback.** If the preferred recipe cannot fit the evidence,
  simplify appearance, supports and terrain fit. Keep the carried route connected,
  clearance usable and all writes inside the footprint. Report the fallback and
  its reasons. If even these limits cannot be met, report the exact location and
  conflict before writing that structure; independent valid structures may proceed.
- **Height and crossing evidence.** Use reliable lidar structure columns for deck
  height, not the terrain beneath a deck. Check mapped carriers/layers and the
  road, rail or water crossed, plus any abutting railway join levels. Report
  uncertain heights as inference and use a simpler usable fallback where evidence
  is incomplete. A tunnel needs a usable bore and sufficient cover outside portals.
- **Railway joins retain their settled rule.** A line plans its level continuously
  through a structure span, builds nothing in that gap and records both ends
  (cell, level, direction, tracks). A rail-carrying structure builds after both
  abutting line features exist, refuses as not ready until then, and supplies
  the connecting rails at those recorded levels.
- **Tree crowns follow the same volume rule.** Preserve crowns outside the
  structure and required clearance, including above tunnels. Clear intrusions
  through the normal feature/journal handling and existing neighbour follow-ups.
  Do not clear a whole vertical column solely because a structure passes through it.
- **Level crossings remain separate features.** They use the same railway join
  contract. Gates, lights and bell come from map tags; style and colours come from
  the crossing's own evidence/design. The bridge/tunnel recipe fallback does not
  introduce an unspecified crossing style.

## Current implementation and resulting boundary

Ground already leaves mapped bridge deck columns to a structure and uses lidar
terrain below; a tunnel's surface remains ordinary ground. There is no general
bridge deck, tunnel bore or portal builder. Road labels avoid decks/tunnels;
water profiling already recognises short bridge/culvert dips. Railway evidence
extraction exists in `lib/sources/railway_evidence.py`; production line integration
and crossing integration are #382 and #383.

Map tags (`bridge`, `tunnel`, `layer`, `bridge:structure`, carrier ways) and lidar
structure columns are the evidence inputs. A road/path bridge over a railway is
an overhead structure, not a railway gap. Keep railway clearance usable and do
not interpret a deck as vegetation. A structure that carries the railway is a gap
filled by that structure feature.

Domain definitions live in [GLOSSARY.md](../../GLOSSARY.md); pass and ownership
rules belong in [layered passes](../../rules/handoff-layered-passes.md) sections 4
and 7, which link to the accepted [layered-pass decisions](../adr/010-layered-pass-decisions.md)
and their structure-volume exception from ADR 009.
Implementation of the decisions is the follow-up work below.

## Follow-up A: bridge and tunnel evidence (#450, implemented)

**builderTask: false.** Implemented in `lib/sources/structure_evidence.py`:
`build_structures(ways, ground)` returns one record per mapped bridge or tunnel
(id, kind, form, carrier, layer, centre line and ends, deck columns, touched
cells, crossed ways, evidence refs and a `DeckHeight`). A deck height is
`measured` (at least 8 lidar structure columns agreeing within 2 blocks),
`inferred` (some, but too few or conflicting; the lowest is used) or `unknown`
(none, no lidar, only a higher deck visible, or a tunnel bore). Tree and roof
columns and the terrain beneath are never read as a deck. The underside assumes
a 1-block deck. The footbridge (way 28019377) near MC 6872,-4194 is a recorded
fixture: lidar shows no deck, so it is `unknown`. Original draft text follows.

**Goal:** a deterministic evidence list for mapped bridges and tunnels, independent
of route carrier. Read this design, the railway evidence types, vector-map provider
interfaces and lidar structure-column interpretation first.

**Inputs/output:** recorded GeoJSON and recorded height samples to records containing
stable identity, kind, carrier, crossed routes/water, layers, portal/span geometry,
touched cells, measured deck heights, uncertainty and evidence references. Keep
missing or conflicting evidence explicit; choose no real-place build design.

**Checks:** synthetic/recorded fixtures cover a road over rail, rail over water,
footbridge, culvert and tunnel; separate overlapping structures by layers; distinguish
an overhead span from a rail-carried span; retain missing/ambiguous height evidence;
use the shared world-frame/cell and provider interfaces. No live build or AI judgement.

## Draft follow-up B: structure recipe planner

**builderTask: false.** Depends on A and #380. Do not file yet.

**Goal:** a pure deterministic planner turning an evidence record, supplied recipe
parameters, terrain and optional railway end records into a JSON-able structure plan.
Read this design, evidence records, shared builder helpers and the railway join
contract first. A Claude design can supply parameters for an unusual structure;
this ticket neither selects one from real evidence nor trials it in game.

**Build:** ordinary bridge/footbridge/viaduct/culvert/tunnel recipes, deck/bore/portals,
supports and approaches; changed blocks, protected clear air, full 3D footprint,
rail joins, labelled inference, fallback report and explicit failures. Reuse railway
rail/power rules. Preserve terrain above a tunnel and below a bridge outside that
volume. Never hide an unusable fallback.

**Checks:** synthetic examples for every recipe; underground bore with partly exposed
portals; bridge and tunnel crossing a cell boundary; measured deck rather than DTM
height; connected rail joins; cover/clearance; crowns outside the protected volume;
usable simplified fallback; unresolvable route/clearance conflict fails before apply;
repeat input gives identical plan. No MCP, journal writes or representative build.

## Draft follow-up C: structure feature integration

**builderTask: false.** Depends on B and railway/crossing join mechanisms #382/#383.
Do not file yet.

**Goal:** integrate a validated structure plan into normal feature proposal, readiness,
apply, ownership and whole-feature undo. Read the journal, feature, site_kit and
existing element-undo interfaces first; use those paths rather than new writers.

**Build:** validated specs, one feature across all touched cells, tiled structure
backups, ownership of structural and clear-air positions, the two-neighbour railway
readiness gate, readback/retry, and complete undo including covered-cell markers.
Later ground/trees/decor respect the volume while preserving ground/crowns outside
it; neighbour effects use the existing journal mechanism.

**Checks:** MemoryBlocks/journal fixtures exercise bridge and partly buried tunnel,
missing neighbours (zero writes), readback miss/retry and persistent failure, no
out-of-footprint changes, later-pass protection, whole-structure undo restoring
exactly the previous blocks and ownership. No live world or AI build interpretation.

## Draft follow-up D: real structure build trial

**builderTask: true.** Depends on C. Do not file yet.

**Goal:** interpret real evidence and choose/assess recipes for one road bridge over
road or rail, one river bridge, one footbridge and one tunnel; trial a bespoke design
only where the evidence warrants it. Establish eligible Claude models, host and
budget under the builder dispatch gate; use the configured build roles/efforts.

**Steps:** choose and record proving structures with the owner; store evidence/specs
and place artefacts in the active world repository; dry-run, obtain any required
world-operation authorization, apply under the site lock and read back; assess the
route, approaches, clearance, cover, surroundings and any fallback in game with the
owner. Existing candidate rail bridge spans near 7285,-4290 and 7415,-4107 to
7474,-4020 are only candidates, not permission to build them.

**Checks:** record the owner's verdict, failures/snags and coordinates, whether recipes
or a bespoke design were used, and a Cost table with actual models/efforts, tokens
and measurement method, elapsed time and build result. Prove no unintended changes
outside the footprint. Offline fixture success does not replace the in-game verdict.
