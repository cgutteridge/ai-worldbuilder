"""Decor plan from aerial/photos with provenance (#150)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import decision_record as dr
import decor_plan as dp
import provenance as prov


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
        garden = {
            (30, 30): ('hedge', 'oak_leaves[persistent=true]', 2),
        }

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
