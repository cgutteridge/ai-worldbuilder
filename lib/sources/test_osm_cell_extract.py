"""Cell extract job list covers the full landscape set (#156)."""
from __future__ import annotations

import unittest
from pathlib import Path

import osm_cell_extract as oce
import street_furniture as sf


# Presets the planner layers already read; must appear in the standard cell list.
REQUIRED_PRESETS = (
    'water',
    'waterway',
    'barrier',
    'furniture',
    'pedestrian',
    'natural',
    'leisure',
    'landuse',
    'landclass',
    'trees',
)


class CellExtractJobList(unittest.TestCase):
    """Proof: the per-cell extract job list names every landscape preset."""

    def test_cell_extract_layers_name_every_landscape_preset(self):
        names = list(oce.CELL_EXTRACT_LAYERS)
        for preset in REQUIRED_PRESETS:
            self.assertIn(preset, names, f'missing landscape preset {preset!r}')
        # Base map families the old standard already pulled.
        for preset in ('buildings', 'highways', 'landuse'):
            self.assertIn(preset, names)
        # Unique, stable order for filenames / CLI.
        self.assertEqual(len(names), len(set(names)))

    def test_landscape_presets_match_geometry_families(self):
        """Each landscape preset is the GDAL layer + WHERE the layers already read."""
        presets = oce.landscape_layer_presets()
        self.assertEqual(presets['water'][0][0], 'multipolygons')
        self.assertIn("natural = 'water'", presets['water'][0][1])
        self.assertEqual(presets['waterway'][0][0], 'lines')
        self.assertIn('waterway', presets['waterway'][0][1])
        self.assertEqual(presets['barrier'][0][0], 'lines')
        where_b = presets['barrier'][0][1]
        for tag in ('fence', 'hedge', 'wall'):
            self.assertIn(tag, where_b)
        self.assertEqual(presets['furniture'], list(sf.EXTRACT_JOBS))
        self.assertEqual(presets['pedestrian'][0][0], 'multipolygons')
        self.assertIn("highway = 'pedestrian'", presets['pedestrian'][0][1])
        self.assertEqual(presets['natural'][0][0], 'multipolygons')
        where_n = presets['natural'][0][1]
        for tag in ('wood', 'scrub'):
            self.assertIn(tag, where_n)
        self.assertEqual(presets['leisure'][0][0], 'multipolygons')
        self.assertIn('leisure', presets['leisure'][0][1])
        trees = presets['trees']
        self.assertEqual({j[0] for j in trees}, {'points', 'lines'})
        blob = ' '.join(j[1] for j in trees)
        self.assertIn("natural = 'tree'", blob)
        self.assertIn("natural = 'tree_row'", blob)

    def test_landuse_and_landclass_presets_are_tool_owned(self):
        """Installed into an empty provider, so a provider without landuse still gets it (#253)."""
        presets: dict = {}
        oce.install_cell_extract_layer_presets(presets)
        self.assertIn("landuse IS NOT NULL", presets['landuse'][0][1])
        where = presets['landclass'][0][1]
        for tag in ('parking', 'university', 'grassland', 'wetland'):
            self.assertIn(tag, where)
        self.assertEqual(presets['landclass'][0][0], 'multipolygons')

    def test_does_not_fetch_kerb_or_crossing_nodes(self):
        """Kerb / crossing points stay out of the standard extract jobs."""
        blob = ' '.join(
            where for jobs in oce.landscape_layer_presets().values() for _g, where, _m in jobs
        ).lower()
        self.assertNotIn('kerb', blob)
        self.assertNotIn('crossing', blob)

    def test_install_writes_landscape_presets(self):
        presets: dict = {
            'buildings': [('multipolygons', "building IS NOT NULL", ['footprint'])],
            'furniture': [('points', "amenity = 'bench'", ['context'])],
        }
        oce.install_cell_extract_layer_presets(presets)
        for name in REQUIRED_PRESETS:
            self.assertIn(name, presets)
        self.assertEqual(presets['furniture'], list(sf.EXTRACT_JOBS))

    def test_default_layers_argv_when_layers_omitted(self):
        """Shim path: omitted --layers becomes the full cell-extract CSV."""
        csv = oce.cell_extract_layers_csv()
        out = oce.with_default_cell_extract_layers(
            ['--at', '1,2', '--cell', '--place', 'X'],
        )
        self.assertEqual(out[:2], ['--layers', csv])
        # Explicit --layers wins.
        kept = oce.with_default_cell_extract_layers(
            ['--layers', 'buildings', '--at', '1,2', '--place', 'X'],
        )
        self.assertEqual(kept[0:2], ['--layers', 'buildings'])
        # Custom GDAL query is left alone.
        gdal = oce.with_default_cell_extract_layers(
            ['--gdal-layer', 'points', '--where', '1=1', '--at', '1,2', '--place', 'X'],
        )
        self.assertNotIn('--layers', gdal)

    def test_osm_extract_shim_wires_cell_extract(self):
        shim = Path(__file__).resolve().parents[2] / 'shims' / 'osm_extract.py'
        text = shim.read_text()
        self.assertIn('install_cell_extract_layer_presets', text)
        self.assertIn('with_default_cell_extract_layers', text)

    def test_cell_sources_guide_uses_shim_cell_default(self):
        """Suggested fetch command relies on the shim default layers, not buildings-only."""
        import cell_sources as cs

        lines = cs.fetch_commands(48, -44)
        blob = '\n'.join(lines)
        self.assertIn('shims/osm_extract.py', blob)
        self.assertIn('--cell', blob)
        self.assertIn('CELL_EXTRACT_LAYERS', blob)
        self.assertNotIn('--layers buildings,highways', blob)
