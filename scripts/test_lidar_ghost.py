#!/usr/bin/env python3
"""Unit tests for lidar_ghost (no Minecraft, mock height samples)."""
from __future__ import annotations

import sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parent.parent
for _p in (_ROOT / "shims", _ROOT / "scripts"):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import cellmath
import lidar_ghost as lg


def _flat_heights(ground_y: int, feature: int, x0: int, z0: int, x1: int, z1: int) -> dict:
    """Uniform height sample for an inclusive block box."""
    nx, nz = x1 - x0 + 1, z1 - z0 + 1
    gy = np.full((nx, nz), ground_y, dtype=np.int64)
    feat = np.full((nx, nz), feature, dtype=np.int64)
    return {'ground_y': gy, 'feature': feat, 'diff': feat}


class NamingAndRing(unittest.TestCase):
    """Owner names and Chebyshev neighbour selection."""

    def test_owner_name_encodes_negative_tz(self):
        self.assertEqual(lg.owner_name(48, -44, 'ground'), 'ghost_48_m44_ground')
        self.assertEqual(lg.owner_name(48, -44, 'features'), 'ghost_48_m44_features')
        self.assertEqual(lg.owner_name(10, 3, 'ground'), 'ghost_10_3_ground')

    def test_neighbour_ring_default_excludes_focus(self):
        focus = (48, -44)
        cells = lg.neighbour_cells(focus, ring=1)
        self.assertEqual(len(cells), 8)
        self.assertNotIn(focus, cells)
        self.assertTrue(all(cellmath.ring_distance(c, focus) == 1 for c in cells))

    def test_neighbour_ring_zero_with_focus(self):
        focus = (5, 5)
        self.assertEqual(lg.neighbour_cells(focus, ring=0, include_focus=True), [focus])
        self.assertEqual(lg.neighbour_cells(focus, ring=0, include_focus=False), [])

    def test_ghost_owners_for(self):
        self.assertEqual(lg.ghost_owners_for((48, -44)),
                         frozenset({'ghost_48_m44_ground', 'ghost_48_m44_features'}))


class PlanFromMockHeights(unittest.TestCase):
    """Ground and feature plans from injectable samples (tiny synthetic cell)."""

    def setUp(self):
        # Patch CELL to 2 so plans stay tiny; restore after.
        self._cell = cellmath.CELL
        cellmath.CELL = 2
        lg.cellmath.CELL = 2

    def tearDown(self):
        cellmath.CELL = self._cell
        lg.cellmath.CELL = self._cell

    def test_ground_fills_under_surface(self):
        cell = (0, 0)
        h = _flat_heights(5, 0, *cellmath.bounds(cell))
        plan = lg.plan_ghost_ground(cell, heights=h)
        # columns (0,0),(1,0),(0,1),(1,1); fill y=1..4 stone, y=5 gray_concrete
        self.assertEqual(plan.name, 'ghost_0_0_ground')
        self.assertEqual(plan.cells[(0, 5, 0)], 'minecraft:gray_concrete')
        self.assertEqual(plan.cells[(0, 1, 0)], 'minecraft:stone')
        self.assertNotIn((0, 0, 0), plan.cells)  # bedrock layer left alone
        self.assertEqual(lg.plan_stats(plan)['blocks'], 4 * 5)  # 4 cols × (4 fill + 1 top)

    def test_features_are_grey_columns_above_ground(self):
        cell = (0, 0)
        h = _flat_heights(5, 3, *cellmath.bounds(cell))
        plan = lg.plan_ghost_features(cell, heights=h)
        self.assertEqual(plan.name, 'ghost_0_0_features')
        self.assertEqual(plan.cells[(0, 6, 0)], 'minecraft:light_gray_concrete')
        self.assertEqual(plan.cells[(0, 8, 0)], 'minecraft:light_gray_concrete')
        self.assertNotIn((0, 5, 0), plan.cells)  # ground surface not owned by features
        self.assertEqual(lg.plan_stats(plan)['blocks'], 4 * 3)

    def test_zero_feature_skips_column(self):
        cell = (0, 0)
        x0, z0, x1, z1 = cellmath.bounds(cell)
        h = _flat_heights(4, 2, x0, z0, x1, z1)
        h['feature'][0, 0] = 0
        plan = lg.plan_ghost_features(cell, heights=h)
        self.assertNotIn((x0, 5, z0), plan.cells)
        self.assertIn((x0 + 1, 5, z0), plan.cells)


