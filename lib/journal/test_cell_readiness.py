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
        self.assertIn('validation stale (evidence facts changed)', policy.check((0, 0))['blockers'])

    def test_validation_records_parts_without_changing_the_combined_identity(self):
        import json
        from cell_readiness import POLICY_VERSION, ReadinessPolicy, content_id
        self.world.seed((0, 0), ghost_ground=True)
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        report = policy.validate((0, 0))
        accepted = json.loads(self.world.reg.notes((0, 0), kinds=('readiness',))[-1]['body'])
        parts = accepted['revision_parts']
        self.assertEqual(parts['policy'], POLICY_VERSION)
        self.assertEqual(parts['evidence'], self.evidence.revision)
        self.assertEqual(parts['plans'], json.loads(json.dumps(policy.plan_identity((0, 0)))))
        self.assertEqual(accepted['revision'], content_id([parts['policy'], parts['evidence'], parts['plans']]))
        self.assertEqual(accepted['revision'], report['revision'])
        before = self.world.reg.journal_path.read_bytes()
        self.assertTrue(policy.validate((0, 0))['validated'])
        self.assertEqual(before, self.world.reg.journal_path.read_bytes())

    def test_stale_validation_names_policy_version_change(self):
        from unittest import mock
        from cell_readiness import POLICY_VERSION, ReadinessPolicy
        self.world.seed((0, 0), ghost_ground=True)
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        policy.validate((0, 0))
        with mock.patch('cell_readiness.POLICY_VERSION', POLICY_VERSION + 1):
            report = policy.check((0, 0))
        blocker = f'validation stale (policy version changed: {POLICY_VERSION} -> {POLICY_VERSION + 1})'
        self.assertIn(blocker, report['blockers'])
        self.assertIn('prepare --cell 0,0 --apply', report['clear_with'][blocker])

    def test_stale_validation_lists_added_changed_and_removed_plan_ids(self):
        from cell_journal import Note
        from cell_readiness import ReadinessPolicy
        self.world.seed((0, 0), ghost_ground=True)
        for fid in ('hall', 'shed', 'unchanged'):
            self.world.reg.change(Note((0, 0), 'plan_feature', fid, fid))
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        policy.validate((0, 0))
        for fid in ('pond_z', 'pond_a'):
            self.world.reg.change(Note((0, 0), 'plan_feature', fid, fid))
        self.world.reg.change(Note((0, 0), 'plan_feature', 'new hall design', 'hall'))
        self.world.reg.change(Note((0, 0), 'plan_feature-dismiss', 'not a building', 'shed'))
        before = self.world.reg.journal_path.read_bytes()
        blocker = ('validation stale (plan identity changed: added: pond_a, pond_z; '
                   'changed: hall; removed: shed)')
        self.assertIn(blocker, policy.check((0, 0))['blockers'])
        self.assertEqual(before, self.world.reg.journal_path.read_bytes())
        self.assertTrue(policy.validate((0, 0))['validated'])

    def test_stale_validation_names_coverage_dismissal_without_calling_its_ref_a_plan(self):
        from cell_journal import Note
        from cell_readiness import ReadinessPolicy
        self.world.seed((0, 0), ghost_ground=True)
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        policy.validate((0, 0))
        self.world.reg.change(Note((0, 0), 'clip_coverage-dismiss', 'accepted gap', self.evidence.revision))
        self.assertIn('validation stale (plan identity changed: coverage dismissal changed)',
                      policy.check((0, 0))['blockers'])

    def test_stale_validation_names_all_changed_parts(self):
        from unittest import mock
        from cell_journal import Note
        from cell_readiness import POLICY_VERSION, ReadinessPolicy
        self.world.seed((0, 0), ghost_ground=True)
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        policy.validate((0, 0))
        self.evidence.revision = 'evidence-2'
        self.world.reg.change(Note((0, 0), 'plan_feature', 'pond', 'pond'))
        with mock.patch('cell_readiness.POLICY_VERSION', POLICY_VERSION + 1):
            report = policy.check((0, 0))
        self.assertIn(f'validation stale (policy version changed: {POLICY_VERSION} -> {POLICY_VERSION + 1}; '
                      'evidence facts changed; plan identity changed: added: pond)', report['blockers'])

    def test_legacy_validation_remains_valid_until_stale_then_reports_unknown_cause(self):
        import json
        from cell_journal import Note
        from cell_readiness import POLICY_VERSION, ReadinessPolicy
        self.world.seed((0, 0), ghost_ground=True)
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        report = policy.check((0, 0))
        self.world.reg.change(Note((0, 0), 'readiness', json.dumps({'revision': report['revision'],
                                                                 'policy': POLICY_VERSION,
                                                                 'warnings': []})))
        self.assertTrue(policy.check((0, 0))['validated'])
        self.evidence.revision = 'evidence-2'
        self.assertIn('validation stale (cause unknown: validation predates cause tracking)',
                      policy.check((0, 0))['blockers'])

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
        self.world.seed((0, 0), ground=True)          # the ground redo a later feature requires (task 285)
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
            self.assertIn('validation stale (evidence facts changed)', report['blockers'])

    def test_prepared_features_and_ground_can_land_in_either_order(self):
        """Both orders land; ground first leaves the ground stale until it is redone (task 285)."""
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
                self.assertTrue(policy.check(cell)['ground_stale'])
                self.world.seed(cell, ground=True)
            self.assertEqual(policy.check(cell)['ground_stale'], [])
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


