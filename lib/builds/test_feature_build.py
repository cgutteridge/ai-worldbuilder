"""Tests for feature_build at agreed seams: load_spec, build→Plan, CLI dry-run, SU fixture, facades, doors."""
from __future__ import annotations

import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import numpy as np

import feature_build as F
import site_kit
from site_kit import door_frame_issues
from block_store import MemoryBlocks


def _glazing_plan(rows, plan=None):
    """Compose facade-plan fixture data without a production legacy reader."""
    result = dict(plan or {})
    for label, faces in rows.items():
        body = dict(result.get(label) or {})
        body['faces'] = {**body.get('faces', {}), **{face: {'glazing': values} for face, values in faces.items()}}
        result[label] = body
    return result


class _FixtureLidar:
    """External DTM fixture: bare terrain maps to block Y=49 in the test frame."""
    def missing(self, *args):
        return []

    def sample(self, product, east, north, geocraft=True):
        return np.full(np.asarray(east).shape, 38.0)


_lidar_fixture = mock.patch('lidar_provider.tile_client', return_value=_FixtureLidar())


def setUpModule():
    _lidar_fixture.start()


def tearDownModule():
    _lidar_fixture.stop()


class LoadSpecTest(unittest.TestCase):
    """Seam: load_spec validates and fills defaults."""

    def write(self, spec: dict) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 's.json'
        path.write_text(json.dumps(spec))
        return path

    def good(self) -> dict:
        return {
            'name': 'demo',
            'ways': {'1': 'A'},
            'storeys': {'A': 3},
            'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
            'groups': [{'nos': ['A'], 'front': 'N'}],
        }

    def test_loads_defaults_and_required_keys(self):
        spec = F.load_spec(self.write(self.good()))
        self.assertEqual(spec['name'], 'demo')
        self.assertEqual(spec['storeys']['A'], 3)
        self.assertEqual(spec['roofs']['A']['kind'], 'flat')
        self.assertEqual(
            (spec['modifies'], spec['floor_min'], spec['step_max'], spec['door_frame'], spec['keep_edits']),
            ([], None, 4, True, False),
        )
        self.assertEqual(spec['wall'], 'bricks')
        self.assertEqual(spec['floor'], 'smooth_stone')
        self.assertEqual(spec['facade_plan'], {})
        self.assertEqual(spec['details'], {})
        self.assertNotIn('doors', spec)
        self.assertEqual(spec['zones'], {})
        self.assertEqual(spec['exclude'], {})

    def test_missing_key_bad_front_unknown_label_bad_storeys_refused(self):
        good = self.good()
        cases = [
            {k: v for k, v in good.items() if k != 'storeys'},
            {k: v for k, v in good.items() if k != 'roofs'},
            {**good, 'groups': [{'nos': ['B'], 'front': 'N'}]},
            {**good, 'groups': [{'nos': ['A'], 'front': 'X'}]},
            {**good, 'storeys': {'A': 0}},
            {**good, 'roofs': {'A': {'kind': 'dome', 'material': 'brick'}}},
            {**good, 'roofs': {'A': 'brick'}},
        ]
        for bad in cases:
            with self.subTest(bad=bad):
                with self.assertRaises(SystemExit):
                    F.load_spec(self.write(bad))

    def test_legacy_glazing_key_is_refused_with_migration_pointer(self):
        with self.assertRaisesRegex(SystemExit, 'facade_plan'):
            F.load_spec(self.write({**self.good(), 'facades': {}}))

    def test_legacy_doors_key_refused(self):
        """#146: per-portal doors map is gone; entrances live on facade_plan."""
        good = self.good()
        with self.assertRaises(SystemExit) as ctx:
            F.load_spec(self.write({
                **good,
                'doors': {'42-A': {'leaves': 2, 'block': 'iron_door'}},
            }))
        self.assertIn('doors', str(ctx.exception))
        self.assertIn('facade_plan', str(ctx.exception))

    def test_roof_edge_absent_by_default_and_accepted_when_valid(self):
        spec = F.load_spec(self.write(self.good()))
        self.assertNotIn('edge', spec['roofs']['A'])
        ok = {
            **self.good(),
            'roofs': {
                'A': {
                    'kind': 'flat',
                    'material': 'smooth_stone',
                    'edge': {'block': 'smooth_stone', 'where': 'outer'},
                },
            },
        }
        loaded = F.load_spec(self.write(ok))
        self.assertEqual(loaded['roofs']['A']['edge'], {'block': 'smooth_stone', 'where': 'outer'})

    def test_roof_edge_bad_values_refused(self):
        good = self.good()
        for bad_edge in (
            'smooth_stone',
            {'block': 'smooth_stone'},
            {'where': 'outer'},
            {'block': '', 'where': 'outer'},
            {'block': 'smooth_stone', 'where': 'steps'},
            {'block': 1, 'where': 'all'},
        ):
            with self.subTest(bad_edge=bad_edge):
                roof = {'kind': 'flat', 'material': 'smooth_stone', 'edge': bad_edge}
                with self.assertRaises(SystemExit):
                    F.load_spec(self.write({**good, 'roofs': {'A': roof}}))


class ExcludeSpecTest(unittest.TestCase):
    """exclude boxes: validated on load, cut from the footprint on build."""

    write = LoadSpecTest.write
    good = LoadSpecTest.good

    def test_exclude_accepted_and_bad_boxes_refused(self):
        good = self.good()
        good['exclude'] = {'A': [[0, 0, 5, 5]]}
        self.assertEqual(F.load_spec(self.write(good))['exclude'], {'A': [[0, 0, 5, 5]]})
        for bad in ({'Z': [[0, 0, 1, 1]]}, {'A': [[5, 0, 1, 1]]}, {'A': [[0, 0, 1]]},
                    {'A': [[0, 0, 1, 'x']]}, {'A': 'no'}, ['A']):
            spec = self.good()
            spec['exclude'] = bad
            with self.assertRaises(SystemExit, msg=str(bad)):
                F.load_spec(self.write(spec))

    def test_build_drops_excluded_cells_from_footprint(self):
        cells = {(x, z) for x in range(10, 15) for z in range(20, 25)}
        spec = BuildPlanTest.spec(self)
        spec['exclude'] = {'A': [[10, 20, 14, 21]]}
        b = F.Build(spec, footprints={'A': cells}, world=_StoneWorld())
        self.assertEqual(b.by_no_all['A'], {c for c in cells if c[1] >= 22})

    def test_build_refuses_when_everything_excluded(self):
        spec = BuildPlanTest.spec(self)
        spec['exclude'] = {'A': [[0, 0, 99, 99]]}
        with self.assertRaises(SystemExit):
            F.Build(spec, footprints={'A': {(10, 20)}}, world=_StoneWorld())


    def test_clipped_cells_are_not_foreign_but_other_buildings_are(self):
        class Idx:
            buildings = [(0, {'osm_way_id': '99'}), (1, {'osm_way_id': '7'})]

            def cells(self):
                cells = {(x, z): 0 for x in range(10, 15) for z in range(20, 25)}
                cells[(30, 30)] = 1
                return cells

        spec = BuildPlanTest.spec(self)
        spec['exclude'] = {'A': [[10, 20, 14, 21]]}
        with mock.patch.object(F.osm_footprints, 'OSMIndex', return_value=Idx()):
            b = F.Build(spec, world=_StoneWorld())
        self.assertEqual(b._foreign_cells(), {(30, 30)})


class _StoneWorld:
    """World stub: solid stone through y<=49, air above."""

    def block(self, x, y, z):
        return 'minecraft:stone' if y <= 49 else 'minecraft:air'


class BuildPlanTest(unittest.TestCase):
    """Seam: build(spec) → Plan with N storeys and flat/pitched roof."""

    def setUp(self):
        self.cells = {(x, z) for x in range(10, 15) for z in range(20, 25)}

    def spec(self, storeys=3, kind='flat', material='smooth_stone', floor_y=50):
        return {
            'name': 'unit_feature',
            'ways': {'99': 'A'},
            'storeys': {'A': storeys},
            'roofs': {'A': {'kind': kind, 'material': material}},
            'groups': [{'nos': ['A'], 'front': 'S', 'mode': 'each'}],
            'modifies': [],
            'floor_min': floor_y,
            'step_max': 4,
            'door_frame': True,
            'keep_edits': False,
            'wall': 'bricks',
            'floor': 'smooth_stone',
            'ledge_stair': False,
        }

    def test_dry_run_reports_unknown_planned_block_ids_and_source(self):
        spec = self.spec()
        spec['wall'] = 'made_up_wall'
        output = io.StringIO()
        with redirect_stdout(output):
            F.build(spec, footprints={'A': self.cells}, world=_StoneWorld())
        self.assertRegex(output.getvalue(), r'unknown block id minecraft:made_up_wall x[1-9][0-9]*')
        self.assertIn('vendored Minecraft 26.2', output.getvalue())

    def test_unzoned_floor_uses_lidar_ground_not_live_rim(self):
        spec = self.spec(floor_y=None)
        data = {'A': {'x0': 10, 'z0': 20, 'ground_y': np.full((5, 5), 54)}}
        class LiveRim52:
            def block(self, x, y, z):
                return 'minecraft:stone' if y <= 52 else 'minecraft:air'
        output = io.StringIO()
        with redirect_stdout(output):
            plan = F.build(spec, footprints={'A': self.cells}, world=LiveRim52(), zone_data=data)
        self.assertIn('A b 54 ', output.getvalue())
        self.assertEqual(plan.cells[(12, 54, 22)], 'minecraft:smooth_stone')

    def test_three_storey_flat_roof_shell_height(self):
        plan = F.build(self.spec(storeys=3, kind='flat'), footprints={'A': self.cells}, world=_StoneWorld())
        floor = 50
        wall_top = floor + 3 * 3 - 1  # 58
        roof_y = floor + 3 * 3       # 59
        # Corner pier (not mid-face): mid columns may get default window rhythm (#146).
        wx, wz = 10, 24
        self.assertEqual(plan.cells.get((wx, floor, wz)), 'minecraft:bricks')
        self.assertEqual(plan.cells.get((wx, wall_top, wz)), 'minecraft:bricks')
        self.assertIn('smooth_stone_slab', plan.cells.get((12, roof_y, 22), ''))
        self.assertEqual(plan.cells.get((12, floor, 22)), 'minecraft:smooth_stone')
        self.assertEqual(plan.cells.get((12, floor + 3, 22)), 'minecraft:smooth_stone')
        self.assertEqual(plan.cells.get((12, floor + 6, 22)), 'minecraft:smooth_stone')

    def test_flat_roof_default_none_skips_overhang_fringe(self):
        """Absent edge: membrane on owned columns only, no N8 slab fringe."""
        plan = F.build(self.spec(storeys=2, kind='flat'), footprints={'A': self.cells}, world=_StoneWorld())
        roof_y = 50 + 2 * 3
        fringe = (9, roof_y, 22)  # one west of the mass
        self.assertNotIn('slab', plan.cells.get(fringe, ''))
        self.assertIn('smooth_stone_slab', plan.cells.get((12, roof_y, 22), ''))

    def test_clear_margin_does_not_fill_stone_shelves(self):
        """#115: margin is cleared but not filled with a stone ring at floor level."""
        floor = 50
        plan = F.build(self.spec(storeys=2, kind='flat'), footprints={'A': self.cells}, world=_StoneWorld())
        door_xz = {(x, z) for (x, _y, z), st in plan.cells.items() if '_door' in st}
        shelf = []
        for (x, y, z), st in plan.cells.items():
            if st != 'minecraft:stone' or y != floor or (x, z) in self.cells:
                continue
            # Stoops / step_out next to doors may still place stone — those are allowed.
            if any(abs(x - dx) <= 1 and abs(z - dz) <= 1 for dx, dz in door_xz):
                continue
            shelf.append((x, y, z))
        self.assertEqual(shelf, [], f'stone shelves outside footprint: {shelf[:12]}')

    def test_flat_roof_edge_builds_overhang_fringe(self):
        """Present edge: one-block N8 overhang fringe (old flat-roof behaviour)."""
        spec = self.spec(storeys=2, kind='flat')
        spec['roofs']['A']['edge'] = {'block': 'smooth_stone', 'where': 'outer'}
        plan = F.build(spec, footprints={'A': self.cells}, world=_StoneWorld())
        roof_y = 50 + 2 * 3
        fringe = (9, roof_y, 22)
        self.assertIn('smooth_stone_slab', plan.cells.get(fringe, ''))

    def test_pitched_roof_rises_above_eave(self):
        plan = F.build(
            self.spec(storeys=2, kind='pitched', material='brick'),
            footprints={'A': self.cells},
            world=_StoneWorld(),
        )
        floor = 50
        eave = floor + 2 * 3
        roof_ys = [
            y for (x, y, z), st in plan.cells.items()
            if 'brick_stairs' in st or st.endswith('bricks') or 'brick_slab' in st
        ]
        self.assertTrue(roof_ys, 'expected pitched brick stairs/slabs/full')
        self.assertGreaterEqual(max(roof_ys), eave)
        stairs = [st for st in plan.cells.values() if 'brick_stairs' in st]
        self.assertTrue(stairs, 'pitched hip must place upslope stairs')
        self.assertTrue(all('waterlogged=false' in st for st in stairs))
        self.assertTrue(all('half=bottom' in st for st in stairs))

    def test_ghost_ground_owner_does_not_drop_shell_cells(self):
        """Ghost ground must not shrink the offline plan (live apply overwrites)."""
        from journal_testkit import JournalWorld
        pos = (12, 50, 22)
        with JournalWorld() as jw:
            jw.build('ghost_0_0_ground', 'ghost-ground',
                     {(12, 40, 22): 'minecraft:light_gray_concrete', pos: 'minecraft:gray_concrete'},
                     cell=(0, 0))
            plan = F.build(self.spec(storeys=2), footprints={'A': self.cells}, world=_StoneWorld())
        self.assertIn(pos, plan.cells)


class CliDryRunTest(unittest.TestCase):
    """Seam: run() previews and dry-applies (never live unless apply=True)."""

    def test_dry_run_slab_normalization_keeps_valid_deepslate_full_id(self):
        spec = {'name': 'slab_integration', 'ways': {'1': 'A'},
                'storeys': {'A': 1}, 'roofs': {'A': {'kind': 'flat', 'material': 'deepslate_tile'}},
                'groups': [{'nos': ['A'], 'front': 'N'}], 'floor_min': 50}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'spec.json'
            path.write_text(json.dumps(spec))
            (F.ROOT / 'verification/site').mkdir(parents=True, exist_ok=True)
            with mock.patch('mc_paced.Client'), mock.patch.object(F, 'preview'), redirect_stdout(io.StringIO()):
                plan = F.run(path, footprints={'A': {(x, z) for x in range(4) for z in range(4)}}, world=_StoneWorld())
        self.assertTrue('minecraft:deepslate_tiles' in plan.cells.values())
        self.assertFalse('minecraft:deepslate_tile' in plan.cells.values())

    def test_run_dry_calls_preview_and_apply_dry(self):
        cells = {(x, z) for x in range(0, 4) for z in range(0, 4)}
        spec = {
            'name': 'cli_feature',
            'ways': {'1': 'A'},
            'storeys': {'A': 2},
            'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
            'groups': [{'nos': ['A'], 'front': 'N'}],
            'floor_min': 50,
        }
        with tempfile.TemporaryDirectory() as d:
            spec_path = Path(d) / 'cli_feature.json'
            spec_path.write_text(json.dumps(spec))
            with mock.patch.object(F, 'preview') as prev, mock.patch.object(F, 'apply') as app:
                plan = F.run(spec_path, do_apply=False, footprints={'A': cells}, world=_StoneWorld())
                self.assertGreater(len(plan.cells), 0)
                prev.assert_called_once()
                app.assert_called_once()
                self.assertEqual(app.call_args.kwargs.get('dry'), True)


