"""Regression: street furniture kinds, recipes, designer spec, extract jobs (#127)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import street_furniture as sf


class ClassifyAndRecipes(unittest.TestCase):
    """Part (1)+(2): tag → kind and a block recipe per kind."""

    def test_classify_each_mapped_tag(self):
        cases = [
            ({'amenity': 'bench'}, 'bench'),
            ({'amenity': 'waste_basket'}, 'waste_basket'),
            ({'amenity': 'bicycle_parking'}, 'bicycle_parking'),
            ({'highway': 'street_lamp'}, 'street_lamp'),
            ({'barrier': 'bollard'}, 'bollard'),
            ({'traffic_sign': 'yes'}, 'sign'),
            ({'information': 'board'}, 'sign'),
            ({'amenity': 'cafe'}, None),
            ({'barrier': 'hedge'}, None),
        ]
        for tags, want in cases:
            self.assertEqual(sf.classify(tags), want, tags)

    def test_every_kind_has_a_non_empty_recipe(self):
        for kind in sf.KINDS:
            stack = sf.recipe(kind)
            self.assertTrue(stack, kind)
            for dy, block in stack:
                self.assertGreaterEqual(dy, 1, kind)
                self.assertTrue(block and ':' not in block, block)
                if block.endswith('_wall'):
                    self.assertIn(
                        block, sf.REAL_WALL_BLOCKS,
                        f'{kind} recipe uses unknown wall block {block!r}',
                    )


class ExtractJobs(unittest.TestCase):
    """Part (1): extract WHERE covers the furniture tags for the points layer."""

    def test_extract_jobs_target_points_and_furniture_tags(self):
        self.assertTrue(sf.EXTRACT_JOBS)
        layers = {job[0] for job in sf.EXTRACT_JOBS}
        self.assertIn('points', layers)
        where = ' '.join(job[1] for job in sf.EXTRACT_JOBS)
        for needle in ('bench', 'waste_basket', 'bicycle_parking', 'street_lamp', 'bollard'):
            self.assertIn(needle, where)

    def test_extract_jobs_install_into_layer_presets(self):
        """Tool extract path consumes EXTRACT_JOBS (shim installs furniture preset)."""
        presets: dict = {
            'buildings': [('multipolygons', "building IS NOT NULL", ['footprint'])],
        }
        sf.install_furniture_layer_preset(presets)
        self.assertEqual(presets['furniture'], list(sf.EXTRACT_JOBS))
        # Overwrite any stale world copy with the tool vocabulary.
        presets['furniture'] = [('points', "amenity = 'bench'", ['context'])]
        sf.install_furniture_layer_preset(presets)
        self.assertEqual(presets['furniture'], list(sf.EXTRACT_JOBS))

    def test_osm_extract_shim_wires_extract_jobs(self):
        """Regression: shims/osm_extract.py installs EXTRACT_JOBS into LAYER_PRESETS."""
        shim = Path(__file__).resolve().parents[2] / 'shims' / 'osm_extract.py'
        text = shim.read_text()
        self.assertIn('install_furniture_layer_preset', text)
        self.assertIn('EXTRACT_JOBS', text)


class DesignerSpec(unittest.TestCase):
    """Part (4): designer JSON proposes placements when the map has none."""

    def test_loads_items_for_cell_and_skips_bad_rows(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'decor-furniture.json'
            path.write_text(json.dumps({
                '48,-44': {
                    'evidence': 'aerial sheet + street photo',
                    'items': [
                        {'x': 6200, 'z': -5550, 'kind': 'bench', 'note': 'plaza'},
                        {'x': 6201, 'z': -5550, 'kind': 'not_a_kind'},
                        {'x': 6202, 'z': -5550},  # missing kind
                        'skip-me',
                    ],
                },
                '0,0': {'items': [{'x': 1, 'z': 2, 'kind': 'bollard'}]},
            }))
            items = sf.designer_items((48, -44), path)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['kind'], 'bench')
        self.assertEqual(items[0]['source'], 'designer')
        self.assertEqual(items[0]['evidence'], 'aerial sheet + street photo')
        self.assertEqual(sf.designer_items((9, 9), path), [])

    def test_merge_prefers_map_over_designer_at_same_column(self):
        mapped = sf.map_items([(10, 20, 'bench')])
        designed = [{'x': 10, 'z': 20, 'kind': 'waste_basket', 'source': 'designer'},
                    {'x': 11, 'z': 20, 'kind': 'bollard', 'source': 'designer'}]
        merged = sf.merge_items(mapped, designed)
        self.assertEqual([(i['x'], i['z'], i['kind'], i['source']) for i in merged],
                         [(10, 20, 'bench', 'map'), (11, 20, 'bollard', 'designer')])


if __name__ == '__main__':
    unittest.main()
