import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest import mock

import numpy as np

import house_build as H
from journal_testkit import LiveApplyCase


class DirectApplyTest(LiveApplyCase):
    def test_existing_house_requires_override_and_undo_allows_a_clean_rebuild(self):
        from site_kit import Plan
        spec = {'name': 'demo', 'ways': {'1': 'A'}, 'roofs': {'A': 'bricks'},
                'groups': [{'nos': ['A'], 'front': 'N'}]}
        path = self.t / 'demo.json'
        path.write_text(json.dumps(spec))
        old = Plan('demo')
        old.set(1, 60, 1, 'bricks')
        old.set(2, 60, 1, 'bricks')
        self.run_apply(old)
        before = self.reg().journal_path.read_bytes()
        new = Plan('demo')
        new.set(1, 60, 1, 'stone')
        with mock.patch.object(H, 'build', return_value=new) as planner, \
                mock.patch.object(H, 'preview'), mock.patch('site_lock.check'), \
                mock.patch('readiness_evidence.require_feature_ready'), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, '--overwrite'):
                H.main([str(path), '--apply'])
            planner.assert_not_called()
            self.assertEqual(self.reg().journal_path.read_bytes(), before)
            H.main([str(path)])  # Dry runs remain available for an existing build.
            self.assertEqual(self.reg().journal_path.read_bytes(), before)
            H.main([str(path), '--apply', '--overwrite'])
            self.assertEqual(self.blocks.read((1, 60, 1)), 'minecraft:stone')
            self.assertEqual(self.blocks.read((2, 60, 1)), 'minecraft:bricks')
            self.run_undo('demo')
            H.main([str(path), '--apply'])
        self.assertEqual(self.blocks.read((1, 60, 1)), 'minecraft:stone')
        self.assertEqual(self.blocks.read((2, 60, 1)), 'minecraft:air')
        self.assertEqual(self.entries('demo')[-1].meta['builder_name'], 'house_build')

    def test_relative_house_spec_is_resolved_against_the_world(self):
        from site_kit import Plan
        spec = {'name': 'demo', 'ways': {'1': 'A'}, 'roofs': {'A': 'bricks'},
                'groups': [{'nos': ['A'], 'front': 'N'}]}
        (self.t / 'demo.json').write_text(json.dumps(spec))
        plan = Plan('demo')
        plan.set(1, 60, 1, 'bricks')
        with mock.patch('active_world.world_root', return_value=self.t), \
                mock.patch.object(H, 'build', return_value=plan), mock.patch.object(H, 'preview'), \
                mock.patch('site_lock.check'), mock.patch('readiness_evidence.require_feature_ready'), \
                redirect_stdout(io.StringIO()):
            H.main(['demo.json', '--apply'])
        self.assertEqual(self.entries('demo')[0].meta['spec_path'], 'demo.json')

class SpecTest(unittest.TestCase):
    def write(self, spec):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        f = Path(tmp.name) / 's.json'
        f.write_text(json.dumps(spec))
        return f

    def test_missing_key_and_unknown_label_are_refused(self):
        good = {'name': 'x', 'ways': {'1': 'A'}, 'roofs': {'A': 'brick'},
                'groups': [{'nos': ['A'], 'front': 'S'}]}
        spec = H.load_spec(self.write(good))
        self.assertEqual((spec['step_max'], spec['ledge_stair'], spec['keep_edits'], spec['floor_min']), (4, False, False, None))
        for bad in ({k: v for k, v in good.items() if k != 'roofs'},
                    {**good, 'groups': [{'nos': ['B'], 'front': 'S'}]},
                    {**good, 'groups': [{'nos': ['A'], 'front': 'X'}]}):
            with self.assertRaises(SystemExit):
                H.load_spec(self.write(bad))

    def test_feature_specs_are_refused_with_correct_builder(self):
        good = {'name': 'x', 'ways': {'1': 'A'}, 'roofs': {'A': 'brick'},
                'groups': [{'nos': ['A'], 'front': 'S'}]}
        for change in ({'roofs': {'A': {'kind': 'pitched', 'material': 'brick'}}},
                       {'roofs': []}, {'storeys': []}, {'facade_plan': {}},
                       {'notes': 'FEATURE PATH (scripts/feature_build.py)'}):
            with self.subTest(change=change), self.assertRaisesRegex(SystemExit, 'scripts/feature_build.py'):
                H.load_spec(self.write({**good, **change}))


