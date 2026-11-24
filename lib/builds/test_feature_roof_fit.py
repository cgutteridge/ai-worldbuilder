"""Tests for the feature-roof octant fit ``roofs[label].stairs: "fit"`` (#472).

The roof is designed as a half-block-cell volume and each block matched to it. Synthetic lidar
grids and footprints only, built through ``feature_build.Build``.
"""

from __future__ import annotations

from contextlib import redirect_stdout
from unittest import mock
import io
import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from block_store import state_props
from feature_build_fixtures import (
    _StoneWorld,
    start_lidar_fixture as setUpModule,
    stop_lidar_fixture as tearDownModule,
)
import feature_build as F
import feature_roof_fit as RF
import feature_roof_zones as FZ
import feature_spec as FS
from roof_geometry import ROOF_DELTA
import roof_lint
from roof_lint import ridge_jogs, stair_shape_mismatches
from roof_octant import stair_cells


def _spec(stairs=None, zones=None):
    roof = {'kind': 'pitched', 'material': 'stone_brick'}
    if stairs is not None:
        roof['stairs'] = stairs
    spec = {
        'name': 'unit_roof_fit',
        'ways': {'99': 'A'},
        'storeys': {'A': 2},
        'roofs': {'A': roof},
        'groups': [{'nos': ['A'], 'front': 'S', 'mode': 'each'}],
        'modifies': [],
        'floor_min': 40,
        'step_max': 4,
        'door_frame': True,
        'keep_edits': False,
        'wall': 'bricks',
        'floor': 'smooth_stone',
        'ledge_stair': False,
    }
    if zones is not None:
        spec['zones'] = {'A': zones}
    return spec


def _zones(regions, axis_deg=0):
    return {'from': 'lidar', 'levels': 'auto', 'bucket': 4, 'axis_deg': axis_deg,
            'regions': regions, 'surface': 'fit'}


def _zone_data(cells, height):
    """Lidar grids over ``cells`` (one-block margin): ground at 40, DSM from ``height(x, z)``."""
    x0 = min(x for x, _ in cells) - 1
    z0 = min(z for _, z in cells) - 1
    width = max(x for x, _ in cells) - x0 + 2
    depth = max(z for _, z in cells) - z0 + 2
    fine = np.full((depth, width), 40.0)
    for x, z in cells:
        fine[z - z0, x - x0] = height(x, z)
    return {'A': {'x0': x0, 'z0': z0, 'dsm': np.round(fine), 'dsm_fine': fine,
                  'ground_y': np.full((depth, width), 40)}}


def _build(spec, cells, data=None):
    """Builder, plan and dry-run text of one feature build (``metres_aod_to_y`` is the identity)."""
    out = io.StringIO()
    with mock.patch.object(FZ, 'metres_aod_to_y', side_effect=lambda m: int(m)), redirect_stdout(out):
        builder = F.Build(spec, footprints={'A': set(cells)}, world=_StoneWorld(), zone_data=data)
        plan = builder.build()
    return builder, plan, out.getvalue()


def _roof_tops(plan):
    """``{(x, z): (y, state)}``: the top stone-brick roof block of each column."""
    top = {}
    for (x, y, z), s in plan.cells.items():
        if 'stone_brick' in s and ((x, z) not in top or y > top[(x, z)][0]):
            top[(x, z)] = (y, s)
    return top


def _quarter_tops(tops):
    """Top height of every quarter of every roof column, in half blocks."""
    q = {}
    for (x, z), (y, s) in tops.items():
        if '_stairs[' in s:
            p = state_props(s)
            cells = stair_cells(p['facing'], p['shape'])
        for dx in (0, 1):
            for dz in (0, 1):
                if '_stairs[' in s:
                    h = 2 * y + (2 if (dx, 1, dz) in cells else 1)
                elif '_slab[' in s:
                    h = 2 * y + 1
                else:
                    h = 2 * y + 2
                q[(2 * x + dx, 2 * z + dz)] = h
    return q


