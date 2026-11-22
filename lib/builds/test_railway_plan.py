"""Synthetic rail legality, profiles, ownership gaps and overhead clearances."""
from __future__ import annotations

import json
import math
import unittest

import numpy as np

import railway_plan as R
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


if __name__ == '__main__':
    unittest.main()
