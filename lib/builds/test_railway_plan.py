"""Synthetic rail legality, profiles, ownership gaps and overhead clearances."""
from __future__ import annotations

import json
import math
import unittest
from types import SimpleNamespace

import numpy as np

import railway_plan as R
import structure_evidence as se
from railway_evidence import Line, LevelCrossing, OverheadSpan, OverheadCrossing, RailSpan, TrackSpan


def line(*tracks, crossings=(), spans=(), overhead=()):
    return Line('fixture-line', tuple(tuple(tuple(p) for p in t) for t in tracks),
                tuple((str(i),) for i in range(len(tracks))), crossings, spans, overhead)


def ground(x0=-100, z0=-100, width=700, depth=220, height=30):
    terrain = np.full((width, depth), height, dtype=float)
    return {'x0': x0, 'z0': z0, 'terrain': terrain, 'h': terrain.copy(),
            'have': np.ones(terrain.shape, dtype=bool)}


class RailwayPlannerTest(unittest.TestCase):
    def plan(self, evidence, terrain=None, start_cell=(0, 0), incoming=(), **kwargs):
        p = R.plan_line(evidence, terrain if terrain is not None else ground(),
                        start_cell, incoming, cell_size=128, **kwargs)
        R.validate_plan(p)
        # The integration seam is deliberately plain JSON, including failure cases.
        R.validate_plan(json.loads(json.dumps(p)))
        return p

    def test_flat_straight_power_and_solid_torch_supports(self):
        p = self.plan(line(((0, 0), (100, 0))))
        self.assertEqual(p['failures'], [])
        self.assertEqual(len(p['rails']), 101)
        self.assertEqual(len(p['torches']), 101)
        self.assertEqual({r[3] for r in p['rails']}, {'east_west'})
        self.assertEqual({r[1] for r in p['rails']}, {31})
        self.assertTrue(all(r[4] == 'minecraft:powered_rail[shape=east_west,powered=true]'
                            for r in p['rails']))
        self.assertTrue(all(p[1] == 28 for p in p['torch_supports']))
        self.assertEqual(p['joins'], [])

    def test_diagonal_corners_never_step_or_take_powered_rail(self):
        p = self.plan(line(((0, 0), (70, 70))))
        self.assertEqual(p['failures'], [])
        self.assertTrue(any(r[3] not in ('east_west', 'north_south') for r in p['rails']))
        self.assertEqual({r[1] for r in p['rails']}, {31})
        corners = [r for r in p['rails'] if r[3] not in ('east_west', 'north_south')]
        self.assertTrue(all(r[4].startswith('minecraft:rail[') for r in corners))

    def test_quarter_circle_rail_shapes_connect_both_neighbours(self):
        points = tuple((60 * math.cos(t), 60 * math.sin(t)) for t in np.linspace(0, math.pi / 2, 24))
        p = self.plan(line(points))
        self.assertEqual(p['failures'], [])
        self.assertGreater(len(p['rails']), 80)
        self.assertTrue(any(r[3] == 'south_east' for r in p['rails']))

    def test_three_steps_are_collinear_and_at_least_100_cells_apart(self):
        g = ground()
        for x in range(500):
            g['terrain'][x - g['x0'], :] = 30 + min(3, x // 120)
        g['h'] = g['terrain'].copy()
        p = self.plan(line(((0, 0), (450, 0))), g)
        self.assertEqual(p['failures'], [])
        levels = p['tracks'][0]['levels']
        steps = [i for i in range(1, len(levels)) if levels[i] != levels[i - 1]]
        self.assertEqual(len(steps), 3)
        self.assertTrue(all(b - a >= R.RAIL_MIN_STEP_RUN for a, b in zip(steps, steps[1:])))
        self.assertEqual(levels[-1] - levels[0], 3)

    def test_paired_tracks_use_shared_target_and_stay_within_one_level(self):
        g = ground()
        for x in range(450):
            g['terrain'][x - g['x0'], :] = 30 + x // 120
        g['terrain'][:, 4 - g['z0']] += 2
        g['h'] = g['terrain'].copy()
        p = self.plan(line(((0, 0), (400, 0)), ((0, 4), (400, 4))), g)
        self.assertEqual(p['failures'], [])
        a, b = (t['levels'] for t in p['tracks'])
        self.assertEqual(a[0], b[0])
        self.assertLessEqual(max(abs(x - y) for x, y in zip(a, b)), 1)

    def test_3000_block_line_uses_box_cut_and_matching_incoming_join(self):
        g = ground(x0=-1000, width=3200, depth=110)
        evidence = line(((-900, 0), (2099, 0)))
        first = self.plan(evidence, g)
        self.assertEqual(first['failures'], [])
        self.assertEqual(first['box'][2] - first['box'][0] + 1, 2048)
        self.assertEqual(len(first['joins']), 1)
        join = first['joins'][0]
        second = self.plan(evidence, g, tuple(join['cell']), [join])
        self.assertEqual(second['failures'], [])
        self.assertEqual(second['rails'][0][1], first['rails'][-1][1])
        self.assertEqual(second['rails'][0][0] - first['rails'][-1][0], 1)
        self.assertEqual(len(first['rails']) + len(second['rails']), 3000)
        self.assertEqual(second['joins'], [])

    def test_crossing_gap_has_no_writes_and_two_matching_level_ends(self):
        crossing = LevelCrossing('crossing-1', 'fixture-line', 0, (50, 0), 50,
                                 None, None, None, None, None)
        p = self.plan(line(((0, 0), (100, 0)), crossings=(crossing,)),
                      crossing_roads={'crossing-1': {'highway': 'residential', 'width': '6'}})
        self.assertEqual(p['failures'], [])
        gap = p['gaps'][0]
        self.assertEqual([e['at'] for e in gap['ends']], [[45, 0], [55, 0]])
        self.assertEqual({e['level'] for e in gap['ends']}, {31})
        self.assertEqual({e['direction'] for e in gap['ends']}, {'east', 'west'})
        self.assertFalse(any(46 <= r[0] <= 54 for r in p['rails']))
        self.assertFalse(any(46 <= c[0] <= 54 for c in p['columns']))
        self.assertIn((45, 2), {(c[0], c[1]) for c in p['columns']})
        self.assertIn((55, 2), {(c[0], c[1]) for c in p['columns']})

    def test_boundary_corner_uses_the_actual_outside_neighbour(self):
        g = ground(x0=-100, z0=-20, width=1450, depth=160)
        evidence = line(((0, 0), (1087, 0), (1087, 100), (1200, 100)))
        p = self.plan(evidence, g)
        self.assertEqual(p['failures'], [])
        boundary = next(r for r in p['rails'] if (r[0], r[2]) == (1087, 100))
        self.assertEqual(boundary[3], 'north_east')
        self.assertEqual(boundary[4], 'minecraft:rail[shape=north_east]')
        continuation = self.plan(evidence, g, tuple(p['joins'][0]['cell']), p['joins'])
        self.assertEqual(continuation['failures'], [])
        boundary[3:] = ['north_south', 'minecraft:powered_rail[shape=north_south,powered=true]']
        with self.assertRaises(AssertionError):
            R.validate_plan(p)

    def test_incoming_join_direction_is_entry_not_the_onward_direction(self):
        g = ground(x0=-100, z0=-20, width=1450, depth=160)
        evidence = line(((0, 0), (1088, 0), (1088, 100)))
        first = self.plan(evidence, g)
        self.assertEqual(first['failures'], [])
        join = first['joins'][0]
        second = self.plan(evidence, g, tuple(join['cell']), [join])
        self.assertEqual(second['failures'], [])
        self.assertEqual(second['rails'][0][3], 'south_west')

    def test_both_box_cuts_have_usable_outgoing_joins(self):
        g = ground(x0=-2100, z0=-20, width=4250, depth=45)
        evidence = line(((-2000, 0), (2000, 0)))
        p = self.plan(evidence, g)
        self.assertEqual(p['failures'], [])
        self.assertEqual({j['direction'] for j in p['joins']}, {'west', 'east'})
        for join in p['joins']:
            continuation = self.plan(evidence, g, tuple(join['cell']), [join])
            self.assertEqual(continuation['failures'], [])
            self.assertEqual(continuation['rails'][0][1], join['level'])
            if join['direction'] == 'west':
                self.assertLess(max(r[0] for r in continuation['rails']), min(r[0] for r in p['rails']))
            else:
                self.assertGreater(min(r[0] for r in continuation['rails']), max(r[0] for r in p['rails']))

    def test_overlapping_crossing_gaps_fail_clearly_instead_of_recording_unbuilt_ends(self):
        crossings = tuple(LevelCrossing(str(x), 'fixture-line', 0, (x, 0), x,
                                       None, None, None, None, None) for x in (50, 55))
        p = self.plan(line(((0, 0), (100, 0)), crossings=crossings),
                      crossing_roads={str(x): {'width': '6'} for x in (50, 55)})
        self.assertTrue(p['failures'])
        self.assertIn('gap', p['failures'][0]['reason'])
        self.assertEqual(p['rails'], [])

    def test_bridge_and_tunnel_are_gaps_without_a_guessed_design(self):
        for kind in ('bridge', 'tunnel'):
            span = RailSpan(kind, 'fixture-line', 'yes' if kind == 'bridge' else None,
                            'yes' if kind == 'tunnel' else None, None, None,
                            (TrackSpan(0, 40, 60, (40, 0), (60, 0)),))
            p = self.plan(line(((0, 0), (100, 0)), spans=(span,)))
            self.assertEqual(p['failures'], [])
            self.assertEqual(p['gaps'][0]['kind'], kind)
            self.assertFalse(any(40 <= r[0] <= 60 for r in p['rails']))

    def test_missing_crossing_road_evidence_is_a_failure_not_a_default_gap(self):
        crossing = LevelCrossing('1', 'fixture-line', 0, (50, 0), 50,
                                 None, None, None, None, None)
        p = self.plan(line(((0, 0), (100, 0)), crossings=(crossing,)))
        self.assertIn('road tags', p['failures'][0]['reason'])
        self.assertEqual(p['rails'], [])

    def test_missing_ground_is_a_reported_failure_not_an_exception(self):
        g = ground()
        g['have'][50 - g['x0'], -g['z0']] = False
        p = self.plan(line(((0, 0), (100, 0))), g)
        self.assertEqual(len(p['failures']), 1)
        self.assertIn('missing ground', p['failures'][0]['reason'])
        self.assertEqual(p['rails'], [])

    def test_unsolvable_profile_and_bad_join_fail_without_invalid_rails(self):
        g = ground()
        g['terrain'][150:, :] += 30
        p = self.plan(line(((0, 0), (100, 0))), g)
        self.assertTrue(p['failures'])
        self.assertEqual(p['rails'], [])
        incoming = [{'line_id': 'fixture-line', 'tracks': [
            {'track_index': 0, 'next': [0, 0], 'level': 500, 'direction': 'east'}]}]
        p = self.plan(line(((0, 0), (100, 0))), incoming=incoming)
        self.assertIn('join', p['failures'][0]['reason'])

    def test_non_solid_torch_support_is_reported(self):
        g = ground()
        g['solid'] = np.ones(g['terrain'].shape, dtype=bool)
        g['solid'][10 - g['x0'], -g['z0']] = False
        p = self.plan(line(((0, 0), (100, 0))), g)
        self.assertIn('torch support', p['failures'][0]['reason'])
        self.assertEqual(p['rails'], [])

    def test_overhead_deck_keeps_two_tracks_without_gap_or_bushes(self):
        deck = frozenset((x, z) for x in range(45, 56) for z in range(-10, 15))
        overhead = OverheadSpan('fixture-line', 'bridge-1', 'primary', 'yes', '1', deck,
                                (OverheadCrossing(0, 50, (50, 0)), OverheadCrossing(1, 50, (50, 4))))
        g = ground()
        # A cutting gives space for non-deck bushes and tests suppressed side slopes.
        g['terrain'][:] = 33
        g['terrain'][:, 100] = 30
        g['terrain'][:, 104] = 30
        g['h'][:] = g['terrain'] + 3
        for x, z in deck:
            g['h'][x - g['x0'], z - g['z0']] = 40
        p = self.plan(line(((0, 0), (100, 0)), ((0, 4), (100, 4)), overhead=(overhead,)), g)
        self.assertEqual(p['failures'], [])
        self.assertEqual(p['gaps'], [])
        self.assertEqual(len(p['rails']), 202)
        self.assertFalse({(p[0], p[2]) for p in p['planting']} & deck)
        self.assertFalse(any((c[0], c[1]) in deck and c[4] == 'slope' for c in p['columns']))
        self.assertTrue(p['planting'])

    def test_profile_rising_into_overhead_deck_fails_clearance(self):
        deck = frozenset((x, 0) for x in range(40, 61))
        overhead = OverheadSpan('fixture-line', 'b', 'primary', 'yes', '1', deck,
                                (OverheadCrossing(0, 50, (50, 0)),))
        g = ground()
        for x, z in deck:
            g['h'][x - g['x0'], z - g['z0']] = 35
        p = self.plan(line(((0, 0), (100, 0)), overhead=(overhead,)), g)
        self.assertIn('clearance', p['failures'][0]['reason'])
        self.assertEqual(p['rails'], [])

    def test_structure_columns_without_map_span_suppress_planting(self):
        g = ground()
        g['kind'] = np.zeros(g['terrain'].shape, dtype=int)
        g['terrain'][:] = 33
        g['terrain'][:, 100] = 30
        g['h'][:] = 40
        g['kind'][120:130, :] = 4
        p = self.plan(line(((0, 0), (100, 0))), g)
        self.assertEqual(p['failures'], [])
        self.assertFalse(any(20 <= c[0] < 30 for c in p['planting']))

    def test_structure_supports_are_preserved_and_rail_intersections_fail(self):
        g = ground()
        g['supports'] = np.zeros(g['terrain'].shape, dtype=bool)
        g['supports'][150, 102] = True
        p = self.plan(line(((0, 0), (100, 0))), g)
        self.assertEqual(p['failures'], [])
        self.assertNotIn((50, 2), {(c[0], c[1]) for c in p['columns']})
        g['supports'][150, 100] = True
        p = self.plan(line(((0, 0), (100, 0))), g)
        self.assertIn('support', p['failures'][0]['reason'])

    def test_vector_road_surface_replaces_grass_in_rail_footprint(self):
        g = ground()
        g['terrain'][:] = 33
        g['terrain'][:, 100] = 30
        p = self.plan(line(((0, 0), (100, 0))), g, road_surfaces={(50, 3): 'tarmac'})
        self.assertEqual(p['failures'], [])
        self.assertIn([50, 3, 'tarmac'], p['surfaces'])

    def test_loop_and_backtrack_repair_and_repeated_input_are_deterministic(self):
        points = ((0, 0), (10, 0), (10, 5), (5, 5), (5, 0), (20, 0))
        a = self.plan(line(points))
        b = self.plan(line(points))
        self.assertEqual(a, b)
        self.assertEqual(a['failures'], [])

    def test_descending_steps_are_ascending_shapes_toward_the_high_neighbour(self):
        g = ground()
        for x in range(450):
            g['terrain'][x - g['x0'], :] = 33 - min(3, x // 120)
        g['h'] = g['terrain'].copy()
        p = self.plan(line(((0, 0), (400, 0))), g)
        self.assertEqual(p['failures'], [])
        ascending = [r for r in p['rails'] if r[3].startswith('ascending_')]
        self.assertEqual(len(ascending), 3)
        self.assertTrue(all(r[3] == 'ascending_west' for r in ascending))

    def test_incoming_join_run_carries_step_spacing_into_next_feature(self):
        g = ground()
        g['terrain'][:] = 33
        incoming = [{'line_id': 'fixture-line', 'tracks': [
            {'track_index': 0, 'next': [0, 0], 'level': 31, 'direction': 'east', 'run': 0}]}]
        p = self.plan(line(((0, 0), (400, 0))), g, incoming=incoming)
        self.assertEqual(p['failures'], [])
        levels = p['tracks'][0]['levels']
        first_step = next(i for i in range(1, len(levels)) if levels[i] != levels[i - 1])
        self.assertGreaterEqual(first_step, R.RAIL_MIN_STEP_RUN)

    def test_planning_never_mutates_ground_arrays(self):
        g = ground()
        before = {k: v.copy() for k, v in g.items() if isinstance(v, np.ndarray)}
        p = self.plan(line(((0, 0), (100, 0))), g)
        self.assertEqual(p['failures'], [])
        for key, data in before.items():
            np.testing.assert_array_equal(data, g[key])

    def test_validator_rejects_a_powered_corner_and_wrong_step_height(self):
        p = self.plan(line(((0, 0), (10, 10))))
        corner = next(r for r in p['rails'] if r[3] not in ('north_south', 'east_west'))
        corner[4] = 'minecraft:powered_rail[powered=true]'
        with self.assertRaises(AssertionError):
            R.validate_plan(p)


def deck_height(status, underside=None, reason='test'):
    top = None if underside is None else underside + se.DECK_THICKNESS - 1
    return SimpleNamespace(height=se.DeckHeight(status, top, underside, 0, 9, reason))


class DeckEvidenceTest(RailwayPlannerTest):
    """Overhead deck heights from structure evidence (#451). Rail level is 30; rail Y 31."""

    deck = frozenset((x, z) for x in range(40, 61) for z in range(-2, 3))

    def overhead(self, osm_id='b'):
        return OverheadSpan('fixture-line', osm_id, 'footway', 'yes', '1', self.deck,
                            (OverheadCrossing(0, 50, (50, 0)),))

    def corridor(self):
        return line(((0, 0), (100, 0)), overhead=(self.overhead(),))

    def test_without_evidence_the_lidar_surface_is_the_ceiling(self):
        p = self.plan(self.corridor())
        self.assertIn('clearance', p['failures'][0]['reason'])
        self.assertEqual(p['decks'], [])

    def test_measured_deck_with_clearance_plans_through_without_a_gap(self):
        p = self.plan(self.corridor(), structures={'b': deck_height('measured', 40)})
        self.assertEqual((p['failures'], p['gaps']), ([], []))
        self.assertEqual(len(p['rails']), 101)
        self.assertEqual([(d['osm_id'], d['status'], d['usable'], d['underside']) for d in p['decks']],
                         [('b', 'measured', True, 40)])

    def test_measured_low_deck_still_fails_loudly(self):
        for underside, ok in ((36, True), (35, False), (34, False)):
            p = self.plan(self.corridor(), structures={'b': deck_height('measured', underside)})
            self.assertEqual(p['failures'] == [], ok, underside)
            if not ok:
                self.assertIn('less than 5 blocks', p['failures'][0]['reason'])
                self.assertEqual(p['rails'], [])
                self.assertEqual(p['gaps'], [])

    def test_unknown_deck_becomes_a_gap_with_end_records_and_the_rest_plans(self):
        p = self.plan(self.corridor(), structures={'b': deck_height('unknown', None, 'no columns')})
        self.assertEqual(p['failures'], [])
        (gap,) = p['gaps']
        self.assertEqual((gap['kind'], gap['source'], gap['deck'], gap['reason']),
                         ('overhead', 'b', 'unknown', 'no columns'))
        self.assertEqual(gap['where'], gap['columns'][0])
        self.assertEqual(sorted(c[0] for c in gap['columns']), list(range(40, 61)))
        self.assertEqual([(e['at'], e['level'], e['direction']) for e in gap['ends']],
                         [([39, 0], 31, 'east'), ([61, 0], 31, 'west')])
        xs = {r[0] for r in p['rails']}
        self.assertFalse(xs & set(range(40, 61)))
        self.assertEqual(len(p['rails']), 101 - 21)
        self.assertEqual(p['tracks'][0]['gap'].count(True), 21)

    def test_missing_evidence_record_is_unknown_not_a_failure(self):
        p = self.plan(self.corridor(), structures={})
        self.assertEqual(p['failures'], [])
        self.assertEqual(p['decks'][0]['status'], 'unknown')
        self.assertEqual(len(p['gaps']), 1)

    def test_inferred_deck_is_a_gap_by_default_and_trusted_by_policy(self):
        inferred = {'b': deck_height('inferred', 40)}
        self.assertFalse(R.INFERRED_DECK_USABLE)
        p = self.plan(self.corridor(), structures=inferred)
        self.assertEqual(p['failures'], [])
        self.assertEqual(p['gaps'][0]['deck'], 'inferred')
        self.assertIn('INFERRED_DECK_USABLE', p['gaps'][0]['reason'])
        p = self.plan(self.corridor(), structures=inferred, inferred_deck_usable=True)
        self.assertEqual((p['failures'], p['gaps']), ([], []))
        self.assertTrue(p['decks'][0]['usable'])

    def test_a_trusted_inferred_deck_that_is_too_low_still_fails_loudly(self):
        p = self.plan(self.corridor(), structures={'b': deck_height('inferred', 34)},
                      inferred_deck_usable=True)
        self.assertIn('less than 5 blocks', p['failures'][0]['reason'])
        self.assertEqual(p['gaps'], [])

    def test_inferred_policy_follows_the_module_constant(self):
        from unittest import mock
        with mock.patch.object(R, 'INFERRED_DECK_USABLE', True):
            p = self.plan(self.corridor(), structures={'b': deck_height('inferred', 40)})
        self.assertEqual((p['failures'], p['gaps']), ([], []))

    def test_touching_unknown_decks_merge_into_one_gap_per_track(self):
        near = OverheadSpan('fixture-line', 'c', 'footway', 'yes', '1',
                            frozenset((x, z) for x in range(62, 70) for z in range(-2, 7)),
                            (OverheadCrossing(0, 65, (65, 0)),))
        ev = {'b': deck_height('unknown'), 'c': deck_height('unknown')}
        p = self.plan(line(((0, 0), (100, 0)), ((0, 4), (100, 4)),
                           overhead=(self.overhead(), near)), structures=ev)
        self.assertEqual(p['failures'], [])
        self.assertEqual(sorted((g['track_index'], g['source'], g['columns'][0][0], g['columns'][-1][0])
                                for g in p['gaps']),
                         [(0, 'b+c', 40, 69), (1, 'c', 62, 69)])

    def test_gap_leaves_no_ground_or_planting_in_its_columns(self):
        g = ground()
        g['terrain'][:] = 33
        g['terrain'][:, 100] = 30
        g['h'][:] = g['terrain'] + 3
        p = self.plan(self.corridor(), g, structures={'b': deck_height('unknown')})
        self.assertEqual(p['failures'], [])
        gap = {tuple(c) for c in p['gaps'][0]['columns']}
        self.assertFalse({(c[0], c[1]) for c in p['columns']} & gap)
        self.assertFalse({(c[0], c[2]) for c in p['planting']} & gap)


if __name__ == '__main__':
    unittest.main()
