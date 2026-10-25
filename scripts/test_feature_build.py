"""Tests for feature_build at agreed seams: load_spec, build→Plan, CLI dry-run, SU fixture, facades, doors."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import numpy as np

import feature_build as F
from site_kit import door_frame_issues


class LoadSpecTest(unittest.TestCase):
    """Seam: load_spec validates and fills defaults."""

    def write(self, spec: dict) -> Path:
        path = Path(tempfile.mkdtemp()) / 's.json'
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
        self.assertEqual(spec['doors'], {})
        self.assertEqual(spec['zones'], {})

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

    def test_doors_map_accepted_and_bad_leaves_or_block_refused(self):
        good = self.good()
        ok = {**good, 'doors': {'42-A': {'leaves': 2, 'block': 'iron_door'}}}
        spec = F.load_spec(self.write(ok))
        self.assertEqual(spec['doors']['42-A'], {'leaves': 2, 'block': 'iron_door'})
        for bad_doors in (
            {'42-A': {'leaves': 3, 'block': 'iron_door'}},
            {'42-A': {'leaves': 2, 'block': 'iron_trapdoor'}},
            {'42-A': {'leaves': 2, 'block': 'bricks'}},
            {'42-A': 'iron_door'},
        ):
            with self.subTest(bad_doors=bad_doors):
                with self.assertRaises(SystemExit):
                    F.load_spec(self.write({**good, 'doors': bad_doors}))


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
        wx, wz = 12, 24
        self.assertEqual(plan.cells.get((wx, floor, wz)), 'minecraft:bricks')
        self.assertEqual(plan.cells.get((wx, wall_top, wz)), 'minecraft:bricks')
        self.assertIn('smooth_stone_slab', plan.cells.get((12, roof_y, 22), ''))
        self.assertEqual(plan.cells.get((12, floor, 22)), 'minecraft:smooth_stone')
        self.assertEqual(plan.cells.get((12, floor + 3, 22)), 'minecraft:smooth_stone')
        self.assertEqual(plan.cells.get((12, floor + 6, 22)), 'minecraft:smooth_stone')

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
        d = Path(tempfile.mkdtemp())
        spec_path = d / 'cli_feature.json'
        spec_path.write_text(json.dumps(spec))
        with mock.patch.object(F, 'preview') as prev, mock.patch.object(F, 'apply') as app:
            plan = F.run(spec_path, do_apply=False, footprints={'A': cells}, world=_StoneWorld())
            self.assertGreater(len(plan.cells), 0)
            prev.assert_called_once()
            app.assert_called_once()
            self.assertEqual(app.call_args.kwargs.get('dry'), True)


class SuFixtureDryRunTest(unittest.TestCase):
    """Seam: committed SU spec dry-runs end-to-end via feature_build (no --apply)."""

    def test_susu_spec_dry_runs(self):
        from script_env import ROOT
        spec_path = ROOT / 'site/builds/susu_42.json'
        if not spec_path.exists():
            self.skipTest('site/builds/susu_42.json not in active world yet')
        # World reference/osm is gitignored; fall back to donor clips when empty.
        world_osm = ROOT / 'reference/osm'
        donor_osm = Path('/Users/cjg/Projects/Southampton Refine/reference/osm')
        osm_root = world_osm if any(world_osm.glob('*/*.geojson')) else donor_osm
        if not any(Path(osm_root).glob('*/*.geojson')):
            self.skipTest('OSM clips unavailable for w33830305 resolution')
        spec = F.load_spec(spec_path)
        self.assertEqual(spec['ways'].get('33830305'), 'SU')
        # Production path: OSMIndex (neighbours) + real site_kit dry apply; mock preview only.
        with mock.patch.object(F, 'preview'):
            plan = F.run(spec_path, do_apply=False, world=F.World(), osm_root=osm_root)
        self.assertGreater(len(plan.cells), 10000)


class FacadeSpecTest(unittest.TestCase):
    """Seam: load_spec accepts per-face facade rows; rejects bad storey/face/height."""

    def write(self, spec: dict) -> Path:
        path = Path(tempfile.mkdtemp()) / 's.json'
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


class DoorSpecBuildTest(unittest.TestCase):
    """Seams: build→Plan with per-portal doors, hinge pairing, framing, dry-run print."""

    def setUp(self):
        # Wide enough for a 2-leaf door plus jambs on the south face.
        self.cells = {(x, z) for x in range(10, 17) for z in range(20, 25)}

    def spec(self, doors):
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
            'doors': doors,
            'facades': {},
        }

    def portals(self):
        return [{
            'id': 'T-A', 'label': 'Main', 'building': 'T',
            'x': 13, 'z': 25, 'images': [], 'lat': 0, 'lon': 0, 'e': 0, 'n': 0,
            'in_map': True, 'uri': '',
        }]

    def build_with_portals(self, doors, portals=None):
        portals = portals if portals is not None else self.portals()
        buf = io.StringIO()
        with mock.patch.object(F.portals, 'load', return_value=portals), redirect_stdout(buf):
            plan = F.build(self.spec(doors), footprints={'A': self.cells}, world=_StoneWorld())
        return plan, buf.getvalue()

    def test_double_door_mirrored_hinges_framed_and_printed(self):
        plan, out = self.build_with_portals({'T-A': {'leaves': 2, 'block': 'iron_door'}})
        lowers = [(pos, st) for pos, st in plan.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 2, lowers)
        hinges = sorted(st.split('hinge=')[1].split(',')[0] for _, st in lowers)
        self.assertEqual(hinges, ['left', 'right'])
        xs = sorted(pos[0] for pos, _ in lowers)
        self.assertEqual(xs[1] - xs[0], 1)
        self.assertEqual(door_frame_issues(plan, _StoneWorld().block), [])
        self.assertIn('T-A', out)
        self.assertIn('Main', out)
        self.assertIn('iron_door', out)
        self.assertIn('2-leaf', out)

    def test_single_coloured_door_uses_block(self):
        plan, out = self.build_with_portals({'T-A': {'leaves': 1, 'block': 'crimson_door'}})
        lowers = [st for st in plan.cells.values() if 'crimson_door' in st and 'half=lower' in st]
        self.assertEqual(len(lowers), 1)
        self.assertEqual(door_frame_issues(plan, _StoneWorld().block), [])
        self.assertIn('crimson_door', out)

    def test_double_door_survives_facade_glazing(self):
        """Both leaves stay doors when a facade table glazes the south face."""
        spec = self.spec({'T-A': {'leaves': 2, 'block': 'iron_door'}})
        spec['facades'] = {
            'A': {'S': [{'storey': 1, 'glazed_h': 3, 'pier': 1}]},
        }
        buf = io.StringIO()
        with mock.patch.object(F.portals, 'load', return_value=self.portals()), redirect_stdout(buf):
            plan = F.build(spec, footprints={'A': self.cells}, world=_StoneWorld())
        lowers = [(pos, st) for pos, st in plan.cells.items()
                  if 'iron_door' in st and 'half=lower' in st]
        uppers = [(pos, st) for pos, st in plan.cells.items()
                  if 'iron_door' in st and 'half=upper' in st]
        self.assertEqual(len(lowers), 2, lowers)
        self.assertEqual(len(uppers), 2, uppers)
        for (x, y, z), _ in lowers + uppers:
            self.assertNotIn('glass_pane', plan.cells.get((x, y, z), ''))


class ZonesSpecTest(unittest.TestCase):
    """Seam: load_spec accepts zones; refuses bad from/levels/pitched roof."""

    def write(self, spec: dict) -> Path:
        path = Path(tempfile.mkdtemp()) / 's.json'
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
        pitched = {
            **good,
            'roofs': {'A': {'kind': 'pitched', 'material': 'brick'}},
        }
        cases = [
            {**good, 'zones': {'B': {'from': 'lidar', 'levels': [40]}}},
            {**good, 'zones': {'A': {'from': 'osm', 'levels': [40]}}},
            {**good, 'zones': {'A': {'from': 'lidar', 'levels': []}}},
            {**good, 'zones': {'A': {'from': 'lidar', 'levels': 'nope'}}},
            {**good, 'zones': {'A': {'from': 'lidar', 'levels': [40], 'bucket': 0}}},
            pitched,
        ]
        for bad in cases:
            with self.subTest(bad=bad.get('zones') or bad.get('roofs')):
                with self.assertRaises(SystemExit):
                    F.load_spec(self.write(bad))


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
        # 6×6 OSM with a 1-block stair notch; two DSM plateaus
        osm = np.ones((6, 6), dtype=bool)
        osm[0, 5] = False  # notch / spur
        dsm = np.full((6, 6), 41.0)
        dsm[:, 3:] = 50.0
        zz, xx = np.mgrid[0:6, 0:6]
        readable, zone, levels, axis = F.assign_roof_zones(
            dsm, osm, [41, 50], xx, zz, bucket=3, axis_deg=0.0,
        )
        self.assertEqual(levels, [41, 50])
        self.assertEqual(axis, 0.0)
        self.assertGreaterEqual(int(readable.sum()), int(osm.sum()) - 2)
        self.assertIn(41.0, set(zone[readable].tolist()))
        self.assertIn(50.0, set(zone[readable].tolist()))
        line = F.footprint_pad_line(osm, readable)
        self.assertTrue(line.startswith('footprint osm='))
        self.assertIn('readable=', line)
        self.assertIn('pad=', line)
        self.assertIn('cover=', line)


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

    def spec(self):
        return {
            'name': 'unit_zones',
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

    def test_zoned_build_parapet_and_variable_roof(self):
        buf = io.StringIO()
        with mock.patch.object(F, 'metres_aod_to_y', side_effect=lambda m: int(m)), \
                redirect_stdout(buf):
            plan = F.build(
                self.spec(), footprints={'A': self.cells}, world=_StoneWorld(),
                zone_data=self.zone_data,
            )
        out = buf.getvalue()
        self.assertIn('footprint osm=', out)
        # Low and high roof decks present
        low_roof = [(x, z) for (x, y, z), st in plan.cells.items()
                    if y == 45 and st == 'minecraft:smooth_stone' and (x, z) in self.cells]
        high_roof = [(x, z) for (x, y, z), st in plan.cells.items()
                     if y == 50 and st == 'minecraft:smooth_stone' and (x, z) in self.cells]
        self.assertTrue(low_roof, 'expected 45 m plateau roof cells')
        self.assertTrue(high_roof, 'expected 50 m plateau roof cells')
        # Parapet above at least one roof edge
        parapets = [pos for pos, st in plan.cells.items() if st == 'minecraft:smooth_stone' and pos[1] in (46, 51)]
        self.assertTrue(parapets, 'expected parapet on roof edges')
        # Downhill / exterior wall above neighbour ground: west face should show bricks above ground
        west_wall = [
            y for (x, y, z), st in plan.cells.items()
            if x == 10 and z == 21 and 'bricks' in st and y > 40
        ]
        self.assertTrue(west_wall, 'expected exposed wall on west face')


if __name__ == '__main__':
    unittest.main()
