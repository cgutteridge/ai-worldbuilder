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
        self.assertEqual(out, [])


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


class TestLandedApproach(unittest.TestCase):
    """A landed feature's approach gives the route its start (#240)."""

    CONV = {'front': {'type': 'fence'}}

    def approach(self):
        import entrance_approach as ea
        return ea.entrance_approach((4, 10), 'N', approach_cells=[(4, 9), (4, 8)], base_y=64)

    def test_route_starts_at_outer_end_of_feature_steps(self):
        meta, plot, roles, house = _strip()
        f = gs.residential_frontage(meta, plot, roles, house, self.CONV, approach=self.approach())
        self.assertEqual(f['door'], {'cell': [4, 10], 'origin': 'generated'})
        self.assertEqual(f['connection']['status'], 'built')
        self.assertEqual(f['connection']['endpoints'][0], [4, 7])
        self.assertEqual(f['route'][0], (4, 7))
        self.assertTrue({(4, 9), (4, 8)}.isdisjoint(f['route']))      # never duplicates feature steps
        self.assertEqual({tuple(c) for c in f['clearance']}, {(4, 9), (4, 8), (4, 7)})
        self.assertIn((4, 8), gs.reserved_route([f]))

    def test_approach_attaches_to_the_plot_holding_the_door(self):
        meta, plot, roles, house = _strip()
        idx = mock.Mock()
        idx.cells.return_value = {q: 1 for q in house}
        owner = {q: 1 for q in plot}
        out = gs.frontages_for_plots(owner, {1: meta}, {(1, 'front'): roles['front']}, idx,
                                     approaches=[self.approach()])
        self.assertEqual(out[0]['connection']['status'], 'built')

    def test_rear_door_is_ignored_for_the_street_frontage(self):
        import entrance_approach as ea
        meta, plot, roles, house = _strip()
        rear = ea.entrance_approach((3, 13), 'S', approach_cells=[(3, 14)])    # lower x, faces away
        front = self.approach()
        idx = mock.Mock()
        idx.cells.return_value = {q: 1 for q in house}
        out = gs.frontages_for_plots({q: 1 for q in plot}, {1: meta}, {(1, 'front'): roles['front']},
                                     idx, approaches=[rear, front])
        self.assertEqual(out[0]['door']['cell'], [4, 10])
        self.assertEqual(out[0]['connection']['status'], 'built')

    def test_no_landed_door_stays_inferred(self):
        meta, plot, roles, house = _strip()
        f = gs.residential_frontage(meta, plot, roles, house, self.CONV)
        self.assertEqual(f['connection']['status'], 'inferred')

    def test_landed_approaches_reads_only_landed_feature_builds(self):
        door = 'spruce_door[facing=north,half=lower,hinge=left,open=false]'
        E = lambda plan, **kw: mock.Mock(kind='build', pass_='feature', status='landed', undone=False,
                                          plan=plan, **kw)
        reg = mock.Mock()
        reg.entries.return_value = [E('a'), E('b', undone=True) if False else mock.Mock(
            kind='build', pass_='feature', status='landed', undone=True, plan='b'),
            mock.Mock(kind='build', pass_='ground', status='landed', undone=False, plan='c')]
        reg.recorded.side_effect = lambda plan: {(4, 65, 10): door} if plan == 'a' else {(9, 65, 9): door}
        got = gs.landed_approaches((0, 0), registry=reg, footprint={(4, 10)})
        self.assertEqual([a['door']['cell'] for a in got], [[4, 10]])

    def test_diagonal_cell_edge_route_stays_four_connected_and_clear_of_house(self):
        meta, plot, roles, house = _strip()
        meta['foot'] = (0, 0)                                   # street foot far to one side: diagonal route
        f = gs.residential_frontage(meta, plot, roles, house, self.CONV, door=(3, 10))
        route = f['route']
        self.assertTrue(route and set(route).isdisjoint(house))
        for a, b in zip(route, route[1:]):
            self.assertEqual(abs(a[0] - b[0]) + abs(a[1] - b[1]), 1)

    def test_blocked_connections_report(self):
        meta, plot, roles, house = _strip()
        blocker = {(4, 9), (4, 8), (4, 7), (4, 6), (4, 5), (4, 4)}
        f = gs.residential_frontage(meta, plot, roles, house | blocker, self.CONV, door=(4, 10))
        (row,) = gs.blocked_connections([f])
        self.assertEqual(row['pass'], 'ground')
        self.assertEqual(gs.frontage_route_columns([f]), set())


