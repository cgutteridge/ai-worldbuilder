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
             plot_owner=None, designer=(), openings=None):
    """Decor plan with every source injected (no map, aerial or lidar read)."""
    return dp.build_decor_plan(
        cell, map_barriers={'hedge': set(), 'wall': set(), 'fence': set()}, map_furniture=[],
        designer_items=designer, photo_observations=[], garden={}, rgb=rgb or {},
        canopy=canopy or set(), footprint=footprint or set(),
        outbuilding_footprints=footprints, lidar_rise=rise, plot_owner=plot_owner,
        openings=openings)


def only(plan, role='outbuilding'):
    return [i for i in plan['items'] if i.get('role') == role]


def lidar_hm(ground=GROUND, size=140):
    return {'x0': X0, 'z0': Z0, 'terrain': np.full((size, size), ground), 'h': np.full((size, size), ground)}


def place(items, *, keep_clear=(), houses=(), way=(), lidar=None, cell=(0, 0), surface=GROUND):
    world = mock.Mock()
    world.block = lambda x, y, z: 'minecraft:air'
    with mock.patch('surface_probe.surface_y', side_effect=lambda w, x, z: (surface, 'grass_block')):
        return dl.place_outbuildings(
            cell, items, world, way=set(way), houses=set(houses), keep_clear=set(keep_clear),
            owned=set(), masked=set(), lidar_hm=lidar or lidar_hm())


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
        owner = {q: (1 if q[0] < 32 else 2) for q in self.cells}
        plan = blob_plan(self.cells, plot_owner=owner)
        (item,) = only(plan)
        self.assertEqual(item['plot_ids'], [1, 2])
        found, _report, unplaced = place([item])
        self.assertEqual(found, {})
        self.assertEqual(self.reasons(unplaced), ['across a plot line'])

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
        world = mock.Mock()
        world.block = lambda x, y, z: 'minecraft:stone' if (y, x) == (GROUND + 1, 30) else 'minecraft:air'
        with mock.patch('surface_probe.surface_y', side_effect=lambda w, x, z: (GROUND, 'grass_block')):
            f, _r, unplaced = dl.place_outbuildings(
                (0, 0), self.items, world, way=set(), houses=set(), keep_clear=set(), owned=set(),
                masked=set(), lidar_hm=lidar_hm())
        self.assertEqual((f, self.reasons(unplaced)), ({}, ['blocked by live or owned block']))
        self.assertEqual(self.reasons(place(self.items, lidar=lidar_hm(ground=0))[2]), ['no lidar ground'])
        self.assertEqual(self.reasons(place(self.items, surface=GROUND + 4)[2]), ['above lidar ground'])


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
        _f, _r, unplaced = place([item], surface=GROUND - 2)
        self.assertEqual([u['provenance']['unresolved_reason'] for u in unplaced], ['below lidar ground'])

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
        vmap = types.SimpleNamespace(road=set(), feat=set(), water=set(), water_line=set(), hedge=set(),
                                     wall=set(), fence=set(), furniture=[])
        plan = types.SimpleNamespace(cells={}, keep_slab_cells=set(),
                                     set=lambda x, y, z, b: plan.cells.__setitem__((x, y, z), b))
        world = mock.Mock()
        world.block = lambda x, y, z: 'minecraft:air'
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
        slabs = {p for p, b in plan.cells.items() if 'slab' in b}
        self.assertEqual(len(slabs), 9)
        self.assertEqual(plan.keep_slab_cells, slabs)
        self.assertFalse(any(p[:3:2] == (31, 31) and 'bench' in b for p, b in plan.cells.items()))
        self.assertEqual([u['plan_id'] for u in stats['unplaced_items']], ['bench-31-31'])


class TestMassing(unittest.TestCase):
    def test_shed_defaults_and_clamps_to_ndsm(self):
        cells = sorted(rect(0, 0, 3, 3))
        self.assertEqual(ob.massing('shed', cells, None)['walls'], 2)
        self.assertEqual(ob.massing('shed', cells, 9)['total'], 3)
        low = ob.massing('shed', cells, 2)
        self.assertEqual((low['walls'], low['total']), (1, 2))

    def test_garage_is_taller_with_a_mono_pitch(self):
        cells = sorted(rect(0, 0, 5, 4))
        m = ob.massing('garage', cells, 5)
        self.assertEqual((m['walls'], m['total'], m['roof'], m['size']), (3, 4, 'mono', [5, 4]))
        self.assertEqual(ob.massing('garage', cells, 3)['walls'], 2)         # clamped to lidar
        self.assertEqual(ob.massing('garage', sorted(rect(0, 0, 5, 2)), 5)['roof'], 'flat')

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
        self.assertEqual(len(roof), 9)
        self.assertTrue(all(b.endswith('[type=bottom]') for b in roof.values()))   # never a top slab over air
        walls = {p for p, b in found.items() if b == 'spruce_planks'}
        self.assertEqual({(x, z) for x, _y, z in walls}, rect(30, 30, 3, 3) - {(31, 31)})   # hollow
        self.assertEqual(report[0]['blocks'], len(found))

    def test_garage_is_brick_with_stair_roof_and_ground_follows_the_slope(self):
        cells = rect(30, 30, 5, 4)
        item = only(blob_plan(cells, rise_value=3.5))[0]
        hm = lidar_hm()
        hm['terrain'][30, 30] = GROUND - 1                       # one corner sits a block lower
        found, _r, unplaced = place([item], lidar=hm)
        self.assertEqual(unplaced, [])
        self.assertEqual(found[(30, GROUND, 30)], 'bricks')        # wall runs down to the low ground
        self.assertTrue(any(b.startswith('stone_brick_stairs[') for b in found.values()))
        self.assertTrue(all(y <= GROUND + 4 for _x, y, _z in found))

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


if __name__ == '__main__':
    unittest.main()
