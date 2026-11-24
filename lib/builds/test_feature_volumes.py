#!/usr/bin/env python3
"""Seam: ``volumes[label]`` through ``Build`` (#456): masses outside the map way, with refusals."""

from __future__ import annotations

from contextlib import redirect_stdout
from unittest import mock
import io
import unittest

import numpy as np

from feature_build_fixtures import (
    _StoneWorld,
    build_spec,
    start_lidar_fixture as setUpModule,
    stop_lidar_fixture as tearDownModule,
    write_spec,
)
import cellmath
import feature_build as F
import feature_spec as FS
import feature_volumes as FV


WAY = {(x, z) for x in range(10, 15) for z in range(20, 25)}
# a 4 x 3 rear store east of the way: columns x 15..18, z 21..23
STORE = [[15, 21], [19, 21], [19, 24], [15, 24]]   # a column is inside when its centre is
STORE_COLS = {(x, z) for x in range(15, 19) for z in range(21, 24)}


def _grids(x0=13, z0=19, width=8, depth=7, top=52.5, low=()):
    """Lidar for the store: ground Y 50, surface ``top`` (metres = Y here), ``low`` columns bare."""
    dsm = np.full((depth, width), 50.0)
    for x, z in STORE_COLS:
        if 0 <= x - x0 < width and 0 <= z - z0 < depth:
            dsm[z - z0, x - x0] = 50.0 if (x, z) in low else top
    return {'dsm': dsm, 'ground_y': np.full((depth, width), 50), 'x0': x0, 'z0': z0}


def _spec(volumes=None, kind='flat'):
    spec = build_spec(storeys=2, kind='flat', material='smooth_stone')
    if volumes is not None:
        spec['volumes'] = {'A': volumes}
    return spec


def _store(**over):
    row = {'polygon': STORE, 'kind': 'flat', 'storeys': 1}
    row.update(over)
    return row


