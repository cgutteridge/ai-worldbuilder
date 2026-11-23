"""Crossing spec, not-ready gate and builder, on synthetic plans and MemoryBlocks."""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

import numpy as np

import crossing_feature as C
import railway_feature as F
from journal_testkit import JournalWorld, LiveApplyCase
from railway_evidence import LevelCrossing, Line


def ground(x0=-50, z0=-50, width=180, depth=120):
    terrain = np.full((width, depth), 30.)
    return {'x0': x0, 'z0': z0, 'terrain': terrain, 'h': terrain.copy()}


def line(points=((10, 10), (30, 10)), *, crossings=()):
    return Line('line-one', (tuple(points),), (('w1',),), crossings, (), ())


def railway():
    crossing = LevelCrossing('n2', 'line-one', 0, (20, 10), 10, None, None, None, None, None)
    return F.plan_feature([line(crossings=(crossing,))], ground(), (0, 0),
                          crossing_roads={'n2': {'highway': 'residential', 'width': '4'}})


def spec_from(raw, **extra):
    """A crossing spec whose joins are the planned gap's two ends."""
    plan = raw['plans'][0]
    gap = plan['gaps'][0]
    columns = [list(column) for column in gap['columns']]
    road = []
    for x, z in columns:
        road.extend(([x, z], [x, z - 1], [x, z + 1]))
    spec = {
        'kind': 'crossing', 'name': 'crossing_0_0_1', 'cell': [0, 0], 'n': 1,
        'abutments': [
            {'feature': raw['name'], 'line_id': plan['line_id'], 'source': gap['source'],
             'ends': [gap['ends'][0]]},
            {'feature': raw['name'], 'line_id': plan['line_id'], 'source': gap['source'],
             'ends': [gap['ends'][1]]},
        ],
        'tracks': [{'track_index': gap['track_index'], 'columns': columns}],
        'road': {'way_ids': ['road-1'], 'surface': 'tarmac', 'columns': road},
        'evidence': {'osm': {}, 'frames': ['frame-1']},
    }
    spec.update(extra)
    return spec


def pair_meta(feature, end, source='n2'):
    """One landed line feature holding a single crossing end."""
    return {
        'kind': 'railway', 'boxes': [[0, 0, 127, 127]], 'line_ids': ['line-one'],
        'joins': [], 'junctions': [],
        'gaps': [{'kind': 'crossing', 'source': source, 'line_id': 'line-one', 'track_index': 0,
                  'ends': [end], 'where': list(end['at']), 'columns': [[6, 0]]}],
    }


class TestCrossingSpec(unittest.TestCase):
    def test_unknown_block_and_disagreeing_flags_are_rejected(self):
        raw = railway()
        spec = spec_from(raw)
        spec['evidence']['osm'] = {'crossing:light': 'yes'}
        spec['light'] = {'flag': 'yes', 'block': 'not_a_real_block', 'positions': [[1, 2, 3]]}
        with self.assertRaisesRegex(ValueError, 'not a valid block'):
            C.load_spec_dict(spec)
        spec['light']['block'] = 'redstone_lamp'
        spec['light']['flag'] = 'no'
        with self.assertRaisesRegex(ValueError, 'disagrees'):
            C.load_spec_dict(spec)

    def test_barrier_kind_resolves_through_the_shared_vocabulary(self):
        self.assertEqual(C.resolve_block('plank_fence'), 'oak_planks')
        self.assertEqual(C.resolve_block('iron_bars'), 'iron_bars')

    def test_bridge_header_rejects_geometry_and_reads_joins(self):
        end_a = {'track_index': 0, 'at': [5, 0], 'level': 31, 'direction': 'east'}
        end_b = {'track_index': 0, 'at': [7, 0], 'level': 31, 'direction': 'west'}
        spec = {
            'kind': 'bridge', 'name': 'bridge_0_0_1', 'cell': [0, 0], 'n': 1,
            'abutments': [
                {'feature': 'railway_a', 'line_id': 'line-one', 'source': 'span-1', 'ends': [end_a]},
                {'feature': 'railway_b', 'line_id': 'line-one', 'source': 'span-1', 'ends': [end_b]},
            ],
            'evidence': {'osm': {}, 'frames': []},
            'road': {'way_ids': ['w'], 'surface': 'tarmac', 'columns': [[6, 0]]},
        }
        with self.assertRaisesRegex(ValueError, 'geometry is not part of this spec'):
            C.load_spec_dict(spec)
        del spec['road']
        saved = C.load_spec_dict(spec)
        with JournalWorld() as jw:
            jw.build('railway_a', 'feature', {(5, 31, 0): 'minecraft:stone'}, cell=(0, 0),
                     meta=pair_meta('railway_a', end_a, 'span-1') | {'gaps': [{
                         **pair_meta('railway_a', end_a, 'span-1')['gaps'][0], 'kind': 'bridge'}]})
            jw.build('railway_b', 'feature', {(7, 31, 0): 'minecraft:stone'}, cell=(0, 0),
                     meta=pair_meta('railway_b', end_b, 'span-1') | {'gaps': [{
                         **pair_meta('railway_b', end_b, 'span-1')['gaps'][0], 'kind': 'bridge'}]})
            C.require_ready(saved, jw.reg)
            with self.assertRaisesRegex(ValueError, 'geometry is not implemented'):
                C.build(saved, jw.reg)
            self.assertEqual(list(jw.reg.entries(plan='bridge_0_0_1')), [])