class RoofSpecTest(unittest.TestCase):
    write = SpecTest.write

    def spec(self, **changes):
        return {'name': 'roof', 'ways': {'1': 'A', '2': 'B'},
                'roofs': {'A': 'brick', 'B': 'brick'},
                'groups': [{'nos': ['A', 'B'], 'front': 'N', 'mode': 'group'}],
                **changes}

    def test_pitch_values_and_labels_are_checked(self):
        for pitch in (-1, 1.01, 10 ** 400, True, False, '1', None, float('nan'), float('inf')):
            with self.subTest(pitch=pitch), self.assertRaisesRegex(SystemExit, 'roof_pitch'):
                H.load_spec(self.write(self.spec(roof_pitch={'A': pitch})))
        for pitches in ([], None, {'C': 1}):
            with self.subTest(pitches=pitches), self.assertRaisesRegex(SystemExit, 'roof_pitch'):
                H.load_spec(self.write(self.spec(roof_pitch=pitches)))
        missing_roof = self.spec(roofs={'A': 'brick'}, roof_pitch={'B': 1})
        with self.assertRaisesRegex(SystemExit, 'needs a way and a roof'):
            H.load_spec(self.write(missing_roof))

    def test_shared_roof_pitch_requires_equal_effective_values(self):
        for pitches in ({'A': 1}, {'A': 0.5, 'B': 0.25}):
            with self.subTest(pitches=pitches), self.assertRaisesRegex(SystemExit, 'equal roof_pitch'):
                H.load_spec(self.write(self.spec(roof_pitch=pitches)))
        for pitches in ({}, {'A': 0.5}, {'A': 1, 'B': 1}):
            self.assertEqual(H.load_spec(self.write(self.spec(roof_pitch=pitches)))['roof_pitch'], pitches)
        each = [{'nos': ['A', 'B'], 'front': 'N', 'mode': 'each'}]
        H.load_spec(self.write(self.spec(groups=each, roof_pitch={'A': 1})))

    def test_shared_material_validation_uses_the_half_pitch_boundary(self):
        for pitch in (0, 0.5):
            H.load_spec(self.write(self.spec(roofs={'A': 'smooth_stone', 'B': 'terracotta'},
                                             roof_pitch={'A': pitch, 'B': pitch})))
        for material in ('smooth_stone', 'terracotta'):
            with self.subTest(material=material), self.assertRaisesRegex(SystemExit, 'not a pitched triad'):
                H.load_spec(self.write(self.spec(roofs={'A': material, 'B': material},
                                                 roof_pitch={'A': 0.501, 'B': 0.501})))


