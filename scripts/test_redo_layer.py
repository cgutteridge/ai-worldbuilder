"""Redo outcomes are verified against a real in-memory cell journal."""
import unittest
from journal_testkit import JournalWorld
from cell_journal import Undo
from layer_redo import redo


class MemoryLayers:
    def __init__(self, world, fail=None):
        self.world, self.fail = world, fail
        self.calls = []
        self.masks = {'trees', 'decor'}
    def has_mask(self, layer): return layer in self.masks
    def preflight(self, cell, layers, undos, live):
        if self.fail == 'preflight': raise ValueError('mask conflict')
    def hover(self, cell):
        if self.fail == 'hover': raise ValueError('move failed')
    def undo(self, cell, layer):
        self.world.reg.change(Undo((cell, layer)), self.world.apply)
        self.masks.discard(layer)
    def preview(self, cell, layer): pass
    def apply(self, cell, layer):
        if layer == self.fail: raise ValueError('simulated readback failure')
        self.world.build('new_' + layer, layer, {(20, 60, 20): 'minecraft:stone'}, cell=cell)
    def cue(self): pass


class TestRedoOutcomes(unittest.TestCase):
    def test_trees_only_clears_decor_and_reports_it_pending(self):
        with JournalWorld() as jw:
            jw.seed((0, 0), ground=True, trees=True, decor=True)
            result = redo((0, 0), ['trees'], MemoryLayers(jw), live=True)
            self.assertTrue(result['ok'])
            self.assertTrue(jw.reg.cell((0, 0)).trees)
            self.assertFalse(jw.reg.cell((0, 0)).decor)
            self.assertIn('decor', result['left_undone'])

    def test_preflight_and_hover_failure_leave_journal_intact(self):
        for failure in ('preflight', 'hover'):
            with self.subTest(failure=failure), JournalWorld() as jw:
                jw.seed((0, 0), ground=True, trees=True, decor=True)
                before = jw.reg.entries()
                result = redo((0, 0), ['trees'], MemoryLayers(jw, failure), live=True)
                self.assertFalse(result['ok'])
                self.assertEqual(jw.reg.entries(), before)

    def test_ground_only_preserves_trees_decor_and_masks(self):
        with JournalWorld() as jw:
            jw.seed((0, 0), ground=True, trees=True, decor=True)
            adapter = MemoryLayers(jw)
            result = redo((0, 0), ['ground'], adapter, live=True)
            self.assertTrue(result['ok'])
            self.assertTrue(jw.reg.cell((0, 0)).trees)
            self.assertTrue(jw.reg.cell((0, 0)).decor)
            self.assertEqual(adapter.masks, {'trees', 'decor'})
            self.assertEqual(result['left_undone'], [])

    def test_later_stage_failure_reports_real_undone_state(self):
        with JournalWorld() as jw:
            jw.seed((0, 0), ground=True, trees=True, decor=True)
            result = redo((0, 0), ['decor', 'trees'], MemoryLayers(jw, 'decor'), live=True)
            self.assertFalse(result['ok'])
            self.assertEqual(result['failed']['stage'], 'apply decor')
            self.assertTrue(jw.reg.cell((0, 0)).trees)
            self.assertFalse(jw.reg.cell((0, 0)).decor)
            self.assertEqual(result['left_undone'], ['decor'])
            self.assertEqual(result['pending'], ['apply decor'])

    def test_dry_run_and_invalid_input_never_change_journal(self):
        with JournalWorld() as jw:
            jw.seed((0, 0), ground=True, trees=True, decor=True)
            before = jw.reg.entries()
            adapter = MemoryLayers(jw)
            result = redo((0, 0), ['trees', 'decor'], adapter)
            self.assertTrue(result['ok'])
            self.assertTrue(result['dry_run'])
            for layers in ([], ['unknown'], ['trees', 'trees']):
                with self.assertRaises(ValueError): redo((0, 0), layers, adapter, live=True)
            self.assertEqual(jw.reg.entries(), before)

    def test_interrupt_stops_before_later_stage(self):
        with JournalWorld() as jw:
            jw.seed((0, 0), ground=True, trees=True, decor=True)
            adapter = MemoryLayers(jw)
            adapter.apply = lambda cell, layer: (_ for _ in ()).throw(KeyboardInterrupt())
            result = redo((0, 0), ['trees', 'decor'], adapter, live=True)
            self.assertFalse(result['ok'])
            self.assertTrue(result['interrupted'])
            self.assertFalse(jw.reg.cell((0, 0)).trees)
            self.assertFalse(jw.reg.cell((0, 0)).decor)

    def test_locked_cli_reports_partial_state_on_termination(self):
        import contextlib
        import io
        import json
        import signal
        from unittest import mock
        import layer_redo
        with JournalWorld() as jw:
            jw.seed((0, 0), ground=True, trees=True, decor=True)
            backend = MemoryLayers(jw)
            backend.apply = lambda cell, layer: signal.raise_signal(signal.SIGTERM)
            out = io.StringIO()
            with mock.patch('script_env.activate_world_from_args'), \
                    mock.patch('site_lock.check'), \
                    mock.patch.object(layer_redo, 'LayerCommands', return_value=backend), \
                    contextlib.redirect_stdout(out):
                # Supply a harmless existing handler until the CLI installs its own.
                original = signal.signal(signal.SIGTERM, lambda *args: None)
                try:
                    code = layer_redo.main(['--world', 'unit-test', '--cell', '0,0',
                                            '--layers', 'trees,decor', '--apply', '--locked'])
                finally:
                    signal.signal(signal.SIGTERM, original)
            self.assertEqual(code, 130)
            report = json.loads(out.getvalue()[out.getvalue().index('{\n'):])
            self.assertEqual(report['failed']['stage'], 'apply trees')
            self.assertIn('decor', report['left_undone'])


