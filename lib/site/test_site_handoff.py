#!/usr/bin/env python3
"""Unit tests for scripts/site_handoff.py: definition of done and cell planning (I-046, I-049, I-057).

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
        (self.site / 'registry').mkdir()
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
        from journal_testkit import JournalWorld
        self.jw = JournalWorld(self.site)
        self.jw.__enter__()
        self.addCleanup(self.jw.__exit__)
        self.addCleanup(self.tmp.cleanup)

    def registry_layers(self, cell, *, ground='generated', trees=True, decor=True):
        """Seed journal layers for (tx, tz) (ground other than generated: ghost ground)."""
        real = ground == 'generated'
        self.jw.seed(cell, ground=real, ghost_ground=not real, trees=trees and real, decor=decor and real)


class DoneTest(Base):
    def test_done_needs_layers_current_trees_and_no_missing_features(self):
        """Registry DoD: layers + trees mask; residual feature builds still block done."""
        self.registry_layers((1, 1))
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
        """An aborted apply leaves a 'begun' entry: never done, listed as 'in-flight: <plan>' by next --plan."""
        from cell_journal import Build
        self.registry_layers((1, 1), decor=False)
        self.jw.reg.begin(Build('highfield_1_1_decor', 'decor', {(140, 61, 140): 'minecraft:poppy'}, cell=(1, 1)))
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
        self.registry_layers((1, 1), trees=False, decor=False)
        with mock.patch.object(H, 'residual_features', side_effect=AssertionError('no scan')):
            self.assertFalse(H.layers_done((1, 1)))
            self.assertEqual(H.cell_gaps((1, 1), features=False)['layers'], ['trees', 'decor'])

    def test_next_cell_from_registry_not_queue(self):
        """Ticket #31: next_cell uses registry_next, not retired queue files."""
        self.registry_layers((48, -44))
        self.registry_layers((48, -45), trees=False, decor=False)
        with mock.patch('pass_mask.trees_rule_current', return_value=True), \
                mock.patch.object(H, 'residual_features', return_value=[]), \
                mock.patch.object(H, 'trees_over_roof_open', return_value=[]), \
                mock.patch('cell_features.clip_coverage', return_value=1.0), \
                mock.patch('cellmath.home_cell', return_value=(48, -44)):
            nxt = H.next_cell(max_ring=1)
        self.assertEqual(nxt[:2], (48, -45))

    def test_advance_returns_registry_next_without_queue_write(self):
        """Ticket #31: advance_to_next_cell does not append QUEUE items."""
        before = H.QUEUE.read_text()
        with mock.patch('registry_next.next_task', return_value={
            'kind': 'layer', 'cell': '48,-44', 'layer': 'ground',
        }):
            first = H.advance_to_next_cell()
        self.assertEqual(first['id'], 'NEXT')
        self.assertIn('ground', first['text'])
        self.assertEqual(H.QUEUE.read_text(), before)

    def test_residual_features_is_missing_builds_only(self):
        """Seam: residual_features is only OSM missing feature builds."""
        mapped = [{'kind': 'houses', 'label': 'A', 'street': 'A', 'refs': ['w1'], 'bbox': (0, 0, 1, 1)}]
        with mock.patch('cell_features.missing_features', return_value=mapped) as miss, \
                mock.patch('osm_footprints.OSMIndex', return_value=mock.Mock(streets={})):
            out = H.residual_features((48, -44))
        self.assertEqual(out, mapped)
        miss.assert_called_once()


class GoogleReviewTest(Base):
    def test_aerial_review_retired(self):
        """Ticket #31: AERIAL-REVIEW is not a progress source."""
        self.assertIsNone(H.next_google_review())
        self.assertFalse(H.set_google_review_status((2, 2), 'finished'))
        self.assertFalse(H.set_google_review_status((2, 2), 'done'))


