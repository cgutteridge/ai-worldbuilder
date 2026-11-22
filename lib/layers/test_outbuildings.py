"""Garden outbuildings: evidence order, refusals and massing (#251). Synthetic inputs only."""
from __future__ import annotations

import unittest
from unittest import mock

import numpy as np

import decor_layer as dl
import decor_plan as dp
import outbuildings as ob
import provenance as prov
import street_furniture as sf

X0, Z0 = 0, 0                      # cell (0, 0) spans 0..127
GREY = (0.5, 0.5, 0.5)             # aerial_class: light (paving / roof)
GREEN = (0.1, 0.3, 0.1)            # aerial_class: shade (dark saturated green)
GROUND = 64


def rect(x0, z0, w, d):
    return {(x, z) for x in range(x0, x0 + w) for z in range(z0, z0 + d)}


def rise_grid(blobs, size=140):
    """nDSM grid (metres) with ``{(x, z): rise}`` set."""
    grid = np.zeros((size, size))
    for (x, z), v in blobs.items():
        grid[x - X0, z - Z0] = v
    return {'x0': X0, 'z0': Z0, 'rise': grid}


def plan_for(cell=(0, 0), *, footprints=(), rise=None, rgb=None, footprint=None, canopy=None,
             plot_owner=None, designer=(), openings=None, water=None):
    """Decor plan with every source injected (no map, aerial or lidar read)."""
    return dp.build_decor_plan(
        cell, map_barriers={'hedge': set(), 'wall': set(), 'fence': set()}, map_furniture=[],
        designer_items=designer, photo_observations=[], garden={}, rgb=rgb or {},
        canopy=canopy or set(), footprint=footprint or set(),
        outbuilding_footprints=footprints, lidar_rise=rise, plot_owner=plot_owner,
        openings=openings, water=water)


def only(plan, role='outbuilding'):
    return [i for i in plan['items'] if i.get('role') == role]


def lidar_hm(ground=GROUND, size=140):
    return {'x0': X0, 'z0': Z0, 'terrain': np.full((size, size), ground), 'h': np.full((size, size), ground)}


def live_world(top=GROUND, extra=None):
    """Live blocks: grass up to ``top``, air above, plus ``extra`` ``{(x, y, z): block}``."""
    extra = extra or {}
    world = mock.Mock()
    world.block = lambda x, y, z: extra.get((x, y, z), 'minecraft:grass_block' if y <= top else 'minecraft:air')
    return world


def place(items, *, keep_clear=(), houses=(), way=(), lidar=None, cell=(0, 0), surface=GROUND, extra=None, owned=()):
    """``surface`` is the top of the live ground; ground height itself comes from the lidar terrain."""
    return dl.place_outbuildings(
        cell, items, live_world(surface, extra), way=set(way), houses=set(houses), keep_clear=set(keep_clear),
        owned=set(owned), masked=set(), lidar_hm=lidar or lidar_hm())


def blob_plan(cells, rise_value=2.6, **kw):
    cells = set(cells)
    rgb = kw.pop('rgb', None)
    if rgb is None:
        rgb = {q: GREY for q in cells}
    return plan_for(rise=rise_grid({q: rise_value for q in cells}), rgb=rgb, **kw)


class TestMappedOutbuilding(unittest.TestCase):
    def test_mapped_shed_is_a_high_confidence_item(self):
        cells = sorted(rect(20, 20, 3, 2))
        plan = plan_for(footprints=[{'ref': '77', 'building': 'shed', 'cells': cells}])
        (item,) = only(plan)
        self.assertEqual(item['kind'], 'outbuilding')
        self.assertEqual(item['type'], 'shed')
        self.assertEqual(item['provenance']['source'], 'vector_map')
        self.assertEqual(item['provenance']['confidence'], 'high')
        self.assertFalse(prov.is_unresolved(item['provenance']))
        self.assertEqual(sorted(map(tuple, item['cells'])), cells)

    def test_mapped_garage_tags_are_garages(self):
        for tag in ('garage', 'garages', 'carport'):
            self.assertEqual(ob.mapped_type(tag), 'garage')
        for tag in ('shed', 'hut', 'roof', 'greenhouse'):
            self.assertEqual(ob.mapped_type(tag), 'shed')

    def test_mapped_footprints_read_from_an_index(self):
        idx = mock.Mock()
        idx.cells.return_value = {(5, 5): 0, (6, 5): 0, (50, 50): 1, (9, 9): 2}
        idx.buildings = [([], {'building': 'shed', 'osm_id': 'a'}), ([], {'building': 'house'}),
                         ([], {'building': 'garage', 'osm_id': 'c'})]
        rows = ob.mapped_outbuilding_footprints(idx, (0, 0, 20, 20))
        self.assertEqual([(r['ref'], r['building'], r['cells']) for r in rows],
                         [('a', 'shed', [(5, 5), (6, 5)]), ('c', 'garage', [(9, 9)])])

    def test_mapped_beats_an_overlapping_lidar_blob(self):
        cells = rect(20, 20, 3, 3)
        plan = plan_for(footprints=[{'ref': '1', 'building': 'shed', 'cells': sorted(cells)}],
                        rise=rise_grid({q: 2.5 for q in cells}), rgb={q: GREY for q in cells})
        # the footprint is also a building column here, so the blob is excluded; either way one item
        self.assertEqual([i['provenance']['source'] for i in only(plan)], ['vector_map'])