class RoofGeometryTest(unittest.TestCase):
    """Synthetic footprint plans only; legacy hashes were captured before #366."""

    @staticmethod
    def footprints(kind):
        if kind == 'square':
            return [{(x, z) for x in range(7) for z in range(7)}]
        if kind == 'L':
            return [{(x, z) for x in range(10) for z in range(10) if x < 5 or z < 5}]
        return [{(x, z) for x in range(lo, hi) for z in range(8)}
                for lo, hi in ((0, 6), (6, 12))]

    def plan(self, kind='square', pitch=None, material='brick', mode='group', parts=None):
        from site_kit import Plan
        parts = parts or self.footprints(kind)
        labels = [str(i) for i in range(len(parts))]
        group = {'nos': labels, 'front': 'N', 'mode': mode}
        b = H.Build.__new__(H.Build)
        b.groups, b.houses = [group], []
        for label, cells in zip(labels, parts):
            house = H.House(label, cells, group)
            house.b = 30
            b.houses.append(house)
        b.owner = {c: i for i, cells in enumerate(parts) for c in cells}
        b.foreign, b.shared = set(), set()
        b.spec = {'roofs': dict.fromkeys(labels, material)}
        if pitch is not None:
            b.spec['roof_pitch'] = dict.fromkeys(labels, pitch)
        b.p = Plan('synthetic_roof')
        b.roof()
        return b.p

    def test_legacy_flatish_plans_match_pre_change_output(self):
        expected = {
            'square': '7064076ad3685a3200f7754feb188c7510428bd58d9acea287d5bb611169d8e0',
            'L': '4d42eae5036129354e47c70c47cc097250f4132c2157131a1ca35bcc829d885b',
            'group': '4da5be58289db1ca95333f5abb8922aa388ec881b6a06a223eb7323c85640dd7',
        }
        for kind, digest in expected.items():
            for pitch in (None, 0, 0.25, 0.5):
                with self.subTest(kind=kind, pitch=pitch):
                    short = {q: state.removeprefix('minecraft:')
                             for q, state in self.plan(kind, pitch).cells.items()}
                    self.assertEqual(hashlib.sha256(json.dumps(sorted(short.items())).encode()).hexdigest(), digest)

    def test_pitched_shapes_lint_and_full_underlay(self):
        from roof_lint import ridge_jogs, stair_shape_mismatches
        for kind in ('square', 'L', 'group'):
            with self.subTest(kind=kind):
                p = self.plan(kind, 1)
                stairs = {q: s for q, s in p.cells.items() if '_stairs[' in s}
                self.assertTrue(stairs)
                self.assertTrue(any('shape=outer_' in s for s in stairs.values()))
                if kind == 'L':
                    self.assertTrue(any('shape=inner_' in s for s in stairs.values()))
                self.assertEqual(stair_shape_mismatches(p.cells), [])
                self.assertEqual(ridge_jogs(p.cells), [])
                H.guard_roof_jogs(p)
                for (x, y, z), state in stairs.items():
                    self.assertIn('shape=', state)
                    self.assertEqual(p.cells[x, y - 1, z], 'minecraft:bricks')
                for q in p.keep_slab_cells:
                    self.assertIn('type=bottom', p.cells[q])
                    x, y, z = q
                    self.assertEqual(p.cells[x, y - 1, z], 'minecraft:bricks')
                # Owned roof columns stay solid from wall plate to the membrane.
                from roof_lint import _tops
                for cells in self.footprints(kind):
                    for x, z in cells:
                        for y in range(36, _tops(p.cells)[x, z][0]):
                            self.assertEqual(p.cells[x, y, z], 'minecraft:bricks')
                from site_kit import rewrite_bottom_half_slabs
                before = dict(p.cells)
                self.assertEqual(rewrite_bottom_half_slabs(p), 0)
                self.assertEqual(p.cells, before)

    def test_pitched_slopes_face_uphill_and_caps_meet(self):
        from roof_lint import _tops, _props
        parts = [{(x, z) for x in range(7) for z in range(11)}]
        p = self.plan(pitch=1, parts=parts)
        top = _tops(p.cells)
        self.assertIn('facing=east', top[0, 5][1])
        self.assertIn('facing=west', top[6, 5][1])
        self.assertIn('facing=south', top[3, 0][1])
        self.assertIn('facing=north', top[3, 10][1])
        self.assertIn('type=bottom', top[3, 5][1])
        delta = {'north': (0, -1), 'east': (1, 0), 'south': (0, 1), 'west': (-1, 0)}
        for (x, z), (y, state) in top.items():
            if '_stairs[' not in state:
                continue
            dx, dz = delta[_props(state)['facing']]
            nx, nz = x + dx, z + dz
            neighbor = top[nx, nz]
            # At a hip corner only the diagonal is higher; cardinal join stays level.
            self.assertGreaterEqual(neighbor[0], y)
            self.assertTrue(any(top.get((x + a, z + c), (-1,))[0] > y
                                for a, c in H.N8))
        even = self.plan(pitch=1, parts=[{(x, z) for x in range(8) for z in range(12)}])
        tops = _tops(even.cells)
        self.assertIn('facing=east', tops[3, 6][1])
        self.assertIn('facing=west', tops[4, 6][1])
        self.assertEqual(tops[3, 6][0], tops[4, 6][0])

    def test_pitch_changes_ridge_and_chimney_height(self):
        for pitch, ridge in ((0.5, 38), (0.75, 39), (1, 40)):
            with self.subTest(pitch=pitch):
                p = self.plan(pitch=pitch)
                pots = [q for q, state in p.cells.items() if state == 'minecraft:flower_pot']
                self.assertEqual(pots, [(3, ridge + 2, 3)])
                for y in range(36, ridge + 2):
                    self.assertEqual(p.cells[3, y, 3], 'minecraft:bricks')

    def test_group_roof_uses_union_and_one_chimney(self):
        parts = self.footprints('group')
        group = self.plan('group', 1)
        single = self.plan(pitch=1, parts=[set.union(*parts)])
        self.assertEqual(group.cells, single.cells)
        each = self.plan('group', 1, mode='each')
        self.assertEqual(sum(s == 'minecraft:flower_pot' for s in each.cells.values()), 2)
        self.assertNotEqual(group.cells, each.cells)

    def test_materials_use_shared_catalogue_and_full_only_flatish(self):
        import roof_material as RM
        ids = RM.block_id_names()
        for material, family in RM.load_catalogue()['families'].items():
            for pitch in ((None, 1) if family['triad'] else (None,)):
                with self.subTest(material=material, pitch=pitch):
                    cells = self.plan(pitch=pitch, material=material).cells
                    self.assertTrue(all(s.removeprefix('minecraft:').split('[')[0] in ids
                                        for s in cells.values()))
                    if pitch == 1:
                        self.assertTrue(any(s.startswith('minecraft:' + family['stairs'] + '[')
                                            for s in cells.values()))
                    elif 'slab' not in family:
                        self.assertTrue(any(s == 'minecraft:' + family['full'] for s in cells.values()))
                        self.assertFalse(any('_slab[' in s for s in cells.values()))
        for alias in ('minecraft:brick_slab[type=bottom]', 'brick_stairs'):
            self.assertEqual(self.plan(pitch=1, material=alias).cells, self.plan(pitch=1).cells)
        unknown = self.plan(material='legacy_unknown').cells
        self.assertTrue(any('legacy_unknown_slab[' in s for s in unknown.values()))

    def test_stair_helper_accepts_explicit_shape_without_changing_legacy_states(self):
        self.assertEqual(H.stair('spruce', 'north'), 'spruce_stairs[facing=north,half=bottom]')
        self.assertEqual(H.stair('brick_stairs', 'east', shape='inner_left'),
                         'brick_stairs[facing=east,half=bottom,shape=inner_left]')


