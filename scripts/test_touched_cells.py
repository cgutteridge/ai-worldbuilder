#!/usr/bin/env python3
"""Touched-cells cache + Q-195 ownership overlap vs site_kit.built_cells / owned_in.

  python3 -m unittest scripts.test_touched_cells
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import site_kit
import touched_cells as tc

AIR, STONE, DIRT = 'minecraft:air', 'minecraft:stone', 'minecraft:coarse_dirt'


class OwnershipOverlapTest(unittest.TestCase):
    """Q-195: owned cells must appear in that build's touched columns; owned_in == merge."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / 'out'
        self.out.mkdir()
        self.cache = self.tmp / 'touched-cache.json'
        self.owners = self.tmp / 'owners.sqlite'
        self.cells_cache = self.tmp / 'cache'
        self.t = 1_700_000_000
        patches = [
            mock.patch.object(tc, 'OUT', self.out),
            mock.patch.object(tc, 'TOUCH_CACHE', self.cache),
            mock.patch.object(site_kit, 'OUT', self.out),
            mock.patch.object(site_kit, 'OWNERS_DB', self.owners),
            mock.patch.object(site_kit, 'CELLS_CACHE', self.cells_cache),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(tc._TOUCHED_MEMO.clear)

    def write(self, name, cells):
        f = self.out / f'{name}-cells.json'
        f.write_text(json.dumps({f'{x},{y},{z}': s for (x, y, z), s in cells.items()}))
        self.t += 10
        os.utime(f, (self.t, self.t))

    def test_owned_cells_agree_with_touched_and_owners_index(self):
        self.write('a', {(0, 0, 0): STONE, (1, 0, 0): STONE, (2, 0, 0): STONE})
        self.write('b', {(0, 0, 0): DIRT, (1, 0, 0): AIR, (3, 0, 0): AIR})
        cells = [(0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0)]
        self.assertEqual(tc.ownership_overlap_issues(exclude=(), within=cells), [])
        by_build = tc.touched_by_build(exclude=())
        self.assertEqual(by_build['a'], {(0, 0), (1, 0), (2, 0)})
        self.assertEqual(by_build['b'], {(0, 0), (1, 0), (3, 0)})
        owned = site_kit.built_cells(exclude=())
        self.assertEqual(owned[(0, 0, 0)], ('b', DIRT))
        self.assertEqual(owned[(2, 0, 0)], ('a', STONE))
        self.assertNotIn((1, 0, 0), owned)

    def test_reports_when_touched_set_omits_an_owned_column(self):
        self.write('a', {(5, 1, 7): STONE})
        with mock.patch.object(tc, 'touched_by_build', return_value={'a': set()}):
            issues = tc.ownership_overlap_issues(exclude=(), within=[(5, 1, 7)])
        self.assertEqual(
            [i for i in issues if i.get('kind') == 'owned_column_not_touched'],
            [{'kind': 'owned_column_not_touched', 'x': 5, 'y': 1, 'z': 7,
              'build': 'a', 'state': STONE}],
        )

    def test_reverted_and_cell_marker_excluded_from_touch(self):
        self.write('cell_marker_48_-44', {(9, 0, 9): STONE})
        name = next(iter(tc.REVERTED))
        self.write(name, {(8, 0, 8): STONE})
        self.write('keep', {(7, 0, 7): STONE})
        by_build = tc.touched_by_build()
        self.assertNotIn('cell_marker_48_-44', by_build)
        self.assertNotIn(name, by_build)
        self.assertEqual(by_build['keep'], {(7, 7)})


class EncodeRunsTest(unittest.TestCase):
    def test_round_trip(self):
        cells = {(0, 0), (1, 0), (2, 0), (5, 0), (-3, -7), (-2, -7), (4, 2)}
        self.assertEqual(tc.decode_runs(tc.encode_runs(cells)), cells)


if __name__ == '__main__':
    unittest.main()
