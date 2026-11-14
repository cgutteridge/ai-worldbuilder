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

    def test_changed_or_unknown_script_identity_stops_before_undo(self):
        for source in ("NAME = 'other'\n", "NAME = unknown\n", "pass\n",
                       "NAME = 'demo'\nNAME = 'other'\n", "NAME = (\n"):
            with self.subTest(source=source), JournalWorld() as jw:
                path = jw.root / 'build_demo.py'
                path.write_text(source)
                cells = {(2, 60, 2): 'minecraft:stone'}
                jw.build('demo', 'feature', cells,
                         meta={'builder': {'kind': 'script', 'path': str(path)}})
                before = jw.reg.journal_path.read_bytes()
                err = io.StringIO()
                with mock.patch.object(reapply_feature, 'activate_world_from_args'), \
                        mock.patch('site_lock.check'), \
                        mock.patch('world_discovery.require_running_world'), \
                        mock.patch.object(reapply_feature.subprocess, 'run') as process, \
                        redirect_stderr(err):
                    code = reapply_feature.main(['--world', 'unit-test', 'demo', '--apply', '--locked'])
                self.assertEqual(code, 1)
                process.assert_not_called()
                self.assertEqual(jw.reg.journal_path.read_bytes(), before)
                self.assertEqual(jw.blocks.read_many(cells), cells)
                self.assertIn('No undo attempted', err.getvalue())

    def test_stale_preparation_for_either_builder_stops_before_undo(self):
        from cell_readiness import ReadinessPolicy
        for kind in ('spec', 'script'):
            with self.subTest(kind=kind), JournalWorld() as jw:
                path = jw.root / ('demo.json' if kind == 'spec' else 'build_demo.py')
                path.write_text(json.dumps({
                    'name': 'demo', 'ways': {'1': 'A'}, 'storeys': {'A': 1},
                    'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
                    'groups': [{'nos': ['A'], 'front': 'N'}]})
                    if kind == 'spec' else "NAME = 'demo'\n")
                jw.seed((0, 0), ghost_ground=True)
                cells = {(2, 60, 2): 'minecraft:stone'}
                jw.build('demo', 'feature', cells,
                         meta={'builder': {'kind': kind, 'path': str(path)}})
                evidence = mock.Mock(spec=['facts', 'candidates'])
                facts = {'revision': 'before', 'clip_coverage': 1.,
                         'aerial_coverage': 1., 'lidar_missing': []}
                evidence.facts.side_effect = lambda cell: dict(facts)
                evidence.candidates.return_value = []
                policy = ReadinessPolicy(jw.reg, evidence)
                policy.validate((0, 0))
                facts['revision'] = 'changed'
                before = jw.reg.journal_path.read_bytes()
                err = io.StringIO()
                with mock.patch.object(reapply_feature, 'activate_world_from_args'), \
                        mock.patch('site_lock.check'), \
                        mock.patch('world_discovery.require_running_world'), \
                        mock.patch('readiness_evidence.policy_for', return_value=policy), \
                        mock.patch.object(reapply_feature.subprocess, 'run') as process, \
                        redirect_stderr(err):
                    code = reapply_feature.main(['--world', 'unit-test', 'demo', '--apply', '--locked'])
                self.assertEqual(code, 1)
                self.assertEqual(process.call_count, 1)  # Builder dry run only.
                self.assertNotIn('--apply', process.call_args.args[0])
                self.assertEqual(jw.reg.journal_path.read_bytes(), before)
                self.assertEqual(jw.blocks.read_many(cells), cells)
                self.assertIn('validation stale', err.getvalue())
                self.assertIn('No undo attempted', err.getvalue())


