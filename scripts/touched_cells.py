#!/usr/bin/env python3
"""Touched columns and ownership overlap for build archives (extracted from coverage.py).

Reads ``verification/site/*-cells.json`` only:

  touched_by_build  — per-build XZ columns (disk cache + process memo)
  union             — all touched columns
  ownership_overlap_issues — owned (x,y,z) whose (x,z) is missing from that build's
                             touched set (Q-195 vs site_kit.built_cells / owned_in)

Caches (invalidate when the matching input changes):

  touched-cache.json   per-build run encoding; key = file mtime + size
  _TOUCHED_MEMO        process memo; key = exclude + every cells file name/mtime_ns/size

Parcel digests reuse encode_runs / decode_runs; parcel model and coverage CLI stay in
coverage.py.
"""
from __future__ import annotations

import json

from site_kit import OUT, built_cells, owned_in  # noqa: E402

STORE = OUT / 'coverage'
TOUCH_CACHE = STORE / 'touched-cache.json'

# Builds whose work was undone (I-009: façade shells on the pavement, reverted by
# broadlands_facade_revert + region snap I-009-revert). Their archives still exist.
REVERTED = frozenset({
    'portswood_x_broadlands_terrace_facade_1', 'portswood_x_broadlands_terrace_facade_1b',
    'portswood_x_broadlands_terrace_facade_2', 'portswood_x_broadlands_terrace_facade_2b',
    'portswood_x_broadlands_terrace_facade_2c', 'broadlands_facade_revert',
})

_TOUCHED_MEMO: dict = {}


def touch_excluded(name: str) -> bool:
    """Whether a build's columns are omitted from parcel touched stats.

    @param name: build name (verification/site/<name>-cells.json)
    @returns: True if the build should not count as touched ground
    """
    return name in REVERTED or name.startswith('cell_marker_')


def encode_runs(cells) -> list:
    """Row run-length encoding of a column set.

    @param cells: iterable of (x, z)
    @returns: [[z, x_first, x_last], ...] sorted by z then x
    """
    rows = {}
    for x, z in cells:
        rows.setdefault(z, []).append(x)
    out = []
    for z in sorted(rows):
        xs = sorted(rows[z])
        start = prev = xs[0]
        for x in xs[1:]:
            if x != prev + 1:
                out.append([z, start, prev])
                start = x
            prev = x
        out.append([z, start, prev])
    return out


def decode_runs(runs) -> set:
    """Inverse of encode_runs.

    @param runs: [[z, x_first, x_last], ...]
    @returns: {(x, z), ...}
    """
    return {(x, z) for z, a, b in runs for x in range(a, b + 1)}


def touched_by_build(exclude=REVERTED) -> dict:
    """Same as ``_touched_by_build``, remembered for the life of the process while no *-cells.json changes.

    The key is every cells file's name, mtime and size plus ``exclude``, so any apply/undo that writes a file
    gives a fresh result. Callers get their own copies of the sets. (next --plan asks 68 times per run.)

    @param exclude: build names to ignore (default: REVERTED); also skips cell_marker_*
    @returns: {build name: set of (x, z)}
    """
    key = (frozenset(exclude), tuple((f.name, st.st_mtime_ns, st.st_size)
                                     for f in sorted(OUT.glob('*-cells.json')) for st in (f.stat(),)))
    if key not in _TOUCHED_MEMO:
        _TOUCHED_MEMO.clear()
        _TOUCHED_MEMO[key] = _touched_by_build(exclude)
    return {name: set(cols) for name, cols in _TOUCHED_MEMO[key].items()}


def _touched_by_build(exclude=REVERTED) -> dict:
    """Columns covered by each build's archived cells (verification/site/*-cells.json).

    Cached per build by file mtime + size in verification/site/coverage/touched-cache.json.

    @param exclude: build names to ignore (default: REVERTED); also skips cell_marker_*
    @returns: {build name: set of (x, z)}
    """
    cache = json.loads(TOUCH_CACHE.read_text()) if TOUCH_CACHE.exists() else {}
    out, dirty = {}, False
    for f in sorted(OUT.glob('*-cells.json')):
        name = f.name[:-len('-cells.json')]
        st = f.stat()
        key = [st.st_mtime, st.st_size]
        if cache.get(name, {}).get('key') != key:
            cols = set()
            for k in json.loads(f.read_text()):
                x, _y, z = k.split(',')
                cols.add((int(x), int(z)))
            cache[name] = {'key': key, 'runs': encode_runs(cols)}
            dirty = True
        if name not in exclude and not touch_excluded(name):
            out[name] = decode_runs(cache[name]['runs'])
    for gone in set(cache) - {f.name[:-len('-cells.json')] for f in OUT.glob('*-cells.json')}:
        del cache[gone]
        dirty = True
    if dirty:
        TOUCH_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TOUCH_CACHE.write_text(json.dumps(cache, separators=(',', ':')))
    return out


def union(by_build: dict) -> set:
    """@param by_build: {build: set of columns}
    @returns: every touched column"""
    out = set()
    for cols in by_build.values():
        out |= cols
    return out


def ownership_overlap_issues(exclude=None, *, within=None) -> list:
    """Owned cells whose XZ column is missing from that build's touched set (Q-195).

    ``touched_by_build``, ``site_kit.built_cells``, and ``site_kit.owned_in`` all read the
    same ``*-cells.json`` archives. Every non-air cell owned by build B must have its
    ``(x, z)`` in ``touched_by_build()[B]``. When ``within`` is set, also require
    ``owned_in(within)`` to match the full ``built_cells`` merge on those keys.

    @param exclude: build names ignored by both readers (default: REVERTED)
    @param within: optional iterable of ``(x, y, z)``; limits the owned-cell scan (and
        enables the owned_in vs built_cells check). Omit only on small fixtures.
    @returns: list of issue dicts (``kind`` plus location / build fields); empty if agreed
    """
    if exclude is None:
        exclude = REVERTED
    exclude = frozenset(exclude)
    by_build = touched_by_build(exclude=exclude)
    issues: list = []

    if within is not None:
        cells = list(within)
        indexed = owned_in(cells, exclude=exclude)
        full = built_cells(exclude=exclude)
        want = {q: full[q] for q in cells if q in full}
        if indexed != want:
            issues.append({
                'kind': 'owners_index_mismatch',
                'indexed': {f'{x},{y},{z}': [b, s] for (x, y, z), (b, s) in indexed.items()},
                'full': {f'{x},{y},{z}': [b, s] for (x, y, z), (b, s) in want.items()},
            })
        owned = indexed
    else:
        owned = built_cells(exclude=exclude)

    for (x, y, z), (build, state) in owned.items():
        cols = by_build.get(build)
        if cols is None or (x, z) not in cols:
            issues.append({
                'kind': 'owned_column_not_touched',
                'x': x, 'y': y, 'z': z, 'build': build, 'state': state,
            })
    return issues
