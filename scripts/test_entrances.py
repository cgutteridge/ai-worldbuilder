"""Entrance behaviour against small building fixtures; no saved-world reads or writes."""
import unittest

from entrances import door, entrances
from site_kit import Plan, PlannedWorld, door_frame_issues

AIR = 'minecraft:air'


class World:
    def __init__(self, cells=None):
        self.cells = dict(cells or {})

    def block(self, x, y, z):
        return self.cells.get((x, y, z), 'minecraft:grass_block' if y == 48 else AIR)


def building():
    plan = Plan('building')
    cells = {(x, z) for x in range(9) for z in range(9)}
    for x, z in cells:
        if x in (0, 8) or z in (0, 8):
            for y in range(49, 55):
                plan.set(x, y, z, 'bricks')
    return plan, cells


class TestEntrancePlanning(unittest.TestCase):
    def test_original_building_plan_wins_over_previous_doors_in_all_directions(self):
        for face, point in [((1, 0), (8, 4)), ((-1, 0), (0, 4)), ((0, 1), (4, 8)), ((0, -1), (4, 0))]:
            with self.subTest(face=face):
                _, cells = building()
                first, _ = building()
                entrances(first, World(), cells, {'A': point}, [], double={'A'}, flat={'A'}, facing=face)
                previous = dict(first.cells)
                again, _ = building()
                entrances(again, World(previous), cells, {'A': point}, [], double={'A'}, flat={'A'}, facing=face)
                self.assertEqual(first.cells, again.cells)
                self.assertEqual(door_frame_issues(first, World().block), [])
                self.assertTrue(any('_door[' in b for b in first.cells.values()))
                self.assertTrue(all(y >= 48 and (x, z) in cells for x, y, z in first.cells))

    def test_fixed_projection_is_rebuilt_without_an_earlier_entrance(self):
        original, _ = building()
        p = Plan('entrance')
        door(p, World(original.cells), 13, 4, 1, 0, [], width='double', y0=49,
             frame='bricks', projection=5)
        self.assertEqual(p.cells[(11, 54, 4)], 'minecraft:bricks')
        self.assertEqual(door_frame_issues(p, World(original.cells).block), [])
        again = Plan('entrance')
        saved = dict(original.cells)
        saved.update(p.cells)
        door(again, World(saved), 13, 4, 1, 0, [], width='double', y0=49,
             frame='bricks', projection=5)
        self.assertEqual(p.cells, again.cells)
        self.assertTrue(all(y >= 48 for x, y, z in p.cells))

    def test_missing_projection_source_refuses_partial_wall(self):
        p = Plan('entrance')
        with self.assertRaisesRegex(ValueError, 'no source wall'):
            door(p, World(), 13, 4, 1, 0, [], y0=49, projection=5)
        self.assertEqual(p.cells, {})

    def test_explicit_air_and_snapshot_are_respected(self):
        p = Plan('clear')
        p.set(1, 50, 1, AIR)
        view = PlannedWorld(p, World({(1, 50, 1): 'minecraft:bricks'}))
        p.set(1, 50, 1, 'stone')
        self.assertEqual(view.block(1, 50, 1), AIR)

    def test_far_portal_is_skipped(self):
        p, cells = building()
        before = dict(p.cells)
        notes = []
        entrances(p, World(), cells, {'A': (100, 100)}, notes)
        self.assertEqual(p.cells, before)
        self.assertIn('skipped', notes[-1])


if __name__ == '__main__':
    unittest.main()