class TestGardenItemProvenance(unittest.TestCase):
    def garden(self, conv=None, share=1.0):
        meta, plot, roles, house = _strip()
        idx = mock.Mock()
        idx.cells.return_value = {q: 1 for q in house}
        report = {}
        with mock.patch.object(gs, 'plots_for', return_value=({q: 1 for q in plot}, {1: meta}, {}, idx)), \
                mock.patch.object(gs, 'segments', return_value={(1, 'front'): roles['front']}), \
                mock.patch.object(gs, 'load_convention', return_value=conv), \
                mock.patch.object(gs, 'hedge_share', return_value=share), \
                mock.patch.object(gs, 'tree_canopy_columns', return_value=set()), \
                mock.patch.object(gs, 'landed_approaches', return_value=[]):
            return gs.garden_barriers((0, 0), rgb={}, report=report), report

    def test_aerial_front_hedge_has_opening_after_straightening(self):
        items, report = self.garden()
        (frontage,) = report['frontages']
        self.assertEqual(len(frontage['opening']), gs.OPENING_WIDTH)
        cols = {tuple(c) for i in items for c in i['cells']}
        self.assertTrue(cols.isdisjoint(gs.reserved_route([frontage])))
        self.assertTrue(cols.isdisjoint(gs.reserved_openings([frontage])))
        self.assertEqual(items[0]['provenance']['source'], 'aerial')
        self.assertEqual(items[0]['scope']['property'], '7')

    def test_same_material_keeps_distinct_street_and_property_provenance(self):
        for override, expected in ((False, 'inference'), (True, 'register_entry')):
            conv = {'front': {'type': 'hedge'}, 'evidence_ids': ['street-photo']}
            if override:
                conv['overrides'] = {'7': {'front': {'type': 'hedge', 'evidence_ids': ['property-photo']}}}
            items, _ = self.garden(conv, share=0.0)
            item = items[0]
            self.assertEqual(item['provenance']['source'], expected)
            self.assertEqual(item['provenance']['evidence_ids'], ['property-photo' if override else 'street-photo'])
            self.assertEqual(item['scope']['layer'], 'property' if override else 'street')
            self.assertTrue(item['reason'])

    def test_street_observation_supports_labelled_inference_at_property(self):
        conv = {'front': {'type': 'hedge', 'provenance': {
            'source': 'photo_read', 'confidence': 'high', 'evidence_ids': ['street-photo']}}}
        items, _ = self.garden(conv, share=0.0)
        self.assertEqual(items[0]['provenance']['source'], 'inference')
        self.assertEqual(items[0]['provenance']['evidence_ids'], ['street-photo'])
        self.assertEqual(items[0]['scope']['layer'], 'street')

    def test_property_override_and_none_win_over_aerial(self):
        items, _ = self.garden({'front': {'type': 'hedge'},
                      'overrides': {'7': {'front': {'type': 'fence'}}}})
        self.assertEqual(items[0]['kind'], 'fence')
        items, report = self.garden({'overrides': {'7': {'front': {'type': 'none'}}}})
        self.assertEqual(items, [])
        self.assertTrue(report['frontages'][0]['open_frontage'])

    def test_missing_convention_and_no_aerial_authorize_no_boundary(self):
        items, report = self.garden(share=0.0)
        self.assertEqual(items, [])
        self.assertEqual(report['frontages'][0]['opening'], [])

    def test_landed_scan_stops_at_other_mapped_building_and_deduplicates(self):
        from types import SimpleNamespace
        blocks = {(4, 65, 10): 'spruce_door[facing=north,half=lower]',
                  (4, 64, 9): 'smooth_stone', (4, 64, 8): 'bricks'}
        entry = SimpleNamespace(kind='build', pass_='feature', status='landed', undone=False, plan='house')
        registry = mock.Mock()
        registry.entries.return_value = [entry, entry]
        registry.recorded.return_value = blocks
        (approach,) = gs.landed_approaches((0, 0), registry=registry, footprint={(4, 10), (4, 8)})
        self.assertEqual(approach['approach'], [[4, 9]])
        self.assertEqual(approach['connection']['cell'], [4, 8])
        registry.recorded.assert_called_once_with('house')


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


