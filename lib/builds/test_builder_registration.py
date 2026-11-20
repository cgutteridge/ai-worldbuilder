"""Explicit legacy registrations persist without writing blocks or inventing build-time provenance."""
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import builder_registration as registration
import feature_reapply
from builder_audit import audit
from builder_provenance import describe_builder
from cell_journal import Build, CellRegistry, InFlight, Note, Refused, Undo
from journal_testkit import JournalWorld, LiveApplyCase


def write_spec(root, builder='house_build', name='demo'):
    """Write a small spec for either supported schema into the disposable world."""
    spec = {'name': name, 'ways': {'1': 'A'}, 'roofs': {'A': 'bricks'},
            'groups': [{'nos': ['A'], 'front': 'N'}]}
    if builder == 'feature_build':
        spec.update(storeys={'A': 1}, roofs={'A': {'kind': 'flat', 'material': 'bricks'}})
    path = root / 'site/builds/demo.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec))
    return path


class RegistrationTest(unittest.TestCase):
    def test_registration_is_one_append_only_note_and_reapply_uses_it_after_reload(self):
        for builder in ('house_build', 'feature_build'):
            with self.subTest(builder=builder), JournalWorld() as jw, \
                    mock.patch('active_world.world_root', side_effect=lambda: jw.root):
                path = write_spec(jw.root, builder)
                cells = {(1, 60, 1): 'minecraft:bricks'}
                receipt = jw.build('demo', 'feature', cells)
                before = jw.reg.journal_path.read_bytes()
                view_before = jw.reg.cell((0, 0))
                revision_before = jw.reg.planning_revision()
                intent = registration.prepare('demo', receipt.seq, builder, 'site/builds/demo.json',
                                              'Verified spec against the legacy build', jw.reg)
                self.assertEqual(jw.reg.journal_path.read_bytes(), before)
                jw.reg.change(intent)
                after = jw.reg.journal_path.read_bytes()
                self.assertTrue(after.startswith(before))
                lines = after[len(before):].decode().splitlines()
                self.assertEqual(len(lines), 1)
                self.assertEqual(json.loads(lines[0])['op'], 'note')
                fresh = CellRegistry(jw.root, jw.blocks)
                entry, = fresh.entries(plan='demo')
                self.assertNotIn('builder_sha', entry.meta)
                self.assertNotIn('spec_hash', entry.meta)  # Reviewed hash is inside the registration.
                self.assertEqual(entry.meta['builder_registration']['reason'],
                                 'Verified spec against the legacy build')
                described = describe_builder('demo', fresh.entries())
                self.assertEqual(described['source'], 'registered')
                self.assertEqual(described['path'], 'site/builds/demo.json')
                cmd = feature_reapply.prepare('demo', registry=fresh)
                self.assertEqual(cmd, [feature_reapply.sys.executable,
                                       str(feature_reapply.TOOL_ROOT / 'scripts' / f'{builder}.py'),
                                       str(path.resolve())])
                report = audit(fresh.entries())
                self.assertEqual(report['unrecorded'][0]['registered_builder']['name'], builder)
                self.assertEqual(fresh.cell((0, 0)), view_before)
                self.assertEqual(fresh.planning_revision(), revision_before)
                self.assertEqual(jw.blocks.read_many(cells), cells)

    def test_invalid_registration_does_not_change_the_journal_or_blocks(self):
        for problem in ('missing-spec', 'invalid-json', 'wrong-name', 'wrong-schema',
                        'unsupported-builder', 'no-reason', 'wrong-seq', 'recorded',
                        'already-registered', 'undone', 'newer-build', 'in-flight'):
            with self.subTest(problem=problem), JournalWorld() as jw, \
                    mock.patch('active_world.world_root', side_effect=lambda: jw.root):
                path = write_spec(jw.root)
                cells = {(1, 60, 1): 'minecraft:bricks'}
                meta = {'builder': {'kind': 'spec', 'path': str(path)}} if problem == 'recorded' else None
                seq = jw.build('demo', 'feature', cells, meta=meta).seq
                builder, reason = 'house_build', 'Verified original spec and builder'
                if problem == 'missing-spec':
                    path.unlink()
                elif problem == 'invalid-json':
                    path.write_text('{bad json')
                elif problem == 'wrong-name':
                    write_spec(jw.root, name='another')
                elif problem == 'wrong-schema':
                    write_spec(jw.root, builder='feature_build')
                elif problem == 'unsupported-builder':
                    builder = 'arbitrary_command'
                elif problem == 'no-reason':
                    reason = ' '
                elif problem == 'wrong-seq':
                    seq += 100
                elif problem == 'already-registered':
                    jw.reg.change(registration.prepare('demo', seq, builder, path, reason, jw.reg))
                elif problem == 'undone':
                    jw.reg.change(Undo('demo'), jw.apply)
                elif problem == 'newer-build':
                    jw.build('demo', 'feature', cells)
                elif problem == 'in-flight':
                    jw.reg.begin(Build('neighbour', 'feature', {(2, 60, 2): 'minecraft:stone'}))
                before = jw.reg.journal_path.read_bytes()
                blocks = dict(jw.blocks._blocks)
                with self.assertRaises((Refused, InFlight, SystemExit)):
                    registration.prepare('demo', seq, builder, path, reason, jw.reg)
                self.assertEqual(jw.reg.journal_path.read_bytes(), before)
                self.assertEqual(jw.blocks._blocks, blocks)

    def test_registration_does_not_transfer_to_a_newer_unrecorded_build(self):
        with JournalWorld() as jw:
            path = write_spec(jw.root)
            cells = {(1, 60, 1): 'minecraft:bricks'}
            seq = jw.build('demo', 'feature', cells).seq
            note = registration.prepare('demo', seq, 'house_build', path, 'Reviewed spec', jw.reg)
            jw.reg.change(note)
            jw.build('demo', 'feature', cells)
            with self.assertRaisesRegex(SystemExit, 'no replayable recorded builder'):
                feature_reapply.prepare('demo', jw.reg)

    def test_stale_prepared_note_and_malformed_notes_are_refused(self):
        with JournalWorld() as jw:
            path = write_spec(jw.root)
            cells = {(1, 60, 1): 'minecraft:bricks'}
            seq = jw.build('demo', 'feature', cells).seq
            note = registration.prepare('demo', seq, 'house_build', path, 'Reviewed spec', jw.reg)
            jw.build('demo', 'feature', cells)
            before = jw.reg.journal_path.read_bytes()
            with self.assertRaisesRegex(Refused, 'latest live'):
                jw.reg.change(note)
            for body in ('{}', 'null', '{bad json', json.dumps({'builder': []})):
                with self.subTest(body=body), self.assertRaises(Refused):
                    jw.reg.change(Note((0, 0), 'builder_registration', body, str(seq)))
            self.assertEqual(jw.reg.journal_path.read_bytes(), before)

    def test_dry_run_changes_nothing_and_apply_requires_the_world_lock(self):
        with JournalWorld() as jw, \
                mock.patch('active_world.world_root', side_effect=lambda: jw.root), \
                mock.patch.object(registration, 'activate_world_from_args'):
            write_spec(jw.root)
            seq = jw.build('demo', 'feature', {(1, 60, 1): 'minecraft:bricks'}).seq
            argv = ['--world', 'unit-test', 'demo', '--seq', str(seq), '--builder', 'house_build',
                    '--spec', 'site/builds/demo.json', '--reason', 'Verified old spec']
            before = jw.reg.journal_path.read_bytes()
            with redirect_stdout(io.StringIO()):
                self.assertEqual(registration.main(argv), 0)
            self.assertEqual(jw.reg.journal_path.read_bytes(), before)
            with mock.patch('site_lock.run', return_value=0) as locked:
                self.assertEqual(registration.main(argv + ['--apply']), 0)
                command = locked.call_args.args[2]
                self.assertIn('--locked', command)
                self.assertEqual(command[command.index('--world') + 1], 'unit-test')
            with mock.patch('site_lock.check', side_effect=SystemExit('lock required')), \
                    redirect_stderr(io.StringIO()):
                self.assertEqual(registration.main(argv + ['--apply', '--locked']), 1)
            self.assertEqual(jw.reg.journal_path.read_bytes(), before)
            with mock.patch('site_lock.check') as checked, redirect_stdout(io.StringIO()):
                self.assertEqual(registration.main(argv + ['--apply', '--locked']), 0)
                checked.assert_called_once_with()
            self.assertEqual(describe_builder('demo', jw.reg.entries())['source'], 'registered')

    def test_changed_or_missing_registered_spec_still_refuses_before_undo(self):
        for problem in ('missing', 'wrong-name', 'wrong-schema'):
            with self.subTest(problem=problem), JournalWorld() as jw:
                path = write_spec(jw.root)
                cells = {(1, 60, 1): 'minecraft:bricks'}
                seq = jw.build('demo', 'feature', cells).seq
                jw.reg.change(registration.prepare('demo', seq, 'house_build', path, 'Reviewed spec', jw.reg))
                if problem == 'missing':
                    path.unlink()
                else:
                    write_spec(jw.root, builder='feature_build' if problem == 'wrong-schema' else 'house_build',
                               name='another' if problem == 'wrong-name' else 'demo')
                before = jw.reg.journal_path.read_bytes()
                with self.assertRaises(SystemExit):
                    feature_reapply.prepare('demo', jw.reg)
                self.assertEqual(jw.reg.journal_path.read_bytes(), before)
                self.assertEqual(jw.blocks.read_many(cells), cells)


