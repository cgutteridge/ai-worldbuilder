"""Railway feature workflow and normal journal/apply safety, with synthetic inputs."""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import numpy as np

import railway_feature as F
from railway_evidence import Line, LevelCrossing
from journal_testkit import FakePaced, JournalWorld, LiveApplyCase
from site_kit import Plan


def ground(x0=-50, z0=-50, width=180, depth=120):
    terrain = np.full((width, depth), 30.)
    return {'x0': x0, 'z0': z0, 'terrain': terrain, 'h': terrain.copy()}


def line(points=((10, 10), (30, 10)), *, crossings=()):
    return Line('line-one', (tuple(points),), (('w1',),), crossings, (), ())


def spec():
    return F.plan_feature([line()], ground(), (0, 0))


class TestRailwaySpec(unittest.TestCase):
    def test_failures_and_invalid_geometry_are_rejected_before_apply(self):
        raw = spec()
        raw['plans'][0]['failures'] = [{'where': [1, 2], 'reason': 'missing ground'}]
        with mock.patch('site_kit.apply') as apply, self.assertRaisesRegex(ValueError, 'missing ground'):
            F.build(raw)
        apply.assert_not_called()
        raw['plans'][0]['failures'] = []
        raw['plans'][0]['rails'][0][3] = 'north_south'
        with self.assertRaisesRegex(ValueError, 'invalid railway plan'):
            F.build(raw)

    def test_plan_is_jsonable_road_surface_and_power_are_preserved(self):
        raw = F.plan_feature([line()], ground(), (0, 0), road_surfaces={(20, 11): 'tarmac'})
        plan = F.build(json.loads(json.dumps(raw)))
        self.assertEqual(plan.cells[(20, 30, 11)], 'minecraft:gray_concrete')
        self.assertEqual(plan.cells[(20, 30, 10)], 'minecraft:stone')
        self.assertEqual(plan.cells[(20, 30, 12)], 'minecraft:gravel')
        self.assertEqual(plan.cells[(20, 29, 10)], 'minecraft:redstone_torch')
        self.assertEqual(plan.cells[(20, 28, 10)], 'minecraft:stone')
        from site_kit import full
        self.assertEqual(plan.cells[(20, 31, 10)], full('powered_rail[shape=east_west,powered=true]'))

    def test_gaps_write_nothing_and_retain_two_join_ends(self):
        crossing = LevelCrossing('n2', 'line-one', 0, (20, 10), 10, None, None, None, None, None)
        raw = F.plan_feature([line(crossings=(crossing,))], ground(), (0, 0),
                             crossing_roads={'n2': {'highway': 'residential', 'width': '4'}})
        plan = F.build(raw)
        gap = raw['plans'][0]['gaps'][0]
        self.assertEqual(len(gap['ends']), 2)
        omitted = {tuple(q) for q in gap['columns']}
        self.assertFalse({(x, z) for x, _y, z in plan.cells} & omitted)
        saved = F.metadata(json.loads(json.dumps(raw)))['gaps'][0]
        self.assertEqual(saved['line_id'], 'line-one')
        self.assertEqual(saved['ends'], gap['ends'])

    def test_cell_proposal_uses_stable_feature_id_and_not_a_way_as_name(self):
        import world_frame as wf
        frame = wf.WorldFrame({'crs': 'EPSG:27700', 'offsets': {'easting': 440000, 'northing': 110000},
                               'y_offset': 0, 'cell': {'size': 128, 'home': [0, 0]}})
        coords = []
        for x, z in ((10, 10), (30, 10)):
            lat, lon = wf.mc_to_wgs84(x, z, frame)
            coords.append([lon, lat])
        with JournalWorld() as jw:
            path = jw.root/'railway.geojson'
            path.write_text(json.dumps({'type': 'FeatureCollection', 'features': [
                {'type': 'Feature', 'properties': {'railway': 'rail', 'osm_id': '12'},
                 'geometry': {'type': 'LineString', 'coordinates': coords}}]}))
            rows = F.proposal((0, 0), jw.reg, source=path, frame=frame)
            self.assertEqual(rows[0]['id'], 'railway_0_0')
            import cell_registry
            self.assertEqual(cell_registry.candidate_plan_id(rows[0]), 'railway_0_0')
            records = cell_registry.record_feature_candidates(jw.reg, '0,0', rows)
            self.assertEqual([r['id'] for r in records], ['railway_0_0'])

    def test_partial_edge_cell_still_needs_the_next_feature(self):
        with JournalWorld() as jw:
            raw = spec()
            jw.build(raw['name'], 'feature', {(10, 30, 10): 'minecraft:gravel'}, meta=F.metadata(raw))
            self.assertTrue(F.covered((0, 0), jw.reg))
            self.assertFalse(F.covered((-8, 0), jw.reg))

    def test_no_railway_cell_does_not_open_box_extract(self):
        with JournalWorld() as jw:
            path = jw.root / 'empty.geojson'
            path.write_text(json.dumps({'type': 'FeatureCollection', 'features': []}))
            with mock.patch('railway_evidence.extract_railway_box') as box:
                self.assertEqual(F.proposal((0, 0), jw.reg, source=path), [])
            box.assert_not_called()

    def test_covered_neighbour_skips_before_loading_any_evidence_and_undo_clears_it(self):
        raw = spec()
        with JournalWorld() as jw:
            jw.build(raw['name'], 'feature', F.build(raw).cells, cell=(0, 0), meta=F.metadata(raw))
            with mock.patch('world_frame.load_frame', side_effect=AssertionError('must skip')):
                self.assertEqual(F.proposal((1, 0), jw.reg, source=jw.root/'missing'), [])
            self.assertTrue(F.reserved_columns((0, 0), jw.reg))
            from cell_journal import Undo
            jw.reg.change(Undo(raw['name']), jw.apply)
            self.assertFalse(F.covered((1, 0), jw.reg))
            self.assertEqual(F.reserved_columns((0, 0), jw.reg), set())

    def test_neighbouring_box_resumes_recorded_rail_level(self):
        g = ground(x0=-960, width=4096)
        evidence = line(((0, 0), (2200, 0)))
        first = F.plan_feature([evidence], g, (0, 0))
        with JournalWorld() as jw:
            jw.build(first['name'], 'feature', {(0, 30, 0): 'minecraft:gravel'},
                     cell=(0, 0), meta=F.metadata(first))
            second = F.plan_feature([evidence], g, (8, 0), reg=jw.reg)
            end = first['plans'][0]['joins'][0]['tracks'][0]
            start = second['plans'][0]['rails'][0]
            self.assertEqual(start[:3], [*end['next'][:1], end['level'], end['next'][1]])
            plan = F.build(second)
            self.assertTrue(all(x > first['plans'][0]['box'][2] for x, _y, _z in plan.cells))

    def test_rail_placement_preserves_a_nonfalling_road_carrier(self):
        plan = Plan('crossing-fixture')
        plan.set(20, 30, 10, 'gray_concrete')
        F.place_rails(plan, [[20, 31, 10, 'east_west',
                             'minecraft:powered_rail[shape=east_west,powered=true]']],
                      [[20, 29, 10]], [[20, 28, 10]])
        self.assertEqual(plan.cells[(20, 30, 10)], 'minecraft:gray_concrete')
        self.assertEqual(plan.cells[(20, 29, 10)], 'minecraft:redstone_torch')

    def test_lidar_mosaic_preserves_offsets_across_cell_edges(self):
        class Cached:
            def heightmap(self, cell):
                tx, tz = cell
                t = np.full((128, 128), 30 + tx + tz)
                return {'x0': tx*128, 'z0': tz*128, 'terrain': t, 'h': t+5,
                        'kind': np.zeros(t.shape, dtype=int)}
        g = F.ground_for_box((120, 120, 130, 130), world=Cached())
        self.assertEqual(g['terrain'].shape, (11, 11))
        self.assertTrue(g['have'].all())
        self.assertEqual(g['terrain'][0, 0], 30)
        self.assertEqual(g['terrain'][-1, -1], 32)
        self.assertEqual(g['h'][-1, -1], 37)

    def test_registry_check_validates_typed_specs(self):
        from cell_journal import Note
        from registry_verify import check_feature_specs
        with JournalWorld() as jw:
            raw = spec()
            jw.reg.change(Note((0, 0), 'plan_feature', 'railway', raw['name']))
            folder = jw.root/'site/builds'
            folder.mkdir(parents=True)
            (folder/(raw['name']+'.json')).write_text(json.dumps(raw))
            self.assertEqual(check_feature_specs(jw.reg, jw.root), [])
            raw['plans'][0]['rails'][0][4] = 'minecraft:air'
            (folder/(raw['name']+'.json')).write_text(json.dumps(raw))
            self.assertTrue(any(r['id'] == 'invalid-spec:'+raw['name'] for r in check_feature_specs(jw.reg, jw.root)))


