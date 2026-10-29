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
import signal
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

    def test_neighbour_ring_default_is_two_deep_and_excludes_focus(self):
        focus = (48, -44)
        cells = lg.neighbour_cells(focus)
        self.assertEqual(lg.DEFAULT_RING, 2)
        self.assertEqual(len(cells), 8 + 16)
        self.assertNotIn(focus, cells)
        self.assertEqual({cellmath.ring_distance(c, focus) for c in cells}, {1, 2})

    def test_smaller_ring_is_one_deep(self):
        focus = (48, -44)
        cells = lg.neighbour_cells(focus, ring=1)
        self.assertEqual(len(cells), 8)
        self.assertTrue(all(cellmath.ring_distance(c, focus) == 1 for c in cells))

    def test_neighbour_ring_zero_with_focus(self):
        focus = (5, 5)
        self.assertEqual(lg.neighbour_cells(focus, ring=0, include_focus=True), [focus])
        self.assertEqual(lg.neighbour_cells(focus, ring=0, include_focus=False), [])

    def test_ghost_owners_for_is_ground_only(self):
        self.assertEqual(lg.ghost_owners_for((48, -44)), frozenset({'ghost_48_m44_ground'}))
        self.assertEqual(lg.ghost_owners_for((10, 3)), frozenset({'ghost_10_3_ground'}))


class PlanFromMockHeights(unittest.TestCase):
    """Ground plans from injectable samples (tiny synthetic cell)."""

    def setUp(self):
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
        self.assertEqual(plan.name, 'ghost_0_0_ground')
        self.assertEqual(plan.cells[(0, 5, 0)], 'minecraft:light_gray_concrete')
        self.assertEqual(plan.cells[(0, 1, 0)], 'minecraft:stone')
        self.assertNotIn((0, 0, 0), plan.cells)
        self.assertEqual(len(plan.cells), 4 * 5)

    def test_top_colour_bands_from_feature_height(self):
        cell = (0, 0)
        x0, z0, x1, z1 = cellmath.bounds(cell)
        h = _flat_heights(5, 0, x0, z0, x1, z1)
        h['feature'][0, 0] = 0
        h['feature'][1, 0] = 2
        h['feature'][0, 1] = 3
        plan = lg.plan_ghost_ground(cell, heights=h)
        self.assertEqual(plan.cells[(x0, 5, z0)], 'minecraft:light_gray_concrete')
        self.assertEqual(plan.cells[(x0 + 1, 5, z0)], 'minecraft:gray_concrete')
        self.assertEqual(plan.cells[(x0, 5, z0 + 1)], 'minecraft:black_concrete')
        self.assertNotIn((x0, 6, z0), plan.cells)  # no volumetric columns

    def test_top_block_for_feature_height_bands(self):
        self.assertEqual(lg.top_block_for_feature_height(0), 'light_gray_concrete')
        self.assertEqual(lg.top_block_for_feature_height(1), 'gray_concrete')
        self.assertEqual(lg.top_block_for_feature_height(2), 'gray_concrete')
        self.assertEqual(lg.top_block_for_feature_height(3), 'black_concrete')


class _Tiny(unittest.TestCase):
    """CELL patched to 2 and a throwaway journal (journal_testkit) as the active registry."""

    def setUp(self):
        from journal_testkit import JournalWorld
        for p in (mock.patch.object(cellmath, 'CELL', 2), mock.patch.object(lg.cellmath, 'CELL', 2),
                  mock.patch.dict(os.environ, {}, clear=False),
                  mock.patch('sys.stderr', new_callable=io.StringIO)):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop('LIDAR_GHOST_RING', None)
        self.jw = JournalWorld()
        self.jw.__enter__()
        self.addCleanup(self.jw.__exit__)
        self.heights = lambda *b: _flat_heights(5, 1, *b)

    def registry(self, ring=lg.DEFAULT_RING):
        import cell_registry
        return cell_registry.open_registry(blocks=self.jw.blocks, ghosts=lg.ghost_planner(self.heights), ring=ring)

    def real_ground(self, cell):
        x0, z0, _x1, _z1 = cellmath.bounds(cell)
        self.jw.build(f'ground_{cell[0]}_{cell[1]}', 'ground', {(x0, 5, z0): 'minecraft:grass_block'}, cell=cell)


