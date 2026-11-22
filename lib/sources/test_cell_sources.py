"""Unit tests for cell_sources helpers."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'shims'))

import cell_sources as cs  # noqa: E402
from ground_sources import cell_coverage  # noqa: E402


class TestCellSources(unittest.TestCase):
    """Path and parse helpers."""

    def test_parse_cell(self):
        self.assertEqual(cs.parse_cell('48,-44'), (48, -44))
        self.assertEqual(cs.parse_cell(' -1 , 2 '), (-1, 2))

    def test_sources_path(self):
        p = cs.sources_path(48, -44)
        self.assertEqual(p.name, '48_-44-SOURCES.txt')
        self.assertEqual(p.parent.name, 'cells')


class TestRegistrySummary(unittest.TestCase):
    def test_summary_reads_registry_status_and_holds(self):
        row = {'cell': (-1, 2), 'status': 'held', 'ground': 'generated',
               'trees': True, 'decor': False, 'hold': 'need survey', 'left': ['decor']}
        with mock.patch.object(cs, 'registry_rollup_rows', return_value=[row]):
            self.assertEqual(cs.registry_summary(-1, 2),
                             ['  status=held ground=generated trees=yes decor=no',
                              '  hold: need survey', '  left: decor'])
            self.assertEqual(cs.registry_summary(0, 0), ['  (cell not registered)'])

    def test_cross_cell_hints_need_no_parcels(self):
        lines = cs._cross_zone_lines(0, 0)
        self.assertTrue(any('neighbor cells:' in line for line in lines))
        self.assertFalse(any('unavailable' in line for line in lines))


class TestAttempts(unittest.TestCase):
    """Attempt ledger blocks blind repeats."""

    def setUp(self):
        self._orig = cs.CELLS
        self._tmp = tempfile.TemporaryDirectory()
        cs.CELLS = Path(self._tmp.name)

    def tearDown(self):
        cs.CELLS = self._orig
        self._tmp.cleanup()

    def test_repeat_needs_reason(self):
        cs.record_attempt(1, 2, 'rightmove', 'none', 'no exteriors')
        with self.assertRaises(SystemExit):
            cs.record_attempt(1, 2, 'rightmove', 'none', 'again')
        row = cs.record_attempt(1, 2, 'rightmove', 'found', 'new postcode', 'different postcode')
        self.assertEqual(row['retry_reason'], 'different postcode')
        self.assertEqual(len(cs.load_attempts(1, 2)), 2)

    def test_status_tried(self):
        cs.record_attempt(1, 2, 'google-streetview', 'none', 'no coverage')
        self.assertEqual(cs.source_status(1, 2)['google-streetview']['state'], 'tried')

    def test_legacy_streetview_ledger_counts(self):
        # Old attempt rows used family key ``streetview``.
        path = cs.attempts_path(1, 2)
        path.write_text(
            '{"date":"2026-10-01","source":"streetview","result":"none","note":"old"}\n'
        )
        self.assertEqual(cs.source_status(1, 2)['google-streetview']['state'], 'tried')


class TestStaleRefresh(unittest.TestCase):
    """HAVE/TRIED older than STALE_DAYS reopen without --retry-reason."""

    def setUp(self):
        self._orig = cs.CELLS
        self._tmp = tempfile.TemporaryDirectory()
        cs.CELLS = Path(self._tmp.name)
        self.today = date(2026, 10, 18)
        self.fresh = (self.today - timedelta(days=cs.STALE_DAYS - 1)).isoformat()
        self.old = (self.today - timedelta(days=cs.STALE_DAYS)).isoformat()

    def tearDown(self):
        cs.CELLS = self._orig
        self._tmp.cleanup()

    def test_is_stale_threshold(self):
        self.assertFalse(cs.is_stale(self.fresh, today=self.today))
        self.assertTrue(cs.is_stale(self.old, today=self.today))
        self.assertFalse(cs.is_stale(None, today=self.today))
        self.assertFalse(cs.is_stale('not-a-date', today=self.today))

    def test_tried_becomes_stale(self):
        path = cs.attempts_path(1, 2)
        path.write_text(
            json.dumps(
                {
                    'date': self.old,
                    'source': 'rightmove',
                    'result': 'none',
                    'note': 'no exteriors',
                }
            )
            + '\n'
        )
        st = cs.source_status(1, 2, today=self.today)['rightmove']
        self.assertEqual(st['state'], 'stale')
        self.assertEqual(st['was'], 'tried')
        self.assertEqual(st['as_of'], self.old)

    def test_fresh_tried_stays_tried(self):
        path = cs.attempts_path(1, 2)
        path.write_text(
            json.dumps(
                {
                    'date': self.fresh,
                    'source': 'rightmove',
                    'result': 'none',
                    'note': 'no exteriors',
                }
            )
            + '\n'
        )
        self.assertEqual(
            cs.source_status(1, 2, today=self.today)['rightmove']['state'],
            'tried',
        )

    def test_stale_attempt_allows_record_without_reason(self):
        path = cs.attempts_path(1, 2)
        path.write_text(
            json.dumps(
                {
                    'date': self.old,
                    'source': 'rightmove',
                    'result': 'none',
                    'note': 'no exteriors',
                }
            )
            + '\n'
        )
        row = cs.record_attempt(
            1, 2, 'rightmove', 'found', 'new listing', today=self.today,
        )
        self.assertIn('stale', row['retry_reason'])
        self.assertEqual(row['date'], self.today.isoformat())
        self.assertEqual(len(cs.load_attempts(1, 2)), 2)

    def test_manifest_have_stale_from_added(self):
        hit = {
            'id': 'aerial-old',
            'source': 'hampshire-hub',
            'kind': 'aerial',
            'added': self.old,
            'bbox_mc': [128, 256, 255, 383],  # covers cell 1,2 fully
            'useful': 'yes',
        }
        family = {'kind': 'aerial', 'tags': ['hampshire-hub'], 'how': 'inspect fixture'}
        with mock.patch.dict(cs.SOURCE_FAMILIES, {'hampshire-hub': family}), \
                mock.patch('cell_sources.references_for_cell', return_value=[hit]):
            with mock.patch('cell_sources.cell_coverage', return_value=1.0):
                st = cs.source_status(1, 2, today=self.today)
        hub = st['hampshire-hub']
        self.assertEqual(hub['state'], 'stale')
        self.assertEqual(hub['was'], 'have')

    def test_checklist_marks_stale(self):
        path = cs.attempts_path(1, 2)
        path.write_text(
            json.dumps(
                {
                    'date': self.old,
                    'source': 'rightmove',
                    'result': 'none',
                    'note': 'gone',
                }
            )
            + '\n'
        )
        text = '\n'.join(cs.source_checklist(1, 2, today=self.today))
        self.assertIn('[STALE]', text)
        self.assertIn('rightmove', text)


class TestProviderAliases(unittest.TestCase):
    """System catalogue aliases resolve legacy DEFRA product ids to ukdefra-lidar."""

    def test_ukdefra_dtm_dsm_alias_to_lidar(self):
        data = cs.load_place_sources()
        self.assertEqual(cs.resolve_source_id('ukdefra-dtm', data), 'ukdefra-lidar')
        self.assertEqual(cs.resolve_source_id('ukdefra-dsm', data), 'ukdefra-lidar')
        self.assertEqual(cs.resolve_source_id('ukdefra-lidar', data), 'ukdefra-lidar')
        row = cs.provider_by_id('ukdefra-dtm', data)
        self.assertIsNotNone(row)
        self.assertEqual(row['id'], 'ukdefra-lidar')
        self.assertEqual(cs.provider_by_id('ukdefra-dsm', data), row)
        ids = [p['id'] for p in data.get('providers') or []]
        self.assertIn('ukdefra-lidar', ids)
        self.assertNotIn('ukdefra-dtm', ids)
        self.assertNotIn('ukdefra-dsm', ids)

    def test_google_providers_stay_separate(self):
        data = cs.load_place_sources()
        ids = {p['id'] for p in data.get('providers') or []}
        self.assertIn('google-satellite', ids)
        self.assertIn('google-streetview', ids)
        self.assertEqual(cs.resolve_source_id('google', data), 'google-satellite')
        self.assertEqual(cs.resolve_source_id('google-streetview', data), 'google-streetview')
        sat = cs.provider_by_id('google-satellite', data)
        sv = cs.provider_by_id('google-streetview', data)
        self.assertIsNotNone(sat)
        self.assertIsNotNone(sv)
        self.assertNotEqual(sat['id'], sv['id'])


class TestGoogleFamily(unittest.TestCase):
    """google-satellite aerial is looked at for every cell, as often as needed."""

    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self._old = cs.CELLS
        cs.CELLS = Path(self._d.name)

    def tearDown(self):
        cs.CELLS = self._old
        self._d.cleanup()

    def test_todo_until_looked_at(self):
        self.assertEqual(cs.source_status(1, 2)['google-satellite']['state'], 'todo')
        cs.record_attempt(1, 2, 'google', 'found', 'flat roof, 4 rows of windows')
        self.assertEqual(cs.source_status(1, 2)['google-satellite']['state'], 'have')
        self.assertEqual(cs.load_attempts(1, 2)[0]['source'], 'google-satellite')

    def test_can_look_again_without_a_retry_reason(self):
        cs.record_attempt(1, 2, 'google-satellite', 'found', 'whole cell')
        cs.record_attempt(1, 2, 'google-satellite', 'found', 'second building')
        self.assertEqual(len(cs.load_attempts(1, 2)), 2)

    def test_legacy_google_ledger_counts(self):
        path = cs.attempts_path(1, 2)
        path.write_text(
            '{"date":"2026-10-16","source":"google","result":"found","note":"old key"}\n'
        )
        self.assertEqual(cs.source_status(1, 2)['google-satellite']['state'], 'have')


class TestDualRootPackage(unittest.TestCase):
    """Catalogue package fields resolve under system and world provider roots."""

    def test_merged_rows_annotate_home(self):
        data = cs.load_place_sources()
        lidar = cs.provider_by_id('ukdefra-lidar', data)
        self.assertIsNotNone(lidar)
        self.assertEqual(lidar['home'], 'system')
        self.assertEqual(cs.provider_home('ukdefra-lidar', data), 'system')
        self.assertEqual(cs.provider_home('ukdefra-dtm', data), 'system')

    def test_system_package_dir(self):
        from script_env import TOOL_ROOT, resolve_provider_package

        path = cs.provider_package_dir('ukdefra-lidar')
        self.assertIsNotNone(path)
        self.assertTrue(path.is_dir())
        self.assertEqual(path, (TOOL_ROOT / 'providers' / 'ukdefra-lidar').resolve())
        self.assertEqual(
            resolve_provider_package('providers/ukdefra-lidar', home='system'),
            path,
        )

    def test_world_package_dir(self):
        from script_env import resolve_provider_package

        with tempfile.TemporaryDirectory() as tmp:
            world = Path(tmp).resolve()
            package = world / 'providers' / 'local-demo'
            package.mkdir(parents=True)
            local = world / 'local-providers.json'
            local.write_text(json.dumps({'providers': [
                {'id': 'local-demo', 'package': 'providers/local-demo'},
            ]}))
            with mock.patch.object(cs, 'LOCAL_PROVIDERS', local), \
                    mock.patch('script_env.ROOT', world):
                data = cs.load_place_sources()
                self.assertEqual(cs.provider_home('local-demo', data), 'world')
                self.assertEqual(cs.provider_package_dir('local-demo', data), package)
                self.assertEqual(
                    resolve_provider_package('providers/local-demo', home='world'),
                    package,
                )

    def test_no_package_returns_none(self):
        self.assertIsNone(cs.provider_package_dir('rightmove'))
        self.assertEqual(cs.provider_home('rightmove'), 'system')

    def test_resolve_provider_package_search_order(self):
        import script_env as se

        with tempfile.TemporaryDirectory() as tmp:
            tool_root = Path(tmp) / 'tool'
            world_root = Path(tmp) / 'world'
            (tool_root / 'providers' / 'demo').mkdir(parents=True)
            (world_root / 'providers' / 'demo').mkdir(parents=True)
            old_tool, old_root = se.TOOL_ROOT, se.ROOT
            try:
                se.TOOL_ROOT = tool_root
                se.ROOT = world_root
                # home=None prefers system when both exist
                self.assertEqual(
                    se.resolve_provider_package('providers/demo', home=None),
                    (tool_root / 'providers' / 'demo').resolve(),
                )
                self.assertEqual(
                    se.resolve_provider_package('providers/demo', home='world'),
                    (world_root / 'providers' / 'demo').resolve(),
                )
            finally:
                se.TOOL_ROOT = old_tool
                se.ROOT = old_root

    def test_resolve_rejects_traversal(self):
        from script_env import resolve_provider_package

        with self.assertRaises(ValueError):
            resolve_provider_package('../etc', home='system')


class TestCellCoverage(unittest.TestCase):
    """An aerial counts for a cell only if it shows most of it, not just a clipped edge."""

    def test_clipped_edge_is_not_coverage(self):
        # B35 mosaic reaches z-5346; cell 49,-42 spans z-5376..-5249 (30 of 128 rows)
        b35 = {'bbox_mc': [6246, -5637, 6538, -5346]}
        self.assertAlmostEqual(cell_coverage(b35, 49, -42), 31 / 128, places=3)
        self.assertLess(cell_coverage(b35, 49, -42), cs.MIN_AERIAL_COVERAGE)

    def test_mosaic_over_the_cell_is_full_coverage(self):
        welbeck = {'bbox_mc': [6151, -5444, 6443, -5153]}
        self.assertEqual(cell_coverage(welbeck, 49, -42), 1.0)

    def test_no_bbox_or_disjoint_is_zero(self):
        self.assertEqual(cell_coverage({}, 49, -42), 0.0)
        self.assertEqual(cell_coverage({'bbox_mc': [0, 0, 10, 10]}, 49, -42), 0.0)


if __name__ == '__main__':
    unittest.main()
