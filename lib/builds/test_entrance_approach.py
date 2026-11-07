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

    def test_no_door_blocks_is_empty_not_guessed(self):
        self.assertEqual(ea.entrances_from_blocks({(1, 64, 1): 'bricks'}), [])

    def test_air_is_not_a_step(self):
        b = {(10, 65, 10): DOOR, (10, 64, 11): 'air'}
        (a,) = ea.entrances_from_blocks(b)
        self.assertEqual(a['approach'], [])


if __name__ == '__main__':
    unittest.main()
