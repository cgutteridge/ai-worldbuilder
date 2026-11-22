# Build workflow: evidence to accepted feature

One loop for every feature (building, water body, anything with a `site/builds/<name>.json`).
It joins the pieces already in `build-roles.md`, `feature-builds.md`, `building-review.md` and
the `redo` skill. Learned 2026-10-29 (SUSU cover + pond): the failures were in the order, not the specs.

## Loop

1. **Evidence table** (designer). Every fact names a source: outline, heights, openings, colour,
   greeblies. Outlines are fitted to what the aerial shows (a rotated rectangle, not a raster of a
   vector polygon) and cross-checked against the vector map. Done: table in spec `notes`.
2. **Spec + dry run** (designer, no apply, no lock). Tool gaps found here become a `task/*`
   change first, not a workaround in the spec.
3. **Readiness check** (builder, read-only, BEFORE any undo). Do not undo until all pass:
   - no journal entry left `begun` (`site_recover.py status`), lock free;
   - ground exists around the feature and the approach cells: live top Y vs lidar `ground_y`
     within 1 outside every door and along the outline (the ground pass skips footprint
     columns, so undoing a build can leave a hole the door logic then reads);
   - dry run on the CURRENT world: door floors, covers and clashes match lidar levels.
   A failure is fixed (ground pass, tool fix) before step 4, never by guessing `floor_offset`.
4. **Apply** (builder). Follow `rules/slow-commands.md#execution`: undo old, dry run again,
   apply, readback 0. Resolve any mismatched journal entry before continuing.
5. **Review** (server-side, never move the player): top + iso `block_render_region` beside the
   aerial, plus numbers (live vs lidar, door y, footprint). Name each mismatch.
6. **Fix = respin the spec** and loop from step 2. Never patch on top; never stack `_b` builds.
   Lessons that apply to every feature go into the rule file that owns them.

## Order rule

Undo is the first destructive step. Everything before it is read-only, so a bad plan costs
nothing. Everything after it must be able to finish in the same session.
