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
            ({'leisure': 'picnic_table'}, 'picnic_table'),
            ({'highway': 'traffic_signals'}, 'traffic_signals'),
            ({'highway': 'traffic_signals', 'crossing': 'traffic_signals'}, 'traffic_signals'),
            ({'highway': 'crossing', 'crossing': 'uncontrolled'}, 'crossing'),
            ({'highway': 'crossing', 'crossing': 'zebra'}, 'crossing'),
            ({'highway': 'crossing'}, 'crossing'),
            ({'highway': 'crossing', 'crossing': 'traffic_signals'}, 'traffic_signals'),
            ({'highway': 'crossing', 'crossing': 'toucan'}, 'traffic_signals'),
            ({'highway': 'crossing', 'crossing:signals': 'yes'}, 'traffic_signals'),
            ({'highway': 'crossing', 'crossing': 'unmarked'}, None),
            ({'highway': 'give_way'}, None),
            ({'traffic_calming': 'island'}, 'traffic_island'),
            ({'traffic_calming': 'bump'}, None),
            ({'highway': 'traffic_signals', 'traffic_sign': 'yes'}, 'traffic_signals'),
            ({'leisure': 'pitch'}, None),
            ({'traffic_sign': 'yes'}, 'sign'),
            ({'information': 'board'}, 'sign'),
            ({'amenity': 'cafe'}, None),
            ({'barrier': 'hedge'}, None),
        ]
        for tags, want in cases:
            self.assertEqual(sf.classify(tags), want, tags)

    def test_every_kind_has_a_non_empty_recipe(self):
        for kind in sf.RECIPES:
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


class RoadEdgeKinds(unittest.TestCase):
    """#334: signals, crossings and mapped islands are single-column kinds with their own placement rules."""

    def test_kinds_exist_with_recipes_and_distinct_blocks(self):
        for kind in ('traffic_signals', 'crossing', 'traffic_island'):
            self.assertIn(kind, sf.KINDS)
            self.assertIn(kind, sf.FURNITURE_KINDS)
            self.assertTrue(sf.recipe(kind))
        self.assertNotEqual(sf.recipe('traffic_signals'), sf.recipe('street_lamp'))
        self.assertGreater(len(sf.recipe('traffic_signals')), len(sf.recipe('crossing')))

    def test_island_slab_is_a_bottom_half_so_decor_keeps_it_half_height(self):
        ((_dy, block),) = sf.recipe('traffic_island')
        self.assertTrue(block.endswith('[type=bottom]'))

    def test_road_node_kinds_move_off_the_road_and_the_island_stays(self):
        self.assertEqual(sf.ROADSIDE_KINDS, {'traffic_signals', 'crossing'})
        self.assertEqual(sf.ON_ROAD_KINDS, {'traffic_island'})

    def test_extract_where_names_the_new_tags(self):
        where = ' '.join(job[1] for job in sf.EXTRACT_JOBS)
        for needle in ('traffic_signals', 'crossing', 'traffic_calming', 'island'):
            self.assertIn(needle, where)

    def test_roadside_candidates_are_nearest_first_and_bounded(self):
        cands = sf.roadside_candidates(10, 20)
        self.assertNotIn((10, 20), cands)
        self.assertEqual(cands[:4], [(9, 20), (10, 19), (10, 21), (11, 20)])
        dists = [(x - 10) ** 2 + (z - 20) ** 2 for x, z in cands]
        self.assertEqual(dists, sorted(dists))
        self.assertLessEqual(max(dists), sf.ROADSIDE_REACH ** 2)


