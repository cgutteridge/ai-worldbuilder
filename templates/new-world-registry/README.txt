Empty cell registry scaffold for a new world (ticket #33).

Copy into worlds/<name>/registry/ when bootstrapping (skill new-world-build
or dryrun_reset). Paths are per active world — never share registry files
across bake-off / donor saves.

  cells.json      {} until tools register layers / markers / holds
  features.json   {} until tools register feature builds

Progress source of truth: scripts/cell_registry.py (not site/QUEUE or STAGES).
