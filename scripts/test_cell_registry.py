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


if __name__ == '__main__':
    unittest.main()
