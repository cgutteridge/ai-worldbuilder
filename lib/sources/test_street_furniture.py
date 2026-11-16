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
        """Regression: shims/osm_extract.py installs furniture via cell-extract presets."""
        shim = Path(__file__).resolve().parents[2] / 'shims' / 'osm_extract.py'
        text = shim.read_text()
        self.assertIn('install_cell_extract_layer_presets', text)
        # Furniture jobs still land in LAYER_PRESETS through osm_cell_extract.
        import osm_cell_extract as oce

        presets: dict = {}
        oce.install_cell_extract_layer_presets(presets)
        self.assertEqual(presets['furniture'], list(sf.EXTRACT_JOBS))


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
                        {'kind': 'hedge', 'cells': [[6210, -5580], [6211, -5580]],
                         'note': 'Retail border', 'allow_against_build': True},
                    ],
                    'observations': [
                        {'kind': 'bench', 'note': 'more plaza seating',
                         'unresolved_reason': 'visible in aerial but xy unclear'},
                    ],
                },
                '0,0': {'items': [{'x': 1, 'z': 2, 'kind': 'bollard'}]},
            }))
            items = sf.designer_items((48, -44), path)
            obs = sf.designer_observations((48, -44), path)
            missing_items = sf.designer_items((9, 9), path)
            missing_obs = sf.designer_observations((9, 9), path)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['kind'], 'bench')
        self.assertEqual(items[0]['source'], 'designer')
        self.assertEqual(items[0]['evidence'], 'aerial sheet + street photo')
        self.assertEqual(items[1]['kind'], 'hedge')
        self.assertEqual(items[1]['cells'], [[6210, -5580], [6211, -5580]])
        self.assertTrue(items[1]['allow_against_build'])
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]['kind'], 'bench')
        self.assertIn('unclear', obs[0]['unresolved_reason'])
        self.assertEqual(missing_items, [])
        self.assertEqual(missing_obs, [])

    def test_loads_coordinate_photo_and_aerial_object_labels(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'decor-furniture.json'
            labels = [{'kind': 'vehicle', 'cell': [4, 5], 'source': 'aerial',
                       'evidence_ids': ['aerial1']}]
            path.write_text(json.dumps({'0,0': {'observations': labels}}))
            self.assertEqual(sf.designer_observations((0, 0), path), labels)

    def test_merge_prefers_map_over_designer_at_same_column(self):
        mapped = sf.map_items([(10, 20, 'bench')])
        designed = [{'x': 10, 'z': 20, 'kind': 'waste_basket', 'source': 'designer'},
                    {'x': 11, 'z': 20, 'kind': 'bollard', 'source': 'designer'}]
        merged = sf.merge_items(mapped, designed)
        self.assertEqual([(i['x'], i['z'], i['kind'], i['source']) for i in merged],
                         [(10, 20, 'bench', 'map'), (11, 20, 'bollard', 'designer')])


TABLE = {'residential': 30, 'primary': 38, 'footway': 40, 'service': None}
ROAD = [(0.0, 0.0), (300.0, 0.0)]          # a straight 300-block strip along +x, centreline z=0


class LampPositions(unittest.TestCase):
    """#260: labelled lamp inference along a synthetic road strip."""

    def lamps(self, highway='residential', seed=1, **kw):
        return sf.lamp_positions(ROAD, highway, TABLE, seed, **kw)

    def test_spacing_follows_the_highway_class(self):
        for highway, spacing in (('residential', 30), ('primary', 38), ('footway', 40)):
            xs = [i['x'] for i in self.lamps(highway)]
            self.assertGreaterEqual(len(xs), int(300 // spacing) - 1)
            self.assertLessEqual(len(xs), int(300 // spacing) + 1)
            gaps = [b - a for a, b in zip(xs, xs[1:])]
            self.assertTrue(all(abs(g - spacing) <= 1 for g in gaps), (highway, gaps))
        self.assertGreater(len(self.lamps('residential')), len(self.lamps('primary')))

    def test_lamps_alternate_sides_and_stand_off_the_carriageway(self):
        items = self.lamps(half_width=4.5)
        sides = [1 if i['z'] > 0 else -1 for i in items]
        self.assertTrue(all(a != b for a, b in zip(sides, sides[1:])))
        # carriageway columns are those within 4.5 of the centreline: a lamp column centre is beyond it
        self.assertTrue(all(abs(i['z'] + 0.5) >= 4.5 + 0.5 for i in items))

    def test_every_item_is_labelled_inference_and_low_confidence(self):
        items = self.lamps()
        self.assertTrue(items)
        for item in items:
            self.assertEqual((item['kind'], item['source'], item['confidence']),
                             ('street_lamp', 'inference', 'low'))
            self.assertIn(item['kind'], sf.RECIPES)

    def test_same_seed_same_lamps_other_seed_other_phase(self):
        self.assertEqual(self.lamps(seed=7), self.lamps(seed=7))
        self.assertNotEqual([i['x'] for i in self.lamps(seed=7)], [i['x'] for i in self.lamps(seed=8)])
        self.assertEqual(sf.stable_seed('48,-44', 12), sf.stable_seed('48,-44', 12))
        self.assertNotEqual(sf.stable_seed('48,-44', 12), sf.stable_seed('48,-44', 13))

    def test_class_without_spacing_is_unlit_unless_lit_says_so(self):
        self.assertEqual(self.lamps('service'), [])          # null in the table
        self.assertEqual(self.lamps('cycleway'), [])         # absent from the table
        self.assertEqual(self.lamps('residential', lit='no'), [])
        lit = self.lamps('cycleway', lit='yes')
        self.assertTrue(lit)                                  # lit=yes lights it at the fallback spacing
        self.assertEqual({i['spacing'] for i in lit}, {sf.LIT_FALLBACK_SPACING})
        self.assertEqual({i['spacing'] for i in self.lamps('residential', lit='yes')}, {30.0})

    def test_bent_road_follows_the_line_and_degenerate_lines_give_nothing(self):
        bent = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)]
        items = sf.lamp_positions(bent, 'residential', TABLE, 3, half_width=3.0)
        self.assertTrue(any(i['x'] > 90 and i['z'] > 10 for i in items))     # lamps on the second leg
        self.assertEqual(sf.lamp_positions([(5.0, 5.0)], 'residential', TABLE, 3), [])
        self.assertEqual(sf.lamp_positions([(5.0, 5.0), (5.0, 5.0)], 'residential', TABLE, 3), [])


class BenchPositions(unittest.TestCase):
    """#260: bench inference along a synthetic path."""

    PATH = [(0.0, 0.0), (200.0, 0.0)]

    def test_density_and_labels(self):
        counts = [len(sf.bench_positions(self.PATH, 1.5, seed)) for seed in range(40)]
        self.assertTrue(set(counts) <= {2, 3})                       # 1.5 per 100 on 200 blocks: 3 expected
        self.assertGreater(sum(counts) / len(counts), 2.5)
        for item in sf.bench_positions(self.PATH, 1.5, 1):
            self.assertEqual((item['kind'], item['source'], item['confidence']),
                             ('bench', 'inference', 'low'))
            self.assertGreaterEqual(abs(item['z'] + 0.5), 1.0 + 0.5)   # beside the path, not on it
            self.assertTrue(0 <= item['x'] <= 200)

    def test_no_density_no_benches_and_seed_is_stable(self):
        self.assertEqual(sf.bench_positions(self.PATH, 0, 1), [])
        self.assertEqual(sf.bench_positions(self.PATH, None, 1), [])
        self.assertEqual(sf.bench_positions([(0.0, 0.0)], 5, 1), [])
        self.assertEqual(sf.bench_positions(self.PATH, 1.5, 4), sf.bench_positions(self.PATH, 1.5, 4))

    def test_benches_are_spread_not_clumped(self):
        items = sf.bench_positions([(0.0, 0.0), (400.0, 0.0)], 2.0, 5)
        xs = sorted(i['x'] for i in items)
        self.assertGreaterEqual(len(xs), 7)
        self.assertTrue(all(b - a >= 10 for a, b in zip(xs, xs[1:])), xs)


if __name__ == '__main__':
    unittest.main()
