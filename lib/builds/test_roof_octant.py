import unittest

import roof_octant as R
from roof_lint import ridge_jogs, stair_shape_mismatches


def rect(width, depth, x0=10, z0=20):
    return {(x, z) for x in range(x0, x0 + width) for z in range(z0, z0 + depth)}


def ring(own):
    return own | {(x + dx, z + dz) for x, z in own for dx in (-1, 0, 1) for dz in (-1, 0, 1)}


class PatternTest(unittest.TestCase):
    def test_stair_cell_counts_follow_the_shape(self):
        for facing in R.DIRS:
            with self.subTest(facing=facing):
                self.assertEqual(len(R.stair_cells(facing, 'straight')), 6)
                for shape in ('outer_left', 'outer_right'):
                    self.assertEqual(len(R.stair_cells(facing, shape)), 5)
                for shape in ('inner_left', 'inner_right'):
                    self.assertEqual(len(R.stair_cells(facing, shape)), 7)
                self.assertLess(R.stair_cells(facing, 'outer_left'), R.stair_cells(facing, 'straight'))
                self.assertLess(R.stair_cells(facing, 'straight'), R.stair_cells(facing, 'inner_left'))

    def test_a_stair_is_a_slab_plus_the_tall_side(self):
        cells = R.stair_cells('north', 'straight')
        self.assertTrue(R.BOTTOM <= cells)
        self.assertEqual({(dx, dz) for dx, dy, dz in cells if dy == 1}, {(0, 0), (1, 0)})   # north half
        self.assertEqual({(dx, dz) for dx, dy, dz in R.stair_cells('east', 'straight') if dy == 1}, {(1, 0), (1, 1)})


class FitTest(unittest.TestCase):
    def fit(self, own, pitch=0.75, **kw):
        return R.fit_octant_roof(own, ring(own), pitch, 36, **kw)

    def test_an_axis_aligned_rectangle_gets_hip_faces_ridge_and_a_slab_or_stair_eave(self):
        own = rect(9, 6)
        fitted = self.fit(own, pitch=1)
        self.assertIsNotNone(fitted)
        self.assertLess(fitted.missed / fitted.wanted, 0.12)
        stairs = {b[1] for b in fitted.blocks.values() if b[0] == 'stair'}
        self.assertEqual(stairs, {'north', 'south', 'east', 'west'})
        for (x, y, z), block in fitted.blocks.items():
            if (x, z) not in own:
                self.assertEqual(y, 36)                     # the overhanging ring is one block thick
                self.assertIn(block[0], ('slab', 'stair'))

    def test_stair_shapes_agree_with_the_neighbour_rule_and_material_is_irrelevant(self):
        from test_roof_geometry import rotated_rect
        for degrees in (0, 25, 40, 70):
            with self.subTest(degrees=degrees):
                own = rotated_rect(14, 8, degrees)
                fitted = self.fit(own)
                for material in ('brick', 'stone_brick'):
                    cells = {pos: 'minecraft:' + state for pos, state in R.block_states(material, fitted.blocks).items()}
                    self.assertEqual(stair_shape_mismatches(cells), [])
                self.assertLess(fitted.missed / fitted.wanted, 0.2)

    def test_the_fit_is_deterministic(self):
        from test_roof_geometry import rotated_rect
        own = rotated_rect(12, 9, 33)
        self.assertEqual(self.fit(own).blocks, self.fit(own).blocks)

    def test_footprints_that_are_not_rectangles_are_refused(self):
        ell = {(x, z) for x in range(10) for z in range(10) if x < 5 or z < 5}
        self.assertIsNone(self.fit(ell))

    def test_a_chimney_column_stays_solid(self):
        own = rect(9, 6)
        fitted = self.fit(own, full_columns={(14, 22)})
        column = {y: b for (x, y, z), b in fitted.blocks.items() if (x, z) == (14, 22)}
        self.assertTrue(column)
        self.assertTrue(all(b == ('full',) for b in column.values()))

    def test_block_states_use_explicit_shapes_and_the_family_names(self):
        states = R.block_states('brick', {(0, 36, 0): ('full',), (1, 36, 0): ('slab', 'bottom'),
                                          (2, 36, 0): ('stair', 'north', 'outer_left')})
        self.assertEqual(states[0, 36, 0], 'bricks')
        self.assertEqual(states[1, 36, 0], 'brick_slab[type=bottom,waterlogged=false]')
        self.assertEqual(states[2, 36, 0],
                         'brick_stairs[facing=north,half=bottom,shape=outer_left,waterlogged=false]')


if __name__ == '__main__':
    unittest.main()
