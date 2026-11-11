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
        self.scans: dict = {}                  # section -> times scanned

    def _scan(self, section, positions):
        self.scans[section] = self.scans.get(section, 0) + 1
        for p in positions:
            self.cache[p] = 'minecraft:grass_block' if p[1] <= 64 else 'minecraft:air'

    @staticmethod
    def _section(p):
        return (p[0] >> 4, p[1] >> 4, p[2] >> 4)

    def prefetch(self, positions):             # LiveBlocks.read_many
        batch = [tuple(p) for p in positions]
        if len(self.cache) + len(batch) > self.CAP:
            self.cache.clear()
        by_section: dict = {}
        for p in batch:
            if p not in self.cache:
                by_section.setdefault(self._section(p), []).append(p)
        for sec, ps in by_section.items():
            self._scan(sec, ps)

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
        self.assertLessEqual(max(new.scans.values()), 1)         # no section scanned twice
        self.assertLessEqual(new.total_scans, len(chunks) * 7)   # y 31..120 spans 7 sections
        self.assertGreater(old.total_scans, 2 * new.total_scans)  # before: 485 scans, after: 224

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
                  'provenance': {'unresolved_reason': 'above lidar ground'}} for n in range(40)]
        stats = {'blocks': 0, 'unplaced_items': items}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(dl, 'plan_cell', return_value=({}, stats)), \
                mock.patch('site_kit.fresh_planning_world'), \
                mock.patch('layer_reports.write_report', side_effect=lambda n, r: write_report(n, r, Path(tmp))), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(dl.run((0, 0)), 0)
            saved = json.loads((Path(tmp) / 'highfield_0_0_decor-report.json').read_text())
            self.assertEqual(saved['stats']['unplaced_items'], items)
        self.assertIn("'above lidar ground': 40", output.getvalue())
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