class MismatchedApplyTest(unittest.TestCase):
    """Task 284: a mismatched apply is cleared by a named command, never by hand-editing."""

    HOUSE = {(1, 60, 1): 'minecraft:stone', (1, 60, 2): 'minecraft:stone'}

    def setUp(self):
        from cell_readiness import ReadinessPolicy
        self.world = JournalWorld()
        self.addCleanup(self.world.__exit__)
        self.world.__enter__()
        self.world.seed((0, 0), ghost_ground=True, features=('house',))   # the cell plan already holds it
        self.policy = ReadinessPolicy(self.world.reg, Evidence())
        self.policy.validate((0, 0))

    def check(self, **kw):
        return self.policy.check((0, 0), require_validation=False, **kw)

    def mismatch(self, plan='house', cells=None):
        receipt = self.world.reg.change(Build(plan, 'feature', cells or self.HOUSE, cell=(0, 0)),
                                        lambda w: None)           # the writer wrote nothing
        self.assertEqual(receipt.status, 'mismatched')
        return receipt

    def test_blocker_names_the_clearing_command(self):
        from cell_readiness import format_readiness
        self.mismatch()
        report = self.check()
        self.assertEqual(report['blockers'], ['recovery: mismatched apply house'])
        command = report['clear_with']['recovery: mismatched apply house']
        self.assertIn('cell_registry.py --world <world> reconcile 0,0 --fix', command)
        self.assertIn('reapply_feature.py --world <world> house --apply', command)
        self.assertIn('clear: recovery: mismatched apply house -> ', format_readiness(report))

    def test_refusal_message_carries_the_command(self):
        self.mismatch()
        self.world.reg.build_policy = self.policy.enforce
        with self.assertRaisesRegex(Refused, r'mismatched apply house \[clear: .*reconcile 0,0 --fix'):
            self.world.reg.begin(Build('ground2', 'ground', {(5, 60, 5): 'minecraft:stone'}, cell=(0, 0)))

    def test_every_blocker_has_a_clearing_command(self):
        from cell_readiness import ReadinessPolicy
        evidence = Evidence()
        evidence.fraction, evidence.aerial = 0.5, 0.5
        evidence.missing = [{'filename': 'a.tif', 'estimated_mb': 1, 'tile': 't'}]
        evidence.items = [{'id': 'candidate'}]
        report = ReadinessPolicy(self.world.reg, evidence).check((4, 4))
        self.assertTrue(report['blockers'])
        self.assertEqual(set(report['clear_with']), set(report['blockers']))
        self.assertFalse([c for c in report['clear_with'].values() if 'none of the known' in c])

    def test_a_plan_may_be_redone_past_its_own_mismatch_but_others_stay_blocked(self):
        self.mismatch()
        self.assertTrue(self.check(redoing=('house',))['ready'])
        self.assertFalse(self.check(redoing=('other',))['ready'])
        self.world.reg.build_policy = self.policy.enforce
        with self.assertRaisesRegex(Refused, 'mismatched apply house'):
            self.world.reg.begin(Build('ground2', 'ground', {(5, 60, 5): 'minecraft:stone'}, cell=(0, 0)))
        receipt = self.world.reg.change(Build('house', 'feature', self.HOUSE, cell=(0, 0)), self.world.apply)
        self.assertEqual(receipt.status, 'landed')                 # the redo's own readback decides
        self.assertTrue(self.check()['ready'])

    def test_mismatch_fully_superseded_by_a_later_landed_plan_blocks_nothing(self):
        self.mismatch()
        self.world.reg.change(Build('patch', 'feature', self.HOUSE, modifies=('house',), cell=(0, 0)),
                              self.world.apply)
        self.assertEqual(self.world.reg.owned_count(self.world.reg.entries(plan='house')[-1].seq), 0)
        self.assertTrue(self.check()['ready'])

    def test_partly_superseded_mismatch_still_blocks(self):
        self.mismatch()
        self.world.reg.change(Build('patch', 'feature', {(1, 60, 1): 'minecraft:glass'},
                                    modifies=('house',), cell=(0, 0)), self.world.apply)
        self.assertIn('recovery: mismatched apply house', self.check()['blockers'])

    def test_reconcile_fix_clears_it_and_says_so(self):
        from cell_registry import format_drift
        self.mismatch()
        dry = self.world.reg.reconcile()
        self.assertEqual([i.kind for i in dry.by_kind('mismatched')], ['mismatched'])
        self.assertIn('clear with: reconcile 0,0 --fix', format_drift(dry))
        drift = self.world.reg.reconcile(fix=True, apply=self.world.apply)
        self.assertTrue(drift.clean)
        self.assertEqual([plan for plan, _seq in drift.cleared], ['house'])
        self.assertIn('house', format_drift(drift, fix=True))
        self.assertIn('preflight no longer blocks', format_drift(drift, fix=True))
        self.assertTrue(self.check()['ready'])

    def test_reconcile_fix_that_cannot_hold_names_the_reapply_command(self):
        from cell_registry import format_drift
        self.mismatch()
        drift = self.world.reg.reconcile(fix=True, apply=lambda w: None)      # the game undoes every write
        self.assertFalse(drift.clean)
        item = drift.by_kind('mismatched')[0]
        self.assertEqual((item.plans, item.layer, item.count, item.fixed), (('house',), 'feature', 2, False))
        out = format_drift(drift, fix=True)
        self.assertIn('still mismatched after repair', out)
        self.assertIn('reapply_feature.py --world <world> house --apply', out)
        self.assertIn('recovery: mismatched apply house', self.check()['blockers'])

    def test_neighbour_entry_names_its_own_cell_for_the_redo(self):
        from cell_registry import format_drift
        self.world.seed((0, 0), ground=True)
        receipt = self.world.reg.change(Build('canopy', 'trees', {(127, 65, 5): 'minecraft:oak_leaves',
                                                                  (128, 65, 5): 'minecraft:oak_leaves'},
                                              cell=(0, 0)), lambda w: None)
        self.assertEqual(receipt.status, 'mismatched')
        report = self.policy.check((1, 0), require_validation=False)
        command = report['clear_with']['recovery: mismatched apply canopy']
        self.assertIn('redo_layer.py --world <world> --cell 0,0 --layers trees', command)
        drift = self.world.reg.reconcile([(1, 0)], fix=True, apply=lambda w: None)
        item = drift.by_kind('mismatched')[0]
        self.assertEqual((item.owner, item.cells), ((0, 0), ((0, 0), (1, 0))))
        self.assertIn('--cell 0,0 --layers trees', format_drift(drift, fix=True))

    def test_dry_run_skips_a_superseded_mismatch(self):
        self.mismatch()
        self.world.reg.change(Build('patch', 'feature', self.HOUSE, modifies=('house',), cell=(0, 0)),
                              self.world.apply)
        self.assertEqual(self.world.reg.reconcile().by_kind('mismatched'), ())



