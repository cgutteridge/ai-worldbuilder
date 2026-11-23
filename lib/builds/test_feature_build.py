"""Feature-build orchestration, shell, CLI and regression fixtures."""

from __future__ import annotations

from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
import inspect
import io
import json
import tempfile
import unittest

import numpy as np

from feature_build_fixtures import (
    PitchedZonesFixture,
    _StoneWorld,
    _entrance_row,
    _glazing_plan,
    build_spec,
    start_lidar_fixture as setUpModule,
    stop_lidar_fixture as tearDownModule,
)
from script_env import TOOL_ROOT
import feature_build as F
import feature_details as FD
import feature_doors as FDoors
import feature_roof_zones as FZ
import feature_spec as FS
import roof_geometry as RG
import site_kit


class BuildPlanTest(unittest.TestCase):
    """Seam: build(spec) → Plan with N storeys and flat/pitched roof."""

    spec = staticmethod(build_spec)

    def setUp(self):
        self.cells = {(x, z) for x in range(10, 15) for z in range(20, 25)}


    def test_house_interior_air_cancels_ground_at_floor_plus_one(self):
        from journal_testkit import JournalWorld
        inside, higher, neighbour = (12, 51, 22), (12, 52, 22), (8, 51, 22)
        with JournalWorld() as world:
            world.build('old_ground', 'ground', {
                q: 'minecraft:gray_concrete' for q in (inside, higher, neighbour)})
            with redirect_stdout(io.StringIO()):
                plan = F.build(self.spec(floor_y=50),
                               footprints={'A': self.cells}, world=_StoneWorld())
            self.assertEqual(plan.cells.get(inside), 'minecraft:air')
            self.assertNotIn(higher, plan.cells)
            self.assertNotIn(neighbour, plan.cells)
            world.build(plan.name, 'feature', plan.cells, payload=plan)
            self.assertEqual(world.blocks.read(inside), 'minecraft:air')
            self.assertNotIn(inside, world.reg.owned([inside]))
            self.assertEqual(world.reg.owned([higher])[higher][0], 'old_ground')
            self.assertEqual(world.reg.owned([neighbour])[neighbour][0], 'old_ground')

    def test_flat_roof_of_a_full_block_only_family_uses_real_block_ids(self):
        """#371: concrete and terracotta have no slab, so a flat deck must use the full block."""
        from pathlib import Path as _P
        valid = {line.strip().removeprefix('minecraft:') for line in
                 (_P(TOOL_ROOT) / 'config' / 'minecraft-block-ids-26.2.txt').read_text().splitlines()
                 if line.strip() and not line.startswith('#')}
        for material, deck in (('light_gray_concrete', 'light_gray_concrete'),
                               ('terracotta', 'terracotta'),
                               ('smooth_stone', 'smooth_stone_slab')):
            with self.subTest(material=material), redirect_stdout(io.StringIO()):
                plan = F.build(self.spec(kind='flat', material=material),
                               footprints={'A': self.cells}, world=_StoneWorld())
            roof_y = 50 + 3 * FS.STOREY_H
            states = {st for (x, y, z), st in plan.cells.items() if y == roof_y and (x, z) in self.cells}
            self.assertIn(f'minecraft:{deck}[type=bottom]' if 'slab' in deck else f'minecraft:{deck}', states)
            for st in plan.cells.values():
                name = st.split('[')[0].removeprefix('minecraft:')
                if name.endswith(('_slab', 'concrete', 'terracotta')):
                    self.assertIn(name, valid, st[:80])

    def test_ledge_and_awning_details_use_the_catalogue_slab_or_full_block(self):
        self.assertEqual(FZ._bottom_slab_or_full('brick'), 'brick_slab[type=bottom]')
        self.assertEqual(FZ._bottom_slab_or_full('smooth_stone'), 'smooth_stone_slab[type=bottom]')
        self.assertEqual(FZ._bottom_slab_or_full('terracotta'), 'terracotta')
        self.assertEqual(FZ._bottom_slab_or_full('light_gray_concrete'), 'light_gray_concrete')

    def test_dry_run_reports_unknown_planned_block_ids_and_source(self):
        spec = self.spec()
        spec['wall'] = 'made_up_wall'
        output = io.StringIO()
        with redirect_stdout(output):
            F.build(spec, footprints={'A': self.cells}, world=_StoneWorld())
        self.assertRegex(output.getvalue(), r'unknown block id minecraft:made_up_wall x[1-9][0-9]*')
        self.assertIn('vendored Minecraft 26.2', output.getvalue())

    def test_unzoned_floor_uses_lidar_ground_not_live_rim(self):
        spec = self.spec(floor_y=None)
        data = {'A': {'x0': 10, 'z0': 20, 'ground_y': np.full((5, 5), 54)}}
        class LiveRim52:
            def block(self, x, y, z):
                return 'minecraft:stone' if y <= 52 else 'minecraft:air'
        output = io.StringIO()
        with redirect_stdout(output):
            plan = F.build(spec, footprints={'A': self.cells}, world=LiveRim52(), zone_data=data)
        self.assertIn('A b 54 ', output.getvalue())
        self.assertEqual(plan.cells[(12, 54, 22)], 'minecraft:smooth_stone')

    def test_three_storey_flat_roof_shell_height(self):
        plan = F.build(self.spec(storeys=3, kind='flat'), footprints={'A': self.cells}, world=_StoneWorld())
        floor = 50
        wall_top = floor + 3 * 3 - 1  # 58
        roof_y = floor + 3 * 3       # 59
        # Corner pier (not mid-face): mid columns may get default window rhythm (#146).
        wx, wz = 10, 24
        self.assertEqual(plan.cells.get((wx, floor, wz)), 'minecraft:bricks')
        self.assertEqual(plan.cells.get((wx, wall_top, wz)), 'minecraft:bricks')
        self.assertIn('smooth_stone_slab', plan.cells.get((12, roof_y, 22), ''))
        self.assertEqual(plan.cells.get((12, floor, 22)), 'minecraft:smooth_stone')
        self.assertEqual(plan.cells.get((12, floor + 3, 22)), 'minecraft:smooth_stone')
        self.assertEqual(plan.cells.get((12, floor + 6, 22)), 'minecraft:smooth_stone')

    def test_flat_roof_default_none_skips_overhang_fringe(self):
        """Absent edge: membrane on owned columns only, no N8 slab fringe."""
        plan = F.build(self.spec(storeys=2, kind='flat'), footprints={'A': self.cells}, world=_StoneWorld())
        roof_y = 50 + 2 * 3
        fringe = (9, roof_y, 22)  # one west of the mass
        self.assertNotIn('slab', plan.cells.get(fringe, ''))
        self.assertIn('smooth_stone_slab', plan.cells.get((12, roof_y, 22), ''))

    def test_clear_margin_does_not_fill_stone_shelves(self):
        """#115: margin is cleared but not filled with a stone ring at floor level."""
        floor = 50
        plan = F.build(self.spec(storeys=2, kind='flat'), footprints={'A': self.cells}, world=_StoneWorld())
        door_xz = {(x, z) for (x, _y, z), st in plan.cells.items() if '_door' in st}
        shelf = []
        for (x, y, z), st in plan.cells.items():
            if st != 'minecraft:stone' or y != floor or (x, z) in self.cells:
                continue
            # Stoops / step_out next to doors may still place stone — those are allowed.
            if any(abs(x - dx) <= 1 and abs(z - dz) <= 1 for dx, dz in door_xz):
                continue
            shelf.append((x, y, z))
        self.assertEqual(shelf, [], f'stone shelves outside footprint: {shelf[:12]}')

    def test_flat_roof_edge_builds_overhang_fringe(self):
        """Present edge: one-block N8 overhang fringe (old flat-roof behaviour)."""
        spec = self.spec(storeys=2, kind='flat')
        spec['roofs']['A']['edge'] = {'block': 'smooth_stone', 'where': 'outer'}
        plan = F.build(spec, footprints={'A': self.cells}, world=_StoneWorld())
        roof_y = 50 + 2 * 3
        fringe = (9, roof_y, 22)
        self.assertIn('smooth_stone_slab', plan.cells.get(fringe, ''))

    def test_pitched_roof_rises_above_eave(self):
        plan = F.build(
            self.spec(storeys=2, kind='pitched', material='brick'),
            footprints={'A': self.cells},
            world=_StoneWorld(),
        )
        floor = 50
        eave = floor + 2 * 3
        roof_ys = [
            y for (x, y, z), st in plan.cells.items()
            if 'brick_stairs' in st or st.endswith('bricks') or 'brick_slab' in st
        ]
        self.assertTrue(roof_ys, 'expected pitched brick stairs/slabs/full')
        self.assertGreaterEqual(max(roof_ys), eave)
        stairs = [st for st in plan.cells.values() if 'brick_stairs' in st]
        self.assertTrue(stairs, 'pitched hip must place upslope stairs')
        self.assertTrue(all('waterlogged=false' in st for st in stairs))
        self.assertTrue(all('half=bottom' in st for st in stairs))

    def test_ghost_ground_owner_does_not_drop_shell_cells(self):
        """Ghost ground must not shrink the offline plan (live apply overwrites)."""
        from journal_testkit import JournalWorld
        pos = (12, 50, 22)
        with JournalWorld() as jw:
            jw.build('ghost_0_0_ground', 'ghost-ground',
                     {(12, 40, 22): 'minecraft:light_gray_concrete', pos: 'minecraft:gray_concrete'},
                     cell=(0, 0))
            plan = F.build(self.spec(storeys=2), footprints={'A': self.cells}, world=_StoneWorld())
        self.assertIn(pos, plan.cells)