class TestRecordedBuilder(unittest.TestCase):
    def spec(self, root, builder_name):
        spec = {'name': 'demo', 'ways': {'1': 'A'},
                'roofs': {'A': 'smooth_stone'},
                'groups': [{'nos': ['A'], 'front': 'N'}],
                'notes': 'Earlier builder: feature_build' if builder_name == 'house_build'
                         else 'Earlier builder: house_build'}
        if builder_name == 'feature_build':
            spec['storeys'] = {'A': 1}
            spec['roofs'] = {'A': {'kind': 'flat', 'material': 'smooth_stone'}}
        path = (root / f'{builder_name}.json').resolve()
        path.write_text(json.dumps(spec))
        return path

    def meta(self, path, builder_name):
        from builder_provenance import provenance
        with mock.patch('builder_provenance.tool_sha', return_value='1' * 40):
            return provenance(builder_name, path)

    def test_dry_run_uses_recorded_builder_for_each_spec_schema(self):
        for builder_name in ('house_build', 'feature_build'):
            with self.subTest(builder=builder_name), JournalWorld() as jw, \
                    mock.patch('active_world.world_root', side_effect=lambda: jw.root):
                path = self.spec(jw.root, builder_name)
                meta = self.meta(path, builder_name)
                self.assertEqual(meta['spec_path'], path.name)
                if builder_name == 'feature_build':
                    meta['builder'] = {'kind': 'spec', 'path': str(path)}
                cells = {(1, 60, 1): 'minecraft:stone'}
                jw.build('demo', 'feature', cells, meta=meta)
                before = jw.reg.journal_path.read_bytes()
                with mock.patch.object(reapply_feature, 'activate_world_from_args'), \
                        mock.patch.object(reapply_feature.subprocess, 'run') as process:
                    code = reapply_feature.main(['--world', 'unit-test', 'demo'])
                self.assertEqual(code, 0)
                process.assert_called_once_with(
                    [reapply_feature.sys.executable,
                     str(reapply_feature.TOOL_ROOT / 'scripts' / f'{builder_name}.py'),
                     str(path), '--world', 'unit-test'],
                    cwd=reapply_feature.TOOL_ROOT, check=True)
                self.assertEqual(jw.reg.journal_path.read_bytes(), before)
                self.assertEqual(jw.blocks.read_many(cells), cells)

    def test_unrecorded_plan_refuses_despite_a_plausible_spec(self):
        with JournalWorld() as jw:
            path = self.spec(jw.root, 'house_build')
            jw.build('demo', 'feature', {(1, 60, 1): 'minecraft:stone'},
                     meta={'spec_path': str(path)})
            before = jw.reg.journal_path.read_bytes()
            err = io.StringIO()
            with mock.patch.object(reapply_feature, 'activate_world_from_args'), \
                    mock.patch.object(reapply_feature.subprocess, 'run') as process, \
                    redirect_stderr(err):
                code = reapply_feature.main(['--world', 'unit-test', 'demo'])
            self.assertEqual(code, 1)
            process.assert_not_called()
            self.assertEqual(jw.reg.journal_path.read_bytes(), before)
            self.assertIn('unrecorded', err.getvalue())
            self.assertIn('builder_audit.py --world', err.getvalue())
            self.assertIn('No undo attempted', err.getvalue())

    def test_unavailable_recorded_builder_refuses_without_fallback(self):
        for missing in ('name', 'launcher'):
            with self.subTest(missing=missing), JournalWorld() as jw:
                path = self.spec(jw.root, 'feature_build')
                builder_name = 'retired_builder' if missing == 'name' else 'feature_build'
                meta = self.meta(path, builder_name)
                meta['builder'] = {'kind': 'spec', 'path': str(path)}
                jw.build('demo', 'feature', {(1, 60, 1): 'minecraft:stone'}, meta=meta)
                before = jw.reg.journal_path.read_bytes()
                err = io.StringIO()
                tool_root = reapply_feature.TOOL_ROOT if missing == 'name' else jw.root / 'missing-tool'
                with mock.patch.object(reapply_feature, 'TOOL_ROOT', tool_root), \
                        mock.patch.object(reapply_feature, 'activate_world_from_args'), \
                        mock.patch.object(reapply_feature.subprocess, 'run') as process, \
                        redirect_stderr(err):
                    code = reapply_feature.main(['--world', 'unit-test', 'demo'])
                self.assertEqual(code, 1)
                process.assert_not_called()
                self.assertEqual(jw.reg.journal_path.read_bytes(), before)
                self.assertIn('recorded builder', err.getvalue())
                self.assertIn(builder_name, err.getvalue())
                self.assertIn('No undo attempted', err.getvalue())

    def test_latest_landed_builder_wins(self):
        with JournalWorld() as jw:
            for builder_name in ('house_build', 'feature_build'):
                path = self.spec(jw.root, builder_name)
                jw.build('demo', 'feature', {(1, 60, 1): 'minecraft:stone'},
                         meta=self.meta(path, builder_name))
            cmd = reapply_feature.prepare('demo', registry=jw.reg)
            self.assertEqual(cmd, [reapply_feature.sys.executable,
                                   str(reapply_feature.TOOL_ROOT / 'scripts/feature_build.py'),
                                   str(path)])

    def test_latest_unrecorded_plan_does_not_reuse_an_older_builder(self):
        with JournalWorld() as jw:
            path = self.spec(jw.root, 'house_build')
            cells = {(1, 60, 1): 'minecraft:stone'}
            jw.build('demo', 'feature', cells, meta=self.meta(path, 'house_build'))
            jw.build('demo', 'feature', cells)
            with self.assertRaisesRegex(SystemExit, 'no replayable recorded builder'):
                reapply_feature.prepare('demo', registry=jw.reg)

    def test_recorded_house_builder_validates_spec_before_execution(self):
        for invalid in ('json', 'identity', 'schema'):
            with self.subTest(invalid=invalid), JournalWorld() as jw:
                path = self.spec(jw.root, 'house_build')
                jw.build('demo', 'feature', {(1, 60, 1): 'minecraft:stone'},
                         meta=self.meta(path, 'house_build'))
                if invalid == 'json':
                    path.write_text('{bad json')
                else:
                    spec = json.loads(path.read_text())
                    spec.update({'name': 'other'} if invalid == 'identity' else {'storeys': {'A': 1}})
                    path.write_text(json.dumps(spec))
                before = jw.reg.journal_path.read_bytes()
                err = io.StringIO()
                with mock.patch.object(reapply_feature, 'activate_world_from_args'), \
                        mock.patch.object(reapply_feature.subprocess, 'run') as process, \
                        redirect_stderr(err):
                    code = reapply_feature.main(['--world', 'unit-test', 'demo'])
                self.assertEqual(code, 1)
                process.assert_not_called()
                self.assertEqual(jw.reg.journal_path.read_bytes(), before)
                self.assertIn('No undo attempted', err.getvalue())


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
                            mock.patch('readiness_evidence.require_feature_ready'), \
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
                mock.patch('readiness_evidence.require_feature_ready'), \
                mock.patch.object(reapply_feature.subprocess, 'run', side_effect=[
                    None, reapply_feature.subprocess.CalledProcessError(1, ['undo'])]) as process, \
                redirect_stderr(err):
            code = reapply_feature.main(['--world', 'unit-test', 'demo', '--apply', '--locked'])
        self.assertEqual(code, 1)
        self.assertEqual(process.call_count, 2)
        self.assertIn('Undo did not complete cleanly', err.getvalue())
        self.assertIn('No rebuild attempted', err.getvalue())
