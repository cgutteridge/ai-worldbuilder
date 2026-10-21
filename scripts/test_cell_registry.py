#!/usr/bin/env python3
"""cell_registry: the CLI and helpers over the cell journal (ADR 004). Invariants of the fold
itself are tested in test_cell_journal.py."""
from __future__ import annotations

import contextlib
import io
import json
import unittest

import cell_registry as R
from journal_testkit import JournalWorld


def run(*argv) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = R.main(list(argv))
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


if __name__ == '__main__':
    unittest.main()