def _class_meta(cls, **kw):
    meta, plot, roles, house = _strip()
    meta['land_use'] = {'class': cls, 'reason': 'test', 'ref': None}
    meta.update(kw)
    return meta, plot, roles, house


def _row(cls, **frontage):
    import copy
    import land_use as lu
    row = copy.deepcopy(lu.defaults(cls))
    row['frontage'].update(frontage)
    return row


class TestFrontageByClass(unittest.TestCase):
    """One strip per land-use class plus unknown (#254): the class row decides, residential is one row."""

    FENCE = {'type': 'fence', 'height': 2}

    def test_residential_is_the_residential_row(self):
        meta, plot, roles, house = _class_meta('residential')
        a = gs.frontage(meta, plot, roles, house, {'front': {'type': 'fence'}})
        meta2, *_ = _strip()
        b = gs.residential_frontage(meta2, plot, roles, house, {'front': {'type': 'fence'}})
        for k in ('eligible', 'extent', 'boundary', 'opening', 'route', 'door', 'connection'):
            self.assertEqual(a[k], b[k], k)
        self.assertEqual(a['scope']['layer'], 'street')
        self.assertEqual(a['private_surface'], 'grass')

    def test_house_classes_need_no_number_but_a_street_edge(self):
        for cls in ('commercial', 'industrial', 'civic', 'transport', 'parking', 'park'):
            meta, plot, roles, house = _class_meta(cls, number='')
            f = gs.frontage(meta, plot, roles, house, None)
            self.assertTrue(f['eligible'], cls)
            self.assertEqual(f['land_use'], cls)
            self.assertEqual(f['boundary'], [], cls)               # no class ships a fence
            self.assertEqual(f['opening'], [], cls)
            f = gs.frontage(meta, plot, {}, house, None)
            self.assertFalse(f['eligible'], cls)
            self.assertIn('street-facing', f['reason'])

    def test_no_inference_classes_and_unknown_are_never_eligible(self):
        for cls in ('rural', 'natural', 'water', 'unknown'):
            meta, plot, roles, house = _class_meta(cls)
            f = gs.frontage(meta, plot, roles, house, self.CONV_FENCE)
            self.assertFalse(f['eligible'], cls)
            self.assertIn('allows no frontage inference', f['reason'])
            self.assertEqual((f['boundary'], f['opening'], f['route'], f['extent']), ([], [], [], []))

    CONV_FENCE = {'front': {'type': 'fence'}}

    def test_keep_classes_keep_guessed_lawn_paving_classes_do_not(self):
        keep, pave = {}, {}
        for cls in ('park', 'rural', 'natural', 'water', 'unknown', 'residential'):
            meta, plot, roles, house = _class_meta(cls)
            keep[cls] = gs.private_frontage_columns([gs.frontage(meta, plot, roles, house, None)])
        for cls in ('commercial', 'industrial', 'civic', 'transport', 'parking'):
            meta, plot, roles, house = _class_meta(cls)
            pave[cls] = gs.private_frontage_columns([gs.frontage(meta, plot, roles, house, None)])
        self.assertTrue(all(keep.values()), keep)
        self.assertEqual(sum(len(v) for v in pave.values()), 0)

    def test_class_default_fence_builds_labelled_class_and_street_gate_is_reserved(self):
        row = _row('commercial', boundary={**_row('commercial')['frontage']['boundary'], 'front': self.FENCE})
        meta, plot, roles, house = _class_meta('commercial')
        f = gs.frontage(meta, plot, roles, house, None, row=row)
        self.assertEqual(f['scope']['layer'], 'class')
        self.assertEqual(f['boundary'], sorted(roles['front']))
        self.assertEqual(len(f['opening']), gs.OPENING_WIDTH)
        self.assertTrue(set(f['opening']) <= set(roles['front']))
        self.assertEqual(f['route'], [])                           # a street gate is not a door route
        self.assertEqual(gs.role_spec(None, '7', 'front', 'commercial', row)['type'], 'fence')

    def test_same_fence_row_under_unknown_is_refused(self):
        row = _row('unknown', boundary={**_row('unknown')['frontage']['boundary'], 'front': self.FENCE})
        self.assertIsNone(gs.role_spec(None, '7', 'front', 'unknown', row))
        meta, plot, roles, house = _class_meta('unknown')
        f = gs.frontage(meta, plot, roles, house, None, row=row)
        self.assertEqual(f['boundary'], [])
        import land_use as lu
        with self.assertRaises(lu.TableError):                      # and the table refuses to load it
            import copy
            doc = copy.deepcopy(lu.load_table())
            doc = {'classes': list(doc['classes'].values())}
            [r for r in doc['classes'] if r['id'] == 'unknown'][0]['frontage'] = row['frontage']
            lu.validate_table(doc)

    def test_explicit_none_beats_a_class_default(self):
        row = _row('commercial', boundary={**_row('commercial')['frontage']['boundary'], 'front': self.FENCE})
        meta, plot, roles, house = _class_meta('commercial')
        f = gs.frontage(meta, plot, roles, house, {'front': {'type': 'none'}}, row=row)
        self.assertTrue(f['open_frontage'])
        self.assertEqual((f['boundary'], f['opening']), ([], []))
        self.assertEqual(f['scope']['layer'], 'street')

    def test_street_convention_beats_class_default_and_override_beats_both(self):
        row = _row('civic', boundary={**_row('civic')['frontage']['boundary'], 'front': self.FENCE})
        conv = {'front': {'type': 'brick_wall'}, 'overrides': {'7': {'front': {'type': 'hedge'}}}}
        self.assertEqual(gs.role_spec(conv, '7', 'front', 'civic', row)['type'], 'hedge')
        self.assertEqual(gs.role_spec(conv, '8', 'front', 'civic', row)['type'], 'brick_wall')
        self.assertEqual(gs.role_spec({}, '8', 'front', 'civic', row)['type'], 'fence')

    def test_pavement_strip_width_comes_from_the_class(self):
        meta, plot, roles, house = _class_meta('residential')
        f = gs.frontage(meta, plot, roles, house, None)
        self.assertEqual(f['pavement'], sorted({(x, z) for x in range(10) for z in (1, 2)}))
        self.assertEqual(gs.pavement_columns([f]), {tuple(q) for q in f['pavement']})
        wide = _row('residential', pavement=4)
        f = gs.frontage(meta, plot, roles, house, None, row=wide)
        self.assertEqual({z for _x, z in f['pavement']}, {-1, 0, 1, 2})
        self.assertTrue(set(f['pavement']).isdisjoint(plot))       # public strip is outside the plot
        self.assertEqual(gs.forecourt_columns([f]), {tuple(q) for q in f['extent']})

    def test_legacy_nonresidential_flag_still_blocks_frontage(self):
        meta, plot, roles, house = _strip()
        meta['nonresidential'] = True
        f = gs.residential_frontage(meta, plot, roles, house, self.CONV_FENCE)
        self.assertFalse(f['eligible'])


