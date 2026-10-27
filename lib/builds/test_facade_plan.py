"""Tests for facade_plan: per-face plan vocabulary, entrances, portal coverage, provenance."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import facade_plan as FP
import feature_build as F
import provenance as prov


def _prov(
    *,
    source: str = 'register_entry',
    confidence: str = 'high',
    evidence_ids: list | None = None,
    unresolved_reason: str = '',
) -> dict:
    return prov.provenance_record(
        source=source,
        confidence=confidence,
        evidence_ids=evidence_ids or (),
        unresolved_reason=unresolved_reason,
    )


def _entrance(**overrides) -> dict:
    row = {
        'id': '57-A',
        'portal_id': '57-A',
        'face': 'S',
        'leaves': 1,
        'block': 'iron_door',
        'at': [10, 20],
        'canopy': {'kind': 'covered_area'},
        'steps': 0,
        'floor_offset': 0,
        'cell': [48, -44],
        'provenance': _prov(),
    }
    row.update(overrides)
    return row


class FacadePlanValidateTest(unittest.TestCase):
    """Seam: validate_facade_plan accepts the vocabulary and refuses bad rows."""

    def test_empty_plan_ok(self):
        FP.validate_facade_plan({}, labels={'A'}, storeys={'A': 2})

    def test_resolved_entrance_with_canopy_and_floor_offset(self):
        plan = {
            'A': {
                'faces': {
                    'S': {
                        'glazing': [{'storey': 1, 'glazed_h': 2, 'pier': 1}],
                    },
                },
                'entrances': [
                    _entrance(id='57-A', leaves=1, canopy={'kind': 'covered_area'}),
                    _entrance(
                        id='40-A',
                        portal_id='40-A',
                        face='E',
                        leaves=2,
                        block='oak_door',
                        canopy={'kind': 'none'},
                        floor_offset=0,
                    ),
                ],
            },
        }
        FP.validate_facade_plan(plan, labels={'A'}, storeys={'A': 2})
        placed = FP.placed_entrances(plan)
        self.assertEqual({e['id'] for e in placed}, {'57-A', '40-A'})
        self.assertEqual(next(e for e in placed if e['id'] == '40-A')['leaves'], 2)

    def test_unresolved_entrance_needs_reason_via_provenance(self):
        plan = {
            'A': {
                'faces': {},
                'entrances': [
                    {
                        'id': '99-Z',
                        'portal_id': '99-Z',
                        'cell': [48, -44],
                        'provenance': _prov(
                            source='register_entry',
                            confidence='low',
                            unresolved_reason='portal sits off the built footprint',
                        ),
                    },
                ],
            },
        }
        FP.validate_facade_plan(plan, labels={'A'}, storeys={'A': 1})
        unresolved = FP.unresolved_entrances(plan)
        self.assertEqual([e['id'] for e in unresolved], ['99-Z'])
        self.assertTrue(prov.is_unresolved(unresolved[0]['provenance']))

    def test_resolved_entrance_refuses_missing_leaves_block_face_or_provenance(self):
        labels, storeys = {'A'}, {'A': 1}
        base = _entrance()
        cases = [
            {k: v for k, v in base.items() if k != 'leaves'},
            {k: v for k, v in base.items() if k != 'block'},
            {k: v for k, v in base.items() if k != 'face'},
            {k: v for k, v in base.items() if k != 'provenance'},
            {**base, 'leaves': 3},
            {**base, 'block': 'iron_trapdoor'},
            {**base, 'face': 'X'},
            {**base, 'floor_offset': '0'},
            {**base, 'steps': -1},
            {**base, 'canopy': {'kind': 'awning'}},
            {
                **base,
                'provenance': {
                    'source': 'guess',
                    'confidence': 'high',
                    'evidence_ids': [],
                    'unresolved_reason': '',
                },
            },
        ]
        for bad in cases:
            with self.subTest(bad=bad):
                with self.assertRaises(FP.FacadePlanError):
                    FP.validate_facade_plan(
                        {'A': {'faces': {}, 'entrances': [bad]}},
                        labels=labels,
                        storeys=storeys,
                    )

    def test_unresolved_refuses_empty_reason(self):
        row = {
            'id': 'x',
            'provenance': _prov(unresolved_reason=''),
            # no leaves/face → treated as attempting unresolved, but reason empty
        }
        # Without leaves/block this is incomplete resolved, not a valid unresolved
        with self.assertRaises(FP.FacadePlanError):
            FP.validate_facade_plan(
                {'A': {'faces': {}, 'entrances': [row]}},
                labels={'A'},
                storeys={'A': 1},
            )

    def test_portal_ids_must_be_placed_or_unresolved(self):
        plan = {
            'A': {
                'faces': {},
                'entrances': [_entrance(id='57-A', portal_id='57-A')],
            },
        }
        FP.validate_facade_plan(
            plan, labels={'A'}, storeys={'A': 1}, portal_ids=['57-A'],
        )
        with self.assertRaises(FP.FacadePlanError) as ctx:
            FP.validate_facade_plan(
                plan, labels={'A'}, storeys={'A': 1}, portal_ids=['57-A', '42-B'],
            )
        self.assertIn('42-B', str(ctx.exception))

    def test_photo_inferred_entrance_without_portal_register(self):
        """Best effort: entrances from street photos need no portal_id."""
        plan = {
            'A': {
                'faces': {'N': {'glazing': []}},
                'entrances': [
                    _entrance(
                        id='photo-north-door',
                        portal_id=None,
                        face='N',
                        leaves=1,
                        canopy={'kind': 'none'},
                        provenance=_prov(source='photo_read', confidence='medium'),
                    ),
                ],
            },
        }
        # strip None portal_id
        plan['A']['entrances'][0].pop('portal_id', None)
        FP.validate_facade_plan(plan, labels={'A'}, storeys={'A': 1}, portal_ids=[])

    def test_glazing_row_matches_facades_shape(self):
        plan = {
            'A': {
                'faces': {
                    'N': {
                        'glazing': [
                            {'storey': 1, 'glazed_h': 3, 'pier': 1, 'rhythm': 'WW_BB'},
                        ],
                        'roof_edge': None,
                        'detail': [],
                    },
                },
                'entrances': [],
            },
        }
        FP.validate_facade_plan(plan, labels={'A'}, storeys={'A': 2})
        with self.assertRaises(FP.FacadePlanError):
            FP.validate_facade_plan(
                {
                    'A': {
                        'faces': {
                            'N': {'glazing': [{'storey': 9, 'glazed_h': 1, 'pier': 0}]},
                        },
                        'entrances': [],
                    },
                },
                labels={'A'},
                storeys={'A': 2},
            )

    def test_unknown_label_or_face_refused(self):
        with self.assertRaises(FP.FacadePlanError):
            FP.validate_facade_plan(
                {'B': {'faces': {}, 'entrances': []}},
                labels={'A'},
                storeys={'A': 1},
            )
        with self.assertRaises(FP.FacadePlanError):
            FP.validate_facade_plan(
                {'A': {'faces': {'X': {}}, 'entrances': []}},
                labels={'A'},
                storeys={'A': 1},
            )

    def test_unresolved_for_cell_uses_shared_provenance_helper(self):
        plan = {
            'A': {
                'faces': {},
                'entrances': [
                    {
                        'id': '99-Z',
                        'cell': [48, -44],
                        'provenance': _prov(
                            unresolved_reason='no wall near portal',
                            confidence='low',
                        ),
                    },
                    _entrance(id='57-A', cell=[48, -44]),
                ],
            },
        }
        FP.validate_facade_plan(plan, labels={'A'}, storeys={'A': 1})
        found = FP.unresolved_entrances_for_cell((48, -44), plan)
        self.assertEqual([e['id'] for e in found], ['99-Z'])
        self.assertEqual(FP.unresolved_entrances_for_cell((49, -44), plan), [])


class FacadePlanLoadSpecTest(unittest.TestCase):
    """Seam: feature_build.load_spec accepts facade_plan and defaults it."""

    def write(self, spec: dict) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 's.json'
        path.write_text(json.dumps(spec))
        return path

    def good(self) -> dict:
        return {
            'name': 'demo',
            'ways': {'1': 'A'},
            'storeys': {'A': 2},
            'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
            'groups': [{'nos': ['A'], 'front': 'N'}],
        }

    def test_facade_plan_default_empty_and_legacy_doors_refused(self):
        spec = F.load_spec(self.write(self.good()))
        self.assertEqual(spec['facade_plan'], {})
        self.assertNotIn('doors', spec)
        with self.assertRaises(SystemExit) as ctx:
            F.load_spec(self.write({
                **self.good(),
                'doors': {'42-A': {'leaves': 1, 'block': 'iron_door'}},
            }))
        self.assertIn('facade_plan', str(ctx.exception))

    def test_load_spec_accepts_facade_plan_entrances(self):
        plan = {
            'A': {
                'faces': {
                    'S': {'glazing': [{'storey': 1, 'glazed_h': 2, 'pier': 0}]},
                },
                'entrances': [
                    _entrance(id='42-B', portal_id='42-B', floor_offset=0, canopy={'kind': 'none'}),
                ],
            },
        }
        spec = F.load_spec(self.write({**self.good(), 'facade_plan': plan}))
        self.assertEqual(spec['facade_plan']['A']['entrances'][0]['id'], '42-B')

    def test_load_spec_refuses_bad_facade_plan(self):
        bad = {
            'A': {
                'faces': {},
                'entrances': [{'id': 'x', 'leaves': 1, 'block': 'iron_door', 'face': 'S'}],
            },
        }
        with self.assertRaises(SystemExit):
            F.load_spec(self.write({**self.good(), 'facade_plan': bad}))


class PortalBriefTest(unittest.TestCase):
    """Designer brief: every portal listed with position and photo when present."""

    def test_format_portal_inputs_lists_position_and_photo(self):
        portals = [
            {
                'id': '57-A',
                'label': 'Side entrance',
                'x': 100,
                'z': 200,
                'images': ['http://example/57-A.jpg'],
            },
            {
                'id': '40-A',
                'label': 'Main',
                'x': 110,
                'z': 210,
                'images': [],
            },
        ]
        lines = FP.format_portal_inputs(portals, photo_for=lambda p: f'photos/{p["id"]}.jpg')
        text = '\n'.join(lines)
        self.assertIn('57-A', text)
        self.assertIn('100,200', text)
        self.assertIn('photos/57-A.jpg', text)
        self.assertIn('40-A', text)
        self.assertIn('facade_plan', text)
        self.assertIn('unresolved', text.lower())
        self.assertNotIn('door_specs', text)
        self.assertNotIn('Put a door at each one', text)


class ProvenanceImportTest(unittest.TestCase):
    """#151: facade plan imports shared provenance; no private provenance fields."""

    def test_module_uses_shared_provenance_constants(self):
        self.assertIs(FP.PROVENANCE_KEY, prov.PROVENANCE_KEY)
        self.assertTrue(hasattr(FP, 'validate_facade_plan'))
        # Private field names must not appear on entrance schema helpers
        sample = _entrance()
        self.assertIn('provenance', sample)
        self.assertNotIn('source', sample)
        self.assertNotIn('confidence', sample)
        self.assertNotIn('unresolved_reason', sample)


if __name__ == '__main__':
    unittest.main()
