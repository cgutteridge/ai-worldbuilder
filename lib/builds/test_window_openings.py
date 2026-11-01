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
from window_openings import DIRECTIONS


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
        faces = list(DIRECTIONS)
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
                    self.assertLessEqual(sum(b.exposed((x, z), face) for face in DIRECTIONS), 1)
                self.assertIn('corner pier / tread too short', report)

    def test_stepped_treads_get_solid_glass_in_both_builders(self):
        for kind in ('feature', 'house'):
            with self.subTest(kind=kind):
                plan, original, report, b, h = self.run_builder(kind, self.stepped)
                south_glass = {p: s for p, s in self.glass(plan).items() if b.exposed((p[0], p[2]), 'S')}
                self.assertTrue(south_glass)
                self.assertTrue(all(s == 'minecraft:glass' for s in south_glass.values()))
                for p in self.glass(plan):
                    self.assertLessEqual(sum(b.exposed((p[0], p[2]), f) for f in DIRECTIONS), 1)
                self.assertIn('solid-glass=', report)
                self.assertTrue(set(plan) <= set(original), 'windows may not enlarge the shell')

    def test_one_block_staircase_keeps_opaque_teeth_and_explains_omissions(self):
        cells = {(x, z) for x in range(10, 22) for z in range(20, 25 + x - 10)}
        for kind in ('feature', 'house'):
            plan, _, report, b, h = self.run_builder(kind, cells)
            for p in self.glass(plan):
                self.assertLessEqual(sum(b.exposed((p[0], p[2]), f) for f in DIRECTIONS), 1)
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