class StagesGhostVsReal(unittest.TestCase):
    """STAGES distinguishes ghost placeholders from real refine."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cells = Path(self.tmp.name)

    def _write(self, cell, stages):
        path = self.cells / f'{cell[0]}_{cell[1]}-STAGES.json'
        path.write_text(json.dumps({'cell': f'{cell[0]},{cell[1]}', 'stages': stages}))

    def test_is_ghost_stage_by_key_and_kind(self):
        self.assertTrue(lg.is_ghost_stage('ghost-ground', {'status': 'applied'}))
        self.assertTrue(lg.is_ghost_stage('feature-x', {'kind': 'ghost', 'status': 'applied'}))
        self.assertFalse(lg.is_ghost_stage('ground-layer', {'status': 'applied'}))

    def test_real_work_ignores_ghost_stages(self):
        cell = (1, 2)
        self._write(cell, {
            'ghost-ground': {'status': 'applied', 'kind': 'ghost', 'plan': 'ghost_1_2_ground'},
            'ghost-features': {'status': 'applied', 'kind': 'ghost', 'plan': 'ghost_1_2_features'},
        })
        self.assertFalse(lg.real_work_started(cell, self.cells))
        self.assertTrue(lg.ghosts_complete(cell, self.cells))

    def test_real_ground_layer_counts_as_real_work(self):
        cell = (1, 2)
        self._write(cell, {
            'ghost-ground': {'status': 'applied', 'kind': 'ghost'},
            'ground-layer': {'status': 'applied', 'plan': 'highfield_1_2_ground'},
        })
        self.assertTrue(lg.real_work_started(cell, self.cells))

    def test_ensure_skips_when_already_applied(self):
        cell = (0, 0)
        self._write(cell, {
            lg.STAGE_GROUND: {'status': 'applied', 'kind': lg.KIND_GHOST},
            lg.STAGE_FEATURES: {'status': 'applied', 'kind': lg.KIND_GHOST},
        })
        with mock.patch.object(cellmath, 'CELL', 2), mock.patch.object(lg.cellmath, 'CELL', 2):
            rep = lg.ensure_cell(cell, dry=True, cells_dir=self.cells,
                                 heights_fn=lambda *b: _flat_heights(5, 1, *b))
        self.assertEqual(rep['skipped'], 'layers-complete')

    def test_ensure_skips_real_work_when_flagged(self):
        cell = (0, 0)
        self._write(cell, {'trees': {'status': 'applied'}})
        rep = lg.ensure_cell(cell, dry=True, cells_dir=self.cells, skip_if_real=True,
                             heights_fn=lambda *b: _flat_heights(5, 1, *b))
        self.assertEqual(rep['skipped'], 'real-work')

    def test_trees_only_still_allows_ghost_ground(self):
        cell = (0, 0)
        self._write(cell, {'trees': {'status': 'applied'}})
        with mock.patch.object(cellmath, 'CELL', 2), mock.patch.object(lg.cellmath, 'CELL', 2):
            rep = lg.ensure_cell(cell, dry=True, cells_dir=self.cells,
                                 heights_fn=lambda *b: _flat_heights(5, 1, *b))
        self.assertIn('ground', rep)

    def test_ensure_plans_when_empty(self):
        cell = (0, 0)
        with mock.patch.object(cellmath, 'CELL', 2), mock.patch.object(lg.cellmath, 'CELL', 2):
            rep = lg.ensure_cell(cell, dry=True, cells_dir=self.cells,
                                 heights_fn=lambda *b: _flat_heights(5, 2, *b))
        self.assertNotIn('skipped', rep)
        self.assertEqual(rep['ground']['blocks'], 4 * 5)
        self.assertEqual(rep['features']['blocks'], 4 * 2)


class OnCellFocus(unittest.TestCase):
    """Focus handoff ensures the v2 frontier (seed rings), or focus neighbours before seeds."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cells = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, cell, stages):
        path = self.cells / f'{cell[0]}_{cell[1]}-STAGES.json'
        path.write_text(json.dumps({'cell': f'{cell[0]},{cell[1]}', 'stages': stages}))

    def test_on_cell_focus_frontier_from_seeds_skips_real_ground(self):
        focus = (0, 0)
        self._write((1, 0), {'ground-layer': {'status': 'applied'}})
        with mock.patch.object(cellmath, 'CELL', 2), mock.patch.object(lg.cellmath, 'CELL', 2), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('LIDAR_GHOST_RING', None)
            reps = lg.on_cell_focus(focus, ring=1, dry=True, cells_dir=self.cells,
                                    heights_fn=lambda *b: _flat_heights(5, 1, *b))
        cells = {tuple(r['cell']) for r in reps}
        # Focus is inside the seed's ring-1 neighbourhood, so it is a frontier target.
        self.assertIn(focus, cells)
        self.assertIn((1, 0), cells)
        by = {tuple(r['cell']): r for r in reps}
        self.assertIn('ground_skip', by[(1, 0)])
        # A neighbour with no stages should be planned
        empty = next(r for r in reps if 'ground' in r and tuple(r['cell']) != focus)
        self.assertIn('ground', empty)

    def test_on_cell_focus_before_seeds_skips_focus_cell(self):
        focus = (0, 0)
        with mock.patch.object(cellmath, 'CELL', 2), mock.patch.object(lg.cellmath, 'CELL', 2), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('LIDAR_GHOST_RING', None)
            reps = lg.on_cell_focus(focus, ring=1, dry=True, cells_dir=self.cells,
                                    heights_fn=lambda *b: _flat_heights(5, 1, *b))
        cells = {tuple(r['cell']) for r in reps}
        self.assertNotIn(focus, cells)

    def test_ring_from_env(self):
        with mock.patch.dict(os.environ, {'LIDAR_GHOST_RING': '2'}):
            self.assertEqual(lg.ring_from_env(), 2)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('LIDAR_GHOST_RING', None)
            self.assertEqual(lg.ring_from_env(), lg.DEFAULT_RING)


