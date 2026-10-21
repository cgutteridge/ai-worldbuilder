"""Ghost-feature readback gate on site_kit.apply (task #34).

Pre-strip plans from ownership records only; grey mass with no record can remain
under a real feature. After live feature apply, leftover FEATURE_BLOCK in plan
columns must fail closed. Pre-strip exceptions must raise, not print-and-continue.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import site_kit
import site_lock
import site_stages

AIR = 'minecraft:air'
FEATURE = 'minecraft:light_gray_concrete'
CELL = (48, -44)
X0, Z0 = 6150, -5600


class View:
    def __init__(self, blocks):
        self.blocks = blocks

    def block(self, x, y, z):
        return self.blocks.get((x, y, z), AIR)


class FakePaced:
    world = {}
    structures = {}
    calls = []
    fills_done = 0
    skip_fills = False
    abort_on_save = False

    def __init__(self, *a, **k):
        pass

    def call(self, name, args):
        FakePaced.calls.append(name)
        if name == 'server_save_all_worlds' and FakePaced.abort_on_save:
            raise RuntimeError('server died')
        return {'content': []}

    def text(self, name, args):
        FakePaced.calls.append(name)
        if name == 'structure_save_from_world':
            FakePaced.structures[args['name']] = dict(FakePaced.world)
            return 'saved'
        if name == 'block_fill_batch':
            for f in args['fills']:
                a, b = f['from'], f['to']
                if not FakePaced.skip_fills:
                    for x in range(a[0], b[0] + 1):
                        for y in range(a[1], b[1] + 1):
                            for z in range(a[2], b[2] + 1):
                                FakePaced.world[(x, y, z)] = f['block']
                FakePaced.fills_done += 1
            return 'filled'
        return ''


class GhostGateBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        for d in ('out', 'cells', 'snaps'):
            (t / d).mkdir()
        FakePaced.world = {}
        FakePaced.structures, FakePaced.calls = {}, []
        FakePaced.fills_done = 0
        FakePaced.skip_fills = FakePaced.abort_on_save = False
        self.t = t
        patches = [
            mock.patch.object(site_kit, 'OUT', t / 'out'),
            mock.patch.object(site_stages, 'CELLS', t / 'cells'),
            mock.patch.object(site_lock, 'LOCK', t / 'LOCK.json'),
            mock.patch.object(site_kit, 'World', lambda region=None: View(FakePaced.world)),
            mock.patch.object(site_kit, '_bring_player', lambda *a, **k: None),
            mock.patch.object(site_kit, 'built_cells', lambda exclude=(), **kw: {}),
            mock.patch('mc_paced.Paced', FakePaced),
            mock.patch('osm_footprints.guard', lambda cells: {'outside': {}, 'blind': []}),
            mock.patch('roof_lint.ridge_jogs', lambda cells: []),
            mock.patch('time.sleep', lambda s: None),
            mock.patch.dict(os.environ, {
                'SITE_OWNER': 'alice',
                'AI_WORLDBUILDER_REGISTRY_ROOT': str(t),
            }),
            mock.patch.object(site_kit, '_reconcile_lidar_ghosts', lambda *a, **k: None),
            mock.patch('lidar_ghost.strip_ghost_features_before_feature_apply', return_value=[]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        hb = 1e9
        site_lock.LOCK.write_text(json.dumps({
            'owner': 'alice', 'task': 't', 'claimed': hb, 'heartbeat': hb,
            'claimed_at': 'x', 'heartbeat_at': 'x',
        }))

    def run_apply(self, plan, **kw):
        with mock.patch.object(site_lock, 'check', lambda owner=None: None):
            return site_kit.apply(plan, **kw)


class TestFindGhostFeatureLeftovers(unittest.TestCase):
    """Pure scan seam: leftover FEATURE_BLOCK the plan did not place."""

    def test_unrecorded_ghost_in_plan_column_is_leftover(self):
        plan = site_kit.Plan('shell')
        plan.set(X0, 60, Z0, 'minecraft:stone')
        plan.set(X0, 62, Z0, 'minecraft:stone')  # Y span includes mid-column
        world = View({
            (X0, 60, Z0): 'minecraft:stone',
            (X0, 61, Z0): FEATURE,  # ghost with no ownership record
            (X0, 62, Z0): 'minecraft:stone',
        })
        hits = site_kit.find_ghost_feature_leftovers(plan, world)
        self.assertEqual(hits, [(X0, 61, Z0)])

    def test_plan_placed_feature_block_is_not_leftover(self):
        plan = site_kit.Plan('pave')
        plan.set(X0, 60, Z0, FEATURE)
        world = View({(X0, 60, Z0): FEATURE})
        self.assertEqual(site_kit.find_ghost_feature_leftovers(plan, world), [])

    def test_neighbour_cell_column_on_cross_cell_plan(self):
        """Columns in a neighbour cell the footprint crosses are scanned."""
        import cellmath

        # Two blocks in different cells when CELL size is 128.
        x_a, z = 130, 0
        x_b = 256
        self.assertNotEqual(cellmath.cell_of(x_a, z), cellmath.cell_of(x_b, z))
        plan = site_kit.Plan('cross')
        plan.set(x_a, 50, z, 'minecraft:bricks')
        plan.set(x_b, 50, z, 'minecraft:bricks')
        plan.set(x_b, 52, z, 'minecraft:bricks')  # span y so mid-column is in range
        world = View({
            (x_a, 50, z): 'minecraft:bricks',
            (x_b, 50, z): 'minecraft:bricks',
            (x_b, 51, z): FEATURE,  # leftover between plan cells in neighbour column
            (x_b, 52, z): 'minecraft:bricks',
        })
        hits = site_kit.find_ghost_feature_leftovers(plan, world)
        self.assertEqual(hits, [(x_b, 51, z)])


class TestApplyGhostGate(GhostGateBase):
    """apply() fail-closed behaviour."""

    def test_leftover_unrecorded_ghost_exits_nonzero(self):
        plan = site_kit.Plan('feat')
        plan.set(X0, 60, Z0, 'minecraft:stone')
        plan.set(X0, 62, Z0, 'minecraft:oak_log')
        # Ghost mid-column with no built_cells record; fills only write plan cells.
        FakePaced.world[(X0, 61, Z0)] = FEATURE
        with self.assertRaises(SystemExit) as cm:
            self.run_apply(plan)
        msg = str(cm.exception)
        self.assertIn('ghost feature leftover', msg.lower())
        self.assertIn(str(X0), msg)
        self.assertIn('61', msg)
        # Stage left applying (fail closed before complete).
        stages = json.loads(site_stages.stage_path(CELL).read_text())['stages']
        self.assertEqual(stages['feature-feat']['status'], 'applying')

    def test_pre_strip_failure_raises(self):
        plan = site_kit.Plan('feat')
        plan.set(X0, 60, Z0, 'minecraft:stone')
        plan.set(X0, 61, Z0, 'minecraft:oak_log')
        with mock.patch(
            'lidar_ghost.strip_ghost_features_before_feature_apply',
            side_effect=RuntimeError('no ownership strip'),
        ):
            with self.assertRaises(SystemExit) as cm:
                self.run_apply(plan)
        self.assertIn('pre-strip failed', str(cm.exception).lower())

    def test_clean_apply_without_ghost_succeeds(self):
        plan = site_kit.Plan('feat')
        plan.set(X0, 60, Z0, 'minecraft:stone')
        plan.set(X0, 61, Z0, 'minecraft:oak_log')
        rep = self.run_apply(plan)
        self.assertEqual(rep['mismatches'], 0)
        stages = json.loads(site_stages.stage_path(CELL).read_text())['stages']
        self.assertEqual(stages['feature-feat']['status'], 'applied')


if __name__ == '__main__':
    unittest.main()
