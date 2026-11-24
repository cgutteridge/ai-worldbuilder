"""Tests for the feature-roof stair rule ``roofs[label].stairs: "slope"`` (#434).

Stairs only on the roof rim or on a real fitted slope; level blocks inside a flat layer and at a
step between layers. Synthetic lidar grids only.
"""

from __future__ import annotations

from contextlib import redirect_stdout
from unittest import mock
import io
import math
import unittest

import numpy as np

from block_store import state_props
from feature_build_fixtures import (
    _StoneWorld,
    start_lidar_fixture as setUpModule,
    stop_lidar_fixture as tearDownModule,
)
import feature_build as F
import feature_roof_zones as FZ
import feature_spec as FS
import roof_geometry as RG
import roof_surface as RS
from roof_geometry import ROOF_DELTA


def _spec(regions, stairs=None, axis_deg=0):
    roof = {'kind': 'pitched', 'material': 'stone_brick'}
    if stairs is not None:
        roof['stairs'] = stairs
    return {
        'name': 'unit_slope_stairs',
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
        'zones': {'A': {'from': 'lidar', 'levels': 'auto', 'bucket': 4, 'axis_deg': axis_deg,
                        'regions': regions, 'surface': 'fit'}},
    }


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


def _build(spec, cells, data):
    """Plan and the dry-run text of one feature build (``metres_aod_to_y`` is the identity)."""
    out = io.StringIO()
    with mock.patch.object(FZ, 'metres_aod_to_y', side_effect=lambda m: int(m)), redirect_stdout(out):
        builder = F.Build(spec, footprints={'A': set(cells)}, world=_StoneWorld(), zone_data=data)
        plan = builder.build()
    return builder, plan, out.getvalue()


def _roof_top(builder, plan):
    """``{(x, z): (y, state)}``: the roof membrane block of every roof column."""
    f = builder.features[0]
    return {c: (y, plan.cells[(c[0], y, c[1])]) for c, y in f.zone_roof.items()}


def _stairs(top):
    return {c: state_props(s)['facing'] for c, (_y, s) in top.items() if '_stairs[' in s}


def _rim(roof, c):
    return any((c[0] + dx, c[1] + dz) not in roof for dx, dz in ROOF_DELTA.values())


class TerracedRoofTest(unittest.TestCase):
    """Square-on roof: a low flat deck, a deck one block higher, then a taller gable (ridge across x)."""

    def setUp(self):
        self.cells = {(x, z) for x in range(10, 36) for z in range(20, 30)}
        rng = np.random.default_rng(4)
        noise = {c: float(rng.normal(0, 0.1)) for c in self.cells}

        def height(x, z):
            if x < 16:
                h = 44.2                                     # low flat deck
            elif x < 22:
                h = 45.2                                     # flat deck one block higher
            else:
                h = 53.0 - abs(x + 0.5 - 29.0)               # gable 1 m/m, ridge at x 28.5
            return h + noise[(x, z)]
        self.data = _zone_data(self.cells, height)
        self.regions = [[(10, 20), (16, 20), (16, 30), (10, 30)],
                        [(16, 20), (22, 20), (22, 30), (16, 30)],
                        [(22, 20), (36, 20), (36, 30), (22, 30)]]

    def test_without_the_key_steps_between_flat_layers_are_stairs(self):
        builder, plan, _out = _build(_spec(self.regions), self.cells, self.data)
        stairs = _stairs(_roof_top(builder, plan))
        # interior columns of each flat deck's high edge face the next layer up (a one-block step
        # from the low deck; the foot of the gable's wall from the high deck)
        self.assertEqual({stairs.get((15, z)) for z in range(21, 29)}, {'east'})
        self.assertEqual({stairs.get((21, z)) for z in range(21, 29)}, {'east'})

    def test_slope_rule_keeps_rim_and_slope_stairs_and_levels_the_steps(self):
        builder, plan, out = _build(_spec(self.regions, stairs='slope'), self.cells, self.data)
        top = _roof_top(builder, plan)
        stairs = _stairs(top)
        roof = set(top)
        # one-block step between flat layers, and a flat layer against a taller volume: a full
        # block at the deck's own level
        for x in (15, 21):
            for z in range(21, 29):
                self.assertNotIn((x, z), stairs)
                self.assertEqual(top[(x, z)][1].removeprefix('minecraft:'), 'stone_bricks')
                self.assertEqual(top[(x, z)][0], top[(x - 1, z)][0])
        self.assertEqual(top[(21, 24)][0], top[(15, 24)][0] + 1)          # one block apart
        # rim: the decks' edge columns on the outline keep their stairs
        self.assertEqual(stairs.get((15, 20)), 'east')
        self.assertEqual(stairs.get((21, 29)), 'east')
        # slope: the gable keeps a stair course on both sides of the ridge, facing up to it
        interior_slope = {c: f for c, f in stairs.items() if c[0] >= 23 and not _rim(roof, c)}
        self.assertTrue(interior_slope)
        for (x, _z), facing in interior_slope.items():
            self.assertEqual(facing, 'east' if x < 28.5 else 'west')
        self.assertIn('A roof stairs slope: kept', out)
        self.assertIn('flat layer 16', out)
        # shapes follow the stairs that remain (no stair-shape mismatch against the neighbour rule)
        import roof_lint
        self.assertEqual(roof_lint.stair_shape_mismatches(plan.cells), [])

    def test_rule_changes_nothing_but_stairs(self):
        b0, p0, _ = _build(_spec(self.regions), self.cells, self.data)
        b1, p1, _ = _build(_spec(self.regions, stairs='slope'), self.cells, self.data)
        t0, t1 = _roof_top(b0, p0), _roof_top(b1, p1)
        self.assertEqual({c: y for c, (y, _s) in t0.items()}, {c: y for c, (y, _s) in t1.items()})
        changed = {c for c in t0 if t0[c][1] != t1[c][1] and state_props(t0[c][1]).get('shape') is None}
        self.assertTrue(all('_stairs[' in t0[c][1] for c in changed))


