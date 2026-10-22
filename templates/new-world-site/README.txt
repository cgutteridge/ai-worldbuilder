New-world site/ scaffold (place memory only).

Copy into worlds/<name>/site/ when bootstrapping a refine world
(skill new-world-build). The registry needs no scaffold: registry/ is created
by the first journal write (templates/new-world-registry/README.txt).

Do not copy craft/process prose here — those files live in ai-worldbuilder
rules/ (process, craftsmanship, foliage, handoff-layered-passes,
rules-context, cleanup). Thin stubs below point there.

Keep in the world repo:
  ISSUES.txt NOTES.txt COVERAGE.txt REVIEWS.txt
  LOCK.json (runtime) cells/ (apply journals + optional SOURCES)
  archive/

Progress source of truth is the cell journal (registry/journal.jsonl), driven by scripts/cell_registry.py next / issues / hold.
Do not recreate QUEUE.txt, CURRENT-CELL.json, CELL-HOLDS.jsonl,
AERIAL-REVIEW.txt, COVERAGE-SIGNOFFS.jsonl, or HANDOFF.txt — verify treats
them as leftovers.

Craft/process: edit ai-worldbuilder/rules/*.md only.
