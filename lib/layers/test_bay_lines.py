"""Unit tests for car-park bay lines (#336): geometry, the tarmac gate, and the ground pass end to end."""
import math
import unittest
from types import SimpleNamespace

import bay_lines as bl
from ground_sources import PRI
from test_ground_layer import SyntheticGroundBox


def box(x0, z0, x1, z1) -> set:
    return {(x, z) for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)}


class TestBayLineGeometry(unittest.TestCase):
    AREA = box(0, 0, 30, 20)
    EAST_WEST = {'points': [(0.0, 10.0), (31.0, 10.0)], 'half': 2.5}

    def test_rows_stand_perpendicular_to_an_east_west_aisle(self):
        lines = bl.bay_line_columns([self.AREA], [self.EAST_WEST])
        xs = sorted({x for x, _ in lines})
        self.assertEqual(xs, list(range(2, 31, 3)))                  # a line every 3 blocks along the aisle
        for x in xs:                                                 # each line runs straight across the aisle (z only)
            self.assertEqual(sorted(z for lx, z in lines if lx == x), [2, 3, 4, 5, 6, 13, 14, 15, 16, 17])

    def test_rows_stand_perpendicular_to_a_north_south_aisle(self):
        aisle = {'points': [(15.0, 0.0), (15.0, 21.0)], 'half': 2.5}
        lines = bl.bay_line_columns([self.AREA], [aisle])
        zs = sorted({z for _, z in lines})
        self.assertEqual(zs, list(range(2, 21, 3)))
        for z in zs:
            self.assertEqual(sorted(x for x, lz in lines if lz == z), list(range(7, 12)) + list(range(18, 23)))

    def test_bays_are_five_deep_and_never_touch_the_aisle(self):
        lines = bl.bay_line_columns([self.AREA], [self.EAST_WEST])
        offsets = {abs(z + .5 - 10.0) for _, z in lines}
        self.assertEqual(min(offsets), 3.5)                          # first column past the 2.5 half width
        self.assertEqual(max(offsets), 7.5)                          # half width + 5
        self.assertFalse({(x, z) for x, z in lines if 8 <= z <= 11})   # aisle columns stay clear

    def test_diagonal_aisle_lines_keep_the_pitch_along_it(self):
        a, b = (2.0, 2.0), (28.0, 28.0)
        aisle = {'points': [a, b], 'half': 2.5}
        lines = bl.bay_line_columns([self.AREA], [aisle])
        self.assertTrue(lines)
        ux = uz = 1 / math.sqrt(2)
        for x, z in lines:
            along = (x + .5 - a[0]) * ux + (z + .5 - a[1]) * uz
            nearest = round(along / bl.BAY_PITCH) * bl.BAY_PITCH
            self.assertLessEqual(abs(along - nearest), 0.5)

    def test_lines_stay_inside_the_car_park_polygon(self):
        area = box(10, 5, 20, 15)
        lines = bl.bay_line_columns([area], [{'points': [(10.0, 10.0), (21.0, 10.0)], 'half': 2.5}])
        self.assertTrue(lines)
        self.assertTrue(lines <= area)
        self.assertIn((12, 13), lines)
        self.assertNotIn((12, 17), lines)                            # the 5-deep row runs past the polygon edge (z 15)

    def test_no_mapped_aisle_means_no_lines(self):
        self.assertEqual(bl.bay_line_columns([self.AREA], []), set())

    def test_aisle_outside_the_polygon_marks_nothing(self):
        far = {'points': [(0.0, 60.0), (31.0, 60.0)], 'half': 2.5}
        self.assertEqual(bl.bay_line_columns([self.AREA], [far]), set())

    def test_two_neighbouring_aisles_each_own_their_own_rows(self):
        area = box(0, 0, 30, 40)
        n = {'points': [(0.0, 10.0), (31.0, 10.0)], 'half': 2.5}
        s = {'points': [(0.0, 30.0), (31.0, 30.0)], 'half': 2.5}
        lines = bl.bay_line_columns([area], [n, s])
        self.assertEqual(lines, bl.bay_line_columns([area], [n]) | bl.bay_line_columns([area], [s]))

    def test_an_aisle_crossing_the_cell_edge_keeps_its_lines_in_the_small_part(self):
        half_area = box(0, 0, 30, 20)                                # the cell's clipped slice of a larger car park
        long_aisle = {'points': [(-60.0, 10.0), (31.0, 10.0)], 'half': 2.5}
        short_aisle = {'points': [(-20.0, 10.0), (31.0, 10.0)], 'half': 2.5}
        whole = box(-80, 0, 30, 20)                                  # the unclipped car park
        for aisle, expect in ((long_aisle, range(2, 30, 3)), (short_aisle, range(0, 31, 3))):
            lines = bl.bay_line_columns([half_area], [aisle])
            self.assertEqual(sorted({x for x, _ in lines}), list(expect))
            clipped = {q for q in bl.bay_line_columns([whole], [aisle]) if q in half_area}
            self.assertEqual(lines, clipped)                         # same lines as the unclipped car park draws here

    def test_a_bend_gives_every_column_to_its_nearest_segment(self):
        pts = [(0.0, 10.0), (15.0, 10.0), (15.0, 25.0)]
        half, area = 2.5, box(0, 0, 30, 30)
        lines = bl.bay_line_columns([area], [{'points': pts, 'half': half}])
        self.assertTrue(lines)
        segs = bl._segments([{'points': pts, 'half': half}])
        for q in lines:
            dists = [bl._frame(s, *q)[2] for s in segs]
            owner = segs[dists.index(min(dists))]
            along, off, _ = bl._frame(owner, *q)
            self.assertTrue(0.0 <= along <= owner[3], q)             # beside the nearest segment, not past its end
            self.assertTrue(half < off <= half + bl.BAY_DEPTH, q)
            self.assertLessEqual(abs(along - round(along / bl.BAY_PITCH) * bl.BAY_PITCH), 0.5, q)
        # the first leg runs east-west (lines vary in z at a fixed x); the second north-south (vary in x at a fixed z)
        inner = {(x, z) for x, z in lines if x < 15 and z > 10 or x > 15 and z < 10}
        self.assertFalse(inner and {q for q in inner if _crosses(lines, q)})
        self.assertFalse({q for q in lines if _crosses(lines, q)})

    def test_the_outside_of_a_bend_gets_no_wedge_lines(self):
        bend = {'points': [(0.0, 10.0), (15.0, 10.0), (15.0, 25.0)], 'half': 2.5}
        lines = bl.bay_line_columns([box(0, 0, 30, 30)], [bend])
        self.assertFalse({(x, z) for x, z in lines if x > 17 and z < 8})   # the outside-corner wedge stays clear