class GhostPlannerAndNeeds(_Tiny):
    """The registry's ghost planner wraps the lidar plan builder; needs come from the view."""

    def test_planner_wraps_plan_builder(self):
        planner = lg.ghost_planner(self.heights)
        self.assertEqual(planner((0, 0), 'ground'), lg.plan_ghost_ground((0, 0), heights=self.heights(0, 0, 1, 1)).cells)
        with self.assertRaises(ValueError):
            planner((0, 0), 'features')

    def test_planner_returns_none_without_lidar(self):
        def missing(*_b):
            raise LookupError('tile not cached')
        self.assertIsNone(lg.ghost_planner(missing)((0, 0), 'ground'))

    def test_ensure_plans_ground_when_empty(self):
        rep = lg.ensure_cells(self.registry(), [(0, 0)])[0]
        self.assertNotIn('skipped', rep)
        self.assertEqual(rep['ground']['blocks'], 4 * 5)
        self.assertEqual(rep['ground']['gray_concrete'], 4)  # feature height 1 → grey tops
        self.assertEqual(self.jw.reg.view(), {})

    def test_ensure_applies_and_then_skips_complete_cells(self):
        reg = self.registry()
        rep = lg.ensure_cells(reg, [(0, 0)], apply=self.jw.apply)[0]
        self.assertEqual(rep['actions'], ['plan-ground', 'apply-ground'])
        v = reg.cell((0, 0))
        self.assertEqual((v.ground, v.ghost.ground), ('ghost', True))
        self.assertEqual(lg.ensure_cells(reg, [(0, 0)])[0]['skipped'], 'layers-complete')

    def test_real_ground_skips_ghost_ground(self):
        self.real_ground((0, 0))
        rep = lg.ensure_cells(self.registry(), [(0, 0)])[0]
        self.assertEqual(rep['ground_skip'], 'real-ground')
        self.assertEqual(rep['skipped'], 'layers-complete')

    def test_ghost_plans_drop_positions_owned_by_real_builds(self):
        self.jw.build('wall', 'feature', {(0, 5, 0): 'minecraft:bricks'})
        reg = self.registry()
        b = lg.cell_builds(reg, (0, 0), 'ground')
        self.assertNotIn((0, 5, 0), b.cells)
        self.assertIn((1, 5, 0), b.cells)