class TestLidarBlob(unittest.TestCase):
    def test_grey_blob_is_a_medium_confidence_outbuilding(self):
        plan = blob_plan(rect(30, 30, 3, 3))
        (item,) = only(plan)
        self.assertEqual(item['provenance']['source'], 'lidar')
        self.assertEqual(item['provenance']['confidence'], 'medium')
        self.assertFalse(prov.is_unresolved(item['provenance']))

    def test_blob_that_reads_green_is_rejected(self):
        cells = rect(30, 30, 3, 3)
        plan = blob_plan(cells, rgb={q: GREEN for q in cells})
        (item,) = only(plan)
        self.assertTrue(prov.is_unresolved(item['provenance']))
        self.assertIn('green', item['provenance']['unresolved_reason'])
        placed, report, unplaced = place([i for i in plan['items']])
        self.assertEqual((placed, report, unplaced), ({}, [], []))      # never reaches the placer as a build
        self.assertEqual(len(dp.unresolved_items(plan)), 1)             # but it is listed, not silent

    def test_blob_outside_the_height_band_is_ignored(self):
        for v in (1.0, 1.5, 5.0):
            self.assertEqual(only(blob_plan(rect(30, 30, 3, 3), rise_value=v)), [], v)

    def test_small_lump_and_thin_line(self):
        self.assertEqual(only(blob_plan(rect(30, 30, 2, 2))), [])       # 4 columns: bin or noise
        (line,) = only(blob_plan(rect(30, 30, 8, 1)))                   # 8 columns, 1 wide: fence
        self.assertIn('line', line['provenance']['unresolved_reason'])

    def test_blob_in_canopy_or_beside_a_building_is_ignored(self):
        cells = rect(30, 30, 3, 3)
        self.assertEqual(only(blob_plan(cells, canopy=set(cells))), [])
        # a footprint beside the blob takes its one-column ring out of the evidence
        (trimmed,) = only(blob_plan(cells, footprint={(29, 31)}))
        self.assertNotIn([30, 31], trimmed['evidence_cells'])
        self.assertEqual(len(trimmed['evidence_cells']), 6)
        # a blob swallowed by the ring is gone
        self.assertEqual(only(blob_plan(cells, footprint={(29, 31), (33, 31)})), [])
        # a footprint two columns off leaves the blob whole
        (whole,) = only(blob_plan(cells, footprint={(28, 31)}))
        self.assertEqual(len(whole['evidence_cells']), 9)

    def test_blob_on_an_edge_of_the_cell_is_not_planned_by_the_neighbour(self):
        self.assertEqual(only(blob_plan(rect(130, 30, 3, 3), cell=(0, 0))), [])

    def test_blob_on_mapped_water_is_not_proposed(self):
        # a garage-sized lump in a river (#324): not a candidate, so not unresolved either
        cells = rect(30, 30, 4, 4)
        self.assertEqual(len(only(blob_plan(cells))), 1)                # control: same blob on land
        self.assertEqual(only(blob_plan(cells, water=set(cells))), [])
        self.assertEqual(dp.unresolved_items(blob_plan(cells, water=set(cells))), [])
        # water over part of a blob takes only those columns out of the evidence
        (trimmed,) = only(blob_plan(cells, water={(30, 30), (31, 30)}))
        self.assertNotIn([30, 30], trimmed['evidence_cells'])
        self.assertEqual(len(trimmed['evidence_cells']), 14)

    def test_no_lidar_no_blobs(self):
        self.assertEqual(only(plan_for(rise=None)), [])


