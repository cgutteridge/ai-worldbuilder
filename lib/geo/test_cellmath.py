import unittest

import cellmath as C


class CellMathTest(unittest.TestCase):
    def test_parse_cell(self):
        self.assertEqual(C.parse_cell(' 48 , -44'), (48, -44))
        for bad in ('48', '1,2,3', 'a,b', ''):
            with self.assertRaises(SystemExit):
                C.parse_cell(bad)

    def test_cell_id_and_filename_token(self):
        self.assertEqual(C.cell_id((48, -44)), '48,-44')
        self.assertEqual(C.filename_cell_token((48, -44)), '48_-44')

    def test_cell_of_and_bounds_round_trip(self):
        self.assertEqual(C.cell_of(6152, -5592), (48, -44))
        self.assertEqual(C.cell_of(-1, -1), (-1, -1))
        x0, z0, x1, z1 = C.bounds((48, -44))
        self.assertEqual((x0, z0, x1, z1), (6144, -5632, 6271, -5505))
        self.assertEqual(C.cell_of(x1, z1), (48, -44))
        self.assertEqual(C.bounds((48, -44), 8), (6136, -5640, 6279, -5497))
        self.assertEqual(C.cell_bbox(48, -44, 48, -44), (6144, -5632, 6271, -5505))

    def test_ring_order_is_north_centre_then_clockwise(self):
        home = C.home_cell()
        ring = C.ring_cells(1)
        self.assertEqual(ring[0], (home[0], home[1] - 1))  # north centre
        self.assertEqual(ring[1], (home[0] + 1, home[1] - 1))  # then east
        self.assertEqual(len(ring), 8)
        self.assertEqual(len(set(ring)), 8)
        self.assertTrue(all(C.ring_distance(c) == 1 for c in ring))
        self.assertEqual(len(C.ring_cells(3)), 24)
        self.assertEqual(C.ring_cells(0), [home])

    def test_home_and_size_from_frame(self):
        self.assertEqual(C.cell_size(), 128)
        self.assertEqual(C.home_cell(), (48, -44))
        self.assertEqual(C.cell_shift(), 7)

    def test_reload_grid_from_dict(self):
        C.reload_grid({'cell': {'size': 64, 'home': [1, 2]}})
        try:
            self.assertEqual(C.cell_size(), 64)
            self.assertEqual(C.home_cell(), (1, 2))
            self.assertEqual(C.cell_of(64, 128), (1, 2))
        finally:
            C.reload_grid()  # restore active world

    def test_old_origin_key_rejected_even_when_home_is_present(self):
        for cell in ({'origin': [1, 2]}, {'origin': [1, 2], 'home': [3, 4]}):
            with self.assertRaisesRegex(ValueError, r'cell\.origin.*cell\.home'):
                C.reload_grid({'cell': cell})

    def test_invalid_home_rejected(self):
        with self.assertRaisesRegex(ValueError, r'cell\.home must be'):
            C.reload_grid({'cell': {'home': [1]}})

    def test_region_mapping(self):
        # Cell (48,-44) → blocks 6144..6271, -5632..-5505 → region (12, -11)
        regs = C.regions_for_cell((48, -44))
        self.assertEqual(regs, [(12, -11)])
        self.assertEqual(C.region_of_block(6144, -5632), (12, -11))

    def test_mc_tile_ids(self):
        self.assertEqual(C.mc_tile_for_cell(48, -44), '128:48:-44')
        self.assertEqual(C.mc_tile_id(6152, -5592), '128:48:-44')


if __name__ == '__main__':
    unittest.main()