class OnCellFocus(_Tiny):
    """Focus raise ensures the journal frontier, or the focus ring before any real work."""

    def test_frontier_from_seeds_is_two_deep_and_skips_real_ground(self):
        self.real_ground((1, 0))
        reps = lg.on_cell_focus((0, 0), dry=True, heights_fn=self.heights)
        by = {tuple(r['cell']): r for r in reps}
        self.assertEqual(set(by), {(x, z) for x in range(-1, 4) for z in range(-2, 3)})
        self.assertEqual(by[(1, 0)]['ground_skip'], 'real-ground')
        self.assertIn('ground', by[(3, 2)])

    def test_before_seeds_rings_the_focus_two_deep_and_skips_it(self):
        reps = lg.on_cell_focus((0, 0), dry=True, heights_fn=self.heights)
        cells = {tuple(r['cell']) for r in reps}
        self.assertNotIn((0, 0), cells)
        self.assertEqual(len(cells), 24)
        self.assertEqual({cellmath.ring_distance(c, (0, 0)) for c in cells}, {1, 2})

    def test_env_ring_overrides_the_default_depth(self):
        with mock.patch.dict(os.environ, {'LIDAR_GHOST_RING': '1'}):
            reps = lg.on_cell_focus((0, 0), dry=True, heights_fn=self.heights)
        self.assertEqual(len(reps), 8)

    def test_explicit_ring_beats_env(self):
        with mock.patch.dict(os.environ, {'LIDAR_GHOST_RING': '1'}):
            reps = lg.on_cell_focus((0, 0), ring=2, dry=True, heights_fn=self.heights)
        self.assertEqual(len(reps), 24)

    def test_ring_zero_ghosts_nothing_before_seeds_and_only_the_seed_after(self):
        self.assertEqual(lg.on_cell_focus((0, 0), ring=0, dry=True, heights_fn=self.heights), [])
        self.real_ground((1, 0))
        reps = lg.on_cell_focus((0, 0), ring=0, dry=True, heights_fn=self.heights)
        self.assertEqual([tuple(r['cell']) for r in reps], [(1, 0)])

    def test_frontier_unions_two_seeds(self):
        self.real_ground((0, 0))
        self.real_ground((7, 0))
        targets = self.registry().frontier()
        self.assertTrue({(x, 0) for x in (-2, 0, 2, 5, 7, 9)} <= targets)
        self.assertFalse({(3, 0), (4, 0)} & targets)

    def test_ring_from_env(self):
        with mock.patch.dict(os.environ, {'LIDAR_GHOST_RING': '3'}):
            self.assertEqual(lg.ring_from_env(), 3)
        with mock.patch.dict(os.environ, {'LIDAR_GHOST_RING': '-1'}):
            with self.assertRaises(ValueError):
                lg.ring_from_env()
        os.environ.pop('LIDAR_GHOST_RING', None)
        self.assertEqual(lg.ring_from_env(), lg.DEFAULT_RING)


class ApplyProgress(_Tiny):
    """Per-cell stderr progress, journal-status summary, clean SIGTERM (task #60)."""

    def test_ensure_prints_one_flushed_stderr_line_per_cell(self):
        err = io.StringIO()
        with mock.patch('sys.stderr', err):
            lg.ensure_cells(self.registry(), [(0, 0), (1, 0)])
        lines = [ln for ln in err.getvalue().splitlines() if 'cell ' in ln]
        self.assertEqual(len(lines), 2)
        self.assertIn('0,0', lines[0])
        self.assertIn('1/2', lines[0])
        self.assertIn('1,0', lines[1])
        self.assertIn('2/2', lines[1])

    def test_apply_announces_ghost_count_once_before_first_cell(self):
        err = io.StringIO()
        with mock.patch('sys.stderr', err):
            lg.ensure_cells(self.registry(), [(0, 0), (1, 0)], apply=self.jw.apply)
        lines = err.getvalue().splitlines()
        notice = [ln for ln in lines if ln.startswith('ghosts:')]
        self.assertEqual(len(notice), 1)
        self.assertIn('generating 2 ghost cells', notice[0])
        self.assertIn('will take a while', notice[0])
        self.assertEqual(lines[0], notice[0])

    def test_apply_notice_counts_only_cells_that_need_ghosts(self):
        self.real_ground((0, 0))
        err = io.StringIO()
        with mock.patch('sys.stderr', err):
            lg.ensure_cells(self.registry(), [(0, 0), (1, 0)], apply=self.jw.apply)
        self.assertIn('generating 1 ghost cell ', err.getvalue())

    def test_no_notice_when_nothing_needed_or_dry_run(self):
        reg = self.registry()
        err = io.StringIO()
        with mock.patch('sys.stderr', err):
            lg.ensure_cells(reg, [(0, 0), (1, 0)])                # dry run
        self.assertNotIn('ghosts:', err.getvalue())
        lg.ensure_cells(reg, [(0, 0)], apply=self.jw.apply)
        err = io.StringIO()
        with mock.patch('sys.stderr', err):
            lg.ensure_cells(reg, [(0, 0)], apply=self.jw.apply)   # already ghosted
        self.assertNotIn('ghosts:', err.getvalue())

    def test_ensure_summary_counts_landed_and_skipped_builds(self):
        reg = self.registry()
        lg.ensure_cells(reg, [(0, 0)], apply=self.jw.apply)
        err = io.StringIO()
        with mock.patch('sys.stderr', err):
            lg.ensure_cells(reg, [(0, 0), (1, 0)], apply=self.jw.apply)
        summary = [ln for ln in err.getvalue().splitlines() if 'summary' in ln]
        self.assertEqual(len(summary), 1)
        self.assertIn('landed=1', summary[0])
        self.assertIn('mismatched=0', summary[0])
        self.assertIn('abandoned=0', summary[0])
        self.assertIn('skipped=1', summary[0])

    def test_sigterm_names_in_flight_cell_and_reconcile_hint(self):
        lg.set_in_flight_cell((48, -44))
        self.addCleanup(lg.set_in_flight_cell, None)
        err = io.StringIO()
        with mock.patch('sys.stderr', err):
            with self.assertRaises(SystemExit) as cm:
                lg.handle_sigterm(signal.SIGTERM, None)
        self.assertNotEqual(cm.exception.code, 0)
        text = err.getvalue()
        self.assertIn('48,-44', text)
        self.assertIn('cell_registry.py reconcile', text)

    def test_cli_plan_keeps_final_json_on_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / 'h.json'
            fixture.write_text(json.dumps({'ground_y': [[10, 10], [10, 10]], 'feature': [[0, 1], [2, 0]]}))
            out, err = io.StringIO(), io.StringIO()
            with mock.patch('sys.stdout', out), mock.patch('sys.stderr', err):
                code = lg.main(['plan', '--cell', '0,0', '--heights-json', str(fixture)])
            self.assertEqual(code, 0)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload['focus'], [0, 0])
            self.assertTrue(payload['dry'])
            self.assertIn('reports', payload)
            self.assertTrue(any('cell ' in ln for ln in err.getvalue().splitlines()))


