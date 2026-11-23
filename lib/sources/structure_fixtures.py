"""Recorded structure fixtures for tests: the footbridge near MC 6872,-4194 (OSM way 28019377).

Not a test module. ``fixtures/structures/footbridge_28019377.json`` holds a
110 by 90 block window of the railway trial extracts: through rail ways and
every bridged or tunnelled road in it (clipped to the window), plus the cached
lidar columns. The lidar shows no deck there: the footbridge columns read as
ground and tree crowns (kind 0 and 2) with surface heights 15 to 22 over a DTM of 15.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'structures' / 'footbridge_28019377.json'
FOOTBRIDGE_ID = '28019377'


def _unrle(runs, shape, dtype):
    flat = [v for v, n in runs for _ in range(n)]
    return np.array(flat, dtype=dtype).reshape(shape)


def load_footbridge() -> dict:
    """The recorded window.

    @returns: ``window``, ``rail_ways`` and ``roads`` (``osm_id``, ``tags``, ``points``
        in Minecraft x, z) and ``ground`` (``x0``, ``z0``, ``terrain``, ``h``,
        ``kind``, ``have`` arrays indexed [x, z])
    """
    raw = json.loads(FIXTURE.read_text())
    g = raw['ground']
    shape = tuple(g['shape'])
    ground = {'x0': g['x0'], 'z0': g['z0'],
              'terrain': _unrle(g['terrain'], shape, np.int16),
              'h': _unrle(g['h'], shape, np.int16),
              'kind': _unrle(g['kind'], shape, np.int16),
              'have': _unrle(g['have'], shape, bool)}
    return {'window': tuple(raw['window']), 'rail_ways': raw['rail_ways'],
            'roads': raw['roads'], 'ground': ground}
