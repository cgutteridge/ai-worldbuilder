"""Unit tests for the trees pass dense-cover fill (no Minecraft, no world writes)."""
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import trees_layer as tl

CELL = (900, 900)          # no ground-cells file exists for it, so ground_cols is None


def _empty_osm_ground(_cell=None):
    """Stand-in OSMGround with no mapped trees (keeps unit tests off the live cache)."""
    return types.SimpleNamespace(trees=[], tree_rows=[])


def cover(frac_left=1.0, frac_right=0.0):
    """128x128 cover grid: left half one value, right half another."""
    g = np.zeros((128, 128))
    g[:64], g[64:] = frac_left, frac_right
    return g


class FakeWorld:
    def block(self, x, y, z):
        return 'minecraft:air'


class TestCoverPixels(unittest.TestCase):
    def test_wood_is_cover_roof_and_lawn_are_not(self):
        from PIL import Image
        img = np.zeros((40, 120, 3), np.uint8)
        img[:, :40] = (94, 83, 58)        # brown-olive shaded wood (g < r) that the crown test rejects
        img[:, 40:80] = (150, 70, 50)     # red roof
        img[:, 80:] = (185, 175, 170)     # bright, unsaturated paving
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'm.png'
            Image.fromarray(img).save(p)
            m = tl.cover_pixels(p)
        self.assertGreater(m[:, :40][10:30, 10:30].mean(), 0.95)
        self.assertEqual(m[:, 40:][10:30, 10:100].mean(), 0)


class TestFillDense(unittest.TestCase):
    def test_dense_patch_gets_proportionally_many_crowns(self):
        n_full = len(tl.fill_dense(cover(1.0, 0.0), []))
        n_mid = len(tl.fill_dense(cover(0.75, 0.0), []))
        area = 64 * 128
        self.assertGreater(n_full, 0.5 * area / tl.FILL_SPACING ** 2)     # about one per spacing² cell
        self.assertLess(n_mid, 0.75 * n_full)                              # fewer at lower cover
        self.assertGreater(n_mid, 0.2 * n_full)
        self.assertTrue(all(i < 70 for i, _j, _r in tl.fill_dense(cover(1.0, 0.0), [])))

    def test_sparse_patch_unchanged(self):
        self.assertEqual(tl.fill_dense(cover(tl.COVER_MIN - 0.05, 0.3), []), [])

    def test_existing_crowns_are_not_overlapped(self):
        crowns = [(i, j, 6.0) for i in range(5, 128, 10) for j in range(5, 128, 10)]
        for i, j, _r in tl.fill_dense(cover(1.0, 1.0), crowns):
            self.assertTrue(all((i - ci) ** 2 + (j - cj) ** 2 > (cr * 0.75) ** 2 for ci, cj, cr in crowns))

    def test_deterministic(self):
        self.assertEqual(tl.fill_dense(cover(), [], seed=3), tl.fill_dense(cover(), [], seed=3))


class TestLidarFill(unittest.TestCase):
    def test_dark_tall_blob_gets_crowns_without_aerial_cover(self):
        ndsm = np.zeros((128, 128))
        ndsm[20:45, 20:45] = 8.
        crowns = tl.lidar_fill(ndsm, [], np.zeros_like(ndsm), seed=7)
        self.assertGreater(len(crowns), 5)
        self.assertTrue(all(ndsm[i, j] >= tl.MIN_NDSM for i, j, _r in crowns))
        self.assertTrue(all(r in tl.FILL_RADII for _i, _j, r in crowns))

    def test_existing_crown_covers_the_blob(self):
        ii, jj = np.indices((128, 128))
        ndsm = np.where((ii - 30) ** 2 + (jj - 30) ** 2 <= 7 ** 2, 8., 0.)
        self.assertEqual(tl.lidar_fill(ndsm, [(30, 30, 7.)], np.ones_like(ndsm)), [])

    def test_low_and_unknown_lidar_overrides_high_aerial_cover(self):
        for height in (tl.MIN_NDSM - .01, np.nan, np.inf):
            with self.subTest(height=height):
                ndsm = np.full((128, 128), height)
                self.assertEqual(tl.fill_dense(cover(1., 1.), [], ndsm=ndsm), [])
                self.assertEqual(tl.lidar_fill(ndsm, [], cover(1., 1.)), [])

    def test_deterministic_separated_crowns_and_unmodified_inputs(self):
        ndsm = np.full((128, 128), tl.MIN_NDSM)
        aerial = cover(1., .2)
        existing = [(30, 30, 7.)]
        crowns = tl.lidar_fill(ndsm, existing, aerial, seed=3)
        self.assertEqual(crowns, tl.lidar_fill(ndsm, existing, aerial, seed=3))
        self.assertNotEqual(crowns, tl.lidar_fill(ndsm, existing, aerial, seed=4))
        for index, (i, j, r) in enumerate(crowns):
            for ci, cj, cr in existing + crowns[:index]:
                distance2 = (i - ci) ** 2 + (j - cj) ** 2
                self.assertGreater(distance2, max(r, cr) ** 2)
                self.assertGreater(distance2, tl.LIDAR_FILL_SPACING ** 2)
        np.testing.assert_array_equal(ndsm, np.full((128, 128), tl.MIN_NDSM))
        np.testing.assert_array_equal(aerial, cover(1., .2))
        self.assertEqual(existing, [(30, 30, 7.)])

    def planning_inputs(self, ndsm, aerial, *, mapped=()):
        idx = types.SimpleNamespace(cells=lambda: set(), areas=[])
        ground = types.SimpleNamespace(trees=list(mapped), tree_rows=[])
        with mock.patch('osm_footprints.OSMIndex', return_value=idx), \
                mock.patch.object(tl, 'canopy_grid', return_value=np.zeros((128, 128))), \
                mock.patch.object(tl, 'cover_grid', return_value=aerial), \
                mock.patch.object(tl, 'extra_crowns', return_value=[]), \
                mock.patch.object(tl, 'google_crowns', return_value=None), \
                mock.patch('tree_lines.standing_height', side_effect=lambda c, margin=0:
                                  (None, 'none') if ndsm is None else (np.pad(ndsm, margin), 'first-return')), \
                mock.patch.object(tl, 'cell_terrain', return_value=None), \
                mock.patch.object(tl, 'neighbour_owned', return_value=set()), \
                mock.patch.object(tl, 'ignored_builds', return_value=set()), \
                mock.patch.object(tl, 'load_masked_positions', return_value={}), \
                mock.patch('cell_registry.view_registry', return_value=types.SimpleNamespace(recorded=lambda n: None)):
            return tl.planning_inputs((0, 0), osm_ground=ground)

    def test_missing_lidar_preserves_original_aerial_output_in_planning(self):
        aerial = np.zeros((128, 128))
        aerial[:12, :12] = 1.
        # Recorded from the pre-#349 fill_dense, rather than another call to the new helper.
        expected = [(2, 2, 3.0), (2, 6, 3.0), (3, 11, 3.5), (8, 3, 2.5),
                    (8, 8, 3.0), (7, 11, 2.5), (11, 1, 2.5)]
        self.assertEqual(tl.fill_dense(aerial, [], seed=3, ndsm=None), expected)
        self.assertEqual(tl.lidar_fill(None, [], aerial), [])
        inputs = self.planning_inputs(None, aerial)
        self.assertEqual(inputs.crowns, tl.fill_dense(aerial, [], seed=0))
        self.assertEqual(inputs.source_counts, {'detected': 0, 'aerial_fill': len(inputs.crowns),
                                                'lidar_fill': 0, 'mapped': 0})

    def test_planning_uses_lidar_when_aerial_is_dark_and_mapped_crowns_win(self):
        ndsm = np.zeros((128, 128))
        ndsm[10, 10] = 8.
        aerial = np.zeros_like(ndsm)
        inputs = self.planning_inputs(ndsm, aerial)
        self.assertEqual(len(inputs.crowns), 1)
        self.assertEqual(inputs.crowns[0][:2], (10, 10))
        inputs = self.planning_inputs(ndsm, aerial, mapped=[(10, 10)])
        self.assertEqual(inputs.crowns, [(10, 10, tl.MAPPED_TREE_R)])
        self.assertEqual(inputs.crown_origins, [(10, 10)])
        self.assertEqual(inputs.source_counts, {'detected': 0, 'aerial_fill': 0, 'lidar_fill': 1, 'mapped': 1})
        with mock.patch.object(tl, 'warm_surface_probe'), \
                mock.patch.object(tl, 'surface_y', return_value=(60, 'grass_block')):
            _placed, report = tl.plan_cell((0, 0), types.SimpleNamespace(set=lambda *a: None), FakeWorld(), inputs)
        self.assertEqual(report['crown_sources'], inputs.source_counts)

    def test_planning_does_not_add_high_cover_crowns_on_low_lidar(self):
        inputs = self.planning_inputs(np.zeros((128, 128)), cover(1., 1.))
        self.assertEqual(inputs.crowns, [])
        self.assertEqual(inputs.source_counts, {'detected': 0, 'aerial_fill': 0, 'lidar_fill': 0, 'mapped': 0})


