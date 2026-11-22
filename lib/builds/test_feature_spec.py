"""Tests for feature spec seams."""

from __future__ import annotations

from pathlib import Path
from unittest import mock
import json
import tempfile
import unittest

from feature_build_fixtures import (
    _StoneWorld,
    _glazing_plan,
    build_spec,
    start_lidar_fixture as setUpModule,
    stop_lidar_fixture as tearDownModule,
    valid_spec,
    write_spec,
)
import feature_build as F
import feature_spec as FS


class LoadSpecTest(unittest.TestCase):
    """Seam: load_spec validates and fills defaults."""

    write = write_spec
    good = staticmethod(valid_spec)


    def test_loads_defaults_and_required_keys(self):
        spec = FS.load_spec(self.write(self.good()))
        self.assertEqual(spec['name'], 'demo')
        self.assertEqual(spec['storeys']['A'], 3)
        self.assertEqual(spec['roofs']['A']['kind'], 'flat')
        self.assertEqual(
            (spec['modifies'], spec['floor_min'], spec['step_max'], spec['door_frame'], spec['keep_edits']),
            ([], None, 4, True, False),
        )
        self.assertEqual(spec['wall'], 'bricks')
        self.assertEqual(spec['floor'], 'smooth_stone')
        self.assertEqual(spec['facade_plan'], {})
        self.assertEqual(spec['details'], {})
        self.assertNotIn('doors', spec)
        self.assertEqual(spec['zones'], {})
        self.assertEqual(spec['exclude'], {})

    def test_missing_key_bad_front_unknown_label_bad_storeys_refused(self):
        good = self.good()
        cases = [
            {k: v for k, v in good.items() if k != 'storeys'},
            {k: v for k, v in good.items() if k != 'roofs'},
            {**good, 'groups': [{'nos': ['B'], 'front': 'N'}]},
            {**good, 'groups': [{'nos': ['A'], 'front': 'X'}]},
            {**good, 'storeys': {'A': 0}},
            {**good, 'roofs': {'A': {'kind': 'dome', 'material': 'brick'}}},
            {**good, 'roofs': {'A': 'brick'}},
        ]
        for bad in cases:
            with self.subTest(bad=bad):
                with self.assertRaises(SystemExit):
                    FS.load_spec(self.write(bad))

    def test_legacy_glazing_key_is_refused_with_migration_pointer(self):
        with self.assertRaisesRegex(SystemExit, 'facade_plan'):
            FS.load_spec(self.write({**self.good(), 'facades': {}}))

    def test_legacy_doors_key_refused(self):
        """#146: per-portal doors map is gone; entrances live on facade_plan."""
        good = self.good()
        with self.assertRaises(SystemExit) as ctx:
            FS.load_spec(self.write({
                **good,
                'doors': {'42-A': {'leaves': 2, 'block': 'iron_door'}},
            }))
        self.assertIn('doors', str(ctx.exception))
        self.assertIn('facade_plan', str(ctx.exception))

    def test_roof_edge_absent_by_default_and_accepted_when_valid(self):
        spec = FS.load_spec(self.write(self.good()))
        self.assertNotIn('edge', spec['roofs']['A'])
        ok = {
            **self.good(),
            'roofs': {
                'A': {
                    'kind': 'flat',
                    'material': 'smooth_stone',
                    'edge': {'block': 'smooth_stone', 'where': 'outer'},
                },
            },
        }
        loaded = FS.load_spec(self.write(ok))
        self.assertEqual(loaded['roofs']['A']['edge'], {'block': 'smooth_stone', 'where': 'outer'})

    def test_roof_edge_bad_values_refused(self):
        good = self.good()
        for bad_edge in (
            'smooth_stone',
            {'block': 'smooth_stone'},
            {'where': 'outer'},
            {'block': '', 'where': 'outer'},
            {'block': 'smooth_stone', 'where': 'steps'},
            {'block': 1, 'where': 'all'},
        ):
            with self.subTest(bad_edge=bad_edge):
                roof = {'kind': 'flat', 'material': 'smooth_stone', 'edge': bad_edge}
                with self.assertRaises(SystemExit):
                    FS.load_spec(self.write({**good, 'roofs': {'A': roof}}))

    def test_roof_flat_tol_m_accepted_on_pitched_and_bad_values_refused(self):
        good = self.good()
        ok = {'kind': 'pitched', 'material': 'brick', 'flat_tol_m': 1.25}
        self.assertEqual(
            FS.load_spec(self.write({**good, 'roofs': {'A': ok}}))['roofs']['A']['flat_tol_m'], 1.25)
        for bad in ('1.0', True, -0.1, 3.1, float('nan'), None):
            with self.subTest(bad=bad):
                with self.assertRaises(SystemExit):
                    FS.load_spec(self.write({**good, 'roofs': {'A': {**ok, 'flat_tol_m': bad}}}))
        with self.assertRaisesRegex(SystemExit, 'pitched'):
            FS.load_spec(self.write({**good, 'roofs': {'A': {
                'kind': 'flat', 'material': 'smooth_stone', 'flat_tol_m': 1.0}}}))


