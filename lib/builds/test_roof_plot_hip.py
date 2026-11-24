"""Tests for the plot-frame hip ``roofs[label].frame: "plot"`` (#463): rotated hip roofs without zones.

Synthetic footprints only, built through ``feature_build.Build``.
"""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import math
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from block_store import state_props
from feature_build_fixtures import (
    _StoneWorld,
    start_lidar_fixture as setUpModule,
    stop_lidar_fixture as tearDownModule,
)
import feature_build as F
import feature_spec as FS
import roof_plot_hip as RP
from roof_geometry import ROOF_DELTA
from roof_lint import ridge_jogs, stair_shape_mismatches


def _spec(frame=None):
    roof = {'kind': 'pitched', 'material': 'stone_brick'}
    if frame is not None:
        roof['frame'] = frame
    return {
        'name': 'unit_plot_hip',
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


def _rotated_rectangle(deg, length, width):
    """Cells whose centres fall in a ``length`` x ``width`` rectangle turned ``deg`` from the x axis."""
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    out = set()
    for x in range(-30, 31):
        for z in range(-30, 31):
            u = (x + 0.5) * c + (z + 0.5) * s
            v = -(x + 0.5) * s + (z + 0.5) * c
            if abs(u) <= length / 2 and abs(v) <= width / 2:
                out.add((x, z))
    return out


DIAGONAL = _rotated_rectangle(45, 20, 11)        # a corner shop on a diagonal plot


def _rotated_union(deg, parts):
    """Cells of axis-frame rectangles ``(u0, u1, v0, v1)`` turned ``deg`` from the x axis (an L or a T)."""
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    out = set()
    for x in range(-30, 31):
        for z in range(-30, 31):
            u = (x + 0.5) * c + (z + 0.5) * s
            v = -(x + 0.5) * s + (z + 0.5) * c
            if any(u0 <= u <= u1 and v0 <= v <= v1 for u0, u1, v0, v1 in parts):
                out.add((x, z))
    return out


ROTATED_L = _rotated_union(45, [(0, 12, 0, 6), (0, 6, 6, 9)])           # 12 x 9, arms 6 thick
ROTATED_T = _rotated_union(45, [(0, 20, 0, 8), (6, 14, 8, 12)])         # 20 x 12, stem 8 wide
L_SHAPE = {(x, z) for x in range(12) for z in range(4)} | {(x, z) for x in range(4) for z in range(4, 12)}


def _build(spec, cells):
    out = io.StringIO()
    with redirect_stdout(out):
        builder = F.Build(spec, footprints={'A': set(cells)}, world=_StoneWorld())
        plan = builder.build()
    return builder, plan, out.getvalue()


def _membrane(plan):
    """``{(x, z): (y, state)}``: the top roof-material block of each column."""
    top = {}
    for (x, y, z), s in plan.cells.items():
        if 'stone_brick' in s and ((x, z) not in top or y > top[(x, z)][0]):
            top[(x, z)] = (y, s)
    return top


def _top_solid(plan):
    top = {}
    for (x, y, z), s in plan.cells.items():
        if not s.endswith(':air'):
            top[(x, z)] = max(top.get((x, z), -999), y)
    return top


def _perpendicular_joins(plan):
    """Pairs of side-by-side stairs at one Y whose facings are perpendicular (the zigzag of a diagonal face)."""
    stairs = {pos: state_props(s)['facing'] for pos, s in plan.cells.items() if '_stairs[' in s}
    n = 0
    for (x, y, z), f in stairs.items():
        for dx, dz in ((1, 0), (0, 1)):
            g = stairs.get((x + dx, y, z + dz))
            if g is not None and ROOF_DELTA[f][0] * ROOF_DELTA[g][0] + ROOF_DELTA[f][1] * ROOF_DELTA[g][1] == 0:
                n += 1
    return n


class FaceFacingTest(unittest.TestCase):
    def test_nearest_cardinal(self):
        self.assertEqual(RP.face_facing(1, 0.2), 'east')
        self.assertEqual(RP.face_facing(0.2, 1), 'south')
        self.assertEqual(RP.face_facing(-1, 0.1), 'west')
        self.assertEqual(RP.face_facing(0.1, -1), 'north')

    def test_exact_diagonals_take_four_different_facings(self):
        r = math.sqrt(0.5)
        got = {RP.face_facing(sx * r, sz * r) for sx in (1, -1) for sz in (1, -1)}
        self.assertEqual(got, {'east', 'south', 'west', 'north'})


class DesignTest(unittest.TestCase):
    def test_heights_rise_half_a_block_per_block_along_each_face_facing(self):
        own = DIAGONAL
        columns = own | {(x + dx, z + dz) for x, z in own for dx in (-1, 0, 1) for dz in (-1, 0, 1)}
        hip = RP.design_plot_hip(own, columns, 50)
        self.assertEqual(hip.angle % 90, 45)
        self.assertEqual(len(set(hip.facing.values())), 4)
        for (x, z), h in hip.half_heights.items():
            for dx, dz in ROOF_DELTA.values():
                n = hip.half_heights.get((x + dx, z + dz))
                if n is not None:
                    self.assertLessEqual(abs(n - h), 2, (x, z))
            dx, dz = ROOF_DELTA[hip.facing[(x, z)]]
            ahead = (x + dx, z + dz)
            rim = any((x + ex, z + ez) not in hip.half_heights for ex, ez in ROOF_DELTA.values())
            if not rim and hip.face.get(ahead) == hip.face[(x, z)]:
                self.assertEqual(hip.half_heights[ahead] - h, 1, (x, z))

    def test_not_a_rectangle_gives_none(self):
        self.assertIsNone(RP.design_plot_hip(L_SHAPE, L_SHAPE, 50))


class PlotHipBuildTest(unittest.TestCase):
    """A hip on a diagonal footprint, through ``Build``."""

    @classmethod
    def setUpClass(cls):
        cls.off = _build(_spec(), DIAGONAL)
        cls.on = _build(_spec('plot'), DIAGONAL)

    def test_default_hip_zigzags_on_the_diagonal(self):
        self.assertGreater(_perpendicular_joins(self.off[1]), 10)

    def test_plot_frame_faces_keep_one_facing(self):
        builder, plan, out = self.on
        self.assertIn('A roof frame plot: rectangle at 45 degrees', out)
        self.assertLessEqual(_perpendicular_joins(plan), 4)        # only where faces meet at a hip
        self.assertLess(_perpendicular_joins(plan), _perpendicular_joins(self.off[1]))
        self.assertEqual(stair_shape_mismatches(plan.cells), [])
        self.assertEqual(ridge_jogs(plan.cells), [])

    def test_every_stair_is_on_the_rim_or_climbs(self):
        _builder, plan, _out = self.on
        top = _top_solid(plan)
        stairs = [(pos, state_props(s)['facing']) for pos, s in plan.cells.items() if '_stairs[' in s
                  and 'stone_brick' in s]
        self.assertTrue(stairs)
        for (x, y, z), f in stairs:
            dx, dz = ROOF_DELTA[f]
            rim = any((x + ex, z + ez) not in top or top[(x + ex, z + ez)] < y - 1
                      for ex, ez in ROOF_DELTA.values())
            self.assertTrue(rim or top[(x + dx, z + dz)] > y, (x, y, z, f))

    def test_no_wall_shows_beside_a_lower_roof_block(self):
        builder, plan, _out = self.on
        mem = _membrane(plan)
        for (x, z), (y, _s) in mem.items():
            for dx, dz in ROOF_DELTA.values():
                n = mem.get((x + dx, z + dz))
                if n is None or n[0] >= y:
                    continue
                for yy in range(n[0], y):
                    cell = plan.cells.get((x, yy, z))
                    if cell is not None:
                        self.assertNotIn('bricks', cell.replace('stone_bricks', ''), (x, yy, z))

    def test_without_the_key_the_plot_hip_is_never_designed(self):
        with mock.patch.object(RP, 'design_plot_hip', side_effect=AssertionError('plot hip without the key')):
            again = _build(_spec(), DIAGONAL)[1]
        self.assertEqual(again.cells, self.off[1].cells)

    def test_not_a_rectangle_falls_back_to_the_default_hip(self):
        _b, off, _ = _build(_spec(), L_SHAPE)
        _b, on, out = _build(_spec('plot'), L_SHAPE)
        self.assertIn('A roof frame plot: footprint is not close to a rectangle; using the default hip', out)
        self.assertEqual(on.cells, off.cells)


class NotchedFootprintTest(unittest.TestCase):
    """A rotated L or T that passes the rectangle fill test: nothing outside the footprint rises above the eave."""

    def _check(self, cells):
        builder, plan, out = _build(_spec('plot'), cells)
        self.assertIn('A roof frame plot: rectangle at 45 degrees', out)
        f = builder.features[0]
        eave = f.b + f.storeys * F.STOREY_H
        above = sorted((x, y, z) for (x, y, z), s in plan.cells.items()
                       if (x, z) not in cells and y > eave and not s.endswith(':air'))
        self.assertEqual(above, [])

    def test_rotated_l(self):
        self._check(ROTATED_L)

    def test_rotated_t(self):
        self._check(ROTATED_T)


class FrameSpecTest(unittest.TestCase):
    def _load(self, spec):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 's.json'
            path.write_text(json.dumps(spec))
            return FS.load_spec(path)

    def test_plot_is_accepted_on_an_unzoned_pitched_roof(self):
        self.assertEqual(self._load(_spec('plot'))['roofs']['A']['frame'], 'plot')

    def test_bad_value_flat_roof_and_zones_refuse(self):
        with self.assertRaisesRegex(SystemExit, 'must be "plot"'):
            self._load(_spec('octant'))
        spec = _spec('plot')
        spec['roofs']['A']['kind'] = 'flat'
        with self.assertRaisesRegex(SystemExit, 'only applies to pitched'):
            self._load(spec)
        spec = _spec('plot')
        spec['zones'] = {'A': {'from': 'lidar', 'levels': 'auto', 'bucket': 4}}
        with self.assertRaisesRegex(SystemExit, 'without zones'):
            self._load(spec)


if __name__ == '__main__':
    unittest.main()
