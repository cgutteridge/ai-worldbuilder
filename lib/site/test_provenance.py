"""provenance: shared source/confidence/evidence/unresolved schema for evidence-read plan items."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import decision_record as dr
import provenance as prov


class TestProvenance(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.root = Path(self._d.name)
        dr.add_evidence('aerial1', type='aerial', provider='p', title='Aerial', root=self.root)
        dr.add_evidence('sv1', type='streetview', provider='p', title='Street', root=self.root)

    def tearDown(self):
        self._d.cleanup()

    def test_record_holds_source_confidence_evidence_ids_and_optional_unresolved(self):
        rec = prov.provenance_record(
            source='photo_read',
            confidence='high',
            evidence_ids=['sv1'],
            root=self.root,
        )
        self.assertEqual(rec, {
            'source': 'photo_read',
            'confidence': 'high',
            'evidence_ids': ['sv1'],
            'unresolved_reason': '',
        })
        unresolved = prov.provenance_record(
            source='inference',
            confidence='low',
            evidence_ids=['aerial1'],
            unresolved_reason='door visible but leaf count unclear',
            root=self.root,
        )
        self.assertEqual(unresolved['unresolved_reason'], 'door visible but leaf count unclear')
        self.assertTrue(prov.is_unresolved(unresolved))
        self.assertFalse(prov.is_unresolved(rec))

    def test_rejects_unknown_source_confidence_and_evidence_ids(self):
        with self.assertRaises(prov.ProvenanceError):
            prov.provenance_record(source='guess', confidence='high', root=self.root)
        with self.assertRaises(prov.ProvenanceError):
            prov.provenance_record(source='aerial', confidence='maybe', root=self.root)
        with self.assertRaises(prov.ProvenanceError):
            prov.provenance_record(source='aerial', confidence='high', evidence_ids=['missing'], root=self.root)

    def test_attach_provenance_puts_record_on_plan_item(self):
        item = {'id': 'entrance-north', 'face': 'N'}
        out = prov.attach_provenance(
            item,
            source='register_entry',
            confidence='medium',
            evidence_ids=['sv1'],
            root=self.root,
        )
        self.assertIs(out, item)
        self.assertEqual(item['provenance']['source'], 'register_entry')
        self.assertEqual(item['provenance']['evidence_ids'], ['sv1'])

    def test_unresolved_for_cell_lists_items_touching_that_cell(self):
        a = prov.attach_provenance(
            {'id': 'hedge-a', 'cells': [[48, -44], [48, -45]]},
            source='aerial',
            confidence='medium',
            evidence_ids=['aerial1'],
            unresolved_reason='outline crosses tree canopy',
            root=self.root,
        )
        b = prov.attach_provenance(
            {'id': 'bench-b', 'cell': [48, -44]},
            source='photo_read',
            confidence='high',
            evidence_ids=['sv1'],
            root=self.root,
        )
        c = prov.attach_provenance(
            {'id': 'pond-c', 'cells': [[49, -44]]},
            source='vector_map',
            confidence='high',
            evidence_ids=['aerial1'],
            unresolved_reason='no bank height in lidar',
            root=self.root,
        )
        found = prov.unresolved_for_cell((48, -44), [a, b, c])
        self.assertEqual([i['id'] for i in found], ['hedge-a'])
        self.assertEqual([i['id'] for i in prov.unresolved_for_cell((49, -44), [a, b, c])], ['pond-c'])
        self.assertEqual(prov.unresolved_for_cell((50, -44), [a, b, c]), [])


if __name__ == '__main__':
    unittest.main()
