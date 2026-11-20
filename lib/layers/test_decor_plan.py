"""Decor plan from aerial/photos with provenance (#150)."""
from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import decision_record as dr
import decor_plan as dp
import provenance as prov
import street_furniture as sf


class TestBuildingBorderHedges(unittest.TestCase):
    """Aerial dark-green ring outside a footprint becomes hedge plan items."""

    def test_shade_ring_around_footprint_becomes_hedge_with_aerial_provenance(self):
        # Tall footprint; shade columns on the west face outside (≥ min_cells).
        footprint = {(x, z) for x in range(4, 7) for z in range(4, 9)}
        border = [(3, z) for z in range(4, 9)]  # west of footprint, 5 columns
        rgb = {q: (0.1, 0.4, 0.1) for q in border}
        with mock.patch('ground_sources.aerial_class', return_value='shade'):
            items = dp.aerial_building_border_hedges(
                footprint, rgb, canopy=set(), evidence_ids=['aerial1'],
            )
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item['kind'], 'hedge')
        self.assertEqual(item['role'], 'barrier')
        self.assertTrue(item['allow_against_build'])
        self.assertEqual(sorted(tuple(c) for c in item['cells']), border)
        self.assertEqual(item['provenance']['source'], 'aerial')
        self.assertEqual(item['provenance']['confidence'], 'medium')
        self.assertEqual(item['provenance']['evidence_ids'], ['aerial1'])
        self.assertFalse(prov.is_unresolved(item['provenance']))

    def test_canopy_columns_are_not_building_border_hedges(self):
        footprint = {(5, 5)}
        border = [(4, 5), (6, 5), (5, 4), (5, 6)]
        rgb = {q: (0.1, 0.4, 0.1) for q in border}
        canopy = set(border)
        with mock.patch('ground_sources.aerial_class', return_value='shade'):
            items = dp.aerial_building_border_hedges(footprint, rgb, canopy=canopy)
        self.assertEqual(items, [])

    def test_short_fragment_becomes_unresolved_not_silent(self):
        """A one-column shade touch is listed unresolved instead of dropped."""
        footprint = {(5, 5)}
        rgb = {(4, 5): (0.1, 0.4, 0.1)}
        with mock.patch('ground_sources.aerial_class', return_value='shade'):
            items = dp.aerial_building_border_hedges(
                footprint, rgb, canopy=set(), min_cells=4,
            )
        self.assertEqual(len(items), 1)
        self.assertTrue(prov.is_unresolved(items[0]['provenance']))
        self.assertIn('too short', items[0]['provenance']['unresolved_reason'])


class TestFreeStandingHedges(unittest.TestCase):
    """Dark-green strips away from buildings become hedges (I-035)."""

    SHADE = (0.1, 0.4, 0.1)

    def hedges(self, cols, *, canopy=(), footprint=(), taken=(), hm=None):
        rgb = {q: self.SHADE for q in cols}
        with mock.patch('ground_sources.aerial_class', return_value='shade'):
            return dp.aerial_free_hedges(
                set(footprint), rgb, set(canopy), set(taken), lidar_hm=hm,
                evidence_ids=['aerial1'])

    def hm(self, raised):
        import numpy as np
        hm = {'x0': 0, 'z0': 0, 'terrain': np.full((20, 20), 64),
              'h': np.full((20, 20), 64), 'kind': np.zeros((20, 20))}
        for x, z in raised:
            hm['h'][x, z] = 66
        return hm

    def test_thin_strip_is_a_resolved_aerial_hedge(self):
        strip = [(5, z) for z in range(2, 9)]
        items = self.hedges(strip)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual((item['kind'], item['role']), ('hedge', 'barrier'))
        self.assertEqual(sorted(tuple(c) for c in item['cells']), strip)
        self.assertEqual(item['provenance']['source'], 'aerial')
        self.assertFalse(prov.is_unresolved(item['provenance']))
        self.assertFalse(item.get('allow_against_build'))

    def test_short_or_wide_shade_is_not_a_hedge(self):
        self.assertEqual(self.hedges([(5, z) for z in range(2, 5)]), [])
        blob = [(x, z) for x in range(2, 8) for z in range(2, 8)]
        self.assertEqual(self.hedges(blob), [])

    def test_canopy_footprint_and_taken_columns_are_excluded(self):
        strip = [(5, z) for z in range(2, 9)]
        self.assertEqual(self.hedges(strip, canopy=strip), [])
        self.assertEqual(self.hedges(strip, footprint=strip), [])
        self.assertEqual(self.hedges(strip, taken=strip[:4]), [])

    def test_lidar_must_show_the_strip_raised_one_to_three(self):
        strip = [(5, z) for z in range(2, 9)]
        self.assertEqual(len(self.hedges(strip, hm=self.hm(strip))), 1)
        self.assertEqual(self.hedges(strip, hm=self.hm([])), [])

    def test_tree_kind_columns_do_not_confirm_a_hedge(self):
        from lidar_heightmap import TREE
        strip = [(5, z) for z in range(2, 9)]
        hm = self.hm(strip)
        for x, z in strip:
            hm['kind'][x, z] = TREE
        self.assertEqual(self.hedges(strip, hm=hm), [])

    def test_designer_strip_outranks_aerial_free_hedge(self):
        strip = [(5, z) for z in range(2, 9)]
        rgb = {q: self.SHADE for q in strip}
        designer = [{'kind': 'iron_bars', 'cells': [list(c) for c in strip]}]
        with mock.patch('ground_sources.aerial_class', return_value='shade'):
            plan = dp.build_decor_plan(
                (0, 0), map_barriers={}, garden={}, rgb=rgb, footprint=set(),
                canopy=set(), designer_items=designer, free_hedges=True)
        self.assertEqual([i['kind'] for i in plan['items']], ['iron_bars'])

    def test_lidar_confirmed_hedge_leaves_no_unresolved_lidar_object(self):
        strip = [(5, z) for z in range(2, 9)]
        rgb = {q: self.SHADE for q in strip}
        with mock.patch('ground_sources.aerial_class', return_value='shade'):
            plan = dp.build_decor_plan(
                (0, 0), map_barriers={}, garden={}, rgb=rgb, footprint=set(),
                canopy=set(), lidar_hm=self.hm(strip), free_hedges=True)
        self.assertEqual([i['id'] for i in plan['items']], ['hedge-free-5-2'])
        self.assertEqual(dp.unresolved_items(plan), [])

    def test_free_hedges_are_off_unless_asked(self):
        strip = [(5, z) for z in range(2, 9)]
        rgb = {q: self.SHADE for q in strip}
        with mock.patch('ground_sources.aerial_class', return_value='shade'):
            plan = dp.build_decor_plan(
                (0, 0), map_barriers={}, garden={}, rgb=rgb, footprint=set(), canopy=set())
        self.assertEqual(plan['items'], [])

    def test_build_decor_plan_includes_free_hedges_but_not_over_map_hedges(self):
        strip = [(5, z) for z in range(2, 9)]
        rgb = {q: self.SHADE for q in strip}
        with mock.patch('ground_sources.aerial_class', return_value='shade'):
            plan = dp.build_decor_plan(
                (0, 0), map_barriers={'hedge': {(5, 2), (5, 3)}, 'wall': set(), 'fence': set()},
                garden={}, rgb=rgb, footprint=set(), canopy=set(), free_hedges=True)
        free = [i for i in plan['items'] if str(i['id']).startswith('hedge-free')]
        self.assertEqual(len(free), 1)
        self.assertEqual(sorted(tuple(c) for c in free[0]['cells']), strip[2:])
        self.assertIn((5, 8), dp.resolved_barriers(plan))


