"""Tests for landmark street-level image gate (#335)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'shims'))

import script_env  # noqa: E402
script_env.ensure_import_paths()

import landmark_gate as LG  # noqa: E402
import feature_build as F  # noqa: E402


class LandmarkClassTest(unittest.TestCase):
    """Tag set for church / cinema / shopping centre — not every retail shop."""

    def test_building_classes(self):
        for value in ('church', 'cathedral', 'chapel', 'cinema'):
            self.assertEqual(LG.landmark_class({'building': value}), value)
            self.assertTrue(LG.is_landmark({'building': value}))

    def test_amenity_cinema(self):
        self.assertEqual(LG.landmark_class({'amenity': 'cinema', 'building': 'yes'}), 'cinema')

    def test_shopping_centre_is_shop_mall_only(self):
        self.assertEqual(LG.landmark_class({'shop': 'mall'}), 'shopping_centre')
        self.assertEqual(LG.landmark_class({'building': 'retail', 'shop': 'mall'}), 'shopping_centre')
        self.assertIsNone(LG.landmark_class({'building': 'retail'}))
        self.assertIsNone(LG.landmark_class({'shop': 'convenience'}))

    def test_ordinary_buildings_are_not_landmarks(self):
        for tags in (
            {'building': 'house'},
            {'building': 'university'},
            {'building': 'civic'},
            {'building': 'industrial'},
            {'amenity': 'cafe'},
        ):
            self.assertIsNone(LG.landmark_class(tags), tags)

    def test_landmark_fields_and_annotate(self):
        self.assertEqual(
            LG.landmark_fields({'building': 'church'}),
            {'landmark': True, 'landmark_class': 'church'},
        )
        self.assertEqual(LG.landmark_fields({'building': 'house'}), {})
        items = LG.annotate_landmark_candidates(
            [{'kind': 'building', 'refs': ['w1'], 'label': 'Hall'}],
            tags_by_ref={'w1': {'building': 'chapel'}},
        )
        self.assertEqual(items[0]['landmark'], True)
        self.assertEqual(items[0]['landmark_class'], 'chapel')
        houses = LG.annotate_landmark_candidates(
            [{'kind': 'houses', 'refs': ['w2'], 'label': 'Street'}],
            tags_by_ref={'w2': {'building': 'church'}},
        )
        self.assertNotIn('landmark', houses[0])


class LandmarkApplyGateTest(unittest.TestCase):
    """Apply refuses without a recorded street-level source; allow with source or override."""

    SPEC = {
        'name': 'victory_centre',
        'ways': {'99': 'A'},
        'storeys': {'A': 2},
        'roofs': {'A': {'kind': 'flat', 'material': 'stone'}},
        'groups': [{'nos': ['A'], 'front': 'N'}],
        'floor_min': 50,
    }
    CELLS = {'A': {(x, z) for x in range(4) for z in range(4)}}
    PLANNED = [(50, -36)]

    def _require(self, *, override='', street_have=False, tags=None, cells=None):
        tags = tags if tags is not None else [{'building': 'church'}]
        cells = self.PLANNED if cells is None else cells
        with mock.patch.object(LG, 'tags_for_spec_ways', return_value=tags), \
                mock.patch.object(LG, 'cell_has_street_level_source', return_value=street_have):
            LG.require_landmark_street_check(
                self.SPEC, override_reason=override, cells=cells,
            )

    def test_refuses_without_street_level_source(self):
        with self.assertRaises(SystemExit) as ctx:
            self._require(street_have=False)
        msg = str(ctx.exception)
        self.assertIn('landmark church', msg)
        self.assertIn('street-level image', msg)
        self.assertIn('cell_sources.py --cell 50,-36 --record SOURCE --result found|partial|none|blocked', msg)
        self.assertIn('--landmark-override', msg)
        self.assertNotIn('Google', msg)
        self.assertNotIn('Street View', msg)
        self.assertNotIn('Mapillary', msg)

    def test_allows_when_street_level_source_recorded(self):
        self._require(street_have=True)

    def test_allows_with_reasoned_override(self):
        self._require(street_have=False, override='owner confirmed facade from site visit')

    def test_whitespace_override_does_not_skip_gate(self):
        with self.assertRaises(SystemExit):
            self._require(street_have=False, override='   ')

    def test_ordinary_building_skips_gate(self):
        self._require(street_have=False, tags=[{'building': 'retail'}])

    def test_run_apply_refuses_before_blocks_written(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'spec.json'
            path.write_text(json.dumps(self.SPEC))
            (F.ROOT / 'verification/site').mkdir(parents=True, exist_ok=True)
            with mock.patch('readiness_evidence.require_feature_ready'), \
                    mock.patch.object(LG, 'feature_cells_for_plan', return_value=self.PLANNED), \
                    mock.patch.object(LG, 'tags_for_spec_ways', return_value=[{'building': 'church'}]), \
                    mock.patch.object(LG, 'cell_has_street_level_source', return_value=False), \
                    mock.patch.object(F, 'landed_build_seqs', return_value=[]), \
                    mock.patch.object(F, 'preview') as preview, \
                    mock.patch.object(F, 'apply') as applied, \
                    mock.patch.object(F, 'build') as built, \
                    redirect_stdout(StringIO()):
                with self.assertRaises(SystemExit) as ctx:
                    F.run(path, do_apply=True, footprints=self.CELLS, world=object())
        self.assertIn('cell_sources.py --cell 50,-36 --record', str(ctx.exception))
        self.assertFalse(built.called)
        self.assertFalse(applied.called)
        self.assertFalse(preview.called)

    def test_run_apply_allows_with_override(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'spec.json'
            path.write_text(json.dumps(self.SPEC))
            (F.ROOT / 'verification/site').mkdir(parents=True, exist_ok=True)
            with mock.patch('readiness_evidence.require_feature_ready'), \
                    mock.patch.object(LG, 'feature_cells_for_plan', return_value=self.PLANNED), \
                    mock.patch.object(LG, 'tags_for_spec_ways', return_value=[{'building': 'church'}]), \
                    mock.patch.object(LG, 'cell_has_street_level_source', return_value=False), \
                    mock.patch.object(F, 'landed_build_seqs', return_value=[]), \
                    mock.patch.object(F, 'preview'), \
                    mock.patch.object(F, 'apply') as applied, \
                    mock.patch.object(F, 'build', return_value=mock.Mock(summary=lambda: {})), \
                    redirect_stdout(StringIO()):
                F.run(
                    path, do_apply=True, footprints=self.CELLS, world=object(),
                    landmark_override='bingo hall already surveyed on foot',
                )
        self.assertTrue(applied.called)

    def test_run_apply_allows_when_source_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'spec.json'
            path.write_text(json.dumps(self.SPEC))
            (F.ROOT / 'verification/site').mkdir(parents=True, exist_ok=True)
            with mock.patch('readiness_evidence.require_feature_ready'), \
                    mock.patch.object(LG, 'feature_cells_for_plan', return_value=self.PLANNED), \
                    mock.patch.object(LG, 'tags_for_spec_ways', return_value=[{'shop': 'mall'}]), \
                    mock.patch.object(LG, 'cell_has_street_level_source', return_value=True), \
                    mock.patch.object(F, 'landed_build_seqs', return_value=[]), \
                    mock.patch.object(F, 'preview'), \
                    mock.patch.object(F, 'apply') as applied, \
                    mock.patch.object(F, 'build', return_value=mock.Mock(summary=lambda: {})), \
                    redirect_stdout(StringIO()):
                F.run(path, do_apply=True, footprints=self.CELLS, world=object())
        self.assertTrue(applied.called)


class StreetLevelSourceTest(unittest.TestCase):
    """HAVE on any street-kind checklist family counts."""

    def test_have_on_street_family(self):
        status = {
            'google-satellite': {'state': 'have'},
            'kartaview': {'state': 'have', 'detail': 'ok'},
        }
        families = {
            'google-satellite': {'kind': 'aerial'},
            'kartaview': {'kind': 'street'},
        }
        with mock.patch('cell_sources.source_status', return_value=status), \
                mock.patch('cell_sources.SOURCE_FAMILIES', families):
            self.assertTrue(LG.cell_has_street_level_source(1, 2))

    def test_todo_street_family_is_not_enough(self):
        status = {'kartaview': {'state': 'todo', 'detail': 'fetch'}}
        families = {'kartaview': {'kind': 'street'}}
        with mock.patch('cell_sources.source_status', return_value=status), \
                mock.patch('cell_sources.SOURCE_FAMILIES', families):
            self.assertFalse(LG.cell_has_street_level_source(1, 2))


if __name__ == '__main__':
    unittest.main()
