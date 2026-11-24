"""Tests for feature facade seams."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import unittest

from feature_build_fixtures import (
    _StoneWorld,
    _glazing_plan,
    start_lidar_fixture as setUpModule,
    stop_lidar_fixture as tearDownModule,
)
import feature_build as F
import feature_spec as FS


class FacadeWindowsTest(unittest.TestCase):
    """Seam: facade table drives openings; door cells stay solid; glazed share reported."""

    def setUp(self):
        # 8×5 rectangle: long S face has 8 wall cells on the south edge.
        self.cells = {(x, z) for x in range(10, 18) for z in range(20, 25)}

    def spec(self, facades):
        return {
            'name': 'unit_facade',
            'ways': {'99': 'A'},
            'storeys': {'A': 2},
            'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
            'groups': [{'nos': ['A'], 'front': 'S', 'mode': 'each'}],
            'modifies': [],
            'floor_min': 50,
            'step_max': 4,
            'door_frame': True,
            'keep_edits': False,
            'wall': 'bricks',
            'floor': 'smooth_stone',
            'ledge_stair': False,
            'facade_plan': _glazing_plan(facades),
        }

    def test_facade_plan_glazing_drives_windows_and_evidence_report(self):
        spec = self.spec({})
        spec['facade_plan'] = {'A': {'faces': {'S': {'glazing': [
            {'storey': 1, 'glazed_h': 2, 'pier': 1, 'rhythm': 'BBBBBBBB'}
        ]}}}}
        with redirect_stdout(io.StringIO()) as output:
            plan = F.build(spec, footprints={'A': self.cells}, world=_StoneWorld())
        self.assertFalse(any('glass' in state for (x, y, z), state in plan.cells.items() if z == 24))
        south = [line for line in output.getvalue().splitlines() if 'face S storey 1' in line]
        self.assertTrue(south)
        self.assertNotIn('no evidence read', south[0])

    def test_storey1_glazed_3_pier_1_places_full_height_glass(self):
        # Q-198 done criterion: "storey 1 glazed 3 high, pier 1"
        facades = {
            'A': {
                'S': [{'storey': 1, 'glazed_h': 3, 'pier': 1, 'wall': 'bricks'}],
            }
        }
        plan = F.build(self.spec(facades), footprints={'A': self.cells}, world=_StoneWorld())
        floor = 50
        # South edge z=24, x=10..17. End cells are piers; pattern WW_ (period 3).
        # x=11,12 glazed; x=14,15 glazed; x=10,13,16,17 pier (and ends).
        glass_ys = [
            y for (x, y, z), st in plan.cells.items()
            if z == 24 and 'glass' in st and floor <= y < floor + 3
        ]
        self.assertTrue(glass_ys, 'expected storey-1 glass on south face')
        # Full storey height: glass at y 50, 51, 52 for glazed columns
        glazed_cols = {
            x for (x, y, z), st in plan.cells.items()
            if z == 24 and 'glass' in st and floor <= y < floor + 3
        }
        for x in glazed_cols:
            for dy in range(3):
                self.assertIn('glass', plan.cells.get((x, floor + dy, 24), ''))
        # Door on south mid stays solid (no glass at door column)
        door_glass = [
            st for (x, y, z), st in plan.cells.items()
            if z == 24 and 'glass' in st and 'door' in plan.cells.get((x, floor + 1, 24), '')
        ]
        self.assertEqual(door_glass, [])

    def test_glazed_share_report_per_storey_face(self):
        facades = {
            'A': {
                'S': [
                    {'storey': 1, 'glazed_h': 3, 'pier': 1},
                    {'storey': 2, 'glazed_h': 2, 'pier': 1},
                ],
            }
        }
        b = F.Build(self.spec(facades), footprints={'A': self.cells}, world=_StoneWorld())
        b.build()
        lines = b.glazed_share_report()
        self.assertTrue(any('A' in ln and 'S' in ln and 'storey 1' in ln for ln in lines), lines)
        self.assertTrue(any('A' in ln and 'S' in ln and 'storey 2' in ln for ln in lines), lines)
        # Share is a percentage the dry-run can compare to the photo
        self.assertTrue(any('%' in ln for ln in lines), lines)

    def test_glazed_share_lists_every_exposed_face_marks_no_evidence(self):
        """#130: dry-run lists every exposed face/storey; missing facade rows → no evidence read."""
        # Only the south face has facade rows; N/E/W are exposed on a free rectangle.
        facades = {
            'A': {
                'S': [
                    {'storey': 1, 'glazed_h': 3, 'pier': 1},
                    {'storey': 2, 'glazed_h': 2, 'pier': 1},
                ],
            }
        }
        b = F.Build(self.spec(facades), footprints={'A': self.cells}, world=_StoneWorld())
        b.build()
        lines = b.glazed_share_report()
        for face in 'NSEW':
            for storey in (1, 2):
                matching = [
                    ln for ln in lines
                    if f'face {face}' in ln and f'storey {storey}' in ln
                ]
                self.assertEqual(
                    len(matching), 1,
                    f'expected one line for face {face} storey {storey}: {lines}',
                )
                line = matching[0]
                self.assertIn('%', line, line)
                if face == 'S':
                    self.assertNotIn('no evidence read', line, line)
                else:
                    self.assertIn('no evidence read', line, line)

    def test_stepped_treads_gain_solid_glass_and_corners_stay_wall(self):
        cells = {(x, z) for x in range(10, 18) for z in range(20, 25 + (x - 10) // 2)}
        spec = self.spec({'A': {'S': [{'storey': 1, 'glazed_h': 2, 'pier': 0, 'rhythm': 'W'}]}})
        spec['storeys'] = {'A': 1}
        with redirect_stdout(io.StringIO()) as output:
            plan = F.build(spec, footprints={'A': cells}, world=_StoneWorld())
        self.assertIn('solid-glass=6', output.getvalue())
        self.assertIn('corner pier / tread too short=', output.getvalue())
        self.assertFalse(any('glass_pane' in state for state in plan.cells.values()))
        for dy in (1, 2):
            self.assertEqual(plan.cells[(12, 50 + dy, 25)], 'minecraft:bricks')
        self.assertTrue(any(state == 'minecraft:glass' for state in plan.cells.values()))

    def test_pier_rows_glaze_a_stair_stepped_face(self):
        """#466: one-column treads join into a flight, so the pier rhythm leaves windows."""
        cells = {(x, z) for x in range(10, 20) for z in range(20, 25 + (x - 10))}
        spec = self.spec({'A': {'S': [{'storey': 1, 'glazed_h': 2, 'pier': 1}]}})
        spec['storeys'] = {'A': 1}
        with redirect_stdout(io.StringIO()) as output:
            plan = F.build(spec, footprints={'A': cells}, world=_StoneWorld())
        glass = {(x, z) for (x, y, z), state in plan.cells.items() if state == 'minecraft:glass'}
        self.assertTrue(glass, output.getvalue())
        self.assertTrue(all(z == 24 + (x - 10) for x, z in glass), glass)
        self.assertNotIn((10, 24), glass)
        self.assertNotIn((19, 33), glass)
        self.assertFalse(any('glass_pane' in state for state in plan.cells.values()))

    def test_one_storey_glazed_h1_gets_glass_under_eave(self):
        """#131: top-of-wall pane needs the eave as lintel; glazed_h=1 must still glaze."""
        # Rectangle so lateral neighbours are solid; only the above-check can fail.
        cells = {(x, z) for x in range(10, 18) for z in range(20, 25)}
        facades = {
            'A': {'S': [{'storey': 1, 'glazed_h': 1, 'pier': 0, 'rhythm': 'W'}]},
        }
        spec = self.spec(facades)
        spec['storeys'] = {'A': 1}
        plan = F.build(spec, footprints={'A': cells}, world=_StoneWorld())
        floor = 50
        eave_y = floor + FS.STOREY_H  # shell top is eave_y - 1; flat roof slab here
        top_y = eave_y - 1
        glass = [
            (x, y, z) for (x, y, z), st in plan.cells.items()
            if z == 24 and y == top_y and 'glass_pane' in st
        ]
        self.assertTrue(
            glass,
            'expected glazed_h=1 glass on the top wall row under the eave lintel',
        )
        for x, y, z in glass:
            eave = plan.cells.get((x, eave_y, z), '')
            self.assertTrue(eave and 'air' not in eave.split('[')[0],
                            f'pane at {(x, y, z)} needs eave solid above, got {eave!r}')


class StepWallWindowsTest(unittest.TestCase):
    """Seam (#473): the wall above a lower part of the same building takes windows."""

    X0, Z0 = 9, 19

    def setUp(self):
        self.cells = {(x, z) for x in range(10, 18) for z in range(20, 30)}

    def build(self, high, high_y=50, low_y=45):
        import numpy as np
        from unittest import mock
        import feature_roof_zones as FZ
        dsm = np.full((12, 10), float(low_y))
        for (x, z) in high:
            dsm[z - self.Z0, x - self.X0] = float(high_y)
        zone_data = {'A': {'dsm': dsm, 'ground_y': np.full((12, 10), 40),
                           'x0': self.X0, 'z0': self.Z0}}
        spec = {
            'name': 'unit_step', 'ways': {'99': 'A'}, 'storeys': {'A': 3},
            'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
            'groups': [{'nos': ['A'], 'front': 'S', 'mode': 'each'}],
            'modifies': [], 'floor_min': 40, 'step_max': 4, 'door_frame': True,
            'keep_edits': False, 'wall': 'bricks', 'floor': 'smooth_stone',
            'ledge_stair': False,
            'zones': {'A': {'from': 'lidar', 'levels': sorted({low_y, high_y}),
                            'bucket': 1, 'axis_deg': 0}},
        }
        builder = F.Build(spec, footprints={'A': self.cells}, world=_StoneWorld(), zone_data=zone_data)
        out = io.StringIO()
        with mock.patch.object(FZ, 'metres_aod_to_y', side_effect=lambda m: int(m)), \
                redirect_stdout(out):
            plan = builder.build()
        self.out = out.getvalue()
        return plan, builder.features[0]

    @staticmethod
    def glass(plan, cell):
        return sorted(y for (x, y, z), st in plan.cells.items()
                      if (x, z) == cell and 'glass' in st)

    @staticmethod
    def step_cells(f):
        """Wall columns with a lower part of the same feature beside them and no outside face."""
        near = ((1, 0), (-1, 0), (0, 1), (0, -1))
        return {c for c in f.walls
                if all((c[0] + dx, c[1] + dz) in f.cells for dx, dz in near)
                and any(f.zone_roof[(c[0] + dx, c[1] + dz)] < f.zone_roof[c] for dx, dz in near)}

    def test_straight_step_wall_gets_glass_above_the_lower_roof(self):
        high = {(x, z) for x in range(10, 18) for z in range(20, 25)}
        plan, f = self.build(high)
        step = self.step_cells(f)
        self.assertTrue(step)
        glazed = [c for c in step if self.glass(plan, c)]
        self.assertTrue(glazed, 'no glass on the zone step wall')
        for c in glazed:
            self.assertTrue(all(45 < y < 50 for y in self.glass(plan, c)), self.glass(plan, c))
        self.assertFalse([k for k, st in plan.cells.items() if '_door[' in st and (k[0], k[2]) in step])

    def test_stair_stepped_step_wall_gets_glass_above_the_lower_roof(self):
        high = {(x, z) for x in range(10, 18) for z in range(20, 30) if x - 10 >= 2 * (z - 20)}
        plan, f = self.build(high)
        step = self.step_cells(f)
        self.assertTrue(step)
        glazed = [c for c in step if self.glass(plan, c)]
        self.assertTrue(glazed, 'no glass on the stair-stepped wall')
        for c in step:
            for y in self.glass(plan, c):
                self.assertTrue(45 < y < 50, (c, y))

    def test_step_lower_than_one_window_row_gets_no_glass(self):
        high = {(x, z) for x in range(10, 18) for z in range(20, 25)}
        plan, f = self.build(high, high_y=47)
        step = self.step_cells(f)
        self.assertTrue(step)
        self.assertEqual([c for c in step if self.glass(plan, c)], [])

    def test_no_step_means_no_step_cells(self):
        plan, f = self.build(set(), high_y=45)
        self.assertEqual(self.step_cells(f), set())