class TestLidarIsOnlyAHint(unittest.TestCase):
    """Lidar sizes and places what OSM/photo/designer name; a lone lump is ignored."""

    def hm(self, raised):
        import numpy as np
        hm = {'x0': 0, 'z0': 0, 'terrain': np.full((16, 16), 64),
              'h': np.full((16, 16), 64), 'kind': np.zeros((16, 16))}
        for (x, z), d in raised.items():
            hm['h'][x, z] = 64 + d
        return hm

    def plan(self, raised, **kw):
        kw.setdefault('map_barriers', {})
        return dp.build_decor_plan(
            (0, 0), garden={}, rgb={}, footprint=set(), canopy=set(),
            lidar_hm=self.hm(raised), **kw)

    def test_lone_lidar_lump_adds_nothing_to_the_plan(self):
        plan = self.plan({(4, 4): 1, (4, 5): 1, (10, 10): 2})
        self.assertEqual(plan['items'], [])
        self.assertEqual(dp.unresolved_items(plan), [])

    def test_map_furniture_on_a_lump_is_kept_with_a_lidar_hint(self):
        plan = self.plan({(4, 4): 1, (4, 5): 1}, map_furniture=[(4, 4, 'bench')])
        rows = dp.resolved_furniture(plan)
        self.assertEqual([(r['x'], r['z'], r['kind']) for r in rows], [(4, 4, 'bench')])
        item = next(i for i in plan['items'] if i['role'] == 'furniture')
        self.assertEqual(item['lidar_height'], 1)
        self.assertEqual(sorted(tuple(c) for c in item['lidar_cells']), [(4, 4), (4, 5)])

    def test_furniture_off_any_lump_has_no_hint(self):
        plan = self.plan({(10, 10): 1}, map_furniture=[(4, 4, 'bench')])
        item = next(i for i in plan['items'] if i['role'] == 'furniture')
        self.assertNotIn('lidar_height', item)
        self.assertEqual(len(dp.resolved_furniture(plan)), 1)

    def test_a_vehicle_label_still_vetoes_map_furniture_on_that_lump(self):
        plan = self.plan({(4, 4): 1, (4, 5): 1}, map_furniture=[(4, 5, 'bench')],
                         photo_observations=[{'kind': 'vehicle', 'cell': [4, 4]}])
        self.assertEqual(dp.resolved_furniture(plan), [])

    def test_barrier_height_follows_lidar_when_the_columns_are_raised(self):
        cols = {(5, z): 2 for z in range(2, 7)}
        plan = self.plan(cols, map_barriers={'wall': set(cols)})
        wall = next(i for i in plan['items'] if i['kind'] == 'brick_wall')
        self.assertEqual(wall['height'], 2)
        self.assertEqual(dp.resolved_barriers(plan)[(5, 3)][2], 2)

    def test_barrier_keeps_default_height_when_lidar_shows_nothing_raised(self):
        cells = {(5, z) for z in range(2, 7)}
        plan = self.plan({}, map_barriers={'wall': cells})
        wall = next(i for i in plan['items'] if i['kind'] == 'brick_wall')
        self.assertEqual(wall['height'], dp.DEFAULT_HEIGHT['brick_wall'])

    def test_explicit_garden_height_beats_lidar(self):
        cols = {(5, z): 1 for z in range(2, 7)}
        plan = dp.build_decor_plan(
            (0, 0), map_barriers={}, rgb={}, footprint=set(), canopy=set(),
            lidar_hm=self.hm(cols),
            garden=[{'id': 'garden-wall', 'role': 'barrier', 'kind': 'brick_wall',
                     'cells': [list(q) for q in cols], 'block': 'brick_wall', 'height': 3,
                     'provenance': {'source': 'register_entry', 'confidence': 'high',
                                    'evidence_ids': [], 'unresolved_reason': ''}}])
        wall = next(i for i in plan['items'] if i['kind'] == 'brick_wall')
        self.assertEqual(wall['height'], 3)

    def test_structure_and_off_grid_columns_do_not_size_a_barrier(self):
        from lidar_heightmap import STRUCTURE
        cols = {(5, z): 2 for z in range(2, 7)}
        hm = self.hm(cols)
        for q in cols:
            hm['kind'][q] = STRUCTURE
        self.assertIsNone(dp.lidar_barrier_height(hm, cols))
        self.assertIsNone(dp.lidar_barrier_height(
            self.hm(cols), list(cols) + [(40, z) for z in range(10)]))

    def test_labelled_compatible_object_carries_lidar_hint_and_is_not_doubled(self):
        plan = self.plan({(4, 4): 1, (4, 5): 1}, map_furniture=[(4, 5, 'bench')],
                         photo_observations=[{'kind': 'bench', 'cell': [4, 4]}])
        objs = [i for i in plan['items'] if i['role'] == 'furniture']
        self.assertEqual(len(objs), 1)
        self.assertEqual(objs[0]['lidar_height'], 1)
        self.assertEqual(len(objs[0]['lidar_cells']), 2)

    def test_tall_or_tree_columns_do_not_size_a_barrier(self):
        from lidar_heightmap import TREE
        cols = {(5, z): 2 for z in range(2, 7)}
        hm = self.hm(cols)
        for q in cols:
            hm['kind'][q] = TREE
        plan = dp.build_decor_plan(
            (0, 0), map_barriers={'wall': set(cols)}, garden={}, rgb={},
            footprint=set(), canopy=set(), lidar_hm=hm)
        wall = next(i for i in plan['items'] if i['kind'] == 'brick_wall')
        self.assertEqual(wall['height'], dp.DEFAULT_HEIGHT['brick_wall'])
        taller = {q: 9 for q in cols}
        plan = self.plan(taller, map_barriers={'wall': set(cols)})
        wall = next(i for i in plan['items'] if i['kind'] == 'brick_wall')
        self.assertEqual(wall['height'], dp.DEFAULT_HEIGHT['brick_wall'])


class TestBuildDecorPlan(unittest.TestCase):
    """Full plan merges map, garden, aerial borders, designer; lists unresolved."""

    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.root = Path(self._d.name)
        dr.add_evidence('aerial1', type='aerial', provider='p', title='Aerial', root=self.root)
        dr.add_evidence('sv1', type='streetview', provider='p', title='Street', root=self.root)

    def tearDown(self):
        self._d.cleanup()

    def test_merges_sources_with_provenance_and_lists_unresolved(self):
        cell = (0, 0)
        footprint = {(10, z) for z in range(10, 14)} | {(11, z) for z in range(10, 14)}
        border = [(9, z) for z in range(10, 14)]  # west face, 4 columns
        rgb = {q: (0.1, 0.4, 0.1) for q in border}

        map_furniture = [(20, 20, 'bench')]
        designer = [
            {'x': 21, 'z': 20, 'kind': 'bicycle_parking', 'source': 'designer',
             'note': 'bike-rack fences'},
        ]
        observations = [
            {'kind': 'bench', 'note': 'plaza seating in photo',
             'unresolved_reason': 'photo shows seating but no MC coordinate'},
        ]
        garden = [{'id': 'garden-hedge', 'role': 'barrier', 'kind': 'hedge',
                   'cells': [[30, 30]], 'block': 'oak_leaves[persistent=true]', 'height': 2,
                   'provenance': {'source': 'inference', 'confidence': 'medium',
                                  'evidence_ids': [], 'unresolved_reason': ''}}]

        with mock.patch('ground_sources.aerial_class', return_value='shade'):
            plan = dp.build_decor_plan(
                cell,
                map_barriers={'hedge': {(40, 40)}, 'wall': set(), 'fence': set()},
                map_furniture=map_furniture,
                designer_items=designer,
                photo_observations=observations,
                garden=garden,
                footprint=footprint,
                canopy=set(),
                evidence_ids=['aerial1'],
                root=self.root,
                rgb=rgb,
            )

        kinds = {(i['kind'], i['role']) for i in plan['items']}
        self.assertIn(('hedge', 'barrier'), kinds)
        self.assertIn(('bench', 'furniture'), kinds)
        self.assertIn(('bicycle_parking', 'furniture'), kinds)

        bike = next(i for i in plan['items'] if i['kind'] == 'bicycle_parking')
        self.assertEqual(bike['provenance']['source'], 'aerial')
        self.assertEqual(bike['cell'], [21, 20])

        unresolved = dp.unresolved_items(plan)
        self.assertTrue(any(i.get('kind') == 'bench' and 'plaza' in (i.get('note') or '')
                            for i in unresolved))
        border_items = [
            i for i in plan['items']
            if i.get('allow_against_build') and i['kind'] == 'hedge'
            and sorted(tuple(c) for c in i.get('cells', [])) == border
        ]
        self.assertEqual(len(border_items), 1)

    def test_resolved_barrier_and_furniture_feeds_layer_inputs(self):
        """resolved_barriers / resolved_furniture drop unresolved rows."""
        items = [
            prov.attach_provenance(
                {'id': 'h1', 'kind': 'hedge', 'role': 'barrier',
                 'cells': [[1, 2], [1, 3]], 'block': 'oak_leaves[persistent=true]',
                 'height': 2, 'allow_against_build': True},
                source='aerial', confidence='medium',
            ),
            prov.attach_provenance(
                {'id': 'b1', 'kind': 'bench', 'role': 'furniture', 'cell': [5, 6]},
                source='vector_map', confidence='high',
            ),
            prov.attach_provenance(
                {'id': 'u1', 'kind': 'bench', 'role': 'furniture', 'note': 'guess'},
                source='photo_read', confidence='low',
                unresolved_reason='no coordinate',
            ),
        ]
        plan = {'cell': [0, 0], 'items': items}
        barriers = dp.resolved_barriers(plan)
        self.assertEqual(barriers[(1, 2)], ('hedge', 'oak_leaves[persistent=true]', 2))
        furniture = dp.resolved_furniture(plan)
        self.assertEqual(len(furniture), 1)
        self.assertEqual(furniture[0]['kind'], 'bench')
        self.assertEqual(furniture[0]['x'], 5)
        self.assertEqual(dp.unresolved_items(plan)[0]['id'], 'u1')


