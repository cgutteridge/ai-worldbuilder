"""LocalPreparation resolves the regional PBF and calls the current extractor CLI."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cellmath
import cell_preparation as prep
import geofabrik_provider as gp
import vector_map_clips as vmc


RECTANGLE = {
    'type': 'Polygon',
    'coordinates': [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
}


def _index():
    return {
        'features': [{
            'properties': {
                'id': 'test-region',
                'urls': {'pbf': 'https://download.geofabrik.de/europe/test-region-latest.osm.pbf'},
            },
            'geometry': RECTANGLE,
        }],
    }


class PreparedPlaceTests(unittest.TestCase):
    def test_place_slug_matches_legacy_prepared_folder_name(self):
        self.assertEqual(prep.prepared_place_slug((48, -44)), 'prepared-48--44')
        self.assertEqual(prep.prepared_place_slug((0, 0)), 'prepared-0-0')


class LocalPbfResolveTests(unittest.TestCase):
    def test_resolves_pbf_from_provider_json_without_default_pbf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = gp.scaffold(root, 'test-region', (1, 1, 3, 3), _index())
            expected = vmc.default_pbf_path('test-region-latest.osm.pbf', root)
            rows = {
                'providers': [{
                    'id': 'geofabrik-test-region',
                    'types': ['vector_map'],
                    'package': 'providers/geofabrik-test-region',
                }],
            }
            with mock.patch('cell_sources.load_place_sources', return_value=rows), \
                    mock.patch('cell_sources.provider_package_dir', return_value=package):
                self.assertEqual(prep.local_vector_map_pbf(root), expected)
            self.assertTrue((package / 'provider.json').is_file())
            self.assertNotIn('DEFAULT_PBF', (package / 'osm_extract.py').read_text())

    def test_falls_back_to_legacy_default_pbf_when_provider_json_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / 'providers' / 'legacy-map'
            package.mkdir(parents=True)
            (package / 'osm_extract.py').write_text('LAYER_PRESETS = {}\ndef main(argv=None): return 0\n')
            legacy = root / 'downloads' / 'legacy.osm.pbf'
            rows = {
                'providers': [{
                    'id': 'legacy-map',
                    'types': ['vector_map'],
                    'package': 'providers/legacy-map',
                }],
            }
            fake = mock.Mock(DEFAULT_PBF=legacy, PROVIDER=None, CONFIG=None)
            with mock.patch('cell_sources.load_place_sources', return_value=rows), \
                    mock.patch('cell_sources.provider_package_dir', return_value=package), \
                    mock.patch.dict('sys.modules', {'osm_extract': fake}):
                self.assertEqual(prep.local_vector_map_pbf(root), legacy)


class LocalPreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.package = gp.scaffold(self.root, 'test-region', (1, 1, 3, 3), _index())
        self.pbf = vmc.default_pbf_path('test-region-latest.osm.pbf', self.root)
        self.pbf.parent.mkdir(parents=True, exist_ok=True)
        self.pbf.write_bytes(b'pbf')
        self.rows = {
            'providers': [{
                'id': 'geofabrik-test-region',
                'types': ['vector_map'],
                'package': 'providers/geofabrik-test-region',
            }],
        }
        self.repairs = prep.LocalPreparation(self.root)
        self.catalogue = mock.patch('cell_sources.load_place_sources', return_value=self.rows)
        self.package_dir = mock.patch(
            'cell_sources.provider_package_dir', return_value=self.package,
        )
        self.catalogue.start()
        self.package_dir.start()
        self.addCleanup(self.catalogue.stop)
        self.addCleanup(self.package_dir.stop)

    def test_preflight_reports_cached_provider_pbf_without_default_attribute(self):
        with mock.patch.object(prep.shutil, 'which', return_value='/bin/ogr2ogr'):
            report = self.repairs.preflight((0, 0), True)
        self.assertEqual(report['blockers'], [])
        self.assertEqual(report['inputs'][0]['filename'], str(self.pbf))
        self.assertTrue(report['inputs'][0]['cached'])
        self.assertEqual(report['inputs'][0]['bytes'], 3)

    def test_preflight_blocks_when_pbf_is_missing(self):
        self.pbf.unlink()
        with mock.patch.object(prep.shutil, 'which', return_value='/bin/ogr2ogr'):
            report = self.repairs.preflight((0, 0), True)
        self.assertTrue(any(b.startswith('local PBF required:') for b in report['blockers']))
        self.assertFalse(report['inputs'][0]['cached'])

    def test_extract_calls_current_place_cli_not_dir_or_force(self):
        cell = (1, -2)
        x0, z0, _x1, _z1 = cellmath.bounds(cell)
        fake = mock.Mock()
        fake.main.return_value = 0
        with mock.patch.object(prep.shutil, 'which', return_value='/bin/ogr2ogr'), \
                mock.patch.dict('sys.modules', {'osm_extract': fake}):
            self.repairs.extract(cell)
        fake.main.assert_called_once_with([
            '--at', f'{x0},{z0}', '--cell',
            '--pbf', str(self.pbf),
            '--place', 'prepared-1--2',
        ])
        argv = fake.main.call_args.args[0]
        self.assertNotIn('--dir', argv)
        self.assertNotIn('--force', argv)

    def test_extract_rejects_unknown_dir_force_style_without_crashing_on_default_pbf(self):
        """Regression: scaffolded providers export no DEFAULT_PBF attribute."""
        self.assertNotIn('DEFAULT_PBF', (self.package / 'osm_extract.py').read_text())
        meta = json.loads((self.package / 'provider.json').read_text())
        self.assertEqual(meta['pbf_filename'], 'test-region-latest.osm.pbf')
        fake = mock.Mock(spec=['main'])
        fake.main.return_value = 0
        with mock.patch.object(prep.shutil, 'which', return_value='/bin/ogr2ogr'), \
                mock.patch.dict('sys.modules', {'osm_extract': fake}):
            self.repairs.extract((0, 0))
        self.assertFalse(hasattr(fake, 'DEFAULT_PBF'))


if __name__ == '__main__':
    unittest.main()
