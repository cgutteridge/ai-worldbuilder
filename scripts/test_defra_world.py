#!/usr/bin/env python3
"""Unit tests for defra_world and lidar_heightmap.ReferenceWorld (synthetic tiles, fake OSM index, no world needed)."""
from __future__ import annotations

import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parent.parent
for _p in (_ROOT / "shims", _ROOT / "scripts"):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

import tempfile
import unittest
from pathlib import Path

import numpy as np

import defra_lidar as dl
import defra_world as dw
import lidar_heightmap as lh
from osgb import load_offsets
from test_defra_lidar import write_tif

CELL = (0, -1)                                    # blocks x 0..127, z -128..-1
GROUND_DTM = 20


class FakeIdx:
    """Footprint columns x 10..19, z -50..-41, as OSMIndex.cells() would give them."""
    def cells(self):
        return {(x, z): 0 for x in range(10, 20) for z in range(-50, -40)}


class DefraWorldTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        o = load_offsets()
        # tile: x -2..131, z -130..3  (134 x 134 pixels), pixel row r is z = -130 + r, column c is x = -2 + c
        dtm = np.full((134, 134), float(GROUND_DTM), dtype=np.float32)
        dsm = dtm.copy()

        def raise_(x0, x1, z0, z1, h):
            dsm[z0 + 130:z1 + 131, x0 + 2:x1 + 3] = GROUND_DTM + h

        raise_(9, 20, -51, -40, 6)                # building, plus a one-block bilinear skirt
        raise_(11, 12, -49, -48, 1)               # inside the building but only 1 m tall at these pixels: forced to 3? (skirt covers it)
        raise_(40, 42, -80, -78, 5)               # a 3 x 3 m tall thing outside every footprint: drawn as leaves
        for name, arr in (('DTM', dtm), ('DSM', dsm)):
            write_tif(Path(cls.tmp.name) / name / 't.tif', arr, o['OFFSET_E'] - 2, o['OFFSET_N'] + 130)
        cls.lidar = dl.DefraLidar(Path(cls.tmp.name), download=False)
        real_missing = cls.lidar.missing
        # the test tile is not a 5 km DEFRA tile, so DTM/DSM count as cached; DSM_FIRST is genuinely absent here
        cls.lidar.missing = lambda model, *a, **k: real_missing(model, *a, **k) if model == 'DSM_FIRST' else []
        cls.world = dw.DefraWorld(cls.lidar, FakeIdx())
        cls.sea = int(o['SEA_LEVEL'])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_building_column_has_wall_top_and_carpet(self):
        hm = self.world.heightmap(CELL)
        i, j = 15 - hm['x0'], -45 - hm['z0']
        top = self.sea + GROUND_DTM + 6
        self.assertEqual((int(hm['kind'][i, j]), int(hm['h'][i, j]), int(hm['base'][i, j]), int(hm['roof'][i, j])),
                         (lh.ROOF, top, top, top + 1))

    def test_footprint_column_is_forced_to_three_high(self):
        hm = self.world.heightmap(CELL)
        # footprint column whose lidar feature is 0 (x 10, z -41 sits at the south skirt edge): still 3 above ground
        i, j = 19 - hm['x0'], -41 - hm['z0']
        self.assertGreaterEqual(int(hm['h'][i, j]) - (self.sea + GROUND_DTM), dw.BUILDING_MIN)

    def test_tall_thing_outside_footprint_is_a_tree_standing_on_ground(self):
        hm = self.world.heightmap(CELL)
        i, j = 41 - hm['x0'], -79 - hm['z0']
        self.assertEqual(int(hm['kind'][i, j]), lh.TREE)
        self.assertEqual(int(hm['base'][i, j]), self.sea + GROUND_DTM)
        self.assertEqual(int(hm['h'][i, j]), self.sea + GROUND_DTM + 5)

    def test_flat_ground_is_ground(self):
        hm = self.world.heightmap(CELL)
        i, j = 90 - hm['x0'], -100 - hm['z0']
        self.assertEqual((int(hm['kind'][i, j]), int(hm['h'][i, j]), int(hm['roof'][i, j])), (lh.GROUND, self.sea + GROUND_DTM, 0))

    def test_block_scan_gives_the_same_columns_as_the_heightmap(self):
        hm = self.world.heightmap(CELL)
        for x, z in [(15, -45), (19, -41), (41, -79), (90, -100), (10, -50), (40, -80), (5, -5)]:
            got = lh.column(self.world, x, z)
            i, j = x - hm['x0'], z - hm['z0']
            want = (int(hm['h'][i, j]), int(hm['base'][i, j]), int(hm['roof'][i, j]), int(hm['kind'][i, j]))
            self.assertEqual(got, want, f'column {x},{z}')

    def test_ground_is_the_solid_top_under_leaves(self):
        self.assertEqual(self.world.ground(41, -79, bottom=0), self.sea + GROUND_DTM)

    def test_uncached_cell_refuses_with_the_fetch_command(self):
        w = dw.DefraWorld(dl.DefraLidar(Path(self.tmp.name) / 'empty', download=False), FakeIdx())
        with self.assertRaises(LookupError) as c:
            w.heightmap(CELL)
        self.assertIn('defra_lidar.py fetch --cell 0,-1', str(c.exception))


