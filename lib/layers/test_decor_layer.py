"""Unit tests for decor_layer (join_pieces + street furniture + empty apply)."""
from __future__ import annotations

import os
import unittest
from unittest import mock

import numpy as np

import decor_layer as dl
import street_furniture as sf

B = 'brick_wall'


def world(grid0, grid1):
    """Live blocks from two ASCII layers (y=0 and y=1), rows along z, columns along x."""
    live = {}
    for y, grid in ((0, grid0), (1, grid1)):
        for z, row in enumerate(grid):
            for x, ch in enumerate(row):
                live[(x, y, z)] = {'G': 'minecraft:grass_block', 'B': f'minecraft:{B}',
                                   'F': 'minecraft:poppy', '_': 'minecraft:air',
                                   'P': 'minecraft:light_gray_concrete'}[ch]
    return live


class TestJoinPieces(unittest.TestCase):
    """Joins of same-type walls and fences."""

    def setUp(self):
        self.live = world(['GGGBB', 'GB___'], ['GFB__', 'B____'])
        self.get = lambda p: self.live.get(p, 'minecraft:air')
        self.can = lambda p, _mat='': p[1] in (0, 1) and 0 <= p[0] <= 4 and 0 <= p[2] <= 1 and \
            dl.is_air(self.get(p))

    def pieces(self):
        return {p: B for p, b in self.live.items() if b == f'minecraft:{B}'}

    def test_example_joins_everything_with_one_block_per_join(self):
        """Pass 1 adds two stacked blocks, pass 2 exactly one of the two diagonal spots."""
        found = {p: B for p, b in self.pieces().items() if p[1] == 0}
        found[(2, 1, 0)] = B
        found[(0, 1, 1)] = B
        added = dl.join_pieces(found, self.get, self.can)
        self.assertEqual(set(added), {(3, 1, 0), (1, 1, 1), (1, 1, 0)})

    def test_prefers_supported_candidate(self):
        """(2,1,1) floats over air, (1,1,0) sits on grass: the grass one wins."""
        added = dl.join_pieces({(2, 1, 0): B, (1, 1, 1): B}, self.get, self.can)
        self.assertEqual(added, {(1, 1, 0): B})

    def test_diagonal_only_is_disjoint_and_other_type_not_joined(self):
        """A different block type between two walls is not bridged."""
        found = {(0, 1, 0): B, (2, 1, 0): 'iron_bars'}
        self.assertEqual(dl.join_pieces(found, self.get, self.can), {})

    def test_cannot_replace_grass(self):
        """Only air and flowers may be replaced."""
        live = world(['GGG'], ['B_B'])
        live[(1, 1, 0)] = 'minecraft:stone'
        found = {(0, 1, 0): B, (2, 1, 0): B}
        self.assertEqual(dl.join_pieces(found, lambda p: live.get(p, 'minecraft:air'),
                                        lambda p, _m='': dl.is_air(live.get(p, 'minecraft:air'))), {})


class TestMayJoinAtNear1(unittest.TestCase):
    """plan_cell join gate: near1 banned unless the material has allow_against_build."""

    def setUp(self):
        self.world = mock.Mock()
        self.world.block = lambda x, y, z: 'minecraft:air'

    def test_bans_near1_by_default(self):
        self.assertFalse(dl.may_join_at(
            (10, 65, 20), 'brick_wall',
            cell=(0, 0), near1={(10, 20)}, allow_near1_mats=set(),
            owned=set(), masked=set(), world=self.world,
        ))

    def test_allows_near1_for_allow_against_build_mat(self):
        mat = 'oak_leaves[persistent=true]'
        self.assertTrue(dl.may_join_at(
            (10, 65, 20), mat,
            cell=(0, 0), near1={(10, 20)}, allow_near1_mats={mat},
            owned=set(), masked=set(), world=self.world,
        ))

    def test_outside_near1_still_ok_for_ordinary_mat(self):
        self.assertTrue(dl.may_join_at(
            (11, 65, 20), 'brick_wall',
            cell=(0, 0), near1={(10, 20)}, allow_near1_mats=set(),
            owned=set(), masked=set(), world=self.world,
        ))

    def test_join_pieces_respects_near1_ban(self):
        """Two wall stubs separated by a near1 gap must not be bridged."""
        live = {(0, 0, 0): 'minecraft:grass_block', (1, 0, 0): 'minecraft:grass_block',
                (2, 0, 0): 'minecraft:grass_block',
                (0, 1, 0): 'minecraft:air', (1, 1, 0): 'minecraft:air', (2, 1, 0): 'minecraft:air'}
        found = {(0, 1, 0): B, (2, 1, 0): B}
        near1 = {(1, 0)}
        world = mock.Mock()
        world.block = lambda x, y, z: live.get((x, y, z), 'minecraft:air')
        can = lambda pos, mat='': dl.may_join_at(
            pos, mat, cell=(0, 0), near1=near1, allow_near1_mats=set(),
            owned=set(), masked=set(), world=world,
        )
        self.assertEqual(dl.join_pieces(found, lambda p: live.get(p, 'minecraft:air'), can), {})


