#!/usr/bin/env python3
"""Unit tests for the pure parts of scripts/coverage.py (no world / OSM access).

  python3 -m unittest scripts/test_coverage.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'shims'))
import coverage as C  # noqa: E402
from osm_footprints import _inside  # noqa: E402


class GeometryTest(unittest.TestCase):
    def test_runs_round_trip(self):
        cells = {(0, 0), (1, 0), (2, 0), (5, 0), (-3, -7), (-2, -7), (4, 2)}
        runs = C.encode_runs(cells)
        self.assertEqual(runs, [[-7, -3, -2], [0, 0, 2], [0, 5, 5], [2, 4, 4]])
        self.assertEqual(C.decode_runs(runs), cells)

    def test_scan_polygon_matches_point_in_polygon(self):
        ring = [(0.3, 0.2), (9.7, 1.1), (8.2, 7.9), (4.0, 4.5), (0.9, 8.6), (0.3, 0.2)]
        hole = [(2.0, 2.0), (4.0, 2.0), (4.0, 3.0), (2.0, 3.0), (2.0, 2.0)]
        rings = [ring, hole]
        got = set(C.scan_polygon(rings, (-5, -5, 20, 20)))
        want = {(x, z) for x in range(-5, 21) for z in range(-5, 21) if _inside(x + .5, z + .5, rings)}
        self.assertEqual(got, want)
        self.assertNotIn((2, 2), got)

    def test_scan_polygon_clips_to_bbox(self):
        sq = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
        self.assertEqual(set(C.scan_polygon([sq], (2, 3, 4, 4))), {(x, z) for x in (2, 3, 4) for z in (3, 4)})

    def test_line_band(self):
        got = {(x, z) for x, z, _d in C.line_band([(0.0, 0.5), (10.0, 0.5)], 1, (0, -5, 10, 5))}
        self.assertIn((5, -1), got)
        self.assertIn((5, 1), got)
        self.assertNotIn((5, 2), got)

    def test_cells(self):
        self.assertEqual(C.cell_of(127, -1), (0, -1))
        self.assertEqual(C.cell_of(-128, -129), (-1, -2))
        self.assertEqual(C.cell_bbox(55, -44, 55, -44), (7040, -5632, 7167, -5505))

    def test_components_split_and_order(self):
        cells = {(0, 0), (1, 0), (5, 5), (5, 6), (5, 7)}
        comps = C.components(cells)
        self.assertEqual([len(c) for c in comps], [3, 2])
        self.assertEqual(comps[1], {(0, 0), (1, 0)})


class SeamTest(unittest.TestCase):
    def test_narrow_strip_is_seam_wide_is_not(self):
        touched = {(x, z) for x in range(0, 3) for z in range(0, 5)}
        touched |= {(x, z) for x in range(5, 8) for z in range(0, 5)}       # 2-wide strip x=3,4
        touched |= {(x, z) for x in range(12, 15) for z in range(0, 5)}     # 4-wide strip x=8..11
        seams = C.find_seams((0, 0, 14, 4), touched, max_width=3)
        self.assertEqual(len(seams), 1)
        self.assertEqual(seams[0], {(x, z) for x in (3, 4) for z in range(0, 5)})

    def test_no_seam_at_open_edge(self):
        touched = {(x, 0) for x in range(0, 4)}
        self.assertEqual(C.find_seams((0, 0, 8, 0), touched), [])


class StateTest(unittest.TestCase):
    def test_done_at_full_coverage_without_signoff(self):
        """Ticket #31: sign-offs removed; 100% touched is done."""
        self.assertEqual(C.parcel_state(10, 10, True), 'done')
        self.assertEqual(C.parcel_state(10, 10, False), 'done')
        self.assertEqual(C.parcel_state(10, 9, True), 'touched')
        self.assertEqual(C.parcel_state(10, 0, False), 'untouched')

    def test_pct_never_rounds_up_to_full(self):
        self.assertEqual(C.pct(226, 227), '99.6%')
        self.assertEqual(C.pct(9999, 10000), '99.9%')
        self.assertEqual(C.pct(5, 5), '100%')
        self.assertEqual(C.pct(0, 5), '0%')


