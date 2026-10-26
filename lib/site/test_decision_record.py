"""decision_record: evidence stored once and shared; phases validate; pages link cells, elements and evidence."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import decision_record as dr
import site_pages as sp


class TestDecisionRecord(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.root = Path(self._d.name)
        for ref in ('aerial1', 'lidar1', 'sv1'):
            dr.add_evidence(ref, type='aerial', provider='p', title=f'T {ref}', root=self.root)

    def tearDown(self):
        self._d.cleanup()

    def add(self, el, cells, **kw):
        return dr.add_phase(el, 'roof form', kind='feature', cells=cells, available=['aerial1', 'lidar1'],
                            consulted=[('aerial1', 'ridge axis')], decision='gable', root=self.root, now='2026-10-27T00:00:00Z', **kw)

    def test_consulted_is_added_to_available_and_unknown_refs_rejected(self):
        rec = dr.add_phase('f1', 'p', kind='feature', cells=[(1, 1)], consulted=['sv1'], decision='d', root=self.root)
        self.assertEqual(rec['phases'][0]['available'], ['sv1'])
        with self.assertRaises(dr.RecordError):
            dr.add_phase('f1', 'p', kind='feature', cells=[(1, 1)], available=['nope'], decision='d', root=self.root)
        with self.assertRaises(dr.RecordError):
            dr.add_phase('../x', 'p', kind='feature', cells=[(1, 1)], decision='d', root=self.root)
        with self.assertRaises(dr.RecordError):
            dr.add_phase('f1', 'p', kind='ground', cells=[(1, 1)], decision='d', root=self.root)

    def test_one_evidence_record_serves_several_cells(self):
        self.add('f1', [(1, 1)])
        self.add('f2', [(2, 1), (2, 2)])
        users = dr.evidence_users(dr.load_decisions(self.root))
        self.assertEqual(sorted(users['aerial1']['elements']), ['f1', 'f2'])
        self.assertEqual(sorted(users['aerial1']['cells']), [(1, 1), (2, 1), (2, 2)])
        self.assertEqual([r['element'] for r in dr.cell_elements((2, 2), dr.load_decisions(self.root))], ['f2'])

    def test_missing_records_start_with_first_record(self):
        e = lambda plan, ts: SimpleNamespace(plan=plan, status='landed', undone=False, ts=ts)
        entries = [e('old_ground', '2026-10-01T00:00:00Z'), e('new_trees', '2026-10-28T00:00:00Z'),
                   e('f1', '2026-10-28T00:00:00Z')]
        self.assertEqual(dr.missing_records(entries, {}), [])
        self.add('f1', [(1, 1)])
        self.assertEqual(dr.missing_records(entries, dr.load_decisions(self.root)), ['new_trees'])

    def test_pages_link_cell_element_and_evidence(self):
        self.add('f1', [(1, 1)])
        decisions, evidence = dr.load_decisions(self.root), dr.load_evidence(self.root)
        el = sp.element_page(decisions['f1'], evidence)
        self.assertIn('evidence-aerial1.html', el)
        self.assertIn('cell-1_1.html', el)
        self.assertIn('ridge axis', el)
        self.assertIn('available, not used', el)
        ev = sp.evidence_page('aerial1', evidence['aerial1'], dr.evidence_users(decisions))
        self.assertIn('element-f1.html', ev)
        self.assertIn('element-f1.html', sp.elements_html(dr.cell_elements((1, 1), decisions)))
        self.assertIn('No decision record', sp.elements_html([]))


if __name__ == '__main__':
    unittest.main()