class VolumeBuildTest(unittest.TestCase):
    def _build(self, spec, zone_data=None, footprints='way', **kw):
        builder = F.Build(spec, footprints={'A': WAY} if footprints == 'way' else footprints,
                          world=_StoneWorld(), zone_data=zone_data or {'A.v1': _grids()}, **kw)
        buf = io.StringIO()
        with mock.patch.object(FV, 'y_to_metres_aod', side_effect=float), redirect_stdout(buf):
            plan = builder.build()
        return builder, plan, buf.getvalue()

    def test_no_key_plans_exactly_as_before(self):
        _b, bare, bare_out = self._build(_spec())
        _b, empty, empty_out = self._build(_spec(volumes=None) | {'volumes': {}})
        self.assertEqual(bare.cells, empty.cells)
        self.assertEqual(bare.outside_footprint_ok, empty.outside_footprint_ok)
        self.assertNotIn('volume', bare_out)
        self.assertEqual(bare_out, empty_out)

    def test_flat_volume_beyond_the_way_is_built_with_its_own_storeys(self):
        builder, plan, out = self._build(_spec([_store()]))
        f = builder.features[-1]
        self.assertEqual((f.no, f.volume_of, f.cells), ('A.v1', 'A', STORE_COLS))
        self.assertEqual(f.b, 50)
        eave = 50 + FS.STOREY_H
        for x, z in STORE_COLS:
            self.assertIn('smooth_stone_slab', plan.cells[(x, eave, z)])
        self.assertEqual(plan.cells[(18, 51, 22)], 'minecraft:bricks')   # outer wall
        self.assertEqual(plan.cells[(16, 51, 22)], 'minecraft:air')      # inside the store
        self.assertNotIn('slab', plan.cells.get((16, eave + 1, 22), ''))   # nothing above one storey
        self.assertIn('A.v1 volume of A: 12 columns flat smooth_stone storeys 1; floor Y 50, roof Y 53', out)
        self.assertIn('lidar mass median 2.5 m; touches the map way', out)
        self.assertIn('volumes outside the map way', plan.outside_footprint_ok)
        # plain mass: no windows or doors on the store
        self.assertFalse(any('glass' in plan.cells.get((x, y, z), '') or '_door' in plan.cells.get((x, y, z), '')
                             for x, z in STORE_COLS for y in range(50, eave)))

    def test_pitched_volume_takes_a_material_and_rises_above_its_eave(self):
        _b, plan, out = self._build(_spec([_store(kind='pitched', material='brick', storeys=2)]))
        eave = 50 + 2 * FS.STOREY_H
        stairs = [q for q, st in plan.cells.items() if 'brick_stairs' in st and (q[0], q[2]) in STORE_COLS]
        self.assertTrue(stairs)
        self.assertTrue(all(y >= eave for _x, y, _z in stairs))
        self.assertIn('A.v1 volume of A: 12 columns pitched brick storeys 2', out)

    def test_columns_with_no_lidar_mass_are_refused_with_the_reason(self):
        low = {(18, 21), (18, 22), (18, 23)}
        builder, plan, out = self._build(_spec([_store()]), zone_data={'A.v1': _grids(low=low)})
        self.assertEqual(builder.features[-1].cells, STORE_COLS - low)
        self.assertIn('A.v1 volume refused 3 columns: lidar shows no mass (under 2 m above ground): '
                      '18,21 18,22 18,23', out)
        self.assertNotIn((18, 53, 22), {q for q, st in plan.cells.items() if 'slab' in st})
        # a lower threshold accepts a lower mass
        builder, _plan, _out = self._build(
            _spec([_store(mass_m=0.5)]), zone_data={'A.v1': _grids(top=51.0)})
        self.assertEqual(builder.features[-1].cells, STORE_COLS)

    def test_polygon_over_the_way_leaves_those_columns_to_the_way(self):
        poly = [[13, 21], [19, 21], [19, 24], [13, 24]]
        builder, _plan, out = self._build(_spec([_store(polygon=poly)]))
        self.assertEqual(builder.features[-1].cells, STORE_COLS)
        self.assertEqual(builder.features[0].cells, WAY)
        self.assertIn("A.v1 volume refused 6 columns: inside this build's map way (left to A)", out)

    def test_column_in_a_neighbouring_map_way_is_refused_and_outside_guard_keeps_the_store(self):
        class Idx:
            buildings = [(0, {'osm_way_id': '99'}), (1, {'osm_way_id': '7'})]
            streets, street_props = [], []

            def cells(self):
                cells = {c: 0 for c in WAY}
                cells.update({(18, z): 1 for z in range(20, 25)})
                return cells

        eave = 50 + FS.STOREY_H
        guard = {'outside': {(16, eave, 22): ('smooth_stone_slab', None)}, 'blind': []}
        with mock.patch.object(F.osm_footprints, 'OSMIndex', return_value=Idx()), \
                mock.patch.object(F.osm_footprints, 'guard', return_value=guard):
            builder, plan, out = self._build(_spec([_store()]), footprints=None)
        self.assertEqual(builder.features[-1].cells, STORE_COLS - {(18, 21), (18, 22), (18, 23)})
        self.assertIn('A.v1 volume refused 3 columns: inside another map way: 18,21 18,22 18,23', out)
        self.assertFalse(any((x, z) == (18, 22) and y > 50 and st != 'minecraft:air'
                             for (x, y, z), st in plan.cells.items()))
        self.assertIn('smooth_stone_slab', plan.cells[(16, eave, 22)])

    def test_column_another_feature_build_owns_is_refused_but_ground_is_not(self):
        from journal_testkit import JournalWorld
        with JournalWorld() as world:
            world.build('next_door_shed', 'feature', {(17, 51, 22): 'minecraft:oak_planks'})
            world.build('yard_paving', 'ground', {(16, 50, 22): 'minecraft:gray_concrete'})
            builder, _plan, out = self._build(_spec([_store()]))
        self.assertEqual(builder.features[-1].cells, STORE_COLS - {(17, 22)})
        self.assertIn('A.v1 volume refused 1 columns: owned by feature build next_door_shed: 17,22', out)
        self.assertNotIn('yard_paving', out)

    def test_cross_cell_volume_is_built_and_stamps_the_neighbour_but_never_moves_the_home_cell(self):
        size = cellmath.CELL
        way = {(x, z) for x in range(size - 5, size) for z in range(20, 25)}
        poly = [[size, 21], [size + 4, 21], [size + 4, 24], [size, 24]]
        cols = {(x, z) for x in range(size, size + 4) for z in range(21, 24)}
        dsm = np.full((7, 8), 50.0)
        for x, z in cols:
            dsm[z - 19, x - (size - 2)] = 52.5
        data = {'A.v1': {'dsm': dsm, 'ground_y': np.full((7, 8), 50), 'x0': size - 2, 'z0': 19}}
        builder, plan, _out = self._build(_spec([_store(polygon=poly)]), zone_data=data, footprints={'A': way})
        self.assertEqual(builder.features[-1].cells, cols)
        self.assertIn((1, 0), plan._input_cells)
        self.assertEqual(cellmath.majority_cell(plan.cells), (0, 0))
        # a store larger than the way would move the plan's home cell: refused whole
        big = [[size, 20], [size + 8, 20], [size + 8, 26], [size, 26]]
        big_cols = {(x, z) for x in range(size, size + 8) for z in range(20, 26)}
        dsm = np.full((8, 10), 50.0)
        for x, z in big_cols:
            dsm[z - 19, x - (size - 1)] = 52.5
        data = {'A.v1': {'dsm': dsm, 'ground_y': np.full((8, 10), 50), 'x0': size - 1, 'z0': 19}}
        builder, plan, out = self._build(_spec([_store(polygon=big)]), zone_data=data, footprints={'A': way})
        self.assertEqual([f.no for f in builder.features], ['A'])
        self.assertIn("A.v1 volume refused 48 columns: would move the plan's home cell from 0,0 to 1,0", out)
        self.assertIn('A.v1 volume of A: no columns left; not built', out)


class VolumeSpecTest(unittest.TestCase):
    def _load(self, volumes):
        spec = _spec(volumes)
        return FS.load_spec(write_spec(self, spec))

    def test_valid_volume_loads_and_absent_key_defaults_empty(self):
        self.assertEqual(self._load([_store(material='brick', mass_m=1.5, source='aerial')])['volumes']['A'][0]['kind'],
                         'flat')
        self.assertEqual(FS.load_spec(write_spec(self, _spec()))['volumes'], {})

    def test_bad_volumes_refuse(self):
        for bad in ([_store(kind='hip')], [_store(storeys=0)], [_store(polygon=[[0, 0], [1, 1]])],
                    [_store(mass_m=0)], [_store(colour='red')], []):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                self._load(bad)
        spec = _spec()
        spec['volumes'] = {'B': [_store()]}
        with self.assertRaises(SystemExit):
            FS.load_spec(write_spec(self, spec))


if __name__ == '__main__':
    unittest.main()
