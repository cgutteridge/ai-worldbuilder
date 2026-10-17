#!/usr/bin/env python3
"""Unit tests for scripts/site_handoff.py: definition of done and atomic `end` (I-046, I-049, I-057).

Temp SITE dir only; the real site/ is never touched, no Minecraft, no OSM.

  python3 -m unittest scripts/test_site_handoff.py
"""
import contextlib
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'shims'))
import site_handoff as H  # noqa: E402

QUEUE_TEXT = '''SOUTHAMPTON REFINE — QUEUE

[ ] Q-001  CELL 1,1 (r=0) layered passes: ground, trees, decor. RULES: ground.
           Auto-queued.

[ ] Q-002  CELL 2,2 (r=1) layered passes: ground, trees, decor. RULES: ground.
           Auto-queued.
'''
HANDOFF_TEXT = '''head
------------------------------------------------------------------------
Q-001 STARTER (copy into a fresh session)
------------------------------------------------------------------------
old
------------------------------------------------------------------------
GENERIC BOOT PROMPT
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name)
        (self.site / 'cells').mkdir()
        (self.site / 'QUEUE.txt').write_text(QUEUE_TEXT)
        (self.site / 'HANDOFF.txt').write_text(HANDOFF_TEXT)
        patches = [mock.patch.object(H, n, v) for n, v in dict(
            SITE=self.site, QUEUE=self.site / 'QUEUE.txt', HANDOFF=self.site / 'HANDOFF.txt',
            PENDING=self.site / '.handoff-pending.json', AERIAL_REVIEW=self.site / 'AERIAL-REVIEW.txt',
            GOOGLE_REVIEW=self.site / 'AERIAL-REVIEW.txt', LOCK=self.site / 'LOCK.json',
            QUEUE_DONE=self.site / 'archive' / 'QUEUE-done.txt').items()]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def stages(self, cell, **st):
        (self.site / 'cells' / f'{cell[0]}_{cell[1]}-STAGES.json').write_text(json.dumps({'stages': st}))


APPLIED = {k: {'status': 'applied'} for k in ('ground', 'trees', 'decor')}


class DoneTest(Base):
    def test_done_needs_layers_current_trees_and_no_missing_features(self):
        """Empty-world DoD: layers + trees mask; missing OSM feature builds still block done."""
        self.stages((1, 1), **APPLIED)
        with mock.patch('pass_mask.trees_rule_current', return_value=True), \
                mock.patch.object(H, 'residual_features', return_value=[]), \
                mock.patch.object(H, 'trees_over_roof_open', return_value=[]):
            self.assertTrue(H.layers_done((1, 1)))
        with mock.patch('pass_mask.trees_rule_current', return_value=True), \
                mock.patch.object(H, 'residual_features', return_value=[{'kind': 'building'}]), \
                mock.patch.object(H, 'trees_over_roof_open', return_value=[]):
            self.assertFalse(H.layers_done((1, 1)))
        with mock.patch('pass_mask.trees_rule_current', return_value=False), \
                mock.patch.object(H, 'residual_features', return_value=[]):
            self.assertFalse(H.layers_done((1, 1)))
            self.assertTrue(H.cell_gaps((1, 1))['trees_stale'])

    def test_applying_stage_is_not_done_and_reported_first(self):
        """An aborted apply leaves 'applying': never done, listed as 'in-flight: <plan>' by next --plan."""
        st = dict(APPLIED)
        st['decor'] = {'status': 'applying', 'plan': 'highfield_1_1_decor'}
        self.stages((1, 1), **st)
        with mock.patch('pass_mask.trees_rule_current', return_value=True), \
                mock.patch.object(H, 'residual_features', return_value=[]):
            g = H.cell_gaps((1, 1))
            self.assertEqual(g['in_flight'], ['in-flight: highfield_1_1_decor'])
            self.assertEqual(g['layers'], ['decor'])
            self.assertFalse(H.layers_done((1, 1)))
        with mock.patch('pass_mask.trees_rule_current', return_value=True), \
                mock.patch.object(H, 'residual_features', return_value=[]), \
                mock.patch.object(H, 'ring_cells', return_value=[(1, 1)]), \
                mock.patch('sys.stdout', new=io.StringIO()) as out:
            H.cmd_plan([])
        self.assertIn('in-flight: highfield_1_1_decor', out.getvalue())

    def test_missing_layer_short_circuits_scan(self):
        self.stages((1, 1), ground={'status': 'verified'})
        with mock.patch.object(H, 'residual_features', side_effect=AssertionError('no scan')):
            self.assertFalse(H.layers_done((1, 1)))
            self.assertEqual(H.cell_gaps((1, 1), features=False)['layers'], ['trees', 'decor'])

    def test_next_cell_requeues_finished_cell_with_missing_features(self):
        cells = {(48, -44): [], (48, -45): [{'kind': 'building', 'label': 'x', 'refs': ['w1'], 'bbox': (0, 0, 9, 9)}]}
        for c in cells:
            self.stages(c, **APPLIED)
        with mock.patch('pass_mask.trees_rule_current', return_value=True), \
                mock.patch.object(H, 'residual_features', side_effect=lambda c, idx=None: cells.get(c, [])), \
                mock.patch.object(H, 'trees_over_roof_open', return_value=[]):
            self.assertEqual(H.next_cell(max_ring=1), (48, -45, 1))
            plan = H.requeue_plan(max_ring=1)
        self.assertEqual([(t, z) for t, z, *_ in plan], [(48, -45)])

    def test_queue_items_for_missing_features_and_stale_trees(self):
        feat = {'kind': 'building', 'label': 'Hartley Avenue (2 buildings)',
                'street': 'Hartley Avenue', 'refs': ['w1'], 'bbox': (6400, -5500, 6430, -5480)}
        gaps = {'layers': [], 'trees_stale': True, 'features': [feat]}
        with mock.patch.object(H, 'next_cell', return_value=(48, -44, 0)), \
                mock.patch.object(H, 'cell_gaps', return_value=gaps), \
                mock.patch.object(H, 'lidar_lumps', return_value=[]):
            first = H.advance_to_next_cell()
        self.assertIn('FEATURE Hartley Avenue', first['text'])
        ids = [(i['id'], 'TREES re-run' in i['text']) for i in H._queue_items() if i['id'] > 'Q-002']
        self.assertEqual([t for _i, t in ids], [False, True])

    def test_residual_features_is_missing_builds_only_not_lump_scan(self):
        """Seam: residual_features never calls lump_scan; only OSM missing feature builds."""
        mapped = [{'kind': 'houses', 'label': 'A', 'street': 'A', 'refs': ['w1'], 'bbox': (0, 0, 1, 1)}]
        with mock.patch('cell_features.missing_features', return_value=mapped) as miss, \
                mock.patch('lump_scan.scan_cell', side_effect=AssertionError('lump_scan must not drive residual')) as scan, \
                mock.patch('lump_scan.lump_features', side_effect=AssertionError('lump_features unused')), \
                mock.patch('osm_footprints.OSMIndex', return_value=mock.Mock(streets={})):
            out = H.residual_features((48, -44))
        self.assertEqual(out, mapped)
        miss.assert_called_once()
        scan.assert_not_called()


REVIEW_TEXT = '''head
 ring  cell      bld  status
  r0   1,1        4  done
  r1   2,2        2  features
  r1   3,3        8  todo
