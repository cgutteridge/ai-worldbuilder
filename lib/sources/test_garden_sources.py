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

        with mock.patch.object(gs, 'plots_for', return_value=(owner, info, street, mock.Mock())), \
                mock.patch.object(gs, 'segments', return_value=seg), \
                mock.patch.object(gs, 'load_convention', return_value=conv), \
                mock.patch.object(gs, 'hedge_share', return_value=1.0), \
                mock.patch.object(gs, 'straighten', side_effect=lambda c: c), \
                mock.patch('ground_sources.aerial_rgb', return_value=rgb), \
                mock.patch('ground_sources.cell_box', return_value=(0, 0, 9, 9)), \
                mock.patch.object(gs, 'tree_canopy_columns', return_value=set()):
            out = gs.garden_barriers(cell, rgb=rgb)
        self.assertEqual(out, {})


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