class TestCrossingGate(unittest.TestCase):
    def ends(self):
        return (
            {'track_index': 0, 'at': [5, 0], 'level': 31, 'direction': 'east'},
            {'track_index': 0, 'at': [7, 0], 'level': 31, 'direction': 'west'},
        )

    def spec(self):
        left, right = self.ends()
        return {
            'kind': 'crossing', 'name': 'crossing_0_0_1', 'cell': [0, 0], 'n': 1,
            'abutments': [
                {'feature': 'railway_a', 'line_id': 'line-one', 'source': 'n2', 'ends': [left]},
                {'feature': 'railway_b', 'line_id': 'line-one', 'source': 'n2', 'ends': [right]},
            ],
            'tracks': [{'track_index': 0, 'columns': [[6, 0]]}],
            'road': {'way_ids': ['road-1'], 'surface': 'tarmac',
                     'columns': [[6, 0], [6, -1], [6, 1]]},
            'evidence': {'osm': {}, 'frames': []},
        }

    def land(self, jw, name, end):
        jw.build(name, 'feature',
                 {(end['at'][0], end['level'], end['at'][1]): 'minecraft:stone'},
                 cell=(0, 0), meta=pair_meta(name, end))

    def test_missing_neighbours_write_nothing(self):
        spec = self.spec()
        watched = {(6, y, 0) for y in range(28, 36)}
        with JournalWorld() as jw:
            before = jw.blocks.read_many(watched)
            with self.assertRaisesRegex(C.NotReady, 'not ready'):
                C.build(spec, jw.reg)
            self.assertEqual(jw.blocks.read_many(watched), before)
            self.assertEqual(list(jw.reg.entries(plan=spec['name'])), [])
            self.land(jw, 'railway_a', self.ends()[0])
            with self.assertRaisesRegex(C.NotReady, 'railway_b'):
                C.build(spec, jw.reg)
            self.assertEqual(jw.blocks.read_many(watched), before)
            self.assertEqual(list(jw.reg.entries(plan=spec['name'])), [])

    def test_both_neighbours_lay_a_continuous_powered_rail_and_no_gates(self):
        left, right = self.ends()
        with JournalWorld() as jw:
            self.land(jw, 'railway_a', left)
            self.land(jw, 'railway_b', right)
            plan = C.build(self.spec(), jw.reg)
        self.assertEqual(plan.cells[(6, 31, 0)], 'minecraft:powered_rail[shape=east_west,powered=true]')
        self.assertEqual(plan.cells[(6, 30, 0)], 'minecraft:gray_concrete')
        self.assertEqual(plan.cells[(6, 29, 0)], 'minecraft:redstone_torch')
        self.assertEqual(plan.cells[(6, 28, 0)], 'minecraft:stone')
        self.assertEqual(plan.cells[(6, 30, -1)], 'minecraft:gray_concrete')
        self.assertFalse(any('fence' in block or 'iron_bars' in block or block.endswith('bell')
                             for block in plan.cells.values()))

    def test_barrier_flag_places_the_named_block(self):
        left, right = self.ends()
        spec = self.spec()
        spec['evidence']['osm'] = {'crossing:barrier': 'double_half'}
        spec['barrier'] = {
            'flag': 'double_half',
            'blocks': {'post': 'oak_fence', 'bar': 'iron_bars'},
            'positions': [{'at': [6, 31, -1], 'part': 'post'}, {'at': [6, 32, -1], 'part': 'bar'}],
        }
        with JournalWorld() as jw:
            self.land(jw, 'railway_a', left)
            self.land(jw, 'railway_b', right)
            plan = C.build(spec, jw.reg)
        self.assertEqual(plan.cells[(6, 31, -1)], 'minecraft:oak_fence')
        self.assertEqual(plan.cells[(6, 32, -1)], 'minecraft:iron_bars')


