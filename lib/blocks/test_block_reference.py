"""Block reference colour grouping (no extracted Minecraft assets required)."""
from __future__ import annotations

import unittest

import block_reference as br

class TestGroups(unittest.TestCase):
    def test_woodland_plants_are_partial_but_solid_mushrooms_are_not(self):
        for plant in ('short_dry_grass', 'tall_dry_grass', 'leaf_litter', 'bush',
                      'firefly_bush', 'wildflowers', 'pink_petals', 'closed_eyeblossom'):
            with self.subTest(plant=plant):
                self.assertTrue(br.is_partial(f'minecraft:{plant}'))
        self.assertFalse(br.is_partial('brown_mushroom_block'))
        self.assertFalse(br.is_partial('grass_block'))

    def test_hue_groups(self):
        self.assertEqual(br.hue_group((200, 30, 30)), 'red')
        self.assertEqual(br.hue_group((120, 120, 120)), 'grey-mid')
        self.assertEqual(br.hue_group((20, 20, 22)), 'grey-black')
        self.assertEqual(br.hue_group((60, 160, 60)), 'green')


if __name__ == '__main__':
    unittest.main()
