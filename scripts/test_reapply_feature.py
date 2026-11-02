"""Reapply command preflights leave existing journal state untouched on failure."""
import io
import json
from contextlib import redirect_stderr
import unittest
from unittest import mock
from active_world import world_root
from journal_testkit import JournalWorld
import feature_reapply as reapply_feature


class TestReapply(unittest.TestCase):
    def test_invalid_recorded_spec_stops_before_undo(self):
        root = world_root()
        path = root / 'site/builds/invalid.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'name': 'feature'}))
        with JournalWorld() as jw:
            jw.build('feature', 'feature', {(1, 60, 1): 'minecraft:stone'},
                     meta={'builder': {'kind': 'spec', 'path': 'site/builds/invalid.json'}})
            with self.assertRaisesRegex(SystemExit, 'storeys|ways'):
                reapply_feature.prepare('feature', registry=jw.reg)
            self.assertFalse(jw.reg.entries(plan='feature')[0].undone)

    def test_missing_or_inferred_builder_does_not_authorize_undo(self):
        with JournalWorld() as jw:
            jw.build('legacy', 'feature', {(2, 60, 2): 'minecraft:stone'})
            with self.assertRaisesRegex(SystemExit, 'no replayable recorded'):
                reapply_feature.prepare('legacy', registry=jw.reg)
            self.assertFalse(jw.reg.entries(plan='legacy')[0].undone)

    def test_valid_recorded_script_uses_argv_not_journal_command_text(self):
        path = world_root() / 'scripts/build_demo.py'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("NAME = 'demo'\n")
        with JournalWorld() as jw:
            jw.build('demo', 'feature', {(2, 60, 2): 'minecraft:stone'},
                     meta={'builder': {'kind': 'script', 'path': str(path)}})
            cmd = reapply_feature.prepare('demo', registry=jw.reg)
            self.assertEqual(cmd, [reapply_feature.sys.executable, str(path.resolve())])
            self.assertFalse(jw.reg.entries(plan='demo')[0].undone)