class CliDryRun(unittest.TestCase):
    """CLI plan/status with fixture heights (no server)."""

    def test_plan_one_cell_with_heights_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 2×2 cell at (0,0) when CELL=2
            fixture = Path(tmp) / 'h.json'
            gy = [[10, 10], [10, 10]]
            feat = [[0, 1], [2, 0]]
            fixture.write_text(json.dumps({'ground_y': gy, 'feature': feat}))
            with mock.patch.object(cellmath, 'CELL', 2), mock.patch.object(lg.cellmath, 'CELL', 2):
                code = lg.main(['plan', '--cell', '0,0', '--heights-json', str(fixture)])
            self.assertEqual(code, 0)

    def test_status_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            cells = Path(tmp)
            path = cells / '48_-44-STAGES.json'
            path.write_text(json.dumps({'stages': {
                'ghost-ground': {'status': 'applied', 'kind': 'ghost'},
            }}))
            with mock.patch.object(lg, 'stage_path',
                                   lambda cell, cells_dir=None: cells / f'{cell[0]}_{cell[1]}-STAGES.json'):
                # status uses read_stages → stage_path; patch read_stages root via cells_dir not available
                # so patch read_stages directly
                pass
            with mock.patch.object(lg, 'read_stages',
                                   lambda cell, cells_dir=None: json.loads(path.read_text())
                                   if cell == (48, -44) else {'stages': {}}):
                code = lg.main(['status', '--cell', '48,-44'])
            self.assertEqual(code, 0)