class FloorReferenceTest(unittest.TestCase):
    def build(self, floor_min=None):
        b = H.Build.__new__(H.Build)
        b.name = 'low_house'
        b.spec = {'floor_min': floor_min}
        b.by_no_all_cells = {(0, 0)}
        b.live = mock.Mock()
        b.live.block.side_effect = lambda x, y, z: 'minecraft:stone' if y <= 28 else 'minecraft:air'
        return b, H.House('A', {(0, 0)}, {'front': 'N'})

    def floor(self, b, h, height=28):
        from world_frame import load_frame
        lidar = mock.Mock()
        lidar.missing.return_value = []
        lidar.sample.side_effect = lambda product, east, north, *args: np.full(
            np.asarray(east).shape, height - load_frame().y_offset)
        with mock.patch('lidar_provider.tile_client', return_value=lidar), redirect_stdout(io.StringIO()):
            return b.floor_y(h)

    def test_low_ground_floor_and_floor_min(self):
        for floor_min, expected in ((None, 28), (30, 30), (25, 28)):
            with self.subTest(floor_min=floor_min):
                b, h = self.build(floor_min)
                self.assertEqual(self.floor(b, h), expected)

    def test_floor_uses_lidar_instead_of_live_buildings(self):
        b, h = self.build()
        b.live.block.side_effect = lambda x, y, z: 'minecraft:stone' if y <= 60 else 'minecraft:air'
        self.assertEqual(self.floor(b, h), 28)
        b.live.block.assert_not_called()

    def test_missing_or_invalid_lidar_is_refused(self):
        b, h = self.build()
        with mock.patch('lidar_provider.tile_client') as client:
            client.return_value.missing.return_value = ['missing DTM']
            with self.assertRaisesRegex(SystemExit, 'low_house A: lidar DTM cache missing'):
                b.floor_y(h)
        with self.assertRaisesRegex(SystemExit, 'no valid ground under footprint'):
            self.floor(b, h, height=float('nan'))


class StoopKeepTest(unittest.TestCase):
    """#243: the stoop outside a door survives the clash filter even when keep_edits is false."""

    def test_stoop_cells_are_kept_by_default(self):
        b = H.Build.__new__(H.Build)
        b.spec = {'keep_edits': False}
        b.keep = set()
        b.set = mock.Mock()
        b.stoop_set(5, 38, 7, 'minecraft:air')
        self.assertIn((5, 38, 7), b.keep)
        b.set.assert_called_once_with(5, 38, 7, 'minecraft:air')

    def test_step_out_cells_are_kept(self):
        import inspect
        src = inspect.getsource(H.Build.step_out)
        self.assertNotIn('self.kset(', src)
        self.assertIn('self.stoop_set(', src)

    def test_plan_receives_keep_set(self):
        import inspect
        self.assertIn('self.p.keep = set(self.keep)', inspect.getsource(H.Build))

    def test_door_code_uses_stoop_set(self):
        import inspect
        src = inspect.getsource(H.Build)
        self.assertIn("self.stoop_set(st[0], y, st[1], 'air')", src)
        self.assertIn("self.stoop_set(sx, y, sz, 'air')", src)
        self.assertIn('self.door_level(', src)


