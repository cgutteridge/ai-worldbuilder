"""Window policy exercised through both builders with synthetic shells and terrain."""
import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

import numpy as np

import feature_build as F
import house_build as H
from site_kit import Plan
from cellmath import DIRS
from window_openings import Window, choose_windows, report_lines


class WindowReportTest(unittest.TestCase):
    def select(self, candidates, blocks, walls, columns=None, protected=()):
        columns = walls if columns is None else columns
        return choose_windows(candidates, block=lambda x, y, z: blocks.get((x, y, z), 'air'),
            walls=walls, exposed=lambda c, face: (
                c[0] + DIRS[face][0], c[1] + DIRS[face][1]) not in columns,
            outside_ground={
                (w.pos[0] + DIRS[w.face][0], w.pos[2] + DIRS[w.face][1]): -1
                for w in candidates}, protected=protected)

    def test_shared_corner_counts_once_on_each_intended_face(self):
        candidates = [Window((0, 1, 0), face) for face in ('N', 'W', 'N')]
        writes, report = self.select(candidates, {(0, 1, 0): 'bricks'}, {(0, 0)})
        self.assertEqual(writes, {})
        self.assertEqual(dict(report), {
            face: {'corner pier / tread too short': 1} for face in ('N', 'W')})

    def test_axis_aligned_mass_keeps_exact_glass_positions_and_states(self):
        columns = {(x, z) for x in range(8) for z in range(5)}
        walls = {c for c in columns if c[0] in (0, 7) or c[1] in (0, 4)}
        blocks = {(x, y, z): 'bricks' for x, z in walls for y in range(4)}
        candidates = [Window((x, y, z), face) for x, z in sorted(walls)
            for face, (dx, dz) in DIRS.items() if (x + dx, z + dz) not in columns
            for y in (1, 2)]
        expected = {(x, y, z): 'glass_pane[east=true,west=true]'
            for x in range(1, 7) for y in (1, 2) for z in (0, 4)}
        expected.update({(x, y, z): 'glass_pane[north=true,south=true]'
            for x in (0, 7) for y in (1, 2) for z in range(1, 4)})
        writes, report = self.select(candidates, blocks, walls, columns)
        self.assertEqual(writes, expected)
        for face in DIRS:
            self.assertEqual(sum(report[face].values()),
                sum(w.face == face for w in candidates))
        self.assertEqual(self.select(list(reversed(candidates)), blocks, walls, columns),
                         (writes, report))

    def test_report_names_failed_jamb_lintel_and_sill_checks(self):
        # Remove one of the four structural bounds from a single opening.
        blocks = {(x, y, 0): 'bricks' for x in (-1, 0, 1) for y in range(3)}
        walls = {(-1, 0), (0, 0), (1, 0)}
        columns = walls | {(x, 1) for x in (-1, 0, 1)}
        for pos, failure in (((1, 1, 0), 'jamb'), ((0, 2, 0), 'lintel'),
                             ((0, 0, 0), 'sill')):
            with self.subTest(failure=failure):
                shell = {p: s for p, s in blocks.items() if p != pos}
                writes, report = self.select([Window((0, 1, 0), 'N', stepped=True)],
                                            shell, walls, columns)
                self.assertEqual(writes, {})
                self.assertEqual(report['N'], {f'unbounded opening: non-structural {failure}': 1})

    def test_structural_block_outside_wall_is_not_a_jamb(self):
        blocks = {(x, y, 0): 'bricks' for x in (-1, 0, 1) for y in range(3)}
        _, report = self.select([Window((0, 1, 0), 'N')], blocks,
                                {(-1, 0), (0, 0)}, columns={(-1, 0), (0, 0), (1, 0), (0, 1)})
        self.assertEqual(report['N'], {'unbounded opening: jamb outside wall': 1})

    def test_multiple_missing_bounds_count_the_whole_opening_once(self):
        blocks = {(x, y, 0): 'bricks' for x in (-1, 0, 1) for y in range(4)}
        del blocks[1, 1, 0]
        del blocks[0, 3, 0]
        candidates = [Window((0, y, 0), 'N') for y in (1, 2)]
        writes, report = self.select(candidates, blocks, {(-1, 0), (0, 0), (1, 0)},
                                    columns={(-1, 0), (0, 0), (1, 0), (0, 1)})
        self.assertEqual(writes, {})
        self.assertEqual(report['N'], {
            'unbounded opening: non-structural jamb, non-structural lintel': 2})

    def test_perpendicular_intended_glass_cannot_supply_a_jamb(self):
        columns = {(-1, 0), (0, 0), (1, 0), (0, 1), (1, -1), (1, 1)}
        blocks = {(x, y, z): 'bricks' for x, z in columns for y in range(3)}
        candidates = [Window((0, 1, 0), 'N'), Window((1, 1, 0), 'E')]
        writes, report = self.select(candidates, blocks, columns)
        self.assertEqual(writes, {(1, 1, 0): 'glass'})
        self.assertEqual(report['N'], {'unbounded opening: jamb meets another opening': 1})
        self.assertEqual(report['E'], {'solid-glass': 1})

    def test_selected_evidenced_corner_counts_on_both_faces(self):
        walls = {(0, 0), (1, 0), (0, 1)}
        blocks = {(x, y, z): 'bricks' for x, z in walls for y in range(3)}
        candidates = [Window((0, 1, 0), face, corner_evidence=True) for face in ('N', 'W')]
        writes, report = self.select(candidates, blocks, walls)
        self.assertEqual(writes, {(0, 1, 0): 'glass'})
        self.assertEqual(dict(report), {face: {'solid-glass': 1} for face in ('N', 'W')})

    def test_rotated_mass_reports_every_intention_and_large_loss(self):
        # A 26.6-degree parallelogram, with one- and two-block treads.
        columns = {(x + z // 2, z) for x in range(12) for z in range(20)}
        walls = {c for c in columns if any((c[0] + dx, c[1] + dz) not in columns
            for dx in (-1, 0, 1) for dz in (-1, 0, 1))}
        blocks = {(x, y, z): 'bricks' for x, z in walls for y in range(4)}
        candidates = [Window((x, y, z), face, stepped=True) for x, z in sorted(walls)
            for face, (dx, dz) in DIRS.items() if (x + dx, z + dz) not in columns
            for y in (1, 2)]
        _, report = self.select(candidates, blocks, walls, columns)
        self.assertEqual(sum(report['E'].values()), 40)
        self.assertEqual(sum(report['W'].values()), 40)
        self.assertEqual(report['E']['corner pier / tread too short'], 22)
        self.assertEqual(report['W']['corner pier / tread too short'], 22)
        for face in DIRS:
            self.assertEqual(sum(report[face].values()),
                sum(w.face == face for w in candidates))

    def test_protected_and_non_wall_omissions_are_distinct(self):
        candidates = [Window((0, 1, 0), 'N'), Window((1, 1, 0), 'N')]
        _, report = self.select(candidates, {(0, 1, 0): 'bricks'}, {(0, 0), (1, 0)},
                                protected={(0, 1, 0)})
        self.assertEqual(report['N'], {'protected entrance': 1, 'non-wall': 1})

    def test_report_quantifies_loss_against_intended_glazing(self):
        report = {'N': {'solid-glass': 2, 'corner pier / tread too short': 8}}
        self.assertEqual(report_lines('A', report), [
            'A face N: windows intended=10 selected=2 (20.0%) panes=0 solid-glass=2 '
            'omitted=8 (corner pier / tread too short=8)'])

    def test_report_with_no_selected_glass_still_shows_total_loss(self):
        self.assertEqual(report_lines('A', {'N': {'corner pier / tread too short': 6}}), [
            'A face N: windows intended=6 selected=0 (0.0%) panes=0 solid-glass=0 '
            'omitted=6 (corner pier / tread too short=6)'])


class WindowPolicyTest(unittest.TestCase):
    rectangle = {(x, z) for x in range(10, 22) for z in range(20, 25)}
    stepped = {(x, z) for x in range(10, 22) for z in range(20, 25 + (x - 10) // 3)}

    def run_builder(self, kind, cells=None, *, terrain=49, edits=None, door=None,
                    reverse=False, corner=None, pier=False):
        cells = set(self.rectangle if cells is None else cells)
        group = {'nos': ['A'], 'front': 'S', 'mode': 'each'}
        row = {'storey': 1, 'glazed_h': 2, 'pier': 1}
        if not pier:
            row['rhythm'] = 'W'
        if corner:
            row['corner_glazing'] = [corner]
        faces = list(DIRS)
        if reverse:
            faces.reverse()
        spec = {'name': 'window_test', 'ways': {'1': 'A'}, 'groups': [group],
                'roofs': {'A': {'kind': 'flat', 'material': 'brick'}}, 'storeys': {'A': 2},
                'floor_min': 50, 'modifies': [], 'step_max': 4, 'ledge_stair': False,
                'door_frame': True, 'keep_edits': False, 'wall': 'bricks', 'floor': 'spruce_planks',
                'facade_plan': {'A': {'faces': {face: {'glazing': [dict(row)]} for face in faces}}}}
        world = SimpleNamespace(block=lambda x, y, z: 'minecraft:stone' if y <= 49 else 'minecraft:air')
        if kind == 'feature':
            b = F.Build(spec, footprints={'A': cells}, world=world)
            h = b.features[0]
            h.b = 50
        else:
            b = H.Build.__new__(H.Build)
            h = H.House('A', cells, group)
            h.b = 50
            b.p, b.live = Plan('window_test'), world
            b.owner = {c: 0 for c in cells}
            b.houses, b.groups = [h], [group]
            b.spec = {'roofs': {'A': 'brick'}}
        b.foreign, b.shared = set(), set()
        b.shell(0, h)
        b.roof()
        if door:
            h.door = door
            h.door_cells = {door}
            for y in (51, 52):
                b.set(door[0], y, door[1], 'oak_door[facing=south,half=lower]')
        for pos, state in (edits or {}).items():
            b.set(*pos, state)
        original = dict(b.p.cells)
        from world_frame import load_frame
        lidar = SimpleNamespace(
            missing=lambda *a: ['missing'] if terrain is None else [],
            sample=lambda product, east, north, *a: np.full(np.asarray(east).shape, terrain - load_frame().y_offset))
        with mock.patch('lidar_provider.tile_client', return_value=lidar), redirect_stdout(io.StringIO()) as out:
            if kind == 'feature':
                original_runs = b.face_runs
                if reverse:
                    b.face_runs = lambda f, face: list(reversed(original_runs(f, face)))
                b.windows(h)
                report = '\n'.join(b._window_lines)
            else:
                b.windows(0, h)
                report = out.getvalue()
        return b.p.cells, original, report, b, h

    def glass(self, plan):
        return {p: s for p, s in plan.items() if 'glass' in s}

    def test_straight_bounded_panes_and_opaque_corners_in_both_builders(self):
        for kind in ('feature', 'house'):
            with self.subTest(kind=kind):
                plan, _, report, b, h = self.run_builder(kind)
                glass = self.glass(plan)
                self.assertTrue(any('glass_pane' in s for s in glass.values()))
                for x, y, z in glass:
                    self.assertLessEqual(sum(b.exposed((x, z), face) for face in DIRS), 1)
                self.assertIn('corner pier / tread too short', report)

    def test_stepped_treads_get_solid_glass_in_both_builders(self):
        for kind in ('feature', 'house'):
            with self.subTest(kind=kind):
                plan, original, report, b, h = self.run_builder(kind, self.stepped)
                south_glass = {p: s for p, s in self.glass(plan).items() if b.exposed((p[0], p[2]), 'S')}
                self.assertTrue(south_glass)
                self.assertTrue(all(s == 'minecraft:glass' for s in south_glass.values()))
                for p in self.glass(plan):
                    self.assertLessEqual(sum(b.exposed((p[0], p[2]), f) for f in DIRS), 1)
                self.assertIn('solid-glass=', report)
                self.assertTrue(set(plan) <= set(original), 'windows may not enlarge the shell')

    def test_one_block_staircase_keeps_opaque_teeth_and_explains_omissions(self):
        cells = {(x, z) for x in range(10, 22) for z in range(20, 25 + x - 10)}
        for kind in ('feature', 'house'):
            plan, _, report, b, h = self.run_builder(kind, cells)
            for p in self.glass(plan):
                self.assertLessEqual(sum(b.exposed((p[0], p[2]), f) for f in DIRS), 1)
            self.assertIn('corner pier / tread too short', report)

    def test_outside_sill_and_unknown_ground_in_both_builders(self):
        for kind in ('feature', 'house'):
            for height, reason in ((60, 'below outside sill'), (None, 'outside ground unavailable'),
                                   (float('nan'), 'outside ground unavailable')):
                with self.subTest(kind=kind, height=height):
                    plan, _, report, _, _ = self.run_builder(kind, terrain=height)
                    self.assertFalse(self.glass(plan))
                    self.assertIn(reason, report)

    def test_adjacent_door_and_its_jambs_are_preserved(self):
        for kind in ('feature', 'house'):
            plan, original, _, _, _ = self.run_builder(kind, door=(14, 24))
            for x in (13, 14, 15):
                for y in (51, 52):
                    self.assertEqual(plan[x, y, 24], original[x, y, 24])

    def test_missing_lintel_or_door_jamb_does_not_support_an_opening(self):
        for kind in ('feature', 'house'):
            for state in ('air', 'oak_door[facing=south,half=upper]', 'glass_pane[east=true,west=true]'):
                # A hole immediately above the intended south opening invalidates it.
                plan, _, report, _, _ = self.run_builder(kind, edits={(16, 53, 24): state})
                self.assertNotIn('glass', plan[16, 52, 24])
                self.assertIn('unbounded opening', report)

    def test_normal_wall_cannot_give_panes_a_third_arm(self):
        for kind in ('feature', 'house'):
            plan, _, _, _, _ = self.run_builder(kind, edits={(16, 52, 23): 'bricks'})
            self.assertEqual(plan[16, 52, 24], 'minecraft:glass')

    def test_face_and_run_order_do_not_change_the_result(self):
        for kind in ('feature', 'house'):
            a = self.run_builder(kind, self.stepped)[0]
            b = self.run_builder(kind, self.stepped, reverse=True)[0]
            self.assertEqual(a, b)

    def test_piers_survive_and_glass_stays_below_the_roof(self):
        plan, original, _, _, _ = self.run_builder('feature', pier=True)
        for x in (10, 12, 15, 18, 21):
            for y in (51, 52):
                self.assertEqual(plan[x, y, 24], original[x, y, 24])
        self.assertTrue(all(50 < p[1] < 56 for p in self.glass(plan)))

    def test_explicit_photo_corner_uses_solid_glass(self):
        import provenance
        corner = {'at': [10, 20], 'provenance': provenance.provenance_record(source='photo_read', confidence='high')}
        plan, _, _, _, _ = self.run_builder('feature', corner=corner)
        self.assertEqual(plan[10, 51, 20], 'minecraft:glass')


class CornerSchemaTest(unittest.TestCase):
    def test_corner_permission_requires_resolved_photo_or_aerial(self):
        import facade_plan
        import provenance
        row = {'storey': 1, 'glazed_h': 2, 'pier': 1}
        for source in ('photo_read', 'aerial', 'inference', 'vector_map'):
            for unresolved in ('', 'no view'):
                record = provenance.provenance_record(source=source, confidence='high', unresolved_reason=unresolved)
                candidate = {**row, 'corner_glazing': [{'at': [10, 20], 'provenance': record}]}
                if source in ('photo_read', 'aerial') and not unresolved:
                    facade_plan.validate_glazing_rows([candidate], n_storeys=2, path='test')
                else:
                    with self.assertRaises(facade_plan.FacadePlanError):
                        facade_plan.validate_glazing_rows([candidate], n_storeys=2, path='test')
        for bad in (None, {}, [{'at': [10, 20]}], [{'at': [1], 'provenance': record}]):
            with self.assertRaises(facade_plan.FacadePlanError):
                facade_plan.validate_glazing_rows([{**row, 'corner_glazing': bad}], n_storeys=2, path='test')

    def test_ground_grid_does_not_fabricate_unknown_heights(self):
        from floor_reference import outside_ground
        grid = {'ground_y': np.array([[49, float('nan')]]), 'x0': 10, 'z0': 20}
        self.assertEqual(outside_ground([(10, 20), (11, 20), (12, 20)], grid),
                         {(10, 20): 49, (11, 20): None, (12, 20): None})


class PlantStructureTest(unittest.TestCase):
    def test_woodland_plants_cannot_terminate_a_window_opening(self):
        from window_openings import structural
        for state in ('short_dry_grass', 'tall_dry_grass', 'bush',
                      'leaf_litter[facing=east,segment_amount=4]', 'closed_eyeblossom'):
            with self.subTest(state=state):
                self.assertFalse(structural(f'minecraft:{state}'))
        self.assertTrue(structural('brown_mushroom_block'))
