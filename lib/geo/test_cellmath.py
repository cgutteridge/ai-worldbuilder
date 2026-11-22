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
        self.assertEqual(C.cell_of(127, -1), (0, -1))
        self.assertEqual(C.cell_of(-128, -129), (-1, -2))
        self.assertEqual(C.cell_bbox(55, -44, 55, -44), (7040, -5632, 7167, -5505))
        self.assertEqual(C.cell_bbox(0, 0, 1, 1), (0, 0, 255, 255))
        # bounds(size=...) and cell_of(size=...) take an explicit edge
        self.assertEqual(C.bounds((1, -1), size=64), (64, -64, 127, -1))
        self.assertEqual(C.cell_of(64, -1, size=64), (1, -1))

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


def _legacy_components_8(cells):
    """The removed 8-connected finders (garden_sources, ground_layer, water_body): ``set.pop`` seeds."""
    left, out = set(cells), []
    while left:
        seed = left.pop()
        comp, todo = {seed}, [seed]
        while todo:
            x, z = todo.pop()
            for a in (-1, 0, 1):
                for b in (-1, 0, 1):
                    n = (x + a, z + b)
                    if n in left:
                        left.remove(n)
                        comp.add(n)
                        todo.append(n)
        out.append(comp)
    return out


def _legacy_components_4(cells):
    """The removed 4-connected finder (roof_aerial)."""
    left, out = set(cells), []
    while left:
        seed = left.pop()
        comp, todo = {seed}, [seed]
        while todo:
            x, z = todo.pop()
            for n in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
                if n in left:
                    left.remove(n)
                    comp.add(n)
                    todo.append(n)
        out.append(comp)
    return out


class ComponentsTest(unittest.TestCase):
    def _samples(self):
        import random
        rng = random.Random(374)
        yield {(0, 0), (1, 0), (5, 5), (5, 6), (5, 7)}
        yield {(0, 0), (1, 1), (2, 2), (9, 9)}             # diagonal chain: one 8-component, three 4-components
        yield set()
        for _ in range(40):
            yield {(rng.randrange(-6, 7), rng.randrange(-6, 7)) for _ in range(rng.randrange(1, 45))}

    @staticmethod
    def _as_sets(comps):
        return sorted(sorted(c) for c in comps)

    def test_direction_tables(self):
        self.assertEqual(C.N8, tuple((a, b) for a in (-1, 0, 1) for b in (-1, 0, 1) if a or b))
        self.assertEqual(set(C.N4) | {(a, b) for a in (-1, 1) for b in (-1, 1)}, set(C.N8))
        self.assertEqual({C.OPP[k] for k in C.DIRS}, set(C.DIRS))
        for k, (dx, dz) in C.DIRS.items():
            self.assertEqual(C.DIRS[C.OPP[k]], (-dx, -dz))

    def test_split_and_order(self):
        cells = {(0, 0), (1, 0), (5, 5), (5, 6), (5, 7)}
        comps = C.components(cells)
        self.assertEqual([len(c) for c in comps], [3, 2])
        self.assertEqual(comps[1], {(0, 0), (1, 0)})

    def test_four_connected_default_matches_removed_roof_aerial_copy(self):
        for cells in self._samples():
            self.assertEqual(self._as_sets(C.components(cells)), self._as_sets(_legacy_components_4(cells)))
        self.assertEqual(len(C.components({(0, 0), (1, 1)})), 2)

    def test_eight_connected_matches_removed_garden_ground_water_copies(self):
        for cells in self._samples():
            self.assertEqual(self._as_sets(C.components(cells, C.N8)), self._as_sets(_legacy_components_8(cells)))
        self.assertEqual(len(C.components({(0, 0), (1, 1)}, C.N8)), 1)

    def test_order_is_stable_whatever_the_input_order(self):
        cells = [(3, 3), (0, 0), (1, 0), (2, 0), (9, 9), (3, 4)]
        self.assertEqual(C.components(cells, C.N8), C.components(reversed(cells), C.N8))


if __name__ == '__main__':
    unittest.main()
