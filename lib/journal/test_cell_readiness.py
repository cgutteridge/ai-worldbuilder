"""Readiness through named-cell checks and the journal's guarded build boundary."""
from __future__ import annotations

import unittest

from cell_journal import Build, Note, Refused
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


def layer_command_preflight(command, world, policy):
    """Run redo_layer's real preflight, substituting synthetic identity/evidence and absent masks."""
    import shlex
    from unittest import mock
    from layer_redo import LayerCommands
    argv = shlex.split(command)
    cell = tuple(map(int, argv[argv.index('--cell') + 1].split(',')))
    layers = argv[argv.index('--layers') + 1].split(',')
    with mock.patch('world_discovery.require_running_world'), \
            mock.patch('cell_registry.view_registry', return_value=world.reg), \
            mock.patch('readiness_evidence.policy_for', return_value=policy), \
            mock.patch.object(LayerCommands, 'has_mask', return_value=False):
        LayerCommands('unit-test', cell).preflight(cell, layers, [], False)


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

    def _cross_cell_pond(self):
        """Pond planned in its home cell 0,0; neighbour 1,0 has ghost ground and other groups."""
        from cell_journal import Note
        from cell_readiness import ReadinessPolicy
        self.world.seed((0, 0), ghost_ground=True)
        self.world.seed((1, 0), ghost_ground=True)
        self.world.reg.change(Note((0, 0), 'plan_feature', 'pond (67 columns)', 'w5'))
        policy = ReadinessPolicy(self.world.reg, self.evidence)
        policy.validate((0, 0))
        others = [{'id': f'w{n}', 'kind': 'building', 'refs': [f'w{n}'], 'label': 'hall'} for n in (7, 8)]
        pond = Build('w5', 'feature', {(120, 60, 1): 'minecraft:water', (121, 60, 1): 'minecraft:water',
                                       (128, 60, 1): 'minecraft:water'})
        return policy, others, pond

    def test_cross_cell_feature_is_accounted_in_the_neighbour(self):
        """Task 288: a feature planned in its home cell is not unaccounted in the neighbour."""
        policy, others, _ = self._cross_cell_pond()
        self.evidence.items = others + [{'id': 'w5', 'kind': 'water_body', 'refs': ['w5'], 'label': 'pond'}]
        report = policy.check((1, 0))
        self.assertEqual([c['id'] for c in report['candidates']], ['w7', 'w8'])
        self.assertIn('feature plans: 2 eligible groups unaccounted for', report['blockers'])

    def test_cross_cell_feature_dismissed_in_its_home_cell_is_accounted_in_the_neighbour(self):
        """Task 288 follow-up: a map-ref feature dismissed at home is dismissed everywhere; houses are not."""
        from cell_journal import Note
        policy, others, _ = self._cross_cell_pond()
        self.world.reg.change(Note((0, 0), 'plan_feature', 'hall', 'w7'))
        self.world.reg.change(Note((0, 0), 'plan_feature-dismiss', 'not a build', 'w7'))
        group = {'id': 'st-1', 'kind': 'houses', 'refs': ['w11'], 'label': 'terrace'}
        self.evidence.items = others + [group]
        report = policy.check((1, 0))
        self.assertEqual([c['id'] for c in report['candidates']], ['w8', 'st-1'])

    def test_cross_cell_feature_applies_with_neighbour_validated_and_its_groups_unplanned(self):
        """Task 288: a validated neighbour's other unplanned groups do not stop the feature."""
        policy, others, pond = self._cross_cell_pond()
        policy.validate((1, 0))
        self.evidence.items = others
        self.world.reg.build_policy = policy.enforce
        self.assertEqual(self.world.reg.change(pond, self.world.apply).status, 'landed')

    def test_cross_cell_feature_refusal_names_only_neighbour_validation(self):
        """Tasks 288, 292: an unchecked neighbour refuses with evidence validation and its clear: line only."""
        policy, others, pond = self._cross_cell_pond()
        self.evidence.items = others
        self.world.reg.build_policy = policy.enforce
        before = self.world.reg.journal_path.read_bytes()
        with self.assertRaises(Refused) as caught:
            self.world.reg.begin(pond)
        self.assertEqual(str(caught.exception), 'needs-prep 1,0: evidence validation required '
                         '[clear: python3 scripts/cell_registry.py --world <world> prepare --cell 1,0 '
                         '--apply --evidence-only (records no feature plans)]')
        self.assertEqual(before, self.world.reg.journal_path.read_bytes())

    def test_cross_cell_feature_applies_with_neighbour_evidence_current_and_groups_unaccounted(self):
        """Task 292: an evidence check on the neighbour is enough; its groups stay unplanned."""
        policy, others, pond = self._cross_cell_pond()
        self.evidence.items = others
        policy.validate_evidence((1, 0))
        self.assertFalse(policy.check((1, 0), require_validation=False)['validated'])
        self.world.reg.build_policy = policy.enforce
        self.assertEqual(self.world.reg.change(pond, self.world.apply).status, 'landed')
        self.assertEqual(self.world.reg.notes((1, 0), kinds=('plan_feature', 'readiness')), ())

    def test_cross_cell_feature_refuses_stale_neighbour_evidence_with_evidence_only_clear(self):
        """Task 292/232: check names the stale neighbour; apply refreshes when checks still pass."""
        from cell_readiness import describe_blockers
        policy, others, pond = self._cross_cell_pond()
        self.evidence.items = others
        policy.validate_evidence((1, 0))
        self.evidence.revision = 'evidence-2'
        self.evidence.items = []
        policy.validate((0, 0))
        self.evidence.items = others
        report = policy.check((1, 0), 'feature', neighbour=True)
        self.assertEqual(report['next_actions'], ['prepare --cell 1,0 --apply --evidence-only'])
        self.assertIn('evidence validation stale', describe_blockers(report))
        self.world.reg.build_policy = policy.enforce
        self.assertEqual(self.world.reg.change(pond, self.world.apply).status, 'landed')
        self.assertTrue(policy.check((1, 0), neighbour=True)['evidence_validated'])

    def test_evidence_only_prepare_writes_no_plan_notes(self):
        """Task 292: dry run says it records no plans; apply writes only a readiness_evidence note."""
        from cell_preparation import prepare_cell
        from cell_readiness import format_readiness
        from unittest import mock
        policy, others, _ = self._cross_cell_pond()
        self.evidence.items = others
        repairs = mock.Mock()
        repairs.preflight.return_value = {'blockers': [], 'inputs': []}
        self.evidence.calls = 0
        before = self.world.reg.journal_path.read_bytes()
        dry = prepare_cell(self.world.reg, self.evidence, (1, 0), repairs=repairs, evidence_only=True)
        self.assertEqual(before, self.world.reg.journal_path.read_bytes())
        self.assertEqual((dry['blockers'], dry['prepare_actions'], dry['candidates']), ([], [], []))
        self.assertIn('records no feature plans', format_readiness(dry))
        report = prepare_cell(self.world.reg, self.evidence, (1, 0), apply=True, repairs=repairs,
                              check_lock=lambda: None, evidence_only=True)
        self.assertTrue(report['ready'] and report['evidence_validated'])
        self.assertEqual(self.evidence.calls, 0)
        kinds = [n['kind'] for n in self.world.reg.notes((1, 0))]
        self.assertEqual(kinds, ['readiness_evidence'])
        repeat = prepare_cell(self.world.reg, self.evidence, (1, 0), apply=True, repairs=repairs,
                              check_lock=lambda: None, evidence_only=True)
        self.assertTrue(repeat['ready'])
        self.assertEqual([n['kind'] for n in self.world.reg.notes((1, 0))], ['readiness_evidence'])

    def test_neighbour_own_feature_build_still_needs_full_validation(self):
        """Task 292: an evidence check does not let the neighbour build its own features."""
        from cell_journal import Note
        policy, others, _ = self._cross_cell_pond()
        self.evidence.items = others
        policy.validate_evidence((1, 0))
        self.world.reg.change(Note((1, 0), 'plan_feature', 'hall', 'w7'))
        self.world.reg.build_policy = policy.enforce
        hall = Build('w7', 'feature', {(140, 65, 1): 'minecraft:bricks'}, cell=(1, 0))
        with self.assertRaises(Refused) as caught:
            self.world.reg.begin(hall)
        self.assertIn('needs-prep 1,0: validation required', str(caught.exception))
        self.assertIn('feature plans: 1 eligible groups unaccounted for', str(caught.exception))
        self.assertIn('prepare --cell 1,0 --apply]', str(caught.exception))

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

    def test_candidates_see_the_whole_pond_and_each_cell_counts_its_clip(self):
        """#321: a pond over a cell line is one clip candidate per cell, banked on the whole pond."""
        from types import SimpleNamespace
        from readiness_evidence import LocalEvidence
        ring = [(6141., -5600.), (6151., -5600.), (6151., -5590.), (6141., -5590.), (6141., -5600.)]
        props = {'natural': 'water', 'osm_way_id': '147601601'}
        evidence = LocalEvidence(self.world.root)
        evidence._index = SimpleNamespace(areas=[([ring], props)], buildings=[], streets=[])
        east = evidence.candidates(self.world.reg, (48, -44))
        west = evidence.candidates(self.world.reg, (47, -44))
        self.assertEqual([(c['id'], c['area']) for c in east], [('w147601601_48_m44', 70)])
        self.assertEqual([(c['id'], c['area']) for c in west], [('w147601601_47_m44', 30)])
        self.assertEqual(len(east[0]['outline']), 100)   # whole pond within reach of the cell
        self.world.reg.change(Note((48, -44), 'plan_feature', 'clip', 'w147601601_48_m44'))
        self.assertEqual(evidence.candidates(self.world.reg, (48, -44)), [])
        self.assertEqual([c['id'] for c in evidence.candidates(self.world.reg, (47, -44))],
                         ['w147601601_47_m44'])

    def test_candidates_include_a_bank_only_clip_next_to_the_water(self):
        """#321: a cell with none of a river's water still counts its bank clip when the water is near."""
        from types import SimpleNamespace
        from readiness_evidence import LocalEvidence
        ring = [(6141., -5632.), (6151., -5632.), (6151., -5629.), (6141., -5629.), (6141., -5632.)]
        evidence = LocalEvidence(self.world.root)
        evidence._index = SimpleNamespace(areas=[([ring], {'natural': 'water', 'osm_way_id': '7'})],
                                          buildings=[], streets=[])
        self.assertEqual([c['id'] for c in evidence.candidates(self.world.reg, (48, -45))], ['w7_48_m45'])

    def test_local_evidence_houses_follow_brickfield_prepare_rules(self):
        """#233: LocalEvidence skips a spec-backed house plan under another id; warns only on legacy."""
        import json
        from types import SimpleNamespace
        from unittest import mock
        from cell_journal import Note
        from readiness_evidence import LocalEvidence

        cell = (48, -44)
        street = 'Brickfield Road'
        refs = ['w176633174', 'w176633175', 'w176633176',
                'w176633177', 'w176633178', 'w176633179']
        terrace = {
            'kind': 'houses', 'street': street, 'label': f'{street} (6 houses)',
            'refs': list(refs), 'bbox': (0, 0, 1, 1), 'area': 6, 'portals': [], 'pictures': [],
        }
        plan = 'houses_brickfield_road_w176633174'
        self.world.reg.change(Note(cell, 'plan_feature', terrace['label'], plan))
        builds = self.world.root / 'site' / 'builds'
        builds.mkdir(parents=True)
        (builds / f'{plan}.json').write_text(json.dumps({
            'name': plan,
            'ways': {r[1:]: f'H{r}' for r in refs},
        }))
        empty = {'built_area': 0, 'unbuilt_area': 6, 'unbuilt': [terrace], 'unbuilt_names': [terrace['label']]}
        evidence = LocalEvidence(self.world.root)
        evidence._index = SimpleNamespace(areas=[], buildings=[], streets=[])
        with mock.patch('cell_features.footprint_progress', return_value=empty):
            first = evidence.candidates(self.world.reg, cell)
            second = evidence.candidates(self.world.reg, cell)
        self.assertEqual(first, [])
        self.assertEqual(second, [])
        self.assertEqual(evidence.warnings(cell), [])

        # Unrelated new terrace on the same street still proposes; no legacy warning.
        other = {
            'kind': 'houses', 'street': street, 'label': f'{street} (2 houses)',
            'refs': ['w20', 'w21'], 'bbox': (0, 0, 1, 1), 'area': 2, 'portals': [], 'pictures': [],
        }
        both = {'built_area': 0, 'unbuilt_area': 8, 'unbuilt': [terrace, other],
                'unbuilt_names': [terrace['label'], other['label']]}
        with mock.patch('cell_features.footprint_progress', return_value=both):
            proposed = evidence.candidates(self.world.reg, cell)
        self.assertEqual([(c['id'], c['refs']) for c in proposed],
                         [('houses_brickfield_road_w20', ['w20', 'w21'])])
        self.assertEqual(evidence.warnings(cell), [])

        # Bare specless street plan is a genuine unresolved legacy collision.
        legacy = JournalWorld()
        with legacy:
            legacy.reg.change(Note(cell, 'plan_feature', 'legacy', 'houses_brickfield_road'))
            leg_ev = LocalEvidence(legacy.root)
            leg_ev._index = SimpleNamespace(areas=[], buildings=[], streets=[])
            with mock.patch('cell_features.footprint_progress', return_value=empty):
                self.assertEqual(leg_ev.candidates(legacy.reg, cell), [])
            self.assertEqual(leg_ev.warnings(cell), [
                "ambiguous legacy plan houses_brickfield_road: no readable handoff spec; "
                f"audit refs {refs}",
            ])

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
                mock.patch.object(aerial_coverage, 'coverage_counts', return_value=(16384, 0)), \
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

    def test_build_spec_writes_preserve_neighbour_evidence_between_feature_applies(self):
        import json
        from unittest import mock
        import aerial_coverage
        from cell_journal import Note
        from cell_readiness import ReadinessPolicy
        from readiness_evidence import LocalEvidence
        root = self.world.root
        frame = root / 'config' / 'world-frame.json'
        frame.parent.mkdir()
        frame.write_text(json.dumps({'offsets': {'easting': 0, 'northing': 0, 'sea_level': 0}}))
        clip = root / 'reference' / 'vector_map' / 'fixture' / 'buildings-bbox-0-0-255-127.geojson'
        clip.parent.mkdir(parents=True)
        clip.write_text('{"type":"FeatureCollection","features":[]}')
        for cell in ((0, 0), (1, 0)):
            self.world.seed(cell, ghost_ground=True)
        for fid in ('first', 'crossing'):
            self.world.reg.change(Note((0, 0), 'plan_feature', fid, fid))
        specs = root / 'site' / 'builds'
        specs.mkdir(parents=True)
        with mock.patch.object(LocalEvidence, 'lidar_status', return_value=([], [])), \
                mock.patch.object(aerial_coverage, 'coverage_counts', return_value=(16384, 0)), \
                mock.patch.object(aerial_coverage, 'mosaics', return_value=[]):
            policy = ReadinessPolicy(self.world.reg, LocalEvidence(root), require_plan_identity=True)
            policy.validate((0, 0))
            before = policy.validate_evidence((1, 0))['revision_parts']
            for fid, cells in (('first', {(5, 61, 5): 'minecraft:bricks'}),
                               ('crossing', {(127, 61, 5): 'minecraft:bricks',
                                             (128, 61, 5): 'minecraft:bricks'})):
                # A builder saves its handoff spec after preparation, then a fresh
                # apply adapter audits the inputs again under the lock.
                (specs / f'{fid}.json').write_text(json.dumps({'id': fid, 'height': 8}))
                policy = ReadinessPolicy(self.world.reg, LocalEvidence(root), require_plan_identity=True)
                report = policy.check((1, 0), neighbour=True)
                self.assertEqual(before, report['revision_parts'])
                self.assertTrue(report['ready'], report['blockers'])
                home = policy.check((0, 0))
                self.assertTrue(home['validated'], home['blockers'])
                import cellmath
                from cell_readiness import content_id
                touched = sorted({(0, 0)} | {cellmath.cell_of(x, z) for x, _y, z in cells})
                reports = [policy.check(c, neighbour=c != (0, 0)) for c in touched]
                evidence_ids = [r.get('source_revision', r['evidence_revision']) for r in reports]
                evidence = evidence_ids[0] if len(evidence_ids) == 1 else content_id(evidence_ids)
                journal = self.world.reg.planning_revision(touched)
                self.world.reg.build_policy = policy.enforce
                receipt = self.world.reg.change(Build(fid, 'feature', cells, cell=(0, 0),
                    meta={'evidence_revision': evidence, 'journal_revision': journal}), self.world.apply)
                self.assertEqual(receipt.status, 'landed')
            before = policy.check((1, 0), neighbour=True)['revision_parts']
            spec = specs / 'first.json'
            for contents in ('{"id":"first","height":12}', None):
                if contents is None:
                    spec.unlink()
                else:
                    spec.write_text(contents)
                policy = ReadinessPolicy(self.world.reg, LocalEvidence(root))
                report = policy.check((1, 0), neighbour=True)
                self.assertEqual(before, report['revision_parts'])
                self.assertTrue(report['ready'], report['blockers'])

    def test_source_revision_tracks_evidence_files_and_additions(self):
        from readiness_evidence import LocalEvidence
        root = self.world.root
        previous = LocalEvidence(root).source_revision()
        for relative in ('config/world-frame.json', 'reference/map.geojson',
                         'reference/aerial.png', 'downloads/terrain.tif'):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            for contents in (b'original evidence', b'changed evidence', None):
                with self.subTest(path=relative, contents=contents):
                    if contents is None:
                        path.unlink()
                    else:
                        path.write_bytes(contents)
                    revision = LocalEvidence(root).source_revision()
                    self.assertNotEqual(previous, revision)
                    previous = revision

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

    def test_neighbour_map_extract_does_not_stale_this_cell_readiness_or_plan(self):
        """Task 232: unrelated neighbouring evidence must not invalidate readiness or an unapplied plan.

        Diagnosis (cell 53,-42 on 2026-10-31): readiness was re-accepted solely after neighbour
        54,-42 prepares, with no local plan-identity change. Notes predating #289 omit revision_parts;
        the code path that fits is world-wide source_revision picking up the neighbour's prepared-*
        extract, which #289 would report as evidence facts changed.
        """
        import json
        from unittest import mock
        import aerial_coverage
        from cell_journal import Note
        from cell_readiness import ReadinessPolicy
        from readiness_evidence import LocalEvidence
        root = self.world.root
        frame = root / 'config' / 'world-frame.json'
        frame.parent.mkdir()
        frame.write_text(json.dumps({'offsets': {'easting': 0, 'northing': 0, 'sea_level': 0}}))
        home = root / 'reference' / 'vector_map' / 'prepared-0-0'
        home.mkdir(parents=True)
        (home / 'buildings+highways-cell-0-0.geojson').write_text(
            '{"type":"FeatureCollection","features":[]}')
        self.world.seed((0, 0), ghost_ground=True)
        self.world.reg.change(Note((0, 0), 'plan_feature', 'house', 'house'))
        with mock.patch.object(LocalEvidence, 'lidar_status', return_value=([], [])), \
                mock.patch.object(aerial_coverage, 'coverage_counts', return_value=(16384, 0)), \
                mock.patch.object(aerial_coverage, 'mosaics', return_value=[]):
            policy = ReadinessPolicy(self.world.reg, LocalEvidence(root), require_plan_identity=True)
            home_report = policy.validate((0, 0))
            before = home_report['revision_parts']
            neighbour = root / 'reference' / 'vector_map' / 'prepared-2-0'
            neighbour.mkdir(parents=True)
            (neighbour / 'buildings+highways-cell-256-0.geojson').write_text(
                '{"type":"FeatureCollection","features":[{"type":"Feature","properties":'
                '{"building":"yes","osm_way_id":"9"},"geometry":{"type":"Polygon","coordinates":'
                '[[[0,0],[1,0],[1,1],[0,0]]]}}]}')
            self.world.reg.change(Note((2, 0), 'plan_feature', 'far_house', 'far house'))
            self.world.seed((2, 0), ghost_ground=True, ground=True)
            policy = ReadinessPolicy(self.world.reg, LocalEvidence(root), require_plan_identity=True)
            report = policy.check((0, 0))
            self.assertEqual(before, report['revision_parts'])
            self.assertTrue(report['validated'], report['blockers'])
            self.world.reg.build_policy = policy.enforce
            receipt = self.world.reg.change(
                Build('house', 'feature', {(5, 61, 5): 'minecraft:bricks'}, cell=(0, 0),
                      meta={'evidence_revision': home_report['evidence_revision'],
                            'journal_revision': home_report['journal_revision']}),
                self.world.apply)
            self.assertEqual(receipt.status, 'landed')

    def test_overlapping_neighbour_clip_stales_evidence_and_refuses_old_plan(self):
        """A map clip that covers this cell is a real dependency; the old plan must re-plan."""
        import json
        from unittest import mock
        import aerial_coverage
        from cell_readiness import ReadinessPolicy
        from readiness_evidence import LocalEvidence
        root = self.world.root
        frame = root / 'config' / 'world-frame.json'
        frame.parent.mkdir()
        frame.write_text(json.dumps({'offsets': {'easting': 0, 'northing': 0, 'sea_level': 0}}))
        clip_dir = root / 'reference' / 'vector_map' / 'shared'
        clip_dir.mkdir(parents=True)
        clip = clip_dir / 'buildings-bbox-0-0-255-127.geojson'
        clip.write_text('{"type":"FeatureCollection","features":[],"rev":1}')
        self.world.seed((0, 0), ghost_ground=True)
        with mock.patch.object(LocalEvidence, 'lidar_status', return_value=([], [])), \
                mock.patch.object(aerial_coverage, 'coverage_counts', return_value=(16384, 0)), \
                mock.patch.object(aerial_coverage, 'mosaics', return_value=[]):
            policy = ReadinessPolicy(self.world.reg, LocalEvidence(root), require_plan_identity=True)
            stamped = policy.validate((0, 0))
            clip.write_text('{"type":"FeatureCollection","features":[],"rev":2}')
            policy = ReadinessPolicy(self.world.reg, LocalEvidence(root), require_plan_identity=True)
            self.assertIn('validation stale (evidence facts changed)', policy.check((0, 0))['blockers'])
            self.world.reg.build_policy = policy.enforce
            with self.assertRaisesRegex(Refused, 'stale plan: evidence changed'):
                self.world.reg.begin(Build('ground', 'ground', {(5, 60, 5): 'minecraft:grass_block'},
                                           cell=(0, 0),
                                           meta={'evidence_revision': stamped['evidence_revision'],
                                                 'journal_revision': stamped['journal_revision']}))

    def test_apply_refreshes_stale_validation_under_the_lock_when_checks_pass(self):
        """Task 232: apply re-checks readiness and rewrites validation when only the note is stale."""
        from cell_journal import Note
        from cell_readiness import ReadinessPolicy, content_id
        self.world.seed((0, 0), ghost_ground=True)
        policy = ReadinessPolicy(self.world.reg, self.evidence, require_plan_identity=True)
        stamped = policy.validate((0, 0))
        # Simulate an accepted note whose combined revision drifted while inputs still pass checks
        # (e.g. a neighbour session that no longer affects cell-scoped facts after the narrow fix).
        parts = dict(stamped['revision_parts'])
        stale_revision = content_id(['not', parts['policy'], parts['evidence'], parts['plans']])
        self.world.reg.change(Note((0, 0), 'readiness',
                                   __import__('json').dumps({'revision': stale_revision,
                                                             'revision_parts': parts,
                                                             'policy': parts['policy'],
                                                             'warnings': []})))
        self.assertFalse(policy.check((0, 0))['validated'])
        self.world.reg.build_policy = policy.enforce
        receipt = self.world.reg.change(
            Build('ground', 'ground', {(5, 60, 5): 'minecraft:grass_block'}, cell=(0, 0),
                  meta={'evidence_revision': stamped['evidence_revision'],
                        'journal_revision': stamped['journal_revision']}),
            self.world.apply)
        self.assertEqual(receipt.status, 'landed')
        self.assertTrue(policy.check((0, 0))['validated'])

    def test_apply_refresh_preserves_hold_and_never_approves_outdated_plan(self):
        """Refreshing validation must not clear holds or bless a plan with stale identities."""
        from cell_journal import Note
        from cell_readiness import ReadinessPolicy, content_id
        self.world.seed((0, 0), ghost_ground=True)
        policy = ReadinessPolicy(self.world.reg, self.evidence, require_plan_identity=True)
        stamped = policy.validate((0, 0))
        parts = dict(stamped['revision_parts'])
        stale_revision = content_id(['drift', parts])
        self.world.reg.change(Note((0, 0), 'readiness',
                                   __import__('json').dumps({'revision': stale_revision,
                                                             'revision_parts': parts,
                                                             'policy': parts['policy'],
                                                             'warnings': []})))
        self.world.reg.change(Note((0, 0), 'hold', 'owner reviewing'))
        self.world.reg.build_policy = policy.enforce
        before = self.world.reg.journal_path.read_bytes()
        with self.assertRaisesRegex(Refused, r'needs-prep 0,0:.*hold:'):
            self.world.reg.begin(Build('ground', 'ground', {(5, 60, 5): 'minecraft:grass_block'},
                                       cell=(0, 0),
                                       meta={'evidence_revision': stamped['evidence_revision'],
                                             'journal_revision': stamped['journal_revision']}))
        self.assertEqual(before, self.world.reg.journal_path.read_bytes())
        self.world.reg.change(Note((0, 0), 'hold', ''))
        with self.assertRaisesRegex(Refused, 'stale plan: evidence changed'):
            self.world.reg.begin(Build('ground', 'ground', {(5, 60, 5): 'minecraft:grass_block'},
                                       cell=(0, 0),
                                       meta={'evidence_revision': 'old-evidence',
                                             'journal_revision': stamped['journal_revision']}))

    def _local_plan_world(self):
        """Temp world root with frame + home clip; bind WORLD_ROOT for Plan stamps."""
        import json
        from unittest import mock
        import active_world
        import aerial_coverage
        import world_frame
        from readiness_evidence import LocalEvidence
        root = self.world.root
        frame = root / 'config' / 'world-frame.json'
        frame.parent.mkdir(exist_ok=True)
        frame.write_text(json.dumps({'offsets': {'easting': 0, 'northing': 0, 'sea_level': 0}}))
        home = root / 'reference' / 'vector_map' / 'prepared-0-0'
        home.mkdir(parents=True, exist_ok=True)
        (home / 'buildings+highways-cell-0-0.geojson').write_text(
            '{"type":"FeatureCollection","features":[]}')
        previous_root = active_world.WORLD_ROOT
        active_world.set_world_root(root)
        world_frame.clear_frame_cache()

        def restore_world():
            active_world.WORLD_ROOT = previous_root
            world_frame.clear_frame_cache()

        self.addCleanup(restore_world)
        patches = (
            mock.patch.object(LocalEvidence, 'lidar_status', return_value=([], [])),
            mock.patch.object(aerial_coverage, 'coverage_counts', return_value=(16384, 0)),
            mock.patch.object(aerial_coverage, 'mosaics', return_value=[]),
        )
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return root

    def test_plan_stamp_apply_survives_neighbour_prepare(self):
        """Task 232: Plan.capture_inputs → apply still lands after an unrelated neighbour extract."""
        import site_kit
        from cell_journal import Note
        from cell_readiness import ReadinessPolicy
        from readiness_evidence import LocalEvidence
        root = self._local_plan_world()
        self.world.seed((0, 0), ghost_ground=True)
        self.world.reg.change(Note((0, 0), 'plan_feature', 'house', 'house'))
        policy = ReadinessPolicy(self.world.reg, LocalEvidence(root), require_plan_identity=True)
        policy.validate((0, 0))
        plan = site_kit.Plan('house', input_cells=[(0, 0)])
        plan.set(5, 61, 5, 'bricks')
        plan.capture_inputs()
        neighbour = root / 'reference' / 'vector_map' / 'prepared-2-0'
        neighbour.mkdir(parents=True)
        (neighbour / 'buildings+highways-cell-256-0.geojson').write_text(
            '{"type":"FeatureCollection","features":[]}')
        self.world.seed((2, 0), ghost_ground=True, ground=True)
        policy = ReadinessPolicy(self.world.reg, LocalEvidence(root), require_plan_identity=True)
        self.world.reg.build_policy = policy.enforce
        receipt = self.world.reg.change(
            Build('house', 'feature', plan.cells, cell=(0, 0), payload=plan), self.world.apply)
        self.assertEqual(receipt.status, 'landed')

    def test_plan_stamp_apply_refuses_relevant_evidence_change(self):
        """Task 232: a Plan stamped before an overlapping clip change must re-plan."""
        import site_kit
        from cell_readiness import ReadinessPolicy
        from readiness_evidence import LocalEvidence
        root = self._local_plan_world()
        clip_dir = root / 'reference' / 'vector_map' / 'shared'
        clip_dir.mkdir(parents=True)
        clip = clip_dir / 'buildings-bbox-0-0-255-127.geojson'
        clip.write_text('{"type":"FeatureCollection","features":[],"rev":1}')
        self.world.seed((0, 0), ghost_ground=True)
        policy = ReadinessPolicy(self.world.reg, LocalEvidence(root), require_plan_identity=True)
        policy.validate((0, 0))
        plan = site_kit.Plan('ground', input_cells=[(0, 0)])
        plan.set(5, 60, 5, 'grass_block')
        plan.capture_inputs()
        clip.write_text('{"type":"FeatureCollection","features":[],"rev":2}')
        policy = ReadinessPolicy(self.world.reg, LocalEvidence(root), require_plan_identity=True)
        self.world.reg.build_policy = policy.enforce
        with self.assertRaisesRegex(Refused, 'stale plan: evidence changed'):
            self.world.reg.begin(Build('ground', 'ground', plan.cells, cell=(0, 0), payload=plan))

    def test_plan_stamp_includes_cells_touched_by_overhang(self):
        """Task 232: capture_inputs after planning covers roof overhang across a cell line."""
        import site_kit
        from cell_journal import Note
        from cell_readiness import ReadinessPolicy
        from readiness_evidence import LocalEvidence
        root = self._local_plan_world()
        # Neighbouring cell also needs a clip so evidence facts exist for the overhang.
        other = root / 'reference' / 'vector_map' / 'prepared-1-0'
        other.mkdir(parents=True)
        (other / 'buildings+highways-cell-128-0.geojson').write_text(
            '{"type":"FeatureCollection","features":[]}')
        self.world.seed((0, 0), ghost_ground=True)
        self.world.seed((1, 0), ghost_ground=True)
        self.world.reg.change(Note((0, 0), 'plan_feature', 'overhang', 'overhang'))
        policy = ReadinessPolicy(self.world.reg, LocalEvidence(root), require_plan_identity=True)
        policy.validate((0, 0))
        policy.validate_evidence((1, 0))
        plan = site_kit.Plan('overhang', input_cells=[(0, 0)])
        plan.set(120, 61, 5, 'bricks')   # home cell
        plan.set(130, 61, 5, 'bricks')   # overhang into (1, 0)
        # Footprint-only stamp would omit (1, 0); re-stamp from finished geometry.
        plan.capture_inputs()
        self.assertEqual(set(plan._input_cells), {(0, 0), (1, 0)})
        policy = ReadinessPolicy(self.world.reg, LocalEvidence(root), require_plan_identity=True)
        self.world.reg.build_policy = policy.enforce
        receipt = self.world.reg.change(
            Build('overhang', 'feature', plan.cells, cell=(0, 0), payload=plan), self.world.apply)
        self.assertEqual(receipt.status, 'landed')

    def test_legacy_whole_world_plan_name_still_applies(self):
        """Task 232: Plan(name) without a cell list still applies via the whole-world stamp fallback."""
        import site_kit
        from cell_journal import Note
        from cell_readiness import ReadinessPolicy
        from readiness_evidence import LocalEvidence
        root = self._local_plan_world()
        self.world.seed((0, 0), ghost_ground=True)
        self.world.reg.change(Note((0, 0), 'plan_feature', 'hand', 'hand'))
        policy = ReadinessPolicy(self.world.reg, LocalEvidence(root), require_plan_identity=True)
        policy.validate((0, 0))
        plan = site_kit.Plan('hand')  # whole-world stamp; no capture_inputs
        plan.set(5, 61, 5, 'bricks')
        self.assertIsNone(plan._input_cells)
        self.world.reg.build_policy = policy.enforce
        receipt = self.world.reg.change(
            Build('hand', 'feature', plan.cells, cell=(0, 0), payload=plan), self.world.apply)
        self.assertEqual(receipt.status, 'landed')

    def test_feature_reapply_with_smaller_footprint_is_not_stale_forever(self):
        """Task 232: re-apply that drops neighbour overhang must not refuse against previous.cells."""
        import site_kit
        from cell_journal import Note
        from cell_readiness import ReadinessPolicy
        from readiness_evidence import LocalEvidence
        root = self._local_plan_world()
        other = root / 'reference' / 'vector_map' / 'prepared-1-0'
        other.mkdir(parents=True)
        (other / 'buildings+highways-cell-128-0.geojson').write_text(
            '{"type":"FeatureCollection","features":[]}')
        self.world.seed((0, 0), ghost_ground=True)
        self.world.seed((1, 0), ghost_ground=True)
        self.world.reg.change(Note((0, 0), 'plan_feature', 'hall', 'hall'))
        policy = ReadinessPolicy(self.world.reg, LocalEvidence(root), require_plan_identity=True)
        policy.validate((0, 0))
        policy.validate_evidence((1, 0))
        wide = site_kit.Plan('hall', input_cells=[(0, 0)])
        wide.set(120, 61, 5, 'bricks')
        wide.set(130, 61, 5, 'bricks')  # overhang into (1, 0)
        wide.capture_inputs()
        self.assertEqual(set(wide._input_cells), {(0, 0), (1, 0)})
        self.world.reg.build_policy = policy.enforce
        self.assertEqual(
            self.world.reg.change(Build('hall', 'feature', wide.cells, cell=(0, 0), payload=wide),
                                  self.world.apply).status,
            'landed')
        # Fresh plan with a smaller footprint; re-apply still touches previous.cells for undo.
        narrow = site_kit.Plan('hall', input_cells=[(0, 0)])
        narrow.set(120, 61, 5, 'bricks')
        narrow.capture_inputs()
        self.assertEqual(set(narrow._input_cells), {(0, 0)})
        policy = ReadinessPolicy(self.world.reg, LocalEvidence(root), require_plan_identity=True)
        self.world.reg.build_policy = policy.enforce
        receipt = self.world.reg.change(
            Build('hall', 'feature', narrow.cells, cell=(0, 0), payload=narrow, modifies=('hall',)),
            self.world.apply)
        self.assertEqual(receipt.status, 'landed')

    def test_shared_reference_evidence_change_refuses_old_plan(self):
        """Task 232: non-clip reference (decor/trees/…) must still stale scoped plan stamps."""
        import site_kit
        from cell_readiness import ReadinessPolicy
        from readiness_evidence import LocalEvidence
        root = self._local_plan_world()
        decor = root / 'reference' / 'decor-furniture.json'
        decor.write_text('{"items":[],"rev":1}')
        self.world.seed((0, 0), ghost_ground=True)
        policy = ReadinessPolicy(self.world.reg, LocalEvidence(root), require_plan_identity=True)
        policy.validate((0, 0))
        plan = site_kit.Plan('ground', input_cells=[(0, 0)])
        plan.set(5, 60, 5, 'grass_block')
        plan.capture_inputs()
        decor.write_text('{"items":[],"rev":2}')
        policy = ReadinessPolicy(self.world.reg, LocalEvidence(root), require_plan_identity=True)
        self.assertIn('validation stale (evidence facts changed)', policy.check((0, 0))['blockers'])
        self.world.reg.build_policy = policy.enforce
        with self.assertRaisesRegex(Refused, 'stale plan: evidence changed'):
            self.world.reg.begin(Build('ground', 'ground', plan.cells, cell=(0, 0), payload=plan))


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


