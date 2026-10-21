#!/usr/bin/env python3
"""Tests for the cell registry module (ticket #29 seams).

Seams: RegistryStore public write API (set_ground, set_trees, set_decor, set_marker,
set_hold, register_feature, set_feature_status, dismiss_coverage_warning) and derived
queries (is_empty, is_done, current_cell, features_in_cell).
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cell_registry as R  # noqa: E402


class RegistryInvariantsTest(unittest.TestCase):
    """Refused writes raise RegistryError naming the rule and leave disk unchanged."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.reg = R.RegistryStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def _cells_on_disk(self) -> dict:
        path = self.root / 'registry' / 'cells.json'
        if not path.exists():
            return {}
        return json.loads(path.read_text())

    def _features_on_disk(self) -> dict:
        path = self.root / 'registry' / 'features.json'
        if not path.exists():
            return {}
        return json.loads(path.read_text())

    def test_absent_cell_is_empty(self):
        self.assertTrue(self.reg.is_empty('48,-44'))
        self.assertFalse(self.reg.is_done('48,-44'))

    def test_single_marker_invariant(self):
        self.reg.set_marker('48,-44', 'up')
        with self.assertRaises(R.RegistryError) as ctx:
            self.reg.set_marker('48,-45', 'up')
        self.assertIn('single marker', str(ctx.exception).lower())
        self.assertEqual(self.reg.current_cell(), (48, -44))
        self.assertNotIn('48,-45', self._cells_on_disk())

    def test_trees_only_on_generated_ground(self):
        self.reg.set_ground('48,-44', 'ghost')
        with self.assertRaises(R.RegistryError) as ctx:
            self.reg.set_trees('48,-44', True)
        self.assertIn('generated', str(ctx.exception).lower())
        self.assertFalse(self._cells_on_disk()['48,-44'].get('trees'))

    def test_decor_only_on_generated_ground(self):
        self.reg.set_ground('48,-44', 'none')
        with self.assertRaises(R.RegistryError) as ctx:
            self.reg.set_decor('48,-44', True)
        self.assertIn('generated', str(ctx.exception).lower())

    def test_ghost_plus_features_only_from_ghost(self):
        self.reg.set_ground('48,-44', 'none')
        with self.assertRaises(R.RegistryError) as ctx:
            self.reg.set_ground('48,-44', 'ghost+features')
        self.assertIn('ghost', str(ctx.exception).lower())
        self.assertEqual(self._cells_on_disk()['48,-44']['ground'], 'none')

        self.reg.set_ground('48,-44', 'ghost')
        self.reg.set_ground('48,-44', 'ghost+features')
        self.assertEqual(self._cells_on_disk()['48,-44']['ground'], 'ghost+features')

    def test_rebuilding_ground_clears_trees_and_decor(self):
        self.reg.set_ground('48,-44', 'generated')
        self.reg.set_trees('48,-44', True)
        self.reg.set_decor('48,-44', True)
        self.reg.set_ground('48,-44', 'generated')  # rebuild
        cell = self._cells_on_disk()['48,-44']
        self.assertFalse(cell['trees'])
        self.assertFalse(cell['decor'])

    def test_reopening_feature_clears_trees_decor_in_touched_cells(self):
        self.reg.register_feature(
            'q001_road', name='Road', cells=['48,-44', '48,-45'], status='built',
        )
        for cid in ('48,-44', '48,-45'):
            self.reg.set_ground(cid, 'generated')
            self.reg.set_trees(cid, True)
            self.reg.set_decor(cid, True)
        self.reg.set_feature_status('q001_road', 'in-progress')
        for cid in ('48,-44', '48,-45'):
            cell = self._cells_on_disk()[cid]
            self.assertFalse(cell['trees'], cid)
            self.assertFalse(cell['decor'], cid)
        self.assertEqual(self._features_on_disk()['q001_road']['status'], 'in-progress')

    def test_register_overwrite_reopen_also_clears_trees_decor(self):
        self.reg.register_feature(
            'q001_road', name='Road', cells=['48,-44'], status='built',
        )
        self.reg.set_ground('48,-44', 'generated')
        self.reg.set_trees('48,-44', True)
        self.reg.register_feature(
            'q001_road', name='Road', cells=['48,-44', '48,-45'], status='planned',
        )
        self.assertFalse(self._cells_on_disk()['48,-44']['trees'])


    def test_done_requires_layers_features_no_marker_no_warnings(self):
        self.reg.register_feature(
            'q001_road', name='Road', cells=['48,-44'], status='planned',
        )
        self.reg.set_ground('48,-44', 'generated')
        self.reg.set_trees('48,-44', True)
        self.reg.set_decor('48,-44', True)
        self.assertFalse(self.reg.is_done('48,-44'))  # feature not built
        self.reg.set_feature_status('q001_road', 'built')
        self.assertTrue(self.reg.is_done('48,-44'))
        self.reg.set_marker('48,-44', 'up')
        self.assertFalse(self.reg.is_done('48,-44'))
        self.reg.set_marker('48,-44', 'none')
        self.reg.add_coverage_warning('48,-44', 'b1', 'uncovered building')
        self.assertFalse(self.reg.is_done('48,-44'))
        self.reg.dismiss_coverage_warning('48,-44', 'b1', reason='shed, ignore')
        self.assertTrue(self.reg.is_done('48,-44'))

    def test_features_in_cell_derived_not_stored(self):
        self.reg.register_feature('a', name='A', cells=['48,-44'], status='planned')
        self.reg.register_feature('b', name='B', cells=['48,-45', '48,-44'], status='built')
        ids = [f['id'] for f in self.reg.features_in_cell('48,-44')]
        self.assertEqual(ids, ['a', 'b'])
        cell = self._cells_on_disk()['48,-44'] if '48,-44' in self._cells_on_disk() else {}
        self.assertNotIn('features', cell)

    def test_atomic_refuse_leaves_sorted_keys_untouched(self):
        self.reg.set_ground('48,-44', 'ghost')
        before = self._cells_on_disk()
        with self.assertRaises(R.RegistryError):
            self.reg.set_trees('48,-44', True)
        self.assertEqual(self._cells_on_disk(), before)

    def test_writes_use_sorted_keys(self):
        self.reg.set_ground('49,-44', 'none')
        self.reg.set_ground('48,-44', 'ghost')
        raw = (self.root / 'registry' / 'cells.json').read_text()
        # "48,-44" sorts before "49,-44"
        self.assertLess(raw.index('"48,-44"'), raw.index('"49,-44"'))


class RegistryCliSmokeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.reg = R.RegistryStore(self.root)
        self.reg.set_ground('48,-44', 'generated')
        self.reg.register_feature('q001_x', name='X', cells=['48,-44'], status='planned')

    def tearDown(self):
        self.tmp.cleanup()

    def test_status_and_query(self):
        self.assertEqual(
            R.main(['--root', str(self.root), 'status', '--cell', '48,-44']), 0,
        )
        self.assertEqual(
            R.main(['--root', str(self.root), 'query', '--feature', 'q001_x']), 0,
        )

    def test_hold_set_clear(self):
        self.assertEqual(
            R.main(['--root', str(self.root), 'hold', 'set', '48,-44', '--reason', 'flood']),
            0,
        )
        self.assertEqual(self.reg.cell('48,-44')['hold'], 'flood')
        self.assertEqual(
            R.main(['--root', str(self.root), 'hold', 'clear', '48,-44']), 0,
        )
        self.assertIsNone(self.reg.cell('48,-44')['hold'])


if __name__ == '__main__':
    unittest.main()