def _crosses(lines, q) -> bool:
    """True when ``q`` has line neighbours on both axes (a '+' where two orientations meet)."""
    x, z = q
    return (((x - 1, z) in lines or (x + 1, z) in lines) and ((x, z - 1) in lines or (x, z + 1) in lines))


class TestMarkCarParkBays(unittest.TestCase):
    def vmap(self, area, **kw):
        pri = dict.fromkeys(area, PRI['parking'])
        return SimpleNamespace(parking_areas=[area], parking_aisles=[TestBayLineGeometry.EAST_WEST], pri=pri, **kw)

    def test_only_tarmac_columns_on_the_car_park_are_marked(self):
        area = box(0, 0, 30, 20)
        vmap = self.vmap(area)
        kind = dict.fromkeys(area, 'tarmac')
        lawn = {(x, z) for x, z in area if x < 10}                    # aerial shows lawn there
        kind.update(dict.fromkeys(lawn, 'grass'))
        for q in box(0, 13, 30, 13):                                   # a mapped path crosses one row
            vmap.pri[q] = PRI['path']
        n = bl.mark_car_park_bays(kind, vmap)
        marked = {q for q, k in kind.items() if k == 'mark'}
        self.assertEqual(n, len(marked))
        self.assertTrue(marked)
        self.assertFalse(marked & lawn)
        self.assertFalse({q for q in marked if q[1] == 13})
        self.assertFalse({q for q in kind if kind[q] == 'grass'} - lawn)

    def test_evidence_without_car_parks_marks_nothing(self):
        kind = {(0, 0): 'tarmac'}
        self.assertEqual(bl.mark_car_park_bays(kind, SimpleNamespace(pri={})), 0)
        self.assertEqual(kind, {(0, 0): 'tarmac'})


class TestGroundPassBayLines(SyntheticGroundBox, unittest.TestCase):
    """The ground pass writes the lines itself (exempt from the #327 gate), as white_concrete on tarmac."""

    AISLE = {'points': [(0.0, 4.0), (16.0, 4.0)], 'half': 1.0}

    def car_park(self, soft=False):
        osm = self.Osm()
        x0, z0, x1, z1 = self.BOX
        area = box(x0, z0, x1, z1)
        osm.kind, osm.pri = dict.fromkeys(area, 'tarmac'), dict.fromkeys(area, PRI['parking'])
        for q in box(0, 3, 15, 4):                                    # the aisle itself is mapped service tarmac
            osm.pri[q] = PRI['service']
        osm.parking_areas, osm.parking_aisles = [area], [self.AISLE]
        osm.soft = set(area) if soft else set()
        return osm

    def test_bays_are_white_concrete_perpendicular_to_the_aisle(self):
        plan, stats, kind, _ = self.run_plan(self.car_park())
        marks = {q for q, k in kind.items() if k == 'mark'}
        self.assertEqual(stats['how: car park bay line (perpendicular to the mapped aisle)'], len(marks))
        self.assertEqual({x for x, _ in marks}, {2, 5, 8, 11, 14})   # one line per 3 blocks along the aisle (x)
        self.assertFalse({q for q in marks if q[1] in (3, 4)})        # clear of the aisle
        for x, z in marks:
            self.assertEqual(plan.cells[(x, self.lidar(x, z), z)], 'minecraft:white_concrete')
        self.assertEqual(plan.cells[(3, self.lidar(3, 4), 4)], 'minecraft:gray_concrete')   # aisle stays tarmac
        self.assertEqual(plan.cells[(3, self.lidar(3, 6), 6)], 'minecraft:gray_concrete')   # between two lines
        self.assertFalse(plan.ground_layer)                           # ground pass's own plan: exempt, not Plan.ground_marking

    def test_a_mapped_car_park_the_mosaic_reads_as_lawn_still_gets_lines(self):
        """#338: the car park stays tarmac against lawn-coloured mosaic, so bay lines draw on it."""
        plan, stats, kind, _ = self.run_plan(self.car_park(soft=True))
        marks = {q for q, k in kind.items() if k == 'mark'}
        self.assertEqual({x for x, _ in marks}, {2, 5, 8, 11, 14})
        self.assertNotIn('grass', {kind[q] for q in box(0, 0, 15, 7)})
        self.assertIn('minecraft:white_concrete', plan.cells.values())

if __name__ == '__main__':
    unittest.main()