class TestReapplyOutcome(unittest.TestCase):
    """Command exit/output reflects the actual new journal outcome, not exit 0."""

    def test_builder_exit_zero_requires_a_new_landed_feature(self):
        from cell_journal import Build, Undo
        for kind in ('script', 'spec'):
            for outcome in ('mismatched', 'begun', 'no-new', 'landed'):
                with self.subTest(kind=kind, outcome=outcome), JournalWorld() as jw:
                    path = world_root() / ('scripts/build_outcome.py' if kind == 'script'
                                           else 'site/builds/outcome.json')
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("NAME = 'outcome'\n" if kind == 'script' else json.dumps({
                        'name': 'outcome', 'ways': {'1': 'A'}, 'storeys': {'A': 1},
                        'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}}, 'groups': [{'nos': ['A'], 'front': 'N'}]}))
                    cells = {(1, 60, 1): 'minecraft:stone'}
                    jw.build('outcome', 'feature', cells,
                             meta={'builder': {'kind': kind, 'path': str(path)}})

                    def process(cmd, **kwargs):
                        if '--apply' not in cmd:
                            return
                        if cmd[1].endswith('element_undo.py'):
                            jw.reg.change(Undo('outcome'), jw.apply)
                        elif outcome != 'no-new':
                            ticket = jw.reg.begin(Build('outcome', 'feature', cells))
                            if outcome == 'landed':
                                jw.apply(ticket.writes)
                                ticket.finish(jw.blocks.read_many(cells))
                            elif outcome == 'mismatched':
                                ticket.finish(jw.blocks.read_many(cells))

                    err = io.StringIO()
                    with mock.patch.object(reapply_feature, 'activate_world_from_args'), \
                            mock.patch('site_lock.check'), \
                            mock.patch('world_discovery.require_running_world'), \
                            mock.patch.object(reapply_feature.subprocess, 'run', side_effect=process), \
                            redirect_stderr(err):
                        code = reapply_feature.main(['--world', 'unit-test', 'outcome', '--apply', '--locked'])
                    self.assertEqual(code, 0 if outcome == 'landed' else 1)
                    if outcome != 'landed':
                        self.assertIn('Undo completed', err.getvalue())


class TestRefusalAdvice(unittest.TestCase):
    def test_refusal_reasons_leave_feature_intact_and_give_specific_advice(self):
        from pathlib import Path
        for reason in ('unknown', 'missing', 'invalid', 'wrong-name', 'dry-failure'):
            with self.subTest(reason=reason), JournalWorld() as jw:
                path = world_root() / 'site/builds/refused.json'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({
                    'name': 'other' if reason == 'wrong-name' else 'refused',
                    'ways': {'1': 'A'}, 'storeys': {'A': 1},
                    'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
                    'groups': [{'nos': ['A'], 'front': 'N'}]}))
                if reason == 'missing':
                    path.unlink()
                elif reason == 'invalid':
                    path.write_text('{bad json')
                meta = {} if reason == 'unknown' else {'builder': {'kind': 'spec', 'path': str(path)}}
                jw.build('refused', 'feature', {(1, 60, 1): 'minecraft:stone'}, meta=meta)
                err = io.StringIO()
                with mock.patch.object(reapply_feature, 'activate_world_from_args'), \
                        mock.patch('site_lock.check'), \
                        mock.patch('world_discovery.require_running_world'), \
                        mock.patch.object(reapply_feature.subprocess, 'run',
                                          side_effect=reapply_feature.subprocess.CalledProcessError(1, ['builder'])) as process, \
                        redirect_stderr(err):
                    code = reapply_feature.main(['--world', 'unit-test', 'refused', '--apply', '--locked'])
                self.assertEqual(code, 1)
                self.assertFalse(jw.reg.entries(plan='refused')[0].undone)
                self.assertEqual(process.call_count, 1 if reason == 'dry-failure' else 0)
                message = err.getvalue()
                self.assertIn('No undo attempted', message)
                self.assertNotIn('site_recover', message)
                self.assertNotIn('reconcile', message)
                self.assertNotIn('undo completed=False', message)
                if reason == 'unknown':
                    self.assertIn('redo (redesign', message)
                elif reason != 'dry-failure':
                    self.assertIn(str(path) if reason != 'wrong-name' else "'other'", message)

    def test_parent_holds_one_lock_for_explicit_world_worker(self):
        with mock.patch.object(reapply_feature, 'activate_world_from_args'), \
                mock.patch('site_lock.run', return_value=17) as run, \
                mock.patch.object(reapply_feature.subprocess, 'run') as process:
            code = reapply_feature.main(['--world', 'unit-test', 'demo', '--apply', '--owner', 'owner'])
        self.assertEqual(code, 17)
        owner, task, cmd = run.call_args.args
        self.assertEqual(owner, 'owner')
        self.assertIn('--locked', cmd)
        self.assertEqual(cmd[cmd.index('--world') + 1], 'unit-test')
        self.assertEqual(cmd[cmd.index('--owner') + 1], 'owner')
        process.assert_not_called()

    def test_undo_failure_stops_rebuild_and_reports_uncertain_undo(self):
        err = io.StringIO()
        with mock.patch.object(reapply_feature, 'activate_world_from_args'), \
                mock.patch.object(reapply_feature, 'prepare', return_value=['python3', 'builder.py']), \
                mock.patch('site_lock.check'), \
                mock.patch('world_discovery.require_running_world'), \
                mock.patch.object(reapply_feature.subprocess, 'run', side_effect=[
                    None, reapply_feature.subprocess.CalledProcessError(1, ['undo'])]) as process, \
                redirect_stderr(err):
            code = reapply_feature.main(['--world', 'unit-test', 'demo', '--apply', '--locked'])
        self.assertEqual(code, 1)
        self.assertEqual(process.call_count, 2)
        self.assertIn('Undo did not complete cleanly', err.getvalue())
        self.assertIn('No rebuild attempted', err.getvalue())
