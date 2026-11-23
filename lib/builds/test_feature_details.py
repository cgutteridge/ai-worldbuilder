"""Tests for feature details seams."""

from __future__ import annotations

from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
import io
import json
import re
import tempfile
import unittest

from block_store import MemoryBlocks
from feature_build_fixtures import (
    _StoneWorld,
    _glazing_plan,
    start_lidar_fixture as setUpModule,
    stop_lidar_fixture as tearDownModule,
)
from script_env import TOOL_ROOT
import feature_build as F
import feature_spec as FS
import site_kit


class DetailsBuildTest(unittest.TestCase):
    """Seam: details place after the shell; dry-run prints each kind and source."""

    def setUp(self):
        self.cells = {(x, z) for x in range(10, 18) for z in range(20, 25)}

    def spec(self, details):
        return {
            'name': 'unit_details',
            'ways': {'99': 'A'},
            'storeys': {'A': 2},
            'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
            'groups': [{'nos': ['A'], 'front': 'S', 'mode': 'each'}],
            'modifies': [],
            'floor_min': 50,
            'step_max': 4,
            'door_frame': True,
            'keep_edits': False,
            'wall': 'bricks',
            'floor': 'smooth_stone',
            'ledge_stair': False,
            'facade_plan': _glazing_plan({}),
            'details': details,
        }

    def test_chimney_spec_normalizes_dimensions_and_refuses_invalid_shape(self):
        base = {'name': 'stack', 'ways': {'1': 'A'}, 'storeys': {'A': 1},
                'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
                'groups': [{'nos': ['A'], 'front': 'N'}]}
        row = {'kind': 'chimney', 'at': [13, 57, 22], 'height': 4,
               'base_diameter': 5, 'top_diameter': 3, 'shape': 'round', 'source': 'photo'}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'stack.json'
            path.write_text(json.dumps({**base, 'details': {'A': [row]}}))
            loaded = FS.load_spec(path)
            self.assertEqual(loaded['details']['A'][0]['size'], [5, 4, 5])
            path.write_text(json.dumps({**base, 'details': {'A': [{**row, 'shape': 'triangle'}]}}))
            with self.assertRaisesRegex(SystemExit, 'round or square'):
                FS.load_spec(path)

    def test_roof_and_facade_details_place_and_dry_run_lists_source(self):
        # Flat roof membrane sits at floor + 2*STOREY_H = 56.
        details = {
            'A': [
                {
                    'kind': 'plant_room',
                    'at': [12, 57, 21],
                    'size': [2, 2, 2],
                    'source': 'aerial plant room',
                },
                {
                    'kind': 'skylight',
                    'at': [15, 56, 22],
                    'size': [1, 1, 1],
                    'source': 'aerial skylight',
                },
                {
                    'kind': 'awning_like',
                    'at': [13, 55, 24],
                    'size': [2, 1, 1],
                    'face': 'S',
                    'source': 'street photo entrance awning_like',
                },
                {
                    'kind': 'drainpipe',
                    'at': [10, 50, 24],
                    'size': [1, 6, 1],
                    'face': 'S',
                    'source': 'street photo south pipe',
                },
            ],
        }
        buf = io.StringIO()
        with redirect_stdout(buf):
            plan = F.build(
                self.spec(details), footprints={'A': self.cells}, world=_StoneWorld(),
            )
        out = buf.getvalue()
        # Plant room sits on a solid pad (not hovering on the membrane slab).
        self.assertIn('minecraft:gray_concrete', plan.cells.get((12, 57, 21), ''))
        self.assertIn('minecraft:gray_concrete', plan.cells.get((13, 58, 22), ''))
        # Skylight sits on the roof membrane.
        sky = plan.cells.get((15, 56, 22), '')
        self.assertTrue('glass' in sky, sky)
        # Awning-like overhangs one block south of the south wall.
        overhang = plan.cells.get((13, 55, 25), '') or plan.cells.get((14, 55, 25), '')
        self.assertTrue('slab' in overhang, f'expected awning_like slab, got {overhang!r}')
        # Drainpipe on the south wall column.
        pipe = plan.cells.get((10, 52, 24), '')
        self.assertTrue('iron' in pipe, pipe)
        for kind, src in (
            ('plant_room', 'aerial plant room'),
            ('skylight', 'aerial skylight'),
            ('awning_like', 'street photo entrance awning_like'),
            ('drainpipe', 'street photo south pipe'),
        ):
            self.assertRegex(out, rf'detail {kind}.*source={re.escape(src)}')

    def test_detail_sign_and_fascia_emit_the_label_options(self):
        """A detail sign uses the label wood; a fascia band uses the label colour."""
        details = {
            'A': [
                {
                    'kind': 'sign',
                    'at': [12, 52, 24],
                    'size': [1, 1, 1],
                    'face': 'S',
                    'source': 'street photo sign',
                },
                {
                    'kind': 'fascia_band',
                    'at': [14, 54, 24],
                    'size': [2, 1, 1],
                    'face': 'S',
                    'source': 'street photo fascia',
                },
            ],
        }
        spec = self.spec(details)
        spec['signs'] = {'A': {'wood': 'dark_oak', 'fascia_colour': 'black', 'text_colour': 'white'}}
        with redirect_stdout(io.StringIO()):
            plan = F.build(spec, footprints={'A': self.cells}, world=_StoneWorld())
        sign = plan.cells.get((12, 52, 25), '')
        self.assertIn('dark_oak_wall_sign[facing=south]', sign)
        self.assertEqual(plan.cells.get((14, 54, 24)), 'minecraft:black_concrete')
        self.assertEqual(plan.cells.get((15, 54, 24)), 'minecraft:black_concrete')


    def detail_plan(self, rows, supports):
        """Plan only details over explicit MemoryBlocks roof fixtures."""
        roof = MemoryBlocks(supports, default='air')
        builder = F.Build(self.spec({'A': rows}), footprints={'A': self.cells}, world=_StoneWorld())
        for pos in supports:
            builder.p.set(*pos, roof.read(pos))
        builder.details()
        return builder

    def test_tapered_square_chimney_has_open_rim_and_band(self):
        row = {'kind': 'chimney', 'at': [13, 57, 22], 'size': [5, 4, 5],
               'base_diameter': 5, 'top_diameter': 3, 'height': 4, 'shape': 'square',
               'material': 'white_concrete', 'rim': 'stone',
               'band': {'offset': 1, 'block': 'black_concrete'}, 'source': 'photo fixture'}
        supports = {(x, 56, z): 'smooth_stone' for x in range(11, 16) for z in range(20, 25)}
        builder = self.detail_plan([row], supports)
        self.assertEqual(builder.p.cells[(15, 57, 22)], 'minecraft:white_concrete')
        self.assertEqual(builder.p.cells[(14, 60, 22)], 'minecraft:stone')
        self.assertEqual(builder.p.cells.get((15, 60, 22), 'minecraft:air'), 'minecraft:air')
        self.assertEqual(builder.p.cells[(13, 60, 22)], 'minecraft:air')
        band = [x for (x, y, z), state in builder.p.cells.items() if y == 58 and state == 'minecraft:black_concrete']
        self.assertEqual(max(band) - min(band) + 1, 4)

    def test_chimney_solidifies_its_slab_base_without_hover_gap(self):
        row = {'kind': 'chimney', 'at': [13, 57, 22], 'size': [3, 4, 3],
               'base_diameter': 3, 'top_diameter': 3, 'height': 4,
               'shape': 'round', 'source': 'photo fixture'}
        supports = {(x, 56, z): 'smooth_stone_slab[type=bottom]' for x in range(12, 15) for z in range(21, 24)}
        builder = self.detail_plan([row], supports)
        self.assertEqual(builder.p.cells[(12, 56, 22)], 'minecraft:smooth_stone')
        self.assertEqual(builder.p.cells[(12, 57, 22)], 'minecraft:white_concrete')
        self.assertEqual(builder.p.cells[(13, 60, 22)], 'minecraft:air')

    def test_vent_pad_retargets_inner_stair_when_the_back_stair_becomes_a_full_block(self):
        # Same neighbour pair as the west roof at (6189, 61, -5608): north inner
        # corner, east-facing stair one step south. The vent pad replaces that
        # south stair, so the corner has to become straight.
        supports = {
            (12, 56, 21): 'brick_stairs[facing=north,half=bottom,shape=inner_right,waterlogged=false]',
            (12, 56, 22): 'brick_stairs[facing=east,half=bottom,shape=straight,waterlogged=false]',
        }
        row = {'kind': 'vent', 'at': [12, 57, 22], 'size': [1, 1, 1], 'source': 'fixture'}
        builder = self.detail_plan([row], supports)
        got = builder.p.cells[(12, 56, 21)]
        self.assertIn('facing=north', got)
        self.assertIn('shape=straight', got)
        self.assertEqual(builder.p.cells[(12, 56, 22)], 'minecraft:smooth_stone')

    def test_roof_detail_solidifies_deepslate_family_slab_and_stair(self):
        row = {'kind': 'vent', 'at': [12, 57, 21], 'size': [2, 1, 1], 'source': 'fixture'}
        spec = self.spec({'A': [row]})
        spec['roofs']['A']['material'] = 'deepslate_tile'
        builder = F.Build(spec, footprints={'A': self.cells}, world=_StoneWorld())
        builder.p.set(12, 56, 21, 'deepslate_tile_slab[type=bottom]')
        builder.p.set(13, 56, 21, 'deepslate_tile_stairs[facing=north,half=bottom]')
        builder.details()
        for x in (12, 13):
            self.assertEqual(builder.p.cells[(x, 56, 21)], 'minecraft:deepslate_tiles')
        self.assertNotIn('minecraft:deepslate_tile', builder.p.cells.values())
        valid_ids = set((TOOL_ROOT / 'config/minecraft-block-ids-26.2.txt').read_text().splitlines())
        self.assertTrue(all(state.split('[', 1)[0] in valid_ids for state in builder.p.cells.values()))

    def test_chimney_rough_check_does_not_hide_corrupt_lidar(self):
        row = {'kind': 'chimney', 'at': [13, 57, 22], 'size': [3, 4, 3],
               'base_diameter': 3, 'top_diameter': 3, 'height': 4,
               'shape': 'round', 'source': 'photo fixture'}
        supports = {(x, 56, z): 'smooth_stone' for x in range(12, 15) for z in range(21, 24)}
        with mock.patch('lidar_provider.mc_heights', side_effect=ValueError('corrupt lidar')):
            with self.assertRaisesRegex(ValueError, 'corrupt lidar'):
                self.detail_plan([row], supports)

    def test_vertical_chimney_is_hollow_open_and_reports_rough_lidar_check(self):
        row = {'kind': 'chimney', 'at': [13, 57, 22], 'size': [5, 4, 5],
               'base_diameter': 5, 'top_diameter': 5, 'height': 4, 'shape': 'round',
               'material': 'white_concrete', 'source': 'photo fixture'}
        supports = {(x, 56, z): 'smooth_stone' for x in range(11, 16) for z in range(20, 25)}
        builder = self.detail_plan([row], supports)
        self.assertEqual(builder.p.cells.get((15, 60, 22)), 'minecraft:white_concrete')
        self.assertTrue(all(builder.p.cells.get((13, y, 22), 'minecraft:air') == 'minecraft:air' for y in range(57, 61)))
        self.assertFalse(any(y > 60 for x, y, z in builder.p.cells))
        self.assertTrue(any('planned height=4' in line and 'lidar' in line for line in builder.detail_report()))

    def test_skylight_is_recessed_and_rejects_whole_box_off_roof(self):
        row = {'kind': 'skylight', 'at': [12, 56, 21], 'size': [2, 1, 2], 'source': 'fixture'}
        supports = {(x, 56, z): 'smooth_stone' for x in (12, 13) for z in (21, 22)}
        builder = self.detail_plan([row], supports)
        self.assertTrue(all(builder.p.cells[pos] == 'minecraft:glass' for pos in supports))
        self.assertFalse(any(y > 56 for x, y, z in builder.p.cells))
        del supports[(13, 56, 22)]
        rejected = self.detail_plan([row], supports)
        self.assertFalse(any('glass' in state for state in rejected.p.cells.values()))
        self.assertTrue(any('cells off roof' in line and 'not built' in line and 'A at (12,56,21)' in line for line in rejected.detail_report()))

    def test_vents_fill_requested_box_and_cap_without_bars(self):
        for size in ([1, 1, 1], [2, 2, 2]):
            row = {'kind': 'vent', 'at': [12, 57, 21], 'size': size, 'source': 'fixture'}
            support = {(x, 56, z): 'smooth_stone' for x in (12, 13) for z in (21, 22)}
            builder = self.detail_plan([row], support)
            for x in range(12, 12 + size[0]):
                for z in range(21, 21 + size[2]):
                    for y in range(57, 57 + size[1]):
                        self.assertEqual(builder.p.cells[(x, y, z)], 'minecraft:gray_concrete')
                    self.assertIn('iron_trapdoor', builder.p.cells[(x, 57 + size[1], z)])
            self.assertFalse(any('iron_bars' in state for state in builder.p.cells.values()))

    def test_supported_roof_detail_box_unchanged(self):
        row = {'kind': 'plant_room', 'at': [12, 57, 21], 'size': [2, 2, 2], 'source': 'fixture'}
        supports = {(x, 56, z): 'smooth_stone' for x in (12, 13) for z in (21, 22)}
        builder = self.detail_plan([row], supports)
        for x in (12, 13):
            for z in (21, 22):
                for y in (57, 58):
                    self.assertEqual(builder.p.cells[(x, y, z)], 'minecraft:gray_concrete')
                self.assertIn('iron_trapdoor', builder.p.cells[(x, 59, z)])
        self.assertFalse(any('unsupported' in line for line in builder.detail_report()))

    def test_every_roof_detail_clips_diagonal_unsupported_columns(self):
        for kind in sorted(FS.ROOF_DETAIL_KINDS - {'skylight', 'chimney'}):
            with self.subTest(kind=kind):
                row = {'kind': kind, 'at': [12, 57, 21], 'size': [2, 2, 2], 'source': 'fixture'}
                # Stair and slab both support a detail. The missing diagonal
                # corner must stay empty through the whole recipe, caps too.
                supports = {
                    (12, 56, 21): 'smooth_stone_slab[type=bottom]',
                    (13, 56, 21): 'smooth_stone',
                    (12, 56, 22): 'brick_stairs[facing=north,half=bottom,shape=straight]',
                }
                builder = self.detail_plan([row], supports)
                self.assertTrue(any(p[1] >= 57 for p in builder.p.cells))
                self.assertFalse(any('iron_bars' in state for state in builder.p.cells.values()))
                for y in (57, 58, 59):
                    self.assertNotIn((13, y, 22), builder.p.cells)
                self.assertIn(
                    f'detail {kind} A at (12,57,21) unsupported cells=1; clipped',
                    builder.detail_report(),
                )

    def test_build_rejects_whole_skylight_overhanging_roof(self):
        row = {'kind': 'skylight', 'at': [17, 56, 22], 'size': [2, 1, 1], 'source': 'fixture'}
        with redirect_stdout(io.StringIO()) as output:
            plan = F.build(self.spec({'A': [row]}), footprints={'A': self.cells}, world=_StoneWorld())
        self.assertNotIn('glass', plan.cells.get((17, 56, 22), ''))
        self.assertNotIn('glass', plan.cells.get((18, 56, 22), ''))
        self.assertIn('cells off roof', output.getvalue())
        self.assertIn('not built', output.getvalue())

    def test_unsupported_vent_anchor_is_clipped_even_if_box_has_support(self):
        row = {'kind': 'vent', 'at': [12, 57, 21], 'size': [2, 2, 1], 'source': 'fixture'}
        builder = self.detail_plan([row], {(13, 56, 21): 'smooth_stone'})
        self.assertNotIn((12, 57, 21), builder.p.cells)
        self.assertNotIn((12, 58, 21), builder.p.cells)

    def test_prior_detail_does_not_become_roof_support(self):
        rows = [
            {'kind': 'plant_room', 'at': [12, 57, 21], 'size': [1, 2, 1], 'source': 'fixture'},
            {'kind': 'skylight', 'at': [12, 59, 21], 'size': [1, 1, 1], 'source': 'fixture'},
        ]
        builder = self.detail_plan(rows, {(12, 56, 21): 'smooth_stone'})
        self.assertIn('iron_trapdoor', builder.p.cells[(12, 59, 21)])
        self.assertTrue(any('skylight A at (12,59,21)' in line and 'not built' in line for line in builder.detail_report()))

    def test_air_fluid_and_thin_roof_edge_are_not_support(self):
        for state in ('air', 'cave_air', 'void_air', 'water', 'iron_bars', 'moss_carpet', 'iron_trapdoor'):
            with self.subTest(state=state):
                row = {'kind': 'skylight', 'at': [12, 57, 21], 'size': [1, 1, 1], 'source': 'fixture'}
                builder = self.detail_plan([row], {(12, 56, 21): state})
                self.assertNotIn((12, 57, 21), builder.p.cells)

    def test_zone_material_and_full_roof_edge_support_details(self):
        rows = [{'kind': 'skylight', 'at': [12, 56, 21], 'size': [2, 1, 1], 'source': 'fixture'}]
        builder = F.Build(self.spec({'A': rows}), footprints={'A': self.cells}, world=_StoneWorld())
        builder.features[0].roof.update({'zone_materials': {'60': 'deepslate_tile'}, 'edge': {'block': 'polished_andesite'}})
        builder.p.set(12, 56, 21, 'deepslate_tile_stairs[facing=north,half=bottom,shape=straight]')
        builder.p.set(13, 56, 21, 'polished_andesite')
        builder.details()
        self.assertIn('glass', builder.p.cells[(12, 56, 21)])
        self.assertIn('glass', builder.p.cells[(13, 56, 21)])