class ExcludeSpecTest(unittest.TestCase):
    """exclude boxes: validated on load, cut from the footprint on build."""

    write = write_spec
    good = staticmethod(valid_spec)

    def test_exclude_accepted_and_bad_boxes_refused(self):
        good = self.good()
        good['exclude'] = {'A': [[0, 0, 5, 5]]}
        self.assertEqual(FS.load_spec(self.write(good))['exclude'], {'A': [[0, 0, 5, 5]]})
        for bad in ({'Z': [[0, 0, 1, 1]]}, {'A': [[5, 0, 1, 1]]}, {'A': [[0, 0, 1]]},
                    {'A': [[0, 0, 1, 'x']]}, {'A': 'no'}, ['A']):
            spec = self.good()
            spec['exclude'] = bad
            with self.assertRaises(SystemExit, msg=str(bad)):
                FS.load_spec(self.write(spec))

    def test_build_drops_excluded_cells_from_footprint(self):
        cells = {(x, z) for x in range(10, 15) for z in range(20, 25)}
        spec = build_spec()
        spec['exclude'] = {'A': [[10, 20, 14, 21]]}
        b = F.Build(spec, footprints={'A': cells}, world=_StoneWorld())
        self.assertEqual(b.by_no_all['A'], {c for c in cells if c[1] >= 22})

    def test_build_refuses_when_everything_excluded(self):
        spec = build_spec()
        spec['exclude'] = {'A': [[0, 0, 99, 99]]}
        with self.assertRaises(SystemExit):
            F.Build(spec, footprints={'A': {(10, 20)}}, world=_StoneWorld())


    def test_clipped_cells_are_not_foreign_but_other_buildings_are(self):
        class Idx:
            buildings = [(0, {'osm_way_id': '99'}), (1, {'osm_way_id': '7'})]

            def cells(self):
                cells = {(x, z): 0 for x in range(10, 15) for z in range(20, 25)}
                cells[(30, 30)] = 1
                return cells

        spec = build_spec()
        spec['exclude'] = {'A': [[10, 20, 14, 21]]}
        with mock.patch.object(F.osm_footprints, 'OSMIndex', return_value=Idx()):
            b = F.Build(spec, world=_StoneWorld())
        self.assertEqual(b._foreign_cells(), {(30, 30)})


