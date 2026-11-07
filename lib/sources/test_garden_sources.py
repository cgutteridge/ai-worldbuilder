"""Unit tests for garden boundary hedge reading (#144).

Aerial dark-green must not count lidar tree-canopy columns as hedges; a designer
``type: none`` veto must beat an aerial hedge call.
"""
from __future__ import annotations

import unittest
from unittest import mock

import garden_sources as gs


class TestHedgeShareIgnoresCanopy(unittest.TestCase):
    """Canopy (nDSM tree mask) columns are not hedge evidence."""

    def test_shade_under_canopy_does_not_count(self):
        """A strip that is only dark green under tree canopy is not a hedge."""
        cells = [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)]
        rgb = {q: (20, 80, 20) for q in cells}  # would class as shade
        canopy = {(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)}
        with mock.patch('ground_sources.aerial_class', return_value='shade'):
            self.assertEqual(gs.hedge_share(cells, rgb, canopy=canopy), 0.0)

    def test_shade_outside_canopy_still_counts(self):
        """Dark green on non-canopy columns still contributes to the hedge share."""
        cells = [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)]
        rgb = {q: (20, 80, 20) for q in cells}
        canopy = {(0, 0), (1, 0)}  # two of five are canopy
        with mock.patch('ground_sources.aerial_class', return_value='shade'):
            # three non-canopy shade cells → share 1.0 among the scored cells
            self.assertEqual(gs.hedge_share(cells, rgb, canopy=canopy), 1.0)

    def test_mixed_ground_without_canopy_unchanged(self):
        """Without a canopy mask, behaviour matches the old shade fraction."""
        cells = [(0, 0), (1, 0), (2, 0), (3, 0)]
        rgb = {q: (0, 0, 0) for q in cells}
        with mock.patch('ground_sources.aerial_class', side_effect=['shade', 'shade', 'lawn', 'lawn']):
            self.assertEqual(gs.hedge_share(cells, rgb), 0.5)


class TestDesignerVeto(unittest.TestCase):
    """Designer ``type: none`` blocks aerial hedge promotion."""

    def test_role_vetoed_when_street_says_none(self):
        conv = {'front': {'type': 'none'}, 'rear': {'type': 'fence'}}
        self.assertTrue(gs.role_vetoed(conv, '12', 'front'))
        self.assertFalse(gs.role_vetoed(conv, '12', 'rear'))
        self.assertFalse(gs.role_vetoed(conv, '12', 'side_front'))

    def test_house_override_none_vetoes_street_hedge(self):
        conv = {
            'side_back': {'type': 'hedge'},
            'overrides': {'7': {'side_back': {'type': 'none'}}},
        }
        self.assertTrue(gs.role_vetoed(conv, '7', 'side_back'))
        self.assertFalse(gs.role_vetoed(conv, '9', 'side_back'))

    def test_aerial_hedge_skipped_when_vetoed(self):
        """garden_barriers must not promote an aerial hedge over an explicit none."""
        cell = (0, 0)
        rgb = {(x, z): (20, 80, 20) for x in range(10) for z in range(10)}
        # One segment: plot 1, side_back, enough near columns with high hedge share.
        cells = [(5, z) for z in range(10)]
        info = {1: {'street': 'Test Road', 'number': '1', 'centre': (5.0, 5.0),
                    'foot': (5, 0), 'map_ref': None}}
        owner = {q: 1 for q in cells}
        street = {}
        seg = {(1, 'side_back'): cells}
        conv = {'side_back': {'type': 'none'}}

        idx = mock.Mock()
        idx.cells.return_value = {}
        with mock.patch.object(gs, 'plots_for', return_value=(owner, info, street, idx)), \
                mock.patch.object(gs, 'segments', return_value=seg), \
                mock.patch.object(gs, 'load_convention', return_value=conv), \
                mock.patch.object(gs, 'hedge_share', return_value=1.0), \
                mock.patch.object(gs, 'straighten', side_effect=lambda c: c), \
                mock.patch('ground_sources.aerial_rgb', return_value=rgb), \
                mock.patch('ground_sources.cell_box', return_value=(0, 0, 9, 9)), \
                mock.patch.object(gs, 'tree_canopy_columns', return_value=set()):
            out = gs.garden_barriers(cell, rgb=rgb)
        self.assertEqual(out, {})


def _strip():
    """Synthetic street along z=0..; plot behind it (z 3..14, x 0..9); house z 10..13, x 3..6."""
    house = {(x, z) for x in range(3, 7) for z in range(10, 14)}
    plot = {(x, z) for x in range(0, 10) for z in range(3, 15)} - house
    front = [(x, 3) for x in range(0, 10)]
    meta = {'street': 'Test Road', 'number': '7', 'centre': (4.5, 11.5), 'foot': (4, 0), 'map_ref': None}
    return meta, plot, {'front': front}, house