class TestTreeSources(unittest.TestCase):
    """Mapped open pasture must not become woodland from aerial texture alone (#317)."""

    @staticmethod
    def polygon(x0, z0, x1, z1):
        return [[(x0, z0), (x1, z0), (x1, z1), (x0, z1), (x0, z0)]]

    def index(self, areas):
        return types.SimpleNamespace(
            cells=lambda: set(),
            areas=[(rings, dict(tags, osm_way_id=str(i))) for i, (rings, tags) in enumerate(areas)],
        )

    def test_meadow_rejects_texture_and_dense_fill_but_keeps_supported_crowns(self):
        idx = self.index([
            (self.polygon(0, 0, 128, 128), {'landuse': 'meadow'}),
            (self.polygon(20, 20, 30, 30), {'natural': 'wood'}),
            (self.polygon(60, 20, 70, 30), {'natural': 'scrub'}),
        ])
        crowns = [(10, 10, 3.), (30, 25, 3.), (31, 25, 3.), (32, 25, 3.),
                  (70, 25, 3.), (50, 50, 3.), (90, 90, 3.), (100, 100, 3.)]
        ndsm = np.zeros((128, 128))
        ndsm[52, 50] = 2.0
        ndsm[90, 90] = 1.99
        ndsm[100, 100] = np.nan
        rejected = tl.unsupported_tree_crowns((0, 0), crowns, idx, {(10, 10)}, ndsm)
        self.assertEqual({(x, z) for x, z, _reason in rejected},
                         {(32, 25), (90, 90), (100, 100)})

    def test_missing_lidar_and_polygon_holes_do_not_confirm_a_tree(self):
        wood = self.polygon(0, 0, 128, 128) + self.polygon(40, 40, 80, 80)
        idx = self.index([(wood, {'natural': 'wood'})])
        rejected = tl.unsupported_tree_crowns((0, 0), [(20, 20, 3.), (60, 60, 3.)], idx, set(), None)
        # The hole is unclassified, so no class policy applies there. Make its pasture explicit.
        self.assertEqual(rejected, [])
        idx.areas.append((self.polygon(40, 40, 80, 80), {'landuse': 'meadow', 'osm_way_id': 'meadow'}))
        rejected = tl.unsupported_tree_crowns((0, 0), [(20, 20, 3.), (60, 60, 3.)], idx, set(), None)
        self.assertEqual([(x, z) for x, z, _reason in rejected], [(60, 60)])

    def test_other_classes_and_unmapped_columns_keep_their_existing_sources(self):
        idx = self.index([(self.polygon(0, 0, 30, 30), {'landuse': 'residential'})])
        self.assertEqual(tl.unsupported_tree_crowns((0, 0), [(10, 10, 3.), (50, 50, 3.)], idx, set(), None), [])

    def test_class_table_controls_the_permitted_sources(self):
        idx = self.index([(self.polygon(0, 0, 128, 128), {'leisure': 'park'})])
        with mock.patch('land_use.tree_sources', side_effect=lambda cls: () if cls == 'park' else None):
            rejected = tl.unsupported_tree_crowns((0, 0), [(10, 10, 3.)], idx, {(10, 10)}, np.full((128, 128), 5.))
        self.assertEqual([(x, z) for x, z, _reason in rejected], [(10, 10)])
        with mock.patch('land_use.tree_sources', return_value=None):
            self.assertEqual(tl.unsupported_tree_crowns((0, 0), [(10, 10, 3.)], idx, set(), None), [])

    def test_wood_in_neighbouring_cell_supports_an_edge_crown(self):
        idx = self.index([
            (self.polygon(0, 0, 128, 128), {'landuse': 'farmland'}),
            (self.polygon(128, 10, 140, 20), {'landuse': 'forest'}),
        ])
        rejected = tl.unsupported_tree_crowns((0, 0), [(127, 15, 3.), (125, 15, 3.)], idx, set(), None)
        self.assertEqual([(x, z) for x, z, _reason in rejected], [(125, 15)])

    def test_lidar_across_each_cell_edge_and_corner_supports_a_crown(self):
        idx = self.index([(self.polygon(0, 0, 128, 128), {'landuse': 'meadow'})])
        cases = [((0, 50), (-2, 50)), ((127, 50), (129, 50)),
                 ((50, 0), (50, -2)), ((50, 127), (50, 129)),
                 ((0, 0), (-1, -1)), ((0, 127), (-1, 128)),
                 ((127, 0), (128, -1)), ((127, 127), (128, 128))]
        for crown, support in cases:
            with self.subTest(crown=crown, support=support):
                ndsm = np.zeros((132, 132))
                ndsm[support[0] + 2, support[1] + 2] = 2.
                self.assertEqual(tl.unsupported_tree_crowns(
                    (0, 0), [(*crown, 3.)], idx, set(), ndsm, margin=2), [])

    def test_circular_height_gate_matches_helper_and_rejects_corner_only_support(self):
        from tree_lines import gated_crowns
        idx = self.index([(self.polygon(0, 0, 128, 128), {'landuse': 'meadow'})])
        crowns = [(10, 10, 3.), (30, 30, 3.), (50, 50, 3.), (70, 70, 3.)]
        h = np.zeros((132, 132))
        h[14, 14] = 3.  # (12,12): square corner beyond circle at (10,10)
        h[34, 32] = 2.  # (32,30): on circle at (30,30)
        h[52, 52] = np.inf
        h[72, 72] = np.nan
        support = gated_crowns(crowns, -2, -2, h)
        self.assertEqual(support, {(10, 10): False, (30, 30): True,
                                   (50, 50): False, (70, 70): False})
        rejected = tl.unsupported_tree_crowns((0, 0), crowns, idx, set(), h, margin=2)
        self.assertEqual({(x, z) for x, z, _ in rejected}, {q for q, ok in support.items() if not ok})

    def test_planning_uses_cached_first_return_and_reports_actual_fallback(self):
        idx = self.index([(self.polygon(0, 0, 128, 128), {'landuse': 'meadow'})])
        shape = (132, 132)
        first = np.zeros(shape)
        first[130, 52] = 3.  # just across east edge, circular distance 1
        client = types.SimpleNamespace(missing=mock.Mock(return_value=False),
                                       sample=mock.Mock(return_value=first))
        with mock.patch('osm_footprints.OSMIndex', return_value=idx), \
                mock.patch.object(tl, 'canopy_grid', return_value=np.zeros((128, 128))), \
                mock.patch.object(tl, 'find_crowns', return_value=[(127, 50, 3.), (40, 40, 3.)]), \
                mock.patch.object(tl, 'cover_grid', return_value=np.zeros((128, 128))), \
                mock.patch.object(tl, 'extra_crowns', return_value=[]), \
                mock.patch.object(tl, 'google_crowns', return_value=None), \
                mock.patch.object(tl, 'cell_terrain', return_value=None), \
                mock.patch.object(tl, 'neighbour_owned', return_value=set()), \
                mock.patch.object(tl, 'ignored_builds', return_value=set()), \
                mock.patch.object(tl, 'load_masked_positions', return_value={}), \
                mock.patch('cell_registry.view_registry', return_value=types.SimpleNamespace(recorded=lambda n: None)), \
                mock.patch('lidar_provider.tile_client', return_value=client) as factory, \
                mock.patch('lidar_provider.mc_heights_float', return_value={'dtm': np.zeros(shape), 'dsm': np.zeros(shape)}), \
                mock.patch('world_frame.load_frame', return_value=object()), \
                mock.patch('world_frame.mc_grid_to_en', side_effect=lambda x, z, f: (x, -z)):
            inputs = tl.planning_inputs((0, 0), osm_ground=_empty_osm_ground())
            self.assertEqual(inputs.crowns, [(127, 50, 3.)])
            self.assertEqual(inputs.height_source, 'first-return')
            self.assertEqual([(x, z) for x, z, _ in inputs.source_rejections], [(40, 40)])
            factory.assert_called_once_with(download=False)
            client.missing.return_value = True
            inputs = tl.planning_inputs((0, 0), osm_ground=_empty_osm_ground())
            self.assertEqual(inputs.crowns, [])
            self.assertEqual(inputs.height_source, 'last-return')

    def test_missing_halo_preserves_cached_interior_lidar_evidence(self):
        idx = self.index([(self.polygon(0, 0, 128, 128), {'landuse': 'meadow'})])
        ndsm = np.zeros((128, 128))
        ndsm[40, 40] = 2.
        with mock.patch('osm_footprints.OSMIndex', return_value=idx), \
                mock.patch.object(tl, 'canopy_grid', return_value=np.zeros((128, 128))), \
                mock.patch.object(tl, 'find_crowns', return_value=[(40, 40, 3.)]), \
                mock.patch.object(tl, 'cover_grid', return_value=np.zeros((128, 128))), \
                mock.patch.object(tl, 'extra_crowns', return_value=[]), \
                mock.patch.object(tl, 'google_crowns', return_value=None), \
                mock.patch('tree_lines.standing_height', side_effect=lambda c, margin=0: (None, 'none') if margin else (ndsm, 'first-return')), \
                mock.patch.object(tl, 'cell_terrain', return_value=None), \
                mock.patch.object(tl, 'neighbour_owned', return_value=set()), \
                mock.patch.object(tl, 'ignored_builds', return_value=set()), \
                mock.patch.object(tl, 'load_masked_positions', return_value={}), \
                mock.patch('cell_registry.view_registry', return_value=types.SimpleNamespace(recorded=lambda n: None)):
            inputs = tl.planning_inputs((0, 0), osm_ground=_empty_osm_ground())
        self.assertEqual(inputs.crowns, [(40, 40, 3.)])
        self.assertEqual(inputs.source_rejections, [])

    def test_planning_inputs_gate_detector_fill_aerial_and_extra_but_keep_mapped_rows(self):
        x0, z0 = CELL[0] * 128, CELL[1] * 128
        idx = self.index([(self.polygon(x0, z0, x0 + 128, z0 + 128), {'landuse': 'meadow'})])
        ground = types.SimpleNamespace(trees=[(x0 + 10, z0 + 10)],
                                       tree_rows=[[(x0 + 20, z0 + 80), (x0 + 32, z0 + 80)]])
        ndsm = np.zeros((132, 132))
        ndsm[128 + 2, 50 + 2] = 2.    # supports only the detector crown beside the east cell edge
        with mock.patch('osm_footprints.OSMIndex', return_value=idx), \
                mock.patch.object(tl, 'canopy_grid', return_value=np.zeros((128, 128))), \
                mock.patch.object(tl, 'find_crowns', return_value=[(40, 40, 3.), (127, 50, 3.)]), \
                mock.patch.object(tl, 'cover_grid', return_value=cover(1., 1.)), \
                mock.patch.object(tl, 'extra_crowns', return_value=[(100, 100, 3.)]), \
                mock.patch.object(tl, 'google_crowns', return_value=([(x0 + 60, z0 + 60, x0 + 70, z0 + 70)], [(65, 65, 3.)])), \
                mock.patch('tree_lines.standing_height', return_value=(ndsm, 'first-return')), \
                mock.patch.object(tl, 'cell_terrain', return_value=None), \
                mock.patch.object(tl, 'neighbour_owned', return_value=set()), \
                mock.patch.object(tl, 'ignored_builds', return_value=set()), \
                mock.patch.object(tl, 'load_masked_positions', return_value={}), \
                mock.patch('cell_registry.view_registry', return_value=types.SimpleNamespace(recorded=lambda n: None)):
            inputs = tl.planning_inputs(CELL, osm_ground=ground)
        self.assertEqual({(x0 + i, z0 + j) for i, j, _r in inputs.crowns},
                         inputs.mapped | {(x0 + 127, z0 + 50)})
        self.assertEqual(len(inputs.source_rejections), 3)  # low lidar prevents aerial fill before this gate
        self.assertEqual(len(inputs.crown_origins), len(inputs.crowns))
        self.assertGreaterEqual(len(inputs.mapped), 4)             # young mapped point and row survive