class CarpetFinishBuildTest(PitchedZonesFixture, unittest.TestCase):
    """Seam: ``roofs[label].finish`` carpet through ``Build`` (#455): flat decks only, on solid blocks."""

    def _finished(self, kind: str, levels: list, zone_data: dict, finish='carpet'):
        spec = self._spec(kind, levels, material='stone_brick')
        if finish:
            spec['roofs']['A']['finish'] = finish
        builder = F.Build(spec, footprints={'A': self.cells}, world=_StoneWorld(), zone_data=zone_data)
        buf = io.StringIO()
        with mock.patch.object(FZ, 'metres_aod_to_y', side_effect=lambda m: int(m)), redirect_stdout(buf):
            plan = builder.build()
        return builder.features[0], plan, buf.getvalue()

    def _shopfront_and_ridge(self) -> dict:
        """A low flat strip (x 10..11) in front of a ridged body (x 12..18)."""
        data = {k: dict(v) for k, v in self.ridge_zone_data.items()}
        dsm = np.full((7, 11), 45.0)
        for xi, h in enumerate([45, 45, 48, 49, 50, 52, 50, 49, 48]):
            dsm[:, 1 + xi] = float(h)
        data['A']['dsm'] = dsm
        return data

    def test_flat_zones_get_a_wall_top_course_and_carpet_with_no_extra_height(self):
        bare_f, bare, _ = self._finished('flat', [45, 50], self.flat_zone_data, finish=None)
        f, plan, out = self._finished('flat', [45, 50], self.flat_zone_data)
        self.assertEqual(set(f.carpet_cells), set(self.cells))
        for (x, z), deck in f.carpet_cells.items():
            self.assertEqual(deck, bare_f.zone_roof[(x, z)])
            self.assertEqual(bare.cells[(x, deck, z)], 'minecraft:stone_bricks')
            self.assertEqual(plan.cells[(x, deck, z)], 'minecraft:bricks')
            self.assertEqual(plan.cells[(x, deck + 1, z)], 'minecraft:gray_carpet')
        self.assertEqual(max(y for _x, y, _z in plan.cells), max(y for _x, y, _z in bare.cells))
        self.assertIn('A roof finish carpet: carpet 45 cells on solid blocks; dropped 0', out)

    def test_pitched_zones_carpet_only_the_flat_strip_never_the_slope(self):
        data = self._shopfront_and_ridge()
        bare_f, bare, _ = self._finished('pitched', [45, 48], data, finish=None)
        f, plan, _ = self._finished('pitched', [45, 48], data, finish='light_gray_carpet')
        self.assertTrue(f.carpet_cells)
        self.assertLessEqual(set(f.carpet_cells), f.zone_flat_cells)
        self.assertEqual({x for x, _z in f.carpet_cells}, {10, 11})
        sloped = [(x, z) for (x, z), y in f.zone_roof.items() if '_stairs[' in plan.cells.get((x, y, z), '')]
        self.assertTrue(sloped)
        carpets = {(x, z) for (x, _y, z), state in plan.cells.items() if state.endswith('_carpet')}
        self.assertEqual(carpets, set(f.carpet_cells))
        for x, z in f.carpet_cells:
            deck = f.carpet_cells[(x, z)]
            self.assertEqual(plan.cells[(x, deck, z)], 'minecraft:bricks')
            self.assertEqual(plan.cells[(x, deck + 1, z)], 'minecraft:light_gray_carpet')
            self.assertEqual(bare.cells.get((x, deck + 1, z)), 'minecraft:air')
        self.assertEqual(bare_f.zone_roof, f.zone_roof)

    def test_unzoned_pitched_roof_never_takes_a_carpet(self):
        spec = build_spec(storeys=2, kind='pitched', material='stone_brick')
        spec['roofs']['A']['finish'] = 'carpet'
        with redirect_stdout(io.StringIO()):
            plan = F.build(spec, footprints={'A': {(x, z) for x in range(10, 15) for z in range(20, 25)}},
                           world=_StoneWorld())
        self.assertFalse(any(state.endswith('_carpet') for state in plan.cells.values()))

    def test_carpet_over_a_deck_cleared_later_is_dropped(self):
        victim = (14, 22)
        real = F.Build.street_face

        def clear_one_deck(builder, f):
            real(builder, f)
            if victim in f.carpet_cells:
                builder.set(victim[0], f.carpet_cells[victim], victim[1], 'air')

        with mock.patch.object(F.Build, 'street_face', clear_one_deck):
            f, plan, out = self._finished('flat', [45, 50], self.flat_zone_data)
        deck = f.carpet_cells[victim]
        self.assertEqual(plan.cells[(victim[0], deck + 1, victim[1])], 'minecraft:air')
        self.assertIn('carpet 44 cells on solid blocks; dropped 1 without a solid block below', out)
        for (x, y, z), state in plan.cells.items():
            if state.endswith('_carpet'):
                self.assertNotIn(plan.cells.get((x, y - 1, z), 'minecraft:air'), ('minecraft:air', None))

    def test_unzoned_full_block_flat_roof_carpets_every_owned_deck_cell(self):
        cells = {(x, z) for x in range(10, 15) for z in range(20, 25)}
        spec = build_spec(storeys=2, kind='flat', material='gray_concrete')
        spec['roofs']['A']['finish'] = 'carpet'
        spec['roofs']['A']['edge'] = {'block': 'stone_brick', 'where': 'outer'}
        with redirect_stdout(io.StringIO()):
            plan = F.build(spec, footprints={'A': cells}, world=_StoneWorld())
        deck = 50 + 2 * 3
        for x, z in cells:
            self.assertEqual(plan.cells[(x, deck, z)], 'minecraft:bricks')
            self.assertEqual(plan.cells[(x, deck + 1, z)], 'minecraft:gray_carpet')
        self.assertEqual(plan.cells[(9, deck, 22)], 'minecraft:gray_concrete')
        self.assertNotIn('carpet', plan.cells.get((9, deck + 1, 22), ''))

    def test_unzoned_slab_deck_flat_roof_refuses_the_carpet_and_keeps_its_height(self):
        cells = {(x, z) for x in range(10, 15) for z in range(20, 25)}
        bare_spec = build_spec(storeys=2, kind='flat', material='stone_brick')
        with redirect_stdout(io.StringIO()):
            bare = F.build(bare_spec, footprints={'A': cells}, world=_StoneWorld())
        spec = build_spec(storeys=2, kind='flat', material='stone_brick')
        spec['roofs']['A']['finish'] = 'carpet'
        out = io.StringIO()
        with redirect_stdout(out):
            plan = F.build(spec, footprints={'A': cells}, world=_StoneWorld())
        self.assertIn('carpet on an unzoned slab-deck flat roof would raise it half a block', out.getvalue())
        self.assertFalse(any(state.endswith('_carpet') for state in plan.cells.values()))
        self.assertEqual(plan.cells, bare.cells)
        self.assertIn('stone_brick_slab', plan.cells[(12, 50 + 2 * 3, 22)])

    def test_carpet_support_is_a_full_solid_cube_only(self):
        for state in ('minecraft:bricks', 'minecraft:lime_concrete', 'stone_bricks', 'minecraft:stone',
                      'minecraft:deepslate_tiles', 'minecraft:smooth_quartz', 'minecraft:oak_planks'):
            with self.subTest(state=state):
                self.assertTrue(F.carpet_support_ok(state))
        for state in (None, '', 'minecraft:air', 'minecraft:glass', 'minecraft:glass_pane',
                      'minecraft:oak_fence', 'minecraft:brick_wall', 'minecraft:oak_trapdoor[half=top]',
                      'minecraft:oak_wall_sign[facing=west]', 'minecraft:lantern',
                      'minecraft:oak_door[half=upper]', 'minecraft:stone_brick_slab[type=bottom]',
                      'minecraft:stone_brick_stairs[facing=east]', 'minecraft:gray_carpet',
                      'minecraft:light_blue_stained_glass'):
            with self.subTest(state=state):
                self.assertFalse(F.carpet_support_ok(state))
        self.assertTrue(F.carpet_support_ok('minecraft:mossy_cobblestone', {'mossy_cobblestone'}))

    def test_glass_under_a_carpet_is_no_support(self):
        victim = (14, 22)
        real = F.Build.street_face

        def glaze_one_deck(builder, f):
            real(builder, f)
            if victim in f.carpet_cells:
                builder.set(victim[0], f.carpet_cells[victim], victim[1], 'glass')

        with mock.patch.object(F.Build, 'street_face', glaze_one_deck):
            f, plan, out = self._finished('flat', [45, 50], self.flat_zone_data)
        self.assertEqual(plan.cells[(victim[0], f.carpet_cells[victim] + 1, victim[1])], 'minecraft:air')
        self.assertIn('dropped 1 without a solid block below', out)


