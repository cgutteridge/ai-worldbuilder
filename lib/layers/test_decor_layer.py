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

    def test_reserved_entrance_survives_final_join(self):
        w = mock.Mock()
        w.block = lambda x, y, z: 'minecraft:stone' if y == 64 else 'minecraft:air'
        found = {(10, 65, 20): 'spruce_fence', (12, 65, 20): 'spruce_fence'}
        def allowed(pos, mat, keep_clear):
            return dl.may_join_at(pos, mat, cell=(0, 0), near1=set(), allow_near1_mats=set(),
                                  owned=set(), masked=set(), world=w, keep_clear=keep_clear)
        baseline = dl.join_pieces(found, lambda p: w.block(*p), lambda p, m: allowed(p, m, set()))
        self.assertIn((11, 65, 20), baseline)
        reserved = dl.join_pieces(found, lambda p: w.block(*p),
                                  lambda p, m: allowed(p, m, {(11, 20)}))
        self.assertNotIn((11, 65, 20), reserved)

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
                        side_effect=lambda w, x, z, hm=None: 'paving' if (x, z) in self.paving else None), \
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
                        side_effect=lambda w, x, z, hm=None: kinds[x]), \
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


class TestFurnitureByProvenance(unittest.TestCase):
    """#260: the decor report counts furniture by provenance source; inferred never reads as mapped."""

    @staticmethod
    def item(source, kind='street_lamp', reason=''):
        return {'role': 'furniture', 'kind': kind, 'cell': [1, 1],
                'provenance': {'source': source, 'confidence': 'low', 'evidence_ids': [], 'unresolved_reason': reason}}

    def test_planned_placed_and_unplaced_are_split_by_source(self):
        decor = {'items': [self.item('vector_map'), self.item('inference'), self.item('inference'),
                           self.item('aerial', reason='no coordinate'),
                           {'role': 'barrier', 'provenance': {'source': 'inference', 'unresolved_reason': ''}}]}
        report = [{'source': 'vector_map'}, {'source': 'inference'}]
        unplaced = [self.item('inference', reason='against build')]
        self.assertEqual(dl.furniture_by_provenance(decor, report, unplaced), {
            'planned': {'vector_map': 1, 'inference': 2},
            'placed': {'vector_map': 1, 'inference': 1},
            'unplaced': {'inference': 1}})