class TestPlanCellKeepsRules(unittest.TestCase):
    """Fill crowns go through plan_cell's footprint / surface rules like any other crown."""

    def run_plan(self, footprint_x, hard_x):
        x0, z0 = CELL[0] * 128, CELL[1] * 128
        fp = {(x0 + i, z0 + j) for i in range(*footprint_x) for j in range(128)}

        def surface(world, x, z):
            return (60, 'stone') if x - x0 in range(*hard_x) else (60, 'grass_block')

        osm = types.ModuleType('osm_footprints')
        osm.OSMIndex = lambda: types.SimpleNamespace(cells=lambda: fp, areas=[])
        plan = types.SimpleNamespace(set=lambda *a: None)
        with mock.patch.object(tl, 'canopy_grid', lambda c: np.zeros((128, 128))), \
                mock.patch.object(tl, 'cover_grid', lambda c: cover(1.0, 1.0)), \
                mock.patch('tree_lines.standing_height', lambda c, margin=0: (None, 'none')), \
                mock.patch.object(tl, 'surface_y', surface), \
                mock.patch.object(tl, 'neighbour_owned', lambda cell, exclude=(): set()), \
                mock.patch.object(tl, 'load_masked_positions', lambda: {}), \
                mock.patch('ground_sources.OSMGround', _empty_osm_ground), \
                mock.patch.dict(sys.modules, {'osm_footprints': osm}):
            return tl.plan_cell(CELL, plan, FakeWorld())[1]

    def test_plan_warms_the_cell_and_overhang_margin_before_probing(self):
        warmed = []
        with mock.patch.object(tl, 'warm_surface_probe', lambda w, *box: warmed.append(box)):
            self.run_plan((10, 30), (60, 80))
        x0, z0 = CELL[0] * 128, CELL[1] * 128
        m = tl.CROWN_MARGIN
        self.assertEqual(warmed, [(x0 - m, z0 - m, x0 + 127 + m, z0 + 127 + m)])

    def test_footprint_and_hard_surface_columns_are_not_rooted(self):
        x0 = CELL[0] * 128
        rep = self.run_plan((10, 30), (60, 80))
        self.assertGreater(len(rep['trees']), 100)
        for x, _z, _r, _k in rep['trees']:
            self.assertNotIn(x - x0, range(10, 30))
            self.assertNotIn(x - x0, range(60, 80))
        why = {w for *_p, w in rep['skipped']}
        self.assertIn('feature zone', why)
        self.assertIn('surface stone', why)


class TestOverhang(unittest.TestCase):
    """One rule: a tree belongs to the cell its trunk is in; the crown may overhang neighbour air."""

    def plan(self, world, masked=None, crown=(127, 64, 5.0)):
        x0, z0 = CELL[0] * 128, CELL[1] * 128
        osm = types.ModuleType('osm_footprints')
        osm.OSMIndex = lambda: types.SimpleNamespace(cells=lambda: set(), areas=[])
        sets = {}
        plan = types.SimpleNamespace(set=lambda x, y, z, b: sets.__setitem__((x, y, z), b))
        with mock.patch.object(tl, 'canopy_grid', lambda c: np.zeros((128, 128))), \
                mock.patch.object(tl, 'find_crowns', lambda g: [crown]), \
                mock.patch.object(tl, 'cover_grid', lambda c: np.zeros((128, 128))), \
                mock.patch.object(tl, 'surface_y', lambda w, x, z: (60, 'grass_block')), \
                mock.patch.object(tl, 'neighbour_owned', lambda cell, exclude=(): set()), \
                mock.patch.object(tl, 'load_masked_positions', lambda: masked or {}), \
                mock.patch('ground_sources.OSMGround', _empty_osm_ground), \
                mock.patch.dict(sys.modules, {'osm_footprints': osm}):
            placed, rep = tl.plan_cell(CELL, plan, world)
        return placed, rep, sets, x0 + 128, z0

    def test_overhang_into_neighbour_air_is_kept_and_planned(self):
        placed, rep, sets, edge, _z = self.plan(FakeWorld())
        outside = [p for p in placed if p[0] >= edge]
        self.assertGreater(len(outside), 0)
        self.assertEqual(rep['overhang'], len(outside))
        self.assertEqual(sets, placed)                       # the plan writes the same blocks the mask will record
        self.assertTrue(all('leaves' in b for p, b in placed.items() if p[0] >= edge))

    def test_trunk_is_in_the_cell(self):
        placed, _rep, _s, edge, z0 = self.plan(FakeWorld())
        logs = [p for p, b in placed.items() if 'log' in b]
        self.assertTrue(logs)
        self.assertTrue(all(p[0] < edge and z0 <= p[2] < z0 + 128 for p in logs))

    def test_neighbour_blocks_and_masks_are_not_overwritten(self):
        edge = CELL[0] * 128 + 128
        solid = {(x, y, z) for x in range(edge, edge + 3) for y in range(60, 75) for z in range(CELL[1] * 128 + 56, CELL[1] * 128 + 72)}

        class W:
            def block(self, x, y, z):
                return 'minecraft:stone' if (x, y, z) in solid else 'minecraft:air'
        owned = {(edge + 3, 63, CELL[1] * 128 + 64): 'trees 901,900'}
        placed, _rep, _s, _e, _z = self.plan(W(), masked=owned)
        self.assertFalse(set(placed) & solid)
        self.assertNotIn((edge + 3, 63, CELL[1] * 128 + 64), placed)

    def test_trunk_blocked_outside_cell_never_moves_the_trunk(self):
        # a crown rooted at the edge column still roots in-cell; nothing is planted rooted outside
        placed, rep, *_ = self.plan(FakeWorld(), crown=(127, 0, 3.0))
        self.assertEqual([(x, z) for x, z, *_ in rep['trees']], [(CELL[0] * 128 + 127, CELL[1] * 128)])
        self.assertTrue(all(p[0] < CELL[0] * 128 + 128 for p, b in placed.items() if 'log' in b))

    def test_leaf_merges_into_neighbour_tree_leaf_but_never_its_trunk(self):
        edge = CELL[0] * 128 + 128
        z = CELL[1] * 128 + 64
        leaf = {(edge + 1, y, z) for y in range(60, 75)}
        log = {(edge + 2, y, z) for y in range(60, 75)}
        owned = {p: 'trees 901,900' for p in leaf | log}

        class W:
            def block(self, x, y, zz):
                return 'minecraft:oak_leaves' if (x, y, zz) in leaf else 'minecraft:oak_log' if (x, y, zz) in log else 'minecraft:air'
        placed, *_ = self.plan(W(), masked=owned)
        self.assertTrue(set(placed) & leaf)
        self.assertFalse(set(placed) & log)

    def test_may_plant_rule(self):
        air, leaf, log = 'minecraft:air', 'minecraft:oak_leaves', 'minecraft:oak_log'
        self.assertTrue(tl.may_plant(air, leaf, None))
        self.assertFalse(tl.may_plant(leaf, leaf, None))                    # unowned leaf (Geocraft) stays
        self.assertTrue(tl.may_plant(leaf, leaf, 'trees 1,1'))
        self.assertFalse(tl.may_plant(leaf, log, 'trees 1,1'))
        self.assertFalse(tl.may_plant(leaf, leaf, 'decor 1,1'))
        self.assertFalse(tl.may_plant(log, leaf, 'trees 1,1'))

    def test_deterministic(self):
        a = self.plan(FakeWorld())[0]
        self.assertEqual(a, self.plan(FakeWorld())[0])


