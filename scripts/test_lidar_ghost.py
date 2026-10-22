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

import io
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


class _Tiny(unittest.TestCase):
    """CELL patched to 2 and a throwaway journal (journal_testkit) as the active registry."""

    def setUp(self):
        from journal_testkit import JournalWorld
        for p in (mock.patch.object(cellmath, 'CELL', 2), mock.patch.object(lg.cellmath, 'CELL', 2),
                  mock.patch.dict(os.environ, {}, clear=False)):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop('LIDAR_GHOST_RING', None)
        self.jw = JournalWorld()
        self.jw.__enter__()
        self.addCleanup(self.jw.__exit__)
        self.heights = lambda *b: _flat_heights(5, 1, *b)

    def registry(self, ring=1):
        import cell_registry
        return cell_registry.open_registry(blocks=self.jw.blocks, ghosts=lg.ghost_planner(self.heights), ring=ring)

    def real_ground(self, cell):
        x0, z0, _x1, _z1 = cellmath.bounds(cell)
        self.jw.build(f'ground_{cell[0]}_{cell[1]}', 'ground', {(x0, 5, z0): 'minecraft:grass_block'}, cell=cell)


class GhostPlannerAndNeeds(_Tiny):
    """The registry's ghost planner wraps the lidar plan builders; needs come from the view."""

    def test_planner_wraps_plan_builders(self):
        planner = lg.ghost_planner(self.heights)
        self.assertEqual(planner((0, 0), 'ground'), lg.plan_ghost_ground((0, 0), heights=self.heights(0, 0, 1, 1)).cells)
        self.assertEqual(len(planner((0, 0), 'features')), 4)

    def test_planner_returns_none_without_lidar(self):
        def missing(*_b):
            raise LookupError('tile not cached')
        self.assertIsNone(lg.ghost_planner(missing)((0, 0), 'ground'))

    def test_ensure_plans_both_layers_when_empty(self):
        rep = lg.ensure_cells(self.registry(), [(0, 0)])[0]
        self.assertNotIn('skipped', rep)
        self.assertEqual(rep['ground']['blocks'], 4 * 5)
        self.assertEqual(rep['features']['blocks'], 4 * 1)
        self.assertEqual(self.jw.reg.view(), {})                         # dry run journals nothing

    def test_ensure_applies_and_then_skips_complete_cells(self):
        reg = self.registry()
        rep = lg.ensure_cells(reg, [(0, 0)], apply=self.jw.apply)[0]
        self.assertEqual(rep['actions'], ['plan-ground', 'apply-ground', 'plan-features', 'apply-features'])
        v = reg.cell((0, 0))
        self.assertEqual((v.ground, v.ghost.ground, v.ghost.features), ('ghost+features', True, True))
        self.assertEqual(lg.ensure_cells(reg, [(0, 0)])[0]['skipped'], 'layers-complete')

    def test_real_ground_skips_ghost_ground_but_still_wants_grey_mass(self):
        self.real_ground((0, 0))
        rep = lg.ensure_cells(self.registry(), [(0, 0)])[0]
        self.assertEqual(rep['ground_skip'], 'real-ground')
        self.assertIn('features', rep)

    def test_real_feature_in_the_cell_means_no_grey_mass(self):
        self.real_ground((0, 0))
        self.jw.build('house', 'feature', {(0, 6, 0): 'minecraft:bricks'})
        rep = lg.ensure_cells(self.registry(), [(0, 0)])[0]
        self.assertEqual(rep['skipped'], 'layers-complete')

    def test_ghost_plans_drop_positions_owned_by_real_builds(self):
        self.jw.build('wall', 'feature', {(0, 5, 0): 'minecraft:bricks'})
        reg = self.registry()
        b = lg.cell_builds(reg, (0, 0), 'ground')
        self.assertNotIn((0, 5, 0), b.cells)
        self.assertIn((1, 5, 0), b.cells)


class OnCellFocus(_Tiny):
    """Focus raise ensures the journal frontier, or the focus ring before any real work."""

    def test_frontier_from_seeds_includes_focus_and_skips_real_ground(self):
        self.real_ground((1, 0))
        reps = lg.on_cell_focus((0, 0), ring=1, dry=True, heights_fn=self.heights)
        by = {tuple(r['cell']): r for r in reps}
        self.assertIn((0, 0), by)                                       # inside the seed's ring
        self.assertEqual(by[(1, 0)]['ground_skip'], 'real-ground')
        self.assertIn('ground', by[(2, 1)])

    def test_before_seeds_rings_the_focus_and_skips_it(self):
        reps = lg.on_cell_focus((0, 0), ring=1, dry=True, heights_fn=self.heights)
        cells = {tuple(r['cell']) for r in reps}
        self.assertNotIn((0, 0), cells)
        self.assertEqual(len(cells), 8)

    def test_frontier_unions_two_seeds(self):
        self.real_ground((0, 0))
        self.real_ground((3, 0))
        targets = self.registry().frontier(1)
        self.assertTrue({(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)} <= targets)

    def test_ring_from_env(self):
        with mock.patch.dict(os.environ, {'LIDAR_GHOST_RING': '2'}):
            self.assertEqual(lg.ring_from_env(), 2)
        os.environ.pop('LIDAR_GHOST_RING', None)
        self.assertEqual(lg.ring_from_env(), lg.DEFAULT_RING)


class CliDryRun(_Tiny):
    """CLI plan/status with fixture heights (no server)."""

    def test_plan_one_cell_with_heights_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / 'h.json'
            fixture.write_text(json.dumps({'ground_y': [[10, 10], [10, 10]], 'feature': [[0, 1], [2, 0]]}))
            with mock.patch('sys.stdout'):
                code = lg.main(['plan', '--cell', '0,0', '--heights-json', str(fixture)])
            self.assertEqual(code, 0)

    def test_status_json_reads_the_journal_views(self):
        lg.ensure_cells(self.registry(), [(0, 0)], apply=self.jw.apply)
        self.real_ground((1, 0))
        with mock.patch('sys.stdout', new_callable=io.StringIO) as out:
            code = lg.main(['status', '--cell', '0,0', '--ring', '1'])
        self.assertEqual(code, 0)
        rows = {tuple(r['cell']): r for r in json.loads(out.getvalue())['cells']}
        self.assertEqual(rows[(0, 0)]['ghost_ground'], 'applied')
        self.assertTrue(rows[(0, 0)]['ghosts_complete'])
        self.assertTrue(rows[(1, 0)]['real_ground'])
        self.assertFalse(rows[(1, 0)]['needs_ghost_ground'])


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