class FacadeSpecTest(unittest.TestCase):
    """Seam: load_spec accepts per-face facade rows; rejects bad storey/face/height."""

    def write(self, spec: dict) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 's.json'
        path.write_text(json.dumps(spec))
        return path

    def good(self) -> dict:
        return {
            'name': 'facade_demo',
            'ways': {'1': 'A'},
            'storeys': {'A': 2},
            'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
            'groups': [{'nos': ['A'], 'front': 'S'}],
            'facade_plan': _glazing_plan({
                'A': {
                    'S': [{'storey': 1, 'glazed_h': 3, 'pier': 1, 'wall': 'bricks'}],
                    'N': [{'storey': 1, 'glazed_h': 2, 'pier': 1, 'rhythm': 'WW_WW'}],
                }
            }),
        }

    def test_loads_facade_table(self):
        spec = F.load_spec(self.write(self.good()))
        self.assertEqual(spec['facade_plan']['A']['faces']['S']['glazing'][0]['glazed_h'], 3)
        self.assertEqual(spec['facade_plan']['A']['faces']['N']['glazing'][0]['rhythm'], 'WW_WW')
        self.assertEqual(spec['facade_plan']['A']['faces']['S']['glazing'][0]['pier'], 1)

    def test_facades_optional_default_empty(self):
        base = {k: v for k, v in self.good().items() if k != 'facade_plan'}
        spec = F.load_spec(self.write(base))
        self.assertEqual(spec['facade_plan'], {})

    def test_bad_facade_rows_refused(self):
        good = self.good()
        cases = [
            {**good, 'facade_plan': _glazing_plan({'B': {'S': [{'storey': 1, 'glazed_h': 3, 'pier': 1}]}})},
            {**good, 'facade_plan': _glazing_plan({'A': {'X': [{'storey': 1, 'glazed_h': 3, 'pier': 1}]}})},
            {**good, 'facade_plan': _glazing_plan({'A': {'S': [{'storey': 0, 'glazed_h': 3, 'pier': 1}]}})},
            {**good, 'facade_plan': _glazing_plan({'A': {'S': [{'storey': 3, 'glazed_h': 3, 'pier': 1}]}})},
            {**good, 'facade_plan': _glazing_plan({'A': {'S': [{'storey': 1, 'glazed_h': 4, 'pier': 1}]}})},
            {**good, 'facade_plan': _glazing_plan({'A': {'S': [{'storey': 1, 'glazed_h': 0, 'pier': 1}]}})},
            {**good, 'facade_plan': _glazing_plan({'A': {'S': [{'storey': 1, 'glazed_h': 3, 'pier': -1}]}})},
            {**good, 'facade_plan': _glazing_plan({'A': {'S': [{'storey': 1, 'glazed_h': 3}]}})},
        ]
        for bad in cases:
            with self.subTest(bad=bad.get('facade_plan')):
                with self.assertRaises(SystemExit):
                    F.load_spec(self.write(bad))


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
            if z == 24 and 'glass_pane' in st and floor <= y < floor + 3
        ]
        self.assertTrue(glass_ys, 'expected storey-1 glass on south face')
        # Full storey height: glass at y 50, 51, 52 for glazed columns
        glazed_cols = {
            x for (x, y, z), st in plan.cells.items()
            if z == 24 and 'glass_pane' in st and floor <= y < floor + 3
        }
        for x in glazed_cols:
            for dy in range(3):
                self.assertIn('glass_pane', plan.cells.get((x, floor + dy, 24), ''))
        # Door on south mid stays solid (no glass at door column)
        door_glass = [
            st for (x, y, z), st in plan.cells.items()
            if z == 24 and 'glass_pane' in st and 'door' in plan.cells.get((x, floor + 1, 24), '')
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

    def test_unframed_panes_on_stepped_face_kept_as_wall(self):
        """#131 / S-007: pane only with solid both sides along the wall and above/below."""
        # Stair-step south edge: at each step the new south cell has air on one
        # lateral neighbour along the wall line, so that pane must stay wall.
        cells = set()
        for x in range(10, 18):
            z_max = 24 + (x - 10) // 2
            for z in range(20, z_max + 1):
                cells.add((x, z))
        facades = {
            'A': {'S': [{'storey': 1, 'glazed_h': 2, 'pier': 0, 'rhythm': 'W'}]},
        }
        spec = self.spec(facades)
        spec['storeys'] = {'A': 1}
        buf = io.StringIO()
        with redirect_stdout(buf):
            plan = F.build(spec, footprints={'A': cells}, world=_StoneWorld())
        out = buf.getvalue()
        self.assertRegex(out, r'A face S: \d+ panes skipped \(no frame\)')
        skip_line = next(ln for ln in out.splitlines() if 'panes skipped' in ln and 'face S' in ln)
        skipped_n = int(skip_line.split('panes skipped')[0].rsplit(':', 1)[-1].strip())
        self.assertGreater(skipped_n, 0, skip_line)

        air = {'air', 'cave_air', 'void_air'}

        def solid(x, y, z):
            st = plan.cells.get((x, y, z))
            if st is None:
                st = _StoneWorld().block(x, y, z)
            return bool(st) and st.split('[')[0].split(':')[-1] not in air

        glass = [(x, y, z) for (x, y, z), st in plan.cells.items() if 'glass_pane' in st]
        self.assertTrue(glass, 'expected some framed panes on straight stretches')
        for x, y, z in glass:
            for nx, ny, nz in ((x - 1, y, z), (x + 1, y, z), (x, y - 1, z), (x, y + 1, z)):
                self.assertTrue(
                    solid(nx, ny, nz),
                    f'pane at {(x, y, z)} missing solid frame at {(nx, ny, nz)}',
                )
        # Step cell (12, 25): south-exposed with air at (11, *, 25) — must stay wall.
        floor = 50
        for dy in (1, 2):
            st = plan.cells.get((12, floor + dy, 25), '')
            self.assertNotIn('glass_pane', st, f'unframed step cell glazed: {st}')

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
        eave_y = floor + F.STOREY_H  # shell top is eave_y - 1; flat roof slab here
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


def _entrance_row(**overrides) -> dict:
    """Minimal placed facade_plan entrance for build tests."""
    import provenance as prov

    row = {
        'id': 'T-A',
        'portal_id': 'T-A',
        'face': 'S',
        'leaves': 1,
        'block': 'iron_door',
        'at': [13, 25],
        'floor_offset': 0,
        'provenance': prov.provenance_record(
            source='register_entry', confidence='high',
        ),
    }
    row.update(overrides)
    return row


class FacadePlanEntranceBuildTest(unittest.TestCase):
    """Seams: build places doors from facade_plan only (#146), including unresolved."""

    def setUp(self):
        # Wide enough for a 2-leaf door plus jambs on the south face.
        self.cells = {(x, z) for x in range(10, 17) for z in range(20, 25)}

    def spec(self, entrances, *, facades=None):
        return {
            'name': 'door_feature',
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
            
            'facade_plan': _glazing_plan(facades or {}, {'A': {'faces': {}, 'entrances': entrances}}),
        }

    def build_plan(self, entrances, *, facades=None, cells=None, world=None, zone_data=None):
        buf = io.StringIO()
        with redirect_stdout(buf):
            plan = F.build(
                self.spec(entrances, facades=facades),
                footprints={'A': cells if cells is not None else self.cells},
                world=world or _StoneWorld(),
                zone_data=zone_data,
            )
        return plan, buf.getvalue()

    def test_door_uses_lidar_and_refuses_stale_live_ground(self):
        class GroundWorld:
            def __init__(self, y):
                self.y = y

            def block(self, x, y, z):
                return 'minecraft:stone' if y <= self.y else 'minecraft:air'

        grids = {'A': {'x0': 9, 'z0': 19, 'ground_y': np.full((7, 9), 51)}}
        for live_y, offset, expected_y in ((49, 0, 52), (49, 2, 54), (42, 0, None)):
            with self.subTest(live_y=live_y, offset=offset):
                plan, out = self.build_plan(
                    [_entrance_row(floor_offset=offset)],
                    world=GroundWorld(live_y), zone_data=grids,
                )
                lower = [p for p, state in plan.cells.items()
                         if 'iron_door' in state and 'half=lower' in state]
                if expected_y is None:
                    self.assertEqual(lower, [])
                    self.assertIn('door T-A: refused', out)
                    self.assertIn('live y=42', out)
                    self.assertIn('lidar y=51', out)
                else:
                    self.assertEqual([p[1] for p in lower], [expected_y])

    def test_high_live_rim_does_not_put_unzoned_door_in_roof(self):
        class HighRimWorld:
            def block(self, x, y, z):
                return 'minecraft:stone' if y <= 58 else 'minecraft:air'
        spec = self.spec([_entrance_row(leaves=1, at=[13, 24])])
        spec['storeys']['A'] = 1
        spec['floor_min'] = 55
        grids = {'A': {'x0': 9, 'z0': 19, 'ground_y': np.full((7, 9), 55)}}
        with redirect_stdout(io.StringIO()):
            plan = F.build(spec, footprints={'A': self.cells}, world=HighRimWorld(), zone_data=grids)
        lower = [p for p, state in plan.cells.items() if '_door[' in state and 'half=lower' in state]
        self.assertEqual(len(lower), 1)
        self.assertEqual(lower[0][1], 56)
        self.assertIn('slab', plan.cells[(13, 58, 22)])

    def test_entrance_cover_preserves_other_real_ground_ownership(self):
        from journal_testkit import JournalWorld
        pos = (13, 52, 26)
        with JournalWorld() as journal:
            journal.build('ground_owner', 'ground', {pos: 'minecraft:light_gray_concrete'}, cell=(0, 0))
            plan, _ = self.build_plan([_entrance_row(awning_like={'kind': 'covered_area', 'depth': 3})])
        self.assertFalse(pos in plan.cells)

    def test_entrance_awning_survives_roof_and_keeps_door_walkable(self):
        plan, out = self.build_plan([
            _entrance_row(awning_like={'kind': 'awning'}),
        ])
        for x in (12, 13, 14):
            self.assertEqual(plan.cells.get((x, 52, 25)),
                             'minecraft:smooth_stone_slab[type=bottom]')
        self.assertEqual(plan.cells.get((13, 51, 25)), 'minecraft:air')
        self.assertEqual(plan.cells.get((13, 50, 25)), 'minecraft:air')
        self.assertIn('door T-A cover awning depth 1', out)

    def test_covered_area_depth_and_double_leaf_width(self):
        plan, out = self.build_plan([
            _entrance_row(leaves=2, awning_like={'kind': 'covered_area', 'depth': 3}),
        ])
        slabs = {pos for pos, state in plan.cells.items()
                 if pos[1] == 52 and pos[2] >= 25 and 'slab' in state}
        self.assertEqual(slabs, {(x, 52, z) for x in range(12, 16) for z in range(25, 28)})
        self.assertIn('door T-A cover covered_area depth 3', out)

    def test_porch_roof_posts_stand_at_front_corners(self):
        plan, out = self.build_plan([
            _entrance_row(awning_like={'kind': 'porch_roof', 'depth': 2}),
        ])
        for x in (12, 14):
            for y in (50, 51):
                self.assertEqual(plan.cells.get((x, y, 26)), 'minecraft:bricks')
            self.assertEqual(plan.cells.get((x, 52, 26)),
                             'minecraft:smooth_stone_slab[type=bottom]')
        for y in (50, 51):
            self.assertNotIn('bricks', plan.cells.get((13, y, 26), ''))
        self.assertIn('door T-A cover porch_roof depth 2', out)

    def test_zero_cover_depth_defaults_to_one(self):
        plan, out = self.build_plan([
            _entrance_row(awning_like={'kind': 'awning', 'depth': 0}),
        ])
        self.assertEqual(plan.cells.get((13, 52, 25)),
                         'minecraft:smooth_stone_slab[type=bottom]')
        self.assertNotIn('slab', plan.cells.get((13, 52, 26), ''))
        self.assertIn('cover awning depth 1', out)

    def test_east_facing_cover_extends_outward_and_uses_roof_material(self):
        spec = self.spec([_entrance_row(
            face='E', at=[16, 22], awning_like={'kind': 'awning', 'depth': 2},
        )])
        spec['roofs']['A']['material'] = 'brick'
        with redirect_stdout(io.StringIO()):
            plan = F.build(spec, footprints={'A': self.cells}, world=_StoneWorld())
        slabs = {pos for pos, state in plan.cells.items()
                 if pos[1] == 52 and pos[0] >= 17 and 'slab' in state}
        self.assertEqual(slabs, {(x, 52, z) for x in (17, 18) for z in (21, 22, 23)})
        self.assertEqual(plan.cells[(18, 52, 22)], 'minecraft:brick_slab[type=bottom]')

    def test_cover_skips_building_mass_beside_entrance(self):
        cells = self.cells | {(12, 25), (12, 26)}
        plain, _ = self.build_plan([_entrance_row()], cells=cells)
        covered, _ = self.build_plan([
            _entrance_row(awning_like={'kind': 'awning', 'depth': 2}),
        ], cells=cells)
        for z in (25, 26):
            self.assertEqual(covered.cells.get((12, 52, z)), plain.cells.get((12, 52, z)))
        self.assertEqual(covered.cells.get((13, 52, 26)),
                         'minecraft:smooth_stone_slab[type=bottom]')

    def test_none_cover_leaves_plan_unchanged(self):
        plain, _ = self.build_plan([_entrance_row()])
        none, out = self.build_plan([_entrance_row(awning_like={'kind': 'none'})])
        self.assertEqual(none.cells, plain.cells)
        self.assertNotIn(' cover ', out)

    def test_unresolved_entrance_builds_no_cover(self):
        import provenance as prov

        plan, out = self.build_plan([_entrance_row(
            awning_like={'kind': 'porch_roof', 'depth': 3},
            provenance=prov.provenance_record(
                source='inference', confidence='low', unresolved_reason='no photo',
            ),
        )])
        empty, _ = self.build_plan([])
        self.assertEqual(plan.cells, empty.cells)
        self.assertNotIn(' cover ', out)

    def test_double_door_mirrored_hinges_and_printed(self):
        plan, out = self.build_plan([_entrance_row(leaves=2, block='iron_door')])
        lowers = [(pos, st) for pos, st in plan.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 2, lowers)
        hinges = sorted(st.split('hinge=')[1].split(',')[0] for _, st in lowers)
        self.assertEqual(hinges, ['left', 'right'])
        xs = sorted(pos[0] for pos, _ in lowers)
        self.assertEqual(xs[1] - xs[0], 1)
        self.assertEqual(door_frame_issues(plan, _StoneWorld().block), [])
        self.assertIn('T-A', out)
        self.assertIn('iron_door', out)
        self.assertIn('2-leaf', out)

    def test_single_coloured_door_uses_block(self):
        plan, out = self.build_plan([_entrance_row(leaves=1, block='crimson_door')])
        lowers = [st for st in plan.cells.values() if 'crimson_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 1)
        self.assertEqual(door_frame_issues(plan, _StoneWorld().block), [])
        self.assertIn('crimson_door', out)

    def test_double_door_survives_facade_glazing(self):
        """Both leaves stay doors when a facade table glazes the south face."""
        plan, _out = self.build_plan(
            [_entrance_row(leaves=2, block='iron_door')],
            facades={'A': {'S': [{'storey': 1, 'glazed_h': 3, 'pier': 1}]}},
        )
        lowers = [(pos, st) for pos, st in plan.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        uppers = [(pos, st) for pos, st in plan.cells.items()
                  if 'iron_door' in st and 'half=upper' in st]
        self.assertEqual(len(lowers), 2, lowers)
        self.assertEqual(len(uppers), 2, uppers)
        for (x, y, z), _ in lowers + uppers:
            self.assertNotIn('glass_pane', plan.cells.get((x, y, z), ''))

    def test_places_at_wall_nearest_entrance_at(self):
        """Designer ``at`` picks the wall cell; no portal-reach chooser."""
        cells = {(x, z) for x in range(10, 18) for z in range(20, 61)}
        for lo in (21, 24, 27, 31, 34, 37):
            cells |= {(18, lo), (18, lo + 1)}
        plan, out = self.build_plan(
            [_entrance_row(
                id='18-A', portal_id='18-A', face='E', leaves=1,
                block='iron_door', at=[18, 29],
            )],
            cells=cells,
        )
        self.assertNotIn(': skipped', out, out)
        lowers = [(pos, st) for pos, st in plan.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 1, lowers)
        door_xz = (lowers[0][0][0], lowers[0][0][2])
        self.assertIn(door_xz, {(17, 29), (17, 30)}, door_xz)

    def test_unresolved_entrance_not_placed(self):
        import provenance as prov

        unresolved = _entrance_row(
            id='99-Z',
            provenance=prov.provenance_record(
                source='inference', confidence='low',
                unresolved_reason='no photo of this face',
            ),
        )
        # drop placement fields that placed rows need — unresolved may omit them
        for k in ('face', 'leaves', 'block', 'at'):
            unresolved.pop(k, None)
        placed = _entrance_row(id='T-A', leaves=1, block='iron_door')
        plan, out = self.build_plan([placed, unresolved])
        lowers = [st for st in plan.cells.values() if '_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 1)
        self.assertIn('99-Z', out)
        self.assertIn('unresolved', out.lower())
        spruce = [st for st in plan.cells.values() if 'spruce_door' in st]
        self.assertEqual(spruce, [])

    def test_no_facade_entrances_places_no_legacy_spruce(self):
        plan, _out = self.build_plan([])
        doors = [st for st in plan.cells.values() if '_door' in st and 'trapdoor' not in st]
        self.assertEqual(doors, [])

    def test_door_row_follows_outside_ground_not_below(self):
        """I-017: door floor y is outside ground + floor_offset (never below outside)."""

        class HighPavementWorld:
            """Interior stone to y=49; pavement south of the wall at y=52."""

            def block(self, x, y, z):
                if z >= 25 and y <= 52:
                    return 'minecraft:stone'
                return 'minecraft:stone' if y <= 49 else 'minecraft:air'

        ground = np.full((6, 7), 49)
        ground[-1, :] = 52
        plan, _out = self.build_plan(
            [_entrance_row(leaves=1, block='iron_door', at=[13, 25], floor_offset=0)],
            world=HighPavementWorld(),
            zone_data={'A': {'x0': 10, 'z0': 20, 'ground_y': ground}},
        )
        lowers = [(pos, st) for pos, st in plan.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 1, lowers)
        _x, y, _z = lowers[0][0]
        # outside ground top is 52 → floor block 52 → lower door leaf at 53
        self.assertEqual(y, 53)
        self.assertGreaterEqual(y - 1, 52)

    def test_door_clears_inward_vestibule_so_door_to_wall_is_zero(self):
        """#143: inward column at door height is air; floaters.door_to_wall does not fire."""
        import floaters as fl

        class HighPavementWorld:
            """Interior stone to y=49; pavement south of the wall at y=52."""

            def block(self, x, y, z):
                if z >= 25 and y <= 52:
                    return 'minecraft:stone'
                return 'minecraft:stone' if y <= 49 else 'minecraft:air'

        ground = np.full((6, 7), 49)
        ground[-1, :] = 52
        plan, _out = self.build_plan(
            [_entrance_row(leaves=1, block='iron_door', at=[13, 25], floor_offset=0)],
            world=HighPavementWorld(),
            zone_data={'A': {'x0': 10, 'z0': 20, 'ground_y': ground}},
        )
        lowers = [(pos, st) for pos, st in plan.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 1, lowers)
        (x, y, z), st = lowers[0]
        # Face S → inward is north (−z). Opening height is lower + upper.
        for dy in (0, 1):
            key = (x, y + dy, z - 1)
            cell = plan.cells.get(key, 'minecraft:air')
            self.assertTrue(
                fl.is_air(cell) or cell.split('[')[0].endswith(':air'),
                f'inward vestibule {key} must be air, got {cell!r}',
            )

        def block_at(bx, by, bz):
            return plan.cells.get((bx, by, bz)) or HighPavementWorld().block(bx, by, bz)

        flags = fl.cell_glitches(block_at, x, y, z, st)
        self.assertFalse(
            any(r['kind'] == 'door_to_wall' for r in flags),
            flags,
        )

    def test_door_opening_clears_are_kept_against_earlier_build_clash(self):
        """#143: stoop/inward air survives owned-ground clash (keep_edits off)."""
        cells = {(x, z) for x in range(10, 17) for z in range(20, 25)}
        buf = io.StringIO()
        with redirect_stdout(buf):
            b = F.Build(
                self.spec([_entrance_row(leaves=1, block='iron_door', at=[13, 25])]),
                footprints={'A': cells},
                world=_StoneWorld(),
            )
            b.build()
        lowers = [(pos, st) for pos, st in b.p.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 1, lowers)
        (x, y, z), _ = lowers[0]
        # Face S → out +z, in −z; floor under door is y-1.
        b_floor = y - 1
        for ox, oz in ((0, 1), (0, -1)):
            for yy in range(b_floor + 1, b_floor + 4):
                key = (x + ox, yy, z + oz)
                self.assertIn(key, b.keep, f'opening clear {key} must be kept')
                self.assertTrue(
                    'air' in (b.p.cells.get(key) or 'air'),
                    f'opening clear {key} missing from plan',
                )
        lintel = (x, b_floor + 3, z)
        self.assertIn(lintel, b.keep)
        self.assertIn('bricks', b.p.cells.get(lintel, ''), lintel)
        self.assertEqual(door_frame_issues(b.p, _StoneWorld().block), [])
        self.assertTrue(hasattr(b.p, 'keep') and lintel in b.p.keep,
                        'plan.keep must carry door keeps for apply clash')

    def test_stepped_wall_door_stays_on_register_with_built_jambs(self):
        """#164: stepped face keeps the door on the register cell; jambs built; guard clean."""
        # Long south face; after shell, strip coplanar jamb columns beside the
        # register (as on a 1–2 cell step) so walls-membership framing is gone
        # and solids at door height are air — the pre-fix pick then jumps away.
        cells = {(x, z) for x in range(10, 40) for z in range(18, 25)}
        reg = (25, 24)
        buf = io.StringIO()
        with redirect_stdout(buf):
            b = F.Build(
                self.spec([_entrance_row(
                    id='18-A', portal_id='18-A', face='S', leaves=1,
                    block='iron_door', at=list(reg),
                )]),
                footprints={'A': cells},
                world=_StoneWorld(),
            )
            # Shell + floors only; place the door ourselves after simulating the step.
            b.spec['facade_plan'] = {'A': {'faces': {}, 'entrances': []}}
            b.build()
            f = b.features[0]
            self.assertIn(reg, f.walls)
            for lx in (1, -1):
                n = (reg[0] + lx, reg[1])
                f.walls.discard(n)
                for y in range(f.b, f.b + f.storeys * F.STOREY_H):
                    b.set(n[0], y, n[1], 'air')
            f.inner = f.cells - f.walls
            b.spec['facade_plan'] = {'A': {'faces': {}, 'entrances': [
                _entrance_row(
                    id='18-A', portal_id='18-A', face='S', leaves=1,
                    block='iron_door', at=list(reg),
                ),
            ]}}
            b.plan_doors(f)
        out = buf.getvalue()
        lowers = [(pos, st) for pos, st in b.p.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 1, lowers)
        (x, y, z), _ = lowers[0]
        self.assertEqual((x, z), reg, f'door moved from register; out={out!r}')
        self.assertNotIn('moved', out.lower(), out)
        b_floor = y - 1
        for lx in (1, -1):
            for yy in (b_floor + 1, b_floor + 2):
                key = (x + lx, yy, z)
                self.assertTrue(
                    b._is_solid(x + lx, yy, z),
                    f'missing built jamb at {key}',
                )
                self.assertIn(key, b.keep, f'built jamb {key} must be kept')
        self.assertEqual(door_frame_issues(b.p, _StoneWorld().block), [])

    def test_set_door_leaf_builds_missing_jamb_solids(self):
        """#164: _set_door_leaf fills air jambs at b+1/b+2 and keeps them."""
        cells = {(x, z) for x in range(10, 17) for z in range(20, 25)}
        buf = io.StringIO()
        with redirect_stdout(buf):
            b = F.Build(
                self.spec([]),
                footprints={'A': cells},
                world=_StoneWorld(),
            )
            b.build()
        f = b.features[0]
        wall = next(c for c in sorted(f.walls) if b.exposed(c, 'S') and c[1] == 24)
        for lx in (1, -1):
            for yy in (f.b + 1, f.b + 2):
                b.set(wall[0] + lx, yy, wall[1], 'air')
        b._set_door_leaf(wall, 'S', 'iron_door', 'left', f.b)
        for lx in (1, -1):
            for yy in (f.b + 1, f.b + 2):
                key = (wall[0] + lx, yy, wall[1])
                self.assertTrue(b._is_solid(*key), key)
                self.assertIn(key, b.keep, key)
        self.assertEqual(door_frame_issues(b.p, _StoneWorld().block), [])

    def test_moved_door_prints_distance_from_register(self):
        """#164: when the door cannot sit on the register coordinate, print distance."""
        cells = {(x, z) for x in range(10, 18) for z in range(20, 25)}
        # Register is south of the mass — nearest wall is one block north.
        plan, out = self.build_plan([
            _entrance_row(
                id='18-A', portal_id='18-A', face='S', leaves=1,
                block='iron_door', at=[13, 26],
            ),
        ], cells=cells)
        lowers = [(pos, st) for pos, st in plan.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 1, lowers)
        (x, _y, z), _ = lowers[0]
        dist = abs(x - 13) + abs(z - 26)
        self.assertGreater(dist, 0)
        self.assertRegex(out, rf'door 18-A:.*moved {dist} .*from register')

    def test_stepped_wall_double_door_two_leaves_same_plane(self):
        """#165: double entrance on a stepped south face lands two coplanar leaves."""
        # Staircase mass: 2-cell treads stepping south. Primary at (15,22) has
        # interior; lateral (14,22) is a step-corner wall (no interior behind) so
        # the old face_ok candidate set refused it — pair must still land.
        cells = set()
        for x in range(10, 22):
            zmax = 20 + (x - 10) // 2
            for z in range(15, zmax + 1):
                cells.add((x, z))
        buf = io.StringIO()
        with redirect_stdout(buf):
            b = F.Build(
                self.spec([
                    _entrance_row(
                        id='40-A', portal_id='40-A', face='S', leaves=2,
                        block='iron_door', at=[15, 22],
                    ),
                ]),
                footprints={'A': cells},
                world=_StoneWorld(),
            )
            b.build()
        out = buf.getvalue()
        self.assertNotIn('no pair cell', out, out)
        self.assertIn('2-leaf', out)
        lowers = [(pos, st) for pos, st in b.p.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 2, lowers)
        xz = sorted((pos[0], pos[2]) for pos, _ in lowers)
        self.assertEqual(xz[0][1], xz[1][1], 'leaves must share one plane (same z)')
        self.assertEqual(abs(xz[0][0] - xz[1][0]), 1)
        # Outer jambs beyond the pair are solid; the free-standing step jamb
        # outside the footprint is written into keep (#164/#165).
        lo_x, hi_x = xz[0][0], xz[1][0]
        z = xz[0][1]
        b_floor = lowers[0][0][1] - 1
        for jx in (lo_x - 1, hi_x + 1):
            for yy in (b_floor + 1, b_floor + 2):
                self.assertTrue(
                    b._is_solid(jx, yy, z),
                    f'missing step/jamb at {(jx, yy, z)}',
                )
        built_step = [
            (jx, yy, z)
            for jx in (lo_x - 1, hi_x + 1)
            for yy in (b_floor + 1, b_floor + 2)
            if (jx, yy, z) in b.keep
        ]
        self.assertTrue(built_step, 'expected a kept step block at the shared step')
        self.assertEqual(door_frame_issues(b.p, _StoneWorld().block), [])

    def test_stepped_wall_double_falls_back_without_lateral_pair(self):
        """#165: no coplanar wall neighbour → 1-leaf and the 'no pair cell' line."""
        cells = {(x, z) for x in range(10, 40) for z in range(18, 25)}
        reg = (25, 24)
        buf = io.StringIO()
        with redirect_stdout(buf):
            b = F.Build(
                self.spec([]),
                footprints={'A': cells},
                world=_StoneWorld(),
            )
            b.spec['facade_plan'] = {'A': {'faces': {}, 'entrances': []}}
            b.build()
            f = b.features[0]
            for lx in (1, -1):
                n = (reg[0] + lx, reg[1])
                f.walls.discard(n)
                for y in range(f.b, f.b + f.storeys * F.STOREY_H):
                    b.set(n[0], y, n[1], 'air')
            f.inner = f.cells - f.walls
            b.spec['facade_plan'] = {'A': {'faces': {}, 'entrances': [
                _entrance_row(
                    id='40-A', portal_id='40-A', face='S', leaves=2,
                    block='iron_door', at=list(reg),
                ),
            ]}}
            b.plan_doors(f)
        out = buf.getvalue()
        self.assertIn('no pair cell', out)
        self.assertIn('1-leaf', out)
        lowers = [(pos, st) for pos, st in b.p.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 1, lowers)
        self.assertEqual(door_frame_issues(b.p, _StoneWorld().block), [])


class ZonesSpecTest(unittest.TestCase):
    """Seam: load_spec accepts zones (flat or pitched); refuses bad from/levels."""

    def write(self, spec: dict) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 's.json'
        path.write_text(json.dumps(spec))
        return path

    def good(self) -> dict:
        return {
            'name': 'zones_demo',
            'ways': {'1': 'A'},
            'storeys': {'A': 4},
            'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
            'groups': [{'nos': ['A'], 'front': 'N'}],
            'zones': {
                'A': {'from': 'lidar', 'levels': [41, 45, 50], 'bucket': 4, 'axis_deg': 'auto'},
            },
        }

    def test_loads_zones_defaults(self):
        spec = F.load_spec(self.write(self.good()))
        self.assertEqual(spec['zones']['A']['levels'], [41, 45, 50])
        self.assertEqual(spec['zones']['A']['bucket'], 4)
        self.assertEqual(spec['zones']['A']['axis_deg'], 'auto')

    def test_auto_levels_accepted(self):
        g = self.good()
        g['zones']['A']['levels'] = 'auto'
        spec = F.load_spec(self.write(g))
        self.assertEqual(spec['zones']['A']['levels'], 'auto')

    def test_bad_zones_refused(self):
        good = self.good()
        cases = [
            {**good, 'zones': {'B': {'from': 'lidar', 'levels': [40]}}},
            {**good, 'zones': {'A': {'from': 'osm', 'levels': [40]}}},
            {**good, 'zones': {'A': {'from': 'lidar', 'levels': []}}},
            {**good, 'zones': {'A': {'from': 'lidar', 'levels': 'nope'}}},
            {**good, 'zones': {'A': {'from': 'lidar', 'levels': [40], 'bucket': 0}}},
        ]
        for bad in cases:
            with self.subTest(bad=bad.get('zones') or bad.get('roofs')):
                with self.assertRaises(SystemExit):
                    F.load_spec(self.write(bad))

        pitched = {
            **good,
            'roofs': {'A': {'kind': 'pitched', 'material': 'brick'}},
        }
        spec = F.load_spec(self.write(pitched))
        self.assertEqual(spec['roofs']['A']['kind'], 'pitched')
        self.assertIn('A', spec['zones'])


class ZoneMathTest(unittest.TestCase):
    """Seam: zone snapping, bucketing, and edge-wall exposure on a fixture grid."""

    def test_snap_to_levels_nearest_plateau(self):
        h = np.array([[40.2, 44.8], [46.1, 49.7]], dtype=float)
        got = F.snap_to_levels(h, [41, 45, 46, 50])
        self.assertEqual(got.tolist(), [[41.0, 45.0], [46.0, 50.0]])

    def test_bucket_zones_one_level_per_rotated_cell(self):
        # 8×4 footprint on axis 0: two 4-wide buckets along x should each take a mode
        zone = np.zeros((4, 8), dtype=float)
        zone[:, :4] = 41
        zone[:, 4:] = 50
        zone[1, 3] = 50  # speck in the left bucket → mode stays 41
        zone[2, 5] = 41  # speck in the right bucket → mode stays 50
        mask = np.ones_like(zone, dtype=bool)
        zz, xx = np.mgrid[0:4, 0:8]
        out = F.bucket_zones(zone, mask, xx, zz, bucket=4, axis_deg=0.0)
        self.assertTrue(np.all(out[:, :4] == 41))
        self.assertTrue(np.all(out[:, 4:] == 50))

    def test_edge_wall_expose_exterior_and_zone_step(self):
        # Three columns: downhill ground 40, mid roof 50, high roof 54; outside east
        roof = {(10, 20): 50, (11, 20): 54}
        ground = {
            (9, 20): 40, (10, 20): 42, (11, 20): 42, (12, 20): 42,
            (10, 19): 42, (10, 21): 42, (11, 19): 42, (11, 21): 42,
        }
        # West neighbour of (10,20) is outside → top is ground 40
        self.assertEqual(F.edge_wall_expose(roof, ground, (10, 20)), 40)
        # East neighbour of (10,20) is higher roof 54; west outside 40 → expose 40
        # Zone step: from high roof looking west onto 50
        self.assertEqual(F.edge_wall_expose(roof, ground, (11, 20)), 42)

    def test_assign_roof_zones_pipeline_square_snap_bucket(self):
        # 6×6 vector outline with a 1-block stair notch; two DSM plateaus
        outline = np.ones((6, 6), dtype=bool)
        outline[0, 5] = False  # notch / spur
        dsm = np.full((6, 6), 41.0)
        dsm[:, 3:] = 50.0
        zz, xx = np.mgrid[0:6, 0:6]
        readable, zone, levels, axis, _dropped = F.assign_roof_zones(
            dsm, outline, [41, 50], xx, zz, bucket=3, axis_deg=0.0,
        )
        self.assertEqual(levels, [41, 50])
        self.assertEqual(axis, 0.0)
        self.assertGreaterEqual(int(readable.sum()), int(outline.sum()) - 2)
        self.assertIn(41.0, set(zone[readable].tolist()))
        self.assertIn(50.0, set(zone[readable].tolist()))
        line = F.footprint_pad_line(outline, readable)
        self.assertTrue(line.startswith('footprint osm='))
        self.assertIn('readable=', line)
        self.assertIn('pad=', line)
        self.assertIn('cover=', line)

    def test_assign_roof_zones_drops_outline_cells_without_lidar_mass(self):
        """#116: zoned readable mask keeps only cells with nDSM ≥ ZONE_LIDAR_MASS_M."""
        outline = np.ones((4, 6), dtype=bool)
        # DSM metres AOD; ground_y chosen so left half is bare (< mass_m) and right has mass.
        dsm = np.full((4, 6), 50.0)
        ground_y = np.full((4, 6), 48)  # nDSM ~2 m after y_to_metres_aod — below default 3 m
        ground_y[:, 3:] = 40            # nDSM ~10 m — keep
        zz, xx = np.mgrid[0:4, 0:6]
        with mock.patch('world_frame.y_to_metres_aod', side_effect=lambda y: float(y)):
            readable, zone, _levels, _axis, _dropped = F.assign_roof_zones(
                dsm, outline, [50], xx, zz, bucket=3, axis_deg=0.0, ground_y=ground_y,
            )
        self.assertFalse(readable[:, :3].any(), 'bare-ground half must be dropped')
        self.assertTrue(readable[:, 3:].all(), 'mass half must remain')
        self.assertEqual(int(readable.sum()), 4 * 3)

    def test_drop_small_disconnected_footprint_pieces(self):
        """#153: drop 1- and 2-cell islands; keep a spur that touches the main mass."""
        # Grid [z, x]; world coords xs=col, zs=row.
        # Main 4×4 at rows 2..5, cols 2..5; touching 2-cell spur at cols 6..7 row 2;
        # island 1-cell at (0,0); island 2-cell at col 0 rows 6..7 (gap from main).
        mask = np.zeros((8, 8), dtype=bool)
        mask[2:6, 2:6] = True
        mask[2, 6] = True
        mask[2, 7] = True
        mask[0, 0] = True
        mask[6, 0] = True
        mask[7, 0] = True
        zz, xx = np.mgrid[0:8, 0:8]
        kept, dropped = F.drop_small_footprint_pieces(mask, xx, zz)
        self.assertTrue(kept[2:6, 2:6].all(), 'main mass kept')
        self.assertTrue(kept[2, 6] and kept[2, 7], 'touching spur kept')
        self.assertFalse(kept[0, 0], '1-cell island dropped')
        self.assertFalse(kept[6, 0] or kept[7, 0], '2-cell island dropped')
        self.assertEqual(int(kept.sum()), 4 * 4 + 2)
        counts = sorted(d['n'] for d in dropped)
        self.assertEqual(counts, [1, 2])
        for d in dropped:
            self.assertIn('at', d)
            self.assertRegex(
                F.dropped_footprint_line('A', d),
                rf'dropped disconnected footprint A n={d["n"]} at ',
            )
        # Through the zone pipeline: mass intersect leaves the same islands.
        outline = np.ones((8, 8), dtype=bool)
        dsm = np.full((8, 8), 50.0)
        ground_y = np.full((8, 8), 48)  # bare (< 3 m) everywhere…
        ground_y[mask] = 40             # …except the intended mass cells
        with mock.patch('world_frame.y_to_metres_aod', side_effect=lambda y: float(y)):
            readable, _zone, _levels, _axis, pipe_dropped = F.assign_roof_zones(
                dsm, outline, [50], xx, zz, bucket=3, axis_deg=0.0, ground_y=ground_y,
            )
        self.assertTrue(readable[2:6, 2:6].all())
        self.assertTrue(readable[2, 6] and readable[2, 7])
        self.assertFalse(readable[0, 0])
        self.assertFalse(readable[6, 0] or readable[7, 0])
        self.assertEqual(sorted(d['n'] for d in pipe_dropped), [1, 2])


class ZoneBuildTest(unittest.TestCase):
    """Seam: build() with injected zone grids places parapets and downhill walls."""

    def setUp(self):
        # 6×4 rectangle at x=10..15, z=20..23
        self.cells = {(x, z) for x in range(10, 16) for z in range(20, 24)}
        # [z, x] grids covering x0=9..16, z0=19..24
        self.x0, self.z0 = 9, 19
        depth, width = 6, 8  # z 19..24, x 9..16
        dsm = np.full((depth, width), 45.0)
        dsm[:, 4:] = 50.0  # x >= 13 → higher plateau
        ground = np.full((depth, width), 40)  # block Y
        ground[:, :2] = 38  # downhill on the west fringe
        self.zone_data = {
            'A': {'dsm': dsm, 'ground_y': ground, 'x0': self.x0, 'z0': self.z0},
        }

    def spec(self, edge=None):
        roof = {'kind': 'flat', 'material': 'smooth_stone'}
        if edge is not None:
            roof['edge'] = edge
        return {
            'name': 'unit_zones',
            'ways': {'99': 'A'},
            'storeys': {'A': 3},
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
            'zones': {
                'A': {'from': 'lidar', 'levels': [45, 50], 'bucket': 2, 'axis_deg': 0},
            },
        }

    def _build(self, edge=None):
        buf = io.StringIO()
        with mock.patch.object(F, 'metres_aod_to_y', side_effect=lambda m: int(m)), \
                redirect_stdout(buf):
            plan = F.build(
                self.spec(edge=edge), footprints={'A': self.cells}, world=_StoneWorld(),
                zone_data=self.zone_data,
            )
        return plan, buf.getvalue()

    def test_empty_zone_mask_refuses_with_feature_and_thresholds(self):
        for levels in ([45], 'auto'):
            spec = self.spec()
            spec['zones']['A']['levels'] = levels
            data = {'A': {**self.zone_data['A'], 'dsm': np.full((6, 8), 20.0)}}
            with self.assertRaisesRegex(SystemExit, r'A: no zone cells survived.*3-cell minimum.*mass threshold'):
                F.build(spec, footprints={'A': self.cells}, world=_StoneWorld(), zone_data=data)

    def test_single_zone_with_sufficient_lidar_mass_plans(self):
        spec = self.spec()
        spec['zones']['A']['levels'] = [45]
        with redirect_stdout(io.StringIO()):
            plan = F.build(spec, footprints={'A': self.cells}, world=_StoneWorld(), zone_data=self.zone_data)
        self.assertTrue(any(y == 56 and state == 'minecraft:smooth_stone' for (x, y, z), state in plan.cells.items()))

    def test_zoned_flat_roof_resolves_slab_family_to_full_block(self):
        spec = self.spec()
        spec['roofs']['A']['material'] = 'deepslate_tile'
        with redirect_stdout(io.StringIO()):
            plan = F.build(spec, footprints={'A': self.cells}, world=_StoneWorld(), zone_data=self.zone_data)
        self.assertIn('minecraft:deepslate_tiles', plan.cells.values())
        self.assertNotIn('minecraft:deepslate_tile', plan.cells.values())

    def test_zoned_build_default_none_no_parapet(self):
        plan, out = self._build()
        self.assertIn('footprint osm=', out)
        low_roof = [(x, z) for (x, y, z), st in plan.cells.items()
                    if y == 45 and st == 'minecraft:smooth_stone' and (x, z) in self.cells]
        high_roof = [(x, z) for (x, y, z), st in plan.cells.items()
                     if y == 50 and st == 'minecraft:smooth_stone' and (x, z) in self.cells]
        self.assertTrue(low_roof, 'expected 45 m plateau roof cells')
        self.assertTrue(high_roof, 'expected 50 m plateau roof cells')
        parapets = [pos for pos, st in plan.cells.items()
                    if st == 'minecraft:smooth_stone' and pos[1] in (46, 51)]
        self.assertFalse(parapets, 'absent edge must place no parapet')
        west_wall = [
            y for (x, y, z), st in plan.cells.items()
            if x == 10 and z == 21 and 'bricks' in st and y > 40
        ]
        self.assertTrue(west_wall, 'expected exposed wall on west face')

    def test_zoned_edge_all_matches_old_parapet_on_steps(self):
        """where=all: parapet on outer edges and zone steps (legacy behaviour)."""
        plan, _ = self._build(edge={'block': 'smooth_stone', 'where': 'all'})
        parapets = [pos for pos, st in plan.cells.items()
                    if st == 'minecraft:smooth_stone' and pos[1] in (46, 51)]
        self.assertTrue(parapets, 'expected parapet on roof edges')
        # High side of the interior zone drop (bucket shifts the step to x=14)
        self.assertEqual(
            plan.cells.get((14, 51, 21)), 'minecraft:smooth_stone',
            'where=all must edge interior zone steps',
        )

    def test_zoned_edge_outer_skips_zone_steps(self):
        """where=outer: parapet on mass outline only, not between plateaus."""
        plan, _ = self._build(edge={'block': 'polished_andesite', 'where': 'outer'})
        outer = [pos for pos, st in plan.cells.items()
                 if st == 'minecraft:polished_andesite' and pos[1] in (46, 51)]
        self.assertTrue(outer, 'expected outer parapet')
        # High side of interior zone drop: all N4 neighbours still in the roof mass
        self.assertNotEqual(
            plan.cells.get((14, 51, 21)), 'minecraft:polished_andesite',
            'where=outer must skip interior zone steps',
        )
        # Outer west face of the low plateau still edged
        west_outer = [pos for pos in outer if pos[0] == 10 and pos[1] == 46]
        self.assertTrue(west_outer, 'expected outer west parapet on low plateau')


class PitchedZonesRidgeTest(unittest.TestCase):
    """#125: pitched+zones → ridge stair-slabs; flat zoned plateaus unchanged."""

    def setUp(self):
        # 9×5 mass at x=10..18, z=20..24; grids cover x0=9..19, z0=19..25
        self.cells = {(x, z) for x in range(10, 19) for z in range(20, 25)}
        self.x0, self.z0 = 9, 19
        depth, width = 7, 11
        ground = np.full((depth, width), 40)
        # DSM ridge along x (eave 48 → peak 52 at centre column x=14)
        dsm = np.full((depth, width), 48.0)
        for xi, h in enumerate([48, 48, 49, 50, 52, 50, 49, 48, 48]):
            dsm[:, 1 + xi] = float(h)  # world x = x0+1+xi → 10..18
        self.ridge_zone_data = {
            'A': {'dsm': dsm, 'ground_y': ground, 'x0': self.x0, 'z0': self.z0},
        }
        # Two flat plateaus (no within-zone ridge) for the flat-kind check
        flat_dsm = np.full((depth, width), 45.0)
        flat_dsm[:, 6:] = 50.0  # x >= 15
        self.flat_zone_data = {
            'A': {'dsm': flat_dsm, 'ground_y': ground, 'x0': self.x0, 'z0': self.z0},
        }

    def _spec(self, kind: str, levels: list, material: str = 'brick'):
        return {
            'name': 'unit_pitched_zones',
            'ways': {'99': 'A'},
            'storeys': {'A': 3},
            'roofs': {'A': {'kind': kind, 'material': material}},
            'groups': [{'nos': ['A'], 'front': 'S', 'mode': 'each'}],
            'modifies': [],
            'floor_min': 40,
            'step_max': 4,
            'door_frame': True,
            'keep_edits': False,
            'wall': 'bricks',
            'floor': 'smooth_stone',
            'ledge_stair': False,
            'zones': {
                'A': {'from': 'lidar', 'levels': levels, 'bucket': 2, 'axis_deg': 0},
            },
        }

    def _build(self, kind: str, levels: list, zone_data: dict, material: str = 'brick'):
        buf = io.StringIO()
        with mock.patch.object(F, 'metres_aod_to_y', side_effect=lambda m: int(m)), \
                redirect_stdout(buf):
            plan = F.build(
                self._spec(kind, levels, material=material),
                footprints={'A': self.cells},
                world=_StoneWorld(),
                zone_data=zone_data,
            )
        return plan

    def test_zoned_shell_uses_regularised_heights_for_placement(self):
        half_grid = np.zeros((7, 11), dtype=int)
        half_grid[:, 5:] = 10  # raw five-block cliff inside one roof
        builder = F.Build(
            self._spec('pitched', [48]), footprints={'A': self.cells},
            world=_StoneWorld(), zone_data=self.ridge_zone_data,
        )
        with mock.patch.object(F, 'metres_aod_to_y', side_effect=lambda m: int(m)), \
                mock.patch.object(F, 'zone_ridge_half_steps', return_value=half_grid), \
                redirect_stdout(io.StringIO()):
            plan = builder.build()
        feature = builder.features[0]
        self.assertLess(feature.zone_roof_half[(14, 22)], 106)
        for (x, z), h in feature.zone_roof_half.items():
            self.assertEqual(feature.zone_roof[(x, z)], h // 2)
            state = plan.cells[(x, h // 2, z)]
            self.assertTrue('brick_stairs' in state or 'brick_slab' in state or state.endswith(':bricks'))
        from roof_lint import stair_shape_mismatches
        self.assertEqual(stair_shape_mismatches(plan.cells), [])

    def test_zoned_storey_floors_stop_below_eave(self):
        for kind, eave in (('pitched', 45), ('pitched', 46), ('flat', 45), ('flat', 46)):
            with self.subTest(kind=kind, eave=eave):
                plan = self._build(kind, [eave], self.ridge_zone_data, material='brick')
                floors = [pos for pos, state in plan.cells.items()
                          if state == 'minecraft:smooth_stone'
                          and (pos[0], pos[2]) in self.cells]
                self.assertTrue(any(y == 43 for x, y, z in floors), 'lower storey lost its floor')
                self.assertEqual([pos for pos in floors if pos[1] >= eave], [])

    def test_pitched_zones_preserve_distinct_flat_plateau_levels(self):
        builder = F.Build(
            self._spec('pitched', [45, 50]), footprints={'A': self.cells},
            world=_StoneWorld(), zone_data=self.flat_zone_data,
        )
        with mock.patch.object(F, 'metres_aod_to_y', side_effect=lambda m: int(m)), \
                mock.patch.object(F, 'zone_ridge_half_steps', return_value=np.zeros((7, 11), dtype=int)), \
                redirect_stdout(io.StringIO()):
            pitched = builder.build()
        feature = builder.features[0]
        self.assertEqual({feature.zone_roof[c] for c in feature.cells}, {45, 50})
        for c in feature.cells:
            self.assertEqual(feature.zone_roof[c], feature.zone_level[c])
        # The semantic zone step has an exposed wall, not a noisy roof cliff.
        # Smoothing across it would lower the taller mass toward the low wing.
        high = {pos for pos, st in pitched.cells.items()
                if pos[1] == 50 and st == 'minecraft:bricks' and (pos[0], pos[2]) in self.cells}
        self.assertTrue(high)

    def test_zoned_shell_backs_horizontal_gap_beside_stair(self):
        # Synthetic version of West's reported opening: an east-facing stair
        # at (6165,60,-5631), air in the taller south column at the same Y,
        # and a former full floor at Y59. That floor could not close a
        # horizontal slit through the stair's partly open south upper edge.
        cells = {(x, z) for x in range(6163, 6168) for z in range(-5633, -5628)}
        rows = [
            [120, 120, 120, 120, 120],
            [120, 120, 120, 122, 122],
            [120, 120, 120, 122, 124],
            [120, 120, 122, 124, 124],
            [120, 120, 122, 124, 124],
        ]
        raw = {(6163 + x, -5633 + z): h for z, row in enumerate(rows) for x, h in enumerate(row)}
        halfs = F.regularise_roof_half_heights(raw)
        builder = F.Build(self._spec('pitched', [48]), footprints={'A': cells}, world=_StoneWorld())
        feature = builder.features[0]
        feature.b = 41
        feature.zone_ground = {c: 41 for c in cells}
        feature.zone_level = {c: 49 for c in cells}
        feature.zone_eave = {c: 49 for c in cells}
        feature.zone_roof_half = halfs
        feature.zone_roof = {c: h // 2 for c, h in halfs.items()}
        builder.shell_zoned(0, feature)
        self.assertIn('facing=east', builder.p.cells[(6165, 60, -5631)])
        self.assertIn('shape=straight', builder.p.cells[(6165, 60, -5631)])
        self.assertEqual(builder.p.cells[(6165, 59, -5630)], 'minecraft:air')
        self.assertEqual(builder.p.cells[(6165, 60, -5630)], 'minecraft:bricks')
        self.assertIn('brick_stairs', builder.p.cells[(6165, 61, -5630)])

    def test_stair_backing_only_closes_open_edges_inside_taller_columns(self):
        builder = F.Build(self._spec('pitched', [48]), footprints={'A': self.cells}, world=_StoneWorld())
        # Test full versus partial upper-edge coverage for every stair shape.
        closed = {'straight': {'east'}, 'inner_left': {'east', 'north'},
                  'inner_right': {'east', 'south'}, 'outer_left': set(), 'outer_right': set()}
        for shape, solid_edges in closed.items():
            for face, (dx, dz) in F._ROOF_DELTA.items():
                with self.subTest(shape=shape, face=face):
                    builder.p.cells.clear()
                    builder.p.set(dx, 50, dz, 'air')
                    roof = {(0, 0): 50, (dx, dz): 51}
                    states = {(0, 0): f'brick_stairs[facing=east,half=bottom,shape={shape}]'}
                    builder._back_roof_stair_edges(roof, states)
                    expected = 'minecraft:air' if face in solid_edges else 'minecraft:bricks'
                    self.assertEqual(builder.p.cells[(dx, 50, dz)], expected)
        builder.p.cells.clear()
        builder.p.set(0, 50, 1, 'smooth_stone')
        states = {(0, 0): 'brick_stairs[facing=east,half=bottom,shape=straight]'}
        builder._back_roof_stair_edges({(0, 0): 50, (0, 1): 51}, states)
        self.assertEqual(builder.p.cells[(0, 50, 1)], 'minecraft:smooth_stone')
        builder.p.cells.clear()
        builder._back_roof_stair_edges({(0, 0): 50}, states)
        self.assertEqual(builder.p.cells, {}, 'backing must not expand outside the roof mass')

    def test_pitched_zones_ridge_slabs_flat_plateaus_unchanged(self):
        """Pitched+zones follows the lidar ridge with stairs; flat stays stepped decks."""
        pitched = self._build('pitched', [48], self.ridge_zone_data, material='brick')
        # 5×5 median flattens the peak to a 3-wide plateau; stairs sit on the flanks.
        slope_stairs = [
            st for (x, y, z), st in pitched.cells.items()
            if (x, z) in self.cells and x in (11, 12, 16, 17) and 'brick_stairs' in st
        ]
        self.assertTrue(slope_stairs, 'expected upslope stairs on the lidar pitch')
        self.assertTrue(all('half=bottom' in st for st in slope_stairs))
        self.assertTrue(all('facing=east' in st or 'facing=west' in st for st in slope_stairs))
        # Highest roof membrane block per column (ignore wall fill under the pitch).
        top: dict[tuple[int, int], tuple[int, str]] = {}
        for (x, y, z), st in pitched.cells.items():
            if (x, z) not in self.cells:
                continue
            if not ('brick_stairs' in st or 'brick_slab' in st or st.endswith(':bricks')):
                continue
            cur = top.get((x, z))
            if cur is None or y > cur[0]:
                top[(x, z)] = (y, st)
        ridge_ys = {top[c][0] for c in top if c[0] == 14}
        edge_ys = {top[c][0] for c in top if c[0] in (10, 18)}
        self.assertTrue(ridge_ys and edge_ys, 'expected ridge and eave membrane')
        self.assertGreater(
            max(ridge_ys), max(edge_ys),
            'ridge must rise above the zone eave (not a flat terrace)',
        )
        slope_tops = [top[c][1] for c in top if c[0] in (11, 12, 16, 17)]
        self.assertTrue(slope_tops, 'expected slope membrane columns')
        self.assertTrue(
            all('brick_stairs' in st for st in slope_tops),
            f'pitched+zones slope must be stairs, got {slope_tops!r}',
        )

        flat = self._build('flat', [45, 50], self.flat_zone_data, material='smooth_stone')
        low = [
            (x, z) for (x, y, z), st in flat.cells.items()
            if y == 45 and st == 'minecraft:smooth_stone' and (x, z) in self.cells
        ]
        high = [
            (x, z) for (x, y, z), st in flat.cells.items()
            if y == 50 and st == 'minecraft:smooth_stone' and (x, z) in self.cells
        ]
        self.assertTrue(low, 'flat zoned roof must keep the low plateau')
        self.assertTrue(high, 'flat zoned roof must keep the high plateau')
        pitched_slabs_on_flat = [
            st for st in flat.cells.values() if 'slab' in st and 'smooth_stone' in st
        ]
        self.assertFalse(
            pitched_slabs_on_flat,
            'flat zoned roofs must stay full-block decks, not stair-slabs',
        )


class ZoneGuardFixtureTest(unittest.TestCase):
    """Done-when: zoned build + guard keeps door stoop; 0 outside after prune."""

    def setUp(self):
        self.cells = {(x, z) for x in range(10, 16) for z in range(20, 24)}
        depth, width = 6, 8
        dsm = np.full((depth, width), 45.0)
        dsm[:, 4:] = 50.0
        ground = np.full((depth, width), 40)
        self.zone_data = {
            'A': {'dsm': dsm, 'ground_y': ground, 'x0': 9, 'z0': 19},
        }

    def spec(self):
        return {
            'name': 'unit_zones_guard',
            'ways': {'99': 'A'},
            'storeys': {'A': 3},
            'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
            'groups': [{'nos': ['A'], 'front': 'S', 'mode': 'each'}],
            'modifies': [],
            'floor_min': 40,
            'step_max': 4,
            'door_frame': True,
            'keep_edits': False,
            'wall': 'bricks',
            'floor': 'smooth_stone',
            'ledge_stair': False,
            'zones': {
                'A': {'from': 'lidar', 'levels': [45, 50], 'bucket': 2, 'axis_deg': 0},
            },
        }

    def test_prune_keeps_stoop_clears_outside_slabs_zero_guard(self):
        import osm_footprints as ofp

        buf = io.StringIO()
        with mock.patch.object(F, 'metres_aod_to_y', side_effect=lambda m: int(m)), \
                mock.patch('cell_registry.view_registry') as vr, \
                redirect_stdout(buf):
            vr.return_value.owned.return_value = {}
            b = F.Build(
                self.spec(), footprints={'A': self.cells}, world=_StoneWorld(),
                zone_data=self.zone_data,
            )
            b.build()
            f = b.features[0]
            wall = next(c for c in sorted(f.cells) if c[1] == max(z for _, z in f.cells))
            b._set_door_leaf(wall, 'S', 'spruce_door', 'left', f.b)
            stoop = (wall[0], wall[1] + 1)
            stoop_key = (stoop[0], f.b, stoop[1])
            self.assertIn(stoop_key, b.p.cells)
            self.assertIn('smooth_stone', b.p.cells[stoop_key])

            far_slab = (200, f.b + 5, 200)
            b.p.cells[far_slab] = 'minecraft:smooth_stone_slab[type=bottom]'
            stray = (201, f.b + 5, 201)
            b.p.cells[stray] = 'minecraft:bricks'

            outside = {
                far_slab: [b.p.cells[far_slab], 5, 'front', None, None],
                stray: [b.p.cells[stray], 5, 'front', None, None],
                stoop_key: [b.p.cells[stoop_key], 5, 'front', None, None],
            }
            with mock.patch.object(F.osm_footprints, 'guard', return_value={'outside': dict(outside), 'blind': []}):
                b._prune_outside_guard_hits()

            self.assertIn(stoop_key, b.p.cells, 'door stoop must survive zone prune')
            self.assertNotIn(far_slab, b.p.cells, 'outside slab must be dropped')
            self.assertIn(stray, b.p.cells, 'non-zoned outside brick must not be wiped')

            del b.p.cells[stray]

            class _Idx:
                def covered(self, x, z):
                    return True

                def near_footprint(self, x, z, margin=2):
                    cols = set(f.cells) | {stoop}
                    return any(
                        abs(x - cx) <= margin and abs(z - cz) <= margin
                        for cx, cz in cols
                    )

                def front_zone(self, x, z):
                    return False

                def nearest_street(self, x, z):
                    return None

            class _GroundWorld:
                def ground(self, x, z, top=120, bottom=0):
                    return 40

            rep = ofp.guard(b.p.cells, world=_GroundWorld(), idx=_Idx())
            self.assertEqual(rep['outside'], {})
            self.assertEqual(rep['blind'], [])
            self.assertIn(stoop_key, b.p.cells)


class LivePrefetchTest(unittest.TestCase):
    """Seam: feature_build plan construction batches LiveBlocks scans via prefetch.

    Without prefetch, each ``block()`` becomes one MCP ``block_scan_region``.
    With prefetch before hot loops, the same plan must use far fewer scans.
    """

    def setUp(self):
        self.cells = {(x, z) for x in range(10, 15) for z in range(20, 25)}
        self.spec = {
            'name': 'unit_feature',
            'ways': {'99': 'A'},
            'storeys': {'A': 3},
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
            
            'facade_plan': _glazing_plan({}, {}),
            'zones': {},
        }

    def _terrain(self) -> dict:
        """Stone through y<=49, air above, covering the footprint ring AABB."""
        blocks = {}
        for x in range(-5, 40):
            for z in range(0, 50):
                for y in range(30, 76):
                    blocks[(x, y, z)] = 'minecraft:stone' if y <= 49 else 'minecraft:air'
        return blocks

    def _live_world(self, blocks: dict):
        """World over LiveBlocks + FakeScanMcp; returns (world, mcp, tmp_cleanup)."""
        from block_store import LiveBlocks
        from site_kit import World
        from test_block_store import FakeScanMcp

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        region = Path(tmp.name) / 'saves' / 'w' / 'region'
        region.mkdir(parents=True)
        mcp = FakeScanMcp(blocks)
        store = LiveBlocks(
            region, allowed=[region], identity_check=lambda r: None,
            flusher=lambda: None, settle_seconds=0, mcp=mcp,
        )
        return World(store=store), mcp

    def test_prefetch_matches_stub_world_and_bounds_scan_calls(self):
        """Plan matches per-block stub path; scan count stays well below one-per-block."""
        from site_kit import Plan

        expected = F.build(self.spec, footprints={'A': self.cells}, world=_StoneWorld())
        world, mcp = self._live_world(self._terrain())
        with mock.patch('cell_registry.view_registry') as vr:
            vr.return_value.owned.return_value = {}
            got = F.build(self.spec, footprints={'A': self.cells}, world=world)
        self.assertEqual(got.cells, expected.cells)
        scans = sum(1 for name, _ in mcp.calls if name == 'block_scan_region')
        # Naive path measured ~7900 scans for this fixture; batched AABB is a few tiles.
        self.assertLessEqual(scans, 64, f'too many block_scan_region calls: {scans}')
        self.assertIsInstance(got, Plan)

    def test_planned_world_prefetch_skips_overlay_cells(self):
        """Overlay (PlannedWorld) must not ask MCP for positions served from .cells."""
        from site_kit import Plan, PlannedWorld

        class CountingWorld:
            def __init__(self):
                self.prefetched = []

            def prefetch(self, positions):
                self.prefetched.extend(tuple(p) for p in positions)

            def block(self, x, y, z):
                return 'minecraft:air'

        plan = Plan('overlay')
        plan.cells[(1, 50, 1)] = 'minecraft:bricks'
        base = CountingWorld()
        view = PlannedWorld(plan, base)
        view.prefetch([(1, 50, 1), (2, 50, 2), (3, 51, 3)])
        self.assertEqual(base.prefetched, [(2, 50, 2), (3, 51, 3)])


class OfflineGuardsTest(unittest.TestCase):
    """Seam: the offline plan step refuses unframed doors and jogging ridges with the live guard's message (#99)."""

    def setUp(self):
        self.cells = {(x, z) for x in range(10, 18) for z in range(20, 25)}
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        p = mock.patch.object(site_kit, 'OUT', Path(t.name))
        p.start()
        self.addCleanup(p.stop)

    def spec(self, door_frame):
        return {
            'name': 'unit_guard', 'ways': {'99': 'A'}, 'storeys': {'A': 2},
            'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
            'groups': [{'nos': ['A'], 'front': 'S', 'mode': 'each'}],
            'modifies': [], 'floor_min': 50, 'step_max': 4, 'door_frame': door_frame,
            'keep_edits': False, 'wall': 'bricks', 'floor': 'smooth_stone', 'ledge_stair': False,
        }

    def test_framed_doors_plan_passes_offline(self):
        F.build(self.spec(True), footprints={'A': self.cells}, world=_StoneWorld())

    def test_bad_door_frame_fails_offline_with_live_guard_message(self):
        # a feature whose planned door has a missing jamb piece (frame check is the live guard's own)
        bad = [(12, 50, 24, 'jamb east')]
        with mock.patch.object(site_kit, 'door_frame_issues', return_value=bad), \
                self.assertRaises(SystemExit) as offline:
            F.build(self.spec(True), footprints={'A': self.cells}, world=_StoneWorld())
        plan = site_kit.Plan('unit_guard')
        for dy, half in ((0, 'lower'), (1, 'upper')):
            plan.set(5, 50 + dy, 5, f'minecraft:oak_door[facing=south,half={half}]')
        with self.assertRaises(SystemExit) as live:
            site_kit.guard_door_frames(plan, lambda x, y, z: None)
        # count and pieces differ with the plan; the guard's own wording is identical
        tail = str(live.exception).split('; see ', 1)[1]
        self.assertIn('door frame piece(s) missing, e.g.', str(offline.exception))
        self.assertTrue(str(offline.exception).endswith(tail))

    def test_roof_jog_fails_offline(self):
        with mock.patch('roof_lint.ridge_jogs', return_value=[{'axis': 'x', 'row': 1}]):
            with self.assertRaises(SystemExit) as ctx:
                F.build(self.spec(True), footprints={'A': self.cells}, world=_StoneWorld())
        self.assertIn('roof ridge(s) jog sideways', str(ctx.exception))


class PitchedMaterialOwnershipTest(unittest.TestCase):
    """#170: photo-derived roof membership owns materials across lidar zones."""

    def _spec(self):
        return {
            'name': 'unit_pitch_materials', 'ways': {'99': 'A'},
            'storeys': {'A': 1},
            'roofs': {'A': {'kind': 'pitched', 'material': 'brick',
                            'zone_materials': {'45': 'polished_deepslate'}}},
            'groups': [{'nos': ['A'], 'front': 'S', 'mode': 'each'}],
            'wall': 'stone', 'floor': 'smooth_stone',
            'zones': {'A': {'levels': [45, 47, 48], 'from': 'lidar'}},
        }

    def _region(self, level=47, x0=-1, x1=10, z0=-1, z1=4):
        return {'level': level, 'polygon': [[x0, z0], [x1, z0], [x1, z1], [x0, z1]]}

    def _shell(self, heights, levels, regions=None):
        spec = self._spec()
        if regions is not None:
            spec['roofs']['A']['material_regions'] = regions
        builder = F.Build(spec, footprints={'A': set(heights)}, world=_StoneWorld())
        f = builder.features[0]
        f.b = 40
        f.zone_roof_half = heights
        f.zone_roof = {c: h // 2 for c, h in heights.items()}
        f.zone_ground = {c: 40 for c in heights}
        f.zone_level = levels
        f.zone_eave = levels
        f.roof_material_level = F.roof_material_levels(f.roof, levels)
        builder.shell_zoned(0, f)
        return {
            c: builder.p.cells[(c[0], h // 2, c[1])]
            for c, h in heights.items()
        }, f.roof_material_level

    def test_explicit_roof_membership_sets_stair_full_and_slab_materials(self):
        heights = {(x, z): h for z in range(3)
                   for x, h in enumerate([90, 92, 94, 96, 97, 96, 94, 92, 90])}
        levels = {c: 47 if 2 <= c[0] <= 6 else 45 for c in heights}

        blocks, owners = self._shell(heights, levels, [self._region()])

        self.assertEqual(set(owners.values()), {47})
        self.assertTrue(any('_stairs[' in st for st in blocks.values()))
        self.assertTrue(any(st == 'minecraft:bricks' for st in blocks.values()))
        self.assertTrue(any(st.startswith('minecraft:brick_slab[')
                            for st in blocks.values()))
        for c, state in blocks.items():
            self.assertTrue(state.startswith(('minecraft:brick_stairs[',
                                              'minecraft:bricks', 'minecraft:brick_slab[')),
                            (c, state))
        self.assertEqual(levels[(0, 0)], 45, 'evidence zones are not rewritten')

    def test_narrow_lower_deck_and_independent_pitch_keep_their_material(self):
        heights = {(x, z): h for z in range(3)
                   for x, h in enumerate([90, 92, 94, 92, 90, 90, 96, 96, 96])}
        # A separate low pitch meets the upper platform: it is not the main roof.
        heights.update({(x, 4): h for x, h in enumerate([90, 92, 94, 96, 96, 96])})
        levels = {c: 47 if (6 <= c[0] or (c[1] < 3 and c[0] == 2)
                            or (c[1] == 4 and c[0] >= 3)) else 45
                  for c in heights}

        blocks, owners = self._shell(
            heights, levels, [self._region(x0=0, x1=5, z0=0, z1=3)],
        )

        self.assertEqual(owners[(5, 1)], 45, 'one-block lower strip stays separate')
        for c, state in blocks.items():
            if (c[1] == 4 and c[0] < 3) or (c[1] < 3 and c[0] == 5):
                self.assertIn('polished_deepslate', state, (c, state))
            elif c[1] == 4:
                self.assertIn('brick', state, (c, state))
            elif c[0] < 5:
                self.assertIn('brick', state, (c, state))

    def test_unmapped_and_legacy_roofs_keep_zone_only_lookup(self):
        heights = {(x, 0): h for x, h in enumerate([90, 92, 94, 96, 96])}
        levels = {c: 45 if c[0] < 3 else 47 for c in heights}

        legacy, legacy_owners = self._shell(heights, levels)
        empty, empty_owners = self._shell(heights, levels, [])

        self.assertEqual(legacy, empty)
        self.assertEqual(legacy_owners, levels)
        self.assertEqual(empty_owners, levels)
        self.assertIn('polished_deepslate', legacy[(2, 0)])
        self.assertIn('brick', legacy[(3, 0)])

    def test_prepared_lidar_slope_uses_explicit_membership_in_shell(self):
        spec = self._spec()
        spec['zones']['A'].update(levels=[45, 47], bucket=1, axis_deg=0)
        spec['roofs']['A']['material_regions'] = [self._region(x0=3, x1=12, z0=0, z1=15)]
        profile = [45, 45, 45, 46, 47, 48, 49, 50, 49, 48, 47, 46, 45, 45, 45]
        dsm = np.tile(profile, (15, 1)).astype(float)
        cells = {(x, z) for x in range(15) for z in range(15)}
        builder = F.Build(
            spec, footprints={'A': cells}, world=_StoneWorld(),
            zone_data={'A': {'x0': 0, 'z0': 0, 'dsm': dsm,
                             'ground_y': np.full((15, 15), 40)}},
        )

        with mock.patch.object(F, 'metres_aod_to_y', side_effect=lambda m: int(m)):
            builder.prepare_zones()
        f = builder.features[0]
        builder.shell_zoned(0, f)

        c = (3, 7)
        self.assertEqual(f.zone_level[c], 45)
        self.assertEqual(f.roof_material_level[c], 47)
        self.assertTrue(builder.p.cells[(c[0], f.zone_roof[c], c[1])].startswith(
            'minecraft:brick_stairs[',
        ))
        self.assertEqual(f.roof_material_level[(0, 7)], 45)
        self.assertIn('A material region 0: level 47 columns 135', builder._zone_pad_lines)

    def test_regions_clip_to_roof_and_use_block_centres(self):
        levels = {(0, 0): 45, (1, 0): 47, (2, 0): 45}
        roof = {'material_regions': [self._region(x0=-100, x1=1.5)]}

        owners = F.roof_material_levels(roof, levels)

        self.assertEqual(owners, {(0, 0): 47, (1, 0): 47, (2, 0): 45})
        self.assertEqual(set(owners), set(levels))

    def test_rejects_overlapping_empty_and_unknown_level_regions(self):
        levels = {(0, 0): 45, (1, 0): 47}
        cases = [
            ([self._region(), self._region()], 'overlapping'),
            ([self._region(x0=10, x1=12)], 'no roof columns'),
            ([self._region(level=49)], 'unknown plateau'),
            ([{'level': 47, 'polygon': [[0, 0], [1, 0], [2, 0]]}], 'no roof columns'),
        ]
        for regions, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(SystemExit, message):
                F.roof_material_levels({'material_regions': regions}, levels)
        with self.assertRaisesRegex(SystemExit, 'no prepared roof columns'):
            F.roof_material_levels({'material_regions': [self._region()]}, {})

    def test_declared_plateau_may_own_roof_even_when_no_cells_snapped_to_it(self):
        levels = {(0, 0): 45}

        owners = F.roof_material_levels(
            {'material_regions': [self._region()]}, levels, known_levels=[45, 47],
        )

        self.assertEqual(owners, {(0, 0): 47})

    def test_region_validation_rejects_malformed_rows_and_unknown_plateaus(self):
        cases = [
            (None, 'must be a list'),
            ([{}], 'needs'),
            ([dict(self._region(), extra=True)], 'needs'),
            ([dict(self._region(), level=49)], 'unknown plateau'),
            ([dict(self._region(), level='47')], 'finite plateau'),
            ([dict(self._region(), level=True)], 'finite plateau'),
            ([dict(self._region(), level=float('nan'))], 'finite plateau'),
            ([dict(self._region(), polygon=[[0, 0], [1, 0]])], 'three vertices'),
            ([dict(self._region(), polygon=[[0, 0], [0, 0], [0, 0]])], 'distinct'),
            ([dict(self._region(), polygon=[[0, 0], [1, 0], [2, float('inf')]])], 'finite'),
            ([dict(self._region(), polygon=[[0, 0], [1, 0], [2, True]])], 'finite'),
        ]
        for regions, message in cases:
            with self.subTest(regions=regions):
                spec = self._spec()
                spec['roofs']['A']['material_regions'] = regions
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / 'spec.json'
                    path.write_text(json.dumps(spec))
                    with self.assertRaisesRegex(SystemExit, message):
                        F.load_spec(path)

    def test_regions_require_pitched_zoned_roofs_and_accept_auto_levels(self):
        for kind, zones in [('flat', {'A': {'levels': [45, 47], 'from': 'lidar'}}),
                            ('pitched', {})]:
            spec = self._spec()
            spec['roofs']['A'].update(kind=kind, material_regions=[self._region()])
            spec['zones'] = zones
            with self.subTest(kind=kind, zones=zones), self.assertRaisesRegex(SystemExit, 'requires'):
                F._validate_material_regions('spec', spec)
        spec = self._spec()
        spec['zones']['A']['levels'] = 'auto'
        spec['roofs']['A']['material_regions'] = [self._region()]
        F._validate_material_regions('spec', spec)


class ZoneMaterialsColourTest(unittest.TestCase):
    """#126: per-zone roof material override + dry-run colour suggestion."""

    def setUp(self):
        # Same two-plateau fixture as ZoneBuildTest (x=10..15, z=20..23).
        self.cells = {(x, z) for x in range(10, 16) for z in range(20, 24)}
        self.x0, self.z0 = 9, 19
        depth, width = 6, 8
        dsm = np.full((depth, width), 45.0)
        dsm[:, 4:] = 50.0  # x >= 13 → higher plateau
        ground = np.full((depth, width), 40)
        self.zone_data = {
            'A': {'dsm': dsm, 'ground_y': ground, 'x0': self.x0, 'z0': self.z0},
        }

    def spec(self):
        return {
            'name': 'unit_zone_materials',
            'ways': {'99': 'A'},
            'storeys': {'A': 3},
            'roofs': {
                'A': {
                    'kind': 'flat',
                    'material': 'smooth_stone',
                    'zone_materials': {
                        '45': 'gray_concrete',
                        '50': 'deepslate_tiles',
                    },
                },
            },
            'groups': [{'nos': ['A'], 'front': 'S', 'mode': 'each'}],
            'modifies': [],
            'floor_min': 40,
            'step_max': 4,
            'door_frame': True,
            'keep_edits': False,
            'wall': 'bricks',
            'floor': 'smooth_stone',
            'ledge_stair': False,
            'zones': {
                'A': {'from': 'lidar', 'levels': [45, 50], 'bucket': 2, 'axis_deg': 0},
            },
        }

    def test_per_zone_materials_and_dry_run_colour_suggest(self):
        """Two zones of one building can use different mats; dry-run prints colour + suggest."""
        # Inject 0..1 RGB: low plateau pale grey, high plateau dark blue-grey.
        aerial = {}
        for x, z in self.cells:
            aerial[(x, z)] = (0.62, 0.62, 0.60) if x < 13 else (0.28, 0.28, 0.32)
        buf = io.StringIO()
        with mock.patch.object(F, 'metres_aod_to_y', side_effect=lambda m: int(m)), \
                redirect_stdout(buf):
            plan = F.build(
                self.spec(),
                footprints={'A': self.cells},
                world=_StoneWorld(),
                zone_data=self.zone_data,
                aerial_rgb=aerial,
            )
        out = buf.getvalue()
        low = [
            (x, z) for (x, y, z), st in plan.cells.items()
            if y == 45 and st == 'minecraft:gray_concrete' and (x, z) in self.cells
        ]
        high = [
            (x, z) for (x, y, z), st in plan.cells.items()
            if y == 50 and st == 'minecraft:deepslate_tiles' and (x, z) in self.cells
        ]
        self.assertTrue(low, 'zone 45 must use gray_concrete override')
        self.assertTrue(high, 'zone 50 must use deepslate_tiles override')
        # Default material must not cover both plateaus when overrides are set.
        default_roofs = [
            st for (x, y, z), st in plan.cells.items()
            if (x, z) in self.cells and y in (45, 50) and st == 'minecraft:smooth_stone'
        ]
        self.assertFalse(default_roofs, 'zone_materials must override the label default')
        self.assertRegex(out, r'A zone 45:.*suggest \w+')
        self.assertRegex(out, r'A zone 50:.*suggest \w+')
        self.assertIn('rgb=', out)


class DetailsSpecTest(unittest.TestCase):
    """Seam: per-label details table (roof + facade greeblies) validates and defaults."""

    def write(self, spec: dict) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 's.json'
        path.write_text(json.dumps(spec))
        return path

    def good(self) -> dict:
        return {
            'name': 'detail_demo',
            'ways': {'1': 'A'},
            'storeys': {'A': 2},
            'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
            'groups': [{'nos': ['A'], 'front': 'S'}],
        }

    def test_details_optional_default_empty(self):
        spec = F.load_spec(self.write(self.good()))
        self.assertEqual(spec['details'], {})

    def test_loads_roof_and_facade_details(self):
        ok = {
            **self.good(),
            'details': {
                'A': [
                    {
                        'kind': 'plant_room',
                        'at': [12, 56, 22],
                        'size': [3, 2, 2],
                        'source': 'aerial NW plant box',
                    },
                    {
                        'kind': 'drainpipe',
                        'at': [10, 50, 24],
                        'size': [1, 6, 1],
                        'face': 'S',
                        'source': 'street photo south',
                    },
                ],
            },
        }
        spec = F.load_spec(self.write(ok))
        self.assertEqual(spec['details']['A'][0]['kind'], 'plant_room')
        self.assertEqual(spec['details']['A'][1]['face'], 'S')

    def test_bad_details_refused(self):
        good = self.good()
        base_row = {
            'kind': 'vent',
            'at': [11, 56, 21],
            'size': [1, 1, 1],
            'source': 'aerial',
        }
        cases = [
            {**good, 'details': {'B': [base_row]}},
            {**good, 'details': {'A': [{**base_row, 'kind': 'chimney'}]}},
            {**good, 'details': {'A': [{**base_row, 'at': [11, 21]}]}},
            {**good, 'details': {'A': [{**base_row, 'size': [0, 1, 1]}]}},
            {**good, 'details': {'A': [{**base_row, 'source': ''}]}},
            {**good, 'details': {'A': [{**base_row, 'kind': 'drainpipe'}]}},
            {**good, 'details': {'A': [{**base_row, 'kind': 'sign', 'face': 'X'}]}},
            {**good, 'details': {'A': 'vent'}},
        ]
        for bad in cases:
            with self.subTest(bad=bad.get('details')):
                with self.assertRaises(SystemExit):
                    F.load_spec(self.write(bad))

    def test_old_detail_kind_canopy_refused_naming_awning_like(self):
        """No silent alias: legacy details kind ``canopy`` must name awning_like."""
        good = self.good()
        row = {
            'kind': 'canopy',
            'at': [11, 55, 21],
            'size': [2, 1, 1],
            'face': 'S',
            'source': 'street photo',
        }
        with self.assertRaises(SystemExit) as ctx:
            F.load_spec(self.write({**good, 'details': {'A': [row]}}))
        msg = str(ctx.exception)
        self.assertIn('awning_like', msg)
        self.assertIn('canopy', msg)


class ReapplyGuardTest(unittest.TestCase):
    """Seam: a live run() refuses a plan with a landed build unless overwrite is set."""

    SPEC = {'name': 'guarded', 'ways': {'1': 'A'}, 'storeys': {'A': 1},
            'roofs': {'A': {'kind': 'flat', 'material': 'stone'}},
            'groups': [{'nos': ['A'], 'front': 'N'}], 'floor_min': 50}
    CELLS = {'A': {(x, z) for x in range(4) for z in range(4)}}

    def _run(self, seqs, **kw):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'spec.json'
            path.write_text(json.dumps(self.SPEC))
            (F.ROOT / 'verification/site').mkdir(parents=True, exist_ok=True)
            with mock.patch.object(F, 'landed_build_seqs', return_value=seqs), \
                    mock.patch.object(F, 'preview'), mock.patch.object(F, 'apply') as applied, \
                    redirect_stdout(io.StringIO()):
                F.run(path, do_apply=True, footprints=self.CELLS, world=_StoneWorld(), **kw)
        return applied

    def test_refuses_apply_over_landed_build(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run([417])
        self.assertIn('element_undo.py', str(ctx.exception))
        self.assertIn('--overwrite', str(ctx.exception))

    def test_overwrite_applies_over_landed_build(self):
        self.assertTrue(self._run([417], overwrite=True).called)

    def test_first_build_applies(self):
        self.assertTrue(self._run([]).called)


class DetailsBuildTest(unittest.TestCase):
    """Seam: details place after the shell; dry-run prints each kind and source."""

    def setUp(self):
        self.cells = {(x, z) for x in range(10, 18) for z in range(20, 25)}

    def spec(self, details):
        return {
            'name': 'unit_details',
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
            'facade_plan': _glazing_plan({}),
            'details': details,
        }

    def test_chimney_spec_normalizes_dimensions_and_refuses_invalid_shape(self):
        base = {'name': 'stack', 'ways': {'1': 'A'}, 'storeys': {'A': 1},
                'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
                'groups': [{'nos': ['A'], 'front': 'N'}]}
        row = {'kind': 'chimney', 'at': [13, 57, 22], 'height': 4,
               'base_diameter': 5, 'top_diameter': 3, 'shape': 'round', 'source': 'photo'}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'stack.json'
            path.write_text(json.dumps({**base, 'details': {'A': [row]}}))
            loaded = F.load_spec(path)
            self.assertEqual(loaded['details']['A'][0]['size'], [5, 4, 5])
            path.write_text(json.dumps({**base, 'details': {'A': [{**row, 'shape': 'triangle'}]}}))
            with self.assertRaisesRegex(SystemExit, 'round or square'):
                F.load_spec(path)

    def test_roof_and_facade_details_place_and_dry_run_lists_source(self):
        # Flat roof membrane sits at floor + 2*STOREY_H = 56.
        details = {
            'A': [
                {
                    'kind': 'plant_room',
                    'at': [12, 57, 21],
                    'size': [2, 2, 2],
                    'source': 'aerial plant room',
                },
                {
                    'kind': 'skylight',
                    'at': [15, 56, 22],
                    'size': [1, 1, 1],
                    'source': 'aerial skylight',
                },
                {
                    'kind': 'awning_like',
                    'at': [13, 55, 24],
                    'size': [2, 1, 1],
                    'face': 'S',
                    'source': 'street photo entrance awning_like',
                },
                {
                    'kind': 'drainpipe',
                    'at': [10, 50, 24],
                    'size': [1, 6, 1],
                    'face': 'S',
                    'source': 'street photo south pipe',
                },
            ],
        }
        buf = io.StringIO()
        with redirect_stdout(buf):
            plan = F.build(
                self.spec(details), footprints={'A': self.cells}, world=_StoneWorld(),
            )
        out = buf.getvalue()
        # Plant room sits on a solid pad (not hovering on the membrane slab).
        self.assertIn('minecraft:gray_concrete', plan.cells.get((12, 57, 21), ''))
        self.assertIn('minecraft:gray_concrete', plan.cells.get((13, 58, 22), ''))
        # Skylight sits on the roof membrane.
        sky = plan.cells.get((15, 56, 22), '')
        self.assertTrue('glass' in sky, sky)
        # Awning-like overhangs one block south of the south wall.
        overhang = plan.cells.get((13, 55, 25), '') or plan.cells.get((14, 55, 25), '')
        self.assertTrue('slab' in overhang, f'expected awning_like slab, got {overhang!r}')
        # Drainpipe on the south wall column.
        pipe = plan.cells.get((10, 52, 24), '')
        self.assertTrue('iron' in pipe, pipe)
        for kind, src in (
            ('plant_room', 'aerial plant room'),
            ('skylight', 'aerial skylight'),
            ('awning_like', 'street photo entrance awning_like'),
            ('drainpipe', 'street photo south pipe'),
        ):
            self.assertRegex(out, rf'detail {kind}.*source={re.escape(src)}')


    def detail_plan(self, rows, supports):
        """Plan only details over explicit MemoryBlocks roof fixtures."""
        roof = MemoryBlocks(supports, default='air')
        builder = F.Build(self.spec({'A': rows}), footprints={'A': self.cells}, world=_StoneWorld())
        for pos in supports:
            builder.p.set(*pos, roof.read(pos))
        builder.details()
        return builder

    def test_tapered_square_chimney_has_open_rim_and_band(self):
        row = {'kind': 'chimney', 'at': [13, 57, 22], 'size': [5, 4, 5],
               'base_diameter': 5, 'top_diameter': 3, 'height': 4, 'shape': 'square',
               'material': 'white_concrete', 'rim': 'stone',
               'band': {'offset': 1, 'block': 'black_concrete'}, 'source': 'photo fixture'}
        supports = {(x, 56, z): 'smooth_stone' for x in range(11, 16) for z in range(20, 25)}
        builder = self.detail_plan([row], supports)
        self.assertEqual(builder.p.cells[(15, 57, 22)], 'minecraft:white_concrete')
        self.assertEqual(builder.p.cells[(14, 60, 22)], 'minecraft:stone')
        self.assertEqual(builder.p.cells.get((15, 60, 22), 'minecraft:air'), 'minecraft:air')
        self.assertEqual(builder.p.cells[(13, 60, 22)], 'minecraft:air')
        band = [x for (x, y, z), state in builder.p.cells.items() if y == 58 and state == 'minecraft:black_concrete']
        self.assertEqual(max(band) - min(band) + 1, 4)

    def test_chimney_solidifies_its_slab_base_without_hover_gap(self):
        row = {'kind': 'chimney', 'at': [13, 57, 22], 'size': [3, 4, 3],
               'base_diameter': 3, 'top_diameter': 3, 'height': 4,
               'shape': 'round', 'source': 'photo fixture'}
        supports = {(x, 56, z): 'smooth_stone_slab[type=bottom]' for x in range(12, 15) for z in range(21, 24)}
        builder = self.detail_plan([row], supports)
        self.assertEqual(builder.p.cells[(12, 56, 22)], 'minecraft:smooth_stone')
        self.assertEqual(builder.p.cells[(12, 57, 22)], 'minecraft:white_concrete')
        self.assertEqual(builder.p.cells[(13, 60, 22)], 'minecraft:air')

    def test_chimney_rough_check_does_not_hide_corrupt_lidar(self):
        row = {'kind': 'chimney', 'at': [13, 57, 22], 'size': [3, 4, 3],
               'base_diameter': 3, 'top_diameter': 3, 'height': 4,
               'shape': 'round', 'source': 'photo fixture'}
        supports = {(x, 56, z): 'smooth_stone' for x in range(12, 15) for z in range(21, 24)}
        with mock.patch('lidar_provider.mc_heights', side_effect=ValueError('corrupt lidar')):
            with self.assertRaisesRegex(ValueError, 'corrupt lidar'):
                self.detail_plan([row], supports)

    def test_vertical_chimney_is_hollow_open_and_reports_rough_lidar_check(self):
        row = {'kind': 'chimney', 'at': [13, 57, 22], 'size': [5, 4, 5],
               'base_diameter': 5, 'top_diameter': 5, 'height': 4, 'shape': 'round',
               'material': 'white_concrete', 'source': 'photo fixture'}
        supports = {(x, 56, z): 'smooth_stone' for x in range(11, 16) for z in range(20, 25)}
        builder = self.detail_plan([row], supports)
        self.assertEqual(builder.p.cells.get((15, 60, 22)), 'minecraft:white_concrete')
        self.assertTrue(all(builder.p.cells.get((13, y, 22), 'minecraft:air') == 'minecraft:air' for y in range(57, 61)))
        self.assertFalse(any(y > 60 for x, y, z in builder.p.cells))
        self.assertTrue(any('planned height=4' in line and 'lidar' in line for line in builder.detail_report()))

    def test_skylight_is_recessed_and_rejects_whole_box_off_roof(self):
        row = {'kind': 'skylight', 'at': [12, 56, 21], 'size': [2, 1, 2], 'source': 'fixture'}
        supports = {(x, 56, z): 'smooth_stone' for x in (12, 13) for z in (21, 22)}
        builder = self.detail_plan([row], supports)
        self.assertTrue(all(builder.p.cells[pos] == 'minecraft:glass' for pos in supports))
        self.assertFalse(any(y > 56 for x, y, z in builder.p.cells))
        del supports[(13, 56, 22)]
        rejected = self.detail_plan([row], supports)
        self.assertFalse(any('glass' in state for state in rejected.p.cells.values()))
        self.assertTrue(any('cells off roof' in line and 'not built' in line and 'A at (12,56,21)' in line for line in rejected.detail_report()))

    def test_vents_fill_requested_box_and_cap_without_bars(self):
        for size in ([1, 1, 1], [2, 2, 2]):
            row = {'kind': 'vent', 'at': [12, 57, 21], 'size': size, 'source': 'fixture'}
            support = {(x, 56, z): 'smooth_stone' for x in (12, 13) for z in (21, 22)}
            builder = self.detail_plan([row], support)
            for x in range(12, 12 + size[0]):
                for z in range(21, 21 + size[2]):
                    for y in range(57, 57 + size[1]):
                        self.assertEqual(builder.p.cells[(x, y, z)], 'minecraft:gray_concrete')
                    self.assertIn('iron_trapdoor', builder.p.cells[(x, 57 + size[1], z)])
            self.assertFalse(any('iron_bars' in state for state in builder.p.cells.values()))

    def test_supported_roof_detail_box_unchanged(self):
        row = {'kind': 'plant_room', 'at': [12, 57, 21], 'size': [2, 2, 2], 'source': 'fixture'}
        supports = {(x, 56, z): 'smooth_stone' for x in (12, 13) for z in (21, 22)}
        builder = self.detail_plan([row], supports)
        for x in (12, 13):
            for z in (21, 22):
                for y in (57, 58):
                    self.assertEqual(builder.p.cells[(x, y, z)], 'minecraft:gray_concrete')
                self.assertIn('iron_trapdoor', builder.p.cells[(x, 59, z)])
        self.assertFalse(any('unsupported' in line for line in builder.detail_report()))

    def test_every_roof_detail_clips_diagonal_unsupported_columns(self):
        for kind in sorted(F.ROOF_DETAIL_KINDS - {'skylight', 'chimney'}):
            with self.subTest(kind=kind):
                row = {'kind': kind, 'at': [12, 57, 21], 'size': [2, 2, 2], 'source': 'fixture'}
                # Stair and slab both support a detail. The missing diagonal
                # corner must stay empty through the whole recipe, caps too.
                supports = {
                    (12, 56, 21): 'smooth_stone_slab[type=bottom]',
                    (13, 56, 21): 'smooth_stone',
                    (12, 56, 22): 'brick_stairs[facing=north,half=bottom,shape=straight]',
                }
                builder = self.detail_plan([row], supports)
                self.assertTrue(any(p[1] >= 57 for p in builder.p.cells))
                self.assertFalse(any('iron_bars' in state for state in builder.p.cells.values()))
                for y in (57, 58, 59):
                    self.assertNotIn((13, y, 22), builder.p.cells)
                self.assertIn(
                    f'detail {kind} A at (12,57,21) unsupported cells=1; clipped',
                    builder.detail_report(),
                )

    def test_build_rejects_whole_skylight_overhanging_roof(self):
        row = {'kind': 'skylight', 'at': [17, 56, 22], 'size': [2, 1, 1], 'source': 'fixture'}
        with redirect_stdout(io.StringIO()) as output:
            plan = F.build(self.spec({'A': [row]}), footprints={'A': self.cells}, world=_StoneWorld())
        self.assertNotIn('glass', plan.cells.get((17, 56, 22), ''))
        self.assertNotIn('glass', plan.cells.get((18, 56, 22), ''))
        self.assertIn('cells off roof', output.getvalue())
        self.assertIn('not built', output.getvalue())

    def test_unsupported_vent_anchor_is_clipped_even_if_box_has_support(self):
        row = {'kind': 'vent', 'at': [12, 57, 21], 'size': [2, 2, 1], 'source': 'fixture'}
        builder = self.detail_plan([row], {(13, 56, 21): 'smooth_stone'})
        self.assertNotIn((12, 57, 21), builder.p.cells)
        self.assertNotIn((12, 58, 21), builder.p.cells)

    def test_prior_detail_does_not_become_roof_support(self):
        rows = [
            {'kind': 'plant_room', 'at': [12, 57, 21], 'size': [1, 2, 1], 'source': 'fixture'},
            {'kind': 'skylight', 'at': [12, 59, 21], 'size': [1, 1, 1], 'source': 'fixture'},
        ]
        builder = self.detail_plan(rows, {(12, 56, 21): 'smooth_stone'})
        self.assertIn('iron_trapdoor', builder.p.cells[(12, 59, 21)])
        self.assertTrue(any('skylight A at (12,59,21)' in line and 'not built' in line for line in builder.detail_report()))

    def test_air_fluid_and_thin_roof_edge_are_not_support(self):
        for state in ('air', 'cave_air', 'void_air', 'water', 'iron_bars', 'moss_carpet', 'iron_trapdoor'):
            with self.subTest(state=state):
                row = {'kind': 'skylight', 'at': [12, 57, 21], 'size': [1, 1, 1], 'source': 'fixture'}
                builder = self.detail_plan([row], {(12, 56, 21): state})
                self.assertNotIn((12, 57, 21), builder.p.cells)

    def test_zone_material_and_full_roof_edge_support_details(self):
        rows = [{'kind': 'skylight', 'at': [12, 56, 21], 'size': [2, 1, 1], 'source': 'fixture'}]
        builder = F.Build(self.spec({'A': rows}), footprints={'A': self.cells}, world=_StoneWorld())
        builder.features[0].roof.update({'zone_materials': {'60': 'deepslate_tile'}, 'edge': {'block': 'polished_andesite'}})
        builder.p.set(12, 56, 21, 'deepslate_tile_stairs[facing=north,half=bottom,shape=straight]')
        builder.p.set(13, 56, 21, 'polished_andesite')
        builder.details()
        self.assertIn('glass', builder.p.cells[(12, 56, 21)])
        self.assertIn('glass', builder.p.cells[(13, 56, 21)])


class BlockFamilyVariantsTest(unittest.TestCase):
    """Seam: block_family_variants exposes full/slab/stairs without cross-mat swaps."""

    def test_brick_has_all_three(self):
        v = F.block_family_variants('brick')
        self.assertEqual(v['full'], 'bricks')
        self.assertEqual(v['slab'], 'brick_slab')
        self.assertEqual(v['stairs'], 'brick_stairs')

    def test_smooth_stone_has_no_stairs(self):
        v = F.block_family_variants('smooth_stone')
        self.assertEqual(v['full'], 'smooth_stone')
        self.assertEqual(v['slab'], 'smooth_stone_slab')
        self.assertNotIn('stairs', v)

    def test_deepslate_tile_full_is_plural(self):
        v = F.block_family_variants('deepslate_tile')
        self.assertEqual(v['full'], 'deepslate_tiles')
        self.assertEqual(v['stairs'], 'deepslate_tile_stairs')


class PitchedRoofStatesTest(unittest.TestCase):
    """Seam: pitched_roof_states — gable, hip, valley; shape from same-Y neighbours (#149, #169)."""

    def _place_membrane(self, half, states):
        """Write pitched membrane into MemoryBlocks at ``y = half // 2``."""
        from block_store import MemoryBlocks
        store = MemoryBlocks(default='air')
        for (x, z), st in states.items():
            store.write((x, half[(x, z)] // 2, z), st)
        store.flush()
        return store

    def _assert_shapes_match_neighbours(self, half, states):
        """Planned stair shapes must equal the vanilla same-Y neighbour rule."""
        from roof_lint import stair_shape_mismatches
        adjusted = F.regularise_roof_half_heights(half)
        store = self._place_membrane(adjusted, states)
        positions = [(x, adjusted[(x, z)] // 2, z) for x, z in states]
        self.assertEqual(stair_shape_mismatches(store.read_many(positions)), [])

    def test_gable_stairs_face_upslope_ridge_full(self):
        # Ridge along x at z=2; half-heights rise by 2 toward the ridge.
        half = {}
        mats = {}
        for x in range(0, 5):
            for z in range(0, 5):
                half[(x, z)] = 20 + 2 * (2 - abs(z - 2))
                mats[(x, z)] = 'brick'
        warn: list[str] = []
        states = F.pitched_roof_states(half, mats, warnings=warn)
        self.assertEqual(warn, [])
        # Low-z slope: +z is south, so facing south toward the ridge
        self.assertEqual(
            states[(2, 0)],
            'brick_stairs[facing=south,half=bottom,shape=straight,waterlogged=false]',
        )
        self.assertEqual(
            states[(2, 1)],
            'brick_stairs[facing=south,half=bottom,shape=straight,waterlogged=false]',
        )
        # High-z slope faces north (upslope toward ridge)
        self.assertEqual(
            states[(2, 3)],
            'brick_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]',
        )
        # Ridge is a full block, not a stair terrace
        self.assertEqual(states[(2, 2)], 'bricks')
        self._assert_shapes_match_neighbours(half, states)

    def test_hip_outer_corner_shape(self):
        # Pyramid: half = 2 * chebyshev dist from outside of a 5×5 (edge=0).
        cells = {(x, z) for x in range(5) for z in range(5)}
        half = {}
        for x, z in cells:
            d = min(x, z, 4 - x, 4 - z)
            half[(x, z)] = 10 + 2 * d
        mats = {c: 'brick' for c in half}
        states = F.pitched_roof_states(half, mats)
        # Side mid (edge ring): straight stair facing in
        self.assertEqual(
            states[(2, 0)],
            'brick_stairs[facing=south,half=bottom,shape=straight,waterlogged=false]',
        )
        # Footprint corner: diagonal rise only → outer hip stair from edge neighbour
        corner = states[(0, 0)]
        self.assertIn('brick_stairs', corner)
        self.assertIn('shape=outer_', corner)
        self.assertIn('waterlogged=false', corner)
        self.assertEqual(states[(2, 2)], 'bricks')
        self._assert_shapes_match_neighbours(half, states)

    def test_valley_inner_corner_shape(self):
        # At (1,1)=12: east and north are +2 uphill; NE diagonal is not higher → valley.
        half = {
            (0, 0): 10, (1, 0): 12, (2, 0): 14,
            (0, 1): 12, (1, 1): 12, (2, 1): 14,
            (0, 2): 14, (1, 2): 14, (2, 2): 12,
        }
        mats = {c: 'brick' for c in half}
        states = F.pitched_roof_states(half, mats)
        v = states[(1, 1)]
        self.assertIn('brick_stairs', v)
        self.assertIn('shape=inner_', v)
        self.assertIn('half=bottom', v)
        self.assertIn('waterlogged=false', v)
        self._assert_shapes_match_neighbours(half, states)

    def test_valley_inner_even_when_diagonal_is_higher(self):
        # Same valley ups at (1,1), but NE diagonal is taller — old height-diff
        # logic called this outer; same-Y neighbours still form an inner.
        half = {
            (0, 0): 10, (1, 0): 12, (2, 0): 14,
            (0, 1): 12, (1, 1): 12, (2, 1): 14,
            (0, 2): 14, (1, 2): 14, (2, 2): 16,
        }
        mats = {c: 'brick' for c in half}
        states = F.pitched_roof_states(half, mats)
        self.assertIn('shape=inner_', states[(1, 1)])
        self._assert_shapes_match_neighbours(half, states)

    def test_straight_run_same_height_neighbours(self):
        half = {(x, 0): 10 for x in range(3)}
        half.update({(x, 1): 12 for x in range(3)})
        mats = {c: 'brick' for c in half}
        states = F.pitched_roof_states(half, mats)
        for x in range(3):
            self.assertEqual(
                states[(x, 0)],
                'brick_stairs[facing=south,half=bottom,shape=straight,waterlogged=false]',
            )
        self._assert_shapes_match_neighbours(half, states)

    def test_three_rises_picks_stable_facing_and_neighbour_shape(self):
        # Local saddle: N/E/S all +2 → ortho pair (N,E), facing north; shape from neighbours.
        half = {
            (1, 0): 14,
            (0, 1): 12, (1, 1): 12, (2, 1): 14,
            (1, 2): 14,
        }
        mats = {c: 'brick' for c in half}
        states = F.pitched_roof_states(half, mats)
        self.assertIn('brick_stairs', states[(1, 1)])
        self.assertIn('facing=north', states[(1, 1)])
        self._assert_shapes_match_neighbours(half, states)

    def test_slab_beside_stair_does_not_force_corner(self):
        # Odd half-height neighbour is a bottom slab, not a stair → shape stays straight.
        half = {(0, 0): 10, (1, 0): 11, (0, 1): 12}
        mats = {c: 'brick' for c in half}
        states = F.pitched_roof_states(half, mats)
        self.assertIn('brick_stairs', states[(0, 0)])
        self.assertIn('shape=straight', states[(0, 0)])
        self.assertIn('_slab', states[(1, 0)])
        self._assert_shapes_match_neighbours(half, states)

    def test_stair_run_ending_in_low_step_gives_bottom_slab(self):
        # #207: odd half-height is a low step; surface half a block below a full block.
        half = {(0, 0): 10, (1, 0): 12, (2, 0): 13}
        mats = {c: 'brick' for c in half}
        states = F.pitched_roof_states(half, mats)
        self.assertEqual(states[(2, 0)], 'brick_slab[type=bottom,waterlogged=false]')
        self.assertIn('brick_stairs', states[(0, 0)])

    def test_set_roof_marks_bottom_slabs_and_survives_apply_rewrite(self):
        from site_kit import Plan, rewrite_bottom_slabs
        stub = mock.Mock()
        stub.p = Plan('t')
        F.Build._set_roof(stub, 1, 60, 1, 'brick_slab[type=bottom,waterlogged=false]')
        F.Build._set_roof(stub, 2, 60, 1, 'bricks')
        self.assertEqual(stub.p.keep_slab_cells, {(1, 60, 1)})
        self.assertEqual(rewrite_bottom_slabs(stub.p), 0)
        self.assertIn('type=bottom', stub.p.cells[(1, 60, 1)])

    def test_roof_block_y_odd_goes_up_even_unchanged(self):
        self.assertEqual([F.roof_block_y(h) for h in range(12, 17)], [6, 7, 7, 8, 8])

    def test_bottom_half_slab_surface_rises_steadily_h12_to_h16(self):
        # #209: surfaces 7, 7.5, 8, 8.5, 9, never a sawtooth.
        half = {(i, 0): 12 + i for i in range(5)}
        states = F.pitched_roof_states(half, {c: 'brick' for c in half})
        surf = []
        for (x, z), h in sorted(half.items()):
            y = F.roof_block_y(h)
            surf.append(y + (0.5 if 'type=bottom' in states[(x, z)] else 1))
        self.assertEqual(surf, [7, 7.5, 8, 8.5, 9])

    def test_stairs_same_y_rule_uses_shifted_y(self):
        # Odd-h stair neighbours share a block Y with each other, not with even-h ones.
        self.assertEqual(F.roof_block_y(13), F.roof_block_y(14))
        self.assertNotEqual(F.roof_block_y(12), F.roof_block_y(13))

    def _roof_stub(self, below):
        stub = mock.Mock()
        stub.p = F.Plan('t')
        stub.wall = 'bricks'
        if below is not None:
            stub.p.set(1, 59, 1, below)
        stub.live.block.return_value = 'minecraft:air'
        stub._is_solid = lambda x, y, z: F.Build._is_solid(stub, x, y, z)
        return stub

    def test_set_roof_puts_wall_under_slab_over_air(self):
        stub = self._roof_stub(None)
        F.Build._set_roof(stub, 1, 60, 1, 'brick_slab[type=bottom,waterlogged=false]')
        self.assertEqual(stub.p.cells[(1, 59, 1)], 'minecraft:bricks')

    def test_set_roof_keeps_existing_solid_under_slab(self):
        stub = self._roof_stub('stone')
        F.Build._set_roof(stub, 1, 60, 1, 'brick_slab[type=bottom,waterlogged=false]')
        self.assertEqual(stub.p.cells[(1, 59, 1)], 'minecraft:stone')

    def test_ridge_unchanged_full_block_even(self):
        half = {(0, 0): 10, (1, 0): 12, (2, 0): 12, (3, 0): 10}
        mats = {c: 'brick' for c in half}
        states = F.pitched_roof_states(half, mats)
        self.assertEqual(states[(1, 0)], 'bricks')
        self.assertEqual(states[(2, 0)], 'bricks')

    def test_valley_low_step_is_bottom_slab_never_top(self):
        half = {(0, 0): 12, (1, 0): 11, (2, 0): 12}
        mats = {c: 'brick' for c in half}
        states = F.pitched_roof_states(half, mats)
        self.assertEqual(states[(1, 0)], 'brick_slab[type=bottom,waterlogged=false]')
        self.assertFalse(any('type=top' in st for st in states.values()))

    def test_slab_family_fallback_still_full_block(self):
        half = {(0, 0): 10, (1, 0): 11}
        warns: list[str] = []
        with mock.patch.object(F, 'block_family_variants',
                               return_value={'full': 'stone', 'stairs': 'stone_stairs'}):
            states = F.pitched_roof_states(half, {c: 'stone' for c in half}, warns)
        self.assertEqual(states[(1, 0)], 'stone')
        self.assertTrue(any('no slab' in w for w in warns))

    def test_full_block_beside_stair_does_not_force_corner(self):
        # Ridge full block upslope of a stair — not a stair neighbour, shape stays straight.
        half = {(0, 0): 10, (1, 0): 12}
        mats = {c: 'brick' for c in half}
        states = F.pitched_roof_states(half, mats)
        self.assertEqual(
            states[(0, 0)],
            'brick_stairs[facing=east,half=bottom,shape=straight,waterlogged=false]',
        )
        self.assertEqual(states[(1, 0)], 'bricks')
        self._assert_shapes_match_neighbours(half, states)

    def test_full_block_fallback_is_not_a_stair_neighbour(self):
        half = {(0, 0): 10, (1, 0): 10, (0, 1): 10, (1, 1): 12}
        mats = {c: 'brick' for c in half}
        mats[(1, 0)] = 'smooth_stone'  # no vanilla stair family
        states = F.pitched_roof_states(half, mats)
        self.assertEqual(states[(1, 0)], 'smooth_stone')
        self.assertIn('shape=straight', states[(0, 0)])
        self._assert_shapes_match_neighbours(half, states)

    def test_regularisation_is_stable_and_does_not_mutate_input(self):
        raw = {(0, 0): 10, (1, 0): 18, (2, 0): 20, (3, 0): 22}
        expected = {(0, 0): 10, (1, 0): 12, (2, 0): 14, (3, 0): 16}
        self.assertEqual(F.regularise_roof_half_heights(raw), expected)
        self.assertEqual(F.regularise_roof_half_heights(dict(reversed(list(raw.items())))), expected)
        self.assertEqual(F.regularise_roof_half_heights(expected), expected)
        self.assertEqual(raw[(1, 0)], 18)

    def test_regularise_lowers_cliff_taller_than_one_stair(self):
        raw = {(0, 0): 10, (1, 0): 18}
        got = F.regularise_roof_half_heights(raw)
        self.assertEqual(got[(0, 0)], 10)
        self.assertEqual(got[(1, 0)], 12)

    def test_missing_stairs_falls_back_to_full_and_warns(self):
        half = {(0, 0): 10, (1, 0): 12}
        mats = {(0, 0): 'smooth_stone', (1, 0): 'smooth_stone'}
        warn: list[str] = []
        states = F.pitched_roof_states(half, mats, warnings=warn)
        self.assertEqual(states[(0, 0)], 'smooth_stone')
        self.assertTrue(any('no stairs' in w for w in warn))


class PitchedRoofBuildShapesTest(unittest.TestCase):
    """Integration: build() hip uses stairs; states cover gable/valley via helper."""

    def test_build_hip_places_outer_corner_and_upslope_stairs(self):
        cells = {(x, z) for x in range(10, 15) for z in range(20, 25)}
        spec = {
            'name': 'unit_hip',
            'ways': {'99': 'A'},
            'storeys': {'A': 2},
            'roofs': {'A': {'kind': 'pitched', 'material': 'brick'}},
            'groups': [{'nos': ['A'], 'front': 'S', 'mode': 'each'}],
            'modifies': [],
            'floor_min': 50,
            'step_max': 4,
            'door_frame': True,
            'keep_edits': False,
            'wall': 'bricks',
            'floor': 'smooth_stone',
            'ledge_stair': False,
        }
        plan = F.build(spec, footprints={'A': cells}, world=_StoneWorld())
        stairs = [st for st in plan.cells.values() if 'brick_stairs' in st]
        self.assertTrue(stairs, 'hip roof must place stairs')
        self.assertTrue(
            any('facing=south' in st and 'shape=straight' in st for st in stairs),
            f'expected a straight south-facing slope stair, got {stairs!r}',
        )
        self.assertTrue(
            any('shape=outer_' in st for st in stairs),
            f'expected outer hip corner stair, got {stairs!r}',
        )


if __name__ == '__main__':
    unittest.main()
