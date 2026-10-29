"""Tests for water-body plan vocabulary, provenance and build (#148)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import decision_record as dr
import provenance as prov
import water_body as W


class LoadSpecTest(unittest.TestCase):
    """Seam: load_spec validates outline, water level, bank/edge and provenance."""

    def write(self, spec: dict) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'pond.json'
        path.write_text(json.dumps(spec))
        return path

    def good(self) -> dict:
        return {
            'name': 'pond_demo',
            'outline': [[10, 20], [11, 20], [10, 21], [11, 21]],
            'water_level': 55,
            'bank': {'material': 'dirt', 'width': 1},
            'edge': {'material': 'cobblestone'},
            'provenance': {
                'source': 'vector_map',
                'confidence': 'high',
                'evidence_ids': [],
                'unresolved_reason': '',
            },
        }

    def test_loads_defaults_and_required_keys(self):
        spec = W.load_spec(self.write(self.good()))
        self.assertEqual(spec['name'], 'pond_demo')
        self.assertEqual(spec['water_level'], 55)
        self.assertEqual(spec['depth'], 1)
        self.assertEqual(spec['floor'], 'dirt')
        self.assertEqual(spec['modifies'], [])
        self.assertEqual(spec['bank'], {'material': 'dirt', 'width': 1, 'rise': 0})
        self.assertEqual(spec['edge'], {'material': 'cobblestone'})
        self.assertEqual(spec['provenance']['source'], 'vector_map')
        self.assertFalse(prov.is_unresolved(spec['provenance']))

    def test_missing_or_bad_fields_refused(self):
        good = self.good()
        cases = [
            {k: v for k, v in good.items() if k != 'name'},
            {k: v for k, v in good.items() if k != 'outline'},
            {k: v for k, v in good.items() if k != 'water_level'},
            {k: v for k, v in good.items() if k != 'bank'},
            {k: v for k, v in good.items() if k != 'provenance'},
            {**good, 'outline': []},
            {**good, 'outline': [[10]]},
            {**good, 'water_level': 55.5},
            {**good, 'depth': 0},
            {**good, 'bank': {'material': ''}},
            {**good, 'bank': 'dirt'},
            {**good, 'edge': {'material': ''}},
            {**good, 'provenance': {'source': 'guess', 'confidence': 'high',
                                   'evidence_ids': [], 'unresolved_reason': ''}},
        ]
        for bad in cases:
            with self.subTest(bad=bad):
                with self.assertRaises(SystemExit):
                    W.load_spec(self.write(bad))

    def test_edge_optional_default_none(self):
        base = {k: v for k, v in self.good().items() if k != 'edge'}
        spec = W.load_spec(self.write(base))
        self.assertIsNone(spec['edge'])

    def test_uses_shared_provenance_module_only(self):
        """#151: water body imports provenance and defines no private schema fields."""
        src = Path(W.__file__).read_text()
        self.assertIn('import provenance as prov', src)
        self.assertIn('prov.attach_provenance', src)
        self.assertIn('prov.provenance_record', src)
        for private in ('source_kind', 'confidence_score', 'evidence_refs'):
            self.assertNotIn(private, src)


class PlanItemTest(unittest.TestCase):
    """Seam: plan-item helper carries outline/level/bank/edge + shared provenance."""

    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.root = Path(self._d.name)
        dr.add_evidence('vmap1', type='vector_map', provider='p', title='Map', root=self.root)

    def tearDown(self):
        self._d.cleanup()

    def test_plan_item_shape_and_unresolved(self):
        item = W.water_body_plan_item(
            label='Library pond',
            outline=[(10, 20), (11, 20), (10, 21)],
            cell=(48, -45),
            refs=['w999'],
            water_level=None,
            bank=None,
            edge=None,
            source='vector_map',
            confidence='medium',
            evidence_ids=['vmap1'],
            unresolved_reason='water level and bank not yet read',
            root=self.root,
        )
        self.assertEqual(item['kind'], 'water_body')
        self.assertEqual(item['label'], 'Library pond')
        self.assertEqual(item['refs'], ['w999'])
        self.assertEqual(item['cells'], [[48, -45]])
        self.assertEqual(item['outline'], [[10, 20], [11, 20], [10, 21]])
        self.assertIsNone(item['water_level'])
        self.assertEqual(item['provenance']['source'], 'vector_map')
        self.assertTrue(prov.is_unresolved(item['provenance']))
        found = prov.unresolved_for_cell((48, -45), [item])
        self.assertEqual(found, [item])

    def test_resolved_item_has_level_bank_edge(self):
        item = W.water_body_plan_item(
            label='pond',
            outline=[(1, 1), (2, 1)],
            cell=(48, -45),
            refs=['w1'],
            water_level=40,
            bank={'material': 'dirt', 'width': 1},
            edge={'material': 'stone'},
            source='aerial',
            confidence='high',
            evidence_ids=['vmap1'],
            root=self.root,
        )
        self.assertEqual(item['water_level'], 40)
        self.assertEqual(item['bank']['material'], 'dirt')
        self.assertEqual(item['edge']['material'], 'stone')
        self.assertFalse(prov.is_unresolved(item['provenance']))


