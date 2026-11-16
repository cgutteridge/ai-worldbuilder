#!/usr/bin/env python3
"""Tests for the type-facing lidar / vector-map seams (no live tiles required)."""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / 'shims', _ROOT / 'scripts'):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

import lidar_provider as lp  # noqa: E402


class ResolveLidarRow(unittest.TestCase):
    """Active catalogue row is chosen by type ``lidar``, not a hard-coded id."""

    def test_picks_provider_that_lists_lidar_type(self):
        data = {
            'providers': [
                {'id': 'other', 'types': ['aerial'], 'package': 'providers/other'},
                {
                    'id': 'national-lidar',
                    'types': ['lidar'],
                    'package': 'providers/national-lidar',
                    'modules': {
                        'world': 'manage/world',
                        'tiles': 'get/tiles',
                    },
                },
            ],
        }
        row = lp.lidar_provider_row(data)
        self.assertEqual(row['id'], 'national-lidar')
        self.assertEqual(row['modules']['world'], 'manage/world')

    def test_missing_lidar_provider_is_error(self):
        with self.assertRaises(lp.NoLidarProviderError):
            lp.lidar_provider_row({'providers': [{'id': 'x', 'types': ['aerial']}]})

    def test_world_lidar_beats_system_and_preserves_order_within_home(self):
        rows = [
            {'id': 'system', 'home': 'system', 'types': ['lidar']},
            {'id': 'world-aerial', 'home': 'world', 'types': ['aerial']},
            {'id': 'world-first', 'home': 'world', 'types': ['lidar']},
            {'id': 'world-second', 'home': 'world', 'types': ['lidar']},
        ]
        self.assertIs(lp.lidar_provider_row({'providers': rows}), rows[2])
        self.assertIs(lp.lidar_provider_row({'providers': rows[:2]}), rows[0])

    def test_explicit_empty_catalogue_does_not_load_system(self):
        with mock.patch.object(lp, 'load_place_sources', side_effect=AssertionError):
            with self.assertRaises(lp.NoLidarProviderError):
                lp.lidar_provider_row({})

    def test_missing_entrypoint_is_configuration_error_not_missing_tiles(self):
        with self.assertRaisesRegex(ValueError, 'modules.tiles'):
            lp.load_lidar_module('tiles', {'providers': [{'id': 'broken', 'types': ['lidar']}]})


class LiveCatalogue(unittest.TestCase):
    """Real system catalogue wiring (no mocks)."""

    def test_live_row_exposes_world_and_tiles_modules(self):
        row = lp.lidar_provider_row()
        self.assertIn('lidar', row.get('types') or [])
        modules = row.get('modules') or {}
        self.assertIn('world', modules)
        self.assertIn('tiles', modules)

    def test_system_provider_exposes_documented_client_and_review_entrypoints(self):
        client = lp.tile_client(download=False)
        self.assertIsInstance(client.crs, str)
        for method in ('missing', 'ensure', 'sample', 'tile_status'):
            self.assertTrue(callable(getattr(client, method)))
        for role in ('query', 'extremes'):
            self.assertTrue(callable(lp.load_lidar_module(role).main))

    def test_live_tile_client_shares_shim_module(self):
        import defra_lidar as shim_tiles
        client_mod = lp.load_lidar_module('tiles')
        self.assertIs(client_mod, shim_tiles)


class LoadEntries(unittest.TestCase):
    """Entry modules load from the catalogue package path."""

    def test_default_heights_ensures_cached_provider_tiles_before_sampling(self):
        client = mock.Mock()
        with mock.patch.object(lp, 'tile_client', return_value=client) as make, \
             mock.patch.object(lp, 'mc_box_en', return_value=(1, 2, 3, 4)) as box, \
             mock.patch.object(lp, 'mc_heights', return_value={'ground_y': 5}) as sample:
            self.assertEqual(lp.default_heights(10, 20, 30, 40), {'ground_y': 5})
        make.assert_called_once_with(download=False)
        box.assert_called_once_with(10, 20, 30, 40)
        client.ensure.assert_called_once_with(1, 2, 3, 4)
        sample.assert_called_once_with(client, 10, 20, 30, 40)

    def test_composite_world_factory_uses_loaded_world_class(self):
        class FakeWorld:
            pass

        fake_mod = types.ModuleType('fake_world')
        fake_mod.LidarWorld = FakeWorld

        with mock.patch.object(lp, 'load_lidar_module', return_value=fake_mod) as load:
            world = lp.composite_world()
        self.assertIsInstance(world, FakeWorld)
        load.assert_called_once_with('world')

    def test_tile_client_constructs_client_from_tiles_module(self):
        class FakeClient:
            def __init__(self, *args, download=True, **kwargs):
                self.download = download

        fake_mod = types.ModuleType('fake_tiles')
        fake_mod.LidarTiles = FakeClient

        with mock.patch.object(lp, 'load_lidar_module', return_value=fake_mod):
            client = lp.tile_client(download=False)
        self.assertIsInstance(client, FakeClient)
        self.assertFalse(client.download)

    def test_tile_helpers_reexported_from_tiles_module(self):
        fake_mod = types.ModuleType('fake_tiles')
        fake_mod.mc_box_en = lambda *a: (1.0, 2.0, 3.0, 4.0)
        fake_mod.mc_heights = lambda *a, **k: {'feature': None}
        with mock.patch.object(lp, 'load_lidar_module', return_value=fake_mod):
            self.assertEqual(lp.mc_box_en(0, 0, 1, 1), (1.0, 2.0, 3.0, 4.0))
            self.assertEqual(lp.mc_heights(None, 0, 0, 1, 1)['feature'], None)

    def test_mc_heights_float_asks_for_the_float_option(self):
        seen = {}

        def fake(*a, geocraft=True, **k):
            seen['geocraft'] = geocraft
            return {'dsm': 'float-grid'}

        fake_mod = types.ModuleType('fake_tiles')
        fake_mod.mc_heights = fake
        with mock.patch.object(lp, 'load_lidar_module', return_value=fake_mod):
            self.assertEqual(lp.mc_heights_float(None, 0, 0, 1, 1)['dsm'], 'float-grid')
        self.assertIs(seen['geocraft'], False)

    def test_mc_heights_float_falls_back_when_provider_has_no_float_option(self):
        fake_mod = types.ModuleType('fake_tiles')
        fake_mod.mc_heights = lambda client, x0, z0, x1, z1: {'dsm': 'int-grid'}
        with mock.patch.object(lp, 'load_lidar_module', return_value=fake_mod):
            self.assertEqual(lp.mc_heights_float(None, 0, 0, 1, 1)['dsm'], 'int-grid')


if __name__ == '__main__':
    unittest.main()