class FirstReturnTest(unittest.TestCase):
    """With the first-return tile cached, trees are foliage (first well above last), and tall solid things are structures."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        o = load_offsets()
        dtm = np.full((134, 134), float(GROUND_DTM), dtype=np.float32)
        last, first = dtm.copy(), dtm.copy()

        def put(arr, x0, x1, z0, z1, h):
            arr[z0 + 130:z1 + 131, x0 + 2:x1 + 3] = GROUND_DTM + h

        put(last, 9, 20, -51, -40, 6)
        put(first, 9, 20, -51, -40, 6)           # roof: first return = last return
        put(last, 40, 44, -80, -76, 5)
        put(first, 40, 44, -80, -76, 5)          # wall: same, outside every footprint -> structure
        put(last, 70, 74, -62, -58, 4)
        put(first, 70, 74, -62, -58, 10)         # crown: laser reaches 6 m further down than the top -> tree to 10 m
        put(last, 100, 104, -30, -26, 1)
        put(first, 100, 104, -30, -26, 2)        # low scrub: below the 3 m threshold -> plain ground
        for name, arr in (('DTM', dtm), ('DSM', last), ('DSM_FIRST', first)):
            write_tif(Path(cls.tmp.name) / name / 't.tif', arr, o['OFFSET_E'] - 2, o['OFFSET_N'] + 130)
        lidar = dl.DefraLidar(Path(cls.tmp.name), download=False)
        lidar.missing = lambda *a, **k: []
        cls.world = dw.DefraWorld(lidar, FakeIdx())
        cls.sea = int(o['SEA_LEVEL'])
        cls.hm = cls.world.heightmap(CELL)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def kind(self, x, z):
        return int(self.hm['kind'][x - self.hm['x0'], z - self.hm['z0']])

    def test_uses_first_return(self):
        self.assertTrue(self.world.trees_from_first_return(CELL))

    def test_foliage_is_a_tree_to_the_first_return_crown_top(self):
        self.assertEqual(self.kind(72, -60), lh.TREE)
        i, j = 72 - self.hm['x0'], -60 - self.hm['z0']
        self.assertEqual(int(self.hm['h'][i, j]), self.sea + GROUND_DTM + 10)
        self.assertEqual(int(self.hm['base'][i, j]), self.sea + GROUND_DTM)

    def test_wall_is_a_structure_standing_solid(self):
        self.assertEqual(self.kind(42, -78), lh.STRUCTURE)
        i, j = 42 - self.hm['x0'], -78 - self.hm['z0']
        self.assertEqual(int(self.hm['h'][i, j]), int(self.hm['base'][i, j]))
        self.assertEqual(int(self.hm['base'][i, j]), self.sea + GROUND_DTM + 5)

    def test_roof_stays_a_roof_and_scrub_stays_ground(self):
        self.assertEqual(self.kind(15, -45), lh.ROOF)
        self.assertEqual(self.kind(102, -28), lh.GROUND)

    def test_block_scan_agrees_for_each_kind(self):
        for x, z in [(72, -60), (42, -78), (15, -45), (102, -28)]:
            i, j = x - self.hm['x0'], z - self.hm['z0']
            want = (int(self.hm['h'][i, j]), int(self.hm['base'][i, j]), int(self.hm['roof'][i, j]))
            self.assertEqual(lh.column(self.world, x, z)[:3], want, f'column {x},{z}')

    def test_ground_estimates_skip_trees_and_structures(self):
        # a ring of columns around a wall: lh._ground only counts GROUND kind, so the wall top is not ground
        comp = {(x, z) for x in range(41, 44) for z in range(-79, -76)}
        self.assertEqual(lh._ground(self.hm, comp, reach=1), self.sea + GROUND_DTM)


class FakeDefra:
    def __init__(self, covered):
        self.covered = covered

    def covers(self, cell):
        return cell in self.covered

    def heightmap(self, cell):
        return {'cell': cell, 'source': 'defra'}

    def block(self, x, y, z):
        return 'minecraft:bricks'

    def ground(self, x, z, top=140, bottom=40):
        return 99


class ReferenceWorldTest(unittest.TestCase):
    def ref(self):
        return lh.ReferenceWorld(composite=FakeDefra({(0, 0)}))

    def test_answers_a_cached_cell_from_composite(self):
        r = self.ref()
        self.assertEqual(r.source_of((0, 0)), 'composite')
        self.assertEqual(r.block(10, 5, 10), 'minecraft:bricks')
        self.assertEqual(r.ground(10, 10), 99)

    def test_uncached_cell_is_an_error_not_a_fallback(self):
        r = self.ref()
        for call in (lambda: r.uses_composite((5, 5)), lambda: r.heightmap((5, 5)), lambda: r.block(300, 5, 300)):
            with self.assertRaises(LookupError):
                call()

    def test_has_no_baseline_save_path(self):
        self.assertFalse(hasattr(lh, 'pristine_world'))
        self.assertFalse(hasattr(self.ref(), 'fallback'))

    def test_heightmap_function_prefers_a_world_with_its_own_map(self):
        self.assertEqual(
            lh.heightmap(self.ref(), (0, 0)),
            {'cell': (0, 0), 'source': 'defra'},
        )


if __name__ == '__main__':
    unittest.main()
