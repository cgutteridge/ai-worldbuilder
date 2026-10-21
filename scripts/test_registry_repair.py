#!/usr/bin/env python3
"""Tests for cell repair backup / flag clear / replay plan (ticket #32)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cell_registry as CR  # noqa: E402
import cellmath  # noqa: E402
import registry_hooks as H  # noqa: E402
import registry_repair as RR  # noqa: E402
import region_backup as RB  # noqa: E402


class ImpactedRegionsTest(unittest.TestCase):
    def setUp(self):
        cellmath.reload_grid({'cell': {'size': 128, 'origin': [48, -44]}})
        self.addCleanup(cellmath.reload_grid)

    def test_cell_alone_covers_its_regions(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            store = CR.RegistryStore(root)
            H.on_layer_clean((48, -44), 'ground', store=store)
            regs = RR.impacted_regions((48, -44), store=store, world_root=root)
        self.assertTrue(regs)
        # 48*128=6144 → region 12; -44*128=-5632 → region -11
        self.assertIn((12, -11), regs)


class RegistrySliceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = CR.RegistryStore(self.root)
        cellmath.reload_grid({'cell': {'size': 128, 'origin': [48, -44]}})

    def tearDown(self):
        cellmath.reload_grid()
        self.tmp.cleanup()

    def test_save_and_restore_cell_slice(self):
        H.on_layer_clean((48, -44), 'ground', store=self.store)
        H.on_layer_clean((48, -44), 'trees', store=self.store)
        self.store.register_feature('q001_x', name='X', cells=['48,-44'], status='built')
        slice_ = RR.export_cell_registry((48, -44), store=self.store)
        self.assertEqual(slice_['cell']['ground'], 'generated')
        self.assertEqual(slice_['features']['q001_x']['status'], 'built')
        # Mutate then restore
        self.store.set_ground('48,-44', 'none')
        self.store.set_feature_status('q001_x', 'planned')
        RR.import_cell_registry((48, -44), slice_, store=self.store)
        self.assertEqual(self.store.cell('48,-44')['ground'], 'generated')
        self.assertTrue(self.store.cell('48,-44')['trees'])
        self.assertEqual(self.store.feature('q001_x')['status'], 'built')


class ClearFlagsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = CR.RegistryStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_clear_from_trees_clears_trees_decor_and_keeps_ground(self):
        H.on_layer_clean((48, -44), 'ground', store=self.store)
        H.on_layer_clean((48, -44), 'trees', store=self.store)
        H.on_layer_clean((48, -44), 'decor', store=self.store)
        self.store.register_feature('q001_x', name='X', cells=['48,-44'], status='built')
        RR.clear_flags_from_step((48, -44), 'trees', store=self.store)
        c = self.store.cell('48,-44')
        self.assertEqual(c['ground'], 'generated')
        self.assertFalse(c['trees'])
        self.assertFalse(c['decor'])
        self.assertEqual(self.store.feature('q001_x')['status'], 'built')

    def test_clear_from_ground_reopens_features_too(self):
        H.on_layer_clean((48, -44), 'ground', store=self.store)
        self.store.register_feature('q001_x', name='X', cells=['48,-44'], status='built')
        H.on_layer_clean((48, -44), 'trees', store=self.store)
        RR.clear_flags_from_step((48, -44), 'ground', store=self.store)
        self.assertEqual(self.store.cell('48,-44')['ground'], 'none')
        self.assertEqual(self.store.feature('q001_x')['status'], 'in-progress')

    def test_clear_from_features_reopens_features_and_later_layers(self):
        H.on_layer_clean((48, -44), 'ground', store=self.store)
        H.on_layer_clean((48, -44), 'trees', store=self.store)
        self.store.register_feature('q001_x', name='X', cells=['48,-44'], status='built')
        RR.clear_flags_from_step((48, -44), 'features', store=self.store)
        self.assertEqual(self.store.feature('q001_x')['status'], 'in-progress')
        self.assertFalse(self.store.cell('48,-44')['trees'])


class ReplayPlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = CR.RegistryStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_order_ground_features_trees_decor(self):
        H.on_layer_clean((48, -44), 'ground', store=self.store)
        self.store.register_feature('a', name='A', cells=['48,-44'], status='built')
        self.store.register_feature('b', name='B', cells=['48,-44'], status='built')
        H.on_layer_clean((48, -44), 'trees', store=self.store)
        H.on_layer_clean((48, -44), 'decor', store=self.store)
        steps = RR.replay_plan((48, -44), store=self.store)
        kinds = [s['kind'] for s in steps]
        self.assertEqual(kinds, ['ground', 'feature', 'feature', 'trees', 'decor'])
        self.assertEqual([s['feature'] for s in steps if s['kind'] == 'feature'], ['a', 'b'])

    def test_ghost_ground_when_registry_says_ghost(self):
        self.store.set_ground('48,-44', 'ghost')
        steps = RR.replay_plan((48, -44), store=self.store)
        self.assertEqual(steps[0], {'kind': 'ghost-ground', 'cell': '48,-44'})


class BackupRepairTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.region = self.root / 'region'
        self.region.mkdir()
        (self.region / 'r.12.-11.mca').write_bytes(b'region-data')
        self.store = CR.RegistryStore(self.root)
        H.on_layer_clean((48, -44), 'ground', store=self.store)
        cellmath.reload_grid({'cell': {'size': 128, 'origin': [48, -44]}})
        self.backup_root = self.root / 'backups' / 'regions'

    def tearDown(self):
        cellmath.reload_grid()
        self.tmp.cleanup()

    def test_backup_writes_repair_kind_and_registry_slice(self):
        with mock.patch.object(RB, 'REGION', self.region), \
                mock.patch.object(RB, 'BACKUP_ROOT', self.backup_root), \
                mock.patch('logout_gate.require_logged_out'):
            dest = RR.backup_cell_repair(
                (48, -44),
                store=self.store,
                world_root=self.root,
                region_dir=self.region,
                backup_root=self.backup_root,
                confirm_logged_out=True,
            )
        meta = json.loads((dest / 'BACKUP.json').read_text())
        self.assertEqual(meta['kind'], RB.KIND_REPAIR)
        self.assertEqual(meta['cell'], '48,-44')
        self.assertIn('r.12.-11.mca', meta['files'])
        slice_ = json.loads((dest / 'registry-cell.json').read_text())
        self.assertEqual(slice_['cell']['ground'], 'generated')


if __name__ == '__main__':
    unittest.main()