class RaisedDoorTest(unittest.TestCase):
    """#243: a door whose outside ground stands one above the floor is raised, not the street lowered."""

    def build(self, top):
        b = H.Build.__new__(H.Build)
        b.top_solid = lambda x, z, near: top
        b.set = mock.Mock()
        return b

    def test_door_level(self):
        self.assertEqual(self.build(31).door_level((0, 0), 30), 31)
        self.assertEqual(self.build(30).door_level((0, 0), 30), 30)
        self.assertEqual(self.build(29).door_level((0, 0), 30), 30)
        self.assertEqual(self.build(None).door_level((0, 0), 30), 30)
        self.assertEqual(self.build(32).door_level((0, 0), 30), 30)

    def test_raise_inner_adds_stair_and_headroom(self):
        b = self.build(31)
        b.keep = set()
        b.raise_inner((10, 10), 'S', 30, 31)
        calls = {c.args[:3]: c.args[3] for c in b.set.call_args_list}
        self.assertIn('stairs', calls[(10, 31, 9)])
        self.assertIn('facing=south', calls[(10, 31, 9)])
        self.assertEqual(calls[(10, 32, 9)], 'air')
        self.assertEqual(calls[(10, 33, 9)], 'air')

    def test_raise_inner_keeps_cell_clear_when_not_raised(self):
        b = self.build(30)
        b.keep = set()
        b.stoop_set = mock.Mock()
        b.raise_inner((10, 10), 'S', 30, 30)
        cells = {c.args[:3] for c in b.stoop_set.call_args_list}
        self.assertEqual(cells, {(10, 31, 9), (10, 32, 9), (10, 33, 9)})

    def test_door_halves_are_kept(self):
        import inspect
        src = inspect.getsource(H.Build.doors)
        self.assertNotIn("self.set(c0[0], y, c0[1], f\"spruce_door", src)
        self.assertNotIn("self.set(c1[0], y, c1[1], f'spruce_door", src)


class DoorFrameTest(unittest.TestCase):
    def build(self, walls, door_frame=True):
        b = H.Build.__new__(H.Build)
        b.spec = {'door_frame': door_frame}
        h = type('h', (), {})()
        h.walls = set(walls)
        b.owner = {c: 0 for c in walls}               # nothing stands in front of the wall
        return b, h

    def test_door_prefers_cell_with_wall_each_side(self):
        # front N: a step at x=2 means (2,0) has air to its west; (4,0) sits in a straight run
        walls = [(2, 0), (3, 0), (4, 0), (5, 0), (6, 0)]
        b, h = self.build(walls)
        self.assertEqual(sorted(b.with_jamb_walls(h, walls, 'N')), [(3, 0), (4, 0), (5, 0)])

    def test_notch_then_unchanged_fallback(self):
        walls = [(0, 0), (1, 0)]
        b, h = self.build(walls)
        self.assertEqual(b.with_jamb_walls(h, walls, 'N'), walls)    # no cell has both neighbours: keep the old pick
        b, h = self.build(walls, door_frame=False)
        self.assertEqual(b.with_jamb_walls(h, walls, 'N'), walls)