class TestPlotsAreClassified(unittest.TestCase):
    """plots_for gives each plot a land-use class; non-house plots get perimeter edges (#254)."""

    @staticmethod
    def _plots(tags, retail):
        import land_use as lu  # noqa: F401
        from test_land_use import index, sq
        areas = [(sq(0, 0, 60, 60), {'landuse': 'retail'})] if retail else []
        idx = index(areas=areas, buildings=[(sq(20, 20, 24, 24), tags)])
        with mock.patch('osm_footprints.OSMIndex', return_value=idx):
            return gs.plots_for((0, 0, 60, 60), margin=10)

    def test_house_number_inside_retail_land_becomes_commercial(self):
        """Behaviour change (owner-confirmed 2026-11-12): a number alone no longer makes a house residential."""
        tags = {'building': 'yes', 'addr:housenumber': '5', 'addr:street': 'High Street'}
        _o, info, *_ = self._plots(tags, retail=True)
        self.assertEqual({v['land_use']['class'] for v in info.values()}, {'commercial'})
        _o, info, *_ = self._plots(tags, retail=False)
        self.assertEqual({v['land_use']['class'] for v in info.values()}, {'residential'})
        _o, info, *_ = self._plots(dict(tags, building='house'), retail=True)
        self.assertEqual({v['land_use']['class'] for v in info.values()}, {'residential'})   # tag wins

    def test_shop_tagged_building_is_commercial_and_unmapped_yes_is_unknown(self):
        _o, info, *_ = self._plots({'building': 'yes', 'shop': 'bakery'}, retail=False)
        self.assertEqual({v['land_use']['class'] for v in info.values()}, {'commercial'})
        _o, info, *_ = self._plots({'building': 'yes'}, retail=False)
        self.assertEqual({v['land_use']['class'] for v in info.values()}, {'unknown'})

    def test_non_house_edges_to_neighbours_are_perimeter(self):
        from test_land_use import index, sq
        idx = index(buildings=[(sq(10, 20, 14, 24), {'building': 'house', 'addr:housenumber': '1'}),
                               (sq(30, 20, 34, 24), {'building': 'retail'})])
        with mock.patch('osm_footprints.OSMIndex', return_value=idx):
            owner, info, street, idx2 = gs.plots_for((0, 0, 60, 60), margin=10)
        seg = gs.segments(owner, info, street, idx2, (0, 0, 60, 60))
        retail = [i for i, v in info.items() if v['land_use']['class'] == 'commercial'][0]
        house = [i for i, v in info.items() if v['land_use']['class'] == 'residential'][0]
        self.assertIn((retail, 'perimeter'), seg)
        self.assertEqual({r for (i, r) in seg if i == retail}, {'perimeter'})
        self.assertNotIn((house, 'perimeter'), seg)


