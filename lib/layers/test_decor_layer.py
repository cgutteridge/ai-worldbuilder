"""Unit tests for decor_layer (join_pieces + street furniture + empty apply)."""
from __future__ import annotations

import os
import unittest
from unittest import mock

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
        self.can = lambda p: p[1] in (0, 1) and 0 <= p[0] <= 4 and 0 <= p[2] <= 1 and \
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
                                        lambda p: dl.is_air(live.get(p, 'minecraft:air'))), {})


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
        found, report = self._place(items, way=set(), near1=set())
        self.assertIn((10, self.surface_y + 1, 20), found)
        self.assertEqual(found[(10, self.surface_y + 1, 20)], 'spruce_stairs')
        self.assertEqual(found[(13, self.surface_y + 1, 20)], 'cauldron')
        sources = {(r['x'], r['z'], r['source'], r['kind']) for r in report}
        self.assertEqual(sources, {
            (10, 20, 'map', 'bench'),
            (13, 20, 'designer', 'waste_basket'),
        })

    def test_skips_path_line_and_near_build(self):
        items = [
            {'x': 11, 'z': 20, 'kind': 'bench', 'source': 'map'},       # path line
            {'x': 12, 'z': 20, 'kind': 'bollard', 'source': 'map'},     # against build
            {'x': 10, 'z': 20, 'kind': 'street_lamp', 'source': 'map'},
        ]
        found, report = self._place(items)
        self.assertEqual({(r['x'], r['z']) for r in report}, {(10, 20)})
        for dy, block in sf.recipe('street_lamp'):
            self.assertEqual(found[(10, self.surface_y + dy, 20)], block)


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