class TestPicnicFootprints(unittest.TestCase):
    """#306: a picnic table is a whole footprint of columns, placed whole or refused whole with a reason."""

    GROUND = 64

    def setUp(self):
        self.live = {}
        self.hm = {'x0': 0, 'z0': 0, 'terrain': np.full((128, 128), self.GROUND)}
        self.options = dict(way=set(), near1=set(), masked=set(), owned=set())
        self.kind = {}

        def block(x, y, z):
            if (x, y, z) in self.live:
                return self.live[(x, y, z)]
            return 'minecraft:grass_block' if y <= self.GROUND else 'minecraft:air'

        self.world = mock.Mock()
        self.world.block = block

    def raise_ground(self, x, z, by):
        self.hm['terrain'][x, z] = self.GROUND + by
        for y in range(self.GROUND + 1, self.GROUND + by + 1):
            self.live[(x, y, z)] = 'minecraft:grass_block'
        for y in range(self.GROUND + by + 1, self.GROUND + 4):
            self.live.pop((x, y, z), None)

    def place(self, items, **kw):
        with mock.patch('decor_layer.column_ground_kind',
                        side_effect=lambda w, x, z, hm=None: self.kind.get((x, z))):
            return dl.place_furniture((0, 0), items, self.world, lidar_hm=self.hm, **{**self.options, **kw})

    @staticmethod
    def reasons(unplaced):
        return [u['provenance']['unresolved_reason'] for u in unplaced]

    def table(self, x=10, z=20, kind='picnic_table', **extra):
        return {'x': x, 'z': z, 'kind': kind, 'source': 'designer', 'plan_id': f'{kind}-{x}-{z}', **extra}

    def test_rectangular_table_is_placed_whole_on_lawn(self):
        found, report, unplaced = self.place([self.table()])
        self.assertEqual(unplaced, [])
        y = self.GROUND + 1
        self.assertEqual(len(found), 9)
        self.assertEqual({p[1] for p in found}, {y})
        self.assertEqual({found[(x, y, 20)] for x in (9, 10, 11)}, {'spruce_slab[type=top]'})
        self.assertEqual({found[(x, y, z)] for x in (9, 10, 11) for z in (19, 21)}, {'spruce_slab[type=bottom]'})
        row = report[0]
        self.assertEqual((row['x'], row['z'], row['kind'], row['blocks'], row['rotation']),
                         (10, 20, 'picnic_table', 9, 0))
        self.assertEqual(len(row['footprint']), 9)
        self.assertEqual(row['plan_id'], 'picnic_table-10-20')
        self.assertTrue(row['on_terrain'] and not row['moved_to_terrain'])

    def test_rotation_swaps_the_long_axis(self):
        found, report, _u = self.place([self.table(rotation=90)])
        y = self.GROUND + 1
        self.assertEqual({found[(10, y, z)] for z in (19, 20, 21)}, {'spruce_slab[type=top]'})
        self.assertEqual({found[(x, y, z)] for x in (9, 11) for z in (19, 20, 21)}, {'spruce_slab[type=bottom]'})
        self.assertEqual(report[0]['rotation'], 90)

    def test_round_table_is_a_plus_of_five(self):
        found, _r, unplaced = self.place([self.table(kind='picnic_table_round')])
        self.assertEqual(unplaced, [])
        self.assertEqual({(p[0], p[2]) for p in found}, {(10, 20), (9, 20), (11, 20), (10, 19), (10, 21)})
        self.assertEqual(found[(10, self.GROUND + 1, 20)], 'spruce_slab[type=top]')

    def test_a_carriageway_under_one_bench_column_refuses_the_whole_table(self):
        self.kind[(11, 21)] = 'tarmac'
        found, report, unplaced = self.place([self.table()])
        self.assertEqual((found, report), ({}, []))
        self.assertEqual(self.reasons(unplaced), ['on carriageway'])
        self.assertEqual(unplaced[0]['blocked_column'], [11, 21])
        self.assertEqual(unplaced[0]['rotation'] if 'rotation' in unplaced[0] else 0, 0)

    def test_each_per_column_filter_refuses_the_whole_table(self):
        cases = [({'way': {(9, 19)}}, 'on path/road/water column'),
                 ({'near1': {(11, 20)}}, 'against build'),
                 ({'keep_clear': {(10, 21)}}, 'entrance opening or route reserved')]
        for opts, reason in cases:
            found, _r, unplaced = self.place([self.table()], **opts)
            self.assertEqual(found, {}, reason)
            self.assertEqual(self.reasons(unplaced), [reason])

    def test_allow_against_build_lifts_only_the_near1_filter(self):
        found, _r, unplaced = self.place([self.table(allow_against_build=True)], near1={(11, 20)})
        self.assertEqual((len(found), unplaced), (9, []))

    def test_footprint_crossing_the_cell_edge_is_refused_not_halved(self):
        found, _r, unplaced = self.place([self.table(x=127)])
        self.assertEqual(found, {})
        self.assertEqual(self.reasons(unplaced), ['crosses the cell edge'])
        self.assertEqual(unplaced[0]['blocked_column'], [128, 20])
        _f, _r, unplaced = self.place([self.table(x=200)])
        self.assertEqual(self.reasons(unplaced), ['outside cell'])

    def test_ground_more_than_one_block_off_the_anchor_is_uneven(self):
        self.raise_ground(11, 21, 2)
        found, _r, unplaced = self.place([self.table()])
        self.assertEqual(found, {})
        self.assertEqual(self.reasons(unplaced), ['uneven ground'])
        self.raise_ground(11, 21, 1)                                 # one block off stands on its own ground
        found, _r, unplaced = self.place([self.table()])
        self.assertEqual((len(found), unplaced), (9, []))
        self.assertEqual({p[1] for p in found if (p[0], p[2]) == (11, 21)}, {self.GROUND + 2})

    def test_missing_ground_under_one_column_names_its_reason(self):
        self.live[(9, self.GROUND, 19)] = 'minecraft:air'
        found, _r, unplaced = self.place([self.table()])
        self.assertEqual(found, {})
        self.assertEqual(self.reasons(unplaced), ['no ground at terrain'])
        self.assertEqual(unplaced[0]['blocked_column'], [9, 19])

    def test_owned_and_live_blocks_in_the_footprint_refuse_it(self):
        found, _r, unplaced = self.place([self.table()], owned={(9, self.GROUND + 1, 21)})
        self.assertEqual((found, self.reasons(unplaced)), ({}, ['feature above terrain']))
        self.live[(11, self.GROUND + 1, 19)] = 'minecraft:stone'
        found, _r, unplaced = self.place([self.table()])
        self.assertEqual((found, self.reasons(unplaced)), ({}, ['blocked by live or owned block']))
        found, _r, unplaced = self.place([self.table(x=20)], masked={(19, self.GROUND + 1, 19): 'outbuilding'})
        self.assertEqual((found, self.reasons(unplaced)), ({}, ['blocked by live or owned block']))

    def test_overlapping_tables_get_their_own_reason_and_the_first_stands(self):
        found, report, unplaced = self.place([self.table(x=10), self.table(x=11)])
        self.assertEqual(len(found), 9)
        self.assertEqual([r['x'] for r in report], [10])
        self.assertEqual(self.reasons(unplaced), ['overlaps another furniture item'])

    def test_a_single_column_item_inside_a_table_overlaps_it_too(self):
        _f, _r, unplaced = self.place([self.table(), {'x': 10, 'z': 21, 'kind': 'bench', 'source': 'map'}])
        self.assertEqual(self.reasons(unplaced), ['overlaps another furniture item'])
        self.assertNotIn('blocked_column', unplaced[0])                # only multi-block rows carry it

    def test_a_multi_block_item_never_replaces_a_planned_barrier(self):
        barrier = {(11, self.GROUND + 1, 21): 'oak_fence'}
        found, _r, unplaced = self.place([self.table()], barrier_voxels=barrier)
        self.assertEqual((found, self.reasons(unplaced)), ({}, ['overlaps a planned barrier']))
        found, _r, unplaced = self.place([{'x': 11, 'z': 21, 'kind': 'bench', 'source': 'map'}], barrier_voxels=barrier)
        self.assertEqual((len(found), unplaced), (1, []))              # single column: furniture wins, as before

    def test_bench_slabs_are_picked_for_keep_slab_cells_and_top_slabs_are_not(self):
        found, _r, _u = self.place([self.table()])
        slabs = dl.half_slab_positions(found, dict(found))
        self.assertEqual(len(slabs), 6)
        self.assertTrue(all(found[p].endswith('[type=bottom]') for p in slabs))
        clipped = {p: b for p, b in found.items() if p[0] != 9}
        self.assertEqual(len(dl.half_slab_positions(found, clipped)), 4)      # clipped-away slabs are not kept

    def test_keep_clear_reaches_single_column_items_with_the_old_reason(self):
        _f, _r, unplaced = self.place([{'x': 10, 'z': 20, 'kind': 'bench', 'source': 'map'}], keep_clear={(10, 20)})
        self.assertEqual(self.reasons(unplaced), ['entrance opening or route reserved'])