'''


class GoogleReviewTest(Base):
    def setUp(self):
        super().setUp()
        (self.site / 'AERIAL-REVIEW.txt').write_text(REVIEW_TEXT)

    def test_first_row_not_done_is_next(self):
        self.assertEqual(H.next_google_review()['cell'], (2, 2))
        H.set_google_review_status((2, 2), 'done')
        self.assertEqual(H.next_google_review()['cell'], (3, 3))
        self.assertIn('  r1   3,3        8  todo', (self.site / 'AERIAL-REVIEW.txt').read_text())

    def test_bad_status_word_is_refused(self):
        with self.assertRaises(ValueError):
            H.set_google_review_status((2, 2), 'finished')

    def test_review_item_is_queued_alone_while_a_row_is_open(self):
        gaps = {'layers': ['ground'], 'trees_stale': False, 'features': [], 'trees_over_roof': []}
        with mock.patch.object(H, 'next_cell', return_value=(4, 4, 2)), \
                mock.patch.object(H, 'cell_gaps', return_value=gaps):
            first = H.advance_to_next_cell()
            H.advance_to_next_cell()            # the review item is open: not queued twice
        self.assertIn('AERIAL REVIEW cell 2,2', first['text'])
        self.assertEqual(H.google_review_cell(first), (2, 2))
        texts = [i['text'] for i in H._queue_items() if i['id'] > 'Q-002']
        self.assertTrue(texts[0].startswith('AERIAL REVIEW'))
        self.assertEqual(sum('AERIAL REVIEW' in t for t in texts), 1)
        self.assertEqual(len(texts), 1)         # no layered-passes / feature / TREES re-run item for the same work

    def test_end_of_review_chat_sets_the_row(self):
        gaps = {'layers': [], 'trees_stale': False, 'features': [], 'trees_over_roof': []}
        with mock.patch.object(H, 'next_cell', return_value=None), \
                mock.patch.object(H, 'cell_gaps', return_value=gaps):
            first = H.advance_to_next_cell()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                mock.patch('site_archive.run'):
            H.main(['end', first['id'], '--summary', 's', '--skip-say', '--skip-view', '--review-status', 'look'])
            self.assertEqual(H.next_google_review()['status'], 'look')
            self.assertEqual(H.cmd_end([first['id'], '--summary', 's', '--skip-say', '--skip-view']), 0)
        self.assertEqual(H.next_google_review()['cell'], (3, 3))


class LidarGapsTest(Base):
    LUMP = {'bbox': [10, 10, 20, 20], 'centre': [15, 15], 'storeys': 2, 'roof': 'pitched', 'ridge_axis': 'x', 'trees_over': 12}

    def test_trees_over_roof_open_until_resolved(self):
        self.assertEqual(H.trees_over_roof_open((1, 1), lumps=[self.LUMP]), [self.LUMP])
        self.stages((1, 1), lidar={'trees_over_roof_resolved': [{'bbox': [5, 5, 30, 30], 'reason': 'crown kept'}]})
        self.assertEqual(H.trees_over_roof_open((1, 1), lumps=[self.LUMP]), [])
        self.assertEqual(H.trees_over_roof_open((1, 1), lumps=[dict(self.LUMP, trees_over=0)]), [])

    def test_open_tree_over_roof_keeps_a_cell_not_done(self):
        self.stages((1, 1), **APPLIED)
        with mock.patch('pass_mask.trees_rule_current', return_value=True), \
                mock.patch.object(H, 'residual_features', return_value=[]), \
                mock.patch.object(H, 'lidar_lumps', return_value=[self.LUMP]):
            self.assertFalse(H.layers_done((1, 1)))
            self.assertEqual(H.cell_gaps((1, 1))['trees_over_roof'], [self.LUMP])

    def test_feature_item_shows_lidar_storeys_roof_ridge(self):
        feat = {'kind': 'building', 'label': 'Hartley Avenue', 'street': 'x', 'refs': ['w1'], 'bbox': (0, 0, 40, 40)}
        gaps = {'layers': [], 'trees_stale': False, 'features': [feat], 'trees_over_roof': []}
        with mock.patch.object(H, 'next_cell', return_value=(1, 1, 0)), \
                mock.patch.object(H, 'cell_gaps', return_value=gaps), \
                mock.patch.object(H, 'lidar_lumps', return_value=[self.LUMP]):
            first = H.advance_to_next_cell()
        blob = ' '.join(first['lines'])
        self.assertIn('2 storeys (estimate), roof pitched, ridge axis x', blob)

    def test_open_tree_over_roof_is_queued(self):
        gaps = {'layers': [], 'trees_stale': False, 'features': [], 'trees_over_roof': [self.LUMP]}
        with mock.patch.object(H, 'next_cell', return_value=(1, 1, 0)), \
                mock.patch.object(H, 'cell_gaps', return_value=gaps):
            first = H.advance_to_next_cell()
        self.assertIn('TREES OVER ROOF 1', first['text'])


class EndTest(Base):
    def run_end(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), \
                mock.patch('site_archive.run'):
            rc = H.main(['end', *args, '--summary', 's', '--skip-say', '--skip-view'])
        return rc, out.getvalue()

    def test_end_ticks_releases_and_arms_next(self):
        (self.site / 'LOCK.json').write_text(json.dumps({'owner': 'me', 'task': 'Q-001 …', 'heartbeat': time.time()}))
        H.set_pending('Q-000', {'id': 'Q-001', 'text': 'stale'})
        rc, _ = self.run_end('Q-001')
        self.assertEqual(rc, 0)
        self.assertFalse((self.site / 'LOCK.json').exists())
        self.assertIn('[x] Q-001', H.QUEUE.read_text())
        self.assertIn('[ ] Q-002', H.QUEUE.read_text())
        pend = H.read_pending()
        self.assertEqual((pend['done'], pend['next']), ('Q-001', 'Q-002'))

    def test_end_without_id_uses_lock_task(self):
        (self.site / 'LOCK.json').write_text(json.dumps({'owner': 'me', 'task': 'Q-002 stuff', 'heartbeat': time.time()}))
        self.run_end()
        self.assertIn('[x] Q-002', H.QUEUE.read_text())
        self.assertEqual(H.read_pending()['next'], 'Q-001')

    def test_state_changes_survive_a_later_crash(self):
        (self.site / 'LOCK.json').write_text(json.dumps({'owner': 'me', 'task': 'Q-001', 'heartbeat': time.time()}))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                mock.patch('site_archive.run', side_effect=IndexError('boom')):
            rc = H.main(['end', 'Q-001', '--summary', 's', '--skip-say', '--skip-view'])
        self.assertEqual(rc, 0)
        self.assertFalse((self.site / 'LOCK.json').exists())
        self.assertIn('[x] Q-001', H.QUEUE.read_text())
        self.assertEqual(H.read_pending()['next'], 'Q-002')

    def test_last_item_leaves_no_next(self):
        H.QUEUE.write_text(QUEUE_TEXT.replace('[ ] Q-002', '[x] Q-002'))
        self.run_end('Q-001')
        self.assertIsNone(H.read_pending()['next'])

    FEATURE_QUEUE = '''SOUTHAMPTON REFINE — QUEUE