class TestPlaceFurniture(unittest.TestCase):
    """Parts (2)+(3)+(4): recipes on paving, never on a path line or against a build."""

    def setUp(self):
        # Cell (0,0) covers x,z in 0..127. Surface at y=64 paving; air above.
        self.surface_y = 64
        self.paving = {(10, 20), (11, 20), (12, 20), (13, 20)}
        self.path_line = {(11, 20)}          # mapped footway column
        self.near_build = {(12, 20)}
        self.live = {}
        for x, z in self.paving:
            self.live[(x, self.surface_y, z)] = 'minecraft:light_gray_concrete'
            for dy in range(1, 5):
                self.live[(x, self.surface_y + dy, z)] = 'minecraft:air'

        def block(x, y, z):
            return self.live.get((x, y, z), 'minecraft:air')

        def surface_y(world, x, z):
            if (x, z) not in self.paving:
                return None
            return (self.surface_y, 'light_gray_concrete')

        self.world = mock.Mock()
        self.world.block = block
        self._surface = surface_y

    def _place(self, items, *, way=None, near1=None):
        with mock.patch('decor_layer.column_ground_kind',
                        side_effect=lambda w, x, z: 'paving' if (x, z) in self.paving else None), \
                mock.patch('surface_probe.surface_y', self._surface):
            return dl.place_furniture(
                (0, 0), items, self.world,
                way=way if way is not None else set(self.path_line),
                near1=near1 if near1 is not None else set(self.near_build),
                owned=set(), masked=set(),
            )

    def test_places_map_and_designer_items_with_source(self):
        items = [
            {'x': 10, 'z': 20, 'kind': 'bench', 'source': 'map'},
            {'x': 13, 'z': 20, 'kind': 'waste_basket', 'source': 'designer'},
        ]
        found, report, unplaced = self._place(items, way=set(), near1=set())
        self.assertEqual(unplaced, [])
        self.assertIn((10, self.surface_y + 1, 20), found)
        self.assertEqual(found[(10, self.surface_y + 1, 20)], 'spruce_stairs')
        self.assertEqual(found[(13, self.surface_y + 1, 20)], 'cauldron')
        sources = {(r['x'], r['z'], r['source'], r['kind']) for r in report}
        self.assertEqual(sources, {
            (10, 20, 'map', 'bench'),
            (13, 20, 'designer', 'waste_basket'),
        })

    def test_allows_lawn_but_not_carriageway(self):
        items = [
            {'x': 10, 'z': 20, 'kind': 'bench', 'source': 'map'},
            {'x': 13, 'z': 20, 'kind': 'bench', 'source': 'map'},
        ]
        kinds = {10: None, 13: 'tarmac'}
        with mock.patch('decor_layer.column_ground_kind',
                        side_effect=lambda w, x, z: kinds[x]), \
                mock.patch('surface_probe.surface_y', self._surface):
            _, report, unplaced = dl.place_furniture(
                (0, 0), items, self.world, way=set(), near1=set(), owned=set(), masked=set())
        self.assertEqual({(r['x'], r['z']) for r in report}, {(10, 20)})
        self.assertEqual([u['provenance']['unresolved_reason'] for u in unplaced],
                         ['on carriageway'])

    def test_skips_path_line_and_near_build(self):
        items = [
            {'x': 11, 'z': 20, 'kind': 'bench', 'source': 'map'},       # path line
            {'x': 12, 'z': 20, 'kind': 'bollard', 'source': 'map'},     # against build
            {'x': 10, 'z': 20, 'kind': 'street_lamp', 'source': 'map'},
        ]
        found, report, unplaced = self._place(items)
        self.assertEqual({(r['x'], r['z']) for r in report}, {(10, 20)})
        self.assertEqual(len(unplaced), 2)
        self.assertEqual(
            {(u['x'], u['z'], u['provenance']['unresolved_reason']) for u in unplaced},
            {(11, 20, 'on path/road/water column'), (12, 20, 'against build')},
        )
        for dy, block in sf.recipe('street_lamp'):
            self.assertEqual(found[(10, self.surface_y + dy, 20)], block)