class Footprints(unittest.TestCase):
    """#305/#306: multi-block kinds are centre-anchored footprints; single-column kinds are one column."""

    def test_every_kind_is_a_recipe_or_a_footprint(self):
        self.assertEqual(set(sf.KINDS), set(sf.FURNITURE_KINDS))
        self.assertEqual(set(sf.FOOTPRINTS) & set(sf.RECIPES), set())

    def test_single_column_kinds_keep_their_recipe_at_every_rotation(self):
        for rot in sf.ROTATIONS:
            self.assertEqual(sf.footprint('street_lamp', rot),
                             [(0, 0, dy, b) for dy, b in sf.recipe('street_lamp')])

    def test_rectangular_table_is_a_table_row_between_two_bench_rows(self):
        rows = sf.footprint('picnic_table')
        self.assertEqual(len(rows), 9)
        self.assertEqual({(dx, dz) for dx, dz, _dy, _b in rows}, {(x, z) for x in (-1, 0, 1) for z in (-1, 0, 1)})
        tops = {(dx, dz) for dx, dz, _dy, b in rows if b.endswith('[type=top]')}
        self.assertEqual(tops, {(-1, 0), (0, 0), (1, 0)})

    def test_rotation_turns_clockwise_from_above(self):
        self.assertEqual(sf.rotate_offset(1, 0, 90), (0, 1))          # east -> south (+z)
        self.assertEqual(sf.rotate_offset(1, 0, 180), (-1, 0))
        self.assertEqual(sf.rotate_offset(1, 0, 270), (0, -1))
        tops90 = {(dx, dz) for dx, dz, _dy, b in sf.footprint('picnic_table', 90) if b.endswith('[type=top]')}
        self.assertEqual(tops90, {(0, -1), (0, 0), (0, 1)})            # long axis now along z
        self.assertEqual(sorted(sf.footprint('picnic_table', 180)), sorted(sf.footprint('picnic_table', 0)))

    def test_round_table_is_a_plus_that_rotation_leaves_alone(self):
        rows = sf.footprint('picnic_table_round')
        self.assertEqual(len(rows), 5)
        self.assertEqual({(dx, dz) for dx, dz, _dy, _b in rows}, {(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)})
        for rot in sf.ROTATIONS:
            self.assertEqual(sorted(sf.footprint('picnic_table_round', rot)), sorted(rows))

    def test_footprints_stay_within_the_warm_ring_and_use_only_rotation_invariant_states(self):
        for kind, rows in sf.FOOTPRINTS.items():
            self.assertIn((0, 0), {(dx, dz) for dx, dz, _dy, _b in rows}, kind)
            for dx, dz, dy, block in rows:
                self.assertLessEqual(max(abs(dx), abs(dz)), sf.FOOTPRINT_RADIUS, kind)
                self.assertGreaterEqual(dy, 1)
                states = block.partition('[')[2].rstrip(']')
                for kv in filter(None, states.split(',')):
                    self.assertEqual(kv.split('=')[0], 'type', f'{kind}: {block} needs a rotator')

    def test_parse_rotation(self):
        self.assertEqual(sf.parse_rotation(None), (0, ''))
        self.assertEqual(sf.parse_rotation(90), (90, ''))
        self.assertEqual(sf.parse_rotation('270'), (270, ''))
        self.assertEqual(sf.parse_rotation(-90), (270, ''))
        for bad in (45, 'east', 91.5):
            rot, reason = sf.parse_rotation(bad)
            self.assertIsNone(rot)
            self.assertIn('not 0, 90, 180 or 270', reason)


class ExtractJobs(unittest.TestCase):
    """Part (1): extract WHERE covers the furniture tags for the points layer."""

    def test_extract_jobs_target_points_and_furniture_tags(self):
        self.assertTrue(sf.EXTRACT_JOBS)
        layers = {job[0] for job in sf.EXTRACT_JOBS}
        self.assertIn('points', layers)
        where = ' '.join(job[1] for job in sf.EXTRACT_JOBS)
        for needle in ('bench', 'waste_basket', 'bicycle_parking', 'street_lamp', 'bollard', 'picnic_table'):
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

    def test_designer_picnic_tables_carry_a_rotation_and_a_bad_one_is_flagged(self):
        rows = [{'x': 5, 'z': 6, 'kind': 'picnic_table', 'rotation': 90},
                {'x': 8, 'z': 6, 'kind': 'picnic_table_round'},
                {'x': 11, 'z': 6, 'kind': 'picnic_table', 'rotation': 45},
                {'x': 14, 'z': 6, 'kind': 'bench', 'rotation': 90}]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'decor-furniture.json'
            path.write_text(json.dumps({'0,0': {'items': rows,
                                                'observations': [{'kind': 'picnic_table', 'cell': [5, 6], 'rotation': 90}]}}))
            items = sf.designer_items((0, 0), path)
            obs = sf.designer_observations((0, 0), path)
        self.assertEqual([(i['kind'], i.get('rotation')) for i in items],
                         [('picnic_table', 90), ('picnic_table_round', 0), ('picnic_table', 0), ('bench', None)])
        self.assertIn('not 0, 90, 180 or 270', items[2]['rotation_error'])
        self.assertNotIn('rotation_error', items[0])
        self.assertEqual(obs[0]['rotation'], 90)

    def test_loads_rejected_items_for_the_cell_and_skips_bad_rows(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'decor-furniture.json'
            path.write_text(json.dumps({
                '48,-44': {'rejected_items': [
                    {'cell': [6168, -5584], 'note': 'misread shed'},
                    {'cell': [1, 2]},
                    {'note': 'no column'},
                    {'cell': ['x', 1]},
                    'skip',
                ]},
            }))
            rows = sf.rejected_items((48, -44), path)
            self.assertEqual(sf.rejected_items((0, 0), path), [])
            self.assertEqual(sf.rejected_items((48, -44), Path(td) / 'missing.json'), [])
        self.assertEqual(rows, [
            {'cell': [6168, -5584], 'note': 'misread shed'},
            {'cell': [1, 2]},
        ])


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