class TestResidentialFrontage(unittest.TestCase):
    """Shared frontage description: extent, boundary, opening, connection, provenance (#240)."""

    CONV = {'front': {'type': 'fence'}}

    def test_inferred_entrance_opening_and_route(self):
        meta, plot, roles, house = _strip()
        f = gs.residential_frontage(meta, plot, roles, house, self.CONV)
        self.assertTrue(f['eligible'])
        self.assertEqual(f['door']['origin'], 'inferred')
        self.assertEqual(f['connection']['status'], 'inferred')
        self.assertNotEqual(f['connection']['status'], 'verified')
        self.assertTrue(f['opening'])
        self.assertTrue(set(f['opening']) <= set(roles['front']))
        self.assertTrue(set(f['route']).isdisjoint(house))
        self.assertEqual(f['provenance']['source'], 'inference')
        self.assertTrue(f['extent'])
        self.assertTrue(all(q[1] < 12 for q in f['extent']))     # front garden only, not the rear

    def test_observed_door_is_verified(self):
        meta, plot, roles, house = _strip()
        f = gs.residential_frontage(meta, plot, roles, house, self.CONV, door=(4, 10))
        self.assertEqual(f['door'], {'cell': [4, 10], 'origin': 'observed'})
        self.assertEqual(f['connection']['status'], 'verified')
        self.assertEqual(f['provenance']['source'], 'vector_map')

    def test_no_convention_means_no_boundary_or_opening(self):
        meta, plot, roles, house = _strip()
        f = gs.residential_frontage(meta, plot, roles, house, None)
        self.assertTrue(f['eligible'])
        self.assertEqual(f['boundary'], [])
        self.assertEqual(f['opening'], [])

    def test_per_house_none_is_open_frontage(self):
        meta, plot, roles, house = _strip()
        conv = {'front': {'type': 'fence'}, 'overrides': {'7': {'front': {'type': 'none'}}}}
        f = gs.residential_frontage(meta, plot, roles, house, conv)
        self.assertTrue(f['open_frontage'])
        self.assertEqual(f['boundary'], [])
        self.assertEqual(f['opening'], [])

    def test_unidentified_property_not_eligible(self):
        meta, plot, roles, house = _strip()
        meta['number'] = ''
        f = gs.residential_frontage(meta, plot, roles, house, self.CONV)
        self.assertFalse(f['eligible'])
        self.assertIn('house number', f['reason'])
        self.assertEqual(f['extent'], [])
        self.assertEqual(gs.private_frontage_columns([f]), set())

    def test_shop_or_campus_building_not_eligible(self):
        for tags in ({'building': 'retail'}, {'building': 'yes', 'shop': 'bakery'}, {'building': 'university'}):
            meta, plot, roles, house = _strip()
            meta['nonresidential'] = gs.nonresidential_building(tags)
            f = gs.residential_frontage(meta, plot, roles, house, self.CONV)
            self.assertFalse(f['eligible'], tags)
            self.assertIn('not residential', f['reason'])
        self.assertFalse(gs.nonresidential_building({'building': 'house'}))

    def test_opening_is_target_plus_nearest_neighbour(self):
        meta, plot, roles, house = _strip()
        f = gs.residential_frontage(meta, plot, roles, house, self.CONV)
        self.assertEqual(len(f['opening']), gs.OPENING_WIDTH)
        self.assertIn(f['route'][-1], [tuple(q) for q in f['opening']])

    def test_no_street_evidence_not_eligible(self):
        meta, plot, _roles, house = _strip()
        f = gs.residential_frontage(meta, plot, {}, house, self.CONV)
        self.assertFalse(f['eligible'])
        self.assertIn('frontage evidence', f['reason'])

    def test_route_through_house_is_reported_blocked(self):
        meta, plot, roles, house = _strip()
        blocker = {(4, 9), (4, 8), (4, 7), (4, 6), (4, 5), (4, 4)}
        f = gs.residential_frontage(meta, plot, roles, house | blocker, self.CONV, door=(4, 10))
        self.assertEqual(f['connection']['status'], 'blocked')
        self.assertEqual(f['connection']['pass'], 'ground')
        self.assertEqual(len(f['connection']['endpoints']), 2)
        self.assertEqual(f['opening'], [])

    def test_reserved_and_private_helpers(self):
        meta, plot, roles, house = _strip()
        f = gs.residential_frontage(meta, plot, roles, house, self.CONV)
        self.assertEqual(gs.reserved_openings([f]), {tuple(q) for q in f['opening']})
        self.assertEqual(gs.private_frontage_columns([f]), {tuple(q) for q in f['extent']})


class TestTreeCanopyColumns(unittest.TestCase):
    """nDSM / lidar tree-kind mask as (x, z) columns."""

    def test_collects_tree_and_tree_over_roof(self):
        import numpy as np
        import lidar_heightmap as L

        kind = np.zeros((4, 4), dtype=np.uint8)
        kind[1, 2] = L.TREE
        kind[3, 0] = L.TREE_OVER_ROOF
        kind[0, 0] = L.ROOF
        hm = {'cell': (1, -1), 'x0': 128, 'z0': -128, 'kind': kind}
        maps = mock.Mock()
        maps.of.return_value = hm
        got = gs.tree_canopy_columns((1, -1), maps=maps)
        self.assertEqual(got, {(129, -126), (131, -128)})


if __name__ == '__main__':
    unittest.main()