class TestLidarGroundVeto(unittest.TestCase):
    """Decor uses live surfaces for placement, with lidar terrain as a veto."""

    def setUp(self):
        self.world = mock.Mock()
        self.world.block = lambda x, y, z: (
            'minecraft:light_gray_concrete' if y == 64 else 'minecraft:air')
        self.hm = {'x0': 0, 'z0': 0, 'terrain': np.full((128, 128), 64)}
        self.hm['terrain'][10, 20] = 58  # Roof six blocks above terrain.
        self.options = dict(way=set(), near1=set(), owned=set(), masked=set())

    def test_furniture_refuses_roof_but_places_on_ground(self):
        items = [{'x': x, 'z': 20, 'kind': 'bench'} for x in (10, 11)]
        found, report, unplaced = dl.place_furniture(
            (0, 0), items, self.world, lidar_hm=self.hm, **self.options)
        self.assertEqual(set(found), {(11, 65, 20)})
        self.assertEqual([r['x'] for r in report], [11])
        self.assertEqual([(u['x'], u['provenance']['unresolved_reason']) for u in unplaced],
                         [(10, 'above lidar ground')])

    def test_barriers_refuse_roof_but_place_on_ground(self):
        item = {'id': 'hedge', 'role': 'barrier', 'kind': 'hedge',
                'cells': [[10, 20], [11, 20]], 'height': 2, 'allow_against_build': True}
        found, report, unplaced = dl.place_barriers_from_plan(
            (0, 0), [item], self.world, lidar_hm=self.hm,
            **{**self.options, 'near1': {(10, 20), (11, 20)}})
        self.assertEqual(set(found), {(11, 65, 20), (11, 66, 20)})
        self.assertEqual([r['x'] for r in report], [11])
        self.assertEqual([(u['cells'], u['provenance']['unresolved_reason']) for u in unplaced],
                         [([[10, 20]], 'above lidar ground')])

    def test_join_pieces_cannot_bridge_a_roof_column(self):
        found = {(9, 65, 20): B, (11, 65, 20): B}

        def can_place(pos, mat):
            return dl.may_join_at(
                pos, mat, cell=(0, 0), near1=set(), allow_near1_mats=set(),
                owned=set(), masked=set(), world=self.world, lidar_hm=self.hm)

        self.assertEqual(dl.join_pieces(found, lambda p: self.world.block(*p), can_place), {})
        # At the two-block tolerance the join still sits on the live surface.
        self.hm['terrain'][10, 20] = 62
        self.assertEqual(dl.join_pieces(found, lambda p: self.world.block(*p), can_place),
                         {(10, 65, 20): B})

    def test_missing_terrain_does_not_veto_any_placer(self):
        item = {'x': 10, 'z': 20, 'kind': 'bench'}
        barrier = {'role': 'barrier', 'kind': 'hedge', 'cells': [[10, 20]], 'height': 1}
        for hm in (None, {'x0': 0, 'z0': 0},
                   {**self.hm, 'terrain': np.zeros((128, 128), dtype=int)}):
            with self.subTest(hm=hm):
                furniture, _, unplaced = dl.place_furniture(
                    (0, 0), [item], self.world, lidar_hm=hm, **self.options)
                self.assertEqual(set(furniture), {(10, 65, 20)})
                self.assertEqual(unplaced, [])
                barriers, _, unplaced = dl.place_barriers_from_plan(
                    (0, 0), [barrier], self.world, lidar_hm=hm, **self.options)
                self.assertEqual(set(barriers), {(10, 65, 20)})
                self.assertEqual(unplaced, [])
                self.assertTrue(dl.may_join_at(
                    (10, 65, 20), B, cell=(0, 0), near1=set(), allow_near1_mats=set(),
                    owned=set(), masked=set(), world=self.world, lidar_hm=hm))

    def test_terrain_is_a_veto_not_a_placement_height(self):
        self.hm['terrain'][10, 20] = 62
        self.hm['terrain'][11, 20] = 61
        items = [{'x': x, 'z': 20, 'kind': 'bench'} for x in (10, 11)]
        found, _, unplaced = dl.place_furniture(
            (0, 0), items, self.world, lidar_hm=self.hm, **self.options)
        self.assertEqual(set(found), {(10, 65, 20)})
        self.assertEqual([u['x'] for u in unplaced], [11])