class TestPicnicTablePlan(unittest.TestCase):
    """#306: picnic tables are furniture plan rows carrying a rotation, from map, designer and photo labels."""

    def plan(self, **kw):
        return dp.build_decor_plan((0, 0), map_barriers={}, garden=[], rgb={}, footprint=set(), canopy=set(), **kw)

    @staticmethod
    def tables(plan):
        return [i for i in plan['items'] if i.get('role') == 'furniture' and i['kind'].startswith('picnic_table')]

    def test_a_map_table_is_a_high_confidence_row_at_rotation_zero(self):
        item, = self.tables(self.plan(map_furniture=[(10, 20, 'picnic_table')]))
        self.assertEqual((item['cell'], item['rotation'], item['rotation_source']), ([10, 20], 0, 'default'))
        self.assertEqual((item['provenance']['source'], item['provenance']['confidence']), ('vector_map', 'high'))
        row, = dp.resolved_furniture(self.plan(map_furniture=[(10, 20, 'picnic_table')]))
        self.assertEqual((row['kind'], row['rotation']), ('picnic_table', 0))

    def test_a_designer_table_carries_its_rotation_and_both_kinds_plan(self):
        plan = self.plan(designer_items=[
            {'x': 10, 'z': 20, 'kind': 'picnic_table', 'rotation': 90, 'source': 'designer', 'evidence': 'aerial z20'},
            {'x': 30, 'z': 20, 'kind': 'picnic_table_round', 'source': 'designer', 'evidence': 'aerial z20'}])
        rect, rnd = self.tables(plan)
        self.assertEqual((rect['rotation'], rect['rotation_source']), (90, 'designer'))
        self.assertEqual(rnd['rotation'], 0)
        self.assertEqual({i['provenance']['source'] for i in (rect, rnd)}, {'aerial'})
        self.assertEqual({r['x']: r['rotation'] for r in dp.resolved_furniture(plan)}, {10: 90, 30: 0})

    def test_single_column_kinds_carry_no_rotation(self):
        item, = [i for i in self.plan(map_furniture=[(4, 4, 'bench')])['items'] if i['role'] == 'furniture']
        self.assertNotIn('rotation', item)
        self.assertNotIn('rotation', dp.resolved_furniture(self.plan(map_furniture=[(4, 4, 'bench')]))[0])

    def test_the_same_anchor_from_map_and_designer_is_one_row_and_the_map_wins(self):
        plan = self.plan(map_furniture=[(10, 20, 'picnic_table')], designer_items=[
            {'x': 10, 'z': 20, 'kind': 'picnic_table', 'rotation': 90, 'source': 'designer'}])
        item, = self.tables(plan)
        self.assertEqual((item['provenance']['source'], item['rotation']), ('vector_map', 0))

    def test_a_bad_designer_rotation_is_unresolved_with_the_reason(self):
        plan = self.plan(designer_items=[
            {'x': 10, 'z': 20, 'kind': 'picnic_table', 'rotation': 0, 'rotation_error': 'rotation 45 is not 0, 90, 180 or 270',
             'source': 'designer'}])
        item, = self.tables(plan)
        self.assertEqual(item['provenance']['unresolved_reason'], 'rotation 45 is not 0, 90, 180 or 270')
        self.assertEqual(dp.resolved_furniture(plan), [])

    def test_a_labelled_lump_places_the_table_at_the_label_with_its_rotation(self):
        import numpy as np
        hm = {'x0': 0, 'z0': 0, 'terrain': np.full((16, 16), 64), 'h': np.full((16, 16), 64),
              'kind': np.zeros((16, 16))}
        hm['h'][4, 4] = hm['h'][4, 5] = 65
        plan = self.plan(lidar_hm=hm, photo_observations=[
            {'kind': 'picnic_table', 'cell': [4, 5], 'source': 'aerial', 'rotation': 90}])
        item, = self.tables(plan)
        self.assertEqual((item['cell'], item['rotation'], item['rotation_source']), ([4, 5], 90, 'observation'))
        self.assertFalse(prov.is_unresolved(item['provenance']))

    def test_an_unlumped_observation_keeps_its_rotation_and_a_bad_one_is_unresolved(self):
        plan = self.plan(photo_observations=[
            {'kind': 'picnic_table', 'cell': [4, 5], 'source': 'aerial', 'rotation': 90},
            {'kind': 'picnic_table', 'cell': [8, 5], 'source': 'aerial', 'rotation': 45}])
        good, bad = self.tables(plan)
        self.assertEqual((good['rotation'], prov.is_unresolved(good['provenance'])), (90, False))
        self.assertTrue(prov.is_unresolved(bad['provenance']))
        self.assertEqual([r['rotation'] for r in dp.resolved_furniture(plan)], [90])

    def test_inference_never_creates_a_multi_block_kind(self):
        road = [(0.0, 64.0), (127.0, 64.0)]
        plan = self.plan(road_lines=[_way('residential', road, seed=1)],
                         path_lines=[_way('footway', [(0.0, 30.0), (127.0, 30.0)], half=1.0, seed=2)],
                         land_classes=_strip_classes('park', z0=0, z1=127))
        inferred = [i for i in plan['items'] if (i.get('provenance') or {}).get('source') == 'inference'
                    and i.get('role') == 'furniture']
        self.assertTrue(inferred)
        self.assertEqual({i['kind'] for i in inferred} & set(sf.FOOTPRINTS), set())


def _way(highway, points, half=3.0, **extra):
    return {'highway': highway, 'points': points, 'half': half, **extra}


def _strip_classes(cls, z0=40, z1=90, x0=0, x1=127):
    return {(x, z): cls for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)}


