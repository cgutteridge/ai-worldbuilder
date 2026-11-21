#!/usr/bin/env python3
"""A plan covers its map ref whatever its name (task 293): proposals, record, next, readiness, audit."""
from __future__ import annotations

import json
import unittest
from unittest import mock

import cell_registry as CR
import cellmath
import registry_next as N
from cell_journal import Note
from cell_readiness import ReadinessPolicy, planned_elsewhere
from journal_testkit import JournalWorld, RegistryEvidence
import plan_refs as PR

CELL = (48, -44)
NEIGHBOUR = (47, -44)


def building(ref, kind='building'):
    return {'kind': kind, 'label': f'Hall {ref}', 'street': None, 'refs': [ref],
            'bbox': (0, 0, 1, 1), 'portals': [], 'pictures': []}


class PlanRefsTest(unittest.TestCase):
    def setUp(self):
        self.jw = JournalWorld()
        self.jw.__enter__()
        self.addCleanup(self.jw.__exit__)
        self.root, self.reg = self.jw.root, self.jw.reg
        cellmath.reload_grid({'cell': {'size': 128, 'home': list(CELL)}})
        self.addCleanup(cellmath.reload_grid)
        clip = mock.patch('cell_features.clip_coverage', return_value=1.0)
        clip.start()
        self.addCleanup(clip.stop)
        self.policy = ReadinessPolicy(self.reg, RegistryEvidence())

    def spec(self, plan, ways=None):
        builds = self.root / 'site' / 'builds'
        builds.mkdir(parents=True, exist_ok=True)
        data = {'name': plan}
        if ways is not None:
            data['ways'] = ways
        (builds / f'{plan}.json').write_text(json.dumps(data))

    def plan(self, plan, cell=CELL):
        self.reg.change(Note(cell, 'plan_feature', plan, plan))

    # --- the helper ---------------------------------------------------------------------------
    def test_refs_on_the_spec_win_over_the_name(self):
        self.spec('west_w111', {'222': 'A'})
        self.assertEqual(PR.plan_refs(self.root, 'west_w111'), ({'w222'}, 'spec'))

    def test_name_is_the_fallback_when_no_refs_are_stored(self):
        self.assertEqual(PR.plan_refs(self.root, 'west_w34391673'), ({'w34391673'}, 'name'))
        self.assertEqual(PR.plan_refs(self.root, 'susu_r55'), ({'r55'}, 'name'))
        self.spec('west_w9')                                   # a spec with no ways says nothing
        self.assertEqual(PR.plan_refs(self.root, 'west_w9'), ({'w9'}, 'name'))

    def test_unrelated_ids_are_not_matched(self):
        covered = PR.covered_by_plans(self.root, ['west_w34391673', 'houses_kitchener_road', 'pond'])
        self.assertTrue(PR.candidate_covered(building('w34391673'), covered))
        self.assertFalse(PR.candidate_covered(building('w3439167'), covered))     # not a prefix match
        self.assertFalse(PR.candidate_covered(building('w4391673'), covered))     # not a suffix match
        self.assertFalse(PR.candidate_covered(building('w1'), covered))
        self.assertEqual(PR.plan_refs(self.root, 'houses_kitchener_road')[1], 'none')

    def test_house_groups_are_never_matched_by_ref(self):
        covered = PR.covered_by_plans(self.root, ['west_w5'])
        self.assertFalse(PR.candidate_covered(building('w5', 'houses'), covered))

    # --- call sites ---------------------------------------------------------------------------
    def test_propose_and_record_skip_a_candidate_covered_by_a_prefixed_plan(self):
        self.plan('west_w34391673')
        items = [building('w34391673'), building('w7')]
        self.assertEqual([i['refs'] for i in CR.drop_covered_candidates(self.reg, CELL, items)], [['w7']])
        self.assertEqual([r['id'] for r in CR.record_feature_candidates(self.reg, '48,-44', items)], ['w7'])
        self.assertNotIn('w34391673', {f.id for f in self.reg.cell(CELL).features})

    def test_spec_refs_cover_a_candidate_the_name_does_not_mention(self):
        self.plan('campus_main')
        self.spec('campus_main', {'10': 'A', '11': 'B'})
        self.assertEqual(CR.record_feature_candidates(self.reg, '48,-44', [building('w11')]), [])

    def test_spec_ways_cover_a_multi_ref_group(self):
        """#328: a plan named for the lowest ref, with all ways listed, covers the group."""
        group = {'kind': 'group', 'label': 'retail (2 buildings)', 'street': None,
                 'refs': ['w10', 'w30'], 'bbox': (0, 0, 1, 1), 'portals': [], 'pictures': []}
        self.plan('w10')
        self.spec('w10', {'10': 'A', '30': 'B'})
        self.assertEqual(CR.drop_covered_candidates(self.reg, CELL, [group]), [])
        self.assertEqual(CR.record_feature_candidates(self.reg, '48,-44', [group]), [])

    def test_partial_group_plan_leaves_uncovered_ref_proposable(self):
        """#328: a plan covering only one ref of a group must not drop the other ref."""
        group = {'kind': 'group', 'label': 'retail (2 buildings)', 'street': None,
                 'refs': ['w10', 'w30'], 'bbox': (0, 0, 1, 1), 'area': 2,
                 'portals': [], 'pictures': []}
        self.plan('w10')
        self.spec('w10', {'10': 'A'})                     # covers w10 only
        left = CR.drop_covered_candidates(self.reg, CELL, [group])
        self.assertEqual([(i['kind'], i['refs']) for i in left], [('building', ['w30'])])
        self.assertFalse(PR.candidate_covered(group, PR.covered_by_plans(self.root, ['w10'])))
        self.assertEqual(PR.uncovered_candidates(group, {'w10'}),
                         [{'kind': 'building', 'label': 'retail (2 buildings)', 'street': None,
                           'refs': ['w30'], 'bbox': (0, 0, 1, 1), 'area': 2,
                           'portals': [], 'pictures': []}])

    def test_group_planned_in_home_cell_is_not_reproposed_next_door(self):
        """#328 / #288: a multi-ref group planned once stays covered across the cell line."""
        group = {'kind': 'group', 'label': 'retail (2 buildings)', 'street': None,
                 'refs': ['w10', 'w30'], 'bbox': (0, 0, 1, 1), 'portals': [], 'pictures': []}
        self.plan('w10')
        self.spec('w10', {'10': 'A', '30': 'B'})
        cid = '47,-44'
        self.assertEqual(CR.record_feature_candidates(self.reg, cid, [group]), [])
        with mock.patch('cell_registry.propose_feature_candidates', return_value=[group]):
            self.assertEqual(N.unplanned_footprint_candidates(self.reg, cid), [])

    def test_next_and_readiness_do_not_offer_a_second_build(self):
        self.jw.seed(CELL, ground=True, features=('west_w34391673',))
        with mock.patch('cell_registry.propose_feature_candidates', return_value=[building('w34391673')]):
            self.assertEqual(N.unplanned_footprint_candidates(self.reg, '48,-44'), [])
            self.assertEqual(self.policy.check(CELL)['candidates'], [])

    def test_a_neighbour_does_not_re_propose_it_either(self):
        """#288's planned_elsewhere: a prefixed plan in the home cell covers the ref next door."""
        self.plan('west_w34391673')
        self.assertIn('w34391673', planned_elsewhere(self.reg, NEIGHBOUR))
        cid = '47,-44'
        self.assertEqual(CR.record_feature_candidates(self.reg, cid, [building('w34391673')]), [])
        with mock.patch('cell_registry.propose_feature_candidates', return_value=[building('w34391673')]):
            self.assertEqual(N.unplanned_footprint_candidates(self.reg, cid), [])

    def test_an_unrelated_plan_does_not_hide_a_candidate(self):
        self.plan('west_w111')
        self.assertEqual([r['id'] for r in CR.record_feature_candidates(self.reg, '48,-44', [building('w1112')])],
                         ['w1112'])

    # --- audit --------------------------------------------------------------------------------
    def test_audit_lists_a_duplicate_plan_note(self):
        self.jw.seed(CELL, features=('west_w34391673',))
        self.spec('west_w34391673', {'34391673': 'WB'})
        self.plan('w34391673')                                  # the second, literal plan note
        self.plan('w5', NEIGHBOUR)                              # unrelated, in another cell
        rows = CR.duplicate_plans(self.reg)
        self.assertEqual([(r['plan'], r['covered_by'], r['refs'], r['dismissed']) for r in rows],
                         [('w34391673', 'west_w34391673', ['w34391673'], False)])
        self.assertEqual(rows[0]['source'], 'name')
        self.assertEqual(rows[0]['cells'], ['48,-44'])
        text = CR.format_duplicate_plans(rows)
        self.assertIn('dismiss-feature 48,-44 --feature w34391673', text)
        self.assertIn('ref read from its name', text)

    def test_audit_flags_a_dismissed_duplicate_and_is_quiet_when_clean(self):
        self.jw.seed(CELL, features=('west_w34391673',))
        self.plan('w34391673')
        self.reg.change(Note(CELL, 'plan_feature-dismiss', 'duplicate', ref='w34391673'))
        rows = CR.duplicate_plans(self.reg)
        self.assertEqual([(r['plan'], r['dismissed']) for r in rows], [('w34391673', True)])
        self.assertNotIn('dismiss-feature', CR.format_duplicate_plans(rows))
        clean = JournalWorld()
        with clean:
            self.assertEqual(CR.format_duplicate_plans(CR.duplicate_plans(clean.reg)),
                             'no duplicate plans: every map ref has one plan')

    def test_two_plans_with_different_refs_are_not_duplicates(self):
        self.plan('west_w1')
        self.plan('susu_w2')
        self.assertEqual(CR.duplicate_plans(self.reg), [])


if __name__ == '__main__':
    unittest.main()
