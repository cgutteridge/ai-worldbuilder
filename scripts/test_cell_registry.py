#!/usr/bin/env python3
"""cell_registry: the CLI and helpers over the cell journal (ADR 004). Invariants of the fold
itself are tested in test_cell_journal.py."""
from __future__ import annotations

import contextlib
import io
import json
import unittest

import cell_registry as R
from unittest import mock

import site_kit
import site_lock
from journal_testkit import FakePaced, JournalWorld, LiveApplyCase


def run(*argv) -> tuple[int, str]:
    """Invoke the registry CLI with a dummy ``--world`` (activation mocked).

    The suite already selects a disposable world via ``unit_test_world``;
    ``--root`` overrides the registry path. Activation is mocked so
    ``set_world`` does not require a real ``worlds/<name>`` checkout.
    """
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        with mock.patch.object(R, 'activate_world_from_args'):
            code = R.main(['--world', 'unit-test', *argv])
    return code, out.getvalue()


class RegistryCliTest(unittest.TestCase):
    def setUp(self):
        self.jw = JournalWorld()
        self.jw.__enter__()
        self.addCleanup(self.jw.__exit__)
        self.root = str(self.jw.root)
        self.jw.seed((48, -44), ground=True, features=('q001_x',))

    def view(self):
        return R.view_registry(self.root).cell((48, -44))

    def test_status_and_query_read_the_journal(self):
        code, out = run('--root', self.root, 'status', '--cell', '48,-44')
        self.assertEqual(code, 0)
        self.assertIn('ground=generated trees=False decor=False marker=none', out)
        self.assertIn('feature q001_x: built', out)
        code, out = run('--root', self.root, 'query', '--feature', 'q001_x')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out[:out.index('built:')])['cells'], ['48,-44'])
        self.assertEqual(run('--root', self.root, 'query', '--feature', 'nope')[0], 1)

    def test_hold_set_clear_are_journal_notes(self):
        self.assertEqual(run('--root', self.root, 'hold', 'set', '48,-44', '--reason', 'flood')[0], 0)
        self.assertEqual(self.view().hold, 'flood')
        self.assertEqual(run('--root', self.root, 'hold', 'clear', '48,-44')[0], 0)
        self.assertIsNone(self.view().hold)

    def test_dismiss_coverage_needs_an_open_warning(self):
        from cell_journal import Note
        self.jw.reg.change(Note((48, -44), 'coverage', 'shops unmapped', 'shops'))
        self.assertEqual(R.coverage_warnings(self.view()), [{'id': 'shops', 'detail': 'shops unmapped'}])
        self.assertEqual(run('--root', self.root, 'dismiss-coverage', '48,-44', '--warning-id', 'x',
                             '--reason', 'r')[0], 2)
        self.assertEqual(run('--root', self.root, 'dismiss-coverage', '48,-44', '--warning-id', 'shops',
                             '--reason', 'checked')[0], 0)
        self.assertEqual(R.coverage_warnings(self.view()), [])

    def test_note_records_a_review(self):
        run('--root', self.root, 'note', '48,-44', '--ref', 'trees-over-roof:1,2,3,4', '--body', 'crown kept')
        self.assertEqual([(n.kind, n.ref, n.body) for n in self.view().notes],
                         [('review', 'trees-over-roof:1,2,3,4', 'crown kept')])

    def test_register_feature_is_gone(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                R.main(['--root', self.root, 'register-feature', 'x', '--name', 'X', '--cells', '1,1'])

    def test_add_issue_and_issues_commands_retired(self):
        """#121: I-nnn ISSUES.txt CLI is gone; snags use scripts/issues.py."""
        for argv in (
            ['--root', self.root, 'add-issue', '--cell', '48,-44', '--reason', 'x'],
            ['--root', self.root, 'issues'],
        ):
            with self.assertRaises(SystemExit):
                with contextlib.redirect_stderr(io.StringIO()):
                    with mock.patch.object(R, 'activate_world_from_args'):
                        R.main(['--world', 'unit-test', *argv])

    def test_empty_and_feature_map(self):
        reg = R.view_registry(self.root)
        self.assertTrue(R.is_empty(reg.cell((0, 0))))
        self.assertFalse(R.is_empty(reg.cell((48, -44))))
        self.assertEqual(R.feature_cells(reg), {'q001_x': {'status': 'built', 'cells': ['48,-44']}})

    def test_latest_cells_merge_later_builds_and_skip_ghosts(self):
        self.jw.build('ghost_0_0_ground', 'ghost-ground', {(1, 40, 1): 'minecraft:gray_concrete'}, cell=(0, 0))
        self.jw.build('a', 'feature', {(1, 50, 1): 'minecraft:stone'})
        self.jw.build('b', 'feature', {(1, 50, 1): 'minecraft:bricks'}, modifies=('a',))
        cells = R.latest_cells(R.view_registry(self.root))
        self.assertEqual(cells[(1, 50, 1)], ('b', 'minecraft:bricks'))
        self.assertNotIn((1, 40, 1), cells)

    def test_view_registry_refuses_block_reads(self):
        from block_store import BlockStoreError
        with self.assertRaises(BlockStoreError):
            R.view_registry(self.root).blocks.read((0, 0, 0))

    def test_propose_feature_candidates_calls_missing_features(self):
        """#118: propose-features uses missing_features and does not swallow errors."""
        mapped = [{
            'kind': 'houses', 'label': 'University Road (6 houses)', 'street': 'University Road',
            'refs': ['w1'], 'bbox': (6278, -5476, 6300, -5443), 'portals': [], 'pictures': [],
        }]
        with mock.patch('cell_features.missing_features', return_value=mapped) as miss:
            got = R.propose_feature_candidates('48,-44')
        miss.assert_called_once_with(48, -44)
        self.assertEqual(got, mapped)
        with mock.patch('cell_features.missing_features', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                R.propose_feature_candidates('48,-44')

    def test_cells_above_ghost_names_cells_and_counts_each_kind(self):
        """#135: one view of every cell with anything above ghost, for purge counting."""
        from cell_journal import Note
        # Ghost-only and planned-only must not appear; real layers and built features must.
        self.jw.seed((0, 0), ghost_ground=True)
        self.jw.reg.change(Note((1, 1), 'plan_feature', 'planned only', 'planned_house'))
        self.jw.seed((49, -44), ground=True, trees=True, features=('barn',))
        reg = R.view_registry(self.root)
        report = R.cells_above_ghost(reg)
        self.assertEqual(report['cells'], [
            {'cell': '48,-44', 'ground': 1, 'trees': 0, 'decor': 0, 'features': 1},
            {'cell': '49,-44', 'ground': 1, 'trees': 1, 'decor': 0, 'features': 1},
        ])
        self.assertEqual(report['totals'],
                         {'cells': 2, 'ground': 2, 'trees': 1, 'decor': 0, 'features': 2})
        code, out = run('--root', self.root, 'above-ghost')
        self.assertEqual(code, 0)
        self.assertIn('cells: 2', out)
        self.assertIn('ground: 2  trees: 1  decor: 0  features: 2', out)
        self.assertIn('48,-44: ground=1 trees=0 decor=0 features=1', out)
        self.assertIn('49,-44: ground=1 trees=1 decor=0 features=1', out)
        self.assertNotIn('0,0:', out)
        self.assertNotIn('1,1:', out)
        code, out = run('--root', self.root, 'above-ghost', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), report)


CELL = (48, -44)
X0, Z0 = 6150, -5600
STONE, LOG = 'minecraft:stone', 'minecraft:oak_log'


class ReconcileCliTest(LiveApplyCase):
    """``cell_registry.py reconcile`` over a journal written by the real apply, a fake MCP and MemoryBlocks."""

    def setUp(self):
        super().setUp()
        plan = site_kit.Plan('p')
        plan.fill((X0, 60, Z0), (X0 + 1, 60, Z0), STONE)
        plan.fill((X0, 61, Z0), (X0 + 1, 61, Z0), LOG)
        self.run_apply(plan, cell=CELL)
        gate = mock.patch.object(site_lock, 'check', lambda owner=None: None)
        gate.start()
        self.addCleanup(gate.stop)

    def reconcile(self, *argv):
        return run('reconcile', *argv)

    def test_dry_run_selects_disk_and_fix_uses_live_session(self):
        with mock.patch.object(site_kit, 'disk_blocks', return_value=self.blocks) as disk, \
             mock.patch.object(site_kit, 'live_blocks', return_value=self.blocks) as live:
            self.assertEqual(self.reconcile()[0], 0)
            disk.assert_called_once_with()
            live.assert_not_called()
            disk.reset_mock()
            self.assertEqual(self.reconcile('--fix')[0], 0)
            disk.assert_not_called()
            live.assert_called()

    def test_clean_world_exits_zero(self):
        code, out = self.reconcile()
        self.assertEqual(code, 0)
        self.assertIn('clean', out)

    def test_dry_run_reports_by_kind_and_changes_nothing(self):
        self.blocks.write((X0, 60, Z0), 'minecraft:air')
        self.blocks.write((X0 + 1, 61, Z0), 'minecraft:dirt')
        seq_before = len(self.entries())
        calls_before = len(FakePaced.calls)
        code, out = self.reconcile()
        self.assertEqual(code, 1)
        self.assertLess(out.index('missing: 1'), out.index('foreign: 1'))
        self.assertIn('48,-44', out)
        self.assertIn(f'{X0},60,{Z0}: want minecraft:stone, got minecraft:air', out)
        self.assertIn(f'{X0 + 1},61,{Z0}: want minecraft:oak_log, got minecraft:dirt', out)
        self.assertIn('dry run', out)
        self.assertEqual(self.blocks.read((X0, 60, Z0)), 'minecraft:air')
        self.assertEqual(len(self.entries()), seq_before)
        self.assertNotIn('block_fill_batch', FakePaced.calls[calls_before:])
        self.assertFalse((self.t / 'LOCK.json').exists())          # a dry run claims nothing

    def test_cell_arguments_scope_the_report(self):
        self.blocks.write((X0, 60, Z0), 'minecraft:air')
        self.assertEqual(self.reconcile('0,0')[0], 0)
        self.assertEqual(self.reconcile('48,-44')[0], 1)

    def test_bad_cell_is_a_usage_error(self):
        self.assertEqual(self.reconcile('nope')[0], 2)

    def test_fix_recommits_the_recorded_plan(self):
        self.blocks.write((X0, 60, Z0), 'minecraft:air')
        self.blocks.write((X0 + 1, 61, Z0), 'minecraft:dirt')
        code, out = self.reconcile('--fix')
        self.assertEqual(code, 0)
        self.assertIn('[fixed]', out)
        self.assertIn('repaired 2 of 2', out)
        self.assertEqual(self.blocks.read((X0, 60, Z0)), STONE)
        self.assertEqual(self.blocks.read((X0 + 1, 61, Z0)), LOG)
        self.assertEqual(self.reconcile()[0], 0)

    def test_fix_lands_an_interrupted_apply(self):
        plan = site_kit.Plan('q')
        plan.fill((X0 + 2, 60, Z0), (X0 + 3, 60, Z0), STONE)
        FakePaced.fill_limit = 0
        with self.assertRaises(RuntimeError):
            self.run_apply(plan, cell=CELL)
        FakePaced.fill_limit = None
        self.assertEqual(self.entries('q')[-1].status, 'begun')
        code, out = self.reconcile()
        self.assertEqual(code, 1)
        self.assertIn('in-flight: 2 in 1 cell(s)', out)
        self.assertEqual(self.reconcile('--fix')[0], 0)
        self.assertEqual(self.entries('q')[-1].status, 'landed')
        self.assertEqual(self.blocks.read((X0 + 3, 60, Z0)), STONE)

    def test_help_names_the_split_with_site_recover(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            R.main(['reconcile', '--help'])
        text = ' '.join(out.getvalue().split())
        self.assertIn('site_recover = resolve an interrupted apply (land or roll back)', text)
        self.assertIn('reconcile = detect and repair drift forward', text)


class DiskBlocksTest(unittest.TestCase):
    def test_saves_before_opening_disk_store(self):
        live = mock.Mock(region='/tmp/test-region')
        events = mock.Mock()
        events.attach_mock(live.flush, 'save')
        with mock.patch.object(site_kit, 'live_blocks', return_value=live), \
             mock.patch('region_blocks.RegionBlocks') as disk:
            events.attach_mock(disk, 'open_disk')
            self.assertIs(site_kit.disk_blocks(), disk.return_value)
        self.assertEqual(events.mock_calls, [mock.call.save(), mock.call.open_disk(live.region)])

    def test_failed_save_never_opens_disk_store(self):
        live = mock.Mock()
        live.flush.side_effect = RuntimeError('save failed')
        with mock.patch.object(site_kit, 'live_blocks', return_value=live), \
             mock.patch('region_blocks.RegionBlocks') as disk:
            with self.assertRaisesRegex(RuntimeError, 'save failed'):
                site_kit.disk_blocks()
            disk.assert_not_called()


if __name__ == '__main__':
    unittest.main()