class TestDesignerTrace(unittest.TestCase):
    def test_trace_with_evidence_is_medium(self):
        cells = sorted(rect(40, 40, 3, 3))
        plan = plan_for(designer=[{'kind': 'shed', 'cells': [list(c) for c in cells],
                                   'evidence_ids': ['aerial-1'], 'source': 'designer'}])
        (item,) = only(plan)
        self.assertEqual(item['provenance']['confidence'], 'medium')
        self.assertEqual(item['provenance']['evidence_ids'], ['aerial-1'])
        self.assertFalse(prov.is_unresolved(item['provenance']))

    def test_trace_without_evidence_id_is_listed_unresolved(self):
        plan = plan_for(designer=[{'kind': 'garage', 'cells': [[40, 40], [41, 40], [40, 41], [41, 41]]}])
        (item,) = only(plan)
        self.assertIn('evidence id', item['provenance']['unresolved_reason'])

    def test_designer_file_rows_carry_cells_and_evidence(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'decor.json'
            path.write_text(json.dumps({'0,0': {'items': [
                {'kind': 'shed', 'cells': [[1, 2], [2, 2]], 'evidence_ids': ['e1'], 'note': 'aerial trace'},
                {'kind': 'garage', 'cells': [['x', 1]]}]}}))
            rows = sf.designer_items((0, 0), path)
        self.assertEqual(rows, [{'kind': 'shed', 'cells': [[1, 2], [2, 2]], 'source': 'designer',
                                 'evidence_ids': ['e1'], 'note': 'aerial trace'}])

    def test_trace_over_an_accepted_blob_is_one_item(self):
        cells = rect(30, 30, 3, 3)
        plan = blob_plan(cells, designer=[{'kind': 'shed', 'cells': [list(c) for c in sorted(cells)],
                                           'evidence_ids': ['e']}])
        self.assertEqual([i['provenance']['source'] for i in only(plan)], ['lidar'])


class TestRefusals(unittest.TestCase):
    def setUp(self):
        self.cells = rect(30, 30, 3, 3)
        self.plan = blob_plan(self.cells)
        self.items = only(self.plan)

    def reasons(self, unplaced):
        return [u['provenance']['unresolved_reason'] for u in unplaced]

    def test_clear_blob_is_placed(self):
        found, report, unplaced = place(self.items)
        self.assertTrue(found)
        self.assertEqual((len(report), unplaced), (1, []))
        self.assertEqual(report[0]['source'], 'lidar')

    def test_blob_on_a_route_is_unplaced(self):
        found, _report, unplaced = place(self.items, keep_clear={(31, 31)})
        self.assertEqual(found, {})
        self.assertEqual(self.reasons(unplaced), ['on frontage route, clearance or entrance opening'])

    def test_blob_on_a_mapped_path_house_or_outside_cell(self):
        self.assertEqual(self.reasons(place(self.items, way={(30, 30)})[2]), ['on path/road/water column'])
        self.assertEqual(self.reasons(place(self.items, houses={(32, 32)})[2]), ['on a house footprint'])
        self.assertEqual(self.reasons(place(self.items, cell=(1, 0))[2]), ['crosses the cell edge'])

    def test_blob_across_a_plot_line_is_unplaced(self):
        owner = {q: (1 if q[0] < 31 else 2 if q[0] < 32 else 3) for q in self.cells}      # three plots, a third each
        plan = blob_plan(self.cells, plot_owner=owner)
        (item,) = only(plan)
        self.assertEqual(item['plot_ids'], [1, 2, 3])
        found, _report, unplaced = place([item])
        self.assertEqual(found, {})
        self.assertEqual(self.reasons(unplaced), ['across a plot line'])

    def test_footprint_mostly_in_one_plot_is_tolerated(self):
        """Plot lines are Voronoi guesses: half or more in one plot builds, an even three-way split does not."""
        ids = sorted(self.cells)
        mostly = {q: (2 if q in ids[:4] else 1) for q in self.cells}       # 5 of 9 in plot 1
        (item,) = only(blob_plan(self.cells, plot_owner=mostly))
        self.assertEqual((item['plot_ids'], item['plot_share']), ([1, 2], 0.556))
        self.assertTrue(place([item])[0])
        three = {q: (i // 3) for i, q in enumerate(ids)}                   # 3 / 3 / 3
        (item,) = only(blob_plan(self.cells, plot_owner=three))
        self.assertEqual(self.reasons(place([item])[2]), ['across a plot line'])

    def test_blob_in_one_plot_is_fine(self):
        plan = blob_plan(self.cells, plot_owner={q: 7 for q in self.cells})
        self.assertEqual(only(plan)[0]['plot_ids'], [7])
        self.assertTrue(place(only(plan))[0])

    def test_blob_on_the_carriageway_is_unplaced(self):
        with mock.patch('decor_layer.column_ground_kind', return_value='tarmac'):
            world = mock.Mock()
            world.block = lambda x, y, z: 'minecraft:air'
            _f, _r, unplaced = dl.place_outbuildings(
                (0, 0), self.items, world, way=set(), houses=set(), keep_clear=set(), owned=set(),
                masked=set(), lidar_hm=lidar_hm())
        self.assertEqual(self.reasons(unplaced), ['on carriageway'])

    def test_blocked_or_ungrounded_is_unplaced_whole(self):
        f, _r, unplaced = place(self.items, extra={(30, GROUND + 1, z): 'minecraft:stone' for z in range(28, 36)})
        self.assertEqual((f, self.reasons(unplaced)), ({}, ['blocked by live or owned block']))
        self.assertEqual(self.reasons(place(self.items, lidar=lidar_hm(ground=0))[2]), ['no lidar ground'])
        self.assertEqual(self.reasons(place(self.items, surface=GROUND - 1)[2]), ['no ground at terrain'])

    def test_a_roof_over_the_ground_does_not_veto_the_shed(self):
        """#301: the shed stands from lidar terrain; a feature's roof high over its column is no reason to skip it."""
        roof = {(x, GROUND + 12, z): 'minecraft:red_concrete' for x in range(28, 36) for z in range(28, 36)}
        found, _report, unplaced = place(self.items, extra=roof, owned=set(roof))
        self.assertEqual(unplaced, [])
        self.assertTrue(found)
        self.assertTrue(all(p[1] < GROUND + 12 for p in found))
        self.assertTrue(not set(found) & set(roof))

    def test_a_feature_block_in_the_shed_volume_is_feature_above_terrain(self):
        roof = {(30, GROUND + 1, 30): 'minecraft:red_concrete'}
        found, _report, unplaced = place(self.items, extra=roof, owned=set(roof))
        self.assertEqual((found, self.reasons(unplaced)), ({}, ['feature above terrain']))

    def test_ground_block_owned_by_a_feature_is_feature_above_terrain(self):
        found, _report, unplaced = place(self.items, owned={(31, GROUND, 31)})
        self.assertEqual((found, self.reasons(unplaced)), ({}, ['feature above terrain']))


class TestReviewFixes(unittest.TestCase):
    def test_mapped_shed_across_the_cell_edge_keeps_its_whole_footprint_and_is_refused(self):
        idx = mock.Mock()
        idx.cells.return_value = {(x, z): 0 for x in range(125, 131) for z in (5, 6)}
        idx.buildings = [([], {'building': 'shed', 'osm_id': 's'})]
        (row,) = ob.mapped_outbuilding_footprints(idx, (0, 0, 127, 127))
        self.assertEqual(len(row['cells']), 12)
        plan = plan_for(footprints=[row])
        _f, _r, unplaced = place(only(plan))
        self.assertEqual([u['provenance']['unresolved_reason'] for u in unplaced], ['crosses the cell edge'])

    def test_blob_with_no_aerial_colour_is_listed_not_accepted(self):
        (item,) = only(plan_for(rise=rise_grid({q: 2.6 for q in rect(30, 30, 3, 3)}), rgb={}))
        self.assertIn('no aerial', item['provenance']['unresolved_reason'])

    def test_walls_never_float_over_a_lower_live_surface(self):
        item = only(blob_plan(rect(30, 30, 3, 3)))[0]
        _f, _r, unplaced = place([item], surface=GROUND - 2)      # live ground below the lidar terrain
        self.assertEqual([u['provenance']['unresolved_reason'] for u in unplaced], ['no ground at terrain'])

    def test_plan_cell_sets_footprint_override_keeps_roof_slabs_and_clears_the_interior(self):
        """Unmapped outbuildings need ``outside_footprint_ok`` (osm_footprints.guard) and half-height roof slabs."""
        import types
        cells = rect(30, 30, 3, 3)
        decor = blob_plan(cells, rise_value=2.6)
        decor['items'].append({'kind': 'bench', 'role': 'furniture', 'cell': [31, 31], 'id': 'bench-31-31',
                               'provenance': {'source': 'vector_map', 'confidence': 'high',
                                              'evidence_ids': [], 'unresolved_reason': ''}})
        idx = mock.Mock()
        idx.cells.return_value = {}
        idx.buildings = []
        idx.areas = []
        vmap = types.SimpleNamespace(road=set(), feat=set(), water=set(), water_line=set(), hedge=set(),
                                     wall=set(), fence=set(), furniture=[], marks=set(),
                                     road_lines=[], path_lines=[])
        plan = types.SimpleNamespace(cells={}, keep_slab_cells=set(),
                                     set=lambda x, y, z, b: plan.cells.__setitem__((x, y, z), b))
        world = live_world()
        with mock.patch('decor_layer.neighbour_owned', return_value=set()), \
                mock.patch('decor_layer.ignored_builds', return_value=()), \
                mock.patch('decor_layer.load_masked_positions', return_value={}), \
                mock.patch('decor_layer.snapshot_decor_columns', side_effect=lambda w, c: w), \
                mock.patch('osm_footprints.OSMIndex', return_value=idx), \
                mock.patch('ground_sources.OSMGround', return_value=vmap), \
                mock.patch('lidar_heightmap.reference_world') as ref, \
                mock.patch('decor_layer.ob.lidar_rise', return_value=None), \
                mock.patch('decor_layer.dplan.build_decor_plan', return_value=decor), \
                mock.patch('surface_probe.surface_y', side_effect=lambda w, x, z: (GROUND, 'grass_block')), \
                mock.patch('decor_layer.on_carriageway', return_value=False), \
                mock.patch('decor_layer.column_ground_kind', return_value='grass'):
            ref.return_value.heightmap.return_value = lidar_hm()
            placed, stats = dl.plan_cell((0, 0), plan, world)
        self.assertEqual(stats['outbuildings'], 1)
        self.assertIn('outbuilding-lidar-30-30', plan.outside_footprint_ok)
        self.assertEqual(sum('stairs' in b for b in plan.cells.values()), 6)         # a gable: two stair rows
        self.assertEqual(plan.keep_slab_cells, set())
        self.assertFalse(any(p[:3:2] == (31, 31) and 'bench' in b for p, b in plan.cells.items()))
        self.assertEqual([u['plan_id'] for u in stats['unplaced_items']], ['bench-31-31'])


class TestMassing(unittest.TestCase):
    def test_shed_defaults_and_clamps_to_ndsm(self):
        cells = sorted(rect(0, 0, 3, 3))
        self.assertEqual(ob.massing('shed', cells, None)['walls'], 2)
        self.assertEqual(ob.massing('shed', cells, 9)['total'], 3)
        low = ob.massing('shed', cells, 2)
        self.assertEqual((low['walls'], low['total']), (1, 2))

    def test_garage_is_taller_and_pitched(self):
        cells = sorted(rect(0, 0, 5, 4))
        m = ob.massing('garage', cells, 5)
        self.assertEqual((m['walls'], m['total'], m['roof'], m['size']), (3, 4, 'pitched', [5, 4]))
        self.assertEqual(ob.massing('garage', cells, 3)['walls'], 2)         # clamped to lidar
        self.assertEqual(ob.massing('shed', sorted(rect(0, 0, 3, 2)), None)['roof'], 'pitched')
        self.assertEqual(ob.massing('shed', sorted(rect(0, 0, 3, 1)), None)['roof'], 'flat')

    def test_blob_size_and_height_come_from_the_evidence(self):
        plan = blob_plan(rect(30, 30, 5, 4), rise_value=3.2)      # 20 columns, 3.2 m: a garage, ndsm 4
        (item,) = only(plan)
        self.assertEqual((item['type'], item['size'], item['walls'], item['height'], item['lidar_height']),
                         ('garage', [5, 4], 3, 4, 4))
        small = only(blob_plan(rect(30, 30, 3, 3), rise_value=2.3))[0]       # 9 columns, 2.3 m: ceil 3
        self.assertEqual((small['type'], small['size'], small['walls'], small['height']), ('shed', [3, 3], 2, 3))
        squat = only(blob_plan(rect(30, 30, 3, 3), rise_value=1.7))[0]       # ceil 2: walls 1
        self.assertEqual((squat['walls'], squat['height'], squat['door']), (1, 2, None))

    def test_ragged_blob_squares_to_a_rectangle_not_its_bounding_box(self):
        blob = rect(30, 30, 4, 3) | {(33, 33)}                    # one stray column below the 4 x 3
        (item,) = only(blob_plan(blob))
        self.assertEqual(item['size'], [4, 3])

    def test_voxels_stand_on_lidar_ground_under_the_clamped_top(self):
        plan = blob_plan(rect(30, 30, 3, 3), rise_value=2.3)
        (item,) = only(plan)
        found, report, _u = place([item])
        ys = sorted({p[1] for p in found})
        self.assertEqual((ys[0], ys[-1]), (GROUND + 1, GROUND + item['height']))   # top clamped to nDSM
        roof = {p: b for p, b in found.items() if p[1] == GROUND + item['height']}
        self.assertEqual(len(roof), 9)                                              # 3 across: stairs, ridge, stairs
        self.assertEqual(sorted({b.split('[')[0] for b in roof.values()}), ['spruce_planks', 'spruce_stairs'])
        walls = {p for p, b in found.items() if b == 'spruce_planks' and p[1] < GROUND + item['height']}
        self.assertEqual({(x, z) for x, _y, z in walls}, rect(30, 30, 3, 3) - {(31, 31)})   # hollow
        self.assertEqual(report[0]['blocks'], len(found))

    def test_garage_is_brick_with_stair_roof_and_ground_follows_the_slope(self):
        cells = rect(30, 30, 5, 4)
        item = only(blob_plan(cells, rise_value=3.5))[0]
        hm = lidar_hm()
        hm['terrain'][30, 30] = GROUND - 1                       # one corner sits a block lower
        lowered = {(30, GROUND, 30): 'minecraft:air'}              # the live ground there is lower too
        found, _r, unplaced = place([item], lidar=hm, extra=lowered)
        self.assertEqual(unplaced, [])
        self.assertEqual(found[(30, GROUND, 30)], 'bricks')        # wall runs down to the low ground
        self.assertTrue(all(y <= GROUND + 6 for _x, y, _z in found))

    def garage(self, w, d, door, facing, walls=3, kind='garage'):
        cells = [[x, z] for x in range(30, 30 + w) for z in range(30, 30 + d)]
        return {'type': kind, 'cells': cells, 'walls': walls, 'roof': 'pitched',
                'door': {'cell': list(door), 'facing': facing}}

    @staticmethod
    def tops(found):
        top = {}
        for (x, y, z), b in found.items():
            if b.split('[')[0] in ('stone_brick_stairs', 'stone_bricks', 'spruce_stairs', 'spruce_planks') \
                    and (x, z) not in top or top.get((x, z), (-1,))[0] < y:
                top[(x, z)] = (y, b)
        return top

    def test_pitched_roof_rises_from_both_eaves_to_a_ridge(self):
        """6 x 4: the ridge runs along x; across the 4 columns the stairs rise a block per row and meet back to back."""
        item = self.garage(6, 4, (30, 32), 'west')
        self.assertEqual(ob.pitched_roof(item, [tuple(c) for c in item['cells']]),
                         {'axis': 'x', 'across': 4, 'eave': 2})
        found = ob.voxels(item, {tuple(c): GROUND for c in item['cells']})
        top = self.tops(found)
        for x in range(30, 36):
            levels = [top[(x, z)][0] for z in range(30, 34)]
            self.assertEqual(levels, [GROUND + 3, GROUND + 4, GROUND + 4, GROUND + 3], x)      # eave, up, up, eave
            self.assertEqual([top[(x, z)][1].split('[')[1] for z in range(30, 34)],
                             ['facing=south,half=bottom]', 'facing=south,half=bottom]',     # uphill = toward the ridge
                              'facing=north,half=bottom]', 'facing=north,half=bottom]'])
        self.assertEqual(found[(35, GROUND + 3, 31)], 'bricks')           # gable end steps up into the roof
        self.assertNotIn((35, GROUND + 4, 31), [p for p in found if found[p] == 'bricks'])

    def test_odd_width_gets_a_flat_full_block_ridge(self):
        item = self.garage(6, 5, (30, 32), 'west')
        top = self.tops(ob.voxels(item, {tuple(c): GROUND for c in item['cells']}))
        levels = [top[(33, z)] for z in range(30, 35)]
        self.assertEqual([y for y, _b in levels], [GROUND + 3, GROUND + 4, GROUND + 4, GROUND + 4, GROUND + 3])
        self.assertEqual(levels[2][1], 'stone_bricks')                      # ridge: full block flush with the stair backs
        self.assertTrue(all('stairs' in levels[i][1] for i in (0, 1, 3, 4)))

    def test_ridge_axis_follows_the_long_side_or_the_door_on_a_square(self):
        tall = self.garage(4, 6, (30, 32), 'west')
        self.assertEqual(ob.pitched_roof(tall, [tuple(c) for c in tall['cells']])['axis'], 'z')
        top = self.tops(ob.voxels(tall, {tuple(c): GROUND for c in tall['cells']}))
        self.assertEqual([top[(x, 33)][0] for x in range(30, 34)], [GROUND + 3, GROUND + 4, GROUND + 4, GROUND + 3])
        for facing, axis in (('west', 'x'), ('east', 'x'), ('north', 'z'), ('south', 'z')):
            sq = self.garage(4, 4, (31, 31), facing)
            self.assertEqual(ob.pitched_roof(sq, [tuple(c) for c in sq['cells']])['axis'], axis, facing)

    def test_stairs_form_a_ridge_the_roof_lint_can_see(self):
        """Stairs face uphill: if they faced downhill the ridge would be a valley (roof_lint finds no ridge)."""
        import roof_lint
        import site_kit
        item = self.garage(6, 4, (30, 32), 'west')
        found = ob.voxels(item, {tuple(c): GROUND for c in item['cells']})
        top = roof_lint._tops({p: site_kit.full(b) for p, b in found.items()})
        self.assertEqual(sorted(roof_lint._ridges(top, 'z')), list(range(30, 36)))     # a ridge on every row along x

    def test_wide_footprints_stay_flat(self):
        self.assertEqual(ob.massing('garage', sorted(rect(0, 0, 8, 6)), 5)['roof'], 'pitched')
        self.assertEqual(ob.massing('garage', sorted(rect(0, 0, 8, 7)), 5)['roof'], 'flat')

    def test_eave_wall_drops_to_hold_the_ridge_but_keeps_the_door(self):
        wide = self.garage(8, 6, (30, 32), 'west', walls=3)                  # 6 across: ridge 3 above the eave layer
        self.assertEqual(ob.pitched_roof(wide, [tuple(c) for c in wide['cells']])['eave'], 2)
        shed = self.garage(3, 3, (30, 31), 'west', walls=2, kind='shed')
        self.assertEqual(ob.pitched_roof(shed, [tuple(c) for c in shed['cells']])['eave'], 2)
        low = self.garage(3, 3, (30, 31), 'west', walls=1, kind='shed')
        self.assertEqual(ob.pitched_roof(low, [tuple(c) for c in low['cells']])['eave'], 1)

    def test_pitched_roofs_are_framed_and_pass_the_roof_guards(self):
        import types
        import site_kit
        for w, d, door, facing, walls, kind in (
                (6, 4, (30, 32), 'west', 3, 'garage'),      # door in a gable end
                (6, 4, (32, 30), 'north', 3, 'garage'),     # door under the eave
                (3, 3, (30, 31), 'west', 2, 'shed'), (4, 3, (32, 33), 'south', 2, 'shed'),
                (6, 5, (35, 32), 'east', 3, 'garage')):
            item = self.garage(w, d, door, facing, walls, kind)
            found = ob.voxels(item, {tuple(c): GROUND for c in item['cells']})
            plan = types.SimpleNamespace(name='t', cells={p: site_kit.full(b) for p, b in found.items()})
            self.assertEqual(site_kit.door_frame_issues(plan, lambda x, y, z: None), [], (w, d, facing))
            site_kit.guard_roof_jogs(plan)                                          # raises SystemExit on a zig-zag

    def test_door_faces_the_house_and_is_two_blocks_in_the_wall(self):
        cells = rect(30, 30, 3, 3)
        item = only(blob_plan(cells, footprint={(20, 31)}))[0]    # house to the west
        self.assertEqual(item['door'], {'cell': [30, 31], 'facing': 'west'})
        found, _r, _u = place([item])
        self.assertIn('half=lower', found[(30, GROUND + 1, 31)])
        self.assertIn('half=upper', found[(30, GROUND + 2, 31)])
        self.assertIn('facing=west', found[(30, GROUND + 1, 31)])

    def test_door_is_framed_for_the_door_check(self):
        """site_kit refuses a door without a jamb each side and a lintel; sheds and garages pass it."""
        import types
        import site_kit
        for rise_value, w, d in ((2.6, 3, 3), (3.6, 5, 4), (2.6, 4, 3)):
            item = only(blob_plan(rect(30, 30, w, d), rise_value=rise_value, footprint={(20, 31)}))[0]
            found, _r, _u = place([item])
            plan = types.SimpleNamespace(cells={p: site_kit.full(b) for p, b in found.items()})
            self.assertTrue(item['door'])
            self.assertEqual(site_kit.door_frame_issues(plan, lambda x, y, z: None), [], (w, d))

    def test_door_moves_to_a_side_long_enough_for_jambs(self):
        # target to the west, but the west side is only 2 columns long: the door goes to a 5-column side
        door = ob.door_for(sorted(rect(30, 30, 5, 2)), [(10, 30)], 2)
        self.assertEqual(door['facing'], 'north')
        self.assertIsNone(ob.door_for(sorted(rect(30, 30, 2, 2)), [(10, 30)], 2))
        self.assertIsNone(ob.door_for(sorted(rect(30, 30, 3, 3)), [], 2))

    def test_evidence_too_small_once_squared_is_listed_unresolved(self):
        plan = plan_for(footprints=[{'ref': '9', 'building': 'shed', 'cells': [(5, 5), (5, 6)]}])   # 1 x 2
        (item,) = only(plan)
        self.assertIn('too small', item['provenance']['unresolved_reason'])
        self.assertEqual(place([item]), ({}, [], []))

    def test_door_faces_a_route_when_no_house_is_near(self):
        door = ob.door_for(sorted(rect(30, 30, 3, 3)), [(31, 50)], 2)
        self.assertEqual(door, {'cell': [31, 32], 'facing': 'south'})


class RoofCatalogueGoldenTest(unittest.TestCase):
    """#372: shed and garage roof blocks come from the roof catalogue and the output is unchanged."""

    GOLDEN = {
        ('shed', 'pitched', 6, 4): '3498a3ecf553',
        ('shed', 'pitched', 5, 5): 'a0a8d8dcaabd',
        ('shed', 'pitched', 5, 4): '39157e045788',
        ('shed', 'pitched', 4, 7): 'b97deaf6f9cd',
        ('shed', 'pitched', 7, 3): '5c042d3c4824',
        ('shed', 'flat', 6, 4): 'fb8175e3c252',
        ('shed', 'flat', 5, 5): 'ed2950bc691e',
        ('shed', 'flat', 5, 4): '483d3c751fd1',
        ('shed', 'flat', 4, 7): '2a17864ce13a',
        ('shed', 'flat', 7, 3): 'bf5651420a52',
        ('garage', 'pitched', 6, 4): 'ddd7704a13be',
        ('garage', 'pitched', 5, 5): '287382798845',
        ('garage', 'pitched', 5, 4): 'c0f2a9201fbd',
        ('garage', 'pitched', 4, 7): '2e1e9b2c409e',
        ('garage', 'pitched', 7, 3): '6373e5e355db',
        ('garage', 'flat', 6, 4): 'f337c95fe04e',
        ('garage', 'flat', 5, 5): 'a650c7fd2b27',
        ('garage', 'flat', 5, 4): '16fa1462ed4e',
        ('garage', 'flat', 4, 7): '17398688a274',
        ('garage', 'flat', 7, 3): '99cdb897cf5c',
    }

    def test_voxels_match_the_recorded_plans_from_before_the_catalogue_change(self):
        import hashlib
        for (kind, roof, w, d), want in self.GOLDEN.items():
            cells = [[x, z] for x in range(30, 30 + w) for z in range(30, 30 + d)]
            item = {'type': kind, 'cells': cells, 'walls': 3, 'roof': roof,
                    'door': {'cell': [30, 32], 'facing': 'west'}}
            found = ob.voxels(item, {tuple(c): GROUND for c in cells})
            got = hashlib.sha1(repr(sorted(found.items())).encode()).hexdigest()[:12]
            self.assertEqual(got, want, (kind, roof, w, d))

    def test_roof_family_blocks_are_the_catalogue_rows(self):
        from roof_material import family_variants
        self.assertEqual(family_variants(ob.ROOF_FAMILY['shed']),
                         {'full': 'spruce_planks', 'slab': 'spruce_slab', 'stairs': 'spruce_stairs'})
        self.assertEqual(family_variants(ob.ROOF_FAMILY['garage']),
                         {'full': 'stone_bricks', 'slab': 'stone_brick_slab', 'stairs': 'stone_brick_stairs'})


if __name__ == '__main__':
    unittest.main()


class TestNonHouseStrip(unittest.TestCase):
    """Outbuildings are not gated to house plots (#254): a yard with no house still places a shed."""

    def test_blob_and_mapped_shed_in_a_plot_with_no_house_still_place(self):
        yard = rect(30, 30, 3, 3)
        owner = {q: 'yard' for q in rect(25, 25, 14, 12)}               # one plot, no footprint inside it
        plan = blob_plan(yard, plot_owner=owner, footprint=set())
        (item,) = only(plan)
        self.assertEqual((item['plot_ids'], item['door']), (['yard'], None))   # no house or route: no door target
        found, report, unplaced = place([item])
        self.assertTrue(found)
        self.assertEqual((len(report), unplaced), (1, []))
        mapped = plan_for(footprints=[{'ref': '9', 'building': 'garage', 'cells': sorted(rect(20, 20, 3, 4))}],
                          plot_owner={q: 'yard' for q in rect(18, 18, 8, 8)})
        self.assertTrue(place(only(mapped))[0])
