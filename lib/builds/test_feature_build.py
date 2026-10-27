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
        self.assertEqual(spec['facades'], {})
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
        roof_ys = [y for (x, y, z), st in plan.cells.items() if 'brick_slab' in st]
        self.assertTrue(roof_ys, 'expected pitched brick slabs')
        self.assertGreaterEqual(max(roof_ys), eave)

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
            'facades': {
                'A': {
                    'S': [{'storey': 1, 'glazed_h': 3, 'pier': 1, 'wall': 'bricks'}],
                    'N': [{'storey': 1, 'glazed_h': 2, 'pier': 1, 'rhythm': 'WW_WW'}],
                }
            },
        }

    def test_loads_facade_table(self):
        spec = F.load_spec(self.write(self.good()))
        self.assertEqual(spec['facades']['A']['S'][0]['glazed_h'], 3)
        self.assertEqual(spec['facades']['A']['N'][0]['rhythm'], 'WW_WW')
        self.assertEqual(spec['facades']['A']['S'][0]['pier'], 1)

    def test_facades_optional_default_empty(self):
        base = {k: v for k, v in self.good().items() if k != 'facades'}
        spec = F.load_spec(self.write(base))
        self.assertEqual(spec['facades'], {})

    def test_bad_facade_rows_refused(self):
        good = self.good()
        cases = [
            {**good, 'facades': {'B': {'S': [{'storey': 1, 'glazed_h': 3, 'pier': 1}]}}},
            {**good, 'facades': {'A': {'X': [{'storey': 1, 'glazed_h': 3, 'pier': 1}]}}},
            {**good, 'facades': {'A': {'S': [{'storey': 0, 'glazed_h': 3, 'pier': 1}]}}},
            {**good, 'facades': {'A': {'S': [{'storey': 3, 'glazed_h': 3, 'pier': 1}]}}},
            {**good, 'facades': {'A': {'S': [{'storey': 1, 'glazed_h': 4, 'pier': 1}]}}},
            {**good, 'facades': {'A': {'S': [{'storey': 1, 'glazed_h': 0, 'pier': 1}]}}},
            {**good, 'facades': {'A': {'S': [{'storey': 1, 'glazed_h': 3, 'pier': -1}]}}},
            {**good, 'facades': {'A': {'S': [{'storey': 1, 'glazed_h': 3}]}}},
        ]
        for bad in cases:
            with self.subTest(bad=bad.get('facades')):
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
            'facades': facades,
        }

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
            'facades': facades or {},
            'facade_plan': {'A': {'faces': {}, 'entrances': entrances}},
        }

    def build_plan(self, entrances, *, facades=None, cells=None, world=None):
        buf = io.StringIO()
        with redirect_stdout(buf):
            plan = F.build(
                self.spec(entrances, facades=facades),
                footprints={'A': cells if cells is not None else self.cells},
                world=world or _StoneWorld(),
            )
        return plan, buf.getvalue()

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

        plan, _out = self.build_plan(
            [_entrance_row(leaves=1, block='iron_door', at=[13, 25], floor_offset=0)],
            world=HighPavementWorld(),
        )
        lowers = [(pos, st) for pos, st in plan.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 1, lowers)
        _x, y, _z = lowers[0][0]
        # outside ground top is 52 → floor block 52 → lower door leaf at 53
        self.assertEqual(y, 53)
        self.assertGreaterEqual(y - 1, 52)

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

    def test_pitched_zones_ridge_slabs_flat_plateaus_unchanged(self):
        """Pitched+zones follows the lidar ridge with stair-slabs; flat stays stepped decks."""
        pitched = self._build('pitched', [48], self.ridge_zone_data, material='brick')
        edge_slabs = [
            st for (x, y, z), st in pitched.cells.items()
            if (x, z) in self.cells and x in (10, 18) and 'brick_slab' in st
        ]
        ridge_slabs = [
            (y, st) for (x, y, z), st in pitched.cells.items()
            if (x, z) in self.cells and x == 14 and 'brick_slab' in st
        ]
        self.assertTrue(edge_slabs, 'expected stair-slabs at the zone eave')
        self.assertTrue(ridge_slabs, 'expected stair-slabs on the lidar ridge line')
        ridge_ys = {y for y, _st in ridge_slabs}
        edge_ys = {
            y for (x, y, z), st in pitched.cells.items()
            if (x, z) in self.cells and x in (10, 18) and 'brick_slab' in st
        }
        self.assertGreater(
            max(ridge_ys), max(edge_ys),
            'ridge must rise above the zone eave (not a flat terrace)',
        )
        # Must not be stepped full-block plateaus along the ridge
        stepped_full = [
            (x, y) for (x, y, z), st in pitched.cells.items()
            if (x, z) in self.cells and st == 'minecraft:brick' and y in (49, 50, 51, 52)
        ]
        self.assertFalse(
            stepped_full,
            'pitched+zones must not snap a ridge into stepped full-block terraces',
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
            'facades': {},
            'facade_plan': {},
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
            'facades': {},
            'details': details,
        }

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
                    'kind': 'canopy',
                    'at': [13, 55, 24],
                    'size': [2, 1, 1],
                    'face': 'S',
                    'source': 'street photo entrance canopy',
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
        # Skylight replaces the roof membrane with glass.
        sky = plan.cells.get((15, 56, 22), '')
        self.assertTrue('glass' in sky, sky)
        # Canopy overhangs one block south of the south wall.
        canopy = plan.cells.get((13, 55, 25), '') or plan.cells.get((14, 55, 25), '')
        self.assertTrue('slab' in canopy, f'expected canopy slab, got {canopy!r}')
        # Drainpipe on the south wall column.
        pipe = plan.cells.get((10, 52, 24), '')
        self.assertTrue('iron' in pipe, pipe)
        for kind, src in (
            ('plant_room', 'aerial plant room'),
            ('skylight', 'aerial skylight'),
            ('canopy', 'street photo entrance canopy'),
            ('drainpipe', 'street photo south pipe'),
        ):
            self.assertRegex(out, rf'detail {kind}.*source={re.escape(src)}')


if __name__ == '__main__':
    unittest.main()