class TestCrossingApply(LiveApplyCase):
    def test_apply_then_undo_restores_the_crossing_cells(self):
        raw = railway()
        gap = raw['plans'][0]['gaps'][0]
        level = gap['ends'][0]['level']
        spec = spec_from(raw)
        folder = self.t / 'site' / 'builds'
        folder.mkdir(parents=True)
        rail_path = folder / (raw['name'] + '.json')
        rail_path.write_text(json.dumps(raw))
        path = folder / (spec['name'] + '.json')
        path.write_text(json.dumps(spec))
        import site_lock
        with mock.patch.object(site_lock, 'check', lambda owner=None: None), redirect_stdout(io.StringIO()):
            F.run(rail_path, do_apply=True)
            crossing = C.build(spec, self.reg())
            before = self.blocks.read_many(crossing.cells)
            plan = C.run(path, do_apply=True)
        self.assertTrue(all(plan.cells[(x, level, z)].startswith('minecraft:powered_rail')
                            for x, z in gap['columns']))
        self.assertTrue(all(plan.cells[(x, level - 1, z)] == 'minecraft:gray_concrete'
                            for x, z in gap['columns']))
        entry = self.entries(spec['name'])[-1]
        self.assertEqual((entry.status, entry.meta['kind'], entry.meta['builder_name']),
                         ('landed', 'crossing', C.BUILDER))
        self.assertTrue(entry.meta['tiled_backup'])
        self.assertEqual(set(self.reg().recorded(spec['name'])), set(plan.cells))
        outside = next(p for p in F.build(raw).cells if p not in plan.cells)
        kept = self.blocks.read(outside)
        with redirect_stdout(io.StringIO()):
            self.run_undo(spec['name'])
        self.assertEqual(self.blocks.read_many(plan.cells), before)
        self.assertEqual(self.blocks.read(outside), kept)
        self.assertTrue(self.entries(spec['name'])[-1].undone)

    def test_readback_retries_a_dropped_rail(self):
        raw = railway()
        spec = spec_from(raw)
        with mock.patch('site_lock.check', lambda owner=None: None), redirect_stdout(io.StringIO()):
            self.run_apply(F.build(raw), cell=(0, 0), meta=F.metadata(raw), strict=True, readback_retries=2)
        plan = C.build(spec, self.reg())
        lost = next(iter(plan.cells))
        original = __import__('journal_testkit').FakePaced.text
        dropped = []

        def text(client, name, args):
            result = original(client, name, args)
            if name == 'block_fill_batch' and not dropped:
                dropped.append(lost)
                self.blocks.write(lost, 'minecraft:air')
            return result

        from journal_testkit import FakePaced
        with mock.patch.object(FakePaced, 'text', text), redirect_stdout(io.StringIO()):
            report = self.run_apply(plan, cell=(0, 0), meta=C.metadata(spec), strict=True, readback_retries=2)
        self.assertEqual(report['mismatches'], 0)
        self.assertEqual(report['readback_retries'], [{'attempt': 1, 'positions': 1}])
        self.assertEqual(len(self.entries(spec['name'])), 1)

    def test_registry_check_reports_a_malformed_spec(self):
        from cell_journal import Note
        from registry_verify import check_feature_specs
        spec = spec_from(railway())
        with JournalWorld() as jw:
            jw.reg.change(Note((0, 0), 'plan_feature', 'crossing', spec['name']))
            folder = jw.root / 'site' / 'builds'
            folder.mkdir(parents=True)
            path = folder / (spec['name'] + '.json')
            path.write_text(json.dumps(spec))
            # The gate is not a registry concern: a saved spec can be waiting on its lines.
            self.assertEqual(check_feature_specs(jw.reg, jw.root), [])
            path.write_text(json.dumps({'kind': 'crossing', 'name': spec['name']}))
            self.assertTrue(any(row['id'] == 'invalid-spec:' + spec['name']
                                for row in check_feature_specs(jw.reg, jw.root)))