class TestTerrainGround(unittest.TestCase):
    """#301: decor stands on the block at lidar terrain Y, never on the roof over it; nothing is skipped for a roof."""

    GROUND = 64
    ROOF = 70

    def setUp(self):
        self.live = {}
        self.owned = set()
        self.hm = {'x0': 0, 'z0': 0, 'terrain': np.full((128, 128), self.GROUND)}
        self.options = dict(way=set(), near1=set(), masked=set())

        def block(x, y, z):
            if (x, y, z) in self.live:
                return self.live[(x, y, z)]
            return 'minecraft:grass_block' if y <= self.GROUND else 'minecraft:air'

        self.world = mock.Mock()
        self.world.block = block

    def roof(self, xs=(10,), z=20, y=ROOF):
        """A roof slab over the column(s), owned by a feature."""
        for x in xs:
            self.live[(x, y, z)] = 'minecraft:red_concrete'
            self.owned.add((x, y, z))

    def furniture(self, items, **kw):
        return dl.place_furniture((0, 0), items, self.world, owned=self.owned, lidar_hm=self.hm, **{**self.options, **kw})

    def test_furniture_under_an_overhang_is_placed_at_terrain_height(self):
        self.roof(xs=(10, 11), y=self.GROUND + 8)
        found, report, unplaced = self.furniture([{'x': x, 'z': 20, 'kind': 'bench'} for x in (10, 11)])
        self.assertEqual(unplaced, [])
        self.assertEqual({p[1] for p in found}, {self.GROUND + 1})
        self.assertEqual({p[:1] + p[2:] for p in found}, {(10, 20), (11, 20)})
        self.assertTrue(all(r['moved_to_terrain'] for r in report))

    def test_furniture_is_never_put_on_a_roof(self):
        self.roof(y=self.GROUND + 8)
        found, _r, _u = self.furniture([{'x': 10, 'z': 20, 'kind': 'bench'}])
        self.assertTrue(found)
        self.assertTrue(all(p[1] < self.GROUND + 8 for p in found))

    def test_plain_ground_is_not_moved(self):
        found, report, _u = self.furniture([{'x': 11, 'z': 20, 'kind': 'bench'}])
        self.assertEqual(set(found), {(11, self.GROUND + 1, 20)})
        self.assertFalse(report[0]['moved_to_terrain'])

    def test_furniture_blocked_by_a_feature_block_in_its_column_is_feature_above_terrain(self):
        self.roof(y=self.GROUND + 1)                        # directly over the ground: a low roof
        found, report, unplaced = self.furniture([{'x': 10, 'z': 20, 'kind': 'bench'}])
        self.assertEqual((found, report), ({}, []))
        self.assertEqual([u['provenance']['unresolved_reason'] for u in unplaced], ['feature above terrain'])

    def test_unowned_live_block_in_the_column_stays_blocked_by_live(self):
        self.live[(10, self.GROUND + 1, 20)] = 'minecraft:stone'
        _f, _r, unplaced = self.furniture([{'x': 10, 'z': 20, 'kind': 'bench'}])
        self.assertEqual([u['provenance']['unresolved_reason'] for u in unplaced], ['blocked by live or owned block'])

    def test_owned_and_masked_blocks_are_never_overwritten(self):
        self.owned.add((10, self.GROUND + 1, 20))
        found, _r, unplaced = self.furniture([{'x': 10, 'z': 20, 'kind': 'bench'}])
        self.assertNotIn((10, self.GROUND + 1, 20), found)
        found, _r, unplaced = self.furniture([{'x': 11, 'z': 20, 'kind': 'bench'}],
                                             masked={(11, self.GROUND + 1, 20): 'decor 0,0'})
        self.assertEqual(found, {})
        self.assertEqual(len(unplaced), 1)

    def test_floating_base_is_no_ground_at_terrain(self):
        self.live[(10, self.GROUND, 20)] = 'minecraft:air'           # air at terrain, a deck floats above
        self.live[(10, self.GROUND + 3, 20)] = 'minecraft:oak_planks'
        found, _r, unplaced = self.furniture([{'x': 10, 'z': 20, 'kind': 'bench'}])
        self.assertEqual(found, {})
        self.assertEqual([u['provenance']['unresolved_reason'] for u in unplaced], ['no ground at terrain'])

    def test_barriers_stand_on_terrain_under_a_roof(self):
        self.roof(xs=(10, 11), y=self.GROUND + 6)
        item = {'id': 'hedge', 'role': 'barrier', 'kind': 'hedge', 'cells': [[10, 20], [11, 20]], 'height': 2,
                'allow_against_build': True}
        found, report, unplaced = dl.place_barriers_from_plan(
            (0, 0), [item], self.world, owned=self.owned, lidar_hm=self.hm,
            **{**self.options, 'near1': {(10, 20), (11, 20)}})
        self.assertEqual(unplaced, [])
        self.assertEqual(set(found), {(x, self.GROUND + i, 20) for x in (10, 11) for i in (1, 2)})
        self.assertTrue(all(r['moved_to_terrain'] for r in report))

    def test_barrier_column_under_a_low_roof_is_feature_above_terrain(self):
        self.roof(y=self.GROUND + 1)
        item = {'id': 'hedge', 'role': 'barrier', 'kind': 'hedge', 'cells': [[10, 20]], 'height': 1}
        found, _r, unplaced = dl.place_barriers_from_plan(
            (0, 0), [item], self.world, owned=self.owned, lidar_hm=self.hm, **self.options)
        self.assertEqual(found, {})
        self.assertEqual([u['provenance']['unresolved_reason'] for u in unplaced], ['feature above terrain'])

    def test_barrier_column_with_a_refused_part_is_marked_partial(self):
        self.roof(y=self.GROUND + 2)          # a feature owns the second block of a 2-high hedge (a solid block, so 'not air')
        item = {'id': 'hedge', 'role': 'barrier', 'kind': 'hedge', 'cells': [[10, 20]], 'height': 2}
        found, report, unplaced = dl.place_barriers_from_plan(
            (0, 0), [item], self.world, owned=self.owned, lidar_hm=self.hm, **self.options)
        self.assertEqual(set(found), {(10, self.GROUND + 1, 20)})
        self.assertEqual(unplaced, [])
        self.assertTrue(report[0]['partial'])
        self.assertEqual([e['reason'] for e in report[0]['excluded_positions']], ['not air'])

    def test_whole_barrier_column_is_not_marked_partial(self):
        item = {'id': 'hedge', 'role': 'barrier', 'kind': 'hedge', 'cells': [[10, 20]], 'height': 2}
        _f, report, _u = dl.place_barriers_from_plan(
            (0, 0), [item], self.world, owned=self.owned, lidar_hm=self.hm, **self.options)
        self.assertNotIn('partial', report[0])

    def test_barrier_on_floating_ground_is_unplaced(self):
        self.live[(10, self.GROUND, 20)] = 'minecraft:air'
        item = {'id': 'hedge', 'role': 'barrier', 'kind': 'hedge', 'cells': [[10, 20]], 'height': 1}
        found, _r, unplaced = dl.place_barriers_from_plan(
            (0, 0), [item], self.world, owned=self.owned, lidar_hm=self.hm, **self.options)
        self.assertEqual(found, {})
        self.assertEqual([u['provenance']['unresolved_reason'] for u in unplaced], ['on road/path/water or no surface'
                                                                                  if False else 'no ground at terrain'])

    def test_carriageway_is_read_at_terrain_not_on_the_roof(self):
        self.roof(y=self.GROUND + 8)
        self.live[(10, self.ROOF - 3, 20)] = 'minecraft:black_concrete'   # a tarmac-coloured deck is not the road
        self.assertFalse(dl.on_carriageway(self.world, 10, 20, self.hm))
        self.live[(10, self.GROUND, 20)] = 'minecraft:black_concrete'
        self.assertEqual(dl.column_ground_kind(self.world, 10, 20, self.hm), dl.BLOCK_KIND.get('black_concrete'))

    def test_join_pieces_has_no_terrain_veto(self):
        found = {(9, self.GROUND + 1, 20): B, (11, self.GROUND + 1, 20): B}

        def can_place(pos, mat):
            return dl.may_join_at(pos, mat, cell=(0, 0), near1=set(), allow_near1_mats=set(),
                                  owned=set(), masked=set(), world=self.world)

        self.assertEqual(dl.join_pieces(found, lambda p: self.world.block(*p), can_place),
                         {(10, self.GROUND + 1, 20): B})

    def test_missing_terrain_keeps_todays_live_surface_for_every_placer(self):
        self.live = {(10, 65, 20): 'minecraft:light_gray_concrete', **{(10, y, 20): 'minecraft:air' for y in range(66, 72)}}
        item = {'x': 10, 'z': 20, 'kind': 'bench'}
        barrier = {'role': 'barrier', 'kind': 'hedge', 'cells': [[10, 20]], 'height': 1}
        self.world.block = lambda x, y, z: self.live.get((x, y, z), 'minecraft:air' if y > 65 else 'minecraft:stone')
        for hm in (None, {'x0': 0, 'z0': 0}, {**self.hm, 'terrain': np.zeros((128, 128), dtype=int)}):
            with self.subTest(hm=hm):
                furniture, report, unplaced = dl.place_furniture(
                    (0, 0), [item], self.world, lidar_hm=hm, owned=set(), **self.options)
                self.assertEqual(set(furniture), {(10, 66, 20)})
                self.assertEqual(unplaced, [])
                self.assertNotIn('on_terrain', report[0])
                barriers, _, unplaced = dl.place_barriers_from_plan(
                    (0, 0), [barrier], self.world, lidar_hm=hm, owned=set(), **self.options)
                self.assertEqual(set(barriers), {(10, 66, 20)})
                self.assertEqual(unplaced, [])


