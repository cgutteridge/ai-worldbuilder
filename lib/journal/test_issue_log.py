#!/usr/bin/env python3
"""Tests for issues and snags (task #120): synthetic stores and journals only."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import issue_log as L

FEATURE = L.parse_element('feature:q001_jubilee')
GROUND = L.parse_element('ground:48,-44')


class _Root(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / 'registry').mkdir()
        self.pic = self.root / 'shot.png'
        self.pic.write_bytes(b'png')

    def tearDown(self):
        self._tmp.cleanup()

    def snag(self, issue_id=None, title='Ground reads plaza as lawn', words='grass everywhere, this is a brick plaza',
             elements=None, **kw):
        kw.setdefault('source', 'user')
        kw.setdefault('confidence', 'confirmed')
        if kw['source'] == 'user':
            kw.setdefault('image', str(self.pic))
        return L.add_snag(self.root, issue_id=issue_id, new_title=None if issue_id else title,
                          words=words, elements=elements or [GROUND], **kw)

    def land_and_confirm(self, issue_id, snag_id='S-001'):
        """Fix an issue on ground 48,-44, journal a redesign, sync, and confirm it."""
        L.log_fix(self.root, issue_id, summary='fix', merged_at='2026-10-26T10:00:00Z', checks={snag_id: 'looks right'})
        self.journal(*build(1, '2026-10-26T09:00:00Z', 'ground', 'g1', [48, -44], 'aaa'),
                     *build(3, '2026-10-26T11:00:00Z', 'ground', 'g2', [48, -44], 'bbb'))
        L.sync(self.root)
        L.verdict(self.root, issue_id, all_fixed=True)

    def journal(self, *lines):
        (self.root / 'registry' / 'journal.jsonl').write_text('\n'.join(json.dumps(x) for x in lines) + '\n')


def undo(seq, ts, targets, status='landed'):
    return [{'op': 'begin', 'seq': seq, 'kind': 'undo', 'plan': 'undo:x', 'pass': 'ground', 'cell': [48, -44],
             'blob': 'u', 'ts': ts, 'targets': targets}, {'op': 'finish', 'seq': seq + 1, 'ref': seq, 'status': status}]


def build(seq, ts, pass_, plan, cell, blob, status='landed'):
    return [{'op': 'begin', 'seq': seq, 'kind': 'build', 'plan': plan, 'pass': pass_, 'cell': cell,
             'blob': blob, 'ts': ts}, {'op': 'finish', 'seq': seq + 1, 'ref': seq, 'status': status}]


class IntakeTest(_Root):
    def test_new_issue_holds_the_snag_and_copies_the_picture(self):
        issue, snag = self.snag()
        self.assertEqual(issue['id'], 'I-001')
        self.assertEqual(snag['id'], 'S-001')
        self.assertTrue((self.root / 'registry' / snag['image']).exists())
        self.assertEqual(L.verify(self.root), [])

    def test_user_snag_needs_a_picture_but_agent_snag_does_not(self):
        with self.assertRaises(L.IssueError):
            self.snag(image=None)
        issue, snag = self.snag(source='agent', confidence='suspected', words='ground stalled at 0% CPU')
        self.assertIsNone(snag['image'])

    def test_snag_needs_an_element(self):
        with self.assertRaises(L.IssueError):
            L.add_snag(self.root, issue_id=None, new_title='x', words='w', elements=[],
                       source='agent', confidence='suspected')

    def test_more_evidence_goes_on_the_existing_issue(self):
        issue, _ = self.snag()
        issue2, snag2 = self.snag(issue['id'], words='grey road not tarmac')
        self.assertEqual(issue2['id'], issue['id'])
        self.assertEqual(len(L.load(self.root)['issues']), 1)
        self.assertEqual(len(L.load(self.root)['issues'][0]['snags']), 2)

    def test_snag_on_a_confirmed_issue_reopens_it_as_a_regression(self):
        issue, _ = self.snag()
        self.land_and_confirm(issue['id'])
        _, snag = self.snag(issue['id'], words='lawn again')
        got = L.load(self.root)['issues'][0]
        self.assertEqual(got['status'], 'open')
        self.assertTrue(snag['regression'])


class FixLandedTest(_Root):
    def setUp(self):
        super().setUp()
        self.issue, _ = self.snag(elements=[GROUND, FEATURE])
        self.merged = '2026-10-26T10:00:00Z'

    def fix(self):
        L.log_fix(self.root, self.issue['id'], summary='fix', merged_at=self.merged,
                  checks={'S-001': 'plaza is brick'})

    def test_fix_without_check_lines_is_refused(self):
        with self.assertRaises(L.IssueError):
            L.log_fix(self.root, self.issue['id'], summary='fix', merged_at=self.merged, checks={})
        with self.assertRaises(L.IssueError):
            L.log_fix(self.root, self.issue['id'], summary='fix', merged_at=self.merged, checks={'S-001': ' '})

    def test_not_rebuilt_stays_open(self):
        self.journal(*build(1, '2026-10-26T09:00:00Z', 'ground', 'g1', [48, -44], 'aaa'))
        self.fix()
        self.assertEqual(L.sync(self.root), [])
        rep = L.rebuild_report(self.root, L.load(self.root)['issues'][0])
        self.assertEqual({st for _, st in rep['elements']}, {'not-rebuilt'})

    def test_rerun_with_identical_content_is_not_a_redesign(self):
        self.journal(*build(1, '2026-10-26T09:00:00Z', 'ground', 'g1', [48, -44], 'aaa'),
                     *build(3, '2026-10-26T11:00:00Z', 'ground', 'g2', [48, -44], 'aaa'))
        self.fix()
        rep = L.rebuild_report(self.root, L.load(self.root)['issues'][0])
        self.assertIn(('ground 48,-44', 'rerun-unchanged'), rep['elements'])

    def test_every_element_must_be_redesigned(self):
        self.journal(*build(1, '2026-10-26T09:00:00Z', 'ground', 'g1', [48, -44], 'aaa'),
                     *build(3, '2026-10-26T11:00:00Z', 'ground', 'g2', [48, -44], 'bbb'))
        self.fix()
        self.assertEqual(L.sync(self.root), [])          # feature not rebuilt yet
        self.journal(*build(1, '2026-10-26T09:00:00Z', 'ground', 'g1', [48, -44], 'aaa'),
                     *build(3, '2026-10-26T11:00:00Z', 'ground', 'g2', [48, -44], 'bbb'),
                     *build(5, '2026-10-26T11:30:00Z', 'feature', 'q001_jubilee', [48, -44], 'ccc'))
        self.assertEqual(L.sync(self.root), [self.issue['id']])
        self.assertEqual(L.load(self.root)['issues'][0]['status'], 'fix-landed')

    def test_mismatched_build_does_not_count(self):
        self.journal(*build(1, '2026-10-26T11:00:00Z', 'ground', 'g2', [48, -44], 'bbb', status='mismatched'))
        self.fix()
        rep = L.rebuild_report(self.root, L.load(self.root)['issues'][0])
        self.assertIn(('ground 48,-44', 'not-rebuilt'), rep['elements'])


class JournalReplayTest(_Root):
    def blobs(self, *lines):
        self.journal(*lines)
        return [b['seq'] for b in L.landed_builds(self.root)]

    def test_landed_undo_voids_its_targets(self):
        self.assertEqual(self.blobs(*build(1, 't1', 'ground', 'g', [48, -44], 'a'),
                                    *undo(3, 't2', [1])), [])

    def test_unlanded_undo_voids_nothing(self):
        self.assertEqual(self.blobs(*build(1, 't1', 'ground', 'g', [48, -44], 'a'),
                                    *undo(3, 't2', [1], status='mismatched')), [1])

    def test_abandoned_build_counts_for_nothing(self):
        self.assertEqual(self.blobs(*build(1, 't1', 'ground', 'g', [48, -44], 'a', status='abandoned')), [])

    def test_revive_restores_the_target(self):
        self.assertEqual(self.blobs(*build(1, 't1', 'ground', 'g', [48, -44], 'a'), *undo(3, 't2', [1]),
                                    {'op': 'revive', 'seq': 5, 'targets': [1], 'undo': 3}), [1])

    def test_undone_redesign_does_not_make_the_issue_fix_landed(self):
        issue, _ = self.snag()
        self.journal(*build(1, '2026-10-26T09:00:00Z', 'ground', 'g1', [48, -44], 'aaa'),
                     *build(3, '2026-10-26T11:00:00Z', 'ground', 'g2', [48, -44], 'bbb'),
                     *undo(5, '2026-10-26T12:00:00Z', [3]))
        L.log_fix(self.root, issue['id'], summary='s', merged_at='2026-10-26T10:00:00Z', checks={'S-001': 'c'})
        self.assertEqual(L.sync(self.root), [])


class ReviewTest(_Root):
    def setUp(self):
        super().setUp()
        self.issue, _ = self.snag()
        self.snag(self.issue['id'], words='grey road')
        self.journal(*build(1, '2026-10-26T09:00:00Z', 'ground', 'g1', [48, -44], 'aaa'),
                     *build(3, '2026-10-26T11:00:00Z', 'ground', 'g2', [48, -44], 'bbb'))
        L.log_fix(self.root, self.issue['id'], summary='load map', merged_at='2026-10-26T10:00:00Z',
                  checks={'S-001': 'plaza brick', 'S-002': 'road tarmac'})
        L.sync(self.root)

    def test_packet_shows_every_snag_with_its_check(self):
        pk = L.review_packet(self.root, self.issue['id'])
        self.assertEqual([s['check'] for s in pk['snags']], ['plaza brick', 'road tarmac'])
        self.assertEqual(pk['snags'][0]['elements'], ['ground 48,-44'])

    def test_all_fixed_confirms(self):
        L.verdict(self.root, self.issue['id'], all_fixed=True)
        self.assertEqual(L.load(self.root)['issues'][0]['status'], 'confirmed')

    def test_any_not_fixed_returns_to_open_and_flags_the_snag(self):
        got = L.verdict(self.root, self.issue['id'], fixed=['S-001'], not_fixed=['S-002'])
        self.assertEqual(got['status'], 'open')
        self.assertEqual([s['resolution'] for s in got['snags']], ['fixed', 'unresolved'])

    def test_partial_verdict_is_refused(self):
        with self.assertRaises(L.IssueError):
            L.verdict(self.root, self.issue['id'], fixed=['S-001'])

    def test_verdict_needs_a_landed_fix(self):
        other, _ = self.snag(title='Roofs', words='no greebles', elements=[FEATURE])
        with self.assertRaises(L.IssueError):
            L.verdict(self.root, other['id'], all_fixed=True)

    def test_sync_does_not_repromote_after_a_verdict_or_a_regression(self):
        L.verdict(self.root, self.issue['id'], fixed=['S-001'], not_fixed=['S-002'])
        self.assertEqual(L.sync(self.root), [])
        self.assertEqual(L.load(self.root)['issues'][0]['status'], 'open')
        L.log_fix(self.root, self.issue['id'], summary='again', merged_at='2026-10-26T10:30:00Z',
                  checks={'S-002': 'road tarmac'})
        self.assertEqual(L.sync(self.root), [self.issue['id']])
        L.verdict(self.root, self.issue['id'], all_fixed=True)
        self.snag(self.issue['id'], words='lawn again')
        self.assertEqual(L.sync(self.root), [])
        self.assertEqual(L.load(self.root)['issues'][0]['status'], 'open')

    def test_ids_are_forgiving(self):
        self.assertEqual(L.find_issue(L.load(self.root), 'I-1')['id'], 'I-001')
        self.assertEqual(L.find_issue(L.load(self.root), '1')['id'], 'I-001')
        with self.assertRaises(L.IssueError):
            L.find_issue(L.load(self.root), 'abc')

    def test_new_fix_resets_the_snags_it_covers(self):
        L.verdict(self.root, self.issue['id'], fixed=['S-001'], not_fixed=['S-002'])
        L.log_fix(self.root, self.issue['id'], summary='again', merged_at='2026-10-27T10:00:00Z',
                  checks={'S-002': 'road tarmac'})
        got = L.load(self.root)['issues'][0]
        self.assertEqual([s['resolution'] for s in got['snags']], ['fixed', None])
        self.assertEqual(got['status'], 'open')

    def test_fix_on_a_confirmed_issue_reopens_it(self):
        L.verdict(self.root, self.issue['id'], all_fixed=True)
        L.log_fix(self.root, self.issue['id'], summary='more', merged_at='2026-10-27T10:00:00Z',
                  checks={'S-001': 'x'})
        self.assertEqual(L.load(self.root)['issues'][0]['status'], 'open')
        self.assertTrue(any('not agreed fixed' in m for m in L.verify(self.root)) is False)


class RestructureTest(_Root):
    def test_move_to_new_issue_splits(self):
        issue, _ = self.snag()
        self.snag(issue['id'], words='roof has no greebles')
        dest = L.move_snag(self.root, 'S-002', to_issue=None, new_title='Roofs lack detail')
        self.assertEqual(dest['id'], 'I-002')
        self.assertEqual(len(L.load(self.root)['issues'][0]['snags']), 1)

    def test_cannot_empty_an_issue_by_moving_its_last_snag(self):
        self.snag()
        with self.assertRaises(L.IssueError):
            L.move_snag(self.root, 'S-001', to_issue=None, new_title='x')

    def test_merge_folds_snags_and_removes_the_absorbed_issue(self):
        a, _ = self.snag()
        b, _ = self.snag(title='Grey roads', words='grey road')
        got = L.merge_issues(self.root, a['id'], b['id'])
        self.assertEqual(len(got['snags']), 2)
        self.assertEqual([i['id'] for i in L.load(self.root)['issues']], [a['id']])
        self.assertEqual(L.verify(self.root), [])


class MoveFromLandedTest(_Root):
    def test_moving_the_only_covered_snag_reopens_the_source(self):
        issue, _ = self.snag()
        self.snag(issue['id'], words='grey road')
        L.log_fix(self.root, issue['id'], summary='s', merged_at='2026-10-26T10:00:00Z', checks={'S-001': 'c'})
        self.journal(*build(1, '2026-10-26T09:00:00Z', 'ground', 'g1', [48, -44], 'aaa'),
                     *build(3, '2026-10-26T11:00:00Z', 'ground', 'g2', [48, -44], 'bbb'))
        # S-002 is uncovered by the fix, so it cannot reach fix-landed yet
        self.assertEqual(L.sync(self.root), [])
        L.move_snag(self.root, 'S-001', to_issue=None, new_title='Plaza')
        src = L.load(self.root)['issues'][0]
        self.assertEqual((src['status'], src['fixes']), ('open', []))
        with self.assertRaises(L.IssueError):
            L.verdict(self.root, src['id'], all_fixed=True)


class MatchTest(_Root):
    def test_same_element_and_wording_ranks_first_and_closed_issues_are_included(self):
        a, _ = self.snag(title='Ground ignores plazas', words='brick plaza shown as grass')
        self.snag(title='Roofs lack detail', words='no greebles on roofs', elements=[FEATURE])
        self.land_and_confirm(a['id'])
        got = L.match(self.root, words='plaza is grass not brick', elements=[GROUND])
        self.assertEqual(got[0]['title'], 'Ground ignores plazas')
        self.assertTrue(got[0]['confirmed'])
        self.assertTrue(any('same element' in w for w in got[0]['why']))
        self.assertEqual(got[0]['snags'][0]['image'], 'issues/img/S-001.png')

    def test_no_overlap_gives_no_candidates(self):
        self.snag(words='brick plaza shown as grass')
        self.assertEqual(L.match(self.root, words='window frames', elements=[L.parse_element('trees:1,1')]), [])


class DigestVerifyTest(_Root):
    def test_digest_counts_and_verify_catches_a_missing_picture(self):
        self.snag()
        self.assertEqual(L.digest(self.root), 'issues: 1 open, 0 fixes ready for your review')
        (self.root / 'registry' / 'issues' / 'img' / 'S-001.png').unlink()
        self.assertTrue(any('picture missing' in m for m in L.verify(self.root)))

    def test_parse_element_rejects_junk(self):
        for bad in ('wall:1,2', 'ground:x', 'feature:', 'trees'):
            with self.assertRaises(L.IssueError):
                L.parse_element(bad)


if __name__ == '__main__':
    unittest.main()