class ClearingCommandPreflightTest(unittest.TestCase):
    """Task 290: exercise the named route, with only external I/O replaced."""

    def setUp(self):
        from cell_readiness import ReadinessPolicy
        self.world = JournalWorld()
        self.addCleanup(self.world.__exit__)
        self.world.__enter__()
        self.world.seed((0, 0), ghost_ground=True)
        self.evidence = Evidence()
        self.policy = ReadinessPolicy(self.world.reg, self.evidence)
        self.policy.validate((0, 0))

    def command(self, prefix, operation='build', **kwargs):
        report = self.policy.check((0, 0), operation, **kwargs)
        return next(cmd for blocker, cmd in report['clear_with'].items() if blocker.startswith(prefix))

    def registry_command(self, command):
        import contextlib
        import io
        import shlex
        from unittest import mock
        import cell_registry
        # Run the first command, stripping its explanatory prose and filling the explicit placeholders.
        argv = shlex.split(command.split(' (')[0].split(', then')[0]
                           .replace('<world>', 'unit-test').replace('<why>', 'fixture reason'))
        self.assertEqual(argv[:2], ['python3', 'scripts/cell_registry.py'])
        self.assertNotIn('--apply', argv)
        out = io.StringIO()
        with mock.patch.object(cell_registry, 'activate_world_from_args'), \
                mock.patch.object(cell_registry, 'view_registry', return_value=self.world.reg), \
                mock.patch('readiness_evidence.policy_for', return_value=self.policy), \
                mock.patch('cell_features.clip_coverage', return_value=self.evidence.fraction), \
                mock.patch.object(cell_registry, 'propose_feature_candidates', return_value=self.evidence.items), \
                contextlib.redirect_stdout(out):
            self.assertEqual(cell_registry.main(argv[2:]), 0, out.getvalue())
        return out.getvalue()

    def test_hold_clear_is_accepted_with_a_hold(self):
        from cell_journal import Note
        self.world.reg.change(Note((0, 0), 'hold', 'owner requests pause'))
        self.registry_command(self.command('hold:'))
        self.assertIsNone(self.world.reg.cell((0, 0)).hold)

    def test_coverage_dismissal_is_accepted_for_the_named_warning(self):
        from cell_journal import Note
        self.world.reg.change(Note((0, 0), 'coverage', 'gap', 'gap-id'))
        self.registry_command(self.command('coverage warning'))
        self.assertFalse(any(b.startswith('coverage warning') for b in self.policy.check((0, 0))['blockers']))

    def test_clip_dismissal_is_accepted_for_incomplete_coverage(self):
        self.evidence.fraction = .5
        self.registry_command(self.command('map query coverage'))
        self.assertTrue(self.policy.check((0, 0), require_validation=False)['ready'])

    def test_stale_clip_dismissal_can_be_renewed(self):
        from cell_journal import Note
        self.evidence.fraction = .5
        self.world.reg.change(Note((0, 0), 'clip_coverage-dismiss', 'accepted gap', 'old-map'))
        self.registry_command(self.command('map coverage dismissal stale'))
        self.assertTrue(self.policy.check((0, 0), require_validation=False)['ready'])

    def test_complete_clip_does_not_offer_a_dismissal_that_its_command_refuses(self):
        from cell_journal import Note
        self.world.reg.change(Note((0, 0), 'clip_coverage-dismiss', 'accepted old gap', 'old-map'))
        report = self.policy.check((0, 0))
        self.assertNotIn('map coverage dismissal stale', report['blockers'])
        self.assertFalse(any('dismiss-clip-coverage' in cmd for cmd in report['clear_with'].values()))

    def test_propose_features_can_record_the_unaccounted_candidate(self):
        self.evidence.items = [{'id': 'w1', 'refs': ['w1'], 'kind': 'building'}]
        self.evidence.revision = 'new-map'
        self.registry_command(self.command('feature plans:'))
        self.assertEqual([f.id for f in self.world.reg.cell((0, 0)).features], ['w1'])

    def test_prepare_accepts_validation_and_terrain_blockers(self):
        from cell_preparation import prepare_cell
        from unittest import mock
        # Preparation's actual preflight and journal validation run over synthetic local repairs.
        repairs = mock.Mock()
        repairs.preflight.return_value = {'blockers': [], 'inputs': []}
        repairs.ghost.side_effect = lambda cell: self.world.seed(cell, ghost_ground=True)
        for prefix in ('validation required', 'validation stale', 'terrain:'):
            with self.subTest(blocker=prefix):
                if prefix == 'validation required':
                    cell = (1, 0)
                    self.world.seed(cell, ghost_ground=True)
                elif prefix == 'validation stale':
                    cell = (0, 0)
                    self.evidence.revision = 'new-evidence'
                else:
                    cell = (2, 0)
                report = self.policy.check(cell)
                command = next(cmd for b, cmd in report['clear_with'].items() if b.startswith(prefix))
                self.assertIn(f'prepare --cell {cell[0]},{cell[1]} --apply', command)
                result = prepare_cell(self.world.reg, self.evidence, cell, apply=True,
                                      repairs=repairs, check_lock=lambda: None)
                self.assertTrue(result['ready'], result['blockers'])

    def test_prepare_can_repair_a_candidate_error_with_a_local_extract(self):
        from cell_preparation import prepare_cell
        from unittest import mock
        self.evidence.fraction = .5
        self.evidence.revision = 'missing-extract'
        candidates = self.evidence.candidates
        def extract(cell):
            self.evidence.fraction = 1
            self.evidence.candidates = candidates
        self.evidence.candidates = mock.Mock(side_effect=ValueError('missing local extract'))
        command = self.command('candidate evidence error')
        self.assertIn('prepare --cell 0,0 --apply', command)
        repairs = mock.Mock()
        repairs.preflight.return_value = {'blockers': [], 'inputs': []}
        repairs.extract.side_effect = extract
        result = prepare_cell(self.world.reg, self.evidence, (0, 0), apply=True,
                              repairs=repairs, check_lock=lambda: None)
        self.assertTrue(result['ready'], result['blockers'])

    def test_evidence_validation_command_accepts_the_neighbours_stale_evidence(self):
        from cell_preparation import prepare_cell
        from unittest import mock
        self.evidence.revision = 'new-evidence'
        command = self.command('evidence validation stale', neighbour=True)
        self.assertIn('prepare --cell 0,0 --apply --evidence-only', command)
        repairs = mock.Mock()
        repairs.preflight.return_value = {'blockers': [], 'inputs': []}
        report = prepare_cell(self.world.reg, self.evidence, (0, 0), apply=True,
                              evidence_only=True, repairs=repairs, check_lock=lambda: None)
        self.assertTrue(report['ready'], report['blockers'])

    def test_missing_aerial_and_lidar_request_data_instead_of_a_refused_command(self):
        self.evidence.aerial = .5
        self.evidence.missing = ['tile.tif']
        for prefix in ('aerial coverage', 'lidar cache'):
            with self.subTest(blocker=prefix):
                command = self.command(prefix)
                self.assertIn('owner supplies local data', command)
                self.assertNotIn('python3', command)

    def test_evidence_error_requests_input_repair(self):
        from unittest import mock
        with mock.patch.object(self.evidence, 'facts', side_effect=ValueError('broken input')):
            command = self.command('evidence error:')
        self.assertIn('fix the named input; no command applies', command)

    def test_generated_ground_command_passes_its_readiness_preflight(self):
        from readiness_evidence import require_ready
        from unittest import mock
        command = self.command('generated ground required', 'trees')
        self.assertIn('ground_layer.py --world <world> --cell 0,0 --apply', command)
        with mock.patch('site_lock.check'), \
                mock.patch('cell_registry.view_registry', return_value=self.world.reg), \
                mock.patch('readiness_evidence.policy_for', return_value=self.policy):
            require_ready([(0, 0)], 'ground')

    def test_planned_features_command_selects_the_blocked_cells_feature(self):
        from cell_journal import Note
        self.world.seed((0, 0), ground=True)
        self.world.reg.change(Note((0, 0), 'plan_feature', 'hall', 'hall'))
        self.policy.validate((0, 0))
        # Another cell holds focus; an unscoped next would select that cell instead.
        self.world.seed((1, 0), ghost_ground=True, marker=True)
        self.policy.validate((1, 0))
        command = self.command('planned features must land', 'trees')
        out = self.registry_command(command)
        self.assertIn('"feature": "hall"', out)
        self.assertIn('"cell": "0,0"', out)

    def test_recovery_accepts_the_unfinished_entry(self):
        from unittest import mock
        from journal_testkit import View
        import site_recover
        ticket = self.world.reg.begin(Build('hall', 'feature', {(4, 61, 4): 'minecraft:bricks'}, cell=(0, 0)))
        self.world.apply(ticket.writes)  # interrupted between writing and finishing
        self.assertIn('site_recover.py --world <world> recover 0,0 --apply',
                      self.command('recovery: unfinished apply'))
        with mock.patch.object(site_recover, 'lock_state', return_value={'state': 'none', 'lock': None}):
            report = site_recover.recover((0, 0), flush=lambda: None,
                                          world_factory=lambda: View(self.world.blocks))
        self.assertEqual(report['action'], 'would-applied', report)

    def test_mismatched_layers_command_passes_its_own_preflight(self):
        from cell_readiness import ReadinessPolicy
        for layer in ('ground', 'trees', 'decor'):
            with self.subTest(layer=layer), JournalWorld() as world:
                world.seed((0, 0), ground=True)
                policy = ReadinessPolicy(world.reg, Evidence())
                policy.validate((0, 0))
                receipt = world.reg.change(Build('broken', layer, {(4, 61, 4): 'minecraft:stone'},
                                                 cell=(0, 0)), lambda w: None)
                self.assertEqual(receipt.status, 'mismatched')
                command = policy.check((0, 0))['clear_with']['recovery: mismatched apply broken']
                redo = command.split('if it reports still mismatched, ')[1].split(' (')[0]
                self.assertIn(f'--layers {layer}', redo)
                layer_command_preflight(redo, world, policy)


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

    def record_builder(self, source="NAME = 'house'\n"):
        path = self.world.root / 'build_house.py'
        path.write_text(source)
        self.world.build('house', 'feature', self.HOUSE, cell=(0, 0),
                         meta={'builder': {'kind': 'script', 'path': str(path)}})
        return path

    def test_blocker_names_reapply_only_when_its_preflight_accepts(self):
        from feature_reapply import prepare
        from cell_readiness import format_readiness
        self.record_builder()
        self.mismatch(cells={(1, 60, 1): 'minecraft:bricks'})
        report = self.check()
        self.assertEqual(report['blockers'], ['recovery: mismatched apply house'])
        command = report['clear_with']['recovery: mismatched apply house']
        self.assertIn('cell_registry.py --world <world> reconcile 0,0 --fix', command)
        self.assertIn('reapply_feature.py --world <world> house --apply', command)
        self.assertTrue(prepare('house', registry=self.world.reg))
        self.assertIn('clear: recovery: mismatched apply house -> ', format_readiness(report))

    def test_unrecorded_mismatch_offers_redo_and_reconcile_accepts(self):
        from unittest import mock
        from element_undo import require_rebuild_ready
        from feature_reapply import prepare
        self.mismatch()
        before = self.world.reg.journal_path.read_bytes()
        with self.assertRaisesRegex(SystemExit, 'no replayable recorded builder'):
            prepare('house', registry=self.world.reg)
        command = self.check()['clear_with']['recovery: mismatched apply house']
        self.assertNotIn('reapply_feature.py', command)
        self.assertIn('redo skill', command)
        self.assertIn('house', command)
        self.assertIn('no replayable recorded builder', command)
        self.assertEqual(before, self.world.reg.journal_path.read_bytes())
        # The redo skill's undo-before-rebuild gate accepts the same unrecorded plan.
        with mock.patch('site_lock.check'), \
                mock.patch('cell_registry.view_registry', return_value=self.world.reg), \
                mock.patch('readiness_evidence.policy_for', return_value=self.policy):
            require_rebuild_ready('house')
        self.assertTrue(self.world.reg.reconcile(fix=True, apply=self.world.apply).clean)

    def test_unusable_recorded_builders_offer_redo_with_the_preflight_reason(self):
        from feature_reapply import prepare
        for source in (None, 'NAME = broken syntax', "NAME = 'another-feature'\n"):
            with self.subTest(source=source):
                path = self.record_builder(source or "NAME = 'house'\n")
                if source is None:
                    path.unlink()
                self.mismatch(cells={(1, 60, 1): 'minecraft:bricks'})
                with self.assertRaises(SystemExit) as refused:
                    prepare('house', registry=self.world.reg)
                command = self.check()['clear_with']['recovery: mismatched apply house']
                self.assertNotIn('reapply_feature.py', command)
                self.assertIn('redo skill', command)
                self.assertIn(str(refused.exception), command)

    def test_invalid_recorded_spec_offers_redo_with_the_preflight_reason(self):
        import json
        from feature_reapply import prepare
        path = self.world.root / 'invalid.json'
        path.write_text(json.dumps({'name': 'house'}))
        self.world.build('house', 'feature', self.HOUSE, cell=(0, 0),
                         meta={'builder': {'kind': 'spec', 'path': str(path)}})
        self.mismatch(cells={(1, 60, 1): 'minecraft:bricks'})
        with self.assertRaisesRegex(SystemExit, 'invalid builder spec') as refused:
            prepare('house', registry=self.world.reg)
        command = self.check()['clear_with']['recovery: mismatched apply house']
        self.assertNotIn('reapply_feature.py', command)
        self.assertIn('redo skill', command)
        self.assertIn(str(refused.exception), command)

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
        self.assertIn('clear with: reconcile 0,0 --fix', format_drift(dry, registry=self.world.reg))
        drift = self.world.reg.reconcile(fix=True, apply=self.world.apply)
        self.assertTrue(drift.clean)
        self.assertEqual([plan for plan, _seq in drift.cleared], ['house'])
        self.assertIn('house', format_drift(drift, registry=self.world.reg, fix=True))
        self.assertIn('preflight no longer blocks', format_drift(drift, registry=self.world.reg, fix=True))
        self.assertTrue(self.check()['ready'])

    def test_reconcile_fix_that_cannot_hold_names_the_reapply_command(self):
        from cell_registry import format_drift
        self.record_builder()
        self.mismatch(cells={(1, 60, 1): 'minecraft:bricks'})
        drift = self.world.reg.reconcile(fix=True, apply=lambda w: None)      # the game undoes every write
        self.assertFalse(drift.clean)
        item = drift.by_kind('mismatched')[0]
        self.assertEqual((item.plans, item.layer, item.count, item.fixed), (('house',), 'feature', 1, False))
        out = format_drift(drift, registry=self.world.reg, fix=True)
        self.assertIn('still mismatched after repair', out)
        self.assertIn('reapply_feature.py --world <world> house --apply', out)
        self.assertIn('recovery: mismatched apply house', self.check()['blockers'])

    def test_reconcile_fix_without_a_builder_names_redo(self):
        from cell_registry import format_drift
        self.mismatch()
        drift = self.world.reg.reconcile(fix=True, apply=lambda w: None)
        out = format_drift(drift, registry=self.world.reg, fix=True)
        self.assertIn('still mismatched after repair', out)
        self.assertIn('redo skill', out)
        self.assertIn('no replayable recorded builder', out)
        self.assertNotIn('reapply_feature.py', out)

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
        self.assertIn('--cell 0,0 --layers trees', format_drift(drift, registry=self.world.reg, fix=True))

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

    def test_stale_ground_command_passes_redo_layers_actual_preflight(self):
        self.world.seed((0, 0), ground=True)
        self.world.seed((0, 0), features=('house',))
        report = self.check('trees')
        command = report['clear_with']['ground stale: ' + report['ground_stale'][0]]
        layer_command_preflight(command, self.world, self.policy)

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


