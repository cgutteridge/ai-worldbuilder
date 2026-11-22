#!/usr/bin/env python3
"""Unit tests for the roof ridge-jog and stair-shape lint (no world / OSM access).

  python3 -m unittest scripts/test_roof_lint.py
"""
import sys
import unittest
import io
from contextlib import redirect_stdout
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'shims'))
from roof_lint import (  # noqa: E402
    ridge_jogs,
    stair_shape_from_neighbours,
    stair_shape_mismatches,
)

T = 'minecraft:deepslate_tile'


def gable(cells, ridge_x, z0, z1, y=50, half=4, cap=f'{T}s'):
    """A ridge-on-x gable over rows z0..z1: stairs rising to a full-block ridge at ridge_x."""
    for z in range(z0, z1 + 1):
        cells[(ridge_x, y, z)] = cap
        for d in range(1, half + 1):
            cells[(ridge_x - d, y - d, z)] = f'{T}_stairs[facing=east,half=bottom]'
            cells[(ridge_x + d, y - d, z)] = f'{T}_stairs[facing=west,half=bottom]'
    return cells


class RidgeJogTest(unittest.TestCase):
    def test_woodland_plants_do_not_hide_a_roof_ridge(self):
        cells = gable({}, 10, 0, 8)
        for z, state in enumerate(('short_dry_grass', 'tall_dry_grass', 'bush',
                                   'leaf_litter[facing=east,segment_amount=4]')):
            cells[(10, 51, z)] = f'minecraft:{state}'
        self.assertEqual(ridge_jogs(cells), [])

    def test_straight_gable_is_clean(self):
        self.assertEqual(ridge_jogs(gable({}, 10, 0, 8)), [])

    def test_stepped_segments_jog(self):
        # broadlands_rd_56_62: one pair squared as segments whose ridge moves 2 east
        cells = gable(gable({}, 10, 0, 4), 12, 5, 9)
        jogs = ridge_jogs(cells)
        self.assertEqual(len(jogs), 1)
        self.assertEqual((jogs[0]['axis'], jogs[0]['shift']), ('x', -2))

    def test_party_wall_stagger_is_allowed(self):
        # terrace houses may stagger at a 2-wide brick parapet across the ridge
        cells = gable({}, 10, 0, 4)
        for d in (0, 1):
            cells[(10 + d, 50, 5)] = 'minecraft:bricks'
        for d in range(1, 5):
            cells[(10 - d, 50 - d, 5)] = f'{T}_stairs[facing=east,half=bottom]'
            cells[(11 + d, 50 - d, 5)] = f'{T}_stairs[facing=west,half=bottom]'
        gable(cells, 12, 6, 9)
        self.assertEqual(ridge_jogs(cells), [])

    def test_chimney_does_not_hide_a_jog(self):
        cells = gable(gable({}, 10, 0, 4), 12, 5, 9)
        cells[(10, 51, 4)] = 'minecraft:bricks'
        self.assertEqual(len(ridge_jogs(cells)), 1)

    def test_ridge_that_drifts_a_block_every_two_rows_is_a_rotated_roof_not_a_jog(self):
        cells = {}
        for z, ridge_x in enumerate((10, 10, 11, 11, 12, 12, 13, 13)):
            gable(cells, ridge_x, z, z)
        self.assertEqual(ridge_jogs(cells), [])

    def test_one_block_step_between_two_straight_runs_is_still_a_jog(self):
        cells = gable(gable({}, 10, 0, 4), 11, 5, 9)
        self.assertEqual(len(ridge_jogs(cells)), 1)

    def test_diagonal_crest_edge_stair_or_slab_is_not_a_jog(self):
        """West Building: one 3-wide crest at x 10..12 in both rows. The crest edge is a stair
        in one row and a corner slab in the next, so the stair ends sit a block apart."""
        up = f'{T}_stairs[facing=east,half=bottom]'
        down = f'{T}_stairs[facing=west,half=bottom]'
        slab = f'{T}_slab[type=bottom]'
        cells = {}
        for z, (left, right) in ((0, (up, slab)), (1, (slab, down))):
            cells[(10, 50, 0 + z)] = left
            cells[(11, 50, z)] = f'{T}s'
            cells[(12, 50, z)] = right
            for d in (1, 2):
                cells[(10 - d, 50 - d, z)] = up
                cells[(12 + d, 50 - d, z)] = down
        # the lone stair at crest height counts as the ridge's end in row 0 and the slab does in row 1
        self.assertEqual(ridge_jogs(cells), [])

    def test_ridge_along_x_is_checked_too(self):
        cells = {}
        for x, rz in ((0, 10), (1, 10), (2, 13), (3, 13)):
            cells[(x, 50, rz)] = f'{T}s'
            for d in range(1, 5):
                cells[(x, 50 - d, rz - d)] = f'{T}_stairs[facing=south,half=bottom]'
                cells[(x, 50 - d, rz + d)] = f'{T}_stairs[facing=north,half=bottom]'
        self.assertEqual([j['axis'] for j in ridge_jogs(cells)], ['z'])