class TestAdditionalRedoChecks(unittest.TestCase):
    def test_canonical_undo_preview_apply_order_and_optional_cue_failure(self):
        with JournalWorld() as jw:
            jw.seed((0, 0), ground=True, trees=True, decor=True)
            backend = MemoryLayers(jw)
            calls = []
            undo, apply = backend.undo, backend.apply
            backend.hover = lambda cell: calls.append('hover')
            backend.undo = lambda cell, layer: (calls.append('undo ' + layer), undo(cell, layer))
            backend.preview = lambda cell, layer: calls.append('preview ' + layer)
            backend.apply = lambda cell, layer: (calls.append('apply ' + layer), apply(cell, layer))
            backend.cue = lambda: (_ for _ in ()).throw(SystemExit('optional cue unavailable'))
            result = redo((0, 0), ['decor', 'ground', 'trees'], backend, live=True)
            self.assertTrue(result['ok'])
            self.assertIn('optional cue', result['cue_warning'])
            self.assertEqual(calls, ['hover', 'undo decor', 'undo trees', 'preview ground', 'apply ground',
                                     'preview trees', 'apply trees', 'preview decor', 'apply decor', 'hover'])
            self.assertEqual(result['pending'], [])
            self.assertEqual(result['left_undone'], [])

    def test_final_view_failure_is_nonzero_after_completed_layers_and_has_no_cue(self):
        from unittest import mock
        with JournalWorld() as jw:
            jw.seed((0, 0), ground=True, trees=True, decor=True)
            backend = MemoryLayers(jw)
            backend.hover = mock.Mock(side_effect=[None, ValueError('final view lost')])
            backend.cue = mock.Mock()
            result = redo((0, 0), ['trees', 'decor'], backend, live=True)
            self.assertFalse(result['ok'])
            self.assertEqual(result['failed']['stage'], 'hover after')
            self.assertEqual(result['pending'], [])
            self.assertIn('apply decor', result['completed'])
            backend.cue.assert_not_called()

    def test_invalid_cli_layers_refuse_before_activation_or_lock(self):
        from unittest import mock
        import layer_redo
        with mock.patch('script_env.activate_world_from_args') as activate, mock.patch('site_lock.run') as lock:
            with self.assertRaises(SystemExit) as error:
                layer_redo.main(['--world', 'unit-test', '--cell', '0,0', '--layers', 'trees,trees', '--apply'])
            self.assertEqual(error.exception.code, 2)
            activate.assert_not_called()
            lock.assert_not_called()

    def test_existing_site_owner_is_checked_without_claiming_another_lock(self):
        import os
        from unittest import mock
        import layer_redo
        backend = mock.Mock()
        backend.has_mask.return_value = False
        with mock.patch.dict(os.environ, {'SITE_OWNER': 'outer-owner'}), \
                mock.patch('script_env.activate_world_from_args'), \
                mock.patch('site_lock.check') as check, \
                mock.patch('site_lock.run') as lock, \
                mock.patch.object(layer_redo, 'LayerCommands', return_value=backend):
            self.assertEqual(layer_redo.main(['--world', 'unit-test', '--cell', '0,0',
                                              '--layers', 'decor', '--apply']), 0)
        check.assert_called_once_with()
        lock.assert_not_called()
        backend.apply.assert_called_once_with((0, 0), 'decor')

    def test_child_command_keeps_owner_and_streams_output(self):
        import os
        from unittest import mock
        from layer_redo import LayerCommands
        with mock.patch.dict(os.environ, {'SITE_OWNER': 'redo-owner'}), mock.patch('layer_redo.subprocess.run') as run:
            LayerCommands('demo', (0, 0)).preview((0, 0), 'ground')
        argv = run.call_args.args[0]
        self.assertIn('--world', argv)
        self.assertIn('demo', argv)
        self.assertEqual(run.call_args.kwargs['env']['SITE_OWNER'], 'redo-owner')
        self.assertEqual(run.call_args.kwargs['env']['PYTHONUNBUFFERED'], '1')
        self.assertTrue(run.call_args.kwargs['check'])

    def test_landed_layer_with_missing_mask_refuses_without_changing_journal(self):
        from types import SimpleNamespace
        from unittest import mock
        from layer_redo import LayerCommands
        with JournalWorld() as jw:
            jw.seed((0, 0), ground=True, trees=True)
            before = jw.reg.entries()
            backend = LayerCommands('demo', (0, 0))
            with mock.patch('world_discovery.require_running_world'), \
                    mock.patch('cell_registry.view_registry', return_value=jw.reg), \
                    mock.patch('readiness_evidence.policy_for', return_value=SimpleNamespace(check=lambda *a, **k: {'ready': True})), \
                    mock.patch.object(backend, 'has_mask', return_value=False):
                with self.assertRaisesRegex(ValueError, 'landed layer has no mask'):
                    backend.preflight((0, 0), ['trees'], [], True)
            self.assertEqual(jw.reg.entries(), before)

    def test_layer_redo_passes_its_own_mismatch_and_names_commands_for_the_rest(self):
        from unittest import mock
        from cell_journal import Build
        from cell_readiness import ReadinessPolicy
        from layer_redo import LayerCommands
        from test_cell_readiness import Evidence
        with JournalWorld() as jw:
            jw.seed((0, 0), ghost_ground=True, ground=True)
            jw.reg.change(Build('seed_trees', 'trees', {(1, 61, 8): 'minecraft:oak_log'}, cell=(0, 0)),
                          lambda w: None)                              # a mismatched trees apply
            jw.reg.change(Build('west', 'feature', {(3, 61, 3): 'minecraft:stone'}, cell=(0, 0)),
                          lambda w: None)                              # a mismatched feature apply
            policy = ReadinessPolicy(jw.reg, Evidence())
            backend = LayerCommands('demo', (0, 0))
            with mock.patch('world_discovery.require_running_world'), \
                    mock.patch('cell_registry.view_registry', return_value=jw.reg), \
                    mock.patch('readiness_evidence.policy_for', return_value=policy):
                with self.assertRaisesRegex(ValueError, r'mismatched apply west \[clear: .*reconcile 0,0 --fix'):
                    backend.preflight((0, 0), ['trees'], [], True)
                try:
                    backend.preflight((0, 0), ['trees'], [], True)
                except ValueError as exc:
                    self.assertNotIn('mismatched apply seed_trees', str(exc))      # its own mismatch is passed

    def test_undo_mask_drift_is_preflight_failure_before_any_mutation(self):
        from types import SimpleNamespace
        from unittest import mock
        from layer_redo import LayerCommands
        from pass_mask import build_mask
        with JournalWorld() as jw:
            jw.seed((0, 0), ground=True, trees=True)
            name = 'seed_0_0_trees'
            blocks = jw.reg.recorded(name)
            mask = build_mask('trees', (0, 0), name, blocks, lambda p: True)
            pos = next(iter(blocks))
            jw.blocks.write(pos, 'minecraft:stone')
            before = jw.reg.entries()
            backend = LayerCommands('demo', (0, 0))
            with mock.patch('world_discovery.require_running_world'), \
                    mock.patch('cell_registry.view_registry', return_value=jw.reg), \
                    mock.patch('readiness_evidence.policy_for', return_value=SimpleNamespace(check=lambda *a, **k: {'ready': True})), \
                    mock.patch.object(backend, 'has_mask', side_effect=lambda layer: layer == 'trees'), \
                    mock.patch('site_kit.fresh_planning_world', return_value=SimpleNamespace(block=lambda *p: jw.blocks.read(p))), \
                    mock.patch('pass_mask.read_mask', return_value=mask), \
                    mock.patch('pass_mask.shared_positions', return_value=set()):
                with self.assertRaisesRegex(ValueError, 'undo mask conflicts'):
                    backend.preflight((0, 0), ['trees'], ['trees'], True)
            self.assertEqual(jw.reg.entries(), before)
            self.assertEqual(jw.blocks.read(pos), 'minecraft:stone')

    def test_production_hover_refuses_missing_player_or_unconfirmed_view(self):
        from types import SimpleNamespace
        from unittest import mock
        from layer_redo import LayerCommands
        backend = LayerCommands('demo', (0, 0))
        with mock.patch('active_world.load_world_config', return_value={}), mock.patch('mc_paced.Paced'):
            with self.assertRaisesRegex(ValueError, 'explicit player_uuid'):
                backend.hover((0, 0))
        for pitch, success in ((90, True), (0, False)):
            info = f'x: 64\ny: 100\nz: 64\npitch: {pitch}\ngameMode: spectator'
            m = mock.Mock()
            m.text.return_value = info
            with self.subTest(pitch=pitch), \
                    mock.patch('active_world.load_world_config', return_value={'player_uuid': 'selected-player'}), \
                    mock.patch('mc_paced.Paced', return_value=m), \
                    mock.patch('mc_player.move_player') as move, \
                    mock.patch('site_kit.fresh_planning_world'), \
                    mock.patch('surface_probe.surface_y', return_value=(60, 'grass_block')), \
                    mock.patch('cell_registry.view_registry', return_value=SimpleNamespace(entries=lambda **kw: [])):
                if success:
                    backend.hover((0, 0))
                else:
                    with self.assertRaisesRegex(ValueError, 'not hovering'):
                        backend.hover((0, 0))
                move.assert_called_once()
                m.call.assert_called_once_with('player_get_info', {'uuid': 'selected-player'})