class FacadeSpecTest(unittest.TestCase):
    """Seam: load_spec accepts per-face facade rows; rejects bad storey/face/height."""

    def write(self, spec: dict) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 's.json'
        path.write_text(json.dumps(spec))
        return path

    def good(self) -> dict:
        return {
            'name': 'facade_demo',
            'ways': {'1': 'A'},
            'storeys': {'A': 2},
            'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
            'groups': [{'nos': ['A'], 'front': 'S'}],
            'facade_plan': _glazing_plan({
                'A': {
                    'S': [{'storey': 1, 'glazed_h': 3, 'pier': 1, 'wall': 'bricks'}],
                    'N': [{'storey': 1, 'glazed_h': 2, 'pier': 1, 'rhythm': 'WW_WW'}],
                }
            }),
        }

    def test_loads_facade_table(self):
        spec = FS.load_spec(self.write(self.good()))
        self.assertEqual(spec['facade_plan']['A']['faces']['S']['glazing'][0]['glazed_h'], 3)
        self.assertEqual(spec['facade_plan']['A']['faces']['N']['glazing'][0]['rhythm'], 'WW_WW')
        self.assertEqual(spec['facade_plan']['A']['faces']['S']['glazing'][0]['pier'], 1)

    def test_facades_optional_default_empty(self):
        base = {k: v for k, v in self.good().items() if k != 'facade_plan'}
        spec = FS.load_spec(self.write(base))
        self.assertEqual(spec['facade_plan'], {})

    def test_bad_facade_rows_refused(self):
        good = self.good()
        cases = [
            {**good, 'facade_plan': _glazing_plan({'B': {'S': [{'storey': 1, 'glazed_h': 3, 'pier': 1}]}})},
            {**good, 'facade_plan': _glazing_plan({'A': {'X': [{'storey': 1, 'glazed_h': 3, 'pier': 1}]}})},
            {**good, 'facade_plan': _glazing_plan({'A': {'S': [{'storey': 0, 'glazed_h': 3, 'pier': 1}]}})},
            {**good, 'facade_plan': _glazing_plan({'A': {'S': [{'storey': 3, 'glazed_h': 3, 'pier': 1}]}})},
            {**good, 'facade_plan': _glazing_plan({'A': {'S': [{'storey': 1, 'glazed_h': 4, 'pier': 1}]}})},
            {**good, 'facade_plan': _glazing_plan({'A': {'S': [{'storey': 1, 'glazed_h': 0, 'pier': 1}]}})},
            {**good, 'facade_plan': _glazing_plan({'A': {'S': [{'storey': 1, 'glazed_h': 3, 'pier': -1}]}})},
            {**good, 'facade_plan': _glazing_plan({'A': {'S': [{'storey': 1, 'glazed_h': 3}]}})},
        ]
        for bad in cases:
            with self.subTest(bad=bad.get('facade_plan')):
                with self.assertRaises(SystemExit):
                    FS.load_spec(self.write(bad))


class ZonesSpecTest(unittest.TestCase):
    """Seam: load_spec accepts zones (flat or pitched); refuses bad from/levels."""

    def write(self, spec: dict) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 's.json'
        path.write_text(json.dumps(spec))
        return path

    def good(self) -> dict:
        return {
            'name': 'zones_demo',
            'ways': {'1': 'A'},
            'storeys': {'A': 4},
            'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
            'groups': [{'nos': ['A'], 'front': 'N'}],
            'zones': {
                'A': {'from': 'lidar', 'levels': [41, 45, 50], 'bucket': 4, 'axis_deg': 'auto'},
            },
        }

    def test_loads_zones_defaults(self):
        spec = FS.load_spec(self.write(self.good()))
        self.assertEqual(spec['zones']['A']['levels'], [41, 45, 50])
        self.assertEqual(spec['zones']['A']['bucket'], 4)
        self.assertEqual(spec['zones']['A']['axis_deg'], 'auto')

    def test_auto_levels_accepted(self):
        g = self.good()
        g['zones']['A']['levels'] = 'auto'
        spec = FS.load_spec(self.write(g))
        self.assertEqual(spec['zones']['A']['levels'], 'auto')

    def test_bad_zones_refused(self):
        good = self.good()
        cases = [
            {**good, 'zones': {'B': {'from': 'lidar', 'levels': [40]}}},
            {**good, 'zones': {'A': {'from': 'osm', 'levels': [40]}}},
            {**good, 'zones': {'A': {'from': 'lidar', 'levels': []}}},
            {**good, 'zones': {'A': {'from': 'lidar', 'levels': 'nope'}}},
            {**good, 'zones': {'A': {'from': 'lidar', 'levels': [40], 'bucket': 0}}},
        ]
        for bad in cases:
            with self.subTest(bad=bad.get('zones') or bad.get('roofs')):
                with self.assertRaises(SystemExit):
                    FS.load_spec(self.write(bad))

        pitched = {
            **good,
            'roofs': {'A': {'kind': 'pitched', 'material': 'brick'}},
        }
        spec = FS.load_spec(self.write(pitched))
        self.assertEqual(spec['roofs']['A']['kind'], 'pitched')
        self.assertIn('A', spec['zones'])


