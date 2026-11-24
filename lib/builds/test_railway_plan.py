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
            g['terrain'][x - g['x0'], :] = 30 + min(3, x // 100)
        g['h'] = g['terrain'].copy()
        p = self.plan(line(((0, 0), (310, 0))), g)
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

    def test_box_is_512_blocks_centred_on_the_start_cell(self):
        p = self.plan(line(((0, 0), (100, 0))))
        self.assertEqual(p['box'], [-192, -192, 319, 319])
        self.assertEqual(R.PLANNER_BOX, 512)

    def test_a_short_line_is_wholly_inside_one_box_with_no_joins(self):
        p = self.plan(line(((-150, 0), (300, 0))), ground(x0=-200, width=600))
        self.assertEqual(p['failures'], [])
        self.assertEqual(len(p['rails']), 451)
        self.assertEqual(p['joins'], [])

    def test_line_longer_than_the_box_is_cut_and_each_next_feature_continues_it(self):
        g = ground(x0=-300, width=1500, depth=110)
        evidence = line(((-100, 0), (1000, 0)))
        first = self.plan(evidence, g)
        self.assertEqual(first['failures'], [])
        self.assertEqual(first['box'][2] - first['box'][0] + 1, 512)
        self.assertEqual(len(first['joins']), 1)
        plans, join = [first], first['joins'][0]
        self.assertEqual((join['side'], join['direction'], join['tracks'][0]['next']), ('end', 'east', [320, 0]))
        while plans[-1]['joins']:
            join = plans[-1]['joins'][0]
            nxt = self.plan(evidence, g, tuple(join['cell']), [join])
            self.assertEqual(nxt['failures'], [])
            # The continuation starts on the recorded column at the recorded level.
            self.assertEqual(nxt['rails'][0][:3], [join['tracks'][0]['next'][0], join['level'], 0])
            plans.append(nxt)
        self.assertEqual(len(plans), 4)
        columns = [r[0] for p in plans for r in p['rails']]
        self.assertEqual(columns, list(range(-100, 1001)))
        self.assertEqual({r[1] for p in plans for r in p['rails']}, {31})

    def test_a_cut_keeps_the_step_spacing_and_level_across_the_join(self):
        g = ground(x0=-300, width=1500, depth=110)
        for x in range(-300, 1200):
            g['terrain'][x - g['x0'], :] = 30 + max(0, x) // 250
        g['h'] = g['terrain'].copy()
        evidence = line(((-100, 0), (900, 0)))
        plans = [self.plan(evidence, g)]
        while plans[-1]['joins']:
            join = plans[-1]['joins'][0]
            plans.append(self.plan(evidence, g, tuple(join['cell']), [join]))
        self.assertTrue(all(p['failures'] == [] for p in plans))
        rails = [r for p in plans for r in p['rails']]
        self.assertEqual([r[0] for r in rails], list(range(-100, 901)))
        steps = [i for i in range(1, len(rails)) if rails[i][1] != rails[i - 1][1]]
        self.assertGreaterEqual(len(steps), 3)
        self.assertTrue(all(b - a >= R.RAIL_MIN_STEP_RUN for a, b in zip(steps, steps[1:])))
        self.assertTrue(all(abs(rails[i][1] - rails[i - 1][1]) <= 1 for i in range(1, len(rails))))

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

    def test_footpath_without_equipment_keeps_rails_and_lays_the_path(self):
        crossing = LevelCrossing('foot-1', 'fixture-line', 0, (50, 0), 50,
                                 None, None, None, None, None)
        p = self.plan(line(((0, 0), (100, 0)), crossings=(crossing,)),
                      crossing_roads={'foot-1': {'highway': 'footway'}})
        self.assertEqual(p['failures'], [])
        self.assertEqual(p['gaps'], [])
        self.assertTrue(any(r[0] == 50 and r[2] == 0 for r in p['rails']))
        surface = {tuple(row[:2]): row[2] for row in p['surfaces']}
        self.assertEqual(surface[(50, 0)], 'paving')

    def test_private_track_without_equipment_is_not_a_gap(self):
        crossing = LevelCrossing('yard-1', 'fixture-line', 0, (50, 0), 50,
                                 None, None, None, None, None)
        p = self.plan(line(((0, 0), (100, 0)), crossings=(crossing,)),
                      crossing_roads={'yard-1': {'highway': 'track', 'access': 'private'}})
        self.assertEqual(p['failures'], [])
        self.assertEqual(p['gaps'], [])
        self.assertTrue(any(r[0] == 50 for r in p['rails']))

    def test_footpath_with_a_barrier_tag_is_still_a_crossing_gap(self):
        crossing = LevelCrossing('foot-2', 'fixture-line', 0, (50, 0), 50,
                                 'gate', None, None, None, None)
        p = self.plan(line(((0, 0), (100, 0)), crossings=(crossing,)),
                      crossing_roads={'foot-2': {'highway': 'footway', 'width': '2'}})
        self.assertEqual(p['failures'], [])
        self.assertEqual(p['gaps'][0]['kind'], 'crossing')
        self.assertFalse(any(r[0] == 50 for r in p['rails']))

    def test_boundary_corner_uses_the_actual_outside_neighbour(self):
        g = ground(x0=-100, z0=-20, width=1450, depth=160)
        evidence = line(((0, 0), (319, 0), (319, 100), (500, 100)))
        p = self.plan(evidence, g)
        self.assertEqual(p['failures'], [])
        boundary = next(r for r in p['rails'] if (r[0], r[2]) == (319, 100))
        self.assertEqual(boundary[3], 'north_east')
        self.assertEqual(boundary[4], 'minecraft:rail[shape=north_east]')
        continuation = self.plan(evidence, g, tuple(p['joins'][0]['cell']), p['joins'])
        self.assertEqual(continuation['failures'], [])
        boundary[3:] = ['north_south', 'minecraft:powered_rail[shape=north_south,powered=true]']
        with self.assertRaises(AssertionError):
            R.validate_plan(p)

    def test_incoming_join_direction_is_entry_not_the_onward_direction(self):
        g = ground(x0=-100, z0=-20, width=1450, depth=160)
        evidence = line(((0, 0), (320, 0), (320, 100)))
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
                      crossing_roads={str(x): {'highway': 'residential', 'width': '6'} for x in (50, 55)})
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
            g['terrain'][x - g['x0'], :] = 33 - min(3, x // 100)
        g['h'] = g['terrain'].copy()
        p = self.plan(line(((0, 0), (310, 0))), g)
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


class BoxEdgeGapTest(unittest.TestCase):
    """Structure gaps and junctions at a 512 box edge (#464). Rail Y 31; the box is x -192..319."""

    plan = RailwayPlannerTest.plan
    ROADS = {'x': {'highway': 'residential', 'width': '6'}}

    @staticmethod
    def crossed(x, start=-100, end=1000):
        crossing = LevelCrossing('x', 'fixture-line', 0, (x, 0), x - start, None, None, None, None, None)
        return line(((start, 0), (end, 0)), crossings=(crossing,))

    def wide(self):
        return ground(x0=-300, width=1500, depth=110)

    def test_a_track_that_leaves_the_box_and_comes_back_fails_loudly_with_both_coordinates(self):
        u = line(((0, 0), (400, 0), (400, 50), (0, 50)))
        g = ground(x0=-300, width=800, depth=110)
        p = self.plan(u, g)
        (failure,) = p['failures']
        self.assertIn('leaves the planning box at 319,0 and re-enters it at 319,50', failure['reason'])
        self.assertIn('choose a start cell', failure['reason'])
        self.assertEqual((p['rails'], p['joins']), ([], []))
        entry = [{'line_id': 'fixture-line', 'tracks': [
            {'track_index': 0, 'next': [0, 0], 'level': 31, 'direction': 'east', 'run': 0}]}]
        p = self.plan(u, g, incoming=entry)
        self.assertIn('leaves the planning box at 319,0 and re-enters it at 319,50', p['failures'][0]['reason'])
        self.assertEqual(p['rails'], [])

    def test_a_track_that_only_runs_along_the_box_edge_still_plans(self):
        p = self.plan(line(((319, -50), (319, 50))), ground(x0=-300, width=800, depth=220))
        self.assertEqual(p['failures'], [])
        self.assertEqual(len(p['rails']), 101)

    def test_a_crossing_wholly_inside_the_box_is_a_gap_with_two_ends(self):
        p = self.plan(self.crossed(200), self.wide(), crossing_roads=self.ROADS)
        self.assertEqual(p['failures'], [])
        (gap,) = p['gaps']
        self.assertEqual([e['at'] for e in gap['ends']], [[195, 0], [205, 0]])
        self.assertFalse(any(e.get('from_join') for e in gap['ends']))

    def test_a_crossing_straddling_the_edge_fails_loudly_with_coordinates(self):
        p = self.plan(self.crossed(322), self.wide(), crossing_roads=self.ROADS)
        (failure,) = p['failures']
        self.assertEqual(failure['where'], [319, 0])
        self.assertIn('crossing gap x straddles the planning box edge at 319,0', failure['reason'])
        self.assertIn('choose a start cell', failure['reason'])
        self.assertEqual((p['rails'], p['joins']), ([], []))

    def test_a_crossing_straddling_the_entry_edge_fails_the_same_way(self):
        p = self.plan(self.crossed(-194, start=-400, end=100), self.wide(), crossing_roads=self.ROADS)
        self.assertEqual([f['where'] for f in p['failures']], [[-192, 0]])
        self.assertIn('straddles the planning box edge at -192,0', p['failures'][0]['reason'])

    def test_a_gap_ending_on_the_last_inside_column_has_no_abutting_rail(self):
        p = self.plan(self.crossed(315), self.wide(), crossing_roads=self.ROADS)
        (failure,) = p['failures']
        self.assertEqual(failure['where'], [319, 0])
        self.assertIn('without an abutting rail at 319,0', failure['reason'])
        self.assertIn('choose a start cell', failure['reason'])

    def test_a_gap_that_starts_at_the_cut_is_the_next_features_with_the_join_as_its_first_end(self):
        evidence = self.crossed(324)
        g = self.wide()
        first = self.plan(evidence, g, crossing_roads=self.ROADS)
        self.assertEqual((first['failures'], first['gaps']), ([], []))
        (join,) = first['joins']
        self.assertEqual(join['tracks'][0]['next'], [320, 0])
        second = self.plan(evidence, g, tuple(join['cell']), [join], crossing_roads=self.ROADS)
        self.assertEqual(second['failures'], [])
        (gap,) = second['gaps']
        self.assertEqual((gap['start'], gap['columns'][0], gap['columns'][-1]), (0, [320, 0], [328, 0]))
        joined, after = gap['ends']
        self.assertEqual((joined['at'], joined['level'], joined['direction'], joined.get('from_join')),
                         ([319, 0], first['rails'][-1][1], 'east', True))
        self.assertEqual((after['at'], after['direction']), ([329, 0], 'west'))
        self.assertEqual(second['rails'][0][0], 329)

    def test_a_crossing_beyond_the_edge_margin_is_another_features_and_needs_no_road_tags(self):
        p = self.plan(self.crossed(600), self.wide())
        self.assertEqual((p['failures'], p['gaps']), ([], []))
        self.assertEqual(len(p['joins']), 1)

    def test_a_crossing_just_outside_the_box_still_needs_its_road_tags(self):
        p = self.plan(self.crossed(350), self.wide())
        self.assertEqual(p['failures'][0]['reason'], 'crossing is missing road tags')

    def test_an_overhead_gap_straddling_the_edge_fails_and_one_inside_resolves(self):
        def span(x0, x1):
            deck = frozenset((x, z) for x in range(x0, x1 + 1) for z in range(-2, 3))
            return OverheadSpan('fixture-line', 'b', 'footway', 'yes', '1', deck,
                                (OverheadCrossing(0, x0 + 10, (x0 + 10, 0)),))
        unknown = {'b': deck_height('unknown')}
        edge = self.plan(line(((-100, 0), (1000, 0)), overhead=(span(310, 330),)), self.wide(), structures=unknown)
        self.assertIn('overhead gap b straddles the planning box edge at 319,0', edge['failures'][0]['reason'])
        inside = self.plan(line(((-100, 0), (1000, 0)), overhead=(span(100, 120),)), self.wide(), structures=unknown)
        self.assertEqual(inside['failures'], [])
        self.assertEqual([(g['kind'], g['columns'][0], g['columns'][-1]) for g in inside['gaps']],
                         [('overhead', [100, 0], [120, 0])])

    def test_a_junction_gap_touching_a_crossing_gap_is_one_junction_stretch(self):
        crossing = LevelCrossing('x', 'fixture-line', 0, (50, 0), 50, None, None, None, None, None)
        p = self.plan(line(((0, 0), (100, 0)), crossings=(crossing,)), crossing_roads=self.ROADS,
                      junction_gaps={0: [{'source': 'main', 'start': 55, 'end': 58}]})
        self.assertEqual(p['failures'], [])
        (gap,) = p['gaps']
        self.assertEqual((gap['kind'], gap['source'], gap['also']), ('junction', 'x+main', ['crossing']))
        self.assertEqual((gap['columns'][0], gap['columns'][-1]), ([46, 0], [58, 0]))
        self.assertEqual([e['at'] for e in gap['ends']], [[45, 0], [59, 0]])

    def test_two_structure_gaps_that_touch_still_need_a_combined_structure(self):
        crossings = tuple(LevelCrossing(str(x), 'fixture-line', 0, (x, 0), x, None, None, None, None, None)
                          for x in (50, 59))
        p = self.plan(line(((0, 0), (100, 0)), crossings=crossings),
                      crossing_roads={str(x): {'highway': 'residential', 'width': '6'} for x in (50, 59)})
        self.assertIn('combined structure', p['failures'][0]['reason'])


class JunctionGapsTest(unittest.TestCase):
    """:func:`railway_plan.junction_gaps` on hand-made first-pass plans (#452)."""

    @staticmethod
    def plan(line_id, path, gap=None, before=None, after=None, tracks=1):
        mask = gap or [False] * len(path)
        track = {'track_index': 0, 'path': [list(p) for p in path], 'gap': list(mask),
                 'before': before, 'after': after}
        rails = [[x, 31, z, 'east_west', ''] for (x, z), g in zip(path, mask) if not g]
        return {'line_id': line_id, 'tracks': [track] * tracks, 'rails': rails}

    @staticmethod
    def row(z, x0, x1):
        return [(x, z) for x in range(x0, x1 + 1)]

    @classmethod
    def dipping(cls, line_id, hits, length, **kwargs):
        """A side line along z=5 that touches the main line (z=0) at the given x values."""
        return cls.plan(line_id, [(x, 0 if x in hits else 5) for x in range(length)], **kwargs)

    def spans(self, out, index):
        return [(g['start'], g['end'], g['source']) for g in out[index][0]]

    def test_short_runs_cut_off_by_the_overlap_are_absorbed_and_long_ones_kept(self):
        main = self.plan('main', self.row(0, 0, 200), tracks=2)
        side = self.dipping('side', {10, 14, 40}, 60)
        out = R.junction_gaps([side, main])
        self.assertEqual(sorted(out), [0])
        # 11..13 is a three-cell stub between two overlaps (absorbed); 0..9, 15..39 and
        # 41..59 are long enough to join something and stay.
        self.assertEqual(self.spans(out, 0), [(10, 14, 'main'), (40, 40, 'main')])

    def test_runs_next_to_a_structure_gap_or_a_box_join_are_not_orphans(self):
        main = self.plan('main', self.row(0, 0, 100), tracks=2)
        gap = [True, True] + [False] * 28
        side = self.dipping('side', {4, 8, 20, 28}, 30, gap=gap, after=(30, 5))
        out = R.junction_gaps([main, side])
        # 2..3 touches the structure gap and 29 the box join, so both stay; 5..7 is cut off
        # on both sides and goes.
        self.assertEqual(self.spans(out, 1), [(4, 8, 'main'), (20, 20, 'main'), (28, 28, 'main')])

    def test_an_overlap_on_a_box_join_fails_with_both_line_ids_and_the_coordinates(self):
        main = self.plan('main', self.row(0, 0, 100), tracks=2)
        side = self.plan('side', self.row(0, 50, 80), before=(49, 0))
        with self.assertRaisesRegex(ValueError, r'junction with main covers the box join of side at \[50, 0\].*choose a start cell'):
            R.junction_gaps([main, side])

    def test_duplicate_line_ids_cannot_choose_a_main_line(self):
        a = self.plan('same', self.row(0, 0, 10))
        b = self.plan('same', self.row(0, 5, 20))
        with self.assertRaisesRegex(ValueError, 'distinct line ids'):
            R.junction_gaps([a, b])

    def test_no_shared_columns_means_no_gaps(self):
        self.assertEqual(R.junction_gaps([self.plan('a', self.row(0, 0, 10)),
                                          self.plan('b', self.row(5, 0, 10))]), {})

    def test_plan_line_rejects_junction_gaps_outside_the_track(self):
        with self.assertRaisesRegex(ValueError, 'outside track 0'):
            R.plan_line(line(((0, 0), (50, 0))), ground(), (0, 0), cell_size=128,
                        junction_gaps={0: [{'source': 'x', 'start': 40, 'end': 90}]})


if __name__ == '__main__':
    unittest.main()
