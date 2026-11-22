"""Entrance approach reconstruction from landed plan blocks (#240)."""
from __future__ import annotations

import unittest

import entrance_approach as ea

DOOR = 'spruce_door[facing=south,half=lower,hinge=left,open=false]'


def blocks_with_steps():
    b = {(10, 65, 10): DOOR, (10, 66, 10): DOOR.replace('lower', 'upper'),
         (10, 64, 11): 'smooth_stone', (10, 63, 12): 'smooth_stone', (10, 62, 13): 'smooth_stone'}
    return b


class EntrancesFromBlocksTest(unittest.TestCase):
    def test_door_and_stepping_approach_are_reconstructed(self):
        (a,) = ea.entrances_from_blocks(blocks_with_steps(), footprint={(10, 10), (10, 9)})
        self.assertEqual(a['door'], {'cell': [10, 10], 'origin': 'generated'})
        self.assertEqual(a['approach'], [[10, 11], [10, 12], [10, 13]])
        self.assertEqual(a['connection']['cell'], [10, 14])
        self.assertEqual(a['connection']['y'], 63)           # top of the last step + 1
        self.assertFalse(a['connection']['verified'])        # block data cannot prove observation

    def test_double_door_is_one_entrance(self):
        b = blocks_with_steps()
        b[(11, 65, 10)] = DOOR.replace('left', 'right')
        self.assertEqual(len(ea.entrances_from_blocks(b)), 1)

    def test_no_steps_connects_next_to_the_door(self):
        b = {(4, 65, 4): DOOR.replace('south', 'west')}
        (a,) = ea.entrances_from_blocks(b)
        self.assertEqual(a['approach'], [])
        self.assertEqual(a['connection']['cell'], [3, 4])

    def test_adjacent_separate_door_is_kept(self):
        b = {(10, 65, 10): DOOR, (11, 65, 10): DOOR.replace('south', 'north')}
        self.assertEqual(len(ea.entrances_from_blocks(b)), 2)

    def test_no_door_blocks_is_empty_not_guessed(self):
        self.assertEqual(ea.entrances_from_blocks({(1, 64, 1): 'bricks'}), [])

    def test_air_is_not_a_step(self):
        b = {(10, 65, 10): DOOR, (10, 64, 11): 'air'}
        (a,) = ea.entrances_from_blocks(b)
        self.assertEqual(a['approach'], [])


class EntranceApproachTest(unittest.TestCase):
    def test_no_approach_connects_at_doors_outward_neighbour(self):
        a = ea.entrance_approach((10, 10), 'S')
        self.assertEqual(a['connection']['cell'], [10, 11])
        self.assertEqual(a['door']['origin'], 'generated')
        self.assertFalse(a['connection']['verified'])

    def test_connection_is_outer_end_of_feature_steps(self):
        a = ea.entrance_approach((10, 10), 'S', approach_cells=[(10, 11), (10, 12)], base_y=64)
        self.assertEqual(a['connection']['cell'], [10, 13])
        self.assertEqual(a['connection']['y'], 64)
        self.assertEqual(a['approach'], [[10, 11], [10, 12]])
        self.assertEqual(a['clearance']['headroom'], 2)

    def test_updated_approach_moves_connection(self):
        short = ea.entrance_approach((4, 4), 'W', approach_cells=[(3, 4)])
        longer = ea.entrance_approach((4, 4), 'W', approach_cells=[(3, 4), (2, 4)])
        self.assertEqual((short['connection']['cell'], longer['connection']['cell']), ([2, 4], [1, 4]))

    def test_observed_door_is_verified(self):
        a = ea.entrance_approach((4, 4), 'N', generated=False)
        self.assertTrue(a['connection']['verified'])
        self.assertEqual(a['provenance']['source'], 'vector_map')

    def test_legacy_build_without_door_is_explicit(self):
        a = ea.entrance_approach(None, 'S')
        self.assertIsNone(a['connection'])
        self.assertIn('unavailable', a['reason'])


if __name__ == '__main__':
    unittest.main()