class LidarGapsTest(Base):
    LUMP = {'bbox': [10, 10, 20, 20], 'centre': [15, 15], 'storeys': 2, 'roof': 'pitched', 'ridge_axis': 'x', 'trees_over': 12}

    def test_trees_over_roof_open_until_resolved(self):
        self.assertEqual(H.trees_over_roof_open((1, 1), lumps=[self.LUMP]), [self.LUMP])
        from cell_journal import Note
        self.jw.reg.change(Note((1, 1), 'review', 'crown kept', 'trees-over-roof:5,5,30,30'))
        self.assertEqual(H.resolved_trees_over_roof((1, 1)), [{'bbox': [5, 5, 30, 30], 'reason': 'crown kept'}])
        self.assertEqual(H.trees_over_roof_open((1, 1), lumps=[self.LUMP]), [])
        self.assertEqual(H.trees_over_roof_open((1, 1), lumps=[dict(self.LUMP, trees_over=0)]), [])

    def test_open_tree_over_roof_keeps_a_cell_not_done(self):
        self.registry_layers((1, 1))
        with mock.patch('pass_mask.trees_rule_current', return_value=True), \
                mock.patch.object(H, 'residual_features', return_value=[]), \
                mock.patch.object(H, 'lidar_lumps', return_value=[self.LUMP]):
            self.assertFalse(H.layers_done((1, 1)))
            self.assertEqual(H.cell_gaps((1, 1))['trees_over_roof'], [self.LUMP])

    def test_plan_cell_items_shows_lidar_storeys_roof_ridge(self):
        feat = {'kind': 'building', 'label': 'Hartley Avenue', 'street': 'x', 'refs': ['w1'], 'bbox': (0, 0, 40, 40)}
        gaps = {'layers': [], 'trees_stale': False, 'features': [feat], 'trees_over_roof': []}
        items = H.plan_cell_items(1, 1, 0, gaps, [self.LUMP])
        blob = ' '.join(line for _h, body in items for line in ([_h] + body))
        self.assertIn('2 storeys (estimate), roof pitched, ridge axis x', blob)

    def test_plan_cell_items_includes_trees_over_roof(self):
        gaps = {'layers': [], 'trees_stale': False, 'features': [], 'trees_over_roof': [self.LUMP]}
        items = H.plan_cell_items(1, 1, 0, gaps, [])
        heads = [h for h, _ in items]
        self.assertTrue(any('TREES OVER ROOF' in h for h in heads))


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

    def test_first_feature_item_describes_v2_ghost_frontier(self):
        body = '\n'.join(H.plan_cell_items(48, -44, 1, self.gaps(features=[self.FEATURE]), [])[0][1])
        self.assertIn('lidar ghost frontier', body)
        self.assertIn('LIDAR_GHOST_RING=2', body)
        self.assertIn('Ghost ground only', body)
        self.assertIn('black_concrete', body)
        self.assertNotIn('neighbour lidar ghosts come with raise', body)

    def test_layered_passes_describes_ghost_ground(self):
        body = '\n'.join(H.plan_cell_items(48, -44, 1, self.gaps(layers=True), [])[0][1])
        self.assertIn('supersedes ghost ground', body)
        self.assertIn('any Element started', body)
        self.assertIn('Ghost ground only', body)
        self.assertNotIn('neighbour lidar ghosts come with raise', body)


class BriefLidarGhost(Base):
    def test_brief_lists_lidar_ghost_status_for_cell_item(self):
        item = {
            'id': 'Q-099',
            'text': 'CELL 48,-44 (r=1) FEATURE Demo. RULES: property.',
            'lines': ['CELL 48,-44 (r=1) FEATURE Demo. RULES: property.'],
        }
        with mock.patch('cross_zone.cross_zone_report', return_value={}), \
                mock.patch('cross_zone.format_report', return_value='(none)'), \
                mock.patch.object(H, '_kit_api', return_value=['  (mock)']), \
                mock.patch.object(H, '_coverage_cheatsheet_lines', return_value=['  (mock)']):
            brief = H.brief_text(item)
        self.assertIn('LIDAR GHOSTS', brief)
        self.assertIn('shims/lidar_ghost.py status --cell 48,-44', brief)
        self.assertIn('Ghost ground only', brief)


class PortalFactsFacadePlan(unittest.TestCase):
    """#145: portal brief feeds facade_plan, not door_specs."""

    def test_portal_facts_asks_for_facade_plan_not_door_specs(self):
        lines = H.portal_facts([
            {'id': '57-A', 'label': 'Side', 'x': 10, 'z': 20, 'images': ['http://example/57.jpg']},
            {'id': '40-A', 'label': 'Main', 'x': 11, 'z': 21, 'images': []},
        ])
        text = '\n'.join(lines)
        self.assertIn('57-A', text)
        self.assertIn('10,20', text)
        self.assertIn('facade_plan', text)
        self.assertIn('unresolved', text.lower())
        self.assertNotIn('door_specs', text)
        self.assertNotIn('Put a door at each one', text)

    def test_portal_facts_empty_when_no_portals(self):
        self.assertEqual(H.portal_facts([]), [])
        self.assertEqual(H.portal_facts(None), [])


class BuildingMapEvidenceFacts(unittest.TestCase):
    """#159: feature brief carries map roof/height hints and snapped entrances."""

    def _idx(self):
        rings = [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]]
        props = {
            'building': 'university',
            'osm_way_id': '42',
            'other_tags': '"roof:shape"=>"hipped","height"=>"12","building:levels"=>"3"',
        }
        idx = mock.Mock()
        idx.buildings = [(rings, props)]
        idx.entrances = [
            (5.0, -1.5, {'entrance': 'main', 'osm_id': '7'}),
            (11.0, 5.0, {'entrance': 'service', 'osm_id': '8'}),
        ]
        return idx

    def test_map_building_tag_facts_lists_hints(self):
        text = '\n'.join(H.map_building_tag_facts(['w42'], idx=self._idx()))
        self.assertIn('hint', text.lower())
        self.assertIn('roof:shape=hipped', text)
        self.assertIn('height=12', text)
        self.assertIn('never override', text.lower())

    def test_mapped_entrance_facts_snaps_and_ranks(self):
        text = '\n'.join(H.mapped_entrance_facts(['w42'], idx=self._idx()))
        self.assertIn('main', text)
        self.assertIn('service', text)
        self.assertLess(text.index('main'), text.index('service'))
        self.assertIn('at 5,0', text)


if __name__ == '__main__':
    unittest.main()
