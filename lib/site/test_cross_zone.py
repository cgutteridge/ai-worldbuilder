"""Cell-boundary reports need no parcel register or selected world."""
import unittest

from cross_zone import (columns_outside_cell, cross_zone_report, expanded_cell_bbox,
                        format_report, neighbor_cells)


class TestCrossZone(unittest.TestCase):
    def test_neighbor_cells(self):
        neighbors = neighbor_cells(0, 0)
        self.assertEqual(len(neighbors), 9)
        self.assertEqual(neighbors, sorted(neighbors))
        self.assertIn((0, 0), neighbors)
        self.assertIn((-1, -1), neighbors)

    def test_expanded_bbox(self):
        x0, z0, x1, z1 = expanded_cell_bbox(0, 0, 8)
        base = expanded_cell_bbox(0, 0)
        self.assertEqual((x0, z0, x1, z1),
                         (base[0] - 8, base[1] - 8, base[2] + 8, base[3] + 8))

    def test_outside_columns_and_deferrals_respect_negative_cells(self):
        x0, z0, x1, z1 = expanded_cell_bbox(0, 0)
        columns = {(x0, z0), (x1, z1), (x0 - 1, z0), (x1 + 1, z1)}
        self.assertEqual(columns_outside_cell(columns, 0, 0),
                         {(x0 - 1, z0), (x1 + 1, z1)})
        report = cross_zone_report(0, 0, margin=8, plan_columns=columns)
        self.assertEqual(report['expanded_bbox'], list(expanded_cell_bbox(0, 0, 8)))
        self.assertEqual(report['neighbors'], neighbor_cells(0, 0))
        self.assertEqual(report['warnings'], ['plan writes 2 columns outside cell 0,0'])
        self.assertEqual(report['deferrals'], ['foliage straddle: 1 cols → cell -1,0',
                                               'foliage straddle: 1 cols → cell 1,0'])
        self.assertIn('warn: plan writes 2 columns', format_report(report))
        self.assertNotIn('zones=', format_report(report))

    def test_report_with_no_plan_works_without_area_configuration(self):
        report = cross_zone_report(0, 0)
        self.assertEqual(report['warnings'], [])
        self.assertEqual(report['deferrals'], [])
        self.assertIn('(no cross-zone warnings)', format_report(report))


if __name__ == '__main__':
    unittest.main()