class TestGroundFloatingLines(unittest.TestCase):
    """#297: check names whose each floating ground column is, with the command that settles it."""

    def test_format_names_the_feature_clear(self):
        from cell_readiness import format_readiness
        report = {'cell': '0,0', 'ready': True, 'complete': False, 'ground': 'generated',
                  'ground_floating': [
                      {'column': [5, 6], 'surface_y': 54, 'belongs': 'feature', 'owner': 'hall',
                       'clear': 'python3 scripts/reapply_feature.py --world <world> hall --apply'},
                      {'column': [7, 6], 'surface_y': 49, 'belongs': 'ground', 'owner': 'west',
                       'clear': 'python3 scripts/redo_layer.py --world <world> --cell 0,0 --layers ground --apply'}]}
        text = format_readiness(report)
        self.assertIn("ground floating: (5,6) y54 is feature hall's -> clear: python3 scripts/reapply_feature.py", text)
        self.assertIn("ground floating: (7,6) y49 is ground's -> clear: python3 scripts/redo_layer.py", text)


class RailwayEvidenceTest(unittest.TestCase):
    """Task 454: a railway feature needs map and lidar evidence, never aerial coverage."""

    PLAN = 'railway_0_0'
    CELLS = {(1, 65, 1): 'minecraft:rail', (129, 65, 1): 'minecraft:rail'}     # cells 0,0 and 1,0

    def setUp(self):
        from cell_readiness import ReadinessPolicy
        self.world = JournalWorld()
        self.addCleanup(self.world.__exit__)
        self.world.__enter__()
        self.evidence = Evidence()
        self.evidence.aerial = 0.0                       # no aerial anywhere
        for cell in ((0, 0), (1, 0)):
            self.world.seed(cell, ghost_ground=True)
        self.world.reg.change(Note((0, 0), 'plan_feature', 'Railway corridor', self.PLAN))
        self.policy = ReadinessPolicy(self.world.reg, self.evidence)

    def railway(self, plan=PLAN, cells=None, **kw):
        return Build(plan, 'feature', cells or self.CELLS, cell=(0, 0), meta={'kind': 'railway'}, **kw)

    def prepare_railway_cells(self):
        self.policy.validate((0, 0), feature=self.PLAN)
        self.policy.validate_evidence((1, 0), feature=self.PLAN)

    def test_neighbour_and_primary_without_aerial_pass_the_apply_gate(self):
        self.prepare_railway_cells()
        self.world.reg.build_policy = self.policy.enforce
        self.assertEqual(self.world.reg.change(self.railway(), self.world.apply).status, 'landed')
        report = self.policy.check((1, 0), 'feature', neighbour=True, feature=self.PLAN)
        self.assertEqual((report['ready'], report['feature_evidence']), (True, 'railway'))

    def test_apply_refreshes_stale_railway_neighbour_without_aerial(self):
        self.prepare_railway_cells()
        self.evidence.revision = 'evidence-2'            # both cells stale; checks still pass
        self.world.reg.build_policy = self.policy.enforce
        self.assertEqual(self.world.reg.change(self.railway(), self.world.apply).status, 'landed')

    def test_primary_cell_alone_without_aerial_passes(self):
        self.policy.validate((0, 0), feature=self.PLAN)
        self.world.reg.build_policy = self.policy.enforce
        cells = {(1, 65, 1): 'minecraft:rail', (2, 65, 1): 'minecraft:rail'}
        self.assertEqual(self.world.reg.change(self.railway(cells=cells), self.world.apply).status, 'landed')

    def test_same_cell_still_blocks_every_other_pass_and_feature_on_aerial(self):
        self.prepare_railway_cells()
        for operation in ('build', 'ground', 'feature', 'trees', 'decor'):
            with self.subTest(operation=operation):
                report = self.policy.check((1, 0), operation, neighbour=operation == 'feature')
                self.assertIn('aerial coverage: 0.0%', report['blockers'])
        self.assertIn('aerial coverage: 0.0%', self.policy.check((0, 0), 'ground')['blockers'])
        with self.assertRaisesRegex(Refused, 'aerial coverage'):
            self.policy.validate((1, 0))
        self.world.reg.build_policy = self.policy.enforce
        self.world.reg.change(Note((0, 0), 'plan_feature', 'house', 'house'))
        with self.assertRaisesRegex(Refused, 'aerial coverage'):                     # non-railway feature
            self.world.reg.begin(Build('house', 'feature', self.CELLS, cell=(0, 0), meta={'kind': 'houses'}))
        with self.assertRaisesRegex(Refused, 'aerial coverage'):                     # no kind recorded
            self.world.reg.begin(Build(self.PLAN, 'feature', self.CELLS, cell=(0, 0)))

    def test_railway_identity_needs_name_kind_and_a_journalled_plan(self):
        from cell_readiness import feature_evidence_kind
        reg = self.world.reg
        self.assertEqual(feature_evidence_kind(reg, 'railway_0_0'), 'railway')
        for plan in (None, 'house', 'railway_0_0x', 'railway_1_0', 'railway_00_0', 'railway_-1_0'):
            self.assertIsNone(feature_evidence_kind(reg, plan), plan)
        reg.change(Note((-1, 0), 'plan_feature', 'Railway corridor', 'railway_m1_0'))
        self.assertEqual(feature_evidence_kind(reg, 'railway_m1_0'), 'railway')
        # a railway plan name does not waive aerial for a build whose kind is not railway
        self.world.reg.build_policy = self.policy.enforce
        self.policy.validate((0, 0), feature=self.PLAN)
        with self.assertRaisesRegex(Refused, 'aerial coverage'):
            self.world.reg.begin(Build(self.PLAN, 'feature', self.CELLS, cell=(0, 0), meta={'kind': 'water'}))
        # and a build that records kind railway under a name the journal does not plan stays strict
        with self.assertRaisesRegex(Refused, 'aerial coverage'):
            self.world.reg.begin(self.railway(plan='railway_5_5', cells={(641, 65, 1): 'minecraft:rail'}))

    def test_railway_neighbour_without_map_or_lidar_still_blocks(self):
        self.evidence.fraction = 0.5
        report = self.policy.check((1, 0), 'feature', neighbour=True, feature=self.PLAN)
        self.assertIn('map query coverage: 50.0%', report['blockers'])
        self.assertNotIn('aerial coverage', ' '.join(report['blockers']))
        self.evidence.fraction, self.evidence.missing = 1.0, ['tile.tif']
        report = self.policy.check((1, 0), 'feature', neighbour=True, feature=self.PLAN)
        self.assertIn('lidar cache: missing local tiles', report['blockers'])
        self.world.reg.build_policy = self.policy.enforce
        self.evidence.missing = []
        with self.assertRaisesRegex(Refused, 'needs-prep 0,0: validation required'):
            self.world.reg.begin(self.railway())
        self.policy.validate((0, 0), feature=self.PLAN)
        self.evidence.fraction = 0.5
        with self.assertRaisesRegex(Refused, 'needs-prep 0,0: .*map query coverage'):
            self.world.reg.begin(self.railway())

    def test_evidence_only_prepare_extracts_the_map_for_a_railway_neighbour_without_aerial(self):
        from cell_preparation import prepare_cell
        from unittest import mock
        self.evidence.fraction = 0.25
        repairs = mock.Mock()
        repairs.preflight.return_value = {'blockers': [], 'inputs': []}
        repairs.extract.side_effect = lambda cell: setattr(self.evidence, 'fraction', 1.0)
        ordinary = prepare_cell(self.world.reg, self.evidence, (1, 0), apply=True, repairs=repairs,
                                check_lock=lambda: None, evidence_only=True)
        self.assertFalse(ordinary['ready'])                          # unchanged: stops on aerial
        repairs.extract.assert_not_called()
        report = prepare_cell(self.world.reg, self.evidence, (1, 0), apply=True, repairs=repairs,
                              check_lock=lambda: None, evidence_only=True, feature=self.PLAN)
        repairs.extract.assert_called_once_with((1, 0))
        self.assertTrue(report['ready'] and report['evidence_validated'], report['blockers'])
        self.assertEqual([n['kind'] for n in self.world.reg.notes((1, 0))], ['readiness_evidence'])
        # a feature that is not a journalled railway keeps the early stop
        repairs.extract.reset_mock()
        self.evidence.fraction = 0.25
        other = prepare_cell(self.world.reg, self.evidence, (1, 0), apply=True, repairs=repairs,
                             check_lock=lambda: None, evidence_only=True, feature='house')
        self.assertFalse(other['ready'])
        repairs.extract.assert_not_called()

    def test_full_prepare_for_a_railway_primary_cell_validates_without_aerial(self):
        from cell_preparation import prepare_cell
        from unittest import mock
        repairs = mock.Mock()
        repairs.preflight.return_value = {'blockers': [], 'inputs': []}
        report = prepare_cell(self.world.reg, self.evidence, (0, 0), apply=True, repairs=repairs,
                              check_lock=lambda: None, feature=self.PLAN)
        self.assertTrue(report['validated'], report['blockers'])

    def test_clearing_commands_repeat_the_feature_scope_only_for_a_railway(self):
        self.evidence.revision = 'evidence-2'
        scoped = self.policy.check((1, 0), 'feature', neighbour=True, feature=self.PLAN)
        self.assertEqual(scoped['next_actions'], ['prepare --cell 1,0 --apply --evidence-only --feature railway_0_0'])
        command = next(iter(scoped['clear_with'].values()))
        self.assertIn('--evidence-only --feature railway_0_0 (records', command)
        plain = self.policy.check((1, 0), 'feature', neighbour=True)
        self.assertEqual(plain['next_actions'][0], 'prepare --cell 1,0 --apply --evidence-only')
        self.assertNotIn('feature_evidence', plain)
        self.assertTrue(any('aerial' in a for a in plain['next_actions']))

    def test_cli_prepare_refuses_an_unjournalled_feature(self):
        import contextlib
        import io
        from unittest import mock
        import cell_registry
        out = io.StringIO()
        with mock.patch.object(cell_registry, 'activate_world_from_args'), \
                mock.patch.object(cell_registry, 'view_registry', return_value=self.world.reg), \
                contextlib.redirect_stderr(out):
            code = cell_registry.main(['--world', 'unit-test', 'prepare', '--cell', '1,0', '--evidence-only', '--feature', 'house'])
        self.assertEqual(code, 2)
        self.assertIn('not a railway feature planned in the journal', out.getvalue())