class ChimneyTest(unittest.TestCase):
    def test_chimney_has_brick_to_one_above_ridge_then_flower_pot(self):
        # Odd square footprints exercise both top- and bottom-slab ridges.
        for width, ridge in ((5, 37), (7, 38)):
            with self.subTest(width=width):
                group = {'nos': ['A'], 'mode': 'each', 'front': 'S'}
                cells = {(x, z) for x in range(width) for z in range(width)}
                house = H.House('A', cells, group)
                house.b = 30
                build = H.Build.__new__(H.Build)
                build.groups = [group]
                build.houses = [house]
                build.owner = {c: 0 for c in cells}
                build.foreign = set()
                build.shared = set()
                build.spec = {'roofs': {'A': 'brick'}}
                blocks = {}
                build.set = lambda x, y, z, block: blocks.__setitem__((x, y, z), block)

                build.roof()

                cx = cz = width // 2
                self.assertEqual([p for p, block in blocks.items() if block == 'flower_pot'],
                                 [(cx, ridge + 2, cz)])
                for y in range(36, ridge + 2):
                    self.assertEqual(blocks[cx, y, cz], H.WALL)
                self.assertNotIn((cx, ridge + 3, cz), blocks)
                adjacent_roof_y = 36 + (width // 2) // 2
                self.assertTrue(blocks[cx + 1, adjacent_roof_y, cz].startswith('brick_slab['))


if __name__ == '__main__':
    unittest.main()


class EntranceApproachTest(unittest.TestCase):
    def test_no_approach_connects_at_doors_outward_neighbour(self):
        a = H.entrance_approach((10, 10), 'S')
        self.assertEqual(a['connection']['cell'], [10, 11])
        self.assertEqual(a['door']['origin'], 'generated')
        self.assertFalse(a['connection']['verified'])

    def test_connection_is_outer_end_of_feature_steps(self):
        a = H.entrance_approach((10, 10), 'S', approach_cells=[(10, 11), (10, 12)], base_y=64)
        self.assertEqual(a['connection']['cell'], [10, 13])
        self.assertEqual(a['connection']['y'], 64)
        self.assertEqual(a['approach'], [[10, 11], [10, 12]])
        self.assertEqual(a['clearance']['headroom'], 2)

    def test_updated_approach_moves_connection(self):
        short = H.entrance_approach((4, 4), 'W', approach_cells=[(3, 4)])
        longer = H.entrance_approach((4, 4), 'W', approach_cells=[(3, 4), (2, 4)])
        self.assertEqual((short['connection']['cell'], longer['connection']['cell']), ([2, 4], [1, 4]))

    def test_observed_door_is_verified(self):
        a = H.entrance_approach((4, 4), 'N', generated=False)
        self.assertTrue(a['connection']['verified'])
        self.assertEqual(a['provenance']['source'], 'vector_map')

    def test_legacy_build_without_door_is_explicit(self):
        a = H.entrance_approach(None, 'S')
        self.assertIsNone(a['connection'])
        self.assertIn('unavailable', a['reason'])


class InteriorGroundTest(unittest.TestCase):
    """Plan → journal: interior air clears ground without claiming neighbouring cells."""

    def test_interior_air_cancels_ground_and_preserves_neighbours(self):
        from journal_testkit import JournalWorld
        cells = {(x, z) for x in range(10, 17) for z in range(20, 27)}
        inside, neighbour, higher, feature = (13, 31, 23), (8, 31, 23), (13, 32, 23), (14, 31, 23)
        index = mock.Mock()
        index.cells.return_value = {c: 0 for c in cells}
        index.buildings = [(None, {'osm_way_id': 99})]
        with JournalWorld() as world:
            ground = {q: 'minecraft:gray_concrete' for q in (inside, neighbour, higher)}
            world.build('old_ground', 'ground', ground)
            world.build('other_feature', 'feature', {feature: 'minecraft:stone'})
            spec = {'name': 'new_house', 'ways': {'99': 'A'}, 'roofs': {'A': 'brick'},
                    'groups': [{'nos': ['A'], 'front': 'N'}], 'floor_min': 30}
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'house.json'
                path.write_text(json.dumps(spec))
                spec = H.load_spec(path)
            live = mock.Mock()
            live.block.side_effect = lambda x, y, z: world.blocks.read((x, y, z))
            with mock.patch.object(H.osm_footprints, 'OSMIndex', return_value=index), \
                    mock.patch.object(H.osm_footprints, 'guard', return_value={'outside': {}}), \
                    mock.patch.object(H, 'fresh_planning_world', return_value=live), \
                    mock.patch('floor_reference.floor_reference', return_value=30), \
                    redirect_stdout(io.StringIO()):
                plan = H.build(spec)
            self.assertEqual(plan.cells.get(inside), 'minecraft:air')
            self.assertNotIn(neighbour, plan.cells)
            self.assertNotIn(higher, plan.cells)
            self.assertNotIn(feature, plan.cells)
            world.build(plan.name, 'feature', plan.cells, payload=plan)
            self.assertEqual(world.blocks.read(inside), 'minecraft:air')
            self.assertNotIn(inside, world.reg.owned([inside]))
            for q in (neighbour, higher):
                self.assertEqual(world.blocks.read(q), 'minecraft:gray_concrete')
                self.assertEqual(world.reg.owned([q])[q][0], 'old_ground')
            self.assertEqual(world.reg.owned([feature])[feature][0], 'other_feature')