class TestClassDefaultBoundaryIsLabelled(unittest.TestCase):
    def test_garden_barriers_item_carries_class_layer_and_land_use(self):
        cells = [(x, 3) for x in range(10)]
        info = {1: {'street': 'Test Road', 'number': '', 'centre': (5.0, 12.0), 'foot': (5, 0), 'map_ref': None,
                    'land_use': {'class': 'commercial', 'reason': 'building_tag', 'ref': None}}}
        owner = {q: 1 for q in cells}
        seg = {(1, 'front'): cells}
        row = _row('commercial', boundary={**_row('commercial')['frontage']['boundary'],
                                           'front': {'type': 'fence', 'height': 2}})
        import land_use as lu
        idx = mock.Mock()
        idx.cells.return_value = {}
        with mock.patch.object(gs, 'plots_for', return_value=(owner, info, {}, idx)), \
                mock.patch.object(gs, 'segments', return_value=seg), \
                mock.patch.object(gs, 'load_convention', return_value=None), \
                mock.patch.object(gs, 'hedge_share', return_value=0.0), \
                mock.patch.object(gs, 'straighten', side_effect=lambda c: c), \
                mock.patch.object(lu, 'defaults', return_value=row), \
                mock.patch('ground_sources.aerial_rgb', return_value={}), \
                mock.patch('ground_sources.cell_box', return_value=(0, 0, 9, 9)), \
                mock.patch.object(gs, 'tree_canopy_columns', return_value=set()):
            out = gs.garden_barriers((0, 0), rgb={})
        (item,) = out
        self.assertEqual(item['scope']['layer'], 'class')
        self.assertEqual(item['scope']['land_use'], 'commercial')
        self.assertEqual(item['provenance']['source'], 'inference')
        self.assertIn('commercial', item['reason'])
        self.assertTrue(set(map(tuple, item['cells'])).isdisjoint(gs.reserved_openings([])))