class TestInferredStreetFurniture(unittest.TestCase):
    """#260 (I-008): labelled lamp and bench inference on synthetic road and path strips, cell (0, 0)."""

    ROAD = [(0.0, 64.0), (127.0, 64.0)]

    def plan(self, roads=(), paths=(), classes=None, profile=None, **kw):
        return dp.build_decor_plan(
            (0, 0), map_barriers={}, garden=[], rgb={}, footprint=set(), canopy=set(),
            road_lines=list(roads), path_lines=list(paths),
            land_classes=_strip_classes('residential') if classes is None else classes,
            district_profile={} if profile is None else profile, **kw)

    @staticmethod
    def inferred(plan, kind=None):
        return [i for i in plan['items'] if i.get('role') == 'furniture'
                and i['provenance']['source'] == 'inference' and kind in (None, i['kind'])]

    def test_lamps_are_labelled_inference_low_and_never_observed(self):
        plan = self.plan(roads=[_way('residential', self.ROAD, seed=3)])
        lamps = self.inferred(plan, 'street_lamp')
        self.assertGreaterEqual(len(lamps), 3)
        for lamp in lamps:
            self.assertEqual((lamp['provenance']['source'], lamp['provenance']['confidence']), ('inference', 'low'))
            self.assertIn('not observed', lamp['note'])
            self.assertFalse(prov.is_unresolved(lamp['provenance']))
        self.assertEqual({r['source'] for r in dp.resolved_furniture(plan)}, {'inference'})
        summary = plan['furniture_inference']
        self.assertEqual(summary['lamps'], len(lamps))
        self.assertEqual(summary['spacing_layers'], {'class': 1})
        self.assertEqual(summary['lit_tags'], {'absent': 1})

    def test_no_ways_means_no_inference_block_and_no_extra_items(self):
        plan = self.plan()
        self.assertNotIn('furniture_inference', plan)
        self.assertEqual(plan['items'], [])

    def test_spacing_follows_the_highway_class(self):
        counts = {highway: sum(len(self.inferred(self.plan(roads=[_way(highway, self.ROAD, seed=seed)])))
                               for seed in range(12))
                  for highway in ('residential', 'tertiary', 'trunk')}      # 30, 34 and 40 blocks apart
        self.assertGreater(counts['residential'], counts['tertiary'])
        self.assertGreater(counts['tertiary'], counts['trunk'])
        for highway in ('service', 'cycleway', 'steps'):               # no table row: unlit
            plan = self.plan(roads=[_way(highway, self.ROAD)])
            self.assertEqual(self.inferred(plan), [])
            self.assertEqual(plan['furniture_inference']['ways_skipped'],
                             {'no lamp spacing for this way (class, highway or lit=no)': 1})

    def test_district_profile_spacing_beats_the_table(self):
        way = _way('residential', self.ROAD, seed=3)
        base = self.plan(roads=[way])
        tight = self.plan(roads=[way], profile={
            'name': 'x', 'evidence_ids': [], 'classes': {'residential': {'furniture': {'lamp_spacing': {'residential': 12}}}}})
        self.assertGreater(len(self.inferred(tight)), len(self.inferred(base)))
        self.assertEqual(tight['furniture_inference']['spacing_layers'], {'district': 1})
        none = self.plan(roads=[way], profile={
            'name': 'x', 'evidence_ids': [], 'classes': {'residential': {'furniture': {'lamp_spacing': {'residential': None}}}}})
        self.assertEqual(self.inferred(none), [])                      # an explicit null stops inference
        self.assertEqual(list(none['furniture_inference']['ways_skipped'].values()), [1])

    def test_class_that_allows_no_inference_or_is_unknown_gets_no_lamps(self):
        for cls in ('rural', 'natural', 'unknown'):
            plan = self.plan(roads=[_way('residential', self.ROAD)], classes=_strip_classes(cls))
            self.assertEqual(self.inferred(plan), [], cls)
        self.assertEqual(self.inferred(self.plan(roads=[_way('residential', self.ROAD)], classes={})), [])

    def test_lit_tag_is_used_when_present_and_counted_when_absent(self):
        plan = self.plan(roads=[_way('residential', self.ROAD, lit='no'),
                                _way('cycleway', [(0.0, 80.0), (127.0, 80.0)], half=1.2, lit='yes'),
                                _way('residential', [(0.0, 50.0), (127.0, 50.0)])])
        self.assertEqual(plan['furniture_inference']['lit_tags'], {'no': 1, 'yes': 1, 'absent': 1})
        zs = {i['cell'][1] for i in self.inferred(plan, 'street_lamp')}
        self.assertTrue(all(z > 70 or z < 55 for z in zs))             # none beside the lit=no road at z=64
        self.assertTrue(any(z > 70 for z in zs))                       # the lit=yes cycleway got lamps

    def test_sidewalk_footways_leave_lamps_to_the_road(self):
        plan = self.plan(paths=[_way('footway', self.ROAD, half=1.0, footway='sidewalk')])
        self.assertEqual(self.inferred(plan), [])

    def test_a_mapped_point_wins_nearby_and_far_lamps_still_fill(self):
        way = _way('residential', self.ROAD, seed=3)
        free = self.inferred(self.plan(roads=[way]), 'street_lamp')
        target = free[len(free) // 2]['cell']
        plan = self.plan(roads=[way], map_furniture=[(target[0] + 2, target[1], 'street_lamp')])
        lamps = self.inferred(plan, 'street_lamp')
        self.assertFalse(any(abs(l['cell'][0] - target[0]) < 22 for l in lamps))
        self.assertTrue(any(i['provenance']['source'] == 'vector_map' for i in plan['items']))
        self.assertGreater(len(lamps), 0)
        self.assertGreaterEqual(plan['furniture_inference']['dropped']['mapped or designed item here or nearby'], 1)

    def test_a_designer_trace_wins_like_a_mapped_point(self):
        way = _way('residential', self.ROAD, seed=3)
        target = self.inferred(self.plan(roads=[way]), 'street_lamp')[1]['cell']
        plan = self.plan(roads=[way], designer_items=[
            {'x': target[0], 'z': target[1], 'kind': 'street_lamp', 'source': 'designer'}])
        self.assertEqual([i['cell'] for i in plan['items'] if i['cell'] == target
                          and i['provenance']['source'] != 'inference'], [target])
        self.assertFalse(any(abs(l['cell'][0] - target[0]) < 22 for l in self.inferred(plan, 'street_lamp')))
        # a designer bench does not suppress lamps, only an item at the same column does
        bench = self.plan(roads=[way], designer_items=[
            {'x': target[0] + 10, 'z': target[1], 'kind': 'bench', 'source': 'designer'}])
        self.assertEqual(len(self.inferred(bench, 'street_lamp')), len(self.inferred(self.plan(roads=[way]))))

    def test_nothing_on_routes_openings_or_the_carriageway(self):
        way = _way('residential', self.ROAD, seed=3)
        free = self.inferred(self.plan(roads=[way]), 'street_lamp')
        victim = free[0]['cell']
        for kwargs in ({'openings': {(victim[0], victim[1])}},                 # entrance opening
                       {'openings': {(victim[0] + 1, victim[1] + 1)}},         # clearance of one column
                       {'carriageway': {(victim[0], victim[1])}},
                       {'furniture_avoid': {(victim[0], victim[1])}}):
            plan = self.plan(roads=[way], **kwargs)
            self.assertNotIn(victim, [i['cell'] for i in self.inferred(plan)], kwargs)
            self.assertEqual(len(self.inferred(plan)), len(free) - 1, kwargs)
        plan = self.plan(roads=[way], openings={(victim[0], victim[1])})
        self.assertIn('door route or entrance opening', plan['furniture_inference']['dropped'])

    def test_door_route_from_frontages_is_kept_clear(self):
        way = _way('residential', self.ROAD, seed=3)
        victim = self.inferred(self.plan(roads=[way]), 'street_lamp')[0]['cell']
        frontage = {'id': 'f1', 'route_reserved': [list(victim)]}
        with mock.patch('garden_sources.reserved_route', return_value={tuple(victim)}):
            plan = self.plan(roads=[way], frontages=[frontage])
        self.assertNotIn(victim, [i['cell'] for i in self.inferred(plan)])

    def test_outside_the_cell_is_dropped_and_counted(self):
        plan = self.plan(roads=[_way('residential', [(-40.0, 64.0), (170.0, 64.0)], seed=3)])
        self.assertTrue(all(0 <= i['cell'][0] <= 127 for i in self.inferred(plan)))
        self.assertGreater(plan['furniture_inference']['dropped']['outside cell'], 0)

    def test_two_ways_do_not_double_up_lamps_at_a_junction(self):
        plan = self.plan(roads=[_way('residential', self.ROAD, seed=3), _way('residential', self.ROAD, seed=3)])
        cells = [tuple(i['cell']) for i in self.inferred(plan)]
        self.assertEqual(len(cells), len(set(cells)))
        self.assertEqual(len(cells), len(self.inferred(self.plan(roads=[_way('residential', self.ROAD, seed=3)]))))

    def test_benches_only_on_paths_beside_park_land(self):
        path = _way('footway', [(0.0, 64.0), (127.0, 64.0)], half=1.0, lit='no')
        park = self.plan(paths=[path], classes=_strip_classes('park'))
        benches = self.inferred(park, 'bench')
        self.assertGreaterEqual(len(benches), 1)
        for bench in benches:
            self.assertEqual((bench['provenance']['source'], bench['provenance']['confidence']), ('inference', 'low'))
            self.assertIn('not observed', bench['note'])
        for cls in ('residential', 'civic', 'commercial', 'rural', 'unknown'):
            plan = self.plan(paths=[path], classes=_strip_classes(cls))
            self.assertEqual(self.inferred(plan, 'bench'), [], cls)
        self.assertIn('no bench density for the adjoining class',
                      self.plan(paths=[path], classes=_strip_classes('residential'))['furniture_inference']['ways_skipped'])
        for kind in ('cycleway', 'service', 'steps'):                  # not walking paths
            self.assertEqual(self.inferred(self.plan(paths=[_way(kind, path['points'], half=1.0)],
                                                     classes=_strip_classes('park')), 'bench'), [])

    def test_bench_density_can_come_from_a_profile_and_a_mapped_bench_wins(self):
        path = _way('path', [(0.0, 64.0), (127.0, 64.0)], half=0.8, lit='no', seed=5)
        dense = {'name': 'x', 'evidence_ids': [], 'classes': {'park': {'furniture': {'bench_per_100m': 10}}}}
        base = self.inferred(self.plan(paths=[path], classes=_strip_classes('park')), 'bench')
        plan = self.plan(paths=[path], classes=_strip_classes('park'), profile=dense)
        many = self.inferred(plan, 'bench')
        self.assertGreater(len(many), len(base))
        mapped = self.plan(paths=[path], classes=_strip_classes('park'), profile=dense,
                           map_furniture=[(many[0]['cell'][0], many[0]['cell'][1], 'bench')])
        self.assertFalse(any(abs(b['cell'][0] - many[0]['cell'][0]) < 8 and b['cell'][1] == many[0]['cell'][1]
                             for b in self.inferred(mapped, 'bench')))

    def test_benches_stay_off_routes_and_paths(self):
        path = _way('path', [(0.0, 64.0), (127.0, 64.0)], half=0.8, lit='no', seed=5)
        dense = {'name': 'x', 'evidence_ids': [], 'classes': {'park': {'furniture': {'bench_per_100m': 10}}}}
        free = self.inferred(self.plan(paths=[path], classes=_strip_classes('park'), profile=dense), 'bench')
        victim = tuple(free[0]['cell'])
        plan = self.plan(paths=[path], classes=_strip_classes('park'), profile=dense, openings={victim})
        self.assertNotIn(victim, [tuple(i['cell']) for i in self.inferred(plan)])

    def test_a_way_crossing_two_cells_has_one_phase(self):
        way = _way('residential', [(0.0, 64.0), (255.0, 64.0)])          # no explicit seed: derived from the way
        classes = _strip_classes('residential', x1=255)
        left = self.inferred(self.plan(roads=[way], classes=classes), 'street_lamp')
        right = [i for i in self.inferred(dp.build_decor_plan(
            (1, 0), map_barriers={}, garden=[], rgb={}, footprint=set(), canopy=set(), road_lines=[way],
            land_classes=classes, district_profile={}), 'street_lamp')]
        self.assertTrue(left and right)
        whole = {tuple(i['cell']) for i in left} | {tuple(i['cell']) for i in right}
        import street_furniture as sf
        seed = sf.stable_seed('residential', 0, 64)
        expected = {(i['x'], i['z']) for i in sf.lamp_positions(
            way['points'], 'residential', {'residential': 30}, seed, half_width=3.0)}
        self.assertEqual(whole, expected)                                   # cell edges change nothing
        xs = sorted(x for x, _ in whole)
        self.assertTrue(all(b - a <= 31 for a, b in zip(xs, xs[1:])), xs)   # no doubled gap at x=127/128

    def test_barrier_columns_are_never_built_over(self):
        way = _way('residential', self.ROAD, seed=3)
        victim = self.inferred(self.plan(roads=[way]), 'street_lamp')[0]['cell']
        for kwargs in ({'map_barriers': {'fence': {tuple(victim)}}},
                       {'designer_items': [{'kind': 'hedge', 'cells': [list(victim)]}]},
                       {'garden': [{'id': 'g', 'role': 'barrier', 'kind': 'hedge', 'cells': [list(victim)],
                                    'provenance': {'source': 'inference', 'confidence': 'medium',
                                                   'evidence_ids': [], 'unresolved_reason': ''}}]}):
            args = dict(map_barriers={}, garden=[], rgb={}, footprint=set(), canopy=set(), road_lines=[way],
                        land_classes=_strip_classes('residential'), district_profile={})
            args.update(kwargs)
            plan = dp.build_decor_plan((0, 0), **args)
            self.assertNotIn(victim, [i['cell'] for i in self.inferred(plan)], list(kwargs))
            self.assertIn('barrier column', plan['furniture_inference']['dropped'], list(kwargs))

    def test_inference_is_deterministic(self):
        way = _way('residential', self.ROAD)
        self.assertEqual(self.plan(roads=[way])['items'], self.plan(roads=[way])['items'])


class TestAdjoiningClass(unittest.TestCase):
    ROAD = [(0.0, 64.0), (100.0, 64.0)]

    def test_most_common_class_within_reach_wins_and_ties_sort_first(self):
        classes = {**_strip_classes('park', 40, 63, 0, 100), **_strip_classes('residential', 66, 70, 0, 100)}
        self.assertEqual(dp.adjoining_class(self.ROAD, 3.0, classes), 'park')
        tie = {**_strip_classes('park', 60, 63, 0, 100), **_strip_classes('civic', 65, 68, 0, 100)}
        self.assertEqual(dp.adjoining_class(self.ROAD, 3.0, tie), 'civic')

    def test_nothing_classified_nearby_is_unknown(self):
        self.assertEqual(dp.adjoining_class(self.ROAD, 3.0, {}), 'unknown')
        self.assertEqual(dp.adjoining_class(self.ROAD, 3.0, _strip_classes('park', 0, 20, 0, 100)), 'unknown')


class TestLandClassColumns(unittest.TestCase):
    """Area polygons and buildings become a column to class map; unknown claims nothing."""

    class Idx:
        def __init__(self, areas, buildings):
            self.areas, self.buildings = areas, buildings

        def cells(self):
            return {(x, z): 0 for x in range(20, 24) for z in range(20, 24)} if self.buildings else {}

        def layer_covered(self, layer, x, z):
            return True

    def test_park_polygon_and_a_house_over_it(self):
        ring = lambda x0, z0, x1, z1: [[(x0, z0), (x1, z0), (x1, z1), (x0, z1), (x0, z0)]]
        idx = self.Idx(areas=[(ring(0, 0, 40, 40), {'leisure': 'park', 'osm_way_id': 1}),
                              (ring(60, 60, 70, 70), {'landuse': 'military', 'osm_way_id': 2})],
                       buildings=[(ring(20, 20, 24, 24), {'building': 'retail', 'osm_way_id': 3})])
        cols = dp.land_class_columns(idx, (0, 0, 80, 80))
        self.assertEqual(cols[(5, 5)], 'park')
        self.assertEqual(cols[(21, 21)], 'commercial')            # the building's own class
        self.assertNotIn((65, 65), cols)                          # unlisted value claims nothing
        self.assertNotIn((50, 50), cols)

    def test_park_is_an_umbrella_over_nested_meadow_and_wood(self):
        """#322: a path through meadow or beside wood inside a park polygon still takes park furniture."""
        ring = lambda x0, z0, x1, z1: [[(x0, z0), (x1, z0), (x1, z1), (x0, z1), (x0, z0)]]
        idx = self.Idx(areas=[(ring(0, 0, 80, 80), {'leisure': 'park', 'osm_way_id': 1}),
                              (ring(10, 10, 30, 30), {'landuse': 'meadow', 'osm_way_id': 2}),
                              (ring(40, 40, 60, 60), {'natural': 'wood', 'osm_way_id': 3}),
                              (ring(60, 10, 70, 20), {'leisure': 'nature_reserve', 'osm_way_id': 4}),
                              (ring(200, 0, 220, 20), {'landuse': 'meadow', 'osm_way_id': 5})],
                       buildings=[])
        cols = dp.land_class_columns(idx, (0, 0, 230, 80))
        self.assertEqual(cols[(20, 20)], 'park')                  # meadow nested in the park
        self.assertEqual(cols[(50, 50)], 'park')                  # wood is cover: the park use polygon holds it
        self.assertEqual(cols[(65, 15)], 'park')                  # nested reserve
        self.assertEqual(cols[(210, 10)], 'rural')                # meadow outside any park stays rural

    def test_grass_cover_and_other_classes_are_not_an_umbrella(self):
        ring = lambda x0, z0, x1, z1: [[(x0, z0), (x1, z0), (x1, z1), (x0, z1), (x0, z0)]]
        idx = self.Idx(areas=[(ring(0, 0, 80, 80), {'landuse': 'grass', 'osm_way_id': 1}),
                              (ring(10, 10, 30, 30), {'landuse': 'meadow', 'osm_way_id': 2}),
                              (ring(100, 0, 180, 80), {'landuse': 'residential', 'osm_way_id': 3}),
                              (ring(110, 10, 130, 30), {'landuse': 'orchard', 'osm_way_id': 4})],
                       buildings=[])
        cols = dp.land_class_columns(idx, (0, 0, 200, 80))
        self.assertEqual(cols[(20, 20)], 'rural')                 # grass cover is no umbrella
        self.assertEqual(cols[(120, 20)], 'rural')                # nor is a residential polygon

    def test_a_footway_through_nested_meadow_in_a_park_gets_benches(self):
        ring = lambda x0, z0, x1, z1: [[(x0, z0), (x1, z0), (x1, z1), (x0, z1), (x0, z0)]]
        meadow = (ring(10, 50, 120, 80), {'landuse': 'meadow', 'osm_way_id': 2})
        inside = self.Idx(areas=[(ring(0, 0, 127, 127), {'leisure': 'park', 'osm_way_id': 1}), meadow], buildings=[])
        alone = self.Idx(areas=[meadow], buildings=[])
        path = _way('footway', [(12.0, 64.0), (115.0, 64.0)], half=1.0, lit='no')
        results = {}
        for name, idx in (('in park', inside), ('alone', alone)):
            plan = dp.build_decor_plan(
                (0, 0), map_barriers={}, garden=[], rgb={}, footprint=set(), canopy=set(), path_lines=[path],
                land_classes=dp.land_class_columns(idx, (0, 0, 127, 127)), district_profile={})
            results[name] = plan
        benches = [i for i in results['in park']['items'] if i['kind'] == 'bench']
        self.assertGreaterEqual(len(benches), 1)
        self.assertNotIn('no bench density for the adjoining class',
                         results['in park']['furniture_inference']['ways_skipped'])
        self.assertIn('no bench density for the adjoining class',        # the same meadow outside a park: none
                      results['alone']['furniture_inference']['ways_skipped'])


class TestUnresolvedBarrierReporting(unittest.TestCase):
    def barrier(self, item_id='boundary', reason='', **extra):
        return prov.attach_provenance(
            {'id': item_id, 'role': 'barrier', 'kind': 'hedge',
             'cells': [[10, 20], [11, 20]], 'height': 1, **extra},
            source='inference', confidence='medium', evidence_ids=['synthetic-boundary'],
            unresolved_reason=reason)

    def test_unresolved_barrier_is_skipped_and_reported_without_changing_plan(self):
        item = self.barrier(reason='boundary height needs confirmation')
        original = copy.deepcopy(item)
        unplaced = []
        self.assertEqual(dp.resolved_barriers({'items': [item]}, unplaced), {})
        self.assertEqual(len(unplaced), 1)
        self.assertEqual(unplaced[0]['id'], item['id'])
        self.assertEqual(unplaced[0]['cells'], item['cells'])
        self.assertEqual(unplaced[0]['provenance'], {
            **item['provenance'], 'unresolved_reason':
            'barrier skipped: provenance carries an unresolved reason: boundary height needs confirmation'})
        self.assertEqual(item, original)
        self.assertEqual(dp.resolved_barriers({'items': [item]}), {})

    def test_resolved_barrier_is_placed_and_not_reported(self):
        from decor_layer import place_barriers_from_plan

        item = self.barrier()
        unplaced = []
        self.assertEqual(dp.resolved_barriers({'items': [item]}, unplaced), {
            (10, 20): ('hedge', dp.HEDGE_BLOCK, 1),
            (11, 20): ('hedge', dp.HEDGE_BLOCK, 1)})
        self.assertEqual(unplaced, [])
        world = mock.Mock()
        world.block.return_value = 'minecraft:air'
        with mock.patch('surface_probe.surface_y', return_value=(64, 'grass_block')), \
                mock.patch('decor_layer.on_carriageway', return_value=False):
            found, report, unplaced = place_barriers_from_plan(
                (0, 0), [item], world, way=set(), near1=set(), owned=set(), masked={})
        self.assertEqual(found, {(10, 65, 20): dp.HEDGE_BLOCK, (11, 65, 20): dp.HEDGE_BLOCK})
        self.assertEqual(len(report), 2)
        self.assertEqual(unplaced, [])
        with redirect_stdout(io.StringIO()) as output:
            dp.print_unresolved_barriers([item])
        self.assertEqual(output.getvalue(), '')

    def test_none_opening_and_unresolved_furniture_are_not_reported_as_barriers(self):
        opening = self.barrier('opening', reason='deliberate open frontage', kind='none')
        furniture = self.barrier('bench', reason='coordinate missing', role='furniture', kind='bench')
        items = [opening, furniture]
        unplaced = []
        self.assertEqual(dp.resolved_barriers({'items': items}, unplaced), {})
        self.assertEqual(unplaced, [])
        with redirect_stdout(io.StringIO()) as output:
            dp.print_unresolved_barriers(items)
        self.assertEqual(output.getvalue(), '')

    def test_summary_counts_runs_instead_of_columns_and_bounds_ids(self):
        skipped = [self.barrier(f'boundary-{n}', reason='height unconfirmed') for n in range(8)]
        items = [self.barrier('resolved'), *skipped,
                 self.barrier('opening', reason='deliberate opening', kind='none'),
                 self.barrier('bench', reason='coordinate missing', role='furniture', kind='bench')]
        unplaced = []
        dp.resolved_barriers({'items': items}, unplaced)
        self.assertEqual([i['id'] for i in unplaced], [i['id'] for i in skipped])
        with redirect_stdout(io.StringIO()) as output:
            dp.print_unresolved_barriers(items)
        self.assertEqual(output.getvalue(),
                         '  8 barrier runs skipped as unresolved: '
                         'boundary-0, boundary-1, boundary-2, boundary-3, boundary-4, ...\n')

    def test_decor_report_prints_warning_and_saves_original_reason(self):
        import decor_layer as dl
        from layer_reports import write_report

        item = self.barrier(reason='boundary height needs confirmation')
        stats = {'blocks': 0, 'unresolved_items': [item], 'unplaced_items': []}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(dl, 'plan_cell', return_value=({}, stats)), \
                mock.patch('site_kit.fresh_planning_world'), \
                mock.patch('layer_reports.write_report',
                           side_effect=lambda n, r, **kw: write_report(n, r, Path(tmp), **kw)), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(dl.run((0, 0)), 0)
            saved = json.loads((Path(tmp) / 'highfield_0_0_decor-dry-report.json').read_text())
        self.assertEqual(saved['stats']['unresolved_items'], [item])
        self.assertEqual(output.getvalue().count('1 barrier runs skipped as unresolved: boundary'), 1)


class TestLidarFurniture(unittest.TestCase):
    """Photo/aerial labels identify lidar objects; lidar alone never identifies cars."""

    def plan(self, observations, *, footprint=None, canopy=None, map_furniture=()):
        import numpy as np
        hm = {'x0': 0, 'z0': 0, 'terrain': np.full((16, 16), 64),
              'h': np.full((16, 16), 64), 'kind': np.zeros((16, 16))}
        for x, z in ((4, 4), (4, 5), (10, 10)):
            hm['h'][x, z] = 65
        return dp.build_decor_plan(
            (0, 0), map_barriers={}, map_furniture=map_furniture,
            garden={}, rgb={}, footprint=footprint or set(),
            canopy=canopy or set(), lidar_hm=hm, photo_observations=observations,
        )

    def test_photo_furniture_places_one_recipe_per_object_and_vehicle_does_not(self):
        from decor_layer import place_furniture
        plan = self.plan([
            {'kind': 'waste_basket', 'cell': [4, 4], 'evidence_ids': ['photo1']},
            {'kind': 'vehicle', 'cell': [10, 10], 'evidence_ids': ['photo1']},
        ])
        rows = dp.resolved_furniture(plan)
        self.assertEqual([(r['x'], r['z'], r['kind']) for r in rows],
                         [(4, 4, 'waste_basket')])
        world = mock.Mock()
        world.block.return_value = 'minecraft:air'
        with mock.patch('decor_layer.column_ground_kind', return_value='paving'), \
                mock.patch('surface_probe.surface_y', return_value=(64, 'stone')):
            placed, _, _ = place_furniture(
                (0, 0), rows, world, way=set(), near1=set(), owned=set(), masked=set())
        self.assertEqual(placed, {(4, 65, 4): 'cauldron'})

    def test_unlabelled_or_conflicting_object_never_places(self):
        for observations in ([], [
            {'kind': 'bench', 'cell': [4, 4]},
            {'kind': 'vehicle', 'cell': [4, 5]},
        ]):
            plan = self.plan(observations)
            self.assertEqual(dp.resolved_furniture(plan), [])
            self.assertEqual(len(dp.unresolved_items(plan)), 1 if observations else 0)

    def test_vehicle_label_overrides_generic_map_furniture(self):
        plan = self.plan([{'kind': 'vehicle', 'cell': [4, 4]}],
                         map_furniture=[(4, 5, 'bench')])
        self.assertEqual(dp.resolved_furniture(plan), [])

    def test_footprints_and_tree_canopy_excluded(self):
        # Labels on excluded columns do not turn them into lidar objects.
        plan = self.plan([{'kind': 'bench', 'cell': [4, 4]}, {'kind': 'bench', 'cell': [10, 10]}],
                         footprint={(4, 4), (4, 5)}, canopy={(10, 10)})
        self.assertEqual([i for i in plan['items'] if str(i['id']).startswith('lidar-object')], [])

    def test_aerial_label_resolves_and_other_sources_do_not(self):
        for source, count in [('aerial', 1), ('vector_map', 0)]:
            plan = self.plan([{'kind': 'bollard', 'cell': [4, 4], 'source': source}])
            self.assertEqual(len(dp.resolved_furniture(plan)), count)


class TestPlaceRespectsBuildingBorder(unittest.TestCase):
    """Building-border hedges may sit in the near1 ring; other barriers may not."""

    def test_allow_against_build_skips_near1(self):
        from decor_layer import place_barriers_from_plan

        surface_y = 64
        live = {}
        for x, z in ((10, 20), (11, 20)):
            live[(x, surface_y, z)] = 'minecraft:grass_block'
            live[(x, surface_y + 1, z)] = 'minecraft:air'
            live[(x, surface_y + 2, z)] = 'minecraft:air'

        world = mock.Mock()
        world.block = lambda x, y, z: live.get((x, y, z), 'minecraft:air')

        items = [
            {'kind': 'hedge', 'role': 'barrier', 'cells': [[10, 20]],
             'block': 'oak_leaves[persistent=true]', 'height': 2,
             'allow_against_build': True,
             'provenance': {'source': 'aerial', 'confidence': 'medium',
                            'evidence_ids': [], 'unresolved_reason': ''}},
            {'kind': 'hedge', 'role': 'barrier', 'cells': [[11, 20]],
             'block': 'oak_leaves[persistent=true]', 'height': 2,
             'allow_against_build': False,
             'provenance': {'source': 'vector_map', 'confidence': 'high',
                            'evidence_ids': [], 'unresolved_reason': ''}},
        ]
        near1 = {(10, 20), (11, 20)}  # both against a build
        with mock.patch('surface_probe.surface_y',
                        side_effect=lambda w, x, z: (surface_y, 'grass_block')), \
                mock.patch('decor_layer.on_carriageway', return_value=False):
            found, report, unplaced = place_barriers_from_plan(
                (0, 0), items, world,
                way=set(), near1=near1, owned=set(), masked=set(),
            )
        self.assertIn((10, surface_y + 1, 20), found)
        self.assertNotIn((11, surface_y + 1, 20), found)
        self.assertEqual(len(unplaced), 1)
        self.assertIn('against build', unplaced[0]['provenance']['unresolved_reason'])


if __name__ == '__main__':
    unittest.main()


class TestReserveOpenings(unittest.TestCase):
    """Entrance gaps stay clear of inferred barriers; mapped boundaries are kept and reported (#240)."""

    def _item(self, iid, source, cells):
        return {'id': iid, 'role': 'barrier', 'kind': 'fence', 'cells': [list(c) for c in cells],
                'provenance': {'source': source, 'confidence': 'medium', 'evidence_ids': [],
                               'unresolved_reason': ''}}

    def test_mapped_boundary_across_widened_opening_is_listed(self):
        out = dp.reserve_openings([self._item('m', 'vector_map', [(2, 3), (3, 3), (4, 3), (5, 3)])],
                                  {(3, 3), (4, 3), (5, 3)})
        self.assertEqual(out['conflicts'][0]['cells'], [[3, 3], [4, 3], [5, 3]])
        self.assertEqual(len(out['items'][0]['cells']), 4)

    def test_inferred_barrier_loses_opening_columns(self):
        out = dp.reserve_openings([self._item('g', 'register_entry', [(0, 3), (1, 3), (2, 3)])], {(1, 3)})
        self.assertEqual(out['items'][0]['cells'], [[0, 3], [2, 3]])
        self.assertEqual(out['conflicts'][0]['cells'], [[1, 3]])

    def test_emptied_item_is_dropped(self):
        out = dp.reserve_openings([self._item('g', 'aerial', [(1, 3)])], {(1, 3)})
        self.assertEqual(out['items'], [])

    def test_mapped_boundary_kept_and_explained(self):
        item = self._item('m', 'vector_map', [(0, 3), (1, 3)])
        out = dp.reserve_openings([item], {(1, 3)})
        self.assertEqual(out['items'], [item])
        self.assertIn('explicit observation', out['conflicts'][0]['reason'])

    def test_non_barrier_untouched(self):
        item = {'id': 'f', 'role': 'furniture', 'cells': [[1, 3]]}
        self.assertEqual(dp.reserve_openings([item], {(1, 3)})['items'], [item])


class GardenProvenanceTransportTest(unittest.TestCase):
    def test_identical_hedges_retain_separate_property_evidence_in_decor(self):
        garden = []
        for number, source, x in (('1', 'inference', 20), ('3', 'register_entry', 21)):
            garden.append({'id': 'property-' + number, 'role': 'barrier', 'kind': 'hedge',
                           'cells': [[x, 20]], 'block': 'oak_leaves[persistent=true]', 'height': 2,
                           'scope': {'street': 'Test Road', 'property': number},
                           'reason': 'street convention' if source == 'inference' else 'property observation',
                           'provenance': {'source': source, 'confidence': 'medium',
                                          'evidence_ids': ['evidence-' + number], 'unresolved_reason': ''}})
        plan = dp.build_decor_plan((0, 0), map_barriers={}, garden=garden,
                                   rgb={}, footprint=set(), canopy=set())
        rows = [i for i in plan['items'] if i['id'].startswith('property-')]
        self.assertEqual(rows, garden)
        self.assertEqual([i['provenance']['source'] for i in rows], ['inference', 'register_entry'])


class TestConventionFenceReachesPlacement(unittest.TestCase):
    """#271: a convention fence is resolved, and an unknown barrier kind is reported."""

    @staticmethod
    def _garden(kind, block, height):
        return [{'id': 'garden-0-front', 'role': 'barrier', 'kind': kind, 'block': block,
                 'height': height, 'cells': [[5, 8], [6, 8]],
                 'provenance': {'source': 'inference', 'confidence': 'medium',
                                'evidence_ids': ['e'], 'unresolved_reason': ''}}]

    def _plan(self, garden):
        return dp.build_decor_plan((0, 0), map_barriers={}, garden=garden,
                                   rgb={}, footprint=set(), canopy=set())

    def test_plank_fence_resolves_with_block_and_height(self):
        plan = self._plan(self._garden('plank_fence', 'oak_planks', 1))
        self.assertEqual(dp.resolved_barriers(plan)[(5, 8)], ('plank_fence', 'oak_planks', 1))

    def test_plank_fence_defaults_to_oak_one_high(self):
        plan = {'items': [{'role': 'barrier', 'kind': 'plank_fence', 'cells': [[1, 1]]}]}
        self.assertEqual(dp.resolved_barriers(plan)[(1, 1)], ('plank_fence', 'oak_planks', 1))

    def test_unknown_kind_is_reported_unplaced_with_reason(self):
        plan = self._plan(self._garden('picket', 'oak_planks', 1))
        row = next(i for i in plan['items'] if i['id'] == 'garden-0-front')
        self.assertIn('unknown barrier kind', row['provenance']['unresolved_reason'])
        self.assertEqual(dp.resolved_barriers(plan), {})
        self.assertIn(row, dp.unresolved_items(plan))

    def test_resolved_barriers_reports_unknown_kind(self):
        plan = {'items': [{'role': 'barrier', 'kind': 'picket', 'cells': [[1, 1]],
                           'provenance': {'source': 'inference', 'confidence': 'medium',
                                          'evidence_ids': ['e'], 'unresolved_reason': ''}}]}
        lost: list = []
        self.assertEqual(dp.resolved_barriers(plan, lost), {})
        self.assertIn('unknown barrier kind', lost[0]['provenance']['unresolved_reason'])

    def test_barrier_kind_and_tone_table(self):
        self.assertEqual(dp.barrier_kind('fence', 'minecraft:spruce_planks'), 'plank_fence')
        self.assertEqual(dp.barrier_kind('fence', None), 'spruce_fence')
        self.assertEqual(dp.barrier_kind('picket'), 'picket')
        self.assertEqual(dp.PLANK_TONES['oak'], ('oak_planks',))
        self.assertIn('mangrove_planks', dp.PLANK_TONES['reddy'])


class TestShelfBlock(unittest.TestCase):
    """#272: plank-style panels are wooden shelves facing out; no outward evidence keeps the solid plank."""

    def test_shelf_state_syntax_and_wood_follow_the_tone(self):
        self.assertEqual(dp.shelf_block('oak_planks', 'north'), 'oak_shelf[facing=north]')
        self.assertEqual(dp.shelf_block('minecraft:pale_oak_planks', 'east'), 'pale_oak_shelf[facing=east]')
        self.assertEqual(dp.shelf_block('dark_oak_planks', 'south'), 'dark_oak_shelf[facing=south]')
        self.assertEqual(dp.shelf_block('birch_planks', 'west'), 'birch_shelf[facing=west]')

    def test_every_tone_plank_has_a_shelf_variant(self):
        shelves = {'oak', 'spruce', 'dark_oak', 'jungle', 'acacia', 'mangrove', 'birch', 'pale_oak'}
        for blocks in dp.PLANK_TONES.values():
            for b in blocks:
                self.assertIn(b.removesuffix('_planks'), shelves)
                self.assertIn('_shelf[facing=north]', dp.shelf_block(b, 'north'))

    def test_no_outward_facing_keeps_the_solid_plank(self):
        self.assertEqual(dp.shelf_block('oak_planks', None), 'oak_planks')
        self.assertEqual(dp.shelf_block('oak_planks', 'up'), 'oak_planks')

    def test_pale_oak_is_a_plank_tone(self):
        self.assertEqual(dp.barrier_kind('fence', 'pale_oak_planks'), 'plank_fence')

    def test_facing_survives_planning(self):
        garden = [{'id': 'garden-0-front', 'role': 'barrier', 'kind': 'plank_fence', 'block': 'oak_planks',
                   'height': 1, 'cells': [[5, 8], [6, 8]], 'facing': {'5,8': 'north', '6,8': 'north'},
                   'facing_basis': {'5,8': 'street', '6,8': 'street'},
                   'provenance': {'source': 'inference', 'confidence': 'medium',
                                  'evidence_ids': ['e'], 'unresolved_reason': ''}}]
        plan = dp.build_decor_plan((0, 0), map_barriers={}, garden=garden, rgb={}, footprint=set(), canopy=set())
        item = [i for i in plan['items'] if i['id'] == 'garden-0-front'][0]
        self.assertEqual(item['facing']['5,8'], 'north')


class TestPanelVersusPicketKinds(unittest.TestCase):
    """#272: only close-boarded panels (plank block) become shelves; other fences keep the fence block."""

    @staticmethod
    def _plan(kind, block):
        item = {'id': 'g', 'role': 'barrier', 'kind': kind, 'block': block, 'height': 1,
                'cells': [[5, 8]], 'facing': {'5,8': 'north'},
                'provenance': {'source': 'inference', 'confidence': 'medium',
                               'evidence_ids': ['e'], 'unresolved_reason': ''}}
        return dp.build_decor_plan((0, 0), map_barriers={}, garden=[item], rgb={}, footprint=set(), canopy=set())

    def test_convention_author_choice(self):
        self.assertEqual(dp.barrier_kind('fence', 'oak_planks'), 'plank_fence')       # panel
        self.assertEqual(dp.barrier_kind('fence', 'pale_oak_planks'), 'plank_fence')
        self.assertEqual(dp.barrier_kind('fence', None), 'spruce_fence')              # picket / slatted
        self.assertEqual(dp.barrier_kind('fence', 'oak_fence'), 'spruce_fence')
        self.assertEqual(dp.barrier_kind('fence', 'spruce_fence'), 'spruce_fence')

    def test_panel_and_picket_resolve_independently(self):
        panel = dp.resolved_barriers(self._plan('plank_fence', 'oak_planks'))[(5, 8)]
        picket = dp.resolved_barriers(self._plan('spruce_fence', 'oak_fence'))[(5, 8)]
        self.assertEqual(panel[:2], ('plank_fence', 'oak_planks'))
        self.assertEqual(picket[:2], ('spruce_fence', 'oak_fence'))
        self.assertEqual(dp.shelf_block(picket[1], 'north'), 'oak_fence')              # never a shelf