class TestOnCarriageway(unittest.TestCase):
    """Barriers are refused only on the carriageway: tarmac, marking and kerb."""

    def carriageway(self, kinds, at=(10, 20)):
        with mock.patch.object(dl, 'column_ground_kind',
                               side_effect=lambda _w, x, z, _hm=None: kinds.get((x, z))):
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

        with mock.patch('readiness_evidence.require_ready'), \
                mock.patch.dict(os.environ, {'SITE_OWNER': 'test'}), \
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


class FakeLive:
    """Live-world stand-in: flat grass at y=64; a block() read outside prefetch is a paced MCP call."""

    def __init__(self):
        self.warmed: set = set()
        self.prefetch_calls = 0
        self.cold_reads = 0
        self.reads = 0

    def prefetch(self, positions):
        self.prefetch_calls += 1
        self.warmed.update(tuple(p) for p in positions)

    def block(self, x, y, z):
        self.reads += 1
        if (x, y, z) not in self.warmed:
            self.cold_reads += 1
        return 'minecraft:grass_block' if y <= 64 else 'minecraft:air'


class TestPrefetchBatching(unittest.TestCase):
    """The live apply must batch its reads: a block() per position is ~1.1 s each."""

    def barrier_items(self):
        # ~200 barrier columns: two long hedges/fences in cell (0, 0) with a gap to join.
        cells = [[x, 10] for x in range(5, 105)] + [[x, 12] for x in range(5, 105)]
        return [{'role': 'barrier', 'kind': 'fence', 'block': 'spruce_fence', 'height': 1,
                 'id': 'f1', 'cells': cells, 'provenance': {'source': 'map'}}]

    def test_columns_cover_cells_furniture_and_ring(self):
        cols = dl.decor_columns(self.barrier_items(), [{'x': 50, 'z': 50}])
        self.assertIn((5, 10), cols)
        self.assertIn((51, 49), cols)
        self.assertIn((4, 11), cols)                      # ring beside the first piece
        self.assertNotIn((50, 60), cols)

    def test_plan_of_200_placements_makes_bounded_cold_reads(self):
        from pass_mask import clip_to_cell
        items = self.barrier_items()
        w = FakeLive()
        dl.warm_decor_columns(w, dl.decor_columns(items, []))
        found, _rep, _un = dl.place_barriers_from_plan(
            (0, 0), items, w, way=set(), near1=set(), owned=set(), masked=set())
        self.assertEqual(len(found), 200)
        joins = dl.join_pieces(found, lambda p: w.block(*p),
                               lambda pos, mat='': dl.is_air(w.block(*pos)), {})
        found.update(joins)
        placed, _ = clip_to_cell(found, 0, 0)
        w.prefetch(placed)                                # what apply_cell does before build_mask
        self.assertTrue(all(dl.is_air(w.block(*p)) for p in placed))
        self.assertGreater(w.reads, 1000)                 # many block() reads in total ...
        self.assertLessEqual(w.cold_reads, 5)             # ... almost all served from prefetch
        self.assertLessEqual(w.prefetch_calls, 20)

    def test_warm_stays_under_budget(self):
        w = FakeLive()
        cols = {(x, z) for x in range(130) for z in range(130)}
        n = dl.warm_decor_columns(w, cols)
        self.assertLessEqual(n, dl.WARM_BUDGET)
        self.assertEqual(len(w.warmed), n)

    def test_world_without_prefetch_is_fine(self):
        self.assertEqual(dl.warm_decor_columns(object(), {(0, 0)}), 0)

    def test_apply_cell_prefetches_placements_for_mask(self):
        plan = mock.Mock()
        plan.name = 'x_decor'
        w = FakeLive()
        placed = {(1, 65, 1): 'spruce_fence', (2, 65, 1): 'spruce_fence'}
        mask = {'pass': 'decor', 'cell': [0, 0], 'plan': plan.name, 'count': 2, 'sha256': 'x'}
        with mock.patch('readiness_evidence.require_ready'), \
                mock.patch.dict(os.environ, {'SITE_OWNER': 'test'}), \
                mock.patch.object(dl, 'Plan', return_value=plan), \
                mock.patch.object(dl, 'World', return_value=w), \
                mock.patch.object(dl, 'plan_cell', return_value=(placed, {'blocks': 2})), \
                mock.patch('site_kit.apply', return_value={'planned': 2, 'mismatches': 0}), \
                mock.patch.object(dl, 'ignored_builds', return_value=()):
            self.assertEqual(dl.apply_cell((0, 0)), 0)
        self.assertEqual(w.cold_reads, 0)
        self.assertEqual(w.warmed, set(placed))