class CliDryRunTest(unittest.TestCase):
    """Seam: run() previews and dry-applies (never live unless apply=True)."""

    def test_dry_run_slab_normalization_keeps_valid_deepslate_full_id(self):
        spec = {'name': 'slab_integration', 'ways': {'1': 'A'},
                'storeys': {'A': 1}, 'roofs': {'A': {'kind': 'flat', 'material': 'deepslate_tile'}},
                'groups': [{'nos': ['A'], 'front': 'N'}], 'floor_min': 50}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'spec.json'
            path.write_text(json.dumps(spec))
            (F.ROOT / 'verification/site').mkdir(parents=True, exist_ok=True)
            with mock.patch('mc_paced.Client'), mock.patch.object(F, 'preview'), redirect_stdout(io.StringIO()):
                plan = F.run(path, footprints={'A': {(x, z) for x in range(4) for z in range(4)}}, world=_StoneWorld())
        self.assertTrue('minecraft:deepslate_tiles' in plan.cells.values())
        self.assertFalse('minecraft:deepslate_tile' in plan.cells.values())

    def test_run_dry_calls_preview_and_apply_dry(self):
        cells = {(x, z) for x in range(0, 4) for z in range(0, 4)}
        spec = {
            'name': 'cli_feature',
            'ways': {'1': 'A'},
            'storeys': {'A': 2},
            'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
            'groups': [{'nos': ['A'], 'front': 'N'}],
            'floor_min': 50,
        }
        with tempfile.TemporaryDirectory() as d:
            spec_path = Path(d) / 'cli_feature.json'
            spec_path.write_text(json.dumps(spec))
            with mock.patch.object(F, 'preview') as prev, mock.patch.object(F, 'apply') as app:
                plan = F.run(spec_path, do_apply=False, footprints={'A': cells}, world=_StoneWorld())
                self.assertGreater(len(plan.cells), 0)
                prev.assert_called_once()
                app.assert_called_once()
                self.assertEqual(app.call_args.kwargs.get('dry'), True)


