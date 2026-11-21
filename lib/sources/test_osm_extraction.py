"""Combined extraction compatibility through the real CLI, with synthetic data."""
from __future__ import annotations

import contextlib
import io
import json
import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import active_world
import cell_sources
import osm_extraction as extraction


PROVIDER = {'source': 'test-region', 'pbf_filename': 'test-region.osm.pbf',
            'pbf_url': 'https://example.test/region.osm.pbf', 'metadata_key': 'legacy_metadata'}


class CombinedExtractionTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.pbf = self.root / 'downloads' / PROVIDER['pbf_filename']
        self.pbf.parent.mkdir()
        self.pbf.touch()
        self.stack.enter_context(mock.patch.object(active_world, 'WORLD_ROOT', self.root))
        self.stack.enter_context(mock.patch.object(extraction, 'to_osgb', side_effect=lambda x, z: (x, z)))
        self.stack.enter_context(mock.patch.object(extraction, 'osgb_to_wgs84',
                                                   side_effect=lambda e, n: (n / 1000, e / 1000)))
        self.run = self.stack.enter_context(mock.patch.object(extraction.subprocess, 'run',
                                                             side_effect=self.write_extract))
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.stdout))
        self.stack.enter_context(contextlib.redirect_stderr(self.stderr))

    @staticmethod
    def write_extract(command, **kwargs):
        """A real query produces one synthetic feature; command[-3] is its staging file."""
        Path(command[-3]).write_text(json.dumps({'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'properties': {'osm_id': '123'},
             'geometry': {'type': 'Point', 'coordinates': [0, 0]}}]}))
        return subprocess.CompletedProcess(command, 0, '', '')

    def invoke(self, *args):
        return extraction.combined_extractor_main(PROVIDER, list(args))

    def output(self, filename, place='survey'):
        return self.root / 'reference' / 'vector_map' / place / filename

    def test_furniture_always_uses_explicit_schema_and_world_override(self):
        schema = self.pbf.parent / 'osmconf.ini'
        for override in (False, True):
            with self.subTest(override=override):
                if override:
                    schema.write_text('[points]\nattributes=amenity,highway\n')
                self.assertEqual(self.invoke('--at', '1,1', '--cell', '--place', 'survey',
                                             '--layers', 'furniture', '--force'), 0)
                command = self.run.call_args.args[0]
                offset = command.index('OSM_CONFIG_FILE')
                self.assertEqual(command[offset - 1], '--config')
                self.assertEqual(command[offset + 1], str(schema if override else extraction.OSM_CONFIG))
                self.assertIn('amenity', command[command.index('-where') + 1])
                self.assertEqual(command[-1], 'points')
                self.assertNotIn('-clipsrc', command)
                self.assertIn('COORDINATE_PRECISION=7', command)

    def test_inclusive_negative_cell_combines_layers_and_preserves_metadata(self):
        self.assertEqual(self.invoke('--at=-1,-1', '--cell', '--place', 'Survey',
                                     '--layers', 'amenity,shops'), 0)
        path = self.output('amenity+shops-cell--128--128.geojson')
        doc = json.loads(path.read_text())
        self.assertEqual(doc['name'], path.stem)
        self.assertEqual(doc['legacy_metadata'], {
            'source': PROVIDER['source'], 'pbf': 'downloads/test-region.osm.pbf',
            'pbf_url': PROVIDER['pbf_url'], 'bbox_mc': [-128, -128, -1, -1],
            'spat_wgs84': [-0.128, -0.128, -0.001, -0.001],
            'layers': ['amenity', 'shops'], 'place': 'Survey'})
        self.assertEqual([f['properties']['osm_layer'] for f in doc['features']],
                         ['amenity', 'amenity', 'shops', 'shops'])
        self.assertEqual(self.run.call_count, 4)
        self.assertFalse((self.root / 'reference/manifest.jsonl').exists())

    def test_add_registers_real_manifest_and_refuses_duplicates(self):
        args = ('--bbox-mc', '0,0,127,127', '--place', 'survey', '--layers', 'furniture',
                '--add', '--models', 'context,landscape', '--useful', 'maybe',
                '--not-for', 'roof,private_interior', '--notes', 'Surveyed furniture')
        self.assertEqual(self.invoke(*args), 0)
        manifest = self.root / 'reference/manifest.jsonl'
        before = manifest.read_bytes()
        row = json.loads(before)
        self.assertEqual(row['path'], 'vector_map/survey/furniture-bbox-0-0-127-127.geojson')
        self.assertEqual(row['source'], PROVIDER['source'])
        self.assertEqual(row['url'], PROVIDER['pbf_url'])
        self.assertEqual(row['bbox_mc'], [0, 0, 127, 127])
        self.assertEqual(row['mc_tiles'], ['128:0:0'])
        self.assertEqual(row['models'], ['context', 'landscape'])
        self.assertEqual(row['not_for'], ['roof', 'private_interior'])
        self.assertEqual(row['useful'], 'maybe')
        self.assertEqual(row['notes'], 'Surveyed furniture')
        self.assertEqual(self.invoke(*args, '--force'), 1)
        self.assertEqual(manifest.read_bytes(), before)

    def test_existing_output_needs_force_and_failure_preserves_it(self):
        args = ('--at', '1,1', '--cell', '--place', 'survey', '--layers', 'buildings')
        path = self.output('buildings-cell-0-0.geojson')
        path.parent.mkdir(parents=True)
        path.write_bytes(b'previous-good-evidence')
        self.assertEqual(self.invoke(*args), 1)
        self.run.assert_not_called()
        self.run.side_effect = subprocess.CalledProcessError(1, 'ogr2ogr', stderr='no amenity column')
        self.assertEqual(self.invoke(*args, '--force'), 2)
        self.assertEqual(path.read_bytes(), b'previous-good-evidence')
        self.assertIn('no amenity column', self.stderr.getvalue())
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_failed_later_query_preserves_whole_combined_file(self):
        args = ('--at', '1,1', '--cell', '--place', 'survey', '--layers', 'amenity', '--force')
        path = self.output('amenity-cell-0-0.geojson')
        path.parent.mkdir(parents=True)
        path.write_bytes(b'previous-good-evidence')
        calls = 0

        def fail_second(command, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise subprocess.CalledProcessError(1, command, stderr='second query failed')
            return self.write_extract(command, **kwargs)

        self.run.side_effect = fail_second
        self.assertEqual(self.invoke(*args), 2)
        self.assertEqual(calls, 2)
        self.assertEqual(path.read_bytes(), b'previous-good-evidence')

    def test_malformed_geojson_does_not_replace_good_evidence(self):
        path = self.output('buildings-cell-0-0.geojson')
        path.parent.mkdir(parents=True)
        path.write_bytes(b'previous-good-evidence')
        self.run.side_effect = lambda command, **kw: Path(command[-3]).write_text('broken JSON')
        self.assertEqual(self.invoke('--at', '1,1', '--cell', '--place', 'survey', '--force'), 2)
        self.assertEqual(path.read_bytes(), b'previous-good-evidence')

    def test_dry_run_has_default_radius_and_makes_no_outputs(self):
        self.assertEqual(self.invoke('--at', '10,20', '--place', 'survey', '--dry-run'), 0)
        self.run.assert_not_called()
        self.assertFalse((self.root / 'reference').exists())
        self.assertIn('buildings-r200m-at-10-20.geojson', self.stdout.getvalue())
        self.assertIn('bbox_mc -190,-180 → 210,220', self.stdout.getvalue())

    def test_prepare_dir_interface_keeps_legacy_tree_and_output_name(self):
        self.assertEqual(self.invoke('--at', '128,256', '--cell', '--layers', 'buildings,highways',
                                     '--pbf', str(self.pbf), '--dir', 'osm/prepared-1-2', '--force'), 0)
        path = self.root / 'reference/osm/prepared-1-2/buildings+highways-cell-128-256.geojson'
        self.assertTrue(path.is_file())
        self.assertEqual(json.loads(path.read_text())['legacy_metadata']['place'], '')

    def test_osgb_and_custom_radius_preserve_centre_and_name(self):
        with mock.patch.object(extraction, 'to_mc', return_value=(10, 20)) as convert:
            self.assertEqual(self.invoke('--osgb', '100,200', '--radius-m', '12', '--place', 'survey'), 0)
        convert.assert_called_once_with(100.0, 200.0)
        self.assertTrue(self.output('buildings-r12m-at-10-20.geojson').is_file())

    def test_wgs_bbox_conversion_keeps_inclusive_normalised_box(self):
        with mock.patch.object(extraction.ref_manifest, 'wgs84_to_osgb',
                               side_effect=lambda lat, lon: (int(lon * 1000), int(lat * 1000))), \
                mock.patch.object(extraction, 'to_mc', side_effect=lambda e, n: (e, n)):
            self.assertEqual(self.invoke('--bbox-wgs', '0.01,0.02,0.03,0.04', '--place', 'survey'), 0)
        path = self.output('buildings-bbox-10-20-30-40.geojson')
        self.assertEqual(json.loads(path.read_text())['legacy_metadata']['bbox_mc'], [10, 20, 30, 40])

    def test_custom_gdal_query_and_external_pbf(self):
        pbf = self.root / 'custom.pbf'
        pbf.touch()
        self.assertEqual(self.invoke('--bbox-mc', '30,40,10,20', '--place', 'survey',
                                     '--pbf', str(pbf), '--gdal-layer', 'points',
                                     '--where', "amenity = 'bench'", '--add'), 0)
        command = self.run.call_args.args[0]
        self.assertEqual(command[-2:], [str(pbf), 'points'])
        self.assertEqual(command[command.index('-where') + 1], "amenity = 'bench'")
        row = json.loads((self.root / 'reference/manifest.jsonl').read_text())
        self.assertEqual(row['models'], ['context'])

    def test_missing_download_never_runs_gdal(self):
        self.pbf.unlink()
        self.assertEqual(self.invoke('--at', '1,1', '--place', 'survey'), 2)
        self.run.assert_not_called()
        self.assertFalse((self.root / 'reference').exists())
        self.assertIn(PROVIDER['pbf_url'], self.stderr.getvalue())

    def test_real_shim_calls_synthetic_combined_provider_with_full_default_set(self):
        package = self.root / 'providers/test-map'
        package.mkdir(parents=True)
        (package / 'osm_extract.py').write_text(
            'from script_env import ROOT\n'
            'from osm_extraction import LAYER_PRESETS, combined_extractor_main\n'
            f'CONFIG = {PROVIDER!r}\n'
            "DEFAULT_PBF = ROOT / 'downloads' / CONFIG['pbf_filename']\n"
            'def main(argv=None): return combined_extractor_main(CONFIG, argv)\n')
        rows = {'providers': [{'id': 'test-map', 'types': ['vector_map'], 'package': 'providers/test-map'}]}
        with mock.patch.object(cell_sources, 'load_place_sources', return_value=rows), \
                mock.patch.object(cell_sources, 'provider_package_dir', return_value=package):
            shim = runpy.run_path(str(extraction.TOOL_ROOT / 'shims/osm_extract.py'))
        self.assertEqual(Path(shim['DEFAULT_PBF']), self.pbf)
        self.assertEqual(shim['main'](['--at', '1,1', '--cell', '--dir', 'prepared', '--force']), 0)
        wheres = [call.args[0][call.args[0].index('-where') + 1] for call in self.run.call_args_list]
        self.assertTrue(any('amenity' in where and 'bench' in where for where in wheres))
        outputs = list((self.root / 'reference/vector_map/prepared').glob('*.geojson'))
        self.assertEqual(len(outputs), 1)
        doc = json.loads(outputs[0].read_text())
        self.assertIn('furniture', doc['legacy_metadata']['layers'])
        self.assertIn('trees', doc['legacy_metadata']['layers'])