class CappedLive:
    """LiveBlocks stand-in: flat grass at y=64, same cache cap and clear-on-overflow, counts section scans."""

    CAP = 65536
    EDGE = 16

    def __init__(self):
        self.cache: dict = {}
        self.scans: dict = {}                  # section (or box) -> times scanned
        self.box_scans: list = []              # (lo, hi) of each prefetch scan
        self.pos_scans: dict = {}              # position -> times fetched by prefetch

    def _scan(self, section, positions):
        self.scans[section] = self.scans.get(section, 0) + 1
        for p in positions:
            self.cache[p] = 'minecraft:grass_block' if p[1] <= 64 else 'minecraft:air'

    @staticmethod
    def _section(p):
        return (p[0] >> 4, p[1] >> 4, p[2] >> 4)

    def prefetch(self, positions):             # LiveBlocks.read_many: one scan per capped box
        from block_store import scan_tiles
        batch = [tuple(p) for p in positions]
        if len(self.cache) + len(batch) > self.CAP:
            self.cache.clear()
        need = [p for p in batch if p not in self.cache]
        if not need:
            return
        xs, ys, zs = zip(*need)
        for lo, hi in scan_tiles(min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)):
            self.box_scans.append((lo, hi))
            self.scans[('box', len(self.box_scans))] = 1
            for p in need:
                if all(lo[i] <= p[i] <= hi[i] for i in range(3)):
                    self.cache[p] = 'minecraft:grass_block' if p[1] <= 64 else 'minecraft:air'
                    self.pos_scans[p] = self.pos_scans.get(p, 0) + 1

    def block(self, x, y, z):                  # LiveBlocks.read
        p = (x, y, z)
        if p not in self.cache:
            if len(self.cache) + self.EDGE ** 3 > self.CAP:
                self.cache.clear()
            sec = self._section(p)
            self._scan(sec, [(sec[0] * 16 + a, sec[1] * 16 + b, sec[2] * 16 + c)
                             for a in range(16) for b in range(16) for c in range(16)])
        return self.cache[p]

    @property
    def total_scans(self):
        return sum(self.scans.values())


