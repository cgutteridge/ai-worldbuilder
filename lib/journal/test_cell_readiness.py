"""Readiness through named-cell checks and the journal's guarded build boundary."""
from __future__ import annotations

import unittest

from cell_journal import Build, Refused
from journal_testkit import JournalWorld


class Evidence:
    """Synthetic provider answers; no selected world or external files."""

    def __init__(self):
        self.revision = 'evidence-1'
        self.fraction = 1.0
        self.aerial = 1.0
        self.missing = []
        self.items = []
        self.calls = 0

    def facts(self, cell):
        return {'revision': self.revision, 'clip_coverage': self.fraction,
                'aerial_coverage': self.aerial, 'lidar_missing': self.missing}

    def candidates(self, reg, cell):
        self.calls += 1
        return self.items


class ReadinessTest(unittest.TestCase):
    def setUp(self):
        self.world = JournalWorld()
        self.addCleanup(self.world.__exit__)
        self.world.__enter__()
        self.evidence = Evidence()

    def test_named_unregistered_cell_reports_partial_coverage_without_writing(self):
        from cell_readiness import ReadinessPolicy
        self.evidence.fraction = 0.125
        report = ReadinessPolicy(self.world.reg, self.evidence).check((0, 0))
        self.assertFalse(report['ready'])
        self.assertEqual(report['clip_coverage'], 0.125)
        self.assertIn('map query coverage: 12.5%', report['blockers'])
        self.assertIn('terrain: focus ghost baseline required', report['blockers'])
        self.assertEqual(self.world.reg.view(), {})
        self.assertFalse(self.world.reg.journal_path.exists())

    def test_apply_policy_refuses_cross_cell_gap_before_reads_or_begin(self):
        from cell_readiness import ReadinessPolicy
        self.world.seed((0, 0), ghost_ground=True)
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        policy.validate((0, 0))
        self.world.reg.build_policy = policy.enforce
        before = self.world.reg.journal_path.read_bytes()
        self.evidence.fraction = 0.125
        with self.assertRaisesRegex(Refused, 'map query coverage'):
            self.world.reg.change(Build('ground', 'ground', {(0, 60, 0): 'minecraft:grass_block'},
                                        cell=(0, 0)), self.world.apply)
        self.assertEqual(before, self.world.reg.journal_path.read_bytes())

    def test_validation_is_versioned_and_successful_ground_redo_does_not_stale_it(self):
        from cell_readiness import ReadinessPolicy
        self.world.seed((0, 0), ghost_ground=True)
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        self.assertFalse(policy.check((0, 0))['ready'])
        self.assertIn('validation required', policy.check((0, 0))['blockers'])
        policy.validate((0, 0))
        self.assertTrue(policy.check((0, 0))['ready'])
        self.world.seed((0, 0), ground=True)
        self.assertTrue(policy.check((0, 0))['ready'])
        self.evidence.revision = 'evidence-2'
        self.assertIn('validation stale', policy.check((0, 0))['blockers'])

    def test_tree_leaves_can_land_in_unprepared_neighbors_and_undo_exactly(self):
        from cell_journal import Undo
        from cell_readiness import ReadinessPolicy
        self.world.seed((0, 0), ground=True)
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        report = policy.validate((0, 0))
        self.world.reg.build_policy = policy.enforce
        cells = {(127, 61, 20): 'minecraft:oak_log',
                 (128, 65, 20): 'minecraft:oak_leaves[persistent=true]',
                 (127, 65, -1): 'minecraft:oak_leaves[persistent=true]'}
        build = Build('edge_trees', 'trees', cells, cell=(0, 0),
                      meta={'evidence_revision': 'evidence-1',
                            'journal_revision': report['journal_revision']})

        receipt = self.world.reg.change(build, self.world.apply)

        self.assertEqual(receipt.status, 'landed')
        entry = self.world.reg.entries(plan='edge_trees')[-1]
        self.assertEqual(set(entry.cells), {(0, 0), (1, 0), (0, -1)})
        self.assertEqual(set(entry.meta['readiness_cells']), {'0,0'})
        for neighbor in ((1, 0), (0, -1)):
            self.assertFalse(policy.check(neighbor)['ready'])
            self.assertFalse(self.world.reg.cell(neighbor).trees)
        for p, state in cells.items():
            self.assertEqual(self.world.blocks.read(p), state)
        self.world.reg.change(Undo('edge_trees'), self.world.apply)
        for p in cells:
            self.assertEqual(self.world.blocks.read(p), 'minecraft:air')

    def test_tree_overhang_still_requires_prepared_trunk_cell(self):
        from cell_readiness import ReadinessPolicy
        self.world.seed((0, 0), ground=True)
        self.world.reg.build_policy = ReadinessPolicy(self.world.reg, self.evidence).enforce
        before = self.world.reg.journal_path.read_bytes()

        with self.assertRaisesRegex(Refused, 'needs-prep 0,0.*validation required'):
            self.world.reg.begin(Build('edge_trees', 'trees',
                                       {(128, 65, 20): 'minecraft:oak_leaves'}, cell=(0, 0)))

        self.assertEqual(before, self.world.reg.journal_path.read_bytes())

    def test_tree_non_leaf_overhang_and_decor_keep_neighbor_readiness(self):
        from cell_readiness import ReadinessPolicy
        self.world.seed((0, 0), ground=True)
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        policy.validate((0, 0))
        self.world.reg.build_policy = policy.enforce
        before = self.world.reg.journal_path.read_bytes()
        for pass_, state in (('trees', 'minecraft:oak_log'), ('trees', 'minecraft:stone'),
                             ('decor', 'minecraft:oak_leaves')):
            with self.subTest(pass_=pass_, state=state):
                with self.assertRaisesRegex(Refused, 'needs-prep 1,0'):
                    self.world.reg.begin(Build('edge_build', pass_, {(128, 65, 20): state},
                                               cell=(0, 0)))
                self.assertEqual(before, self.world.reg.journal_path.read_bytes())

    def test_tree_overhang_keeps_neighbor_ownership_protection(self):
        from cell_journal import Clash
        from cell_readiness import ReadinessPolicy
        self.world.seed((0, 0), ground=True)
        p = (128, 65, 20)
        self.world.build('neighbor_wall', 'feature', {p: 'minecraft:bricks'}, cell=(1, 0))
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        policy.validate((0, 0))
        self.world.reg.build_policy = policy.enforce
        before = self.world.reg.journal_path.read_bytes()

        with self.assertRaises(Clash):
            self.world.reg.begin(Build('edge_trees', 'trees', {p: 'minecraft:oak_leaves'}, cell=(0, 0)))

        self.assertEqual(before, self.world.reg.journal_path.read_bytes())
        self.assertEqual(self.world.blocks.read(p), 'minecraft:bricks')

    def test_tree_overhang_keeps_neighbor_in_flight_exclusion(self):
        from cell_journal import InFlight
        from cell_readiness import ReadinessPolicy
        self.world.seed((0, 0), ground=True)
        self.world.reg.begin(Build('neighbor_wall', 'feature',
                                   {(129, 65, 20): 'minecraft:bricks'}, cell=(1, 0)))
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        policy.validate((0, 0))
        self.world.reg.build_policy = policy.enforce
        before = self.world.reg.journal_path.read_bytes()

        with self.assertRaises(InFlight):
            self.world.reg.begin(Build('edge_trees', 'trees',
                                       {(128, 65, 20): 'minecraft:oak_leaves'}, cell=(0, 0)))

        self.assertEqual(before, self.world.reg.journal_path.read_bytes())

    def test_cross_cell_feature_requires_neighbor_preparation_and_accounting(self):
        from cell_journal import Note
        from cell_readiness import ReadinessPolicy
        self.world.seed((0, 0), ghost_ground=True)
        self.world.reg.change(Note((0, 0), 'plan_feature', 'house', 'house'))
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        policy.validate((0, 0))
        self.world.reg.build_policy = policy.enforce
        cells = {(1, 65, 1): 'minecraft:bricks', (129, 65, 1): 'minecraft:bricks'}
        before = self.world.reg.journal_path.read_bytes()
        with self.assertRaisesRegex(Refused, 'needs-prep 1,0'):
            self.world.reg.begin(Build('house', 'feature', cells, cell=(0, 0)))
        self.assertEqual(before, self.world.reg.journal_path.read_bytes())
        self.world.reg.build_policy = None
        self.world.seed((1, 0), ghost_ground=True)
        policy.validate((1, 0))
        self.world.reg.build_policy = policy.enforce
        self.assertEqual(self.world.reg.change(Build('house', 'feature', cells, cell=(0, 0)),
                                              self.world.apply).status, 'landed')

    def test_provider_errors_are_not_empty_evidence_or_ready(self):
        from cell_readiness import ReadinessPolicy
        from unittest import mock
        with mock.patch.object(self.evidence, 'facts', side_effect=ValueError('broken map')):
            report = ReadinessPolicy(self.world.reg, self.evidence).check((3, 4))
        self.assertFalse(report['ready'] or report['complete'])
        self.assertIn('evidence error: broken map', report['blockers'])

    def test_prepare_is_report_only_then_restartable_and_idempotent(self):
        from cell_preparation import prepare_cell
        world, evidence = self.world, self.evidence
        evidence.fraction = .125
        evidence.items = [{'id': 'w1', 'kind': 'building', 'refs': ['w1'], 'label': 'hall'}]

        class Repairs:
            extracts = ghosts = 0

            def preflight(self, cell, needs_extract):
                return {'blockers': [], 'inputs': []}

            def extract(self, cell):
                self.extracts += 1
                evidence.fraction = 1

            def ghost(self, cell):
                self.ghosts += 1
                world.seed(cell, ghost_ground=True)

        repairs = Repairs()
        before = dict(world.reg.view())
        report = prepare_cell(world.reg, evidence, (0, 0), repairs=repairs)
        self.assertFalse(report['ready'])
        self.assertEqual(before, world.reg.view())
        self.assertEqual((repairs.extracts, repairs.ghosts), (0, 0))
        report = prepare_cell(world.reg, evidence, (0, 0), apply=True, repairs=repairs,
                              check_lock=lambda: None)
        self.assertTrue(report['ready'])
        count = world.reg.journal_path.read_bytes()
        self.assertEqual((repairs.extracts, repairs.ghosts), (1, 1))
        self.assertTrue(prepare_cell(world.reg, evidence, (0, 0), apply=True, repairs=repairs,
                                    check_lock=lambda: None)['ready'])
        self.assertEqual(world.reg.journal_path.read_bytes(), count)

    def test_skipped_ghost_leaves_prepare_blocked(self):
        from cell_preparation import prepare_cell
        from unittest import mock
        repairs = mock.Mock()
        repairs.preflight.return_value = {'blockers': [], 'inputs': []}
        report = prepare_cell(self.world.reg, self.evidence, (0, 0), apply=True,
                              repairs=repairs, check_lock=lambda: None)
        self.assertFalse(report['ready'])
        self.assertIn('terrain: focus ghost baseline required', report['blockers'])
        self.assertEqual(self.world.reg.notes((0, 0), kinds=('readiness',)), ())

    def test_queue_reports_preparation_then_skips_run_local_exclusions(self):
        from cell_readiness import ReadinessPolicy
        from registry_next import next_task
        self.world.seed((0, 0), ghost_ground=True, marker=True)
        self.world.seed((1, 0), ghost_ground=True)
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        policy.validate((1, 0))
        task = next_task(self.world.reg, policy=policy)
        self.assertEqual((task['kind'], task['cell']), ('needs-prep', '0,0'))
        task = next_task(self.world.reg, policy=policy, exclude={(0, 0)})
        self.assertEqual((task['kind'], task['cell'], task['layer']), ('layer', '1,0', 'ground'))
        blocked = next_task(self.world.reg, policy=policy, exclude={(0, 0), (1, 0)})
        self.assertEqual(blocked['kind'], 'blocked')
        self.assertEqual(self.world.reg.marker_cell(), (0, 0))

    def test_validated_completed_queue_does_not_generate_candidates(self):
        from cell_readiness import ReadinessPolicy
        from registry_next import next_task
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        self.world.seed((0, 0), ghost_ground=True)
        policy.validate((0, 0))
        self.world.reg.build_policy = policy.enforce
        self.world.seed((0, 0), ground=True, trees=True, decor=True)
        before = self.evidence.calls
        self.assertIsNone(next_task(self.world.reg, policy=policy))
        self.assertIsNone(next_task(self.world.reg, policy=policy))
        self.assertEqual(self.evidence.calls, before)
        self.evidence.revision = 'map-growth'
        self.assertEqual(next_task(self.world.reg, policy=policy)['kind'], 'needs-prep')
        self.assertEqual(self.evidence.calls, before)

    def test_known_stale_plan_is_refused_even_after_inputs_are_prepared_again(self):
        from cell_readiness import ReadinessPolicy
        self.world.seed((0, 0), ghost_ground=True)
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        policy.validate((0, 0))
        plan = Build('ground', 'ground', {(1, 60, 1): 'minecraft:grass_block'}, cell=(0, 0),
                     meta={'evidence_revision': 'evidence-1'})
        self.evidence.revision = 'evidence-2'
        policy.validate((0, 0))
        self.world.reg.build_policy = policy.enforce
        with self.assertRaisesRegex(Refused, 'stale plan'):
            self.world.reg.begin(plan)

    def test_plan_made_before_a_new_feature_lands_is_refused_after_revalidation(self):
        from cell_journal import Note
        from cell_readiness import ReadinessPolicy
        self.world.seed((0, 0), ground=True)
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        report = policy.validate((0, 0))
        old_trees = Build('trees', 'trees', {}, cell=(0, 0),
                          meta={'evidence_revision': 'evidence-1',
                                'journal_revision': report['journal_revision']})
        self.world.reg.change(Note((0, 0), 'plan_feature', 'new house', 'house'))
        policy.validate((0, 0))
        self.world.seed((0, 0), features=('house',))
        self.world.reg.build_policy = policy.enforce
        before = self.world.reg.journal_path.read_bytes()
        with self.assertRaisesRegex(Refused, 'stale plan.*journal'):
            self.world.reg.begin(old_trees)
        self.assertEqual(before, self.world.reg.journal_path.read_bytes())

    def test_explicit_parent_followup_and_reset_keep_the_base_feature_accounting(self):
        import site_kit
        from cell_journal import CellRegistry
        from cell_readiness import ReadinessPolicy
        self.world.seed((0, 0), ground=True, features=('base',))
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        policy.validate((0, 0))
        self.world.reg.build_policy = policy.enforce
        q = (4, 61, 4)
        self.world.reg.change(Build('base-lower', 'feature', {q: 'minecraft:air'}, cell=(0, 0),
                                    modifies=('base',), meta={'parent_feature': 'base'}), self.world.apply)
        policy.validate((0, 0))
        reset = site_kit.reset_to_base('base', ('base-lower',))
        reset.evidence_revision = 'evidence-1'  # synthetic provider identity
        self.world.reg.change(Build(reset.name, 'feature', reset.cells, cell=(0, 0), payload=reset,
                                    modifies=('base', 'base-lower')), self.world.apply)
        fresh = CellRegistry(self.world.root, self.world.blocks)
        self.assertEqual(self.world.blocks.read(q), 'minecraft:bricks')
        self.assertEqual(fresh.entries(plan='base-reset')[-1].meta['parent_feature'], 'base')
        self.assertTrue(all(f.status == 'built' for f in fresh.cell((0, 0)).features))

    def test_parent_followup_still_requires_prepared_neighbors_before_begin(self):
        from cell_journal import CellRegistry
        from cell_readiness import ReadinessPolicy
        self.world.seed((0, 0), ground=True, features=('base',))
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        policy.validate((0, 0))
        self.world.reg.build_policy = policy.enforce
        cells = {(4, 61, 4): 'minecraft:air', (129, 65, 1): 'minecraft:bricks'}
        followup = Build('base-extension', 'feature', cells, cell=(0, 0), modifies=('base',),
                         meta={'parent_feature': 'base'})
        before = self.world.reg.journal_path.read_bytes()
        with self.assertRaisesRegex(Refused, 'needs-prep 1,0'):
            self.world.reg.begin(followup)
        self.assertEqual(before, self.world.reg.journal_path.read_bytes())
        self.world.reg.build_policy = None
        self.world.seed((1, 0), ghost_ground=True)
        policy.validate((1, 0))
        self.world.reg.build_policy = policy.enforce
        self.assertEqual(self.world.reg.change(followup, self.world.apply).status, 'landed')
        fresh = CellRegistry(self.world.root, self.world.blocks)
        entry = fresh.entries(plan='base-extension')[-1]
        self.assertEqual(entry.cells, ((0, 0), (1, 0)))
        self.assertEqual(entry.meta['parent_feature'], 'base')
        self.assertEqual(fresh.planning_revision(), self.world.reg.planning_revision())

    def test_parent_followup_requires_a_built_parent_and_explicit_modifies(self):
        from cell_journal import Note
        from cell_readiness import ReadinessPolicy
        self.world.seed((0, 0), ground=True)
        self.world.reg.change(Note((0, 0), 'plan_feature', 'base', 'base'))
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        policy.validate((0, 0))
        self.world.reg.build_policy = policy.enforce
        cells = {(4, 61, 4): 'minecraft:bricks'}
        with self.assertRaisesRegex(Refused, 'parent feature base must be built'):
            self.world.reg.begin(Build('base-detail', 'feature', cells, cell=(0, 0),
                                       modifies=('base',), meta={'parent_feature': 'base'}))
        self.world.seed((0, 0), features=('base',))
        with self.assertRaisesRegex(Refused, 'parent feature base must be listed in modifies'):
            self.world.reg.begin(Build('base-detail', 'feature', cells, cell=(0, 0),
                                       meta={'parent_feature': 'base'}))

    def test_old_decor_plan_is_refused_after_successful_tree_generation(self):
        from cell_readiness import ReadinessPolicy
        self.world.seed((0, 0), ground=True)
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        report = policy.validate((0, 0))
        decor = Build('decor', 'decor', {}, cell=(0, 0),
                      meta={'evidence_revision': 'evidence-1', 'journal_revision': report['journal_revision']})
        self.world.seed((0, 0), trees=True)
        self.world.reg.build_policy = policy.enforce
        with self.assertRaisesRegex(Refused, 'stale plan.*journal'):
            self.world.reg.begin(decor)

    def test_ground_redo_stales_unexecuted_plans_but_preserves_current_completion(self):
        from cell_readiness import ReadinessPolicy
        self.world.seed((0, 0), ground=True)
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        policy.validate((0, 0))
        self.world.reg.build_policy = policy.enforce
        self.world.seed((0, 0), trees=True, decor=True)
        report = policy.check((0, 0))
        trees = Build('trees', 'trees', {}, cell=(0, 0),
                      meta={'evidence_revision': 'evidence-1', 'journal_revision': report['journal_revision']})
        self.assertTrue(report['complete'])
        self.world.seed((0, 0), ground=True)
        self.assertTrue(policy.check((0, 0))['complete'])
        with self.assertRaisesRegex(Refused, 'stale plan.*journal'):
            self.world.reg.begin(trees)

    def test_planning_identity_ignores_abandoned_attempts_and_non_geometry_notes(self):
        from cell_journal import CellRegistry, Note
        self.world.seed((0, 0), ground=True)
        before = self.world.reg.planning_revision()
        ticket = self.world.reg.begin(Build('trees', 'trees', {}, cell=(0, 0)))
        ticket.abandon('no blocks changed')
        self.world.reg.change(Note((0, 0), 'marker', 'up'))
        self.world.reg.change(Note((0, 0), 'review', 'looks good', 'review-1'))
        self.assertEqual(before, self.world.reg.planning_revision())
        self.assertEqual(before, CellRegistry(self.world.root, self.world.blocks).planning_revision())

    def test_failed_tree_redo_does_not_invalidate_decor_but_cannot_certify_completion(self):
        from cell_readiness import ReadinessPolicy
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        self.world.seed((0, 0), ghost_ground=True)
        policy.validate((0, 0))
        self.world.reg.build_policy = policy.enforce
        self.world.seed((0, 0), ground=True, trees=True, decor=True)
        receipt = self.world.reg.change(Build('failed-trees', 'trees',
                                              {(10, 70, 10): 'minecraft:oak_log'}, cell=(0, 0)), lambda w: None)
        self.assertEqual(receipt.status, 'mismatched')
        self.assertTrue(self.world.reg.cell((0, 0)).decor)
        self.assertFalse(policy.check((0, 0))['complete'])
        self.world.reg.reconcile([(0, 0)], fix=True, apply=self.world.apply)
        self.assertTrue(self.world.reg.cell((0, 0)).trees)
        self.assertFalse(self.world.reg.cell((0, 0)).decor)

    def test_content_audit_detects_same_timestamp_changes_and_check_is_read_only(self):
        import json
        import os
        from unittest import mock
        import aerial_coverage
        from cell_readiness import ReadinessPolicy
        from readiness_evidence import LocalEvidence
        root = self.world.root
        frame = root / 'config' / 'world-frame.json'
        frame.parent.mkdir()
        frame.write_text(json.dumps({'offsets': {'easting': 0, 'northing': 0, 'sea_level': 0}}))
        clip = root / 'reference' / 'vector_map' / 'fixture' / 'buildings-bbox-0-0-127-127.geojson'
        clip.parent.mkdir(parents=True)
        clip.write_text('{"type":"FeatureCollection","features":[],"revision":1}')
        self.world.seed((0, 0), ghost_ground=True)
        evidence = LocalEvidence(root)
        policy = ReadinessPolicy(self.world.reg, evidence)
        with mock.patch.object(LocalEvidence, 'lidar_status', return_value=([], [])), \
                mock.patch.object(aerial_coverage, 'coverage', return_value=1.), \
                mock.patch.object(aerial_coverage, 'mosaics', return_value=[]):
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            self.assertFalse(policy.check((0, 0))['validated'])
            self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()})
            policy.validate((0, 0))
            first = policy.check((0, 0))['revision']
            stamp = clip.stat()
            clip.write_text(clip.read_text().replace('"revision":1', '"revision":2'))
            os.utime(clip, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            evidence.refresh()
            report = policy.check((0, 0))
            self.assertNotEqual(first, report['revision'])
            self.assertIn('validation stale', report['blockers'])

    def test_prepared_features_and_ground_can_land_in_either_order(self):
        from cell_journal import Note
        from cell_readiness import ReadinessPolicy
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        for tx, feature_first in enumerate((True, False)):
            cell, fid = (tx, 0), f'house-{tx}'
            self.world.reg.build_policy = None
            self.world.seed(cell, ghost_ground=True)
            self.world.reg.change(Note(cell, 'plan_feature', fid, fid))
            policy.validate(cell)
            self.world.reg.build_policy = policy.enforce
            if feature_first:
                self.world.seed(cell, features=(fid,))
                self.world.seed(cell, ground=True)
            else:
                self.world.seed(cell, ground=True)
                self.world.seed(cell, features=(fid,))
            self.world.seed(cell, trees=True, decor=True)
            self.assertTrue(policy.check(cell)['complete'])

    def test_versioned_coverage_dismissals_can_be_renewed_without_losing_history(self):
        from cell_journal import Note
        from cell_readiness import ReadinessPolicy
        self.evidence.fraction = 0.
        self.world.seed((0, 0), ghost_ground=True)
        self.world.reg.change(Note((0, 0), 'clip_coverage-dismiss', 'surveyed', 'evidence-1'))
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        policy.validate((0, 0))
        self.evidence.revision = 'evidence-2'
        self.assertIn('map coverage dismissal stale', policy.check((0, 0))['blockers'])
        self.world.reg.change(Note((0, 0), 'clip_coverage-dismiss', 'resurveyed', 'evidence-2'))
        self.assertTrue(policy.validate((0, 0))['ready'])
        self.assertEqual(len(self.world.reg.notes((0, 0), kinds=('clip_coverage-dismiss',))), 2)

    def test_prepare_preserves_partly_built_legacy_cells_and_does_not_certify_old_layers(self):
        from cell_preparation import prepare_cell
        from unittest import mock
        self.world.seed((0, 0), ground=True, features=('house',), trees=True, decor=True)
        repairs = mock.Mock()
        repairs.preflight.return_value = {'blockers': [], 'inputs': []}
        before = self.world.reg.cell((0, 0))
        report = prepare_cell(self.world.reg, self.evidence, (0, 0), apply=True,
                              repairs=repairs, check_lock=lambda: None)
        self.assertTrue(report['ready'])
        self.assertFalse(report['complete'] or report['trees_current'] or report['decor_current'])
        self.assertEqual(before, self.world.reg.cell((0, 0)))
        repairs.ghost.assert_not_called()
        repairs.extract.assert_not_called()

    def test_missing_local_inputs_block_preparation_without_any_repairs(self):
        from cell_preparation import prepare_cell
        from unittest import mock
        for missing in ('lidar', 'aerial', 'pbf'):
            with self.subTest(missing=missing):
                self.evidence.missing = [{'filename': 'missing.tif', 'estimated_mb': 75}] if missing == 'lidar' else []
                self.evidence.aerial = 0.5 if missing == 'aerial' else 1.
                repairs = mock.Mock()
                repairs.preflight.return_value = {'blockers': ['local PBF required: fixture.pbf']
                                                  if missing == 'pbf' else [], 'inputs': []}
                result = prepare_cell(self.world.reg, self.evidence, (0, 0), apply=True,
                                      repairs=repairs, check_lock=lambda: None)
                self.assertFalse(result['ready'])
                repairs.extract.assert_not_called()
                repairs.ghost.assert_not_called()
                self.assertEqual(self.world.reg.view(), {})

    def test_production_style_policy_requires_an_input_identity_on_direct_builds(self):
        from cell_readiness import ReadinessPolicy
        self.world.seed((0, 0), ghost_ground=True)
        policy = ReadinessPolicy(self.world.reg, self.evidence, require_plan_identity=True)
        policy.validate((0, 0))
        self.world.reg.build_policy = policy.enforce
        cells = {(5, 60, 5): 'minecraft:grass_block'}
        with self.assertRaisesRegex(Refused, 'no evidence identity'):
            self.world.reg.begin(Build('ground', 'ground', cells, cell=(0, 0)))
        with self.assertRaisesRegex(Refused, 'no journal identity'):
            self.world.reg.begin(Build('ground', 'ground', cells, cell=(0, 0),
                                       meta={'evidence_revision': 'evidence-1'}))
        self.assertEqual(self.world.reg.change(Build('ground', 'ground', cells, cell=(0, 0),
                                                    meta={'evidence_revision': 'evidence-1',
                                                          'journal_revision': policy.check((0, 0))['journal_revision']}),
                                              self.world.apply).status, 'landed')


from journal_testkit import FakePaced, LiveApplyCase


class DirectApplyTest(LiveApplyCase):
    def test_narrow_rebuild_refuses_an_unprepared_previous_neighbor_before_backup(self):
        import readiness_evidence
        import site_kit
        from cell_journal import CellRegistry
        from cell_readiness import ReadinessPolicy
        from unittest import mock
        evidence = Evidence()
        reg = CellRegistry(self.t, self.blocks)
        writer = lambda w: self.blocks.write_many(w.cells)
        reg.change(Build('ground', 'ground', {(1, 60, 1): 'minecraft:grass_block'}, cell=(0, 0)), writer)
        reg.change(Build('base', 'feature', {(1, 65, 1): 'minecraft:bricks',
                                             (129, 65, 1): 'minecraft:bricks'}, cell=(0, 0)), writer)
        ReadinessPolicy(reg, evidence).validate((0, 0))
        plan = site_kit.Plan('base')
        plan.evidence_revision = 'evidence-1'
        plan.set(1, 65, 1, 'stone')
        before = reg.journal_path.read_bytes()
        guard = lambda reg, build, touched: ReadinessPolicy(reg, evidence, require_plan_identity=True).enforce(build, touched)
        with mock.patch.object(readiness_evidence, 'enforce_live', guard):
            with self.assertRaisesRegex(SystemExit, 'needs-prep 1,0'):
                self.run_apply(plan, cell=(0, 0))
        self.assertEqual(before, reg.journal_path.read_bytes())
        self.assertNotIn('structure_save_from_world', FakePaced.calls)
        self.assertNotIn('block_fill_batch', FakePaced.calls)

    def test_live_apply_refuses_the_plan_snapshot_after_journal_geometry_changes(self):
        import readiness_evidence
        import site_kit
        from cell_journal import CellRegistry
        from cell_readiness import ReadinessPolicy
        from unittest import mock
        evidence = Evidence()
        reg = CellRegistry(self.t, self.blocks)
        reg.change(Build('ground', 'ground', {(1, 60, 1): 'minecraft:grass_block'}, cell=(0, 0)),
                   lambda w: self.blocks.write_many(w.cells))
        ReadinessPolicy(reg, evidence).validate((0, 0))
        plan = site_kit.Plan('old-decor')
        plan.evidence_revision = 'evidence-1'  # synthetic provider identity
        reg.change(Build('trees', 'trees', {}, cell=(0, 0)))
        before = reg.journal_path.read_bytes()
        guard = lambda reg, build, touched: ReadinessPolicy(reg, evidence, require_plan_identity=True).enforce(build, touched)
        with mock.patch.object(readiness_evidence, 'enforce_live', guard):
            with self.assertRaisesRegex(SystemExit, 'stale plan.*journal'):
                self.run_apply(plan, stage='decor', cell=(0, 0))
        self.assertEqual(before, reg.journal_path.read_bytes())
        self.assertNotIn('structure_save_from_world', FakePaced.calls)
        self.assertNotIn('block_fill_batch', FakePaced.calls)

    def test_reset_apply_preserves_parent_authorization_at_both_live_guards(self):
        import readiness_evidence
        import site_kit
        from cell_journal import CellRegistry
        from cell_readiness import ReadinessPolicy
        from unittest import mock
        evidence = Evidence()
        reg = CellRegistry(self.t, self.blocks)
        writer = lambda w: self.blocks.write_many(w.cells)
        q = (1, 65, 1)
        reg.change(Build('ground', 'ground', {(1, 60, 1): 'minecraft:grass_block'}, cell=(0, 0)), writer)
        reg.change(Build('base', 'feature', {q: 'minecraft:bricks'}, cell=(0, 0)), writer)
        reg.change(Build('base-lower', 'feature', {q: 'minecraft:stone'}, cell=(0, 0),
                         modifies=('base',)), writer)
        ReadinessPolicy(reg, evidence).validate((0, 0))
        plan = site_kit.reset_to_base('base', ('base-lower',))
        plan.evidence_revision = 'evidence-1'  # synthetic provider identity
        guard = lambda reg, build, touched: ReadinessPolicy(reg, evidence, require_plan_identity=True).enforce(build, touched)
        with mock.patch.object(readiness_evidence, 'enforce_live', guard):
            receipt = self.run_apply(plan, modifies=('base', 'base-lower'), cell=(0, 0))
        self.assertEqual(receipt['mismatches'], 0)
        self.assertEqual(self.blocks.read(q), 'minecraft:bricks')
        self.assertEqual(self.entries('base-reset')[-1].meta['parent_feature'], 'base')

    def test_direct_apply_and_live_session_both_enforce_before_begin_and_writes(self):
        import cell_registry
        import readiness_evidence
        import site_kit
        from cell_readiness import ReadinessPolicy
        from unittest import mock
        evidence = Evidence()
        evidence.fraction = .125
        plan = site_kit.Plan('house')
        plan.set(1, 65, 1, 'bricks')
        guard = lambda reg, build, touched: ReadinessPolicy(reg, evidence).enforce(build, touched)
        with mock.patch.object(readiness_evidence, 'enforce_live', guard):
            with self.assertRaisesRegex(SystemExit, 'map query coverage: 12.5%'):
                self.run_apply(plan)
            live = cell_registry.open_registry(self.t, blocks=self.blocks, ghosts=None, ring=0)
            with self.assertRaisesRegex(Refused, 'map query coverage: 12.5%'):
                live.begin(Build('house', 'feature', plan.cells))
        self.assertEqual(self.entries(), ())
        self.assertNotIn('structure_save_from_world', FakePaced.calls)
        self.assertNotIn('block_fill_batch', FakePaced.calls)


