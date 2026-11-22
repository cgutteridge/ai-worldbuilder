# Bridges and tunnels are structure features with three-dimensional ownership

Accepted by the owner on 2026-11-22 (#380). Bridges and tunnels share one structure
feature type, distinguished by kind, rather than adding a layer or splitting a
structure at cell boundaries. One named feature owns its changed blocks,
excavation and required clear air across every touched cell, with one logical
structure backup and whole-feature undo. Ground may remain underneath a bridge
or above a tunnel, whose entrances may be partly exposed. Later passes preserve
that volume; existing tree crowns outside it remain. This avoids treating a
structure as ownership of an entire vertical column, which would destroy ground
or vegetation that does not obstruct it.

Ordinary bridges, footbridges, simple viaducts, culverts and tunnels use
evidence-driven recipes; Claude designs landmarks and unusual structures. Deck
height comes from reliable lidar structure columns checked against the crossed
route/water and railway joins, with uncertain evidence labelled inference. When
the preferred design cannot fit, use a reported simpler fallback that preserves
route connection, usable clearance and the feature footprint. If even those
limits cannot be met, report the conflict before writing the failed structure.
This trades exact appearance for a usable result without silently blocking a route.

A railway-carrying structure supplies the rails through the gap left by the line
features and is not ready until both abutting ends are backed by landed feature
records. Level crossings remain separate features with the same join contract;
their equipment follows map tags and their appearance follows their own evidence.
The bridge/tunnel fallback does not supply a default crossing style. See the
[scope and four draft follow-ups](../plans/bridges-and-tunnels.md); these decisions
do not authorize real-place builds or filing those drafts.