class TestExtraCrowns(unittest.TestCase):
    """reference/trees-extra-crowns.json: a tree the mosaic cannot show, planted through the normal rules."""

    def run_plan(self, in_footprint, surface):
        import json
        x0, z0 = CELL[0] * 128, CELL[1] * 128
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'extra.json'
            f.write_text(json.dumps({f'{CELL[0]},{CELL[1]}': [{'x': x0 + 60, 'z': z0 + 60, 'r': 3.4}]}))
            osm = types.ModuleType('osm_footprints')
            fp = {(x0 + 60, z0 + 60)} if in_footprint else set()
            osm.OSMIndex = lambda: types.SimpleNamespace(cells=lambda: fp, areas=[])
            plan = types.SimpleNamespace(set=lambda *a: None)
            with mock.patch.object(tl, 'EXTRA_CROWNS', f), \
                    mock.patch.object(tl, 'canopy_grid', lambda c: np.zeros((128, 128))), \
                    mock.patch.object(tl, 'cover_grid', lambda c: np.zeros((128, 128))), \
                    mock.patch.object(tl, 'surface_y', lambda w, x, z: (60, surface)), \
                    mock.patch.object(tl, 'neighbour_owned', lambda cell, exclude=(): set()), \
                    mock.patch.object(tl, 'load_masked_positions', lambda: {}), \
                    mock.patch('ground_sources.OSMGround', _empty_osm_ground), \
                    mock.patch.dict(sys.modules, {'osm_footprints': osm}):
                return tl.plan_cell(CELL, plan, FakeWorld())

    def test_extra_crown_is_planted_even_in_an_osm_footprint(self):
        placed, rep = self.run_plan(True, 'grass_block')
        self.assertEqual(len(rep['trees']), 1)
        self.assertTrue(any('log' in b for b in placed.values()))

    def test_extra_crown_can_root_on_evidenced_hard_ground(self):
        placed, rep = self.run_plan(True, 'light_gray_concrete')
        self.assertEqual(len(rep['trees']), 1)
        self.assertTrue(placed)


