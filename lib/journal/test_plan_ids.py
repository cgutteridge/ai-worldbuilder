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

    def built_street(self):
        """The street plan built earlier, with a hand-off spec naming its two houses."""
        self.jw.seed(CELL, ground=True, trees=True, decor=True, features=(STREET,))
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

    def test_cell_with_uncovered_footprint_is_not_done(self):
        self.built_street()
        self.assertTrue(self.reg.cell(CELL).done)            # the journal alone cannot see the map
        with mock.patch('cell_registry.propose_feature_candidates', return_value=[GROUP_A, GROUP_B]):
            task = N.next_task(self.reg, root=self.root)
            self.assertEqual((task['kind'], task['cell'], task['count']), ('needs-feature-plan', '48,-44', 2))
            self.assertIn('5 building footprints in 2 groups', CR._format_status(self.reg, '48,-44'))
            self.assertIn('done=False', CR._format_status(self.reg, '48,-44'))
            issues = V.check_uncovered_footprints(self.reg)
            self.assertEqual([i['id'] for i in issues], ['uncovered-footprint:48,-44'])
            self.assertIn('w10', issues[0]['message'])
            self.record([GROUP_A, GROUP_B])
            self.assertEqual(V.check_uncovered_footprints(self.reg), [])
            self.assertEqual(N.next_task(self.reg, root=self.root)['kind'], 'feature')

    def test_fully_covered_cell_stays_done(self):
        self.built_street()
        with mock.patch('cell_registry.propose_feature_candidates', return_value=[OLD]):
            self.assertIsNone(N.next_task(self.reg, root=self.root))
            self.assertIn('done=True', CR._format_status(self.reg, '48,-44'))
            self.assertEqual(V.check_uncovered_footprints(self.reg), [])


if __name__ == '__main__':
    unittest.main()