class LivePrefetchTest(unittest.TestCase):
    """Seam: feature_build plan construction batches LiveBlocks scans via prefetch.

    Without prefetch, each ``block()`` becomes one MCP ``block_scan_region``.
    With prefetch before hot loops, the same plan must use far fewer scans.
    """

    def setUp(self):
        self.cells = {(x, z) for x in range(10, 15) for z in range(20, 25)}
        self.spec = {
            'name': 'unit_feature',
            'ways': {'99': 'A'},
            'storeys': {'A': 3},
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
            
            'facade_plan': _glazing_plan({}, {}),
            'zones': {},
        }

    def _terrain(self) -> dict:
        """Stone through y<=49, air above, covering the footprint ring AABB."""
        blocks = {}
        for x in range(-5, 40):
            for z in range(0, 50):
                for y in range(30, 76):
                    blocks[(x, y, z)] = 'minecraft:stone' if y <= 49 else 'minecraft:air'
        return blocks

    def _live_world(self, blocks: dict):
        """World over LiveBlocks + FakeScanMcp; returns (world, mcp, tmp_cleanup)."""
        from block_store import LiveBlocks
        from site_kit import World
        from test_block_store import FakeScanMcp

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        region = Path(tmp.name) / 'saves' / 'w' / 'region'
        region.mkdir(parents=True)
        mcp = FakeScanMcp(blocks)
        store = LiveBlocks(
            region, allowed=[region], identity_check=lambda r: None,
            flusher=lambda: None, settle_seconds=0, mcp=mcp,
        )
        return World(store=store), mcp

    def test_prefetch_matches_stub_world_and_bounds_scan_calls(self):
        """Plan matches per-block stub path; scan count stays well below one-per-block."""
        from site_kit import Plan

        expected = F.build(self.spec, footprints={'A': self.cells}, world=_StoneWorld())
        world, mcp = self._live_world(self._terrain())
        with mock.patch('cell_registry.view_registry') as vr:
            vr.return_value.owned.return_value = {}
            got = F.build(self.spec, footprints={'A': self.cells}, world=world)
        self.assertEqual(got.cells, expected.cells)
        scans = sum(1 for name, _ in mcp.calls if name == 'block_scan_region')
        # Naive path measured ~7900 scans for this fixture; batched AABB is a few tiles.
        self.assertLessEqual(scans, 64, f'too many block_scan_region calls: {scans}')
        self.assertIsInstance(got, Plan)

    def test_planned_world_prefetch_skips_overlay_cells(self):
        """Overlay (PlannedWorld) must not ask MCP for positions served from .cells."""
        from site_kit import Plan, PlannedWorld

        class CountingWorld:
            def __init__(self):
                self.prefetched = []

            def prefetch(self, positions):
                self.prefetched.extend(tuple(p) for p in positions)

            def block(self, x, y, z):
                return 'minecraft:air'

        plan = Plan('overlay')
        plan.cells[(1, 50, 1)] = 'minecraft:bricks'
        base = CountingWorld()
        view = PlannedWorld(plan, base)
        view.prefetch([(1, 50, 1), (2, 50, 2), (3, 51, 3)])
        self.assertEqual(base.prefetched, [(2, 50, 2), (3, 51, 3)])