class TestGoogleCrowns(unittest.TestCase):
    """reference/trees-google-crowns.json: crowns read from Google replace the detector inside the zones."""

    X0, Z0 = CELL[0] * 128, CELL[1] * 128

    def run_plan(self, surface, detected=((20, 20, 3.0),), in_footprint=False):
        import json
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'google.json'
            f.write_text(json.dumps({f'{CELL[0]},{CELL[1]}': {
                'zones': [[self.X0, self.Z0, self.X0 + 50, self.Z0 + 50]],
                'crowns': [{'x': self.X0 + 60, 'z': self.Z0 + 60, 'r': 3.4}]}}))
            osm = types.ModuleType('osm_footprints')
            fp = {(self.X0 + 60, self.Z0 + 60)} if in_footprint else set()
            osm.OSMIndex = lambda: types.SimpleNamespace(cells=lambda: fp, areas=[])
            plan = types.SimpleNamespace(set=lambda *a: None)
            with mock.patch.object(tl, 'GOOGLE_CROWNS', f), \
                    mock.patch.object(tl, 'canopy_grid', lambda c: np.zeros((128, 128))), \
                    mock.patch.object(tl, 'find_crowns', lambda g: list(detected)), \
                    mock.patch.object(tl, 'cover_grid', lambda c: np.zeros((128, 128))), \
                    mock.patch.object(tl, 'surface_y', lambda w, x, z: (60, surface)), \
                    mock.patch.object(tl, 'neighbour_owned', lambda cell, exclude=(): set()), \
                    mock.patch.object(tl, 'load_masked_positions', lambda: {}), \
                    mock.patch('ground_sources.OSMGround', _empty_osm_ground), \
                    mock.patch('tree_lines.standing_height', lambda c, margin=0: (None, 'none')), \
                    mock.patch.dict(sys.modules, {'osm_footprints': osm}):
                return tl.plan_cell(CELL, plan, FakeWorld())

    def test_detector_crown_inside_a_zone_is_dropped_and_the_google_crown_planted(self):
        _placed, rep = self.run_plan('grass_block')
        self.assertEqual([(x, z) for x, z, _r, _k in rep['trees']], [(self.X0 + 60, self.Z0 + 60)])

    def test_google_crown_may_stand_on_paving_and_in_a_footprint(self):
        _placed, rep = self.run_plan('light_gray_concrete', in_footprint=True)
        self.assertEqual(len(rep['trees']), 1)

    def test_detector_crown_outside_the_zones_is_kept(self):
        _placed, rep = self.run_plan('grass_block', detected=((90, 90, 3.0),))
        self.assertEqual(len(rep['trees']), 2)

    def test_no_entry_means_no_change(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'g.json'
            f.write_text(json.dumps({'1,1': {'crowns': []}}))
            self.assertIsNone(tl.google_crowns(CELL, f))


class TestSnapGoogleCrowns(unittest.TestCase):
    """Hand-read crowns move to the nearest high nDSM peak; otherwise stay and are unconfirmed (#129)."""

    def test_crowns_snap_to_nearby_ndsm_peaks_and_stay_when_unconfirmed(self):
        ndsm = np.zeros((128, 128))
        # Peak a few blocks east of a low hand-read spot (S-017-style offset).
        ndsm[64, 60] = 1.0
        ndsm[68, 63] = 9.0
        # Peak on a building footprint: must not move there.
        ndsm[40, 40] = 0.5
        ndsm[44, 40] = 10.0
        # No peak in reach of (10, 10).
        crowns = [(64, 60, 3.0), (40, 40, 3.0), (10, 10, 3.0)]
        footprints = {(44, 40)}
        snapped, report = tl.snap_google_crowns(crowns, ndsm, footprints)
        self.assertEqual(snapped[0], (68, 63, 3.0))
        self.assertEqual(report[0]['status'], 'moved')
        self.assertEqual(report[0]['shift'], (4, 3))
        self.assertEqual(snapped[1], (40, 40, 3.0))
        self.assertEqual(report[1]['status'], 'unconfirmed')
        self.assertEqual(snapped[2], (10, 10, 3.0))
        self.assertEqual(report[2]['status'], 'unconfirmed')
        self.assertEqual(sum(1 for r in report if r['status'] == 'moved'), 1)


class TestMappedTreeCrowns(unittest.TestCase):
    """Mapped natural=tree points and tree_row lines → crown candidates (#157).

    Young trees with no lidar peak are kept as small crowns (same as #129
    ``unconfirmed``), not rejected.
    """

    X0, Z0 = CELL[0] * 128, CELL[1] * 128

    def test_three_in_cell_points_each_yield_crown_or_logged_rejection(self):
        """Fixture: three tree points + canopy raster; every in-cell point is accounted for."""
        ndsm = np.zeros((128, 128))
        ndsm[50, 50] = 1.0
        ndsm[54, 52] = 8.5   # peak near first point
        # Second point (20, 20): no peak in reach → young tree, kept small.
        # Third point (80, 80): peak only on a building → unconfirmed, kept.
        ndsm[84, 80] = 10.0
        points = [
            (self.X0 + 50, self.Z0 + 50),
            (self.X0 + 20, self.Z0 + 20),
            (self.X0 + 80, self.Z0 + 80),
        ]
        footprints = {(84, 80)}
        crowns, rejections, snaps = tl.resolve_mapped_tree_crowns(
            CELL, tree_points=points, tree_rows=[], ndsm=ndsm, footprints=footprints,
        )
        # Every in-cell point → crown or rejection (no silent drop).
        self.assertEqual(len(crowns) + len(rejections), 3)
        origins = [s['from'] for s in snaps] + [
            (r['x'] - self.X0, r['z'] - self.Z0) for r in rejections
        ]
        self.assertEqual(set(origins), {(50, 50), (20, 20), (80, 80)})
        # First snapped to the peak.
        self.assertIn((54, 52, tl.MAPPED_TREE_R), crowns)
        self.assertEqual(snaps[0]['status'], 'moved')
        # Young tree (no peak): kept as a small crown, not rejected (#129 match).
        young = [c for c in crowns if c[0] == 20 and c[1] == 20]
        self.assertEqual(len(young), 1)
        self.assertEqual(young[0][2], tl.MAPPED_YOUNG_TREE_R)
        self.assertEqual(snaps[1]['status'], 'unconfirmed')
        self.assertEqual(snaps[1].get('reason'), 'no lidar canopy peak')
        # Peak on building: kept unconfirmed (not rejected).
        self.assertTrue(any(c[0] == 80 and c[1] == 80 for c in crowns))
        self.assertEqual(snaps[2]['status'], 'unconfirmed')
        self.assertEqual(snaps[2].get('reason'), 'peak on building')

    def test_outside_cell_point_is_logged_rejection(self):
        ndsm = np.zeros((128, 128))
        points = [
            (self.X0 + 10, self.Z0 + 10),
            (self.X0 + 200, self.Z0 + 10),  # outside the 128²
        ]
        crowns, rejections, _snaps = tl.resolve_mapped_tree_crowns(
            CELL, tree_points=points, tree_rows=[], ndsm=ndsm, footprints=set(),
        )
        self.assertEqual(len(crowns), 1)
        self.assertEqual(len(rejections), 1)
        self.assertEqual(rejections[0]['reason'], 'outside cell')
        self.assertEqual((rejections[0]['x'], rejections[0]['z']),
                         (self.X0 + 200, self.Z0 + 10))

    def test_tree_row_becomes_crown_sequence(self):
        ndsm = np.zeros((128, 128))
        # Horizontal row along z=40 from dx=10 to dx=30; peaks at each spacing site.
        for i in range(10, 31, tl.TREE_ROW_SPACING):
            ndsm[i, 40] = 7.0
        row = [(self.X0 + 10.0, self.Z0 + 40.0), (self.X0 + 30.0, self.Z0 + 40.0)]
        crowns, rejections, snaps = tl.resolve_mapped_tree_crowns(
            CELL, tree_points=[], tree_rows=[row], ndsm=ndsm, footprints=set(),
        )
        self.assertEqual(rejections, [])
        self.assertGreaterEqual(len(crowns), 3)
        xs = sorted(c[0] for c in crowns)
        for a, b in zip(xs, xs[1:]):
            self.assertAlmostEqual(b - a, tl.TREE_ROW_SPACING, delta=1)
        self.assertTrue(all(c[1] == 40 for c in crowns))
        self.assertTrue(all(s['status'] == 'moved' for s in snaps))

    def test_planning_inputs_merges_mapped_crowns(self):
        """Mapped points from OSMGround become crowns in planning_inputs."""
        x0, z0 = self.X0, self.Z0
        ndsm = np.zeros((128, 128))
        ndsm[40, 40] = 9.0
        fake = types.SimpleNamespace(
            trees=[(x0 + 40, z0 + 40), (x0 + 15, z0 + 15)],
            tree_rows=[[(x0 + 5.0, z0 + 60.0), (x0 + 17.0, z0 + 60.0)]],
        )
        osm = types.ModuleType('osm_footprints')
        osm.OSMIndex = lambda: types.SimpleNamespace(cells=lambda: set(), areas=[])
        with mock.patch.object(tl, 'canopy_grid', lambda c: np.zeros((128, 128))), \
                mock.patch.object(tl, 'cover_grid', lambda c: np.zeros((128, 128))), \
                mock.patch('tree_lines.standing_height', lambda c, margin=0: (np.pad(ndsm, margin), 'first-return')), \
                mock.patch.object(tl, 'neighbour_owned', lambda cell, exclude=(): set()), \
                mock.patch.object(tl, 'ignored_builds', lambda cell: set()), \
                mock.patch.object(tl, 'load_masked_positions', lambda: {}), \
                mock.patch.dict(sys.modules, {'osm_footprints': osm}), \
                mock.patch('cell_registry.view_registry',
                           lambda: types.SimpleNamespace(recorded=lambda n: None)):
            inputs = tl.planning_inputs(CELL, osm_ground=fake)
        self.assertTrue(inputs.mapped)
        self.assertIsNotNone(inputs.mapped_rejections)
        local = {(i, j) for i, j, _r in inputs.crowns}
        self.assertIn((40, 40), local)
        self.assertIn((15, 15), local)


class TestRetainedCrownOrigins(unittest.TestCase):
    """Exercise source snapping/precedence through the production input loader and planner."""

    def load_and_plan(self, aerial=(), mapped=()):
        from site_kit import Plan
        ndsm = np.zeros((128, 128))
        ndsm[10, 10] = 9.0
        osm = types.ModuleType('osm_footprints')
        osm.OSMIndex = lambda: types.SimpleNamespace(cells=lambda: set(), areas=[])
        ground = types.SimpleNamespace(trees=list(mapped), tree_rows=[], road={(10, 10)})
        with mock.patch.object(tl, 'canopy_grid', return_value=np.zeros((128, 128))), \
                mock.patch.object(tl, 'cover_grid', return_value=np.zeros((128, 128))), \
                mock.patch.object(tl, 'extra_crowns', return_value=[]), \
                mock.patch.object(tl, 'google_crowns', return_value=([(0, 0, 127, 127)], list(aerial))), \
                mock.patch('tree_lines.standing_height', side_effect=lambda c, margin=0: (np.pad(ndsm, margin), 'first-return')), \
                mock.patch.object(tl, 'neighbour_owned', return_value=set()), \
                mock.patch.object(tl, 'ignored_builds', return_value=set()), \
                mock.patch.object(tl, 'load_masked_positions', return_value={}), \
                mock.patch.dict(sys.modules, {'osm_footprints': osm}), \
                mock.patch('cell_registry.view_registry',
                           return_value=types.SimpleNamespace(recorded=lambda n: None)):
            inputs = tl.planning_inputs((0, 0), osm_ground=ground)
        class Ground:
            def block(self, x, y, z):
                return 'minecraft:grass_block' if y <= 60 else 'minecraft:air'
        # Isolate rooting direction from canopy-overlap policy between colliding crowns.
        with mock.patch.object(tl, 'surface_y', return_value=(60, 'grass_block')), \
                mock.patch.object(tl, 'warm_surface_probe'), \
                mock.patch.object(tl, 'voxels_for', side_effect=lambda x, z, gy, r: {(x, gy + 1, z): 'oak_log'}):
            _, report = tl.plan_cell((0, 0), Plan('origins'), Ground(), inputs.after_undo({}))
        return inputs, report

    def test_colliding_aerial_snap_destinations_keep_opposite_observations(self):
        inputs, report = self.load_and_plan(aerial=[(8, 10, 3.), (12, 10, 3.)])
        self.assertEqual([c[:2] for c in inputs.crowns], [(10, 10), (10, 10)])
        self.assertEqual(inputs.crown_origins, [(8, 10), (12, 10)])
        self.assertEqual([r['observed_crown'] for r in report['rooting']], [[8, 10], [12, 10]])
        self.assertEqual([r['to'] for r in report['rooting']], [[9, 10], [11, 10]])

    def test_colliding_mapped_snap_destinations_keep_opposite_observations(self):
        inputs, report = self.load_and_plan(mapped=[(8, 10), (12, 10)])
        self.assertEqual(inputs.crown_origins, [(8, 10), (12, 10)])
        self.assertEqual([r['to'] for r in report['rooting']], [[9, 10], [11, 10]])

    def test_dropped_aerial_snap_cannot_change_retained_mapped_origin(self):
        inputs, report = self.load_and_plan(aerial=[(8, 10, 3.)], mapped=[(12, 10)])
        self.assertEqual(len(inputs.crown_snaps), 1)  # diagnostics still include the dropped observation
        self.assertEqual(inputs.crowns, [(10, 10, tl.MAPPED_TREE_R)])
        self.assertEqual(inputs.crown_origins, [(12, 10)])
        self.assertEqual(report['rooting'][0]['observed_crown'], [12, 10])
        self.assertEqual(report['rooting'][0]['to'], [11, 10])


class TestEvidencedRooting(unittest.TestCase):
    """Plan blocks and reasons: evidence permits hard ground, never road or water."""

    def plan(self, roads=(), water=(), crown=(10, 10, 3.), owned=(), surface='blue_concrete',
             masked=None, footprints=(), source='aerial', snaps=None, ground_block=None, crown_origins=None):
        from site_kit import Plan
        from dataclasses import replace
        origin = (crown[0], crown[1])
        inputs = tl.TreeInputs([crown], 0, {origin} if source == 'extra' else set(),
                               {origin} if source == 'aerial' else set(), set(owned), None, masked or {}, set(footprints),
                               crown_snaps=snaps, mapped={origin} if source == 'mapped' else set(),
                               crown_origins=crown_origins)
        inputs = replace(inputs, road=set(roads), water=set(water))
        class Ground:
            def block(self, x, y, z):
                if y <= 60 and ground_block is not None:
                    return ground_block(x, y, z)
                return f'minecraft:{surface}' if y <= 60 else 'minecraft:air'
        with mock.patch.object(tl, 'surface_y', return_value=(60, surface)), \
                mock.patch.object(tl, 'warm_surface_probe'):
            return tl.plan_cell((0, 0), Plan('rooting'), Ground(), inputs)

    def test_evidenced_hard_ground_is_legal(self):
        placed, report = self.plan()
        self.assertTrue(any('log' in b for b in placed.values()))
        self.assertEqual(report['skipped'], [])

    def test_road_edge_shunts_one_orthogonal_block_deterministically(self):
        placed, report = self.plan(roads={(10, 10)})
        self.assertTrue(placed)
        self.assertEqual(report['rooting'][0]['from'], [10, 10])
        self.assertEqual(report['rooting'][0]['to'], [9, 10])
        self.assertEqual(report, self.plan(roads={(10, 10)})[1])

    def test_no_adjacent_legal_position_leaves_tree_unplaced(self):
        cross = {(10, 10), (9, 10), (11, 10), (10, 9), (10, 11)}
        placed, report = self.plan(water=cross)
        self.assertFalse(placed)
        self.assertIn('one-block', report['skipped'][0][-1])

    def test_blocked_trunk_does_not_shunt_on_legal_ground(self):
        placed, report = self.plan(owned={(10, 61, 10)})
        self.assertFalse(placed)
        self.assertEqual(report['skipped'][0][-1], 'feature above terrain')
        self.assertIsNone(report['rooting'][0]['to'])

    def test_one_block_adjustment_cannot_move_trunk_outside_its_cell(self):
        roads = {(0, 10), (0, 9), (0, 11), (1, 10)}
        placed, report = self.plan(roads=roads, crown=(0, 10, 3.))
        self.assertFalse(placed)
        self.assertIn('no legal one-block', report['skipped'][0][-1])

    def test_live_water_rejects_even_without_map_water(self):
        placed, report = self.plan(surface='water')
        self.assertFalse(placed)
        self.assertIn('water', report['skipped'][0][-1])

    def test_each_explicit_source_can_root_on_hard_ground_but_detector_cannot(self):
        for source in ('aerial', 'mapped', 'extra', 'detector'):
            placed, report = self.plan(source=source)
            self.assertEqual(bool(placed), source != 'detector')
        # Detector/dense-fill retain their existing lawn and footprint policy.
        placed, report = self.plan(source='detector', surface='grass_block', roads={(10, 10)})
        self.assertFalse(placed)
        self.assertEqual(report['skipped'][0][-1], 'carriageway')
        self.assertEqual(len(report['rooting'][0]['attempts']), 1)
        placed, report = self.plan(source='detector', surface='grass_block', footprints={(10, 10)})
        self.assertFalse(placed)
        self.assertEqual(report['skipped'][0][-1], 'feature zone')

    def test_courtyard_exception_and_masked_trunk_are_preserved(self):
        placed, _ = self.plan(footprints={(10, 10)})
        self.assertTrue(placed)
        placed, report = self.plan(masked={(10, 61, 10): 'decor 0,0'})
        self.assertFalse(placed)
        self.assertEqual(report['skipped'][0][-1], 'trunk blocked')
        self.assertEqual(len(report['rooting'][0]['attempts']), 1)

    def test_shunt_is_closest_to_observed_crown_after_separate_lidar_snap(self):
        snaps = [{'from': (12, 10), 'to': (10, 10), 'shift': (-2, 0), 'status': 'moved'}]
        _, report = self.plan(roads={(10, 10)}, snaps=snaps, crown_origins=[(12, 10)])
        rooting = report['rooting'][0]
        self.assertEqual(rooting['from'], [10, 10])
        self.assertEqual(rooting['to'], [11, 10])
        self.assertEqual(rooting['observed_crown'], [12, 10])
        self.assertEqual(report['crown_snaps'], snaps)

    def test_surface_or_unknown_water_status_is_a_review_reason_without_shunt(self):
        for mode in ('missing', 'unknown'):
            if mode == 'missing':
                with mock.patch.object(tl, 'surface_y', return_value=None), mock.patch.object(tl, 'warm_surface_probe'):
                    # Call directly so the fixture's surface mock does not replace this one.
                    from site_kit import Plan
                    inputs = tl.TreeInputs([(10, 10, 3.)], 0, set(), {(10, 10)}, set(), None, {}, set())
                    placed, report = tl.plan_cell((0, 0), Plan('no_surface'), FakeWorld(), inputs)
            else:
                placed, report = self.plan(ground_block=lambda *args: None)
            self.assertFalse(placed)
            self.assertIn(report['skipped'][0][-1], ('no surface', 'unknown water status'))
            self.assertEqual(len(report['rooting'][0]['attempts']), 1)

    def test_road_water_under_canopy_do_not_reject_a_legal_trunk(self):
        placed, report = self.plan(roads={(12, 10)}, water={(10, 12)})
        self.assertTrue(placed)
        self.assertEqual(report['rooting'][0]['from'], report['rooting'][0]['to'])
        self.assertTrue(any('leaves' in b for p, b in placed.items() if (p[0], p[2]) in {(12, 10), (10, 12)}))

    def test_all_trunk_footprint_columns_are_checked(self):
        def voxels(x, z, gy, r):
            return {(x, gy + 1, z): 'oak_log', (x + 1, gy + 1, z): 'oak_log', (x, gy + 2, z): 'oak_leaves'}
        with mock.patch.object(tl, 'voxels_for', side_effect=voxels):
            placed, report = self.plan(roads={(11, 10)})
        self.assertTrue(placed)
        self.assertEqual(report['rooting'][0]['reason'], 'carriageway')
        self.assertEqual(report['rooting'][0]['to'], [9, 10])
        self.assertTrue(all((p[0], p[2]) != (11, 10) for p, b in placed.items() if 'log' in b))

    def test_every_shunt_candidate_obeys_ownership_and_mask_checks(self):
        placed, report = self.plan(roads={(10, 10)}, owned={(9, 61, 10)}, masked={(10, 61, 9): 'decor 0,0'})
        self.assertTrue(placed)
        self.assertEqual(report['rooting'][0]['to'], [10, 11])
        attempts = report['rooting'][0]['attempts']
        self.assertEqual([a['reason'] for a in attempts], ['carriageway', 'feature above terrain', 'trunk blocked', 'accepted'])
        self.assertTrue(all(abs(a['at'][0] - 10) + abs(a['at'][1] - 10) <= 1 for a in attempts))

    def test_waterlogged_live_surface_is_not_a_legal_root(self):
        placed, report = self.plan(ground_block=lambda *args: 'minecraft:smooth_stone_slab[waterlogged=true]')
        self.assertFalse(placed)
        self.assertIn('water', report['skipped'][0][-1])


class TestTerrainGround(unittest.TestCase):
    """#301: trunk base, material, water and carriageway checks read the block at lidar terrain Y, never a roof."""

    GROUND = 60
    ROOF = 70

    def plan(self, blocks=None, owned=(), masked=None, terrain=GROUND, road=(), water=(), crown=(10, 10, 3.),
             source='aerial', footprints=(), tx=(0, 0), terrain_x0=0):
        from site_kit import Plan
        origin = (crown[0], crown[1])
        hm = None if terrain is None else {'x0': terrain_x0, 'z0': 0, 'terrain': np.full((128, 128), terrain)}
        inputs = tl.TreeInputs([crown], 0, set(), {origin} if source == 'aerial' else set(), set(owned), None,
                               masked or {}, set(footprints), terrain=hm, road=set(road), water=set(water))
        live = dict(blocks or {})

        class W:
            def block(self, x, y, z):
                if (x, y, z) in live:
                    return live[(x, y, z)]
                return 'minecraft:grass_block' if y <= TestTerrainGround.GROUND else 'minecraft:air'
        with mock.patch.object(tl, 'warm_surface_probe'):
            placed, report = tl.plan_cell(tx, Plan('terrain'), W(), inputs)
        return placed, report

    @staticmethod
    def roof(xs=range(0, 25), zs=range(0, 25), y=ROOF):
        return {(x, y, z): 'minecraft:red_concrete' for x in xs for z in zs}

    def logs(self, placed):
        return [p for p, b in placed.items() if 'log' in b]

    def test_crown_over_a_roof_with_open_ground_beneath_grows_from_the_terrain(self):
        # the live top block is the roof at 70; ground at 60 is open beneath it (an overhang or a canopy roof)
        placed, report = self.plan(blocks=self.roof(y=self.ROOF + 10))
        self.assertTrue(self.logs(placed))
        self.assertEqual(min(p[1] for p in self.logs(placed)), self.GROUND + 1)
        self.assertEqual(report['skipped'], [])
        self.assertEqual(report['terrain']['used'], 1)
        self.assertEqual(report['terrain']['moved'], 1)

    def test_trunk_never_stands_on_the_roof(self):
        placed, _ = self.plan(blocks=self.roof(y=self.GROUND + 40))
        self.assertTrue(all(p[1] <= self.GROUND + 40 for p in placed))
        for p in self.logs(placed):
            self.assertLess(p[1], self.GROUND + 40)

    def test_crown_overlapping_a_feature_roof_keeps_the_roof_and_drops_those_leaves(self):
        roof = {(x, self.GROUND + 4, z): 'minecraft:red_concrete' for x in range(12, 25) for z in range(5, 16)}
        owned = {(x, y, z) for (x, y, z) in roof}
        placed, report = self.plan(blocks=roof, owned=owned)
        self.assertTrue(self.logs(placed))
        self.assertFalse(set(placed) & set(roof))
        self.assertTrue(any(p[0] >= 12 for p in placed))             # leaves above and beside it still fill air
        self.assertTrue(all(p[0] < 12 or p[1] != self.GROUND + 4 for p in placed))

    def test_trunk_column_blocked_by_a_feature_block_is_unplaced_feature_above_terrain(self):
        for y in (self.GROUND + 1, self.GROUND + 3):
            with self.subTest(y=y):
                owned = {(10, y, 10)}
                placed, report = self.plan(blocks={(10, y, 10): 'minecraft:red_concrete'}, owned=owned)
                self.assertEqual(report['skipped'][0][-1], 'feature above terrain')
                self.assertFalse(placed)

    def test_leaves_fill_air_beside_a_wall(self):
        wall = {(13, y, z): 'minecraft:bricks' for y in range(self.GROUND + 1, self.GROUND + 12) for z in range(0, 25)}
        placed, report = self.plan(blocks=wall, owned=set(wall))
        self.assertEqual(report['skipped'], [])
        self.assertTrue(any(p[0] == 12 and 'leaves' in b for p, b in placed.items()))   # beside the wall
        self.assertFalse(set(placed) & set(wall))

    def test_owned_and_masked_blocks_are_never_overwritten(self):
        owned = {(x, self.GROUND + 4, 10) for x in range(5, 16)}
        masked = {(x, self.GROUND + 5, 9): 'decor 0,0' for x in range(5, 16)}
        placed, _ = self.plan(owned=owned, masked=masked, crown=(10, 8, 3.))
        self.assertFalse(set(placed) & owned)
        self.assertFalse(set(placed) & set(masked))

    def test_floating_base_is_no_ground_at_terrain(self):
        # terrain says 60 but the block at 60 is air and the live surface floats at 63
        blocks = {(10, 60, 10): 'minecraft:air', (10, 63, 10): 'minecraft:grass_block'}
        placed, report = self.plan(blocks=blocks)
        self.assertFalse(placed)
        self.assertEqual(report['skipped'][0][-1], 'no ground at terrain')

    def test_feature_owned_block_at_terrain_is_feature_above_terrain_and_never_ground(self):
        placed, report = self.plan(owned={(10, self.GROUND, 10)})
        self.assertFalse(placed)
        self.assertEqual(report['skipped'][0][-1], 'feature above terrain')

    def test_missing_terrain_keeps_todays_live_surface_and_is_reported(self):
        for terrain, x0 in ((None, 0), (0, 0), (60, 500)):          # no map, zero terrain, column outside the map
            with self.subTest(terrain=terrain, x0=x0):
                with mock.patch.object(tl, 'surface_y', lambda w, x, z: (self.ROOF, 'red_concrete')):
                    placed, report = self.plan(terrain=terrain, terrain_x0=x0, blocks={(10, self.ROOF, 10): 'minecraft:red_concrete'})
                self.assertEqual(min(p[1] for p in self.logs(placed)), self.ROOF + 1)
                self.assertEqual(report['terrain'], {'used': 0, 'moved': 0, 'fallback': 1})

    def test_water_and_carriageway_read_the_block_at_terrain(self):
        pond = {(x, self.GROUND, z): 'minecraft:water' for x in range(8, 13) for z in range(8, 13)}
        placed, report = self.plan(blocks={**pond, **self.roof(y=self.ROOF)})
        self.assertFalse(placed)
        self.assertIn('water', report['skipped'][0][-1])
        # a tarmac roof deck at 70 does not matter; the road set decides the carriageway
        placed, report = self.plan(blocks=self.roof(), road={(10, 10)}, source='detector')
        self.assertEqual(report['skipped'][0][-1], 'carriageway')
        # live surface is a roof, terrain is lawn: detector material rule reads the terrain block
        placed, report = self.plan(blocks=self.roof(y=self.ROOF + 5), source='detector')
        self.assertEqual(report['skipped'][0][-1] if report['skipped'] else None, None)
        placed, report = self.plan(blocks={(10, self.GROUND, 10): 'minecraft:stone'}, source='detector')
        self.assertEqual(report['skipped'][0][-1], 'surface stone')

    def test_planning_inputs_loads_the_terrain_once(self):
        hm = {'x0': 0, 'z0': 0, 'terrain': np.full((128, 128), 60)}
        osm = types.ModuleType('osm_footprints')
        osm.OSMIndex = lambda: types.SimpleNamespace(cells=lambda: set(), areas=[])
        with mock.patch.object(tl, 'canopy_grid', lambda c: np.zeros((128, 128))), \
                mock.patch.object(tl, 'cover_grid', lambda c: np.zeros((128, 128))), \
                mock.patch('tree_lines.standing_height', lambda c, margin=0: (None, 'none')), \
                mock.patch.object(tl, 'cell_terrain', lambda c: hm) as ct, \
                mock.patch.object(tl, 'neighbour_owned', lambda cell, exclude=(): set()), \
                mock.patch.object(tl, 'ignored_builds', lambda cell: set()), \
                mock.patch.object(tl, 'load_masked_positions', lambda: {}), \
                mock.patch.dict(sys.modules, {'osm_footprints': osm}), \
                mock.patch('cell_registry.view_registry', lambda: types.SimpleNamespace(recorded=lambda n: None)):
            inputs = tl.planning_inputs(CELL, osm_ground=_empty_osm_ground())
        self.assertIs(inputs.terrain, hm)

    def test_cell_terrain_is_none_when_lidar_is_unavailable(self):
        with mock.patch('lidar_heightmap.reference_world', side_effect=RuntimeError('no lidar')):
            self.assertIsNone(tl.cell_terrain((0, 0)))

    def test_cell_terrain_warns_once_per_call_when_lidar_is_unavailable(self):
        import io
        from contextlib import redirect_stderr
        err = io.StringIO()
        with mock.patch('lidar_heightmap.reference_world', side_effect=RuntimeError('no lidar')), \
                redirect_stderr(err):
            tl.cell_terrain((3, 4))
        self.assertIn('no lidar terrain for cell (3, 4)', err.getvalue())
        self.assertIn('no lidar', err.getvalue())

    def test_terrain_summary_line_names_used_moved_and_fallback(self):
        line = tl.terrain_summary({'terrain': {'used': 5, 'moved': 2, 'fallback': 1}})
        self.assertEqual(line, '  terrain ground: 5 used, 2 moved under a roof, 1 fell back to the live surface')


class TestDryRunUndo(unittest.TestCase):
    def test_built_cell_and_empty_plan_preserve_apply_artifacts(self):
        import io
        import json
        from contextlib import redirect_stdout
        from layer_reports import artifact_path
        from pass_mask import build_mask, write_mask
        from site_kit import Plan, full
        from PIL import Image

        class Ground:
            def __init__(self, blocks=None):
                self.blocks = {p: full(b) for p, b in (blocks or {}).items()}

            def block(self, x, y, z):
                return self.blocks.get((x, y, z), 'minecraft:grass_block' if y <= 60 else 'minecraft:air')

        for crowns in ([(64, 64, 3.)], []):
            with self.subTest(crowns=crowns), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                masks = directory / 'masks'
                masks.mkdir()
                inputs = tl.TreeInputs(crowns, len(crowns), set(), set(), set(), None, {}, set())
                expected, _ = tl.plan_cell((0, 0), Plan('empty'), Ground(), inputs)
                write_mask(build_mask('trees', (0, 0), 'old', expected, lambda p: True), masks)
                name = tl.plan_name(0, 0)
                originals = {directory / f'{name}-{suffix}': b'last apply' for suffix in
                             ('report.json', 'preview.png', 'crowns.png', 'plan.json')}
                for path, data in originals.items():
                    path.write_bytes(data)
                with mock.patch('pass_mask.MASK_DIR', masks), \
                        mock.patch.object(tl, 'planning_inputs', return_value=inputs), \
                        mock.patch('site_kit.fresh_planning_world', return_value=Ground(expected)), \
                        mock.patch('aerial_coverage.require_coverage'), \
                        mock.patch('aerial_coverage.cell_image', return_value=Image.new('RGB', (128, 128))), \
                        mock.patch('layer_reports.artifact_path', side_effect=lambda n, a, **kw:
                            artifact_path(n, a, **{**kw, 'directory': directory})), \
                        mock.patch('site_kit.apply', return_value={}) as apply, \
                        mock.patch('site_kit.preview', wraps=__import__('site_kit').preview) as preview, \
                        redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(tl.run((0, 0)), 0)
                report = json.loads((directory / f'{name}-dry-report.json').read_text())
                self.assertEqual(report['placement_positions'], len(expected))
                self.assertEqual(report['skipped'], [])
                for suffix in ('report.json', 'plan.json', 'crowns.png'):
                    self.assertTrue((directory / f'{name}-dry-{suffix}').exists())
                if expected:
                    self.assertTrue((directory / f'{name}-dry-preview.png').exists())
                    apply.assert_called_once()
                else:
                    preview.assert_not_called()
                    apply.assert_not_called()
                    self.assertIn('no preview written', output.getvalue())
                for path, data in originals.items():
                    self.assertEqual(path.read_bytes(), data)

    def test_apply_still_writes_unsuffixed_report(self):
        import io
        from contextlib import redirect_stdout
        from layer_reports import artifact_path
        from site_kit import Plan
        placed, report = tl.plan_cell((0, 0), Plan('empty'), FakeWorld(),
                                     tl.TreeInputs([], 0, set(), set(), set(), None, {}, set()))
        placed = {(10, 61, 10): 'oak_log'}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict('os.environ', {'SITE_OWNER': 'unit-test'}), \
                mock.patch('readiness_evidence.require_ready'), \
                mock.patch('site_kit.fresh_planning_world', return_value=FakeWorld()) as fresh, \
                mock.patch.object(tl, 'plan_cell', return_value=(placed, report)), \
                mock.patch('site_kit.apply', return_value={'planned': 1, 'mismatches': 0}), \
                mock.patch('layer_reports.artifact_path', side_effect=lambda n, a, **kw:
                    artifact_path(n, a, **{**kw, 'directory': Path(tmp)})), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(tl.apply_cell((0, 0)), 0)
            self.assertEqual([p.name for p in Path(tmp).iterdir()], [tl.plan_name(0, 0) + '-report.json'])
            fresh.assert_called_once_with(strict=True)   # plans from saved regions, not the live store


def _box_oracle(a, k):
    """The shifted-copy box mean ``_box`` used to compute. Test oracle only."""
    pad = np.pad(a, k // 2, mode='edge')
    h, w = a.shape
    return sum(pad[i:i + h, j:j + w] for i in range(k) for j in range(k)) / (k * k)


def _threshold_masks(values):
    """The comparisons callers apply to a box mean."""
    return (values > 0.5, values > 0.999, values > 1e-6, values >= 0.035)


def _canopy_oracle(img_path):
    """``canopy_pixels`` with the shifted-copy box, for mask equality."""
    from PIL import Image
    a = np.asarray(Image.open(img_path).convert('RGB')).astype(float) / 255
    r, g = a[..., 0], a[..., 1]
    mx, mn = a.max(2), a.min(2)
    d = mx - mn
    s = np.where(mx > 0, d / np.maximum(mx, 1e-9), 0)
    h = np.zeros_like(mx)
    m = d > 0
    b = a[..., 2]
    idx = m & (mx == r)
    h[idx] = ((g - b)[idx] / d[idx]) % 6
    idx = m & (mx == g)
    h[idx] = ((b - r)[idx] / d[idx]) + 2
    idx = m & (mx == b)
    h[idx] = ((r - g)[idx] / d[idx]) + 4
    h *= 60
    lum = a.mean(2)
    mu = _box_oracle(lum, 7)
    sd = np.sqrt(np.maximum(_box_oracle(lum ** 2, 7) - mu ** 2, 0))
    fm = img_path.with_name(img_path.stem + '-mask.png')
    filled = np.asarray(Image.open(fm).convert('L')) > 0 if fm.exists() else np.zeros(sd.shape, bool)
    mask = (sd >= np.where(filled, 0.0, 0.035)) & (g >= r * 0.93) & (s >= 0.14) & (mx <= 0.85) & (h >= 38) & (h <= np.where(filled, 200, 140))
    dil = _box_oracle(mask.astype(float), 9) > 1e-6
    return _box_oracle(dil.astype(float), 9) > 0.999


def _cover_oracle(img_path):
    """``cover_pixels`` with the shifted-copy box, for mask equality."""
    from PIL import Image
    a = np.asarray(Image.open(img_path).convert('RGB')).astype(float) / 255
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mx, mn = a.max(2), a.min(2)
    s = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9), 0)
    mask = (s >= 0.22) & (mx <= 0.62) & (g > b + 0.02) & (g >= r * 0.8)
    return _box_oracle(_box_oracle(mask.astype(float), 5), 5) > 0.5


