"""Builder provenance (#248): recording, journal schema 1->2 migration, and the read-only stale/unrecorded listing.
Synthetic data only: tmp world root, MemoryBlocks, a throwaway git repo."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import builder_audit as A
import builder_provenance as BP
from block_store import MemoryBlocks
from cell_journal import AIR, Build, CellRegistry, JournalError

STONE = 'minecraft:stone'


def cells(tx=0):
    return {(16 * tx + i, 64, 0): STONE for i in range(3)}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.blocks = MemoryBlocks(default=AIR, strict_flush=True)
        self.reg = CellRegistry(self.root, self.blocks, None, cell_size=16)

    def build(self, name, tx=0, meta=None):
        self.reg.change(Build(name, 'feature', cells(tx), cell=(tx, 0), meta=meta),
                        lambda w: self.blocks.write_many(w.cells))

    def journal(self):
        return self.root / 'registry' / 'journal.jsonl'

    def reload(self):
        return CellRegistry(self.root, self.blocks, None, cell_size=16)

    def spec(self, text='{"name":"a","b":1}'):
        p = self.root / 'spec.json'
        p.write_text(text)
        return p


class RecordingTest(Base):
    def test_provenance_records_name_sha_and_stable_spec_hash(self):
        p = self.spec('{"name":"a","b":1}')
        with mock.patch.object(BP, 'tool_sha', return_value='abc123'), \
                mock.patch.object(BP, 'resolve_builder_path', side_effect=lambda x: Path(x)):
            rec = BP.provenance('house_build', p)
            self.spec('{ "b": 1,\n "name": "a" }')           # same content, other order/whitespace
            same = BP.provenance('house_build', p)
            self.spec('{"name":"a","b":2}')
            changed = BP.provenance('house_build', p)
        self.assertEqual((rec['builder_name'], rec['builder_sha']), ('house_build', 'abc123'))
        self.assertEqual(rec['spec_hash'], same['spec_hash'])
        self.assertNotEqual(rec['spec_hash'], changed['spec_hash'])

    def test_unknown_sha_is_left_out_not_invented(self):
        with mock.patch.object(BP, 'tool_sha', return_value=None), \
                mock.patch.object(BP, 'resolve_builder_path', side_effect=lambda x: Path(x)):
            rec = BP.provenance('feature_build', self.spec())
        self.assertNotIn('builder_sha', rec)

    def test_new_entry_carries_schema_and_meta(self):
        self.build('f1', meta={'builder_name': 'feature_build', 'builder_sha': 'a1', 'spec_hash': 'h'})
        begin = json.loads(self.journal().read_text().splitlines()[0])
        self.assertEqual(begin['schema'], 2)
        e = self.reload().entries(plan='f1')[0]
        self.assertEqual((e.schema, e.meta['builder_sha']), (2, 'a1'))


class SchemaMigrationTest(Base):
    def test_legacy_schema1_entries_load_and_list_unrecorded(self):
        self.build('old')
        lines = [json.loads(l) for l in self.journal().read_text().splitlines()]
        for l in lines:
            l.pop('schema', None)                          # a pre-#248 line has no schema key
            l.pop('meta', None)
        self.journal().write_text(''.join(json.dumps(l, sort_keys=True, separators=(',', ':')) + '\n' for l in lines))
        reg = self.reload()
        e = reg.entries(plan='old')[0]
        self.assertEqual((e.schema, e.status), (1, 'landed'))
        res = A.audit(reg.entries())
        self.assertEqual([r['plan'] for r in res['unrecorded']], ['old'])
        self.assertEqual(res['stale'] + res['current'], [])
        # an old and a new line may coexist
        self.build('new', tx=1, meta={'builder_name': 'b', 'builder_sha': 's', 'spec_hash': 'h'})
        self.assertEqual([e.schema for e in self.reload().entries()][:1], [1])

    def test_future_schema_is_refused(self):
        self.build('x')
        lines = self.journal().read_text().splitlines()
        first = json.loads(lines[0])
        first['schema'] = 99
        self.journal().write_text(json.dumps(first) + '\n' + '\n'.join(lines[1:]) + '\n')
        with self.assertRaises(JournalError):
            self.reload().entries()


class ListingTest(Base):
    def meta(self, sha='old1', spec_hash='h1', spec_path=None):
        m = {'builder_name': 'house_build', 'builder_sha': sha, 'spec_hash': spec_hash}
        if spec_path:
            m['spec_path'] = spec_path
        return m

    def run_audit(self, older=None, current='h1'):
        with mock.patch.object(A, 'current_hash', return_value=current):
            return A.audit(self.reload().entries(), older)

    def test_builder_older_than_since(self):
        self.build('a', meta=self.meta(sha='old1'))
        self.build('b', tx=1, meta=self.meta(sha='new1'))
        res = self.run_audit(older=lambda sha: sha == 'old1')
        self.assertEqual([(r['plan'], r['reasons']) for r in res['stale']], [('a', ['builder-older'])])
        self.assertEqual([r['plan'] for r in res['current']], ['b'])

    def test_spec_hash_differs(self):
        self.build('a', meta=self.meta())
        res = self.run_audit(current='h2')
        self.assertEqual([(r['plan'], r['reasons']) for r in res['stale']], [('a', ['spec-changed'])])

    def test_unrecorded_listed_separately_and_dirty_sha_compared_by_base(self):
        self.build('plain')
        self.build('dirty', tx=1, meta=self.meta(sha='old1+dirty'))
        res = self.run_audit(older=lambda sha: sha == 'old1')
        self.assertEqual([r['plan'] for r in res['unrecorded']], ['plain'])
        self.assertEqual([r['plan'] for r in res['stale']], ['dirty'])

    def test_undone_plans_are_not_listed(self):
        self.build('a', meta=self.meta())
        from cell_journal import Undo
        self.reg.change(Undo('a'), lambda w: self.blocks.write_many(w.cells))
        res = self.run_audit(current='zzz')
        self.assertEqual(sum(map(len, res.values())), 0)

    def test_missing_spec_is_not_a_change(self):
        self.build('a', meta=self.meta(spec_path='gone.json'))
        with mock.patch.object(A, 'resolve_builder_path', side_effect=lambda x: self.root / x):
            res = A.audit(self.reload().entries())
        self.assertEqual([r['plan'] for r in res['current']], ['a'])


class GitAncestryTest(unittest.TestCase):
    def test_strict_ancestor_only(self):
        with tempfile.TemporaryDirectory() as d:
            def git(*a):
                return subprocess.run(['git', '-C', d, '-c', 'user.name=t', '-c', 'user.email=t@t', *a],
                                      capture_output=True, text=True, check=True).stdout.strip()
            git('init', '-q')
            shas = []
            for i in range(3):
                Path(d, 'f').write_text(str(i))
                git('add', 'f')
                git('commit', '-q', '-m', str(i))
                shas.append(git('rev-parse', 'HEAD'))
            older = A.git_older_than(shas[1], root=d)
            self.assertTrue(older(shas[0]))
            self.assertFalse(older(shas[1]))
            self.assertFalse(older(shas[2]))
            with self.assertRaises(SystemExit):
                A.git_older_than('nope', root=d)


if __name__ == '__main__':
    unittest.main()
