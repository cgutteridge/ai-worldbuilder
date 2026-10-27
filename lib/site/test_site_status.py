"""site_status: parsing, layer aliases, the run log and the morning report (temp files only)."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import site_status as ss


class TestSiteStatus(unittest.TestCase):
    def setUp(self):
        from journal_testkit import JournalWorld
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.saved = (ss.CELLS, ss.MARKER, ss.LOCK, ss.RUN_LOG)
        ss.CELLS, ss.MARKER, ss.LOCK, ss.RUN_LOG = d / 'cells', d / 'cur.json', d / 'LOCK.json', d / 'RUN-LOG.md'
        self.jw = JournalWorld(d)
        self.jw.__enter__()
        self.jw.seed((47, -45), marker=True)

    def tearDown(self):
        self.jw.__exit__()
        ss.CELLS, ss.MARKER, ss.LOCK, ss.RUN_LOG = self.saved
        self.tmp.cleanup()

    def test_stamps_and_rings(self):
        self.assertEqual(ss.parse_stamp('2026-10-15 19:03:18'), datetime(2026, 10, 15, 19, 3, 18))
        self.assertEqual(ss.parse_stamp('2026-10-15 19:03'), datetime(2026, 10, 15, 19, 3))
        self.assertEqual(ss.parse_stamp('2026-10-15T19:03:18Z'), datetime(2026, 10, 15, 19, 3, 18))
        self.assertIsNone(ss.parse_stamp('never'))
        self.assertEqual([ss.ring(c) for c in [(48, -44), (47, -45), (50, -42), (51, -41)]], [0, 1, 2, 3])

    def test_layer_aliases_pick_the_latest_and_verified_counts(self):
        sts = {'ground': {'status': 'applied', 'readback': {'checked': '2026-10-14 10:00'}},
               'ground-layer': {'status': 'applied-with-mismatches', 'readback': {'checked': '2026-10-15 10:00'}},
               'trees': {'status': 'verified'}, 'decor': {'status': 'undone'}}
        self.assertEqual(ss.layer_status(sts, 'ground'), 'applied-with-mismatches')
        self.assertEqual(ss.layer_marks(sts), 'ground ⚠ trees ✓ decor –')
        self.assertFalse(ss.layers_applied(sts))
        sts['ground-layer'] = {'status': 'applied', 'readback': {'checked': '2026-10-16 10:00'}}
        sts['decor'] = {'status': 'applied'}
        self.assertTrue(ss.layers_applied(sts))

    def test_journal_entries_become_stage_records(self):
        self.jw.seed((47, -45), ground=True, trees=True, features=('feature_x',))
        st = ss.load_stages()[(47, -45)]
        self.assertEqual(st['ground-layer']['status'], 'applied')
        self.assertEqual(st['feature-feature_x']['blocks'], 1)
        self.assertEqual(len(ss.events(ss.load_stages())), 3)
        self.assertEqual(ss.events(ss.load_stages(), datetime(2999, 1, 1)), [])

    def test_status_flags_problems_and_marker(self):
        from cell_journal import Build
        self.jw.seed((47, -45), ground=True, trees=True, decor=True)
        self.jw.reg.begin(Build('regrade', 'ground', {(6016, 61, -5760): 'minecraft:stone'}, cell=(47, -45)))
        out = ss.cmd_status(ss.load_stages())
        self.assertIn('MARKER (where work is): 47,-45', out)
        self.assertIn('▶  47,-45', out)
        self.assertIn('ground !', out)
        self.assertIn('⚠ ground-layer', out)

    def test_log_then_morning_lists_eye_items(self):
        self.jw.seed((47, -45), ground=True, trees=True, decor=True)
        ss.main(['log', '--cell', '47,-45', '--did', 'plaza lawn applied', '--look', '6139,-5742: oak',
                 '--problem', 'strip under Building 2 still paved'])
        text = ss.RUN_LOG.read_text()
        self.assertIn('— cell 47,-45', text)
        self.assertIn('Layers now: ground ✓ trees ✓ decor ✓  (all applied)', text)
        out = ss.cmd_morning(ss.load_stages(), datetime(2000, 1, 1))
        self.assertIn('NEEDS YOUR EYE:', out)
        self.assertIn('6139,-5742: oak', out)
        self.assertIn('strip under Building 2 still paved', out)
        future = ss.cmd_morning(ss.load_stages(), datetime(2999, 1, 1))
        self.assertIn('no run-log entries', future)

    def test_format_footprint_shows_built_unbuilt_and_names(self):
        """#142: one-line footprint summary for status rows and the morning report."""
        fp = {
            'built_area': 400,
            'unbuilt_area': 250,
            'unbuilt_names': ['Fixture Hall', 'West Block'],
        }
        line = ss.format_footprint(fp)
        self.assertIn('400 built', line)
        self.assertIn('250 unbuilt', line)
        self.assertIn('Fixture Hall', line)
        self.assertIn('West Block', line)
        self.assertEqual(ss.format_footprint({'built_area': 10, 'unbuilt_area': 0, 'unbuilt_names': []}),
                         '10 built / 0 unbuilt')

    def test_status_and_morning_show_footprint_per_cell(self):
        """#142: each cell row and morning report list built vs unbuilt footprint."""
        self.jw.seed((47, -45), ground=True, trees=True, decor=True)
        fp = {
            'built_area': 120,
            'unbuilt_area': 80,
            'unbuilt_names': ['Fixture Hall'],
            'unbuilt': [{'label': 'Fixture Hall', 'area': 80}],
        }
        with mock.patch('site_status.cell_footprint', return_value=fp):
            status = ss.cmd_status(ss.load_stages())
            morning = ss.cmd_morning(ss.load_stages(), datetime(2000, 1, 1))
        self.assertIn('120 built', status)
        self.assertIn('80 unbuilt', status)
        self.assertIn('Fixture Hall', status)
        self.assertIn('FOOTPRINT', morning)
        self.assertIn('47,-45', morning)
        self.assertIn('Fixture Hall', morning)


if __name__ == '__main__':
    unittest.main()