class OfflineGuardsTest(unittest.TestCase):
    """Seam: the offline plan step refuses unframed doors and jogging ridges with the live guard's message (#99)."""

    def setUp(self):
        self.cells = {(x, z) for x in range(10, 18) for z in range(20, 25)}
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        p = mock.patch.object(site_kit, 'OUT', Path(t.name))
        p.start()
        self.addCleanup(p.stop)

    def spec(self, door_frame):
        return {
            'name': 'unit_guard', 'ways': {'99': 'A'}, 'storeys': {'A': 2},
            'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
            'groups': [{'nos': ['A'], 'front': 'S', 'mode': 'each'}],
            'modifies': [], 'floor_min': 50, 'step_max': 4, 'door_frame': door_frame,
            'keep_edits': False, 'wall': 'bricks', 'floor': 'smooth_stone', 'ledge_stair': False,
        }

    def test_framed_doors_plan_passes_offline(self):
        F.build(self.spec(True), footprints={'A': self.cells}, world=_StoneWorld())

    def test_bad_door_frame_fails_offline_with_live_guard_message(self):
        # a feature whose planned door has a missing jamb piece (frame check is the live guard's own)
        bad = [(12, 50, 24, 'jamb east')]
        with mock.patch.object(site_kit, 'door_frame_issues', return_value=bad), \
                self.assertRaises(SystemExit) as offline:
            F.build(self.spec(True), footprints={'A': self.cells}, world=_StoneWorld())
        plan = site_kit.Plan('unit_guard')
        for dy, half in ((0, 'lower'), (1, 'upper')):
            plan.set(5, 50 + dy, 5, f'minecraft:oak_door[facing=south,half={half}]')
        with self.assertRaises(SystemExit) as live:
            site_kit.guard_door_frames(plan, lambda x, y, z: None)
        # count and pieces differ with the plan; the guard's own wording is identical
        tail = str(live.exception).split('; see ', 1)[1]
        self.assertIn('door frame piece(s) missing, e.g.', str(offline.exception))
        self.assertTrue(str(offline.exception).endswith(tail))

    def test_roof_jog_fails_offline(self):
        with mock.patch('roof_lint.ridge_jogs', return_value=[{'axis': 'x', 'row': 1}]):
            with self.assertRaises(SystemExit) as ctx:
                F.build(self.spec(True), footprints={'A': self.cells}, world=_StoneWorld())
        self.assertIn('roof ridge(s) jog sideways', str(ctx.exception))