class TestOnCarriageway(unittest.TestCase):
    """Barriers are refused only on the carriageway: tarmac, marking and kerb."""

    def carriageway(self, kinds, at=(10, 20)):
        with mock.patch.object(dl, 'column_ground_kind',
                               side_effect=lambda _w, x, z: kinds.get((x, z))):
            return dl.on_carriageway(mock.Mock(), *at)

    def test_tarmac_marking_and_kerb_are_carriageway(self):
        for kind in ('tarmac', 'mark', 'kerb'):
            self.assertTrue(self.carriageway({(10, 20): kind}), kind)

    def test_paving_brick_and_other_soft_ground_are_not_carriageway(self):
        for kind in ('paving', 'brick', 'grass', 'gravel', 'dirt', None):
            self.assertFalse(self.carriageway({(10, 20): kind}), kind)

    def test_nearby_road_or_paving_does_not_count(self):
        kinds = {(10, 20): 'grass', (11, 20): 'tarmac', (12, 20): 'brick', (10, 21): 'paving'}
        self.assertFalse(self.carriageway(kinds))


class TestEmptyDecorApply(unittest.TestCase):
    """Part (5): --apply with nothing to place still records the decor pass."""

    def test_apply_cell_records_empty_pass_instead_of_exiting(self):
        plan = mock.Mock()
        plan.cells = {}
        plan.name = 'highfield_0_0_decor'
        world = mock.Mock()
        mask = {'pass': 'decor', 'cell': [0, 0], 'plan': plan.name, 'count': 0, 'sha256': 'x'}
        apply_calls = []

        def fake_apply(p, **kw):
            apply_calls.append((p, kw))
            return {'planned': 0, 'mismatches': 0}

        with mock.patch.dict(os.environ, {'SITE_OWNER': 'test'}), \
                mock.patch('site_kit.Plan', return_value=plan), \
                mock.patch('site_kit.World', return_value=world), \
                mock.patch.object(dl, 'plan_cell', return_value=({}, {'blocks': 0})), \
                mock.patch.object(dl, 'build_mask', return_value=mask) as build_mask, \
                mock.patch('site_kit.apply', side_effect=fake_apply), \
                mock.patch.object(dl, 'is_air', return_value=True), \
                mock.patch.object(dl, 'ignored_builds', return_value=()):
            rc = dl.apply_cell((0, 0))
        self.assertEqual(rc, 0)
        self.assertEqual(len(apply_calls), 1)
        _p, kw = apply_calls[0]
        self.assertEqual(kw.get('stage'), 'decor')
        self.assertEqual(kw.get('cell'), (0, 0))
        self.assertIs(kw.get('mask'), mask)
        build_mask.assert_called_once()


if __name__ == '__main__':
    unittest.main()
