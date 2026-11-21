#!/usr/bin/env python3
"""Plan ids for distinct building groups of one street, and the cell completion gate."""
from __future__ import annotations

import json
import unittest
from unittest import mock

import cell_registry as CR
import cellmath
import registry_next as N
import registry_verify as V
from cell_journal import Note
from cell_readiness import ReadinessPolicy
from journal_testkit import RegistryEvidence

CELL = (48, -44)
STREET = 'houses_kitchener_road'


def group(refs, street='Kitchener Road'):
    return {'kind': 'houses', 'label': f'{street} ({len(refs)} houses)', 'street': street,
            'refs': list(refs), 'bbox': (0, 0, 1, 1), 'portals': [], 'pictures': []}


OLD = group(['w1', 'w2'])            # already planned and built as houses_kitchener_road
GROUP_A = group(['w10', 'w11', 'w12'])
GROUP_B = group(['w20', 'w21'])


class PlanIdTest(unittest.TestCase):
    def setUp(self):
        from journal_testkit import JournalWorld
        self.jw = JournalWorld()
        self.jw.__enter__()
        self.addCleanup(self.jw.__exit__)
        self.root = self.jw.root
        cellmath.reload_grid({'cell': {'size': 128, 'home': list(CELL)}})
        self.addCleanup(cellmath.reload_grid)
        clip = mock.patch('cell_features.clip_coverage', return_value=1.0)
        clip.start()
        self.addCleanup(clip.stop)
        self.reg = self.jw.reg
        self.policy = ReadinessPolicy(self.reg, RegistryEvidence())
        patch = mock.patch('readiness_evidence.policy_for', lambda reg: ReadinessPolicy(reg, self.policy.evidence))
        patch.start()
        self.addCleanup(patch.stop)

    def built_street(self):
        """The street plan built earlier, with a hand-off spec naming its two houses."""
        self.jw.seed(CELL, features=(STREET,))
        self.jw.seed(CELL, ground=True, trees=True, decor=True)   # ground after the feature: not stale (#285)
        builds = self.root / 'site' / 'builds'
        builds.mkdir(parents=True)
        (builds / f'{STREET}.json').write_text(json.dumps({'name': STREET, 'ways': {'1': 'K1', '2': 'K2'}}))

    def ids(self, items):
        return CR.plan_ids_for_candidates(self.reg, CELL, items)

    def record(self, items):
        return [r['id'] for r in CR.record_feature_candidates(self.reg, '48,-44', items)]

    def test_new_groups_of_a_planned_street_get_their_own_ids(self):
        self.built_street()
        self.assertEqual(self.ids([GROUP_A, GROUP_B]), [f'{STREET}_w10', f'{STREET}_w20'])
        self.assertEqual(self.record([GROUP_A, GROUP_B]), [f'{STREET}_w10', f'{STREET}_w20'])

    def test_rerun_is_idempotent(self):
        self.built_street()
        self.record([GROUP_A, GROUP_B])
        self.assertEqual(self.record([GROUP_A, GROUP_B]), [])
        with mock.patch('cell_registry.propose_feature_candidates', return_value=[GROUP_A, GROUP_B]):
            self.assertEqual(N.unplanned_footprint_candidates(self.reg, '48,-44'), [])

    def test_feature_planned_in_its_home_cell_is_not_planned_again_next_door(self):
        """Task 288: the neighbour's record and next skip a map-ref feature planned elsewhere."""
        pond = {'kind': 'water_body', 'label': 'pond (13 columns)', 'refs': ['w147601601'],
                'bbox': (0, 0, 1, 1), 'portals': [], 'pictures': []}
        self.reg.change(Note(CELL, 'plan_feature', 'pond (67 columns)', 'w147601601'))
        neighbour = (CELL[0] - 1, CELL[1])
        cid = f'{neighbour[0]},{neighbour[1]}'
        self.assertEqual(CR.record_feature_candidates(self.reg, cid, [pond, GROUP_A]),
                         [{'id': STREET, 'label': GROUP_A['label'], 'kind': 'houses'}])
        self.assertEqual([f.id for f in self.reg.cell(neighbour).features], [STREET])
        with mock.patch('cell_registry.propose_feature_candidates', return_value=[pond]):
            self.assertEqual(N.unplanned_footprint_candidates(self.reg, cid), [])

    def test_dismissed_group_stays_dismissed(self):
        self.built_street()
        self.record([GROUP_A, GROUP_B])
        self.reg.change(Note(CELL, 'plan_feature-dismiss', 'not houses', ref=f'{STREET}_w10'))
        self.assertEqual(self.record([GROUP_A, GROUP_B]), [])
        self.assertNotIn(f'{STREET}_w10', {f.id for f in self.reg.cell(CELL).features})

    def test_candidate_covered_by_the_existing_plan_is_skipped(self):
        self.built_street()
        self.assertEqual(self.ids([OLD]), [STREET])
        self.assertEqual(self.record([OLD]), [])

    def test_existing_ids_keep_meaning(self):
        """A sole free street group still takes the bare street id; a street with no spec stays covered."""
        self.assertEqual(self.ids([GROUP_A]), [STREET])
        self.jw.seed(CELL, ground=True, features=(STREET,))   # planned, no spec to say otherwise
        self.assertEqual(self.ids([GROUP_A]), [STREET])
        self.assertEqual(self.record([GROUP_A]), [])

    def test_two_groups_of_a_fresh_street_do_not_collide(self):
        self.assertEqual(self.ids([GROUP_A, GROUP_B]), [f'{STREET}_w10', f'{STREET}_w20'])
        self.assertEqual(len(self.record([GROUP_A, GROUP_B])), 2)

    def test_other_candidates_keep_their_ids(self):
        building = {'kind': 'building', 'label': 'Hall', 'street': None, 'refs': ['w99'],
                    'bbox': (0, 0, 1, 1), 'portals': [], 'pictures': []}
        self.assertEqual(self.ids([building]), ['w99'])

    def test_group_plan_id_is_the_lowest_ref(self):
        """#328: a touching non-house group keys by min(refs), not refs[0] as packaged."""
        group = {'kind': 'group', 'label': 'retail (2 buildings)', 'street': None,
                 'refs': ['w30', 'w10'], 'bbox': (0, 0, 1, 1), 'portals': [], 'pictures': []}
        self.assertEqual(CR.candidate_plan_id(group), 'w10')
        self.assertEqual(self.ids([group]), ['w10'])

    def test_cell_with_uncovered_footprint_is_not_done(self):
        self.built_street()
        self.assertTrue(self.reg.cell(CELL).done)            # the journal alone cannot see the map
        with mock.patch('cell_registry.propose_feature_candidates', return_value=[GROUP_A, GROUP_B]):
            task = N.next_task(self.reg, root=self.root)
            self.assertEqual((task['kind'], task['cell']), ('needs-prep', '48,-44'))
            self.assertEqual(len(self.policy.check(CELL)['candidates']), 2)
            self.assertIn('5 building footprints in 2 groups', CR._format_status(self.reg, '48,-44'))
            self.assertIn('done=False', CR._format_status(self.reg, '48,-44'))
            issues = V.check_uncovered_footprints(self.reg)
            self.assertEqual([i['id'] for i in issues], ['uncovered-footprint:48,-44'])
            self.assertIn('w10', issues[0]['message'])
            self.record([GROUP_A, GROUP_B])
            self.assertEqual(V.check_uncovered_footprints(self.reg), [])
            self.policy.validate(CELL)
            self.assertEqual(N.next_task(self.reg, root=self.root)['kind'], 'feature')

    def test_fully_covered_cell_stays_done(self):
        self.built_street()
        with mock.patch('cell_registry.propose_feature_candidates', return_value=[OLD]):
            self.policy.validate(CELL)
            self.reg.build_policy = self.policy.enforce
            self.jw.seed(CELL, trees=True, decor=True)
            self.assertIsNone(N.next_task(self.reg, root=self.root))
            self.assertIn('done=True', CR._format_status(self.reg, '48,-44'))
            self.assertEqual(V.check_uncovered_footprints(self.reg), [])

    def test_spec_backed_plan_under_a_different_id_prevents_a_duplicate(self):
        """#233: houses_street_wN with a ways spec covers the same buildings; prepare does not re-plan."""
        plan = f'{STREET}_w1'
        self.reg.change(Note(CELL, 'plan_feature', OLD['label'], plan))
        builds = self.root / 'site' / 'builds'
        builds.mkdir(parents=True)
        (builds / f'{plan}.json').write_text(json.dumps({'name': plan, 'ways': {'1': 'K1', '2': 'K2'}}))
        self.assertEqual(self.record([OLD]), [])
        self.assertEqual(self.record([OLD]), [])
        self.assertEqual([f.id for f in self.reg.cell(CELL).features], [plan])

    def test_partly_covered_house_group_proposes_only_uncovered_refs(self):
        """#233: a house plan covering some ways leaves the rest proposable as houses."""
        plan = f'{STREET}_w10'
        self.reg.change(Note(CELL, 'plan_feature', 'partial', plan))
        builds = self.root / 'site' / 'builds'
        builds.mkdir(parents=True)
        (builds / f'{plan}.json').write_text(json.dumps({'name': plan, 'ways': {'10': 'A', '11': 'B'}}))
        left = CR.drop_covered_candidates(self.reg, CELL, [GROUP_A])
        self.assertEqual([(i['kind'], i['refs']) for i in left], [('houses', ['w12'])])
        self.assertEqual(self.record([GROUP_A]), [f'{STREET}_w12'])

    def test_two_groups_sharing_a_street_name_stay_distinct_across_cells(self):
        """#233: same street name in two cells does not merge unrelated buildings."""
        neighbour = (CELL[0] - 1, CELL[1])
        self.reg.change(Note(CELL, 'plan_feature', GROUP_A['label'], f'{STREET}_w10'))
        builds = self.root / 'site' / 'builds'
        builds.mkdir(parents=True)
        (builds / f'{STREET}_w10.json').write_text(
            json.dumps({'name': f'{STREET}_w10', 'ways': {'10': 'A', '11': 'B', '12': 'C'}}))
        cid = f'{neighbour[0]},{neighbour[1]}'
        self.assertEqual(CR.record_feature_candidates(self.reg, cid, [GROUP_B]),
                         [{'id': f'{STREET}_w20', 'label': GROUP_B['label'], 'kind': 'houses'}])

    def test_unresolved_legacy_street_plan_is_flagged_and_not_duplicated(self):
        """#233: a bare street plan with no recoverable refs is neither assumed covered nor re-planned."""
        neighbour = (CELL[0] - 1, CELL[1])
        self.reg.change(Note(neighbour, 'plan_feature', 'legacy', STREET))
        self.assertEqual(self.ids([GROUP_A]), [STREET])
        self.assertEqual(self.record([GROUP_A]), [])
        self.assertEqual([f.id for f in self.reg.cell(CELL).features], [])
        self.assertEqual(CR.record_feature_candidates(self.reg, f'{neighbour[0]},{neighbour[1]}',
                                                      [GROUP_A]), [])
        warning = CR.ambiguous_legacy_house_plans(self.reg, CELL, [GROUP_A])
        self.assertEqual(warning, [
            f'ambiguous legacy plan {STREET}: no readable handoff spec; audit refs {GROUP_A["refs"]}',
        ])

    def test_unrelated_group_next_to_unbuilt_prepared_plan_is_not_legacy(self):
        """#233: a just-prepared specless sibling is not ambiguous legacy for another group."""
        self.reg.change(Note(CELL, 'plan_feature', GROUP_A['label'], f'{STREET}_w10'))
        self.assertEqual(CR.ambiguous_legacy_house_plans(self.reg, CELL, [GROUP_B]), [])
        self.assertEqual(self.record([GROUP_B]), [f'{STREET}_w20'])
        self.assertEqual({f.id for f in self.reg.cell(CELL).features},
                         {f'{STREET}_w10', f'{STREET}_w20'})

    def test_specless_house_plan_name_ref_does_not_split_or_duplicate(self):
        """#233: a specless houses_*_<ref> plan is unresolved — no name peel, no second plan."""
        terrace = group(['w10', 'w11', 'w12', 'w13', 'w14', 'w15'])
        self.reg.change(Note(CELL, 'plan_feature', 'legacy partial', f'{STREET}_w13'))
        self.assertEqual(CR.drop_covered_candidates(self.reg, CELL, [terrace]), [])
        self.assertEqual(self.record([terrace]), [])
        self.assertEqual([f.id for f in self.reg.cell(CELL).features], [f'{STREET}_w13'])
        self.assertEqual(CR.ambiguous_legacy_house_plans(self.reg, CELL, [terrace]), [
            f'ambiguous legacy plan {STREET}_w13: no readable handoff spec; '
            f'audit refs {terrace["refs"]}',
        ])

    def test_repeat_prepare_records_no_extra_plans_for_unchanged_houses(self):
        """#233: a second record pass is a no-op once the house group is planned by ref."""
        plan = f'{STREET}_w10'
        self.reg.change(Note(CELL, 'plan_feature', GROUP_A['label'], plan))
        builds = self.root / 'site' / 'builds'
        builds.mkdir(parents=True)
        (builds / f'{plan}.json').write_text(
            json.dumps({'name': plan, 'ways': {'10': 'A', '11': 'B', '12': 'C'}}))
        self.assertEqual(self.record([GROUP_A, GROUP_B]), [f'{STREET}_w20'])
        self.assertEqual(self.record([GROUP_A, GROUP_B]), [])


if __name__ == '__main__':
    unittest.main()