class TestChunkedSnapshot(unittest.TestCase):
    """A long fence (more columns than the warm budget) must not thrash the live cache."""

    def items(self):
        cells = [[x, z] for z in (10, 40, 70, 100) for x in range(2, 126)]
        return [{'role': 'barrier', 'kind': 'fence', 'block': 'spruce_fence', 'height': 2,
                 'id': 'f1', 'cells': cells, 'provenance': {'source': 'map'}}]

    def plan(self, w, items, view):
        found, rep, un = dl.place_barriers_from_plan(
            (0, 0), items, view, way=set(), near1=set(), owned=set(), masked=set())
        return found, rep, un

    def test_snapshot_bounds_cold_scans_and_keeps_placements(self):
        items = self.items()
        cols = dl.decor_columns(items, [])
        self.assertGreater(len(cols), dl.WARM_BUDGET // 90)       # the old warm truncated here

        old = CappedLive()                                       # before: one global warm
        dl.warm_decor_columns(old, cols)
        before = self.plan(old, items, old)

        new = CappedLive()                                       # after: chunk-wise snapshot
        view = dl.snapshot_decor_columns(new, cols)
        after = self.plan(new, items, view)

        self.assertEqual(len(after[0]), 4 * 124 * 2)
        self.assertEqual(after, before)                          # same placements and reports
        chunks = {(x >> 4, z >> 4) for x, z in cols}
        self.assertLessEqual(max(new.scans.values()), 1)         # no section or box scanned twice
        self.assertLessEqual(max(new.pos_scans.values()), 1)     # every position fetched once
        self.assertLessEqual(new.total_scans, len(chunks) * 7)   # y 31..120 spans 7 sections
        self.assertGreater(old.total_scans, 2 * new.total_scans)
        self.assertLess(len(new.box_scans), len(chunks))         # fewer scans than one per chunk (#263)
        for lo, hi in new.box_scans:
            vol = (hi[0] - lo[0] + 1) * (hi[1] - lo[1] + 1) * (hi[2] - lo[2] + 1)
            self.assertLessEqual(vol, 65536)                     # MCP volume cap

    def test_groups_bound_volume_and_cover_every_column(self):
        cols = {(x, z) for x in range(0, 132) for z in range(0, 132)}
        groups = dl.snapshot_groups(cols, 90)
        self.assertEqual(sorted(c for g in groups for c in g), sorted(cols))
        for g in groups:
            xs, zs = [c[0] for c in g], [c[1] for c in g]
            self.assertLessEqual((max(xs) - min(xs) + 1) * (max(zs) - min(zs) + 1) * 90, 65536)
        self.assertLessEqual(len(groups), 9 * 3 + 9)             # 81 chunks; was one scan each
        self.assertLess(len(groups), 81 * 0.5)

    def test_sparse_columns_do_not_share_a_big_empty_box(self):
        groups = dl.snapshot_groups({(0, 0), (500, 500), (2000, 0)}, 90)
        self.assertEqual(len(groups), 3)

    def test_outbuilding_and_fence_columns_both_snapshotted(self):
        w = CappedLive()
        cols = {(x, 10) for x in range(2, 126)} | {(60, z) for z in range(20, 40)}
        view = dl.snapshot_decor_columns(w, cols)
        self.assertEqual(view.block(60, 64, 30), 'minecraft:grass_block')
        before = w.total_scans
        for x, z in cols:
            view.block(x, 70, z)
        self.assertEqual(w.total_scans, before)                  # all served from the snapshot

    def test_view_falls_through_outside_snapshot(self):
        w = CappedLive()
        view = dl.snapshot_decor_columns(w, {(5, 5)})
        self.assertEqual(view.block(5, 64, 5), 'minecraft:grass_block')
        self.assertEqual(view.block(900, 70, 900), 'minecraft:air')   # not snapshotted: live read

    def test_world_without_prefetch_is_returned_unchanged(self):
        w = object()
        self.assertIs(dl.snapshot_decor_columns(w, {(0, 0)}), w)


if __name__ == '__main__':
    unittest.main()


class TestReporting(unittest.TestCase):
    def test_summary_is_bounded_but_full_hedge_exclusions_are_saved(self):
        import io
        import json
        import tempfile
        from contextlib import redirect_stdout
        from pathlib import Path
        from layer_reports import write_report
        items = [{'id': f'hedge-{n}', 'kind': 'hedge',
                  'provenance': {'unresolved_reason': 'feature above terrain'}} for n in range(40)]
        stats = {'blocks': 0, 'unplaced_items': items}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(dl, 'plan_cell', return_value=({}, stats)), \
                mock.patch('site_kit.fresh_planning_world'), \
                mock.patch('layer_reports.write_report', side_effect=lambda n, r, **kw: write_report(n, r, Path(tmp), **kw)), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(dl.run((0, 0)), 0)
            saved = json.loads((Path(tmp) / 'highfield_0_0_decor-dry-report.json').read_text())
            self.assertEqual(saved['stats']['unplaced_items'], items)
        self.assertIn("'feature above terrain': 40", output.getvalue())
        self.assertIn('full report:', output.getvalue())
        self.assertIn('empty decor plan', output.getvalue())
        self.assertNotIn('hedge-39', output.getvalue())
        self.assertLess(len(output.getvalue().splitlines()), 10)

    def test_partial_barrier_records_each_excluded_position_without_changing_placement(self):
        world = mock.Mock()
        world.block.return_value = 'minecraft:air'
        item = {'id': 'hedge', 'role': 'barrier', 'kind': 'hedge', 'height': 2, 'cells': [[10, 20]]}
        with mock.patch('surface_probe.surface_y', return_value=(64, 'grass_block')), \
                mock.patch.object(dl, 'on_carriageway', return_value=False):
            found, report, unplaced = dl.place_barriers_from_plan(
                (0, 0), [item], world, way=set(), near1=set(), owned={(10, 65, 20)}, masked={})
        self.assertEqual(set(found), {(10, 66, 20)})
        self.assertEqual(unplaced, [])
        self.assertEqual(report[0]['placed_positions'], [[10, 66, 20]])
        self.assertEqual(report[0]['excluded_positions'], [{'position': [10, 65, 20], 'reason': 'owned block'}])


class GardenPlacementProvenanceTest(unittest.TestCase):
    def test_placed_report_retains_scope_reason_and_evidence(self):
        item = {'id': 'front-7', 'role': 'barrier', 'kind': 'hedge', 'height': 2,
                'cells': [[10, 20]], 'scope': {'street': 'Test Road', 'property': '7', 'layer': 'street'},
                'reason': 'same-street convention',
                'provenance': {'source': 'inference', 'confidence': 'medium',
                               'evidence_ids': ['street-photo'], 'unresolved_reason': ''}}
        world = mock.Mock()
        world.block.side_effect = lambda x, y, z: 'minecraft:stone' if y <= 64 else 'minecraft:air'
        with mock.patch('surface_probe.surface_y', return_value=(64, 'stone')):
            _, rows, _ = dl.place_barriers_from_plan((0, 0), [item], world, way=set(),
                                                    near1=set(), owned=set(), masked=set())
        self.assertEqual(len(rows), 1)
        for key in ('provenance', 'scope', 'reason'):
            self.assertEqual(rows[0][key], item[key])


class ShelfFencePlacementTest(unittest.TestCase):
    """#272: plank_fence columns are written as facing shelves; no facing keeps the solid plank."""

    def _place(self, item, **kw):
        world = mock.Mock()
        world.block.side_effect = lambda x, y, z: 'minecraft:stone' if y <= 64 else 'minecraft:air'
        with mock.patch('surface_probe.surface_y', return_value=(64, 'stone')), \
                mock.patch.object(dl, 'on_carriageway', return_value=False):
            return dl.place_barriers_from_plan((0, 0), [item], world, way=set(), near1=set(),
                                               owned=set(), masked=set(), **kw)

    @staticmethod
    def _item(**extra):
        return {'id': 'p', 'role': 'barrier', 'kind': 'plank_fence', 'block': 'oak_planks', 'height': 1,
                'cells': [[10, 20], [11, 20], [12, 20]], **extra}

    def test_front_run_writes_the_exact_shelf_state(self):
        item = self._item(facing={'10,20': 'north', '11,20': 'north', '12,20': 'north'},
                          facing_basis={'10,20': 'street', '11,20': 'street', '12,20': 'street'})
        found, rows, _ = self._place(item)
        self.assertEqual(set(found.values()), {'oak_shelf[facing=north]'})
        self.assertEqual(rows[0]['facing'], 'north')
        self.assertEqual(rows[0]['facing_basis'], 'street')

    def test_corner_columns_keep_their_own_facing_and_tone(self):
        item = self._item(block='pale_oak_planks',
                          facing={'10,20': 'north', '11,20': 'north', '12,20': 'east'})
        found, _, _ = self._place(item)
        self.assertEqual(found[(10, 65, 20)], 'pale_oak_shelf[facing=north]')
        self.assertEqual(found[(12, 65, 20)], 'pale_oak_shelf[facing=east]')

    def test_opening_columns_are_simply_absent(self):
        item = self._item(facing={'10,20': 'north', '12,20': 'north'})
        item['cells'] = [[10, 20], [12, 20]]                       # entrance gap at 11 already removed
        found, _, _ = self._place(item)
        self.assertEqual({p[0] for p in found}, {10, 12})

    def test_no_outward_evidence_falls_back_to_the_solid_plank_labelled(self):
        found, rows, _ = self._place(self._item(facing={'10,20': 'north'}))
        self.assertEqual(found[(10, 65, 20)], 'oak_shelf[facing=north]')
        self.assertEqual(found[(11, 65, 20)], 'oak_planks')
        self.assertIsNone(rows[1]['facing'])
        self.assertIn('solid plank', rows[1]['facing_basis'])

    def test_other_barrier_kinds_are_unchanged(self):
        item = {'id': 'h', 'role': 'barrier', 'kind': 'hedge', 'height': 1, 'cells': [[10, 20]]}
        found, rows, _ = self._place(item)
        self.assertEqual(set(found.values()), {dl.dplan.HEDGE_BLOCK})
        self.assertNotIn('facing', rows[0])


class PicketKeepsFenceBlockTest(ShelfFencePlacementTest):
    def test_picket_fence_places_the_plain_fence_block_even_with_a_facing(self):
        item = {'id': 'f', 'role': 'barrier', 'kind': 'spruce_fence', 'block': 'oak_fence', 'height': 1,
                'cells': [[10, 20]], 'facing': {'10,20': 'north'}}
        found, rows, _ = self._place(item)
        self.assertEqual(set(found.values()), {'oak_fence'})
        self.assertNotIn('state', rows[0])

    def test_panel_output_is_unchanged_by_a_picket_beside_it(self):
        panel = self._item(facing={'10,20': 'north'}); panel['cells'] = [[10, 20]]
        alone, _, _ = self._place(panel)
        picket = {'id': 'f', 'role': 'barrier', 'kind': 'spruce_fence', 'block': 'oak_fence', 'height': 1,
                  'cells': [[13, 20]]}
        both = {**self._place(panel)[0], **self._place(picket)[0]}
        self.assertEqual(both[(10, 65, 20)], alone[(10, 65, 20)])


class TestDryRunUndo(unittest.TestCase):
    """Built decor is previewed against a simulated undo; apply artifacts survive."""

    def test_built_cell_matches_empty_world_and_preserves_apply_artifacts(self):
        import io
        import json
        import tempfile
        import types
        from contextlib import ExitStack, redirect_stdout
        from pathlib import Path
        from layer_reports import artifact_path
        from pass_mask import build_mask, write_mask
        from site_kit import Plan

        class Ground:
            def __init__(self, blocks=None):
                self.blocks = blocks or {}

            def block(self, x, y, z):
                return self.blocks.get((x, y, z), 'minecraft:grass_block' if y <= 64 else 'minecraft:air')

        empty = Ground()
        item = {'id': 'front', 'role': 'barrier', 'kind': 'hedge', 'height': 2, 'cells': [[10, 20], [11, 20]]}
        vmap = types.SimpleNamespace(**{k: set() for k in
            ('road', 'feat', 'water', 'water_line', 'hedge', 'wall', 'fence', 'marks')},
            furniture=[], road_lines=[], path_lines=[])
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack, redirect_stdout(io.StringIO()):
            directory = Path(tmp)
            masks = directory / 'masks'
            masks.mkdir()
            stack.enter_context(mock.patch('pass_mask.MASK_DIR', masks))
            stack.enter_context(mock.patch('ground_sources.OSMGround', return_value=vmap))
            stack.enter_context(mock.patch('osm_footprints.OSMIndex', return_value=mock.Mock(cells=lambda: {}, buildings={})))
            stack.enter_context(mock.patch.object(dl, 'neighbour_owned', return_value=set()))
            stack.enter_context(mock.patch.object(dl, 'load_masked_positions', return_value={}))
            stack.enter_context(mock.patch.object(dl.dplan, 'land_class_columns', return_value={}))
            stack.enter_context(mock.patch.object(dl.dplan, 'build_decor_plan', return_value={'items': [item]}))
            stack.enter_context(mock.patch.object(dl.ob, 'mapped_outbuilding_footprints', return_value=[]))
            stack.enter_context(mock.patch.object(dl.ob, 'lidar_rise', return_value=None))
            stack.enter_context(mock.patch.object(dl.sf, 'designer_items', return_value=[]))
            stack.enter_context(mock.patch.object(dl.sf, 'designer_observations', return_value=[]))
            stack.enter_context(mock.patch('lidar_heightmap.reference_world', return_value=mock.Mock(heightmap=lambda c: None)))
            expected, _ = dl.plan_cell((0, 0), Plan('empty'), empty)
            self.assertGreater(len(expected), 0)
            write_mask(build_mask('decor', (0, 0), 'old', expected, lambda p: True), masks)
            built = Ground({p: 'minecraft:' + b for p, b in expected.items()})
            name = dl.plan_name(0, 0)
            originals = {directory / f'{name}-{suffix}': b'last apply' for suffix in ('report.json', 'preview.png')}
            for path, data in originals.items():
                path.write_bytes(data)
            stack.enter_context(mock.patch('site_kit.fresh_planning_world', return_value=built))
            stack.enter_context(mock.patch('site_kit.apply', return_value={}))
            stack.enter_context(mock.patch('layer_reports.artifact_path', side_effect=lambda n, a, **kw:
                artifact_path(n, a, **{**kw, 'directory': directory})))
            self.assertEqual(dl.run((0, 0)), 0)
            report = json.loads((directory / f'{name}-dry-report.json').read_text())
            self.assertEqual(report['placement_positions'], len(expected))
            self.assertEqual(report['stats']['unplaced_items'], [])
            self.assertTrue((directory / f'{name}-dry-preview.png').exists())
            for path, data in originals.items():
                self.assertEqual(path.read_bytes(), data)

    def test_conflicting_undo_is_reported_and_world_is_unchanged(self):
        import io
        import tempfile
        from contextlib import redirect_stdout
        from pathlib import Path
        from pass_mask import build_mask, write_mask

        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()) as output:
            directory = Path(tmp)
            pos = (10, 65, 20)
            write_mask(build_mask('decor', (0, 0), 'old', {pos: 'oak_leaves'}, lambda p: True), directory)
            live = mock.Mock(block=lambda *p: 'minecraft:stone')
            with mock.patch('pass_mask.MASK_DIR', directory), \
                    mock.patch('site_kit.fresh_planning_world', return_value=live), \
                    mock.patch('layer_reports.write_report'), \
                    mock.patch.object(dl, 'plan_cell', return_value=({}, {'blocks': 0})) as planner:
                self.assertEqual(dl.run((0, 0)), 0)
            self.assertIs(planner.call_args.args[2], live)
            self.assertIn(pos, planner.call_args.kwargs['masked'])
            self.assertIn('undo conflicts; planning against the unmodified world', output.getvalue())
