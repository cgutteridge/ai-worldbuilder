"""Tests for feature_build at agreed seams: load_spec, build→Plan, CLI dry-run, SU fixture."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import feature_build as F


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


if __name__ == '__main__':
    unittest.main()