class CliDryRun(_Tiny):
    """CLI plan/status with fixture heights (no server)."""

    def test_plan_one_cell_with_heights_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / 'h.json'
            fixture.write_text(json.dumps({'ground_y': [[10, 10], [10, 10]], 'feature': [[0, 1], [2, 0]]}))
            with mock.patch('sys.stdout'), mock.patch('sys.stderr'):
                code = lg.main(['plan', '--cell', '0,0', '--heights-json', str(fixture)])
            self.assertEqual(code, 0)

    def test_status_json_reads_the_journal_views(self):
        lg.ensure_cells(self.registry(), [(0, 0)], apply=self.jw.apply)
        self.real_ground((1, 0))
        with mock.patch('sys.stdout', new_callable=io.StringIO) as out:
            code = lg.main(['status', '--cell', '0,0', '--ring', '2'])
        self.assertEqual(code, 0)
        rows = {tuple(r['cell']): r for r in json.loads(out.getvalue())['cells']}
        self.assertEqual(rows[(0, 0)]['ghost_ground'], 'applied')
        self.assertTrue(rows[(0, 0)]['ghosts_complete'])
        self.assertTrue(rows[(1, 0)]['real_ground'])
        self.assertFalse(rows[(1, 0)]['needs_ghost_ground'])


class GroundLayerIgnoresGhosts(unittest.TestCase):
    """Real ground pass may overwrite ghost owners."""

    def test_ignored_builds_includes_ghost_ground_owner(self):
        import ground_layer as gl
        ign = gl.ignored_builds((48, -44))
        self.assertIn('ghost_48_m44_ground', ign)


class CellFeaturesGhostNotFeature(unittest.TestCase):
    """Ghost builds are layer-like, not queue features."""

    def test_ghost_names_are_not_feature_builds(self):
        import cell_features as cf
        self.assertFalse(cf.is_feature_build('ghost_48_m44_ground'))
        self.assertTrue(cf.is_feature_build('b2_main'))


if __name__ == '__main__':
    unittest.main()