def _notches(tops):
    """Slots one or two quarters wide, lower than both sides along a line: gaps where stairs meet badly."""
    q = _quarter_tops(tops)
    out = set()
    for (a, b), h in q.items():
        for da, db in ((1, 0), (0, 1)):
            before = q.get((a - da, b - db))
            if before is None or before <= h:
                continue
            after = q.get((a + da, b + db))
            if after is not None and after > h:
                out.add((a // 2, b // 2))
            elif after == h and q.get((a + 2 * da, b + 2 * db), h) > h:
                out.update({(a // 2, b // 2), ((a + da) // 2, (b + db) // 2)})
    return out


def _stairs(tops):
    return {c: state_props(s)['facing'] for c, (_y, s) in tops.items() if '_stairs[' in s}


def _common_checks(case, plan, tops):
    """No gaps where stairs meet, no top slab, shapes match the game, no stair faces off the roof."""
    case.assertEqual(_notches(tops), set())
    case.assertEqual(stair_shape_mismatches(plan.cells), [])
    case.assertEqual(ridge_jogs(plan.cells), [])
    case.assertFalse([p for p, s in plan.cells.items() if '_slab[' in s and 'type=top' in s])
    for (x, z), facing in _stairs(tops).items():
        dx, dz = ROOF_DELTA[facing]
        case.assertIn((x + dx, z + dz), tops, f'{(x, z)} faces {facing}, off the roof')


def _rotated_region(centre, deg, u0, u1, v0, v1):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [(round(centre[0] + u * c - v * s, 2), round(centre[1] + u * s + v * c, 2))
            for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]


class SteppedFlatAndPitchTest(unittest.TestCase):
    """Square-on roof: a low flat deck, a deck one block higher, then a taller gable (ridge across x)."""

    @classmethod
    def setUpClass(cls):
        cls.cells = {(x, z) for x in range(10, 36) for z in range(20, 30)}
        rng = np.random.default_rng(4)
        noise = {c: float(rng.normal(0, 0.1)) for c in cls.cells}

        def height(x, z):
            if x < 16:
                h = 44.2                                     # low flat deck
            elif x < 22:
                h = 45.2                                     # flat deck one block higher
            else:
                h = 53.0 - abs(x + 0.5 - 29.0)               # gable 1 m/m, ridge at x 28.5
            return h + noise[(x, z)]
        cls.data = _zone_data(cls.cells, height)
        regions = [[(10, 20), (16, 20), (16, 30), (10, 30)],
                   [(16, 20), (22, 20), (22, 30), (16, 30)],
                   [(22, 20), (36, 20), (36, 30), (22, 30)]]
        cls.regions = regions
        cls.off = _build(_spec(zones=_zones(regions)), cls.cells, cls.data)
        cls.on = _build(_spec('fit', zones=_zones(regions)), cls.cells, cls.data)

    def test_dry_run_names_the_fit(self):
        self.assertIn('A roof stairs fit: surface: fitted slopes on', self.on[2])
        self.assertNotIn('A roof stairs fit', self.off[2])

    def test_flat_layers_and_the_one_block_step_are_full_blocks(self):
        tops = _roof_tops(self.on[1])
        low = {tops[(x, z)][0] for x in range(10, 16) for z in range(20, 30)}
        high = {tops[(x, z)][0] for x in range(16, 22) for z in range(20, 30)}
        self.assertEqual(len(low), 1)
        self.assertEqual(high, {low.pop() + 1})
        for x in range(10, 22):
            for z in range(20, 30):
                self.assertEqual(tops[(x, z)][1].removeprefix('minecraft:'), 'stone_bricks', (x, z))

    def test_gable_stairs_climb_to_the_ridge(self):
        stairs = {c: f for c, f in _stairs(_roof_tops(self.on[1])).items() if c[0] >= 22}
        self.assertTrue(stairs)
        for (x, _z), facing in stairs.items():
            self.assertEqual(facing, 'east' if x < 28.5 else 'west', (x, _z))

    def test_no_gaps_top_slabs_or_outward_stairs(self):
        _common_checks(self, self.on[1], _roof_tops(self.on[1]))

    def test_without_the_key_the_fit_never_runs(self):
        with mock.patch.object(F, 'fit_roof', side_effect=AssertionError('fit without the key')):
            again = _build(_spec(zones=_zones(self.regions)), self.cells, self.data)[1]
        self.assertEqual(again.cells, self.off[1].cells)


class RotatedLTest(unittest.TestCase):
    """An L of two gable wings 30 degrees off the grid, each wing its own fitted region."""

    ANGLE = 30.0
    CENTRE = (100.0, 200.0)

    @classmethod
    def setUpClass(cls):
        a = math.radians(cls.ANGLE)
        c, s = math.cos(a), math.sin(a)

        def uv(x, z):
            px, pz = x + 0.5 - cls.CENTRE[0], z + 0.5 - cls.CENTRE[1]
            return px * c + pz * s, -px * s + pz * c

        cls.cells = set()
        for x in range(80, 125):
            for z in range(180, 225):
                u, v = uv(x, z)
                if (0 <= u <= 16 and 0 <= v <= 7) or (0 <= u <= 7 and 7 < v <= 16):
                    cls.cells.add((x, z))

        def height(x, z):
            u, v = uv(x, z)
            if v <= 7:
                return 52.0 - 0.8 * abs(v - 3.5)             # wing along u, ridge along it at v 3.5
            return 52.0 - 0.8 * abs(u - 3.5)                 # wing along v, ridge at u 3.5
        cls.data = _zone_data(cls.cells, height)
        regions = [_rotated_region(cls.CENTRE, cls.ANGLE, -0.5, 16.5, -0.5, 7.0),
                   _rotated_region(cls.CENTRE, cls.ANGLE, -0.5, 7.0, 7.0, 16.5)]
        cls.on = _build(_spec('fit', zones=_zones(regions, cls.ANGLE)), cls.cells, cls.data)

    def test_both_wings_take_their_fitted_slopes(self):
        out = self.on[2]
        self.assertEqual(out.count('surface gable ridge along the axis')
                         + out.count('surface gable ridge across the axis'), 2, out)
        self.assertIn('A roof stairs fit: surface: fitted slopes on', out)

    def test_no_gaps_top_slabs_or_outward_stairs(self):
        _common_checks(self, self.on[1], _roof_tops(self.on[1]))

    def test_interior_stairs_climb_the_fitted_surface(self):
        builder, plan, _out = self.on
        f = builder.features[0]
        tops = _roof_tops(plan)
        climbing = 0
        for (x, z), facing in _stairs(tops).items():
            if any((x + dx, z + dz) not in tops for dx, dz in ROOF_DELTA.values()):
                continue
            gx, gz = f.zone_surface[(x, z)].gradient(x, z)
            dx, dz = ROOF_DELTA[facing]
            self.assertGreater(gx * dx + gz * dz, 0, f'{(x, z)} faces {facing}, downhill')
            climbing += 1
        self.assertGreater(climbing, 10)


class UnzonedHipTest(unittest.TestCase):
    """Footprints without zones: a hip on the footprint's rectangle, else along the outline."""

    @staticmethod
    def _cells(deg, parts):
        c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        out = set()
        for x in range(-30, 31):
            for z in range(-30, 31):
                u = (x + 0.5) * c + (z + 0.5) * s
                v = -(x + 0.5) * s + (z + 0.5) * c
                if any(u0 <= u <= u1 and v0 <= v <= v1 for u0, u1, v0, v1 in parts):
                    out.add((x, z))
        return out

    def _check(self, cells, source):
        builder, plan, out = _build(_spec('fit'), cells)
        self.assertIn(f'A roof stairs fit: {source}', out)
        tops = _roof_tops(plan)
        _common_checks(self, plan, tops)
        eave = builder.features[0].b + builder.features[0].storeys * F.STOREY_H
        # the overhang rim is one layer at the eave
        for x, z in RF.overhang_rim(cells, set(tops)):
            ys = [y for (a, y, b), s in plan.cells.items() if (a, b) == (x, z) and 'stone_brick' in s]
            self.assertEqual(ys, [eave], (x, z))
        _b, default, _ = _build(_spec(), cells)
        self.assertLess(len(_notches(tops)), max(1, len(_notches(_roof_tops(default)))))
        return tops

    def test_rotated_rectangle_hip(self):
        self._check(self._cells(45, [(-10, 10, -5.5, 5.5)]), 'hip on a rectangle at 45 degrees')

    def test_rotated_l_follows_the_outline(self):
        tops = self._check(self._cells(30, [(0, 14, 0, 6), (0, 6, 6, 13)]), 'hip following the outline')
        self.assertGreater(len(_stairs(tops)), 20)


class FitUnitTest(unittest.TestCase):
    """``feature_roof_fit`` on hand-made surfaces."""

    COLS = {(x, z) for x in range(5) for z in range(5)}

    def _fit(self, top):
        return RF.fit_roof(self.COLS, top, dict.fromkeys(self.COLS, 40), owned=self.COLS)

    def test_level_surfaces_are_full_blocks_or_bottom_slabs(self):
        full = self._fit(lambda c, px, pz: 41.0)
        self.assertEqual(set(full.blocks.values()), {('full',)})
        self.assertEqual(set(full.tops.values()), {40})
        slab = self._fit(lambda c, px, pz: 40.5)
        self.assertEqual(set(slab.blocks.values()), {('slab', 'bottom')})
        self.assertEqual(slab.half_heights()[(2, 2)], 79)
        self.assertEqual(full.half_heights()[(2, 2)], 80)

    def test_one_block_step_is_full_blocks(self):
        fit = self._fit(lambda c, px, pz: 41.0 if c[0] < 2 else 42.0)
        self.assertEqual(set(fit.blocks.values()), {('full',)})
        self.assertEqual({fit.tops[(1, 2)], fit.tops[(2, 2)]}, {40, 41})

    def test_slope_gets_stairs_facing_up_and_no_top_slab(self):
        fit = self._fit(lambda c, px, pz: 40.5 + 0.5 * px)
        facings = {b[1] for b in fit.blocks.values() if b[0] == 'stair'}
        self.assertEqual(facings, {'east'})
        self.assertNotIn(('slab', 'top'), set(fit.blocks.values()))
        self.assertIn('top blocks:', fit.summary('A'))

    def test_grooves_finds_a_slot_between_stairs(self):
        slot = {(0, 40, 0): ('stair', 'west', 'straight'), (1, 40, 0): ('slab', 'bottom'),
                (2, 40, 0): ('stair', 'east', 'straight')}
        self.assertEqual(RF.grooves(slot), set())          # tall sides outward: a valley, no slot
        slot[(0, 40, 0)], slot[(2, 40, 0)] = ('stair', 'east', 'straight'), ('stair', 'west', 'straight')
        self.assertEqual(RF.grooves(slot), {(1, 0)})       # tall sides on the slab: a one-quarter slot

    def test_first_layer_of_an_owned_column_is_never_air(self):
        fit = self._fit(lambda c, px, pz: 40.1)              # almost nothing to fill
        self.assertEqual(set(fit.tops), self.COLS)

    def test_heightfield_keeps_level_columns_and_volume_steps_crisp(self):
        halfs = {(0, 0): 80, (1, 0): 82, (2, 0): 82}
        top = RF.heightfield_top(halfs, level={(2, 0)}, volume={(0, 0): 'a', (1, 0): 'a', (2, 0): 'b'})
        self.assertLess(top((1, 0), 1.25, 0.25), 42.0)        # leans toward its lower neighbour
        self.assertEqual(top((1, 0), 1.75, 0.25), 42.0)       # the next column is another volume
        self.assertEqual(top((2, 0), 2.25, 0.25), 42.0)       # a level column stays level

    def test_skin_shows_through_a_stair_but_not_out_of_the_roof(self):
        fit = RF.RoofFit({(0, 40, 0): ('full',), (0, 41, 0): ('full',), (1, 40, 0): ('full',),
                          (1, 41, 0): ('stair', 'west', 'straight')},
                         {(0, 0): 41, (1, 0): 41}, 0, 0, 'test')
        self.assertEqual(fit.skin({(0, 0), (1, 0)}), set())
        fit = RF.RoofFit({(0, 40, 0): ('full',), (0, 41, 0): ('full',), (1, 40, 0): ('stair', 'west', 'straight')},
                         {(0, 0): 41, (1, 0): 40}, 0, 0, 'test')
        self.assertEqual(fit.skin({(0, 0), (1, 0)}), {(0, 40, 0)})


class CarpetGateTest(unittest.TestCase):
    """A fitted roof carpets only the flat-layer columns it tops with a full block."""

    @staticmethod
    def _feature(blocks):
        """Stub zoned feature: flat deck columns (0..3, 0) at half-height 10 (y 5), fitted ``blocks`` on top."""
        cols = [(x, 0) for x in range(4)]
        fit = mock.Mock(tops={c: 5 for c in cols}, blocks={(c[0], 5, c[1]): blocks[c] for c in cols})
        return mock.Mock(
            roof={'kind': 'pitched', 'material': 'stone_brick', 'finish': 'white_carpet'},
            zone_roof={c: 5 for c in cols}, zone_roof_half={c: 10 for c in cols},
            zone_flat_cells=set(cols), zone_surface={}, zone_fit=fit)

    def test_no_carpet_above_a_slab_or_stair_top(self):
        f = self._feature({(0, 0): ('full',), (1, 0): ('slab', 'bottom'),
                           (2, 0): ('stair', 'east', 'straight'), (3, 0): ('full',)})
        carpets = F.Build._carpet_columns(f, {})
        self.assertEqual(set(carpets), {(0, 0), (3, 0)})
        self.assertTrue(all(b.endswith('white_carpet') for b in carpets.values()))

    def test_without_a_fit_every_flat_column_carpets(self):
        f = self._feature({(x, 0): ('slab', 'bottom') for x in range(4)})
        f.zone_fit = None
        self.assertEqual(set(F.Build._carpet_columns(f, {})), {(x, 0) for x in range(4)})

    def test_built_fit_lays_carpet_over_full_block_decks_only(self):
        cells = {(x, z) for x in range(10, 36) for z in range(20, 30)}
        data = _zone_data(cells, lambda x, z: 44.2 if x < 22 else 53.0 - abs(x + 0.5 - 29.0))
        regions = [[(10, 20), (22, 20), (22, 30), (10, 30)], [(22, 20), (36, 20), (36, 30), (22, 30)]]
        spec = _spec('fit', zones=_zones(regions))
        spec['roofs']['A']['finish'] = 'white_carpet'
        _b, plan, _out = _build(spec, cells, data)
        carpet = {(x, y, z) for (x, y, z), s in plan.cells.items() if 'white_carpet' in s}
        self.assertTrue(carpet)
        for x, y, z in carpet:
            self.assertLess(x, 22)
            self.assertEqual(plan.cells[(x, y - 1, z)].removeprefix('minecraft:'), 'bricks')


class RidgeJogFallbackTest(unittest.TestCase):
    """A fitted ridge that would jog falls back to the heightfield stairs or the default hip."""

    JOG = 'the fitted ridge would jog'

    @staticmethod
    def _jog_once():
        """Report a jog on the fit's own check only; the build's final lint stays real."""
        calls = []

        def jogs(cells):
            calls.append(1)
            return [(1, 1)] if len(calls) == 1 else ridge_jogs(cells)
        return jogs

    def test_zoned_falls_back_to_the_heightfield_stairs(self):
        cells = {(x, z) for x in range(10, 36) for z in range(20, 30)}
        data = _zone_data(cells, lambda x, z: 53.0 - abs(x + 0.5 - 22.5))
        regions = [[(10, 20), (36, 20), (36, 30), (10, 30)]]
        with mock.patch.object(roof_lint, 'ridge_jogs', side_effect=self._jog_once()) as jogs:
            builder, plan, out = _build(_spec('fit', zones=_zones(regions)), cells, data)
        self.assertTrue(jogs.called)
        self.assertIn(f'A roof stairs fit: {self.JOG}; using the heightfield stairs', out)
        self.assertIsNone(getattr(builder.features[0], 'zone_fit', None))
        plain = _build(_spec(zones=_zones(regions)), cells, data)[1]
        self.assertEqual(plan.cells, plain.cells)

    def test_unzoned_falls_back_to_the_default_hip(self):
        cells = UnzonedHipTest._cells(30, [(0, 14, 0, 6), (0, 6, 6, 13)])
        with mock.patch.object(roof_lint, 'ridge_jogs', side_effect=self._jog_once()) as jogs:
            _b, plan, out = _build(_spec('fit'), cells)
        self.assertTrue(jogs.called)
        self.assertIn(f'A roof stairs fit: {self.JOG}; using the default hip', out)
        self.assertEqual(plan.cells, _build(_spec(), cells)[1].cells)


class FitSpecTest(unittest.TestCase):
    """``roofs[label].stairs: "fit"`` validation."""

    def _load(self, spec):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 's.json'
            path.write_text(json.dumps(spec))
            return FS.load_spec(path)

    def test_fit_is_accepted_with_or_without_zones(self):
        self.assertEqual(self._load(_spec('fit'))['roofs']['A']['stairs'], 'fit')
        zoned = _spec('fit', zones=_zones([[(0, 0), (4, 0), (4, 4), (0, 4)]]))
        self.assertEqual(self._load(zoned)['roofs']['A']['stairs'], 'fit')

    def test_fit_with_a_plot_frame_or_a_flat_roof_refuses(self):
        spec = _spec('fit')
        spec['roofs']['A']['frame'] = 'plot'
        with self.assertRaisesRegex(SystemExit, 'drop frame "plot"'):
            self._load(spec)
        spec = _spec('fit')
        spec['roofs']['A']['kind'] = 'flat'
        with self.assertRaisesRegex(SystemExit, 'only applies to pitched'):
            self._load(spec)


if __name__ == '__main__':
    unittest.main()
