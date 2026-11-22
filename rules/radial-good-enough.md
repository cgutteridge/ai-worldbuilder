# Radial cadence and the good-enough bar

Read before choosing, planning or reviewing build work.

## Good enough (the one home for this rule)
- Hit the done-bar (`site/NOTES.txt` §8) and move on. This is a big city.
- When a detail is burning tokens without changing street-readable accuracy:
  stop, mark it good enough, log a short note if useful, go to the next cell.
- Do not chase chimney pots, garden furniture, planting detail or single
  disputed blocks unless the place brief raised the bar.
- Good enough never means "skip the survey" or "front face only": a house is
  still built on every side (see `rules/frontage-and-evidence.md`).

## Radial, no gaps
- Rebuild all of an area, outward from the configured home cell, finishing each Chebyshev ring before
  the next. One active cell at a time. Cell order: north centre of the ring, then
  clockwise (north is smaller tz).
- Coverage + registry rollup: `rules/process.md` §2c. The record is
  `coverage.py write` + `cell_registry.py`, never chat, and never hand-edit the
  generated COVERAGE block.
- Use registry DONE for completion. Where an area already has builds,
  keep them unless a snag reopens work.
- Revisits: file a snag (or redo) for the layer/feature;
  dry-run before re-apply (I-043). Code/tooling fixes beat new cells.
- Distinctive local features → `snag` skill / `scripts/issues.py` (PROCESS §2e).
- Focus moves with `cell_marker.py --clear` / `--raise`; leave no ring holes behind.
