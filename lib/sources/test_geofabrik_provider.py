"""Regional providers use published coverage and the same-world clip contract."""
from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
import contextlib
import io
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


class PerLayerExtractionTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.package = gp.scaffold(self.root, 'test-region', (1, 1, 3, 3), index())
        self.provider = runpy.run_path(str(self.package / 'osm_extract.py'))
        self.pbf = self.root / 'source.pbf'
        self.pbf.touch()
        self.output = self.root / 'reference/vector_map/survey'
        self.stack.enter_context(mock.patch.object(gp.shutil, 'which', return_value='/bin/ogr2ogr'))
        self.stack.enter_context(mock.patch.object(gp, 'place_clip_dir', return_value=self.output))
        self.run = self.stack.enter_context(mock.patch.object(gp.subprocess, 'run', side_effect=self.write_extract))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))

    @staticmethod
    def write_extract(command, **kwargs):
        Path(command[-3]).write_text('{"type":"FeatureCollection","features":[]}')
        return subprocess.CompletedProcess(command, 0, '', '')

    def invoke(self, *args):
        return self.provider['main'](['--place', 'survey', '--bbox-wgs84', '1,1,3,3',
                                     '--pbf', str(self.pbf), *args])

    def test_scaffolded_entry_point_uses_adjacent_schema_and_shared_furniture_queries(self):
        self.assertEqual(self.invoke('--layers', 'furniture'), 0)
        command = self.run.call_args.args[0]
        self.assertEqual(command[command.index('OSM_CONFIG_FILE') + 1],
                         str(self.package / 'osmconf.ini'))
        self.assertIn('amenity', command[command.index('-where') + 1])
        self.assertIn('-clipsrc', command)
        self.assertNotIn('COORDINATE_PRECISION=7', command)
        self.assertTrue((self.output / 'furniture.geojson').is_file())
        self.assertFalse((self.root / 'reference/manifest.jsonl').exists())

    def test_schema_falls_back_to_tool_when_provider_copy_absent(self):
        (self.package / 'osmconf.ini').unlink()
        self.assertEqual(self.invoke('--layers', 'buildings'), 0)
        command = self.run.call_args.args[0]
        self.assertEqual(command[command.index('OSM_CONFIG_FILE') + 1], str(gp.OSM_CONFIG))

    def test_failed_query_preserves_existing_layer_and_reports_gdal_error(self):
        self.output.mkdir(parents=True)
        path = self.output / 'furniture.geojson'
        path.write_bytes(b'previous-good-evidence')
        self.run.side_effect = subprocess.CalledProcessError(1, 'ogr2ogr', stderr='missing amenity')
        error = io.StringIO()
        with contextlib.redirect_stderr(error), self.assertRaises(SystemExit) as caught:
            self.invoke('--layers', 'furniture')
        self.assertEqual(caught.exception.code, 2)
        self.assertIn('missing amenity', error.getvalue())
        self.assertEqual(path.read_bytes(), b'previous-good-evidence')
        self.assertEqual(list(self.output.iterdir()), [path])

    def test_existing_custom_query_interface_and_per_layer_names(self):
        self.assertEqual(self.invoke('--gdal-layer', 'points', '--where', "amenity = 'bench'"), 0)
        self.assertEqual([p.name for p in self.output.iterdir()], ['points.geojson'])
        command = self.run.call_args.args[0]
        self.assertEqual(command[command.index('-where') + 1], "amenity = 'bench'")
        self.assertEqual(command[-1], 'points')

    def test_default_layers_and_radius_remain_the_modern_defaults(self):
        with mock.patch('world_frame.mc_to_wgs84', side_effect=lambda x, z: (z / 1000, x / 1000)):
            self.assertEqual(self.provider['main'](['--place', 'survey', '--at', '10,20',
                                                   '--pbf', str(self.pbf)]), 0)
        command = self.run.call_args_list[0].args[0]
        offset = command.index('-spat')
        self.assertEqual(command[offset + 1:offset + 5], ['-0.118', '-0.108', '0.138', '0.148'])
        names = {p.name for p in self.output.iterdir()}
        self.assertIn('buildings.geojson', names)
        self.assertIn('furniture.geojson', names)
        self.assertIn('trees-0.geojson', names)
        self.assertIn('trees-1.geojson', names)


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