class PvArraysTest(unittest.TestCase):
    """Seam: roofs[label].pv_arrays -> daylight_detector rows on the finished flat roof."""

    def setUp(self):
        self.cells = {(x, z) for x in range(10, 18) for z in range(20, 25)}

    def spec(self, arrays, **roof):
        return {
            'name': 'unit_pv',
            'ways': {'99': 'A'},
            'storeys': {'A': 2},
            'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone', 'pv_arrays': arrays, **roof}},
            'groups': [{'nos': ['A'], 'front': 'S', 'mode': 'each'}],
            'modifies': [],
            'floor_min': 50,
            'step_max': 4,
            'door_frame': True,
            'keep_edits': False,
            'wall': 'bricks',
            'floor': 'smooth_stone',
            'ledge_stair': False,
            'facade_plan': _glazing_plan({}),
        }

    def build(self, arrays, **roof):
        out = io.StringIO()
        with redirect_stdout(out):
            plan = F.build(self.spec(arrays, **roof), footprints={'A': self.cells}, world=_StoneWorld())
        return plan, out.getvalue()

    def detectors(self, plan):
        return {q for q, s in plan.cells.items() if 'daylight_detector' in s}

    def test_rows_along_x_every_pitch_sit_on_a_solid_roof_block(self):
        row = {'box': [11, 21, 16, 23], 'rows': 'x', 'pitch': 2, 'source': 'aerial PV'}
        plan, out = self.build([row])
        # Flat membrane at floor + 2 * STOREY_H = 56; detectors one above.
        want = {(x, 57, z) for x in range(11, 17) for z in (21, 23)}
        self.assertEqual(self.detectors(plan), want)
        # power is set by daylight in game: never planned, so readback cannot mismatch on it
        self.assertEqual({plan.cells[q] for q in want}, {'minecraft:daylight_detector[inverted=false]'})
        for x, _, z in want:
            self.assertEqual(plan.cells[(x, 56, z)], 'minecraft:smooth_stone')
        self.assertIn('smooth_stone_slab', plan.cells[(12, 56, 22)])
        self.assertIn('pv_array A box (11,21)..(16,23) rows=x pitch=2 detectors=12 clipped=0 source=aerial PV', out)

    def test_rows_along_z_with_pitch_three(self):
        row = {'box': [10, 20, 16, 24], 'rows': 'z', 'pitch': 3, 'source': 'aerial PV'}
        plan, _ = self.build([row])
        self.assertEqual(self.detectors(plan), {(x, 57, z) for x in (10, 13, 16) for z in range(20, 25)})

    def test_pitch_defaults_to_two(self):
        plan, _ = self.build([{'box': [11, 21, 11, 24], 'rows': 'z', 'source': 'aerial PV'}])
        self.assertEqual(len(self.detectors(plan)), 4)

    def test_columns_off_the_footprint_are_clipped_and_reported(self):
        row = {'box': [15, 21, 19, 21], 'rows': 'x', 'pitch': 2, 'source': 'aerial PV'}
        plan, out = self.build([row], edge={'block': 'gray_concrete', 'where': 'outer'})
        self.assertEqual(self.detectors(plan), {(x, 57, 21) for x in (15, 16, 17)})
        self.assertIn('detectors=3 clipped=2', out)

    def test_detail_on_the_roof_wins_over_a_detector(self):
        spec = self.spec([{'box': [11, 21, 16, 21], 'rows': 'x', 'pitch': 2, 'source': 'aerial PV'}])
        spec['details'] = {'A': [{'kind': 'vent', 'at': [12, 57, 21], 'size': [1, 1, 1], 'source': 'aerial vent'}]}
        with redirect_stdout(io.StringIO()):
            plan = F.build(spec, footprints={'A': self.cells}, world=_StoneWorld())
        self.assertEqual(plan.cells[(12, 57, 21)], 'minecraft:gray_concrete')
        self.assertNotIn((12, 57, 21), self.detectors(plan))
        self.assertEqual(len(self.detectors(plan)), 5)

    def test_deck_mode_zone_material_and_nonroof_tops(self):
        from types import SimpleNamespace
        build = object.__new__(F.Build)
        build.p = site_kit.Plan('pv_fixture')
        roof={'material':'smooth_stone','zone_materials':{'1':'polished_diorite'},
              'pv_arrays':[{'box':[0,0,4,0],'rows':'x','pitch':1,'source':'fixture'}]}
        build.features=[SimpleNamespace(roof=roof,cells={(x,0) for x in range(5)},no='A')]
        for x, y, material in [(0,10,'smooth_stone_slab'),(1,10,'polished_diorite_slab'),(2,10,'smooth_stone'),(3,12,'smooth_stone'),(4,10,'gray_concrete')]:
            build.p.set(x,y,0,material)
        build.set=lambda x,y,z,state:build.p.set(x,y,z,state)
        F.Build.pv_rows(build)
        self.assertEqual(self.detectors(build.p),{(x,11,0) for x in range(3)})
        self.assertEqual(build.p.cells[(1,10,0)],'minecraft:polished_diorite')
        self.assertEqual(build.p.cells[(4,10,0)],'minecraft:gray_concrete')

    def test_absent_arrays_equal_empty_arrays(self):
        empty,_=self.build([])
        spec=self.spec([]); del spec['roofs']['A']['pv_arrays']
        with redirect_stdout(io.StringIO()):
            absent=F.build(spec,footprints={'A':self.cells},world=_StoneWorld())
        self.assertEqual(absent.cells,empty.cells)

    def test_load_spec_validates_pv_arrays(self):
        good = {'box': [11, 21, 16, 23], 'rows': 'x', 'pitch': 2, 'source': 'aerial PV'}
        bad = [
            ({**good, 'rows': 'y'}, 'rows'),
            ({**good, 'pitch': 0}, 'pitch'),
            ({**good, 'pitch': True}, 'pitch'),
            ({**good, 'source': ' '}, 'source'),
            ({**good, 'box': [11, 21, 16]}, 'box'),
            ({**good, 'box': [16, 21, 11, 23]}, 'box'),
            ({**good, 'colour': 'blue'}, 'unknown'),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'pv.json'
            path.write_text(json.dumps(self.spec([good])))
            self.assertEqual(FS.load_spec(path)['roofs']['A']['pv_arrays'][0]['pitch'], 2)
            for row, msg in bad:
                with self.subTest(row=row):
                    path.write_text(json.dumps(self.spec([row])))
                    with self.assertRaisesRegex(SystemExit, msg):
                        FS.load_spec(path)
            spec = self.spec([good])
            spec['roofs']['A']['kind'] = 'pitched'
            path.write_text(json.dumps(spec))
            with self.assertRaisesRegex(SystemExit, 'flat'):
                FS.load_spec(path)
