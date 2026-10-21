#!/usr/bin/env python3
"""Tests for registry writer hooks (ticket #30 seams).

Seams: registry_hooks.on_* public functions against a temp RegistryStore.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cell_registry as R  # noqa: E402
import registry_hooks as H  # noqa: E402


class RegistryHooksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = R.RegistryStore(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_layer_clean_ground_trees_decor(self):
        H.on_layer_clean((48, -44), 'ground', store=self.store)
        self.assertEqual(self.store.cell('48,-44')['ground'], 'generated')
        H.on_layer_clean((48, -44), 'trees', store=self.store)
        H.on_layer_clean((48, -44), 'decor', store=self.store)
        self.assertTrue(self.store.cell('48,-44')['trees'])
        self.assertTrue(self.store.cell('48,-44')['decor'])

    def test_layer_clean_refuses_trees_before_ground(self):
        with self.assertRaises(R.RegistryError):
            H.on_layer_clean((48, -44), 'trees', store=self.store)

    def test_feature_begin_then_clean_sets_built_and_cells(self):
        positions = [(6144, 60, -5632), (6272, 60, -5632)]  # cells 48,-44 and 49,-44
        H.on_feature_begin('q001_x', name='X', cells=['48,-44'], store=self.store)
        self.assertEqual(self.store.feature('q001_x')['status'], 'in-progress')
        H.on_feature_clean('q001_x', positions, name='X', store=self.store)
        feat = self.store.feature('q001_x')
        self.assertEqual(feat['status'], 'built')
        self.assertEqual(feat['cells'], ['48,-44', '49,-44'])

    def test_feature_mismatches_stay_in_progress(self):
        H.on_feature_begin('q001_x', cells=['48,-44'], store=self.store)
        H.on_feature_mismatches('q001_x', [(6144, 60, -5632)], store=self.store)
        self.assertEqual(self.store.feature('q001_x')['status'], 'in-progress')

    def test_marker_raise_clears_previous(self):
        H.on_marker_raise((48, -44), store=self.store)
        H.on_marker_raise((48, -45), store=self.store)
        self.assertEqual(self.store.cell('48,-44')['marker'], 'none')
        self.assertEqual(self.store.cell('48,-45')['marker'], 'up')
        self.assertEqual(self.store.current_cell(), (48, -45))

    def test_ghost_ground_then_features_then_strip(self):
        H.on_ghost_ground_applied((48, -44), store=self.store)
        self.assertEqual(self.store.cell('48,-44')['ground'], 'ghost')
        H.on_ghost_features_applied((48, -44), store=self.store)
        self.assertEqual(self.store.cell('48,-44')['ground'], 'ghost+features')
        H.on_ghost_features_stripped([(48, -44)], store=self.store)
        self.assertEqual(self.store.cell('48,-44')['ground'], 'ghost')

    def test_real_ground_via_stage_dispatch(self):
        H.on_ghost_features_applied((48, -44), store=self.store)
        H.on_stage_key_clean((48, -44), 'ground-layer', mismatches=0, store=self.store)
        self.assertEqual(self.store.cell('48,-44')['ground'], 'generated')
        self.assertFalse(self.store.cell('48,-44')['trees'])

    def test_failed_readback_does_not_set_layer(self):
        H.on_stage_key_clean((48, -44), 'ground-layer', mismatches=3, store=self.store)
        self.assertEqual(self.store.cell('48,-44')['ground'], 'none')

    def test_undo_clears_trees(self):
        H.on_layer_clean((48, -44), 'ground', store=self.store)
        H.on_layer_clean((48, -44), 'trees', store=self.store)
        H.on_stage_key_clean(
            (48, -44), 'trees-undo', meta={'kind': 'undo', 'pass': 'trees'},
            mismatches=0, store=self.store,
        )
        self.assertFalse(self.store.cell('48,-44')['trees'])

    def test_cells_touched_by_positions(self):
        ids = H.cells_touched_by_positions([(6144, 1, -5632), (6150, 2, -5600)])
        self.assertEqual(ids, ['48,-44'])


if __name__ == '__main__':
    unittest.main()
