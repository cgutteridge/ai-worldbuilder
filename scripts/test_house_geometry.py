import unittest

import house_geometry as hg


class Perimeter(unittest.TestCase):
    def test_solid_3x3_has_eight_perimeter_cells(self):
        cells = {(x, z) for x in range(3) for z in range(3)}
        self.assertEqual(hg.perimeter(cells), cells - {(1, 1)})

    def test_single_cell_is_all_perimeter(self):
        self.assertEqual(hg.perimeter({(5, 5)}), {(5, 5)})


class Facing(unittest.TestCase):
    def test_door_faces_dominant_axis(self):
        self.assertEqual(hg.door_facing((0, 1)), 'south')
        self.assertEqual(hg.door_facing((0, -1)), 'north')
        self.assertEqual(hg.door_facing((1, 0)), 'east')
        self.assertEqual(hg.door_facing((-1, 0)), 'west')

    def test_tie_goes_to_z_axis(self):
        self.assertEqual(hg.door_facing((0.7, 0.7)), 'south')

    def test_roof_stairs_ascend_away_from_street_on_front_slope(self):
        self.assertEqual(hg.stair_facing_for_roof(True, (0, 1)), 'north')
        self.assertEqual(hg.stair_facing_for_roof(False, (0, 1)), 'south')
        self.assertEqual(hg.stair_facing_for_roof(True, (1, 0)), 'west')
        self.assertEqual(hg.stair_facing_for_roof(False, (1, 0)), 'east')


class Scores(unittest.TestCase):
    cells = {(0, 0), (0, 3)}

    def test_depth_grows_away_from_the_street(self):
        # street to the north (outward = -z), front point at z=0: further south is deeper
        d = hg.depth_score(self.cells, (0, -1), (0.5, 0))
        self.assertLess(d[(0, 0)], d[(0, 3)])
        self.assertAlmostEqual(d[(0, 3)] - d[(0, 0)], 3)

    def test_along_is_signed_along_street_axis(self):
        a = hg.along_score({(0, 0), (4, 0)}, (1, 0), (0.5, 0))
        self.assertAlmostEqual(a[(0, 0)], 0)
        self.assertAlmostEqual(a[(4, 0)], 4)


if __name__ == '__main__':
    unittest.main()