class ReapplyGuardTest(unittest.TestCase):
    """Seam: a live run() refuses a plan with a landed build unless overwrite is set."""

    SPEC = {'name': 'guarded', 'ways': {'1': 'A'}, 'storeys': {'A': 1},
            'roofs': {'A': {'kind': 'flat', 'material': 'stone'}},
            'groups': [{'nos': ['A'], 'front': 'N'}], 'floor_min': 50}
    CELLS = {'A': {(x, z) for x in range(4) for z in range(4)}}

    def _run(self, seqs, **kw):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'spec.json'
            path.write_text(json.dumps(self.SPEC))
            (F.ROOT / 'verification/site').mkdir(parents=True, exist_ok=True)
            with mock.patch('readiness_evidence.require_feature_ready'), \
                    mock.patch('landmark_gate.require_landmark_street_check'), \
                    mock.patch('landmark_gate.feature_cells_for_plan', return_value=[]), \
                    mock.patch.object(F, 'landed_build_seqs', return_value=seqs), \
                    mock.patch.object(F, 'preview'), mock.patch.object(F, 'apply') as applied, \
                    redirect_stdout(io.StringIO()):
                F.run(path, do_apply=True, footprints=self.CELLS, world=_StoneWorld(), **kw)
        return applied

    def test_refuses_apply_over_landed_build(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run([417])
        self.assertIn('element_undo.py', str(ctx.exception))
        self.assertIn('--overwrite', str(ctx.exception))

    def test_overwrite_applies_over_landed_build(self):
        self.assertTrue(self._run([417], overwrite=True).called)

    def test_first_build_applies(self):
        self.assertTrue(self._run([]).called)


class PitchedRoofBuildShapesTest(unittest.TestCase):
    """Integration: build() hip uses stairs; states cover gable/valley via helper."""

    def test_build_hip_places_outer_corner_and_upslope_stairs(self):
        cells = {(x, z) for x in range(10, 15) for z in range(20, 25)}
        spec = {
            'name': 'unit_hip',
            'ways': {'99': 'A'},
            'storeys': {'A': 2},
            'roofs': {'A': {'kind': 'pitched', 'material': 'brick'}},
            'groups': [{'nos': ['A'], 'front': 'S', 'mode': 'each'}],
            'modifies': [],
            'floor_min': 50,
            'step_max': 4,
            'door_frame': True,
            'keep_edits': False,
            'wall': 'bricks',
            'floor': 'smooth_stone',
            'ledge_stair': False,
        }
        plan = F.build(spec, footprints={'A': cells}, world=_StoneWorld())
        stairs = [st for st in plan.cells.values() if 'brick_stairs' in st]
        self.assertTrue(stairs, 'hip roof must place stairs')
        self.assertTrue(
            any('facing=south' in st and 'shape=straight' in st for st in stairs),
            f'expected a straight south-facing slope stair, got {stairs!r}',
        )
        self.assertTrue(
            any('shape=outer_' in st for st in stairs),
            f'expected outer hip corner stair, got {stairs!r}',
        )


class BuilderPathTest(unittest.TestCase):
    def test_world_relative_spec_and_recorded_builder_are_independent_of_cwd(self):
        from active_world import world_root
        root = world_root()
        path = root / 'site/builds/relative.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ReapplyGuardTest.SPEC))
        # Existing public run seam: only preview/apply outputs are substituted.
        import os
        original = Path.cwd()
        try:
            with tempfile.TemporaryDirectory() as other, \
                    mock.patch.object(F, 'preview'), mock.patch.object(F, 'apply') as applied, \
                    mock.patch.object(F, 'build', return_value=mock.Mock(summary=lambda: {})), \
                    redirect_stdout(io.StringIO()):
                for dirname in ('first', 'second'):
                    directory = Path(other) / dirname
                    directory.mkdir()
                    os.chdir(directory)
                    F.run('site/builds/relative.json')
                    self.assertEqual(applied.call_args.kwargs['meta']['builder'],
                                     {'kind': 'spec', 'path': 'site/builds/relative.json'})
                F.run(path.resolve())
                self.assertEqual(applied.call_args.kwargs['meta']['builder'],
                                 {'kind': 'spec', 'path': 'site/builds/relative.json'})
        finally:
            os.chdir(original)


class GhostGroundIsGroundTest(unittest.TestCase):
    """Task 286: features build on ghost ground, so margin clearing must keep every ghost top."""

    def test_ghost_tops_are_in_both_builders_ground_lists(self):
        import house_build
        import lidar_ghost
        tops = {'minecraft:' + lidar_ghost.top_block_for_feature_height(h) for h in (0, 1, 3)}
        tops.add('minecraft:' + lidar_ghost.FILL_BLOCK)
        self.assertLessEqual(tops, set(F.GROUND))
        self.assertLessEqual(tops, set(house_build.GROUND))


_ROOF_MATERIAL_CALLERS = frozenset({
    'fam', 'shell_zoned', 'entrance_covers', '_solidify_roof_under', 'details',
})


_ROOF_MATERIAL_GOLDEN = (
    Path(__file__).resolve().parent / 'fixtures' / 'roof-material-plan.json'
)


def _roof_material_migration_plan():
    """Build the synthetic plan used to lock roof-material output (#355).

    One pitched zoned mass carries ``zone_materials`` into ``pitched_roof_states``
    and the detail support set. One flat zoned mass places the full-block deck.
    A covered entrance and a plant room on a stair reach the other callers.
    ``family_variants`` is wrapped only to record those callers.

    @returns: ``(plan, caller names)``
    """
    cells_p = {(x, z) for x in range(10, 19) for z in range(20, 25)}
    cells_f = {(x, z) for x in range(30, 39) for z in range(20, 25)}
    depth, width = 7, 11
    ground = np.full((depth, width), 51)
    ridge = np.full((depth, width), 59.0)
    for xi, height in enumerate([59, 59, 60, 61, 63, 61, 60, 59, 59]):
        ridge[:, 1 + xi] = float(height)
    flat = np.full((depth, width), 59.0)
    zone_data = {
        'P': {'dsm': ridge, 'ground_y': ground, 'x0': 9, 'z0': 19},
        'F': {'dsm': flat, 'ground_y': ground.copy(), 'x0': 29, 'z0': 19},
    }

    def spec(detail):
        details = {'P': [detail]} if detail is not None else {}
        return {
            'name': 'unit_roof_material_golden',
            'ways': {'1': 'P', '2': 'F'},
            'storeys': {'P': 2, 'F': 2},
            'roofs': {
                'P': {
                    'kind': 'pitched',
                    'material': 'brick',
                    'zone_materials': {'59': 'deepslate_tile'},
                },
                'F': {'kind': 'flat', 'material': 'brick'},
            },
            'groups': [
                {'nos': ['P'], 'front': 'S', 'mode': 'each'},
                {'nos': ['F'], 'front': 'S', 'mode': 'each'},
            ],
            'modifies': [],
            'floor_min': 51,
            'step_max': 4,
            'door_frame': True,
            'keep_edits': False,
            'wall': 'bricks',
            'floor': 'smooth_stone',
            'ledge_stair': False,
            'zones': {
                'P': {'from': 'lidar', 'levels': [59], 'bucket': 2, 'axis_deg': 0},
                'F': {'from': 'lidar', 'levels': [59], 'bucket': 2, 'axis_deg': 0},
            },
            'facade_plan': {
                'F': {
                    'faces': {},
                    'entrances': [_entrance_row(
                        id='T-F', portal_id='T-F', at=[34, 24],
                        awning_like={'kind': 'covered_area', 'depth': 1},
                    )],
                },
            },
            'details': details,
        }

    seen = set()
    real = F.family_variants

    def wrapped(mat):
        frame = inspect.currentframe()
        while frame is not None:
            if frame.f_code.co_name in _ROOF_MATERIAL_CALLERS:
                seen.add(frame.f_code.co_name)
                break
            frame = frame.f_back
        return real(mat)

    def build(detail):
        with mock.patch.object(F, 'family_variants', wrapped), \
                mock.patch.object(FD, 'family_variants', wrapped), \
                mock.patch.object(FDoors, 'family_variants', wrapped), \
                mock.patch.object(RG, 'family_variants', wrapped), \
                mock.patch.object(FZ, 'metres_aod_to_y', side_effect=lambda m: int(m)), \
                redirect_stdout(io.StringIO()):
            return F.build(
                spec(detail),
                footprints={'P': cells_p, 'F': cells_f},
                world=_StoneWorld(),
                zone_data=zone_data,
            )

    bare = build(None)
    pad = None
    for (x, y, z), state in sorted(bare.cells.items()):
        if not (10 <= x <= 18 and 20 <= z <= 24):
            continue
        name = state.split('[', 1)[0].removeprefix('minecraft:')
        if name.endswith('_slab') or name.endswith('_stairs'):
            pad = (x, y, z)
            break
    if pad is None:
        raise AssertionError('pitched roof produced no slab or stair for the detail pad')
    x, y, z = pad
    plan = build({
        'kind': 'plant_room',
        'at': [x, y + 1, z],
        'size': [1, 1, 1],
        'source': 'golden roof pad',
    })
    return plan, frozenset(seen)


def _roof_material_cell_rows(plan) -> list:
    """Canonical ``[x, y, z, state]`` rows for the roof-material golden.

    @param plan: feature plan
    @returns: rows sorted by coordinate
    """
    return [[x, y, z, state] for (x, y, z), state in sorted(plan.cells.items())]


class RoofMaterialMigrationGoldenTest(unittest.TestCase):
    """Seam: feature plans stay byte-identical once roof materials move (#355)."""

    def test_plan_cells_match_pre_migration_golden(self):
        plan, callers = _roof_material_migration_plan()
        self.assertEqual(callers, _ROOF_MATERIAL_CALLERS)
        states = list(plan.cells.values())
        self.assertTrue(any('deepslate_tile_stairs' in state for state in states))
        self.assertTrue(any(state == 'minecraft:bricks' for state in states))
        self.assertTrue(any('brick_slab' in state for state in states))
        self.assertTrue(any(state == 'minecraft:gray_concrete' for state in states))
        rows = json.loads(_ROOF_MATERIAL_GOLDEN.read_text(encoding='utf-8'))
        self.assertEqual(_roof_material_cell_rows(plan), rows)
