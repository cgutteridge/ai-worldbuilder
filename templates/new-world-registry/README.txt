Nothing to scaffold for the cell registry.

The registry directory is created by the first journal write
(registry/journal.jsonl plus registry/blobs/, see docs/adr/004-cell-journal.md).
A new or freshly reset world has no registry/ at all, and that is the correct
empty state: do not create registry files by hand.

Progress source of truth: scripts/cell_registry.py (next / verify).
