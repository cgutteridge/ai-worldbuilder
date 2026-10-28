"""Regional providers use published coverage and the same-world clip contract."""
from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cell_sources
import geofabrik_provider as gp


RECTANGLE = {'type': 'Polygon', 'coordinates': [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]}


def index(geometry=RECTANGLE):
    """Synthetic Geofabrik metadata; no world or network reads."""
    return {'features': [{'properties': {'id': 'test-region', 'urls': {
        'pbf': 'https://download.geofabrik.de/europe/test-region-latest.osm.pbf'}},
        'geometry': geometry}]}


class CoverageTests(unittest.TestCase):
    def test_checks_geometry_not_just_bounding_envelope(self):
        self.assertTrue(gp.covers_bbox(RECTANGLE, (1, 1, 9, 9)))
        self.assertFalse(gp.covers_bbox(RECTANGLE, (1, 1, 11, 9)))
        notched = {'type': 'Polygon', 'coordinates': [[
            [0, 0], [10, 0], [10, 10], [6, 10], [6, 4], [4, 4], [4, 10], [0, 10], [0, 0]]]}
        self.assertFalse(gp.covers_bbox(notched, (1, 1, 9, 9)))

    def test_holes_and_disjoint_components(self):
        holed = {'type': 'Polygon', 'coordinates': [RECTANGLE['coordinates'][0],
                 [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]]}
        self.assertFalse(gp.covers_bbox(holed, (1, 1, 9, 9)))
        self.assertFalse(gp.covers_bbox(holed, (4.5, 4.5, 5.5, 5.5)))
        self.assertTrue(gp.covers_bbox(holed, (1, 1, 3, 3)))
        multi = {'type': 'MultiPolygon', 'coordinates': [RECTANGLE['coordinates']]}
        self.assertTrue(gp.covers_bbox(multi, (1, 1, 3, 3)))

    def test_invalid_bbox(self):
        for value in ['1,2,3', 'nan,1,3,4', '3,1,2,4', '-181,1,2,4']:
            with self.assertRaises(ValueError):
                gp.parse_bbox(value)


class ScaffoldTests(unittest.TestCase):
    def test_scaffolds_schema_metadata_wrapper_and_preserves_catalogue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'config').mkdir()
            catalogue = root / 'config/local-providers.json'
            catalogue.write_text(json.dumps({'place': 'synthetic', 'providers': [
                {'id': 'local-photo', 'types': ['aerial']}], 'aliases': {'photo': 'local-photo'}}))
            package = gp.scaffold(root, 'test-region', (1, 1, 3, 3), index())
            data = json.loads(catalogue.read_text())
            self.assertEqual(data['aliases']['photo'], 'local-photo')
            self.assertEqual(data['providers'][-1]['package'], 'providers/geofabrik-test-region')
            self.assertEqual((package / 'osmconf.ini').read_text(), gp.OSM_CONFIG.read_text())
            provider = runpy.run_path(str(package / 'osm_extract.py'))
            self.assertIs(provider['LAYER_PRESETS'], gp.LAYER_PRESETS)
            self.assertFalse((root / 'downloads').exists())
            with self.assertRaises(ValueError):
                gp.scaffold(root, 'test-region', (1, 1, 3, 3), index())

    def test_coverage_failure_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, 'cover bbox'):
                gp.scaffold(root, 'test-region', (1, 1, 30, 30), index())
            self.assertEqual(list(root.iterdir()), [])

    def test_extract_uses_shared_schema_and_atomic_place_clips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = gp.scaffold(root, 'test-region', (1, 1, 3, 3), index())
            pbf = root / 'source.pbf'
            pbf.touch()
            output = root / 'reference/vector_map/survey'
            calls = []

            def run(command, **kwargs):
                calls.append(command)
                Path(command[-3]).write_text('{"type":"FeatureCollection","features":[]}')

            with mock.patch.object(gp.shutil, 'which', return_value='/bin/ogr2ogr'), \
                 mock.patch.object(gp, 'place_clip_dir', return_value=output), \
                 mock.patch.object(gp.subprocess, 'run', side_effect=run):
                result = gp.extractor_main(package / 'provider.json', ['--place', 'survey',
                    '--bbox-wgs84', '1,1,3,3', '--pbf', str(pbf), '--layers', 'buildings,trees'])
            self.assertEqual(result, 0)
            self.assertEqual(sorted(p.name for p in output.iterdir()), [
                'buildings.geojson', 'trees-0.geojson', 'trees-1.geojson'])
            self.assertIn(str(package / 'osmconf.ini'), calls[0])
            self.assertIn('-clipsrc', calls[0])


class ShimTests(unittest.TestCase):
    def test_resolves_arbitrary_catalogue_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp)
            (package / 'osm_extract.py').write_text('LAYER_PRESETS = {}\ndef main(argv=None): return 23\n')
            rows = {'providers': [{'id': 'different-region', 'types': ['vector_map'], 'package': 'providers/custom'}]}
            with mock.patch.object(cell_sources, 'load_place_sources', return_value=rows), \
                 mock.patch.object(cell_sources, 'provider_package_dir', return_value=package) as resolve:
                mod = runpy.run_path(str(gp.TOOL_ROOT / 'shims/osm_extract.py'))
            resolve.assert_called_once_with('different-region', rows)
            self.assertEqual(mod['main']([]), 23)
            self.assertIn('trees', mod['LAYER_PRESETS'])

    def test_missing_or_ambiguous_provider_has_actionable_error(self):
        row = {'id': 'r', 'types': ['vector_map'], 'package': 'providers/r'}
        for rows in ([], [row, row]):
            with mock.patch.object(cell_sources, 'load_place_sources', return_value={'providers': rows}):
                with self.assertRaisesRegex(ImportError, 'exactly one packaged vector_map'):
                    runpy.run_path(str(gp.TOOL_ROOT / 'shims/osm_extract.py'))

    def test_plain_checkout_imports_script_env_before_loading_provider(self):
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        code = ('import sys; sys.path.insert(0, ' + repr(str(gp.TOOL_ROOT / 'shims')) + '); '
                'import _provider_shim; import script_env; import world_frame')
        result = subprocess.run([sys.executable, '-c', code], cwd=tempfile.gettempdir(),
                                env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