class DetailsSpecTest(unittest.TestCase):
    """Seam: per-label details table (roof + facade greeblies) validates and defaults."""

    def write(self, spec: dict) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 's.json'
        path.write_text(json.dumps(spec))
        return path

    def good(self) -> dict:
        return {
            'name': 'detail_demo',
            'ways': {'1': 'A'},
            'storeys': {'A': 2},
            'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
            'groups': [{'nos': ['A'], 'front': 'S'}],
        }

    def test_details_optional_default_empty(self):
        spec = FS.load_spec(self.write(self.good()))
        self.assertEqual(spec['details'], {})

    def test_loads_roof_and_facade_details(self):
        ok = {
            **self.good(),
            'details': {
                'A': [
                    {
                        'kind': 'plant_room',
                        'at': [12, 56, 22],
                        'size': [3, 2, 2],
                        'source': 'aerial NW plant box',
                    },
                    {
                        'kind': 'drainpipe',
                        'at': [10, 50, 24],
                        'size': [1, 6, 1],
                        'face': 'S',
                        'source': 'street photo south',
                    },
                ],
            },
        }
        spec = FS.load_spec(self.write(ok))
        self.assertEqual(spec['details']['A'][0]['kind'], 'plant_room')
        self.assertEqual(spec['details']['A'][1]['face'], 'S')

    def test_bad_details_refused(self):
        good = self.good()
        base_row = {
            'kind': 'vent',
            'at': [11, 56, 21],
            'size': [1, 1, 1],
            'source': 'aerial',
        }
        cases = [
            {**good, 'details': {'B': [base_row]}},
            {**good, 'details': {'A': [{**base_row, 'kind': 'chimney'}]}},
            {**good, 'details': {'A': [{**base_row, 'at': [11, 21]}]}},
            {**good, 'details': {'A': [{**base_row, 'size': [0, 1, 1]}]}},
            {**good, 'details': {'A': [{**base_row, 'source': ''}]}},
            {**good, 'details': {'A': [{**base_row, 'kind': 'drainpipe'}]}},
            {**good, 'details': {'A': [{**base_row, 'kind': 'sign', 'face': 'X'}]}},
            {**good, 'details': {'A': 'vent'}},
        ]
        for bad in cases:
            with self.subTest(bad=bad.get('details')):
                with self.assertRaises(SystemExit):
                    FS.load_spec(self.write(bad))

    def test_old_detail_kind_canopy_refused_naming_awning_like(self):
        """No silent alias: legacy details kind ``canopy`` must name awning_like."""
        good = self.good()
        row = {
            'kind': 'canopy',
            'at': [11, 55, 21],
            'size': [2, 1, 1],
            'face': 'S',
            'source': 'street photo',
        }
        with self.assertRaises(SystemExit) as ctx:
            FS.load_spec(self.write({**good, 'details': {'A': [row]}}))
        msg = str(ctx.exception)
        self.assertIn('awning_like', msg)
        self.assertIn('canopy', msg)
