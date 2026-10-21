RULES CONTEXT — WHAT TO READ WHEN (token index)
Tool-repo index (ai-worldbuilder/rules/rules-context.md). Start with
`python3 scripts/cell_registry.py next` (or site_handoff.py next, which
prints the same picker). Rule files in rules/ are not auto-loaded: read
only what the brief / task needs.

------------------------------------------------------------------------
0. ANTI-REREAD (every session)
------------------------------------------------------------------------
 - Run `python3 scripts/cell_registry.py next` once at start.
 - Read each listed file once per chat unless the work clearly crosses into
   another context (e.g. you start planning a whole house after a ground-only
   item — then read PROPERTY rules before plan).
 - Do not read whole rules/process.md. Grep the § slices below.
 - Do not run coverage.py --help. Use `coverage.py write` / status cheatsheet.
 - Imagery: read site/cells/*-SOURCES.txt (or manifest grep) before fetch/Read;
   do not re-Read assets marked useful=no; one contact sheet before full-size.
 - After a rule file is read, grep/sed targeted sections later — do not re-Read
   the whole rule file for a reminder.

------------------------------------------------------------------------
1. CONTEXTS (pick one primary per registry task)
------------------------------------------------------------------------
 SESSION       Boot only: lock, cadence. No live edits.
 TOOLING       Scripts/tests/docs; no Minecraft apply.
 SOURCES       cell_sources.py --write; manifest + one imagery judge pass.
 GROUND        Layered passes: ground_layer, trees_layer, decor_layer; no new house shells.
 FOLIAGE       Trees pass (trees_layer.py); rules/foliage.md + cross_zone.
 PROPERTY      House on its footprint map (all sides, roof, floors); gardens = ground/trees/decor.
 TERRACE       Broadlands-type row; scripts/terrace.py generator.
 ISSUE         Registry issue fix (cell_registry add-issue / close).

------------------------------------------------------------------------
2. rules/process.md SLICES (grep / sed -n; not the full file)
------------------------------------------------------------------------
 All live edits     §2 (cadence), §2a (region snap when needed), §6–§7
 GROUND / FOLIAGE   + §2c §2e (step 5 = layered passes)
 PROPERTY           + §2b (refs), §2c §2d-i §3 (checklist as needed)
 SOURCES            §2b, §2e step 1b, reference/LOGGING.txt §8
 TOOLING            §2c only if touching coverage data

------------------------------------------------------------------------
3. RULES + SITE DOCS BY CONTEXT
------------------------------------------------------------------------
 Always (any live world edit):
   rules/radial-good-enough.md

 AERIAL COVERAGE: never assume the z19 tiles cover the whole cell (47,-43 is black west of ~x6058).
   Check, and for any gap use the zoomed-out z18 mosaic that covers that cell (ref_manifest.py find --xz, else geo.py tiles).
   Not tied to any one place or folder.

 Before fetch / Read of reference imagery (any context that opens photos/aerial):
   rules/reference-token-budget.md
   rules/evidence-order.md (which type answers which question; lidar composites; look-only aerial every cell)
   reference/TYPES.txt (when choosing or adding a provider)
   + committed site/cells/<tx>_<tz>-SOURCES.txt when CELL is set

 GROUND:
   rules/handoff-layered-passes.md §1 (who clears volume) and §10 (ground step)
   rules/craftsmanship.md §3a (palette) + good-enough only
   rules/foliage.md — skim; full read only for FOLIAGE context
   site/cells/*-SOURCES.txt — GROUND FINISH block when present for active cell

 FOLIAGE (includes GROUND list):
   rules/foliage.md — full

 PROPERTY (includes Always + imagery when surveying):
   rules/frontage-and-evidence.md
   rules/craftsmanship.md — full
   university register entries on the footprint: reference/portals, reference/buildings
     (scripts/portals.py building N, scripts/buildings.py building N)
   Non-house building feature: rules/feature-builds.md + scripts/feature_build.py

 TERRACE (includes PROPERTY list):
   rules/terrace-builds.md

 TOOLING / SESSION:
   (no rules file unless you touch imagery or apply)

------------------------------------------------------------------------
4. OUT OF SCOPE HERE
------------------------------------------------------------------------
 Provider catalogue / fetch how → place SOURCES + evaluate-data-source / add-data-source.
 System tracker → tasks/index.json (not the world registry).
