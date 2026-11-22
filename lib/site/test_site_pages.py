"""site_pages: media matching only links existing files; status classification; issue lookup (temp files only)."""
import tempfile
import unittest
from pathlib import Path

import site_pages as sp


class TestSitePages(unittest.TestCase):
    def test_find_media_links_only_existing_matching_images(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for n in ['cell_47_m45_ground-preview.png', 'cell_47_m45_ground-kinds.png', 'cmp-47_m45-strip.png',
                      'other.png', 'cell_marker_47_m45-preview.png', 'cell_47_m45_notes.txt', 'extra.png',
                      'cell_48_m44_ground-preview.png']:
                (d / n).write_bytes(b'x')
            sts = {'ground-layer': {'plan': 'cell_47_m45_ground'}}
            got = {f.name: g for g, f in sp.find_media((47, -45), sts, d, {'extra.png': ['47,-45']})}
            self.assertEqual(set(got), {'cell_47_m45_ground-preview.png', 'cell_47_m45_ground-kinds.png',
                                        'cmp-47_m45-strip.png', 'extra.png'})
            self.assertEqual(got['cell_47_m45_ground-kinds.png'], 'cell_47_m45_ground')
            self.assertEqual(got['cmp-47_m45-strip.png'], 'Comparisons and extras')
            self.assertEqual(sp.find_media((47, -45), sts, d / 'missing'), [])

    def test_classify(self):
        done = {n: {'status': 'applied'} for n in ('ground-layer', 'trees', 'decor')}
        # cell_gaps always sets footprint to a dict (even all zeros) — must still classify Completed.
        none = {'in_flight': [], 'layers': [], 'trees_stale': False, 'features': [],
                'unbuilt_footprint': [],
                'footprint': {'built_area': 0, 'unbuilt_area': 0, 'unbuilt': [], 'unbuilt_names': []}}
        self.assertEqual(sp.classify((1, 1), done, none, (9, 9), ''), 'Completed')
        self.assertEqual(sp.classify((1, 1), done, none, (1, 1), ''), 'In progress')
        self.assertEqual(sp.classify((1, 1), done, {**none, 'features': [1]}, None, ''), 'Built, items remaining')
        self.assertEqual(sp.classify((1, 1), {}, {**none, 'layers': ['decor']}, None, ''), 'Layers incomplete')
        self.assertEqual(sp.classify((1, 1), done, {**none, 'in_flight': ['x']}, None, ''), 'In progress')
        self.assertEqual(sp.classify((1, 1), done, none, None, 'task on 1,1'), 'In progress')

    def test_classify_completed_with_built_only_footprint(self):
        """#142: footprint summary dict is always truthy; zero unbuilt must still be Completed."""
        done = {n: {'status': 'applied'} for n in ('ground-layer', 'trees', 'decor')}
        gaps = {
            'in_flight': [], 'layers': [], 'trees_stale': False, 'features': [],
            'unbuilt_footprint': [],
            'footprint': {'built_area': 400, 'unbuilt_area': 0, 'unbuilt': [], 'unbuilt_names': []},
        }
        self.assertEqual(sp.classify((49, -45), done, gaps, None, ''), 'Completed')
        self.assertIn('400', sp.gap_text(gaps))
        self.assertIn('0 unbuilt', sp.gap_text(gaps))

    def test_classify_refuses_completed_when_unbuilt_footprint(self):
        """#142: empty layers/features still cannot read Completed while unbuilt footprint remains."""
        done = {n: {'status': 'applied'} for n in ('ground-layer', 'trees', 'decor')}
        gaps = {
            'in_flight': [], 'layers': [], 'trees_stale': False, 'features': [],
            'unbuilt_footprint': ['Fixture Hall'],
            'footprint': {'built_area': 100, 'unbuilt_area': 80, 'unbuilt_names': ['Fixture Hall']},
        }
        self.assertEqual(sp.classify((49, -45), done, gaps, None, ''), 'Built, items remaining')
        self.assertIn('Fixture Hall', sp.gap_text(gaps))
        self.assertIn('100', sp.footprint_text(gaps['footprint']))
        self.assertIn('80', sp.footprint_text(gaps['footprint']))

    def test_issue_lookup_matches_whole_cell_ids(self):
        with tempfile.TemporaryDirectory() as d:
            old = sp.ISSUES
            sp.ISSUES = Path(d) / 'ISSUES.txt'
            sp.ISSUES.write_text('I-001  M  A  open  mentions 47,-45 here\n  more\nI-002  M  B  open  cell 147,-45 not it\nI-003  M  C  open  cell 47,-455\n')
            try:
                self.assertEqual([t.split()[0] for t, _ in sp.issues_for((47, -45))], ['I-001'])
            finally:
                sp.ISSUES = old


if __name__ == '__main__':
    unittest.main()
