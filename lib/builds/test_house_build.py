import json
import tempfile
import unittest
from pathlib import Path

import house_build as H

class SpecTest(unittest.TestCase):
    def write(self, spec):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        f = Path(tmp.name) / 's.json'
        f.write_text(json.dumps(spec))
        return f

    def test_missing_key_and_unknown_label_are_refused(self):
        good = {'name': 'x', 'ways': {'1': 'A'}, 'roofs': {'A': 'brick'},
                'groups': [{'nos': ['A'], 'front': 'S'}]}
        spec = H.load_spec(self.write(good))
        self.assertEqual((spec['step_max'], spec['ledge_stair'], spec['keep_edits'], spec['floor_min']), (4, False, False, None))
        for bad in ({k: v for k, v in good.items() if k != 'roofs'},
                    {**good, 'groups': [{'nos': ['B'], 'front': 'S'}]},
                    {**good, 'groups': [{'nos': ['A'], 'front': 'X'}]}):
            with self.assertRaises(SystemExit):
                H.load_spec(self.write(bad))


class DoorFrameTest(unittest.TestCase):
    def build(self, walls, door_frame=True):
        b = H.Build.__new__(H.Build)
        b.spec = {'door_frame': door_frame}
        h = type('h', (), {})()
        h.walls = set(walls)
        b.owner = {c: 0 for c in walls}               # nothing stands in front of the wall
        return b, h

    def test_door_prefers_cell_with_wall_each_side(self):
        # front N: a step at x=2 means (2,0) has air to its west; (4,0) sits in a straight run
        walls = [(2, 0), (3, 0), (4, 0), (5, 0), (6, 0)]
        b, h = self.build(walls)
        self.assertEqual(sorted(b.with_jamb_walls(h, walls, 'N')), [(3, 0), (4, 0), (5, 0)])

    def test_notch_then_unchanged_fallback(self):
        walls = [(0, 0), (1, 0)]
        b, h = self.build(walls)
        self.assertEqual(b.with_jamb_walls(h, walls, 'N'), walls)    # no cell has both neighbours: keep the old pick
        b, h = self.build(walls, door_frame=False)
        self.assertEqual(b.with_jamb_walls(h, walls, 'N'), walls)


class ChimneyTest(unittest.TestCase):
    def test_chimney_has_brick_to_one_above_ridge_then_flower_pot(self):
        # Odd square footprints exercise both top- and bottom-slab ridges.
        for width, ridge in ((5, 37), (7, 38)):
            with self.subTest(width=width):
                group = {'nos': ['A'], 'mode': 'each', 'front': 'S'}
                cells = {(x, z) for x in range(width) for z in range(width)}
                house = H.House('A', cells, group)
                house.b = 30
                build = H.Build.__new__(H.Build)
                build.groups = [group]
                build.houses = [house]
                build.owner = {c: 0 for c in cells}
                build.foreign = set()
                build.shared = set()
                build.spec = {'roofs': {'A': 'brick'}}
                blocks = {}
                build.set = lambda x, y, z, block: blocks.__setitem__((x, y, z), block)

                build.roof()

                cx = cz = width // 2
                self.assertEqual([p for p, block in blocks.items() if block == 'flower_pot'],
                                 [(cx, ridge + 2, cz)])
                for y in range(36, ridge + 2):
                    self.assertEqual(blocks[cx, y, cz], H.WALL)
                self.assertNotIn((cx, ridge + 3, cz), blocks)
                adjacent_roof_y = 36 + (width // 2) // 2
                self.assertTrue(blocks[cx + 1, adjacent_roof_y, cz].startswith('brick_slab['))


if __name__ == '__main__':
    unittest.main()