class FrontierAndSplitLayers(unittest.TestCase):
    """V2 frontier union and independent ground vs feature needs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cells = Path(self.tmp.name)

    def _write(self, cell, stages):
        path = self.cells / f'{cell[0]}_{cell[1]}-STAGES.json'
        path.write_text(json.dumps({'cell': f'{cell[0]},{cell[1]}', 'stages': stages}))

    def test_frontier_unions_two_seeds(self):
        self._write((0, 0), {'ground-layer': {'status': 'applied'}})
        self._write((3, 0), {'trees': {'status': 'applied'}})
        targets = set(lg.frontier_targets(ring=1, cells_dir=self.cells))
        self.assertIn((0, 0), targets)
        self.assertIn((3, 0), targets)
        self.assertIn((1, 0), targets)
        self.assertIn((2, 0), targets)

    def test_real_ground_supersedes_ghost_ground_need(self):
        cell = (5, 5)
        self._write(cell, {
            'ghost-ground': {'status': 'applied', 'kind': lg.KIND_GHOST},
            'ground-layer': {'status': 'applied'},
        })
        self.assertFalse(lg.needs_ghost_ground(cell, self.cells))
        lg.supersede_ghost_ground(cell, self.cells)
        self.assertEqual(lg.ghost_stage_status(cell, lg.STAGE_GROUND, self.cells), 'superseded')

    def test_mark_features_stripped_is_not_idempotent_complete(self):
        cell = (2, 2)
        self._write(cell, {lg.STAGE_FEATURES: {'status': 'applied', 'kind': lg.KIND_GHOST}})
        lg.mark_ghost_features_stripped(cell, cells_dir=self.cells)
        self.assertEqual(lg.ghost_stage_status(cell, lg.STAGE_FEATURES, self.cells), 'stripped')
        self.assertFalse(lg.ghosts_complete(cell, self.cells))

    def test_mark_stripped_for_volume_touches_neighbour_cell(self):
        from site_kit import Plan

        self._write((1, 0), {lg.STAGE_FEATURES: {'status': 'applied', 'kind': lg.KIND_GHOST}})
        self._write((2, 0), {lg.STAGE_FEATURES: {'status': 'applied', 'kind': lg.KIND_GHOST}})
        plan = Plan('cross_cell_feature')
        plan.set(130, 50, 0, 'minecraft:bricks')
        plan.set(256, 50, 0, 'minecraft:bricks')
        touched = lg.mark_ghost_features_stripped_for_volume(plan, cells_dir=self.cells)
        self.assertEqual(set(touched), {(1, 0), (2, 0)})
        self.assertEqual(lg.ghost_stage_status((1, 0), lg.STAGE_FEATURES, self.cells), 'stripped')


class ReconcileHook(unittest.TestCase):
    """Post-apply reconcile updates stages without live MCP."""

    def test_ground_layer_supersedes_ghost_ground(self):
        tmp = tempfile.TemporaryDirectory()
        cells = Path(tmp.name)
        cell = (0, 0)
        path = cells / '0_0-STAGES.json'
        path.write_text(json.dumps({'stages': {
            lg.STAGE_GROUND: {'status': 'applied', 'kind': lg.KIND_GHOST},
        }}))
        from site_kit import Plan

        plan = Plan('highfield_0_0_ground')
        with mock.patch.object(lg, 'ensure_frontier', return_value={'seeds': [], 'targets': [], 'reports': []}), \
             mock.patch.object(lg, 'plan_strip_ghost_features', return_value=[]):
            rep = lg.reconcile_after_real_apply(plan, stage_key='ground-layer', cell=cell,
                                                cells_dir=cells)
        self.assertEqual(rep['ghost_ground'], 'superseded')
        self.assertEqual(lg.ghost_stage_status(cell, lg.STAGE_GROUND, cells), 'superseded')
        tmp.cleanup()

    def test_feature_reconcile_marks_cross_cell_ghost_stages(self):
        tmp = tempfile.TemporaryDirectory()
        cells = Path(tmp.name)
        for cell in ((1, 0), (2, 0)):
            path = cells / f'{cell[0]}_{cell[1]}-STAGES.json'
            path.write_text(json.dumps({'stages': {
                lg.STAGE_FEATURES: {'status': 'applied', 'kind': lg.KIND_GHOST},
            }}))
        from site_kit import Plan

        plan = Plan('feat')
        plan.set(130, 50, 0, 'minecraft:bricks')
        plan.set(256, 50, 0, 'minecraft:bricks')
        with mock.patch.object(lg, 'ensure_frontier', return_value={'seeds': [], 'targets': [], 'reports': []}), \
             mock.patch.object(lg, 'plan_strip_ghost_features', return_value=[]), \
             mock.patch.object(lg, 'apply_strip_plans', return_value=[]):
            rep = lg.reconcile_after_real_apply(plan, stage_key='feature-feat', cell=(2, 0),
                                                cells_dir=cells)
        self.assertEqual(set(map(tuple, rep['ghost_features_marked'])), {(1, 0), (2, 0)})
        tmp.cleanup()


class GroundLayerIgnoresGhosts(unittest.TestCase):
    """Real ground pass may overwrite ghost owners."""

    def test_ignored_builds_includes_ghost_owners(self):
        import ground_layer as gl
        ign = gl.ignored_builds((48, -44))
        self.assertIn('ghost_48_m44_ground', ign)
        self.assertIn('ghost_48_m44_features', ign)


class CellFeaturesGhostNotFeature(unittest.TestCase):
    """Ghost builds are layer-like, not queue features."""

    def test_ghost_names_are_not_feature_builds(self):
        import cell_features as cf
        self.assertFalse(cf.is_feature_build('ghost_48_m44_ground'))
        self.assertFalse(cf.is_feature_build('ghost_48_m44_features'))
        self.assertTrue(cf.is_feature_build('b2_main'))


if __name__ == '__main__':
    unittest.main()
