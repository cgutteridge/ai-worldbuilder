New-world site/ scaffold (place memory only).

Copy into worlds/<name>/site/ when bootstrapping a refine world
(skill new-world-build). The registry needs no scaffold: registry/ is created
by the first journal write (templates/new-world-registry/README.txt).

Do not copy craft/process prose here — those files live in ai-worldbuilder
rules/ (process, craftsmanship, foliage, handoff-layered-passes,
rules-context, cleanup). Thin stubs below point there.

Keep in the world repo:
  NOTES.txt COVERAGE.txt REVIEWS.txt
  LOCK.json (runtime) cells/ (optional SOURCES notes)
  archive/
  (ISSUES.txt is optional scratch only; snags → registry/issues.json)

Progress source of truth is the cell journal (registry/journal.jsonl), driven by
scripts/cell_registry.py next / hold / reconcile. Snags: scripts/issues.py.
Do not recreate retired progress files under site/ — `cell_registry.py verify`
treats them as leftovers (see dryrun_reset / registry_verify for the list).

Craft/process: edit ai-worldbuilder/rules/*.md only.