class TestRailwayApply(LiveApplyCase):
    def test_apply_owns_footprint_and_normal_undo_restores_exactly(self):
        raw = spec()
        plan = F.build(raw)
        before = self.blocks.read_many(plan.cells)
        with redirect_stdout(io.StringIO()):
            self.run_apply(plan, cell=(0, 0), meta=F.metadata(raw), strict=True, readback_retries=2)
        self.assertEqual(self.entries(raw['name'])[-1].status, 'landed')
        self.assertEqual(set(self.reg().recorded(raw['name'])), set(plan.cells))
        self.assertEqual(F.reserved_columns((0, 0), self.reg()), {(x, z) for x, _y, z in plan.cells})
        with redirect_stdout(io.StringIO()):
            self.run_undo(raw['name'])
        self.assertEqual(self.blocks.read_many(plan.cells), before)
        self.assertFalse(F.covered((0, 0), self.reg()))
        self.assertEqual(F.reserved_columns((0, 0), self.reg()), set())

    def test_readback_retry_repairs_dropped_rail_inside_one_journal_entry(self):
        raw = spec()
        plan = F.build(raw)
        lost = tuple(raw['plans'][0]['rails'][5][:3])
        original = FakePaced.text
        dropped = []
        def text(client, name, args):
            result = original(client, name, args)
            if name == 'block_fill_batch' and not dropped:
                dropped.append(lost)
                self.blocks.write(lost, 'minecraft:air')
            return result
        with mock.patch.object(FakePaced, 'text', text), redirect_stdout(io.StringIO()):
            rep = self.run_apply(plan, meta=F.metadata(raw), strict=True, readback_retries=2)
        self.assertEqual(rep['mismatches'], 0)
        self.assertEqual(rep['readback_retries'], [{'attempt': 1, 'positions': 1}])
        self.assertEqual(len(self.entries(raw['name'])), 1)
        from block_store import same_state
        self.assertTrue(same_state(self.blocks.read(lost), plan.cells[lost]))

    def test_wide_backup_is_tiled_and_undo_uses_every_tile(self):
        raw = F.plan_feature([line(((10, 10), (170, 10)))], ground(width=260), (0, 0))
        plan = F.build(raw)
        before = self.blocks.read_many(plan.cells)
        with redirect_stdout(io.StringIO()):
            self.run_apply(plan, meta=F.metadata(raw), strict=True, readback_retries=2)
        structure = self.entries(plan.name)[-1].meta['structure']
        self.assertGreater(len(structure['tiles']), 1)
        for tile in structure['tiles']:
            self.assertTrue(all(b-a+1 <= 128 for a, b in zip(tile['from'], tile['to'])))
        with redirect_stdout(io.StringIO()):
            self.run_undo(plan.name)
        self.assertEqual(self.blocks.read_many(plan.cells), before)

    def test_interrupted_wide_feature_recovers_every_backup_tile(self):
        import site_recover
        from journal_testkit import View
        raw = F.plan_feature([line(((10, 10), (170, 10)))], ground(width=260), (0, 0))
        plan = F.build(raw)
        before = self.blocks.read_many(plan.cells)
        FakePaced.abort_on_save = True
        with redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            self.run_apply(plan, meta=F.metadata(raw), strict=True, readback_retries=2)
        entry = self.entries(plan.name)[-1]
        names = [t['name'] for t in entry.meta['structure']['tiles']]
        self.blocks.write(next(iter(plan.cells)), 'minecraft:gold_block')
        # This position is inside a backup tile but outside every planned column.
        sentinel = (20, 32, 7)
        self.assertNotIn(sentinel, plan.cells)
        self.blocks.write(sentinel, 'minecraft:gold_block')
        FakePaced.abort_on_save = False
        with redirect_stdout(io.StringIO()):
            result = site_recover.recover((0, 0), apply=True, flush=lambda: None,
                                         world_factory=lambda: View(self.blocks))
        self.assertEqual(result['action'], 'rolled-back')
        self.assertTrue(names)
        self.assertEqual(self.blocks.read(sentinel), 'minecraft:gold_block')
        self.assertNotIn('structure_load_to_world', FakePaced.calls)
        self.assertEqual(self.blocks.read_many(plan.cells), before)

    def test_persistent_miss_fails_and_stays_begun(self):
        plan = F.build(spec())
        FakePaced.skip_fills = True
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(SystemExit, 'readback has'):
            self.run_apply(plan, meta=F.metadata(spec()), strict=True, readback_retries=2)
        self.assertEqual(self.entries(plan.name)[-1].status, 'begun')
        self.assertFalse(F.covered((0, 0), self.reg()))