class StairShapeFromNeighboursTest(unittest.TestCase):
    """Seam: vanilla same-Y neighbour rule (#169)."""

    def test_outer_right_from_front_neighbour(self):
        adj = {'east': ('south', 'bottom'), 'west': None, 'north': None, 'south': None}
        self.assertEqual(
            stair_shape_from_neighbours('east', 'bottom', adj), 'outer_right')

    def test_outer_left_from_front_neighbour(self):
        adj = {'east': ('north', 'bottom'), 'west': None, 'north': None, 'south': None}
        self.assertEqual(
            stair_shape_from_neighbours('east', 'bottom', adj), 'outer_left')

    def test_inner_right_from_behind_neighbour(self):
        adj = {'west': ('south', 'bottom'), 'east': None, 'north': None, 'south': None}
        self.assertEqual(
            stair_shape_from_neighbours('east', 'bottom', adj), 'inner_right')

    def test_inner_left_from_behind_neighbour(self):
        adj = {'west': ('north', 'bottom'), 'east': None, 'north': None, 'south': None}
        self.assertEqual(
            stair_shape_from_neighbours('east', 'bottom', adj), 'inner_left')

    def test_straight_run_and_non_stair_neighbours(self):
        adj = {'east': ('south', 'bottom'), 'west': ('south', 'bottom'),
               'north': None, 'south': None}
        self.assertEqual(
            stair_shape_from_neighbours('south', 'bottom', adj), 'straight')
        adj_slab = {'east': None, 'west': ('south', 'bottom'),
                    'north': None, 'south': None}
        self.assertEqual(
            stair_shape_from_neighbours('south', 'bottom', adj_slab), 'straight')

    def test_conflicting_check_side_forces_straight(self):
        # Behind faces south, but south neighbour faces the same way as us.
        adj = {'west': ('south', 'bottom'), 'south': ('east', 'bottom'),
               'east': None, 'north': None}
        self.assertEqual(
            stair_shape_from_neighbours('east', 'bottom', adj), 'straight')

    def test_different_half_is_ignored(self):
        adj = {'east': ('south', 'top'), 'west': None, 'north': None, 'south': None}
        self.assertEqual(
            stair_shape_from_neighbours('east', 'bottom', adj), 'straight')


class StairShapeMismatchLintTest(unittest.TestCase):
    def test_cli_reports_shape_mismatch_and_returns_failure(self):
        import roof_lint
        cells = {
            (0, 50, 0): f'{T}_stairs[facing=east,half=bottom,shape=straight]',
            (1, 50, 0): f'{T}_stairs[facing=south,half=bottom,shape=straight]',
        }
        output = io.StringIO()
        with mock.patch.object(sys, 'argv', ['roof_lint.py', 'unit-roof']), \
                mock.patch.object(roof_lint, '_load', return_value=cells), \
                redirect_stdout(output):
            self.assertEqual(roof_lint.main(), 1)
        self.assertIn('1 stair-shape mismatch(es)', output.getvalue())
        self.assertIn('expected=outer_right', output.getvalue())

    def test_matching_outer_plan_is_clean(self):
        cells = {
            (0, 50, 0): f'{T}_stairs[facing=east,half=bottom,shape=outer_right]',
            (1, 50, 0): f'{T}_stairs[facing=south,half=bottom,shape=straight]',
        }
        self.assertEqual(stair_shape_mismatches(cells), [])

    def test_wrong_planned_shape_is_reported(self):
        cells = {
            (0, 50, 0): f'{T}_stairs[facing=east,half=bottom,shape=straight]',
            (1, 50, 0): f'{T}_stairs[facing=south,half=bottom,shape=straight]',
        }
        bad = stair_shape_mismatches(cells)
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]['expected'], 'outer_right')
        self.assertEqual(bad[0]['planned'], 'straight')

    def test_straight_gable_plan_has_no_shape_mismatches(self):
        cells = gable({}, 10, 0, 4)
        # gable helper omits shape= → defaults to straight; same-Y neighbours face
        # the same way along each row, so expected is straight.
        self.assertEqual(stair_shape_mismatches(cells), [])


if __name__ == '__main__':
    unittest.main()
