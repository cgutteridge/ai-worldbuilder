# Models tested in the place-build bake-off

Task: `feature_design`, pilot pack `v1`. Each model builds the Students' Union (SUSU, building 42) in cell 48,-44 of the `bakeoff` world from the same evidence, then the owner grades five axes (massing, openings, roof/greeblies, materials, keep). Massing and keep are critical: failing either fails the round. Grades are fail, pass, great. Source: `bakeoff/rounds/R-*.json`; older rows from `bakeoff/MACRO-RESULTS.md`.

## Results

| Round | Model (tool) | Tier | Result | Why, where recorded |
| --- | --- | --- | --- | --- |
| R-001 | Composer 2.5 | | fail | no reason recorded |
| R-002 | Grok 4.7 | | fail | no reason recorded |
| R-003 | Claude Haiku 5.5 | low | fail | failed the mechanical gate |
| R-004 | Claude Haiku 5.5, high effort | low | skipped | abandoned unrun to test Sonnet first |
| R-005 | Claude Haiku 5.5 | low | **fail** | mechanical gate passed, then failed overall; no per-axis grades |
| R-006 | Claude Haiku 5.5, high effort | low | **fail** | massing, openings, roof and keep failed, materials passed: building sat wrongly on the lidar ground with too much surrounding ground; door rhythm and count wrong; invented a glazed atrium from lidar no-return gaps that the aerial photo does not show |
| R-007 | Claude Sonnet 5.5, low effort | mid | **fail** | declared a failure by the owner before the interview; stopped early |
| R-008 | Claude Sonnet 5.5, medium effort | mid | **pass** | all five axes pass; roof/greeblies a weak pass (stray carpet bits, flat aerial look) |
| R-009 | Claude Sonnet 5.5, high effort | mid | **pass** | openings failed (not critical): diagonal stair wall left black/brick corners instead of glazing, too many solid blocks; a 7-high grey slab read as a stray wall |
| R-010 | GPT-6.1 Sol, low reasoning (Codex) | low | **pass** | all five axes pass |
| R-011 | GPT-6.1 Sol, medium reasoning (Codex) | low | **pass** | mixed great, pass and fail elements; walls missing and not airtight, overall good |
| R-012 | Claude Opus 5.5, low effort | high | **great** | owner: "low-great"; no per-axis grades |
| R-013 | Claude Opus 5.5, medium effort | high | **great** | owner: great; no per-axis grades |
| R-014 | Sonnet 5.5 medium builds, Opus 5.5 low designs | mid | skipped | |
| R-015 | Sonnet 5.5 medium builds, Opus 5.5 low designs (hybrid) | mid | **pass** | scored pass on closing the bake-off, no per-axis interview |

## Reading the results

- **Haiku 5.5 is not suitable** at either effort setting (R-003, R-005, R-006). It passed the mechanical checks but misjudged massing and invented roof detail the evidence did not support.
- **Sonnet 5.5 low effort failed; medium and high pass.** Medium (R-008) was the cleanest Sonnet run; high (R-009) did not improve it.
- **GPT-6.1 Sol passes at low and medium** on the Codex tool.
- **Opus 5.5 is the only model graded great** (low and medium effort).
- **A hybrid works:** Opus designs the evidence table and plan, Sonnet builds it, and the owner passed that (R-015).

## Caveats

- One building, one cell, one owner's grading; per-axis grades are missing for R-005, R-007 and R-011 to R-015.
- The R-010 to R-013 token totals in the round files (millions) look like they include cached reads; treat them as unreliable.
- R-008 passed partly with parasols outside the OSM footprint, which the footprint guard let through; the round notes call that a tool gap, not a model fault.

## A note on `truth.json`

The sealed file `~/.local/share/ai-worldbuilder/bakeoff-sealed/truth.json` is not a results file. It is the answer key for the feature-selection check ("which building is the largest in the cell"), written before any run so models cannot see it. Its contents are deliberately not copied here, because bake-off packs and docs are readable by the models under test.