class TestBoxFilter(unittest.TestCase):
    """The running-sum box matches the shifted-copy mean, including thresholds."""

    def arrays(self):
        rng = np.random.default_rng(345)
        edge = np.zeros((40, 50))
        edge[0, :] = 1
        edge[-1, :] = 1
        edge[:, 0] = 1
        edge[:, -1] = 1
        edge[0, 0] = 5
        edge[-1, -1] = -3
        edge[3:9, 4:11] = 0.2
        return {
            'random': rng.random((80, 70)),
            'smaller_than_window': rng.random((20, 15)),
            'edge': edge,
            'binary': rng.integers(0, 2, size=(36, 28)).astype(float),
        }

    def test_running_sum_matches_shifted_copies_and_thresholds(self):
        for name, array in self.arrays().items():
            for k in (5, 7, 9, 35):
                with self.subTest(array=name, k=k):
                    got = tl._box(array, k)
                    old = _box_oracle(array, k)
                    self.assertEqual(got.shape, array.shape)
                    self.assertTrue(np.allclose(got, old, rtol=1e-9, atol=1e-12))
                    for new_mask, old_mask in zip(_threshold_masks(got), _threshold_masks(old)):
                        self.assertTrue(np.array_equal(new_mask, old_mask))
                    if name == 'binary':
                        self.assertTrue(np.array_equal(got, old))

    def test_canopy_and_cover_masks_match_the_shifted_box(self):
        from PIL import Image
        rng = np.random.default_rng(345)
        img = np.zeros((48, 72, 3), np.uint8)
        canopy = rng.integers(15, 70, size=(48, 36, 3), dtype=np.uint8)
        canopy[..., 1] = np.clip(canopy[..., 0].astype(int) + 45, 0, 180)
        canopy[..., 2] = 25
        img[:, :36] = canopy
        img[:, 36:54] = (150, 40, 40)
        img[:, 54:] = (90, 80, 40)
        img[0, :] = (255, 255, 255)
        img[-1, :] = (0, 0, 0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'mosaic.png'
            Image.fromarray(img).save(path)
            self.assertTrue(np.array_equal(tl.canopy_pixels(path), _canopy_oracle(path)))
            self.assertTrue(np.array_equal(tl.cover_pixels(path), _cover_oracle(path)))
            self.assertGreater(tl.canopy_pixels(path)[:, :30].mean(), 0.5)
            self.assertEqual(tl.cover_pixels(path)[:, 36:54].mean(), 0)


class TestSpeciesMix(unittest.TestCase):
    def test_seeded_species_mix_and_config_order(self):
        from collections import Counter
        weights = {'oak': 60, 'birch': 25, 'dark_oak': 15}
        reversed_weights = dict(reversed(list(weights.items())))
        positions = [(i % 128, i // 128) for i in range(10000)]
        species = [tl.choose_species((0, 0), x, z, weights) for x, z in positions]
        self.assertEqual(species, [tl.choose_species((0, 0), x, z, reversed_weights)
                                   for x, z in positions])
        self.assertNotEqual(species[:128], [tl.choose_species((1, 0), x, z, weights)
                                           for x, z in positions[:128]])
        counts = Counter(species)
        for name, weight in weights.items():
            self.assertAlmostEqual(counts[name] / len(species), weight / 100, delta=.02)

    def test_oak_only_matches_pre_change_voxel_fixtures(self):
        import hashlib
        import json
        # Captured from the original voxels_for on e521d10; tests do not recalculate an oracle.
        fixtures = {
            1.5: 'bf07ab7e73f8d913c2e18a8f8b578b5dc846f7af746b203a74a6a24b23b1da8b',
            2.5: '4ff47eb48b5141322591c4c87ec774c93a68c98a54b2141d3084d45b32e72c8e',
            4.5: '515b87f1ee526294c4dff421730ec167ab6d2c8842fab206d358665b2610e11d',
            6.0: '1d7455b786b435b24f978175b3b02d2afe95e560da7e6bde91d4c51426c0045f',
        }
        for radius, expected in fixtures.items():
            with self.subTest(radius=radius):
                species = tl.choose_species((0, 0), 20, 24, {'oak': 100})
                vox = tl.voxels_for(20, 24, 60, radius, species=species)
                payload = json.dumps([[*p, b] for p, b in sorted(vox.items())], separators=(',', ':'))
                self.assertEqual(hashlib.sha256(payload.encode()).hexdigest(), expected)
                self.assertNotIn('birch_log', vox.values())

    def test_each_species_crown_is_connected_and_trunks_start_at_ground(self):
        silhouettes = {}
        for species in ('oak', 'birch', 'dark_oak'):
            for seed_x in range(10, 20):
                with self.subTest(species=species, x=seed_x):
                    vox = tl.voxels_for(seed_x, 24, 60, 4., species=species)
                    self.assertTrue(all(p[1] > 60 for p in vox))
                    logs = {p for p, b in vox.items() if 'log' in b}
                    roots = {(x, z) for x, _y, z in logs}
                    for x, z in roots:
                        self.assertIn((x, 61, z), logs)
                    connected = set()
                    frontier = [next(iter(logs))]
                    while frontier:
                        p = frontier.pop()
                        if p in connected:
                            continue
                        connected.add(p)
                        for axis in range(3):
                            for delta in (-1, 1):
                                q = list(p)
                                q[axis] += delta
                                q = tuple(q)
                                if q in vox and q not in connected:
                                    frontier.append(q)
                    self.assertEqual(connected, set(vox), 'every leaf reaches the grounded trunk')
                    if seed_x == 10:
                        silhouettes[species] = {(x - seed_x, y - 60, z - 24) for x, y, z in vox}
                    self.assertTrue(all(b.startswith(species + '_') for b in vox.values()))
        self.assertNotEqual(silhouettes['oak'], silhouettes['birch'])
        self.assertNotEqual(silhouettes['birch'], silhouettes['dark_oak'])

    def plan_species(self, crowns, classes, weights, *, footprints=(), terrain=None, road=(), owned=(), masked=None):
        from site_kit import Plan
        inputs = tl.TreeInputs(crowns, 0, set(), set(), set(owned), None, masked or {}, set(footprints),
                               land_classes=classes, terrain=terrain, road=set(road))
        class Ground:
            def block(self, x, y, z):
                height = 60 if terrain is None else int(terrain['terrain'][x, z])
                return 'minecraft:grass_block' if y <= height else 'minecraft:air'
        with mock.patch.object(tl, 'surface_y', return_value=(60, 'grass_block')), \
                mock.patch.object(tl, 'warm_surface_probe'), \
                mock.patch('land_use.tree_species_weights', side_effect=lambda cid: weights.get(cid, {'oak': 1})):
            return tl.plan_cell((0, 0), Plan('species'), Ground(), inputs)

    def test_species_report_and_existing_mask_round_trip(self):
        from pass_mask import build_mask, mask_placements, plan_undo
        crowns = [(20, 20, 3.), (60, 60, 3.), (100, 100, 3.)]
        classes = {(20, 20): 'natural', (60, 60): 'park', (100, 100): 'rural'}
        weights = {'natural': {'birch': 1}, 'park': {'dark_oak': 1}, 'rural': {'oak': 1}}
        placed, report = self.plan_species(crowns, classes, weights)
        self.assertEqual(report['skipped'], [])
        self.assertEqual(report['species'], {'birch': 1, 'dark_oak': 1, 'oak': 1})
        self.assertEqual([row['species'] for row in report['tree_species']], ['birch', 'dark_oak', 'oak'])
        self.assertEqual(placed, self.plan_species(crowns, classes, weights)[0])
        mask = tl.annotate_mask_species(build_mask('trees', (0, 0), 'species', placed, lambda p: True),
                                        report['species'])
        self.assertEqual(mask['species'], report['species'])
        decoded = mask_placements(mask)
        self.assertEqual(decoded, placed)
        removals, conflicts = plan_undo(mask, decoded.get)
        self.assertEqual(conflicts, [])
        self.assertEqual(set(removals), set(placed))
        for species in report['species']:
            self.assertIn(species + '_log', mask['palette'])

    def test_missing_class_evidence_and_oak_only_config_preserve_old_plan(self):
        crown = (20, 24, 4.5)
        for classes, weights in (({}, {}), ({(20, 24): 'natural'}, {'natural': {'oak': 100}})):
            with self.subTest(classes=classes):
                placed, report = self.plan_species([crown], classes, weights)
                self.assertEqual(placed, tl.voxels_for(20, 24, 60, 4.5))
                self.assertEqual(report['species'], {'oak': 1})

    def test_bushes_stay_out_of_the_species_mix(self):
        placed, report = self.plan_species([(20, 24, 1.5)], {(20, 24): 'natural'},
                                           {'natural': {'birch': 1}})
        self.assertTrue(placed)
        self.assertEqual(report['species'], {})
        self.assertEqual(report['tree_species'], [])
        self.assertTrue(all('log' not in block for block in placed.values()))

    def test_wide_species_respects_all_root_feature_columns(self):
        placed, report = self.plan_species([(20, 24, 3.)], {(20, 24): 'natural'},
                                           {'natural': {'dark_oak': 1}}, footprints={(21, 24)})
        self.assertEqual(placed, {})
        self.assertEqual(report['skipped'][0][-1], 'feature zone')

    def test_wide_species_requires_every_root_at_the_same_terrain_height(self):
        hm = {'x0': 0, 'z0': 0, 'terrain': np.full((128, 128), 60)}
        hm['terrain'][21, 24] = 59
        placed, report = self.plan_species([(20, 24, 3.)], {(20, 24): 'natural'},
                                           {'natural': {'dark_oak': 1}}, terrain=hm)
        self.assertEqual(placed, {})
        self.assertEqual(report['skipped'][0][-1], 'uneven trunk ground')

    def test_wide_species_secondary_roots_respect_ownership_and_roads(self):
        for inputs, reason in (({'road': {(21, 24)}}, 'carriageway'),
                               ({'owned': {(21, 61, 24)}}, 'feature above terrain'),
                               ({'masked': {(21, 61, 24): 'decor 0,0'}}, 'trunk blocked')):
            with self.subTest(inputs=inputs):
                placed, report = self.plan_species([(20, 24, 3.)], {(20, 24): 'natural'},
                                                   {'natural': {'dark_oak': 1}}, **inputs)
                self.assertEqual(placed, {})
                self.assertEqual(report['skipped'][0][-1], reason)


if __name__ == '__main__':
    unittest.main()
