"""Block reference colour grouping (no extracted Minecraft assets required)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'shims'))
import block_reference as br  # noqa: E402

class TestGroups(unittest.TestCase):
    def test_hue_groups(self):
        self.assertEqual(br.hue_group((200, 30, 30)), 'red')
        self.assertEqual(br.hue_group((120, 120, 120)), 'grey-mid')
        self.assertEqual(br.hue_group((20, 20, 22)), 'grey-black')
        self.assertEqual(br.hue_group((60, 160, 60)), 'green')


if __name__ == '__main__':
    unittest.main()