class RotatedGableTest(unittest.TestCase):
    """A clean gable on a footprint 34 degrees off the grid: diagonal steps are slope, not isolated."""

    ANGLE = 34.0

    def setUp(self):
        a = math.radians(self.ANGLE)
        c, s = math.cos(a), math.sin(a)
        self.centre = (100.0, 200.0)
        self.half_u, self.half_v = 9.0, 5.0
        self.cells = set()
        for x in range(80, 121):
            for z in range(180, 221):
                u = (x - self.centre[0]) * c + (z - self.centre[1]) * s
                v = -(x - self.centre[0]) * s + (z - self.centre[1]) * c
                if abs(u) <= self.half_u and abs(v) <= self.half_v:
                    self.cells.add((x, z))

        def height(x, z):
            u = (x - self.centre[0]) * c + (z - self.centre[1]) * s
            return 52.0 - 0.8 * abs(u)                       # ridge across the axis, 0.8 m/m
        self.data = _zone_data(self.cells, height)
        corners = [(su * self.half_u, sv * self.half_v) for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        pad = 1.2
        self.regions = [[(round(self.centre[0] + pad * (u * c - v * s), 2), round(self.centre[1] + pad * (u * s + v * c), 2))
                         for u, v in corners]]

    def test_every_stair_kept_and_each_interior_stair_climbs_the_fitted_slope(self):
        spec_off = _spec(self.regions, axis_deg=self.ANGLE)
        spec_on = _spec(self.regions, stairs='slope', axis_deg=self.ANGLE)
        b0, p0, _ = _build(spec_off, self.cells, self.data)
        b1, p1, out = _build(spec_on, self.cells, self.data)
        self.assertIn('surface gable ridge across the axis', out)
        top = _roof_top(b1, p1)
        stairs = _stairs(top)
        self.assertEqual(stairs, _stairs(_roof_top(b0, p0)))              # a clean slope loses no stair
        self.assertIn('0 made level blocks', out)
        a = math.radians(self.ANGLE)
        roof = set(top)
        isolated_by_neighbours = 0
        for cell, facing in stairs.items():
            if _rim(roof, cell):
                continue
            u = (cell[0] - self.centre[0]) * math.cos(a) + (cell[1] - self.centre[1]) * math.sin(a)
            uphill = (-math.cos(a), -math.sin(a)) if u > 0 else (math.cos(a), math.sin(a))
            dx, dz = ROOF_DELTA[facing]
            self.assertGreater(dx * uphill[0] + dz * uphill[1], 0, f'{cell} faces {facing}, downhill')
            up = (cell[0] + dx, cell[1] + dz)
            down = (cell[0] - dx, cell[1] - dz)
            if not any(stairs.get(n) == facing for n in (up, down)):
                isolated_by_neighbours += 1
        # a neighbour-only test would call these diagonal steps isolated; the fitted shape keeps them
        self.assertGreater(isolated_by_neighbours, 0)


class SlopeStairRuleTest(unittest.TestCase):
    """The rule's decisions on a hand-made 5x5 roof."""

    def setUp(self):
        self.roof = {(x, z) for x in range(5) for z in range(5)}

    def test_reasons_in_order(self):
        rise = {(2, 2): 0.5, (1, 2): 0.05}
        rule = RG.SlopeStairRule(
            self.roof, flat={(3, 1)},
            volume={c: ('b' if c[0] == 4 else 'a') for c in self.roof},
            rise=lambda c, _f: rise.get(c),
        )
        self.assertEqual(rule.reason((0, 2), 'east'), 'rim')
        self.assertEqual(rule.reason((3, 1), 'north'), 'flat layer')
        self.assertEqual(rule.reason((3, 2), 'east'), 'step between volumes')
        self.assertEqual(rule.reason((3, 3), 'south'), 'no fitted surface')
        self.assertEqual(rule.reason((2, 2), 'east'), 'slope')
        self.assertEqual(rule.reason((1, 2), 'east'), 'no fitted slope')
        self.assertTrue(rule((2, 2), 'east'))
        self.assertFalse(rule((1, 2), 'east'))
        self.assertIn('kept 1 stair(s) (slope 1); 1 made level blocks (no fitted slope 1)', rule.summary('A'))

    def test_dropped_stair_takes_the_level_block_of_its_height(self):
        # a 3-column ramp 0, 2, 4 half-steps: the middle stair, when refused, is a full block;
        # an odd half-height becomes a bottom slab
        heights = {(x, 0): 2 * x for x in range(3)}
        mats = {c: 'stone_brick' for c in heights}
        on = RG.pitched_roof_states(heights, mats)
        self.assertIn('_stairs[', on[(0, 0)])
        off = RG.pitched_roof_states(heights, mats, stair_ok=lambda c, f: c != (1, 0))
        self.assertEqual(off[(1, 0)].removeprefix('minecraft:'), 'stone_bricks')
        self.assertEqual(off[(0, 0)], on[(0, 0)])
        odd = RG.pitched_roof_states({(0, 0): 1, (1, 0): 3}, {(0, 0): 'stone_brick', (1, 0): 'stone_brick'},
                                     stair_ok=lambda c, f: False)
        self.assertIn('_slab[type=bottom', odd[(0, 0)])

    def test_no_stair_ok_is_the_old_behaviour(self):
        heights = {(x, z): 2 * min(x, 4 - x, z, 4 - z) for x in range(5) for z in range(5)}
        mats = {c: 'stone_brick' for c in heights}
        self.assertEqual(RG.pitched_roof_states(heights, mats),
                         RG.pitched_roof_states(heights, mats, stair_ok=lambda c, f: True))


class SurfaceGradientTest(unittest.TestCase):
    """``Surface.gradient`` matches the surface's own heights."""

    def test_gradient_matches_finite_differences(self):
        for surface, point in (
            (RS.Surface('plane', 0.1, 10, False, (30.0, 0.4, -0.2), 34.0), (3.0, 4.0)),
            (RS.Surface('gable', 0.1, 10, True, (32.0, 1.0, 0.8, -0.6), 34.0), (-4.0, 1.0)),
            (RS.Surface('gable', 0.1, 10, True, (32.0, 1.0, 0.8, -0.6), 34.0), (6.0, 3.0)),
            (RS.Surface('gable', 0.1, 10, False, (32.0, 0.5, 0.7, -0.7), 10.0), (2.0, -5.0)),
        ):
            x, z = point
            e = 1e-4
            gx, gz = surface.gradient(x, z)
            self.assertAlmostEqual(gx, float(surface.height(x + e, z) - surface.height(x - e, z)) / (2 * e), places=4)
            self.assertAlmostEqual(gz, float(surface.height(x, z + e) - surface.height(x, z - e)) / (2 * e), places=4)
        self.assertEqual(RS.Surface('flat', 0.1, 10, False, (30.0,), 0.0).gradient(1.0, 2.0), (0.0, 0.0))


class SlopeStairsSpecTest(unittest.TestCase):
    """``roofs[label].stairs`` validation."""

    def _load(self, spec):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 's.json'
            path.write_text(json.dumps(spec))
            return FS.load_spec(path)

    def _spec(self):
        spec = _spec([[(0, 0), (4, 0), (4, 4), (0, 4)]], stairs='slope')
        spec['ways'] = {'99': 'A'}
        return spec

    def test_slope_is_accepted_on_a_zoned_pitched_roof(self):
        self.assertEqual(self._load(self._spec())['roofs']['A']['stairs'], 'slope')

    def test_bad_value_flat_roof_and_missing_zones_refuse(self):
        spec = self._spec()
        spec['roofs']['A']['stairs'] = 'octant'
        with self.assertRaisesRegex(SystemExit, 'must be "slope" or "fit"'):
            self._load(spec)
        spec = self._spec()
        del spec['zones']
        with self.assertRaisesRegex(SystemExit, 'needs zones'):
            self._load(spec)
        spec = self._spec()
        spec['roofs']['A']['kind'] = 'flat'
        spec['zones']['A'].pop('surface')
        with self.assertRaisesRegex(SystemExit, 'only applies to pitched'):
            self._load(spec)


if __name__ == '__main__':
    unittest.main()