class FeatureDryReadinessSurveyTest(unittest.TestCase):
    """Task 479: feature dry-run lists every readiness gap at once."""

    def setUp(self):
        from cell_readiness import ReadinessPolicy
        self.world = JournalWorld()
        self.addCleanup(self.world.__exit__)
        self.world.__enter__()
        self.evidence = Evidence()
        self.policy = ReadinessPolicy(self.world.reg, self.evidence)
        from cell_journal import Note
        self.Note = Note
        self.Build = Build

    def _plan_feature(self, cell, plan='hall'):
        self.world.reg.change(self.Note(tuple(cell), 'plan_feature', 'test', plan))

    def _survey(self, cells, plan='hall', primary=(0, 0), meta=None, modifies=()):
        from cell_readiness import format_feature_dry_readiness
        from readiness_evidence import survey_feature_dry
        build = self.Build(plan, 'feature',
                           {(primary[0] * 128 + 4, 65, primary[1] * 128 + 4): 'minecraft:bricks'},
                           cell=primary, meta=meta or {}, modifies=tuple(modifies))
        # Touch every listed cell with at least one column so majority/primary stay stable.
        for cell in cells:
            x0, z0 = cell[0] * 128 + 4, cell[1] * 128 + 4
            build.cells[(x0, 65, z0)] = 'minecraft:bricks'
        survey = survey_feature_dry(self.world.reg, build, set(cells), policy=self.policy)
        return survey, format_feature_dry_readiness(survey)

    def test_all_ready_reports_ready_without_writing(self):
        self.world.seed((0, 0), ghost_ground=True)
        self.world.seed((1, 0), ghost_ground=True)
        self._plan_feature((0, 0))
        self.policy.validate((0, 0))
        self.policy.validate_evidence((1, 0))
        before = self.world.reg.journal_path.read_bytes()
        survey, text = self._survey([(0, 0), (1, 0)])
        self.assertEqual(before, self.world.reg.journal_path.read_bytes())
        self.assertTrue(survey['ready'], text)
        self.assertIn('verdict: ready', text)
        self.assertEqual(survey['lock']['state'], 'free')

    def test_missing_ghost_and_missing_evidence_are_both_named(self):
        self.world.seed((0, 0), ghost_ground=True)
        self._plan_feature((0, 0))
        self.policy.validate((0, 0))
        # Neighbour 1,0: no ghost; partial map coverage.
        self.evidence.fraction = 0.0
        survey, text = self._survey([(0, 0), (1, 0)])
        self.assertFalse(survey['ready'])
        texts = [g['text'] for g in survey['gaps']]
        self.assertTrue(any('1,0' in t and 'ghost' in t for t in texts), texts)
        self.assertTrue(any('1,0' in t and 'map query coverage' in t for t in texts), texts)
        self.assertTrue(any('1,0' in t and 'evidence validation' in t for t in texts), texts)
        self.assertIn('gap:', text)
        self.assertIn('verdict: not ready', text)

    def test_unvalidated_evidence_is_named(self):
        self.world.seed((0, 0), ghost_ground=True)
        self.world.seed((1, 0), ghost_ground=True)
        self._plan_feature((0, 0))
        self.policy.validate((0, 0))
        # Neighbour has ghost but no evidence note.
        survey, text = self._survey([(0, 0), (1, 0)])
        self.assertFalse(survey['ready'])
        self.assertTrue(any('1,0' in g['text'] and 'evidence validation' in g['text']
                            for g in survey['gaps']), survey['gaps'])
        self.assertIn('--evidence-only', text)

    def test_unfinished_apply_names_recover_command(self):
        self.world.seed((0, 0), ghost_ground=True)
        self._plan_feature((0, 0))
        self.policy.validate((0, 0))
        self.world.reg.begin(self.Build('hall', 'feature', {(4, 65, 4): 'minecraft:bricks'},
                                        cell=(0, 0)))
        survey, text = self._survey([(0, 0)])
        self.assertFalse(survey['ready'])
        self.assertTrue(any('unfinished apply' in g['text'] for g in survey['gaps']), survey['gaps'])
        self.assertIn('site_recover.py', text)
        self.assertEqual(survey['cells'][0]['journal']['apply'], 'begun')

    def test_lock_held_by_another_owner_is_a_gap(self):
        from unittest import mock
        self.world.seed((0, 0), ghost_ground=True)
        self._plan_feature((0, 0))
        self.policy.validate((0, 0))
        lk = {'owner': 'other', 'task': 'busy', 'claimed_at': '2026-01-01 00:00:00',
              'heartbeat': 10**12}
        with mock.patch('site_lock.read', return_value=lk), \
                mock.patch('site_lock.fresh', return_value=True), \
                mock.patch.dict('os.environ', {'SITE_OWNER': 'me'}):
            survey, text = self._survey([(0, 0)])
        self.assertFalse(survey['ready'])
        self.assertEqual(survey['lock']['state'], 'held-by-other')
        self.assertTrue(any('site lock held by other' in g['text'] for g in survey['gaps']), survey['gaps'])
        self.assertIn('site_lock.py', text)

    def test_survey_names_every_gap_not_just_the_first(self):
        """Missing ghost, missing evidence, unfinished apply and foreign lock together."""
        from unittest import mock
        self.world.seed((0, 0), ghost_ground=True)
        self._plan_feature((0, 0))
        self.policy.validate((0, 0))
        self.world.reg.begin(self.Build('stuck', 'feature', {(5, 65, 5): 'minecraft:bricks'},
                                        cell=(0, 0)))
        self.evidence.fraction = 0.25
        lk = {'owner': 'other', 'task': 'busy', 'claimed_at': '2026-01-01 00:00:00',
              'heartbeat': 10**12}
        with mock.patch('site_lock.read', return_value=lk), \
                mock.patch('site_lock.fresh', return_value=True), \
                mock.patch.dict('os.environ', {'SITE_OWNER': 'me'}):
            survey, text = self._survey([(0, 0), (1, 0)])
        kinds = ' '.join(g['text'] for g in survey['gaps'])
        self.assertIn('site lock held by other', kinds)
        self.assertIn('unfinished apply', kinds)
        self.assertIn('1,0', kinds)
        self.assertIn('ghost', kinds)
        self.assertIn('map query coverage', kinds)
        self.assertGreaterEqual(len(survey['gaps']), 4, survey['gaps'])
        self.assertIn('verdict: not ready', text)
