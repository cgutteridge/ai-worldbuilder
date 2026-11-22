"""Coverage views use a synthetic journal; retained map helpers use pure fixtures."""
import argparse
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import coverage as C
from osm_footprints import _inside


class GeometryTest(unittest.TestCase):
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


class SectionTest(unittest.TestCase):
    def test_replace_section(self):
        text = f'head\n{C.BEGIN}\nold\n{C.END}\ntail\n'
        out = C.replace_section(text, 'new 1\nnew 2')
        self.assertEqual(out, f'head\n{C.BEGIN}\nnew 1\nnew 2\n{C.END}\ntail\n')
        self.assertEqual(C.replace_section(out, 'x'), f'head\n{C.BEGIN}\nx\n{C.END}\ntail\n')

    def test_replace_section_needs_markers(self):
        with self.assertRaises(ValueError):
            C.replace_section('no markers here', 'x')


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


class RegistryCoverageTest(unittest.TestCase):
    def test_holds_survive_and_clear_in_registry_views(self):
        from cell_journal import Note
        from journal_testkit import JournalWorld

        with JournalWorld() as jw, mock.patch.object(C, 'focus_cell', return_value=(0, 0)):
            jw.seed((0, 0), ground=True, trees=True, decor=True)
            self.assertEqual(C.registry_rollup_rows()[0]['status'], 'done')
            jw.reg.change(Note((0, 0), 'hold', 'survey pending'))
            row = C.registry_rollup_rows()[0]
            self.assertEqual(row['status'], 'held')
            self.assertEqual(row['hold'], 'survey pending')
            self.assertEqual(C.load_holds()[(0, 0)]['registry']['reason'], 'survey pending')
            self.assertIn('hold=survey pending', C.registry_rollup_text([row]))
            jw.reg.change(Note((0, 0), 'hold', ''))
            self.assertEqual(C.load_holds(), {})
            self.assertEqual(C.registry_rollup_rows()[0]['status'], 'done')

    def test_write_creates_missing_site_and_preserves_notes(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'site' / 'COVERAGE.txt'
            with mock.patch.object(C, 'COVERAGE_TXT', path), \
                    mock.patch.object(C, 'registry_rollup_text', return_value='current registry'):
                self.assertEqual(C.write_coverage(), 'current registry')
                path.write_text(f'my focus note\n{C.BEGIN}\nold parcel table\n{C.END}\nmy tail\n')
                C.write_coverage()
                self.assertEqual(path.read_text(),
                                 f'my focus note\n{C.BEGIN}\ncurrent registry\n{C.END}\nmy tail\n')
                C.write_coverage()
                self.assertEqual(path.read_text().count(C.BEGIN), 1)

    def test_write_appends_generated_block_to_notes_without_markers(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'COVERAGE.txt'
            path.write_text('handwritten focus\n')
            with mock.patch.object(C, 'COVERAGE_TXT', path), \
                    mock.patch.object(C, 'registry_rollup_text', return_value='registry'):
                C.write_coverage()
                self.assertTrue(path.read_text().startswith('handwritten focus\n'))
                self.assertIn(f'{C.BEGIN}\nregistry\n{C.END}', path.read_text())

    def test_status_filters_cell_and_writes_full_rollup_only_when_requested(self):
        rows = [{'cell': (1, 2)}, {'cell': (-1, 0)}]
        with mock.patch.object(C, 'registry_rollup_rows', return_value=rows), \
                mock.patch.object(C, 'registry_rollup_text', return_value='status') as render, \
                mock.patch.object(C, 'write_coverage') as write, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(C.cmd_status(argparse.Namespace(cell=(-1, 0), write=False)), 0)
            render.assert_called_once_with([rows[1]])
            write.assert_not_called()
            C.cmd_status(argparse.Namespace(cell=None, write=True))
            write.assert_called_once_with()

    def test_help_lists_only_live_registry_commands(self):
        out = io.StringIO()
        with mock.patch('sys.argv', ['coverage.py', '--help']), contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as exit_:
                C.main()
        self.assertEqual(exit_.exception.code, 0)
        self.assertIn('{status,write}', out.getvalue())
        for retired in ('areas', 'build', 'gaps', 'signoff', 'map'):
            with self.subTest(command=retired), mock.patch('sys.argv', ['coverage.py', retired]), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exit_:
                    C.main()
                self.assertEqual(exit_.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