class SectionTest(unittest.TestCase):
    def test_replace_section(self):
        text = f'head\n{C.BEGIN}\nold\n{C.END}\ntail\n'
        out = C.replace_section(text, 'new 1\nnew 2')
        self.assertEqual(out, f'head\n{C.BEGIN}\nnew 1\nnew 2\n{C.END}\ntail\n')
        self.assertEqual(C.replace_section(out, 'x'), f'head\n{C.BEGIN}\nx\n{C.END}\ntail\n')

    def test_replace_section_needs_markers(self):
        with self.assertRaises(ValueError):
            C.replace_section('no markers here', 'x')


class SignoffStoreTest(unittest.TestCase):
    def test_signoffs_retired_always_empty(self):
        """Ticket #31: load_signoffs is empty; append_signoff is a no-op."""
        import tempfile
        old = C.SIGNOFFS
        with tempfile.TemporaryDirectory() as d:
            C.SIGNOFFS = Path(d) / 'signoffs.jsonl'
            try:
                C.append_signoff({'parcel': 'prop:w1', 'build': 'a', 'date': '2026-10-12'})
                active = C.load_signoffs()
                self.assertEqual(active, {})
                self.assertFalse(C.SIGNOFFS.exists())
            finally:
                C.SIGNOFFS = old

    def test_named_signoff_is_deprecated_noop(self):
        import argparse
        from unittest import mock
        args = argparse.Namespace(ids=['prop:w1'], build='b', date='2026-10-12', note=None,
                                  backfill=False, builds=None, dry_run=False, revoke=False,
                                  reason=None, limit=10)
        with mock.patch.object(C, 'append_signoff') as append, \
                mock.patch.object(C, 'write_coverage') as write:
            self.assertEqual(C.cmd_signoff(args), 0)
            append.assert_not_called()
            write.assert_not_called()


class RegistryRollupTest(unittest.TestCase):
    def test_registry_rollup_lists_journal_cells(self):
        """Ticket #33: COVERAGE can roll up from the cell journal alone."""
        from unittest import mock
        from journal_testkit import JournalWorld

        with JournalWorld() as jw:
            jw.seed((48, -44), ground=True, trees=True)
            with mock.patch.object(C, 'focus_cell', return_value=(48, -44)):
                rows = C.registry_rollup_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['cell'], (48, -44))
        self.assertEqual(rows[0]['ground'], 'generated')
        self.assertTrue(rows[0]['trees'])
        self.assertEqual(rows[0]['status'], 'partial')
        text = C.registry_rollup_text(rows)
        self.assertIn('48,-44', text)
        self.assertIn('registry rollup', text)
        self.assertIn('registry/journal.jsonl', text)


class ClassifyTest(unittest.TestCase):
    def test_area_kind(self):
        self.assertEqual(C.area_kind({'amenity': 'university'}), 'umbrella')
        self.assertEqual(C.area_kind({'landuse': 'residential'}), 'umbrella')
        self.assertEqual(C.area_kind({'landuse': 'grass'}), 'open')
        self.assertEqual(C.area_kind({'highway': 'pedestrian'}), 'path')
        self.assertIsNone(C.area_kind({'type': 'multipolygon'}))

    def test_grows_garden(self):
        self.assertTrue(C.grows_garden({'building': 'house'}))
        self.assertTrue(C.grows_garden({'building': 'yes', 'other_tags': '"addr:housenumber"=>"12"'}))
        self.assertFalse(C.grows_garden({'building': 'yes'}))
        self.assertFalse(C.grows_garden({'building': 'university', 'other_tags': '"addr:housenumber"=>"1"'}))
        self.assertFalse(C.grows_garden({'building': 'shed'}))


if __name__ == '__main__':
    unittest.main()
