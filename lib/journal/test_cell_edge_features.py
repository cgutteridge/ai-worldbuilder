#!/usr/bin/env python3
"""A feature that crosses a cell line is proposed, recorded and applied in its primary cell.

Task 433. Column counts are the diagnose-first figures for the two Portswood shops
(ways 149855650 and 167071535): The Mitre has 92 columns in 51,-37 and 313 in 52,-37;
St Denys Surgery has 12 in 51,-37 and 162 in 52,-37. Both homes are 52,-37. The
fixtures below use the same shape (a stub in the western cell, most columns in the
eastern cell, and a same-tag neighbour homed in the west) rather than the live map.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

import cell_features as cf
import cell_registry as CR
import cellmath
from cell_journal import Build, Note, Refused
from cell_readiness import ReadinessPolicy
from coverage import scan_polygon
from journal_testkit import JournalWorld
from site_kit import primary_cell

WHOLE = (-10**9, -10**9, 10**9, 10**9)
WEST = (51, -37)
EAST = (52, -37)
LINE = 52 * 128  # x of the 51,-37 / 52,-37 edge at cell size 128


def _square(x0, z0, x1, z1):
    return [[(float(x0), float(z0)), (float(x1), float(z0)),
             (float(x1), float(z1)), (float(x0), float(z1)), (float(x0), float(z0))]]


def _columns(rings):
    return list(scan_polygon(rings, WHOLE))


def _blocks(rings, y=70):
    return {(x, y, z): 'minecraft:bricks' for x, z in _columns(rings)}


class Evidence:
    """Synthetic provider answers; no selected world or external files."""

    def __init__(self):
        self.revision = 'evidence-1'
        self.fraction = 1.0
        self.aerial = 1.0
        self.missing = []
        self.items = []

    def facts(self, cell):
        return {'revision': self.revision, 'clip_coverage': self.fraction,
                'aerial_coverage': self.aerial, 'lidar_missing': self.missing}

    def candidates(self, reg, cell):
        return self.items


class CellEdgeFeatureTest(unittest.TestCase):
    def setUp(self):
        self.world = JournalWorld()
        self.addCleanup(self.world.__exit__)
        self.world.__enter__()
        self.reg = self.world.reg
        cellmath.reload_grid({'cell': {'size': 128, 'home': [48, -44]}})
        self.addCleanup(cellmath.reload_grid)
        self.evidence = Evidence()
        self.policy = ReadinessPolicy(self.reg, self.evidence)
        self.world.seed(WEST, ghost_ground=True)
        self.world.seed(EAST, ghost_ground=True)

    def _progress(self, idx, cell):
        with mock.patch('cell_features.for_footprint', return_value=[]), \
             mock.patch('cell_features.pictures_for', return_value=[]), \
             mock.patch('cell_features.osm_ref', side_effect=lambda p: p['id']), \
             mock.patch('cell_features.property_label', side_effect=lambda p: p['name']):
            return cf.missing_features(*cell, idx=idx, covered=set(), water_columns=set())

    def _apply(self, plan, rings):
        """Land ``plan`` from the cell that holds most of its columns."""
        blocks = _blocks(rings)
        self.assertEqual(primary_cell(blocks), EAST)
        build = Build(plan, 'feature', blocks)
        self.policy.validate(EAST)
        self.policy.validate_evidence(WEST)
        self.reg.build_policy = self.policy.enforce
        with self.assertRaises(Refused) as caught:
            self.reg.begin(build)
        self.assertIn(f'feature plan {plan} absent from cell plan', str(caught.exception))
        self.assertIn('52,-37', str(caught.exception))
        self.reg.change(Note(EAST, 'plan_feature', plan, plan))
        receipt = self.reg.change(build, self.world.apply)
        self.assertEqual(receipt.status, 'landed')
        landed = [e for e in self.reg.entries() if e.plan == plan and e.kind == 'build']
        self.assertEqual(landed[-1].cell, EAST)

    def test_feature_planned_in_the_wrong_cell_is_proposed_and_applied_from_its_primary(self):
        """A single footprint filed on the minority cell is recovered from the majority cell."""
        # Stub west of the line, most of the footprint east of it.
        hall = _square(LINE - 4, -4700, LINE + 16, -4688)
        cols = _columns(hall)
        counts = {WEST: 0, EAST: 0}
        for x, z in cols:
            counts[cellmath.cell_of(x, z)] += 1
        self.assertGreater(counts[EAST], counts[WEST])
        self.assertEqual(cf.home_cell(cols), EAST)
        idx = SimpleNamespace(
            buildings=[(hall, {'building': 'retail', 'name': 'Edge Hall', 'id': 'w900'})],
            streets=[])
        self.assertEqual([u['refs'] for u in self._progress(idx, EAST)], [['w900']])
        self.assertEqual(self._progress(idx, WEST), [])
        self.reg.change(Note(WEST, 'plan_feature', 'Edge Hall', 'w900'))
        self.reg.change(Note(WEST, 'plan_feature-dismiss', 'filed on the wrong cell', 'w900'))
        east = self._progress(idx, EAST)
        self.assertEqual(CR.drop_covered_candidates(self.reg, EAST, east)[0]['refs'], ['w900'])
        recorded = CR.record_feature_candidates(self.reg, '52,-37', east)
        self.assertEqual([r['id'] for r in recorded], ['w900'])
        self.assertEqual([f.id for f in self.reg.cell(EAST).features if f.id == 'w900'], ['w900'])
        self.assertIn('w900', self.reg.plan_dismissed_ids(WEST))
        self.assertEqual(CR.record_feature_candidates(self.reg, '52,-37', east), [])
        # The plan is now on the primary cell, so apply is the builder's check.
        # Clear it and show the builder refuses until it is put back, then land it.
        self.reg.change(Note(EAST, 'plan_feature-dismiss', 'reset for the apply check', 'w900'))
        self._apply('w900', hall)

    def test_two_shops_propose_and_apply_from_their_primary_cell(self):
        """The parade trap: each shop touches a same-tag neighbour homed in the western cell.

        Diagnosed column counts (live footprints, not this rectangle): The Mitre
        w149855650 is 92 / 313 and St Denys Surgery w167071535 is 12 / 162, west
        then east. Both homes are 52,-37. One plan was left active on 51,-37 and
        one was dismissed there; neither blocked propose or apply from the east.
        """
        mitre = _square(LINE - 6, -4710, LINE + 20, -4690)
        nails = _square(LINE - 40, -4710, LINE - 6, -4690)          # touches Mitre, stays in the west
        surgery = _square(LINE - 4, -4640, LINE + 18, -4620)
        neighbour = _square(LINE - 36, -4640, LINE - 4, -4620)      # touches surgery, stays in the west
        for rings, home in ((mitre, EAST), (nails, WEST), (surgery, EAST), (neighbour, WEST)):
            self.assertEqual(cf.home_cell(_columns(rings)), home)
        idx = SimpleNamespace(buildings=[
            (nails, {'building': 'retail', 'name': '3D Nails', 'id': 'w149855642'}),
            (mitre, {'building': 'retail', 'name': 'The Mitre', 'id': 'w149855650'}),
            (neighbour, {'building': 'yes', 'name': 'West Neighbour', 'id': 'w160671196'}),
            (surgery, {'building': 'yes', 'name': 'St Denys Surgery', 'id': 'w167071535'}),
        ], streets=[])
        east_refs = [tuple(u['refs']) for u in self._progress(idx, EAST)]
        west_refs = [tuple(u['refs']) for u in self._progress(idx, WEST)]
        self.assertIn(('w149855650',), east_refs)
        self.assertIn(('w167071535',), east_refs)
        self.assertNotIn(('w149855650',), west_refs)
        self.assertNotIn(('w167071535',), west_refs)
        self.reg.change(Note(WEST, 'plan_feature', 'The Mitre', 'w149855650'))
        self.reg.change(Note(WEST, 'plan_feature', 'St Denys Surgery', 'w167071535'))
        self.reg.change(Note(WEST, 'plan_feature-dismiss',
                             'filed on the wrong cell', 'w167071535'))
        east = self._progress(idx, EAST)
        kept = [tuple(u['refs']) for u in CR.drop_covered_candidates(self.reg, EAST, east)]
        self.assertIn(('w149855650',), kept)
        self.assertIn(('w167071535',), kept)
        recorded = CR.record_feature_candidates(self.reg, '52,-37', east)
        self.assertEqual(sorted(r['id'] for r in recorded), ['w149855650', 'w167071535'])
        self.assertEqual(sorted(f.id for f in self.reg.cell(EAST).features
                                if f.id in ('w149855650', 'w167071535')),
                         ['w149855650', 'w167071535'])
        self.assertNotIn('w149855650', {f.id for f in self.reg.cell(WEST).features})
        self.assertIn('w149855650', self.reg.plan_dismissed_ids(WEST))
        self.reg.change(Note(EAST, 'plan_feature-dismiss', 'reset for the apply check', 'w149855650'))
        self.reg.change(Note(EAST, 'plan_feature-dismiss', 'reset for the apply check', 'w167071535'))
        self._apply('w149855650', mitre)
        self._apply('w167071535', surgery)


if __name__ == '__main__':
    unittest.main()
