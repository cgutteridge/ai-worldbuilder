"""Footprint overlap: synthetic footway through a polygon reports path cells."""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout

import footprint_overlap as FO


class PathInFootprintTest(unittest.TestCase):
    """Seam: a footway centreline through a rectangular footprint yields path cells."""

    def setUp(self):
        # 10×6 solid rectangle; centreline runs east–west through the middle row.
        self.footprint = {(x, z) for x in range(10, 20) for z in range(20, 26)}
        self.footway = (
            'footway',
            [(9.5, 22.5), (20.5, 22.5)],
            {'osm_id': 'syn-1', 'name': 'test footway'},
        )

    def test_synthetic_footway_reports_path_cells(self):
        rep = FO.check_footprint(self.footprint, [self.footway])
        self.assertGreater(len(rep.path_cells), 0, rep.lines())
        # Middle row of the rectangle should be hit (half-width 1.0 around z=22.5).
        mid = {(x, 22) for x in range(10, 20)} | {(x, 23) for x in range(10, 20)}
        self.assertTrue(rep.path_cells & mid, rep.path_cells)
        self.assertTrue(all(q in self.footprint for q in rep.path_cells))
        self.assertTrue(any(h['highway'] == 'footway' for hits in rep.path_hits.values() for h in hits))

    def test_cycleway_and_steps_also_count(self):
        for hw in ('cycleway', 'steps'):
            with self.subTest(hw=hw):
                street = (hw, [(9.5, 22.5), (20.5, 22.5)], {'osm_id': hw})
                rep = FO.check_footprint(self.footprint, [street])
                self.assertGreater(len(rep.path_cells), 0, hw)

    def test_road_does_not_count_as_path(self):
        street = ('residential', [(9.5, 22.5), (20.5, 22.5)], {'osm_id': 'r'})
        rep = FO.check_footprint(self.footprint, [street])
        self.assertEqual(rep.path_cells, set())

    def test_footway_outside_footprint_ignored(self):
        street = ('footway', [(9.5, 40.5), (20.5, 40.5)], {'osm_id': 'out'})
        rep = FO.check_footprint(self.footprint, [street])
        self.assertEqual(rep.path_cells, set())


class OpenLidarAndAerialTest(unittest.TestCase):
    """Seams: open lidar columns and aerial ground share over a footprint."""

    def setUp(self):
        self.footprint = {(x, 0) for x in range(5)}

    def test_open_lidar_below_threshold(self):
        feature = {(0, 0): 0.0, (1, 0): 0.5, (2, 0): 3.0, (3, 0): 12.0}
        open_cells = FO.open_lidar_cells_in_footprint(self.footprint, feature, open_max_m=1.0)
        self.assertEqual(open_cells, {(0, 0), (1, 0)})

    def test_aerial_ground_share(self):
        aerial = {
            (0, 0): 'lawn',
            (1, 0): 'light',
            (2, 0): 'shade',
            (3, 0): 'brick',
            # (4, 0) unclassified
        }
        got = FO.aerial_ground_share(self.footprint, aerial)
        self.assertEqual(got['classified'], 4)
        self.assertEqual(got['ground'], 3)  # lawn, light, brick — not shade
        self.assertAlmostEqual(got['share'], 0.75)

    def test_check_combines_all(self):
        street = ('footway', [(-0.5, 0.5), (4.5, 0.5)], {'osm_id': '1'})
        feature = {q: 0.0 for q in self.footprint}
        aerial = {q: 'lawn' for q in self.footprint}
        rep = FO.check_footprint(
            self.footprint, [street], feature_m=feature, aerial=aerial,
        )
        self.assertEqual(rep.footprint_n, 5)
        self.assertEqual(rep.path_cells, self.footprint)
        self.assertEqual(rep.open_lidar, self.footprint)
        self.assertAlmostEqual(rep.aerial['share'], 1.0)
        lines = rep.lines(label='demo')
        self.assertTrue(any('path' in ln and '5' in ln for ln in lines), lines)
        self.assertTrue(any('aerial ground share: 100%' in ln for ln in lines), lines)


class CliDryRunTest(unittest.TestCase):
    """Seam: CLI dry-run with synthetic cells + footway lists path cells as JSON."""

    def test_cli_cells_json_street(self):
        cells = [[x, 22] for x in range(10, 15)] + [[x, 23] for x in range(10, 15)]
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = FO.main([
                '--cells-json', json.dumps(cells),
                '--street', 'footway,9.5,22.5,15.5,22.5',
                '--json',
            ])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertGreater(data['path_n'], 0)
        self.assertTrue(all(tuple(c) in {tuple(p) for p in cells} for c in data['path_cells']))


if __name__ == '__main__':
    unittest.main()