class TestRailwayDecorReservation(unittest.TestCase):
    def test_woodland_decor_cannot_grow_on_a_railway_cut_above_its_owned_blocks(self):
        from types import SimpleNamespace
        import decor_layer as dl
        from block_store import MemoryBlocks
        from site_kit import World
        wood = {(x, z) for x in range(8, 24) for z in range(8, 24)}
        world = World(store=MemoryBlocks({(x, 64, z): 'minecraft:grass_block' for x, z in wood}, default='air'))
        vmap = SimpleNamespace(**{k: set() for k in
            ('road', 'feat', 'water', 'water_line', 'hedge', 'wall', 'fence', 'marks')},
            furniture=[], road_lines=[], path_lines=[])
        ring = [[(8, 8), (24, 8), (24, 24), (8, 24), (8, 8)]]
        idx = mock.Mock(cells=lambda: {}, buildings={}, areas=[(ring, {'natural': 'wood'})])
        with JournalWorld() as jw:
            jw.build('railway_0_0', 'feature', {(x, 30, z): 'minecraft:stone' for x, z in wood},
                     meta={'kind': 'railway', 'boxes': [[0, 0, 127, 127]]})
            with mock.patch('ground_sources.OSMGround', return_value=vmap), \
                    mock.patch('osm_footprints.OSMIndex', return_value=idx), \
                    mock.patch.object(dl, 'neighbour_owned', return_value=set()), \
                    mock.patch.object(dl, 'load_masked_positions', return_value={}), \
                    mock.patch.object(dl.dplan, 'land_class_columns', return_value=dict.fromkeys(wood, 'natural')), \
                    mock.patch.object(dl.dplan, 'build_decor_plan', return_value={'items': []}), \
                    mock.patch.object(dl.ob, 'mapped_outbuilding_footprints', return_value=[]), \
                    mock.patch.object(dl.ob, 'lidar_rise', return_value=None), \
                    mock.patch.object(dl.sf, 'designer_items', return_value=[]), \
                    mock.patch.object(dl.sf, 'designer_observations', return_value=[]), \
                    mock.patch('lidar_heightmap.reference_world', return_value=mock.Mock(heightmap=lambda c: None)):
                with mock.patch('layer_common.railway_reserved_columns', return_value=set()):
                    baseline, _ = dl.plan_cell((0, 0), Plan('without-reservation'), world)
                protected, _ = dl.plan_cell((0, 0), Plan('reserved'), world)
            self.assertTrue(baseline)
            self.assertEqual(protected, {})