class StaleGroundTest(unittest.TestCase):
    """Task 285: a feature that lands after the cell's ground leaves that ground stale."""

    HOUSE = {(4, 61, 4): 'minecraft:bricks'}
    CAUSE = r'^ground \d{4}-\d\d-\d\dT\d\d:\d\d is older than feature house VERB \d{4}-\d\d-\d\dT\d\d:\d\d$'

    def setUp(self):
        from cell_readiness import ReadinessPolicy
        self.world = JournalWorld()
        self.addCleanup(self.world.__exit__)
        self.world.__enter__()
        from cell_journal import Note
        self.world.seed((0, 0), ghost_ground=True)
        self.world.reg.change(Note((0, 0), 'plan_feature', 'house', 'house'))
        self.policy = ReadinessPolicy(self.world.reg, Evidence())
        self.policy.validate((0, 0))
        self.world.reg.build_policy = self.policy.enforce

    def check(self, operation='build', **kw):
        return self.policy.check((0, 0), operation, **kw)

    def complete(self):
        return self.check()['complete']

    def test_feature_landed_after_ground_makes_ground_stale(self):
        from cell_readiness import format_readiness
        self.world.seed((0, 0), ground=True)
        self.world.seed((0, 0), features=('house',))
        report = self.check()
        self.assertEqual(len(report['ground_stale']), 1)
        self.assertRegex(report['ground_stale'][0], self.CAUSE.replace('VERB', 'landed'))
        self.assertTrue(report['ready'])                      # ground/feature work is never blocked
        self.assertIn('--cell 0,0 --layers ground --apply', report['ground_redo'])
        # trees/decor that landed over the stale ground (policy off, as in an older journal)
        self.world.reg.build_policy = None
        meta = {'readiness_revision': report['revision']}
        self.world.build('seed_0_0_trees', 'trees', {(1, 61, 8): 'minecraft:oak_log'}, cell=(0, 0), meta=meta)
        self.world.build('seed_0_0_decor', 'decor', {(2, 61, 9): 'minecraft:poppy'}, cell=(0, 0), meta=meta)
        report = self.check()
        self.assertTrue(report['trees_current'] and report['decor_current'])
        self.assertFalse(report['complete'])                  # the stale ground alone keeps it not DONE
        self.assertIn('--cell 0,0 --layers ground,trees,decor --apply', report['ground_redo'])
        text = format_readiness(report)
        self.assertIn('  ground_stale: ground ', text)
        self.assertIn('  clear: ground stale -> python3 scripts/redo_layer.py', text)
        trees = self.check('trees')
        self.assertFalse(trees['ready'])
        blocker = 'ground stale: ' + report['ground_stale'][0]
        self.assertIn(blocker, trees['blockers'])
        self.assertEqual(trees['clear_with'][blocker], report['ground_redo'])
        self.assertTrue(self.check('trees', redoing=('seed_0_0_ground',))['ready'])   # a ground redo first

    def test_ground_redone_after_the_feature_is_current(self):
        self.world.seed((0, 0), ground=True)
        self.world.seed((0, 0), features=('house',))
        self.world.seed((0, 0), ground=True)
        self.world.seed((0, 0), trees=True, decor=True)
        report = self.check()
        self.assertEqual(report['ground_stale'], [])
        self.assertIsNone(report['ground_redo'])
        self.assertTrue(self.complete())

    def test_feature_landed_before_ground_does_not(self):
        self.world.seed((0, 0), features=('house',))
        self.world.seed((0, 0), ground=True, trees=True, decor=True)
        self.assertEqual(self.check()['ground_stale'], [])
        self.assertTrue(self.complete())

    def test_undone_feature_entries_are_ignored(self):
        from cell_journal import Undo
        self.world.seed((0, 0), ground=True)
        self.world.build('house', 'feature', self.HOUSE, cell=(0, 0))
        self.assertTrue(self.check()['ground_stale'])
        self.world.reg.change(Undo('house'), self.world.apply)
        self.assertEqual(self.check()['ground_stale'], [])

    def test_mismatched_feature_entry_is_reported(self):
        self.world.seed((0, 0), ground=True)
        receipt = self.world.reg.change(Build('house', 'feature', self.HOUSE, cell=(0, 0)), lambda w: None)
        self.assertEqual(receipt.status, 'mismatched')
        self.assertRegex(self.check()['ground_stale'][0], self.CAUSE.replace('VERB', 'applied mismatched'))

    def test_begun_feature_entry_is_reported(self):
        self.world.seed((0, 0), ground=True)
        self.world.reg.begin(Build('house', 'feature', self.HOUSE, cell=(0, 0)))
        report = self.check()
        self.assertRegex(report['ground_stale'][0], self.CAUSE.replace('VERB', r'begun \(unfinished\)'))
        self.assertIn('recovery: unfinished apply', report['blockers'])

    def test_reconcile_landing_an_old_ground_later_does_not_hide_a_later_feature(self):
        """48,-44: reconcile --fix upgraded a 10-27 ground on 11-14; the 10-29 feature is still later."""
        self.world.reg.build_policy = None
        receipt = self.world.reg.change(Build('seed_0_0_ground', 'ground', {(0, 60, 0): 'minecraft:grass_block'},
                                              cell=(0, 0)), lambda w: None)
        self.assertEqual(receipt.status, 'mismatched')
        self.world.build('house', 'feature', self.HOUSE, cell=(0, 0))
        self.world.reg.reconcile([(0, 0)], fix=True, apply=self.world.apply)
        ground = self.world.reg.entries(plan='seed_0_0_ground')[-1]
        self.assertEqual(ground.status, 'landed')
        self.assertGreater(ground.finish_seq, self.world.reg.entries(plan='house')[-1].finish_seq)
        self.assertRegex(self.check()['ground_stale'][0], self.CAUSE.replace('VERB', 'landed'))

    def test_neighbour_feature_touching_the_cell_counts_but_not_untouched_cells(self):
        self.world.reg.build_policy = None                  # the neighbour is not prepared here
        self.world.seed((0, 0), ground=True)
        self.world.seed((1, 0), ground=True)
        self.world.build('wide', 'feature', {(127, 61, 5): 'minecraft:bricks',
                                             (128, 61, 5): 'minecraft:bricks'}, cell=(1, 0))
        self.assertIn('feature wide landed', self.check()['ground_stale'][0])
        self.world.seed((0, 1), ground=True)
        self.assertEqual(self.policy.check((0, 1))['ground_stale'], [])

    def test_morning_report_names_the_stale_ground(self):
        from unittest import mock
        import site_status
        self.world.seed((0, 0), ground=True)
        self.world.seed((0, 0), features=('house',))
        with mock.patch('cell_registry.view_registry', return_value=self.world.reg):
            lines = site_status.stale_ground_lines([(0, 0), (5, 5)])
        self.assertEqual(len(lines), 2)
        self.assertRegex(lines[0], r'^  0,0  ground .* is older than feature house landed ')
        self.assertIn('0,0  clear: ground stale -> python3 scripts/redo_layer.py --world <world> --cell 0,0 '
                      '--layers ground --apply', lines[1])
