New-world site/ scaffold (place memory only).

Copy into worlds/<name>/site/ when bootstrapping a refine world
(skill new-world-build). Do not copy craft/process prose here — those files
live in ai-worldbuilder rules/ (process, craftsmanship, foliage,
handoff-layered-passes, rules-context, cleanup). Thin stubs below point there.

Keep in the world repo:
  QUEUE.txt ISSUES.txt NOTES.txt COVERAGE.txt REVIEWS.txt HANDOFF.txt
  CURRENT-CELL.json LOCK.json (runtime) cells/*-STAGES.json archive/

Craft/process: edit ai-worldbuilder/rules/*.md only.