class RegisteredReapplyTest(LiveApplyCase):
    def test_registered_house_undoes_first_then_builds_without_an_override(self):
        import house_build
        from site_kit import Plan
        path = write_spec(self.t)
        old = Plan('demo')
        old.set(1, 60, 1, 'bricks')
        old.set(2, 60, 1, 'bricks')
        self.run_apply(old)
        seq = self.entries('demo')[0].seq
        reg = self.reg()
        reg.change(registration.prepare('demo', seq, 'house_build', path, 'Reviewed spec', reg))
        new = Plan('demo')
        new.set(1, 60, 1, 'stone')

        def process(cmd, **kwargs):
            self.assertNotIn('--overwrite', cmd)
            if cmd[1].endswith('element_undo.py'):
                self.run_undo('demo')
            else:
                house_build.run(path, do_apply='--apply' in cmd)

        err = io.StringIO()
        with mock.patch.object(feature_reapply, 'activate_world_from_args'), \
                mock.patch('world_discovery.require_running_world'), mock.patch('site_lock.check'), \
                mock.patch('readiness_evidence.require_feature_ready'), \
                mock.patch('builder_provenance.tool_sha', return_value='1' * 40), \
                mock.patch.object(house_build, 'build', return_value=new), \
                mock.patch.object(house_build, 'preview'), \
                mock.patch.object(feature_reapply.subprocess, 'run', side_effect=process), \
                redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = feature_reapply.main(['--world', 'unit-test', 'demo', '--apply', '--locked'])
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(self.blocks.read((2, 60, 1)), 'minecraft:air')
        self.assertEqual(self.blocks.read((1, 60, 1)), 'minecraft:stone')
        self.assertTrue(self.entries('demo')[0].undone)
        self.assertEqual(describe_builder('demo', self.entries('demo'))['source'], 'recorded')