[ ] Q-001  CELL 1,1 (r=0) FEATURE Test Building. RULES: property.
           Auto-queued.

[ ] Q-002  CELL 2,2 (r=1) layered passes: ground, trees, decor. RULES: ground.
           Auto-queued.
'''

    def test_end_blocks_ungreebled_feature(self):
        """FEATURE end without <name>-greeblies or escape phrase must not tick."""
        H.QUEUE.write_text(self.FEATURE_QUEUE)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err), \
                mock.patch('site_archive.run'):
            rc = H.main(['end', 'Q-001', '--summary', 'built the shell only',
                         '--skip-say', '--skip-view'])
        self.assertEqual(rc, 2)
        self.assertIn('greeblies', err.getvalue().lower())
        self.assertIn('[ ] Q-001', H.QUEUE.read_text())

    def _install_contact_sheet(self, build='test_bldg'):
        """Fake evidence sheet so greeblies-gate tests can clear the #17 gate too."""
        from PIL import Image
        vroot = Path(tempfile.mkdtemp())
        out = vroot / 'verification' / 'site'
        out.mkdir(parents=True)
        sheet = out / f'{build}-contact.png'
        Image.new('RGB', (40, 40), (10, 20, 30)).save(sheet)
        (out / f'{build}-result.json').write_text(json.dumps({
            'contact_sheet': f'verification/site/{build}-contact.png'}))
        return vroot, out, build

    def test_end_allows_feature_when_summary_says_no_greeblies_seen(self):
        H.QUEUE.write_text(self.FEATURE_QUEUE)
        vroot, out, build = self._install_contact_sheet()
        out_s = io.StringIO()
        with mock.patch('evidence_sheet.ROOT', vroot), \
                mock.patch('evidence_sheet.OUT', out), \
                mock.patch.object(H, 'ROOT', vroot), \
                contextlib.redirect_stdout(out_s), contextlib.redirect_stderr(io.StringIO()), \
                mock.patch('site_archive.run'):
            rc = H.main(['end', 'Q-001', '--summary', 'shell done; no greeblies seen',
                         '--skip-say', '--skip-view', '--build', build])
        self.assertEqual(rc, 0)
        self.assertIn('[x] Q-001', H.QUEUE.read_text())

    def test_end_allows_feature_with_greeblies_stage(self):
        H.QUEUE.write_text(self.FEATURE_QUEUE)
        self.stages((1, 1), **{'feature-test-greeblies': {
            'status': 'applied', 'plan': 'test-greeblies'}})
        vroot, out, build = self._install_contact_sheet()
        with mock.patch('evidence_sheet.ROOT', vroot), \
                mock.patch('evidence_sheet.OUT', out), \
                mock.patch.object(H, 'ROOT', vroot), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()), \
                mock.patch('site_archive.run'):
            rc = H.main(['end', 'Q-001', '--summary', 's', '--skip-say', '--skip-view',
                         '--build', build])
        self.assertEqual(rc, 0)
        self.assertIn('[x] Q-001', H.QUEUE.read_text())

    def test_feature_end_refuses_without_contact_sheet(self):
        """FEATURE handoff must not tick when the evidence contact sheet is missing (#17)."""
        from PIL import Image

        feat = (
            'SOUTHAMPTON REFINE — QUEUE\n\n'
            '[ ] Q-010  CELL 1,1 (r=0) FEATURE Demo Hall. RULES: property.\n'
            '           Non-house building: use scripts/feature_build.py — write site/builds/demo_hall.json\n\n'
            '[ ] Q-011  CELL 1,1 (r=0) layered passes: ground, trees, decor. RULES: ground.\n'
        )
        H.QUEUE.write_text(feat)
        (self.site / 'LOCK.json').write_text(
            json.dumps({'owner': 'me', 'task': 'Q-010 FEATURE', 'heartbeat': time.time()}))
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err), \
                mock.patch('site_archive.run'):
            # Clear greeblies gate so the refusal is specifically the contact sheet.
            rc = H.main(['end', 'Q-010', '--summary', 's; no greeblies seen',
                         '--skip-say', '--skip-view', '--build', 'demo_hall'])
        self.assertEqual(rc, 2)
        self.assertIn('contact sheet', err.getvalue().lower())
        self.assertIn('[ ] Q-010', H.QUEUE.read_text())
        self.assertTrue((self.site / 'LOCK.json').exists())

        # With a recorded sheet, end succeeds and names the path.
        vroot = Path(tempfile.mkdtemp())
        out = vroot / 'verification' / 'site'
        out.mkdir(parents=True)
        sheet = out / 'demo_hall-contact.png'
        Image.new('RGB', (40, 40), (10, 20, 30)).save(sheet)
        (out / 'demo_hall-result.json').write_text(json.dumps({
            'contact_sheet': 'verification/site/demo_hall-contact.png'}))
        with mock.patch('evidence_sheet.ROOT', vroot), \
                mock.patch('evidence_sheet.OUT', out), \
                mock.patch.object(H, 'ROOT', vroot):
            out_s = io.StringIO()
            with contextlib.redirect_stdout(out_s), contextlib.redirect_stderr(io.StringIO()), \
                    mock.patch('site_archive.run'):
                rc = H.main(['end', 'Q-010', '--summary', 'built demo; no greeblies seen',
                             '--skip-say', '--skip-view', '--build', 'demo_hall'])
            self.assertEqual(rc, 0)
            self.assertIn('CONTACT SHEET:', out_s.getvalue())
            self.assertIn('demo_hall-contact.png', out_s.getvalue())
        self.assertIn('[x] Q-010', H.QUEUE.read_text())


class PlanCellItems(unittest.TestCase):
    FEATURE = {'bbox': [1, 2, 3, 4], 'label': 'L', 'kind': 'building', 'refs': ['w1'], 'portals': [], 'pictures': []}

    def gaps(self, **kw):
        base = {'in_flight': None, 'features': [], 'trees_over_roof': [], 'layers': False, 'trees_stale': False}
        return {**base, **kw}

    def heads(self, gaps):
        return [h.split(' ', 3)[2] + ' ' + h.split(' ', 3)[3][:12] for h, _b in H.plan_cell_items(1, 2, 0, gaps, [])]

    def test_nothing_missing_plans_nothing(self):
        self.assertEqual(H.plan_cell_items(1, 2, 0, self.gaps(), []), [])

    def test_order_is_recover_features_trees_layers(self):
        items = H.plan_cell_items(1, 2, 0, self.gaps(in_flight=['x'], features=[self.FEATURE], layers=True,
                                                     trees_over_roof=[{'centre': (0, 0), 'bbox': (0, 0, 1, 1), 'roof': 'flat'}]), [])
        kinds = [h.split(') ', 1)[1].split(' ')[0] for h, _b in items]
        self.assertEqual(kinds, ['RECOVER', 'FEATURE', 'TREES', 'layered'])

    def test_first_item_of_new_cell_carries_marker_line_only_when_first(self):
        feats = H.plan_cell_items(1, 2, 0, self.gaps(features=[self.FEATURE, self.FEATURE]), [])
        marker = 'First item of a new cell'
        self.assertTrue(any(marker in line for line in feats[0][1]))
        self.assertFalse(any(marker in line for line in feats[1][1]))
        recovered = H.plan_cell_items(1, 2, 0, self.gaps(in_flight=['x'], features=[self.FEATURE]), [])
        self.assertFalse(any(marker in line for line in recovered[1][1]))

    def test_stale_trees_item_only_when_layers_done(self):
        self.assertEqual(len(H.plan_cell_items(1, 2, 0, self.gaps(trees_stale=True), [])), 1)
        both = H.plan_cell_items(1, 2, 0, self.gaps(trees_stale=True, layers=True), [])
        self.assertEqual(len(both), 1)
        self.assertIn('layered passes', both[0][0])

    def test_feature_points_at_house_build_spec_not_peer_copy(self):
        house = {**self.FEATURE, 'kind': 'houses', 'label': 'Some Street (3 houses)'}
        body = H.plan_cell_items(1, 2, 0, self.gaps(features=[house]), [])[0][1]
        joined = '\n'.join(body)
        self.assertIn('house_build.py', joined)
        self.assertIn('site/builds/', joined)
        self.assertNotIn('feature_build.py', joined)
        self.assertNotIn('copy the nearest', joined)
        self.assertNotIn('build_runner', joined)
        self.assertNotIn('house_geometry', joined)

    def test_non_house_feature_points_at_feature_build(self):
        body = H.plan_cell_items(1, 2, 0, self.gaps(features=[self.FEATURE]), [])[0][1]
        joined = '\n'.join(body)
        self.assertIn('feature_build.py', joined)
        self.assertIn('site/builds/', joined)
        self.assertNotIn('house_build.py', joined)
        self.assertNotIn('build_runner', joined)

    def test_feature_template_includes_greeblies_line(self):
        """Queue FEATURE body must name greeblies list + <name>-greeblies / escape."""
        body = '\n'.join(H.plan_cell_items(1, 2, 0, self.gaps(features=[self.FEATURE]), [])[0][1])
        self.assertIn('Greeblies:', body)
        self.assertIn('plant, vents, skylights, panels, plant colour', body)
        self.assertIn('<name>-greeblies', body)
        self.assertIn('no greeblies seen', body)
    def test_feature_template_names_evidence_sheet_command(self):
        body = '\n'.join(H.plan_cell_items(1, 2, 0, self.gaps(features=[self.FEATURE]), [])[0][1])
        self.assertIn('evidence_sheet.py', body)
        self.assertIn('contact sheet', body.lower())

    def test_layered_passes_done_line_is_empty_world_not_geocraft_lumps(self):
        """Seam: queue copy defines done as evidence + layers + masks, not clearing Geocraft."""
        body = '\n'.join(H.plan_cell_items(1, 2, 0, self.gaps(layers=True), [])[0][1])
        self.assertNotIn('unrefined Geocraft', body)
        self.assertNotIn('lump_scan', body)
        self.assertNotIn('lump_sweep', body)
        self.assertIn('evidence', body.lower())

    def test_feature_item_does_not_instruct_lump_scan_exceptions(self):
        """Seam: even a legacy kind=lumps payload must not revive lump_scan queue driver copy."""
        lump = {**self.FEATURE, 'kind': 'lumps', 'label': 'X (unrefined)'}
        body = '\n'.join(H.plan_cell_items(1, 2, 0, self.gaps(features=[lump]), [])[0][1])
        self.assertNotIn('lump_scan', body)
        self.assertNotIn('lump_sweep.exceptions', body)
        self.assertNotIn('Original Geocraft still standing', body)


class AppendQueueItems(unittest.TestCase):
    def test_numbers_after_highest_and_fills_qid(self):
        with tempfile.TemporaryDirectory() as d:
            q = Path(d) / 'QUEUE.txt'
            q.write_text('[x] Q-007  old\n')
            with mock.patch.object(H, 'QUEUE', q), mock.patch.object(H, 'QUEUE_DONE', Path(d) / 'none.txt'):
                first = H._append_queue_items([('one', ['end {qid}']), ('two', ['b'])])
            text = q.read_text()
        self.assertEqual(first, 'Q-008')
        self.assertIn('[ ] Q-008  one', text)
        self.assertIn('end Q-008', text)
        self.assertIn('[ ] Q-009  two', text)

    def test_empty_writes_nothing(self):
        self.assertIsNone(H._append_queue_items([]))


if __name__ == '__main__':
    unittest.main()