class ComponentsTest(unittest.TestCase):
    """Seam: connected water columns become water-body candidates."""

    def test_components_split_disconnected_ponds(self):
        cols = {(0, 0), (1, 0), (0, 1), (1, 1), (50, 50), (51, 50)}
        bodies = W.water_body_components(cols, cell=(48, -45), min_columns=2)
        self.assertEqual(len(bodies), 2)
        areas = sorted(b['area'] for b in bodies)
        self.assertEqual(areas, [2, 4])
        for b in bodies:
            self.assertEqual(b['kind'], 'water_body')
            self.assertEqual(b['cells'], [[48, -45]])
            self.assertTrue(b['outline'])
            self.assertIn('bbox', b)

    def test_tiny_slivers_dropped(self):
        cols = {(0, 0), (10, 10), (11, 10), (10, 11), (11, 11)}
        bodies = W.water_body_components(cols, cell=(48, -45), min_columns=4)
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0]['area'], 4)

    def test_column_refs_and_vector_map_provenance(self):
        """#163: column_refs become candidate refs; provenance source vector_map."""
        cols = {(10, 10), (11, 10), (10, 11), (11, 11)}
        refs = {c: 'w141657072' for c in cols}
        bodies = W.water_body_components(
            cols,
            cell=(48, -45),
            min_columns=4,
            column_refs=refs,
            provenance_source='vector_map',
        )
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0]['refs'], ['w141657072'])
        self.assertEqual(bodies[0]['provenance']['source'], 'vector_map')


class BuildTest(unittest.TestCase):
    """Seam: build places water at level, bank ring, and shoreline edge."""

    def spec(self, **overrides) -> dict:
        base = {
            'name': 'unit_pond',
            'outline': [[10, 20], [11, 20], [10, 21], [11, 21]],
            'water_level': 55,
            'depth': 2,
            'floor': 'dirt',
            'bank': {'material': 'coarse_dirt', 'width': 1},
            'edge': {'material': 'cobblestone'},
            'modifies': [],
            'provenance': {
                'source': 'vector_map',
                'confidence': 'high',
                'evidence_ids': [],
                'unresolved_reason': '',
            },
        }
        base.update(overrides)
        return W.load_spec_dict(base)

    def test_water_floor_bank_and_edge_placed(self):
        plan = W.build(self.spec())
        # water surface on outline
        for x, z in ((10, 20), (11, 20), (10, 21), (11, 21)):
            self.assertEqual(plan.get(x, 55, z), 'minecraft:water')
            self.assertEqual(plan.get(x, 54, z), 'minecraft:water')
            self.assertEqual(plan.get(x, 53, z), 'minecraft:dirt')
        # shoreline bank cell gets edge material at water_level
        self.assertEqual(plan.get(9, 55, 20), 'minecraft:cobblestone')
        self.assertEqual(plan.get(12, 55, 20), 'minecraft:cobblestone')
        # outer bank (diagonal corner of N8 that is not 4-adjacent? width-1 N8)
        # (9,19) is N8 neighbour of (10,20) — shoreline → edge
        self.assertEqual(plan.get(9, 55, 19), 'minecraft:cobblestone')
        # a bank cell only touching via width but wait — width 1 is N8 ring
        # all bank cells touch water via N8, so all get edge when edge is set
        bank_only = W.build(self.spec(edge=None))
        self.assertEqual(bank_only.get(9, 55, 20), 'minecraft:coarse_dirt')
        # outside the width-1 bank ring: untouched
        self.assertIsNone(bank_only.get(8, 55, 20))

    def test_sunken_pond_clears_above_and_raises_bank(self):
        plan = W.build(self.spec(clear_above=1, bank={'material': 'coarse_dirt', 'rise': 1}, edge=None))
        self.assertEqual(plan.get(10, 55, 20), 'minecraft:water')
        self.assertEqual(plan.get(10, 56, 20), 'minecraft:air')
        self.assertEqual(plan.get(9, 56, 20), 'minecraft:coarse_dirt')
        self.assertIsNone(plan.get(9, 55, 20))
        for bad in ({'clear_above': -1}, {'bank': {'material': 'x', 'rise': -1}}):
            with self.assertRaises(SystemExit):
                self.spec(**bad)

    def test_summary_counts_water(self):
        plan = W.build(self.spec())
        summary = plan.summary()
        self.assertEqual(summary['name'], 'unit_pond')
        self.assertGreater(summary['materials'].get('minecraft:water', 0), 0)
        self.assertIn('minecraft:cobblestone', summary['materials'])


class CandidateIdTest(unittest.TestCase):
    """Seam: water_body candidates get a stable plan id."""

    def test_candidate_plan_id_for_water_body(self):
        import cell_registry as CR
        item = {'kind': 'water_body', 'label': 'pond', 'refs': ['w555'], 'outline': [[1, 1]]}
        self.assertEqual(CR.candidate_plan_id(item), 'w555')
        with self.assertRaises(CR.RegistryError):
            CR.candidate_plan_id({'kind': 'water_body', 'label': 'x', 'refs': []})


if __name__ == '__main__':
    unittest.main()
