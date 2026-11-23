"""Tests for feature roof zones seams."""

from __future__ import annotations

from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
import io
import json
import tempfile
import unittest

import numpy as np

from feature_build_fixtures import (
    FloatDsmFixture,
    PitchedZonesFixture,
    _StoneWorld,
    start_lidar_fixture as setUpModule,
    stop_lidar_fixture as tearDownModule,
)
import feature_build as F
import feature_roof_zones as FZ
import feature_spec as FS
import roof_geometry as RG


class ZoneMathTest(unittest.TestCase):
    """Seam: zone snapping, bucketing, and edge-wall exposure on a fixture grid."""

    def test_snap_to_levels_nearest_plateau(self):
        h = np.array([[40.2, 44.8], [46.1, 49.7]], dtype=float)
        got = FZ.snap_to_levels(h, [41, 45, 46, 50])
        self.assertEqual(got.tolist(), [[41.0, 45.0], [46.0, 50.0]])

    def test_bucket_zones_one_level_per_rotated_cell(self):
        # 8×4 footprint on axis 0: two 4-wide buckets along x should each take a mode
        zone = np.zeros((4, 8), dtype=float)
        zone[:, :4] = 41
        zone[:, 4:] = 50
        zone[1, 3] = 50  # speck in the left bucket → mode stays 41
        zone[2, 5] = 41  # speck in the right bucket → mode stays 50
        mask = np.ones_like(zone, dtype=bool)
        zz, xx = np.mgrid[0:4, 0:8]
        out = FZ.bucket_zones(zone, mask, xx, zz, bucket=4, axis_deg=0.0)
        self.assertTrue(np.all(out[:, :4] == 41))
        self.assertTrue(np.all(out[:, 4:] == 50))

    def test_edge_wall_expose_exterior_and_zone_step(self):
        # Three columns: downhill ground 40, mid roof 50, high roof 54; outside east
        roof = {(10, 20): 50, (11, 20): 54}
        ground = {
            (9, 20): 40, (10, 20): 42, (11, 20): 42, (12, 20): 42,
            (10, 19): 42, (10, 21): 42, (11, 19): 42, (11, 21): 42,
        }
        # West neighbour of (10,20) is outside → top is ground 40
        self.assertEqual(FZ.edge_wall_expose(roof, ground, (10, 20)), 40)
        # East neighbour of (10,20) is higher roof 54; west outside 40 → expose 40
        # Zone step: from high roof looking west onto 50
        self.assertEqual(FZ.edge_wall_expose(roof, ground, (11, 20)), 42)

    def test_assign_roof_zones_pipeline_square_snap_bucket(self):
        # 6×6 vector outline with a 1-block stair notch; two DSM plateaus
        outline = np.ones((6, 6), dtype=bool)
        outline[0, 5] = False  # notch / spur
        dsm = np.full((6, 6), 41.0)
        dsm[:, 3:] = 50.0
        zz, xx = np.mgrid[0:6, 0:6]
        readable, zone, levels, axis, _dropped = FZ.assign_roof_zones(
            dsm, outline, [41, 50], xx, zz, bucket=3, axis_deg=0.0,
        )
        self.assertEqual(levels, [41, 50])
        self.assertEqual(axis, 0.0)
        self.assertGreaterEqual(int(readable.sum()), int(outline.sum()) - 2)
        self.assertIn(41.0, set(zone[readable].tolist()))
        self.assertIn(50.0, set(zone[readable].tolist()))
        line = FZ.footprint_pad_line(outline, readable)
        self.assertTrue(line.startswith('footprint osm='))
        self.assertIn('readable=', line)
        self.assertIn('pad=', line)
        self.assertIn('cover=', line)

    def test_regions_from_the_aerial_set_the_zones_and_lidar_sets_their_levels(self):
        # A hipped front (rises from 30 m at the eave to 34 m) beside a flat rear deck at 26 m.
        # Lidar alone would blend the rear into the hip's low edge; the image splits them.
        outline = np.ones((12, 20), dtype=bool)
        zz, xx = np.mgrid[0:12, 0:20]
        dsm = np.full((12, 20), 26.0)
        front = xx < 10
        dsm[front] = 30.0 + 4.0 * (1 - np.abs(zz[front] - 5.5) / 6.0)
        regions = [
            [(0, 0), (10, 0), (10, 12), (0, 12)],
            [(10, 0), (20, 0), (20, 12), (10, 12)],
        ]
        readable, zone, levels, _axis, _dropped = FZ.assign_roof_zones(
            dsm, outline, 'auto', xx, zz, bucket=4, axis_deg=0.0, regions=regions,
        )
        rear = readable & ~front
        self.assertEqual(set(zone[rear].tolist()), {26.0})
        self.assertEqual(len(set(zone[readable & front].tolist())), 1)
        eave = zone[readable & front][0]
        self.assertGreaterEqual(eave, 30.0)
        self.assertLess(eave, 32.0)
        self.assertIn(26.0, levels)
        self.assertIn(eave, levels)

    def _hip_with_dead_auto_plateau(self):
        # A 4 m hip over a 30 m eave. Lidar finds auto plateaus between 30 and 34 m (the hip's flank) that
        # the region then absorbs into one 30 m zone, beside a flat 26 m deck.
        outline = np.ones((12, 20), dtype=bool)
        zz, xx = np.mgrid[0:12, 0:20]
        dsm = np.full((12, 20), 26.0)
        front = xx < 10
        dsm[front] = 30.0 + 4.0 * (1 - np.abs(zz[front] - 5.5) / 6.0)
        regions = [
            [(0, 0), (10, 0), (10, 12), (0, 12)],
            [(10, 0), (20, 0), (20, 12), (10, 12)],
        ]
        readable, zone, levels, _axis, _dropped = FZ.assign_roof_zones(
            dsm, outline, 'auto', xx, zz, bucket=4, axis_deg=0.0, regions=regions,
        )
        return dsm, front, regions, readable, zone, levels

    def test_auto_levels_unused_after_regions_do_not_clip_the_ridge(self):
        dsm, front, regions, readable, zone, levels = self._hip_with_dead_auto_plateau()
        dead = [v for v in levels if v not in {float(z) for z in zone[readable].tolist()}]
        self.assertTrue(dead, 'the lidar found plateaus the regions absorbed')
        eave = float(zone[readable & front][0])
        want = int(2 * (float(dsm[readable & front].max()) - eave)) - 1
        clipped = FZ.zone_ridge_half_steps(dsm, zone, readable, flat_tol_m=0.8, levels=levels)
        self.assertLess(int(clipped[readable & front].max()), want)   # the old behaviour: the dead levels clip
        used = FZ.ridge_clip_levels('auto', regions, zone, readable, levels)
        self.assertEqual(used, sorted({float(v) for v in zone[readable].tolist()}))
        half = FZ.zone_ridge_half_steps(dsm, zone, readable, flat_tol_m=0.8, levels=used)
        self.assertGreaterEqual(int(half[readable & front].max()), want)

    def test_full_level_list_stays_known_for_material_regions(self):
        # material_regions[].level may still name an auto plateau a region absorbed.
        _dsm, _front, regions, readable, zone, levels = self._hip_with_dead_auto_plateau()
        dead = [v for v in levels if v not in {float(z) for z in zone[readable].tolist()}]
        self.assertTrue(set(dead) <= set(levels))
        self.assertNotEqual(FZ.ridge_clip_levels('auto', regions, zone, readable, levels), levels)

    def test_explicit_levels_or_no_regions_keep_the_full_clip_list(self):
        _dsm, _front, regions, readable, zone, levels = self._hip_with_dead_auto_plateau()
        self.assertEqual(FZ.ridge_clip_levels([26, 30, 34], regions, zone, readable, levels), levels)
        self.assertEqual(FZ.ridge_clip_levels('auto', None, zone, readable, levels), levels)

    def test_surface_fit_cleans_noise_per_region_and_leaves_other_cells_alone(self):
        # A noisy gable (ridge across the axis, 30 -> 34 -> 30 m) beside a noisy flat deck at 26 m.
        rng = np.random.default_rng(11)
        outline = np.ones((8, 24), dtype=bool)
        zz, xx = np.mgrid[0:8, 0:24]
        gable = xx < 12
        dsm = np.where(gable, 34.0 - 0.7 * np.abs(xx - 6.0), 26.0) + rng.normal(0, 0.3, (8, 24))
        regions = [[(0, 0), (12, 0), (12, 8), (0, 8)], [(12, 0), (24, 0), (24, 8), (12, 8)]]
        readable, zone, _levels, _axis, _dropped = FZ.assign_roof_zones(
            dsm, outline, 'auto', xx, zz, bucket=4, axis_deg=0.0, regions=regions,
        )
        clean, lines = FZ.fit_clean_heights(dsm, zone, readable, xx.astype(float), zz.astype(float), 0.0, regions, 'a')
        deck = readable & ~gable
        self.assertLess(float(np.ptp(clean[deck])), 0.01)                       # noise gone: one level
        self.assertAlmostEqual(float(clean[deck][0]), 26.0, delta=0.3)
        ridge = float(clean[readable & gable].max())
        self.assertAlmostEqual(ridge, 34.0, delta=0.5)
        self.assertEqual(len(lines), 2)
        self.assertIn('gable ridge across the axis', lines[0])
        self.assertIn('flat', lines[1])
        outside = ~readable
        self.assertTrue(np.array_equal(clean[outside], dsm[outside]))           # untouched

    def test_surface_fit_keeps_raw_heights_when_no_shape_fits_and_says_so(self):
        # A stepped roof (30 m, then 33 m) inside one region: no flat/plane/gable explains it.
        outline = np.ones((8, 12), dtype=bool)
        zz, xx = np.mgrid[0:8, 0:12]
        dsm = np.where(xx < 7, 30.0, 33.0)
        regions = [[(0, 0), (12, 0), (12, 8), (0, 8)]]
        readable, zone, _levels, _axis, _dropped = FZ.assign_roof_zones(
            dsm, outline, 'auto', xx, zz, bucket=4, axis_deg=0.0, regions=regions,
        )
        clean, lines = FZ.fit_clean_heights(dsm, zone, readable, xx.astype(float), zz.astype(float), 0.0, regions, 'a')
        self.assertTrue(np.array_equal(clean, dsm))
        self.assertIn('surface fit skipped', lines[0])

    def test_surface_fit_ignores_nan_columns_in_a_group(self):
        outline = np.ones((8, 12), dtype=bool)
        zz, xx = np.mgrid[0:8, 0:12]
        dsm = np.full((8, 12), 28.0)
        regions = [[(0, 0), (12, 0), (12, 8), (0, 8)]]
        readable, zone, _levels, _axis, _dropped = FZ.assign_roof_zones(
            dsm, outline, 'auto', xx, zz, bucket=4, axis_deg=0.0, regions=regions,
        )
        dsm[2:4, 3:6] = np.nan
        clean, lines = FZ.fit_clean_heights(dsm, zone, readable, xx.astype(float), zz.astype(float), 0.0, regions, 'a')
        self.assertTrue(np.allclose(clean[readable], 28.0))
        self.assertIn('flat', lines[0])

    def test_surface_fit_without_regions_fits_each_zone_level(self):
        outline = np.ones((6, 12), dtype=bool)
        zz, xx = np.mgrid[0:6, 0:12]
        rng = np.random.default_rng(2)
        dsm = np.where(xx < 6, 41.0, 50.0).astype(float) + rng.normal(0, 0.2, xx.shape)
        readable, zone, _levels, _axis, _dropped = FZ.assign_roof_zones(
            dsm, outline, [41, 50], xx, zz, bucket=3, axis_deg=0.0,
        )
        clean, lines = FZ.fit_clean_heights(dsm, zone, readable, xx.astype(float), zz.astype(float), 0.0, None, 'a')
        self.assertEqual(len(lines), 2)
        self.assertLess(float(np.ptp(clean[readable & (xx < 6)])), 0.01)      # noise replaced by one level
        self.assertLess(float(np.ptp(clean[readable & (xx >= 6)])), 0.01)
        self.assertAlmostEqual(float(clean[readable & (xx < 6)][0]), 41.0, delta=0.3)

    def test_region_levels_are_whole_metres_and_merge_when_equal(self):
        # Two regions whose lidar eaves are 30.3 and 29.8 are the same 30 m zone, not a fractional step.
        outline = np.ones((8, 16), dtype=bool)
        zz, xx = np.mgrid[0:8, 0:16]
        dsm = np.where(xx < 8, 30.3, 29.8)
        regions = [[(0, 0), (8, 0), (8, 8), (0, 8)], [(8, 0), (16, 0), (16, 8), (8, 8)]]
        readable, zone, levels, _axis, _dropped = FZ.assign_roof_zones(
            dsm, outline, 'auto', xx, zz, bucket=4, axis_deg=0.0, regions=regions,
        )
        self.assertEqual(set(zone[readable].tolist()), {30.0})
        self.assertTrue(all(float(level).is_integer() for level in levels))

    def test_cells_outside_every_region_keep_their_lidar_zone(self):
        outline = np.ones((6, 12), dtype=bool)
        zz, xx = np.mgrid[0:6, 0:12]
        dsm = np.where(xx < 6, 41.0, 50.0)
        regions = [[(0, 0), (6, 0), (6, 6), (0, 6)]]
        readable, zone, _levels, _axis, _dropped = FZ.assign_roof_zones(
            dsm, outline, [41, 50], xx, zz, bucket=3, axis_deg=0.0, regions=regions,
        )
        self.assertEqual(set(zone[readable & (xx >= 6)].tolist()), {50.0})
        self.assertEqual(set(zone[readable & (xx < 6)].tolist()), {41.0})

    def test_regions_refuse_overlap_and_empty_polygons(self):
        outline = np.ones((6, 6), dtype=bool)
        zz, xx = np.mgrid[0:6, 0:6]
        dsm = np.full((6, 6), 41.0)
        box = [(0, 0), (4, 0), (4, 6), (0, 6)]
        overlapping = [box, [(3, 0), (6, 0), (6, 6), (3, 6)]]
        with self.assertRaises(SystemExit):
            FZ.assign_roof_zones(dsm, outline, [41], xx, zz, bucket=3, axis_deg=0.0, regions=overlapping)
        empty = [[(50, 50), (60, 50), (60, 60)]]
        with self.assertRaises(SystemExit):
            FZ.assign_roof_zones(dsm, outline, [41], xx, zz, bucket=3, axis_deg=0.0, regions=empty)

    def test_assign_roof_zones_drops_outline_cells_without_lidar_mass(self):
        """#116: zoned readable mask keeps only cells with nDSM ≥ ZONE_LIDAR_MASS_M."""
        outline = np.ones((4, 6), dtype=bool)
        # DSM metres AOD; ground_y chosen so left half is bare (< mass_m) and right has mass.
        dsm = np.full((4, 6), 50.0)
        ground_y = np.full((4, 6), 48)  # nDSM ~2 m after y_to_metres_aod — below default 3 m
        ground_y[:, 3:] = 40            # nDSM ~10 m — keep
        zz, xx = np.mgrid[0:4, 0:6]
        with mock.patch('world_frame.y_to_metres_aod', side_effect=lambda y: float(y)):
            readable, zone, _levels, _axis, _dropped = FZ.assign_roof_zones(
                dsm, outline, [50], xx, zz, bucket=3, axis_deg=0.0, ground_y=ground_y,
            )
        self.assertFalse(readable[:, :3].any(), 'bare-ground half must be dropped')
        self.assertTrue(readable[:, 3:].all(), 'mass half must remain')
        self.assertEqual(int(readable.sum()), 4 * 3)

    def test_drop_small_disconnected_footprint_pieces(self):
        """#153: drop 1- and 2-cell islands; keep a spur that touches the main mass."""
        # Grid [z, x]; world coords xs=col, zs=row.
        # Main 4×4 at rows 2..5, cols 2..5; touching 2-cell spur at cols 6..7 row 2;
        # island 1-cell at (0,0); island 2-cell at col 0 rows 6..7 (gap from main).
        mask = np.zeros((8, 8), dtype=bool)
        mask[2:6, 2:6] = True
        mask[2, 6] = True
        mask[2, 7] = True
        mask[0, 0] = True
        mask[6, 0] = True
        mask[7, 0] = True
        zz, xx = np.mgrid[0:8, 0:8]
        kept, dropped = FZ.drop_small_footprint_pieces(mask, xx, zz)
        self.assertTrue(kept[2:6, 2:6].all(), 'main mass kept')
        self.assertTrue(kept[2, 6] and kept[2, 7], 'touching spur kept')
        self.assertFalse(kept[0, 0], '1-cell island dropped')
        self.assertFalse(kept[6, 0] or kept[7, 0], '2-cell island dropped')
        self.assertEqual(int(kept.sum()), 4 * 4 + 2)
        counts = sorted(d['n'] for d in dropped)
        self.assertEqual(counts, [1, 2])
        for d in dropped:
            self.assertIn('at', d)
            self.assertRegex(
                FZ.dropped_footprint_line('A', d),
                rf'dropped disconnected footprint A n={d["n"]} at ',
            )
        # Through the zone pipeline: mass intersect leaves the same islands.
        outline = np.ones((8, 8), dtype=bool)
        dsm = np.full((8, 8), 50.0)
        ground_y = np.full((8, 8), 48)  # bare (< 3 m) everywhere…
        ground_y[mask] = 40             # …except the intended mass cells
        with mock.patch('world_frame.y_to_metres_aod', side_effect=lambda y: float(y)):
            readable, _zone, _levels, _axis, pipe_dropped = FZ.assign_roof_zones(
                dsm, outline, [50], xx, zz, bucket=3, axis_deg=0.0, ground_y=ground_y,
            )
        self.assertTrue(readable[2:6, 2:6].all())
        self.assertTrue(readable[2, 6] and readable[2, 7])
        self.assertFalse(readable[0, 0])
        self.assertFalse(readable[6, 0] or readable[7, 0])
        self.assertEqual(sorted(d['n'] for d in pipe_dropped), [1, 2])


class ZoneBuildTest(unittest.TestCase):
    """Seam: build() with injected zone grids places parapets and downhill walls."""

    def setUp(self):
        # 6×4 rectangle at x=10..15, z=20..23
        self.cells = {(x, z) for x in range(10, 16) for z in range(20, 24)}
        # [z, x] grids covering x0=9..16, z0=19..24
        self.x0, self.z0 = 9, 19
        depth, width = 6, 8  # z 19..24, x 9..16
        dsm = np.full((depth, width), 45.0)
        dsm[:, 4:] = 50.0  # x >= 13 → higher plateau
        ground = np.full((depth, width), 40)  # block Y
        ground[:, :2] = 38  # downhill on the west fringe
        self.zone_data = {
            'A': {'dsm': dsm, 'ground_y': ground, 'x0': self.x0, 'z0': self.z0},
        }

    def spec(self, edge=None):
        roof = {'kind': 'flat', 'material': 'smooth_stone'}
        if edge is not None:
            roof['edge'] = edge
        return {
            'name': 'unit_zones',
            'ways': {'99': 'A'},
            'storeys': {'A': 3},
            'roofs': {'A': roof},
            'groups': [{'nos': ['A'], 'front': 'S', 'mode': 'each'}],
            'modifies': [],
            'floor_min': 40,
            'step_max': 4,
            'door_frame': True,
            'keep_edits': False,
            'wall': 'bricks',
            'floor': 'smooth_stone',
            'ledge_stair': False,
            'zones': {
                'A': {'from': 'lidar', 'levels': [45, 50], 'bucket': 2, 'axis_deg': 0},
            },
        }

    def _build(self, edge=None):
        buf = io.StringIO()
        with mock.patch.object(FZ, 'metres_aod_to_y', side_effect=lambda m: int(m)), \
                redirect_stdout(buf):
            plan = F.build(
                self.spec(edge=edge), footprints={'A': self.cells}, world=_StoneWorld(),
                zone_data=self.zone_data,
            )
        return plan, buf.getvalue()

    def test_empty_zone_mask_refuses_with_feature_and_thresholds(self):
        for levels in ([45], 'auto'):
            spec = self.spec()
            spec['zones']['A']['levels'] = levels
            data = {'A': {**self.zone_data['A'], 'dsm': np.full((6, 8), 20.0)}}
            with self.assertRaisesRegex(SystemExit, r'A: no zone cells survived.*3-cell minimum.*mass threshold'):
                F.build(spec, footprints={'A': self.cells}, world=_StoneWorld(), zone_data=data)

    def test_single_zone_with_sufficient_lidar_mass_plans(self):
        spec = self.spec()
        spec['zones']['A']['levels'] = [45]
        with redirect_stdout(io.StringIO()):
            plan = F.build(spec, footprints={'A': self.cells}, world=_StoneWorld(), zone_data=self.zone_data)
        self.assertTrue(any(y == 56 and state == 'minecraft:smooth_stone' for (x, y, z), state in plan.cells.items()))

    def test_zoned_flat_roof_resolves_slab_family_to_full_block(self):
        spec = self.spec()
        spec['roofs']['A']['material'] = 'deepslate_tile'
        with redirect_stdout(io.StringIO()):
            plan = F.build(spec, footprints={'A': self.cells}, world=_StoneWorld(), zone_data=self.zone_data)
        self.assertIn('minecraft:deepslate_tiles', plan.cells.values())
        self.assertNotIn('minecraft:deepslate_tile', plan.cells.values())

    def test_zoned_build_default_none_no_parapet(self):
        plan, out = self._build()
        self.assertIn('footprint osm=', out)
        low_roof = [(x, z) for (x, y, z), st in plan.cells.items()
                    if y == 45 and st == 'minecraft:smooth_stone' and (x, z) in self.cells]
        high_roof = [(x, z) for (x, y, z), st in plan.cells.items()
                     if y == 50 and st == 'minecraft:smooth_stone' and (x, z) in self.cells]
        self.assertTrue(low_roof, 'expected 45 m plateau roof cells')
        self.assertTrue(high_roof, 'expected 50 m plateau roof cells')
        parapets = [pos for pos, st in plan.cells.items()
                    if st == 'minecraft:smooth_stone' and pos[1] in (46, 51)]
        self.assertFalse(parapets, 'absent edge must place no parapet')
        west_wall = [
            y for (x, y, z), st in plan.cells.items()
            if x == 10 and z == 21 and 'bricks' in st and y > 40
        ]
        self.assertTrue(west_wall, 'expected exposed wall on west face')

    def test_zoned_edge_all_matches_old_parapet_on_steps(self):
        """where=all: parapet on outer edges and zone steps (legacy behaviour)."""
        plan, _ = self._build(edge={'block': 'smooth_stone', 'where': 'all'})
        parapets = [pos for pos, st in plan.cells.items()
                    if st == 'minecraft:smooth_stone' and pos[1] in (46, 51)]
        self.assertTrue(parapets, 'expected parapet on roof edges')
        # High side of the interior zone drop (bucket shifts the step to x=14)
        self.assertEqual(
            plan.cells.get((14, 51, 21)), 'minecraft:smooth_stone',
            'where=all must edge interior zone steps',
        )

    def test_zoned_edge_outer_skips_zone_steps(self):
        """where=outer: parapet on mass outline only, not between plateaus."""
        plan, _ = self._build(edge={'block': 'polished_andesite', 'where': 'outer'})
        outer = [pos for pos, st in plan.cells.items()
                 if st == 'minecraft:polished_andesite' and pos[1] in (46, 51)]
        self.assertTrue(outer, 'expected outer parapet')
        # High side of interior zone drop: all N4 neighbours still in the roof mass
        self.assertNotEqual(
            plan.cells.get((14, 51, 21)), 'minecraft:polished_andesite',
            'where=outer must skip interior zone steps',
        )
        # Outer west face of the low plateau still edged
        west_outer = [pos for pos in outer if pos[0] == 10 and pos[1] == 46]
        self.assertTrue(west_outer, 'expected outer west parapet on low plateau')


class PitchedZonesRidgeTest(PitchedZonesFixture, unittest.TestCase):
    """#125: pitched+zones → ridge stair-slabs; flat zoned plateaus unchanged."""


    def test_zoned_shell_uses_regularised_heights_for_placement(self):
        half_grid = np.zeros((7, 11), dtype=int)
        half_grid[:, 5:] = 10  # raw five-block cliff inside one roof
        builder = F.Build(
            self._spec('pitched', [48]), footprints={'A': self.cells},
            world=_StoneWorld(), zone_data=self.ridge_zone_data,
        )
        with mock.patch.object(FZ, 'metres_aod_to_y', side_effect=lambda m: int(m)), \
                mock.patch.object(FZ, 'zone_ridge_half_steps', return_value=half_grid), \
                redirect_stdout(io.StringIO()):
            plan = builder.build()
        feature = builder.features[0]
        self.assertLess(feature.zone_roof_half[(14, 22)], 106)
        for (x, z), h in feature.zone_roof_half.items():
            self.assertEqual(feature.zone_roof[(x, z)], RG.roof_block_y(h))
            state = plan.cells[(x, RG.roof_block_y(h), z)]
            self.assertTrue('brick_stairs' in state or 'brick_slab' in state or state.endswith(':bricks'))
        from roof_lint import stair_shape_mismatches
        self.assertEqual(stair_shape_mismatches(plan.cells), [])

    def test_roof_flat_tol_m_reaches_zone_ridge_half_steps(self):
        for roof_extra, want in (({}, FZ.FLAT_TOL_M), ({'flat_tol_m': 1.25}, 1.25)):
            with self.subTest(roof_extra=roof_extra):
                spec = self._spec('pitched', [48])
                spec['roofs']['A'].update(roof_extra)
                builder = F.Build(
                    spec, footprints={'A': self.cells},
                    world=_StoneWorld(), zone_data=self.ridge_zone_data,
                )
                seen = []
                real = FZ.zone_ridge_half_steps

                def spy(*a, **kw):
                    seen.append(kw.get('flat_tol_m'))
                    return real(*a, **kw)

                with mock.patch.object(FZ, 'metres_aod_to_y', side_effect=lambda m: int(m)), \
                        mock.patch.object(FZ, 'zone_ridge_half_steps', side_effect=spy), \
                        redirect_stdout(io.StringIO()):
                    builder.build()
                self.assertEqual(seen, [want])

    def test_zoned_storey_floors_stop_below_eave(self):
        for kind, eave in (('pitched', 45), ('pitched', 46), ('flat', 45), ('flat', 46)):
            with self.subTest(kind=kind, eave=eave):
                plan = self._build(kind, [eave], self.ridge_zone_data, material='brick')
                floors = [pos for pos, state in plan.cells.items()
                          if state == 'minecraft:smooth_stone'
                          and (pos[0], pos[2]) in self.cells]
                self.assertTrue(any(y == 43 for x, y, z in floors), 'lower storey lost its floor')
                self.assertEqual([pos for pos in floors if pos[1] >= eave], [])

    def test_pitched_zones_preserve_distinct_flat_plateau_levels(self):
        builder = F.Build(
            self._spec('pitched', [45, 50]), footprints={'A': self.cells},
            world=_StoneWorld(), zone_data=self.flat_zone_data,
        )
        with mock.patch.object(FZ, 'metres_aod_to_y', side_effect=lambda m: int(m)), \
                mock.patch.object(FZ, 'zone_ridge_half_steps', return_value=np.zeros((7, 11), dtype=int)), \
                redirect_stdout(io.StringIO()):
            pitched = builder.build()
        feature = builder.features[0]
        self.assertEqual({feature.zone_roof[c] for c in feature.cells}, {45, 50})
        for c in feature.cells:
            self.assertEqual(feature.zone_roof[c], feature.zone_level[c])
        # The semantic zone step has an exposed wall, not a noisy roof cliff.
        # Smoothing across it would lower the taller mass toward the low wing.
        high = {pos for pos, st in pitched.cells.items()
                if pos[1] == 50 and st == 'minecraft:bricks' and (pos[0], pos[2]) in self.cells}
        self.assertTrue(high)

    def test_zoned_shell_backs_horizontal_gap_beside_stair(self):
        # Synthetic version of West's reported opening: an east-facing stair
        # at (6165,60,-5631), air in the taller south column at the same Y,
        # and a former full floor at Y59. That floor could not close a
        # horizontal slit through the stair's partly open south upper edge.
        cells = {(x, z) for x in range(6163, 6168) for z in range(-5633, -5628)}
        rows = [
            [120, 120, 120, 120, 120],
            [120, 120, 120, 122, 122],
            [120, 120, 120, 122, 124],
            [120, 120, 122, 124, 124],
            [120, 120, 122, 124, 124],
        ]
        raw = {(6163 + x, -5633 + z): h for z, row in enumerate(rows) for x, h in enumerate(row)}
        halfs = RG.regularise_roof_half_heights(raw)
        builder = F.Build(self._spec('pitched', [48]), footprints={'A': cells}, world=_StoneWorld())
        feature = builder.features[0]
        feature.b = 41
        feature.zone_ground = {c: 41 for c in cells}
        feature.zone_level = {c: 49 for c in cells}
        feature.zone_eave = {c: 49 for c in cells}
        feature.zone_roof_half = halfs
        feature.zone_roof = {c: RG.roof_block_y(h) for c, h in halfs.items()}
        builder.shell_zoned(0, feature)
        self.assertIn('facing=east', builder.p.cells[(6165, 60, -5631)])
        self.assertIn('shape=straight', builder.p.cells[(6165, 60, -5631)])
        self.assertEqual(builder.p.cells[(6165, 59, -5630)], 'minecraft:air')
        self.assertEqual(builder.p.cells[(6165, 60, -5630)], 'minecraft:bricks')
        self.assertIn('brick_stairs', builder.p.cells[(6165, 61, -5630)])

    def test_stair_backing_only_closes_open_edges_inside_taller_columns(self):
        builder = F.Build(self._spec('pitched', [48]), footprints={'A': self.cells}, world=_StoneWorld())
        # Test full versus partial upper-edge coverage for every stair shape.
        closed = {'straight': {'east'}, 'inner_left': {'east', 'north'},
                  'inner_right': {'east', 'south'}, 'outer_left': set(), 'outer_right': set()}
        for shape, solid_edges in closed.items():
            for face, (dx, dz) in RG.ROOF_DELTA.items():
                with self.subTest(shape=shape, face=face):
                    builder.p.cells.clear()
                    builder.p.set(dx, 50, dz, 'air')
                    roof = {(0, 0): 50, (dx, dz): 51}
                    states = {(0, 0): f'brick_stairs[facing=east,half=bottom,shape={shape}]'}
                    builder._back_roof_stair_edges(roof, states)
                    expected = 'minecraft:air' if face in solid_edges else 'minecraft:bricks'
                    self.assertEqual(builder.p.cells[(dx, 50, dz)], expected)
        builder.p.cells.clear()
        builder.p.set(0, 50, 1, 'oak_planks')
        states = {(0, 0): 'brick_stairs[facing=east,half=bottom,shape=straight]'}
        builder._back_roof_stair_edges({(0, 0): 50, (0, 1): 51}, states)
        self.assertEqual(builder.p.cells[(0, 50, 1)], 'minecraft:oak_planks')
        builder.p.cells.clear()
        builder._back_roof_stair_edges({(0, 0): 50}, states)
        self.assertEqual(builder.p.cells, {}, 'backing must not expand outside the roof mass')

    def test_stair_backing_replaces_storey_floor_block_beside_open_half(self):
        # S-087: a smooth_stone storey floor at the stair's Y in the taller
        # column showed as a light-grey square through the stair's open half.
        builder = F.Build(self._spec('pitched', [48]), footprints={'A': self.cells}, world=_StoneWorld())
        states = {(0, 0): 'brick_stairs[facing=east,half=bottom,shape=straight]'}
        # Open side (south) at the stair's Y: floor becomes wall.
        builder.p.set(0, 50, 1, builder.floor_mat)
        builder._back_roof_stair_edges({(0, 0): 50, (0, 1): 51}, states)
        self.assertEqual(builder.p.cells[(0, 50, 1)], 'minecraft:bricks')
        # The stair's closed (facing) side keeps its floor block.
        builder.p.cells.clear()
        builder.p.set(1, 50, 0, builder.floor_mat)
        builder._back_roof_stair_edges({(0, 0): 50, (1, 0): 51}, states)
        self.assertEqual(builder.p.cells[(1, 50, 0)], 'minecraft:smooth_stone')

    def test_zoned_shell_swaps_storey_floor_for_wall_beside_open_stair_half(self):
        # S-087 end to end: with the facade datum at 42, a storey floor falls at
        # Y60, the stair's Y, in the taller south column. Its open upper half
        # must see wall, not the light-grey smooth_stone floor (#310).
        cells = {(x, z) for x in range(6163, 6168) for z in range(-5633, -5628)}
        rows = [
            [120, 120, 120, 120, 120],
            [120, 120, 120, 122, 122],
            [120, 120, 120, 122, 124],
            [120, 120, 122, 124, 124],
            [120, 120, 122, 124, 124],
        ]
        raw = {(6163 + x, -5633 + z): h for z, row in enumerate(rows) for x, h in enumerate(row)}
        halfs = RG.regularise_roof_half_heights(raw)
        builder = F.Build(self._spec('pitched', [48]), footprints={'A': cells}, world=_StoneWorld())
        feature = builder.features[0]
        feature.b = 42  # storey floors at 42, 45, ..., 60
        feature.zone_ground = {c: 41 for c in cells}
        feature.zone_level = {c: 49 for c in cells}
        feature.zone_eave = {c: 70 for c in cells}
        feature.zone_roof_half = halfs
        feature.zone_roof = {c: RG.roof_block_y(h) for c, h in halfs.items()}
        builder.shell_zoned(0, feature)
        self.assertIn('facing=east', builder.p.cells[(6165, 60, -5631)])
        self.assertIn('shape=straight', builder.p.cells[(6165, 60, -5631)])
        # The stair's closed (east) side keeps the storey floor at that Y.
        self.assertEqual(builder.p.cells[(6166, 60, -5631)], 'minecraft:smooth_stone')
        self.assertEqual(builder.p.cells[(6165, 60, -5630)], 'minecraft:bricks')

    def test_stair_backing_replaces_floor_block_beside_outer_corner_stair(self):
        # An outer-corner stair has an open side on every face; a floor block
        # on one of them is swapped for wall too.
        builder = F.Build(self._spec('pitched', [48]), footprints={'A': self.cells}, world=_StoneWorld())
        states = {(0, 0): 'brick_stairs[facing=east,half=bottom,shape=outer_left]'}
        builder.p.set(-1, 50, 0, builder.floor_mat)
        builder._back_roof_stair_edges({(0, 0): 50, (-1, 0): 51}, states)
        self.assertEqual(builder.p.cells[(-1, 50, 0)], 'minecraft:bricks')

    def test_pitched_zones_ridge_slabs_flat_plateaus_unchanged(self):
        """Pitched+zones follows the lidar ridge with stairs; flat stays stepped decks."""
        pitched = self._build('pitched', [48], self.ridge_zone_data, material='brick')
        # 5×5 median flattens the peak to a 3-wide plateau; stairs sit on the flanks.
        slope_stairs = [
            st for (x, y, z), st in pitched.cells.items()
            if (x, z) in self.cells and x in (11, 12, 16, 17) and 'brick_stairs' in st
        ]
        self.assertTrue(slope_stairs, 'expected upslope stairs on the lidar pitch')
        self.assertTrue(all('half=bottom' in st for st in slope_stairs))
        self.assertTrue(all('facing=east' in st or 'facing=west' in st for st in slope_stairs))
        # Highest roof membrane block per column (ignore wall fill under the pitch).
        top: dict[tuple[int, int], tuple[int, str]] = {}
        for (x, y, z), st in pitched.cells.items():
            if (x, z) not in self.cells:
                continue
            if not ('brick_stairs' in st or 'brick_slab' in st or st.endswith(':bricks')):
                continue
            cur = top.get((x, z))
            if cur is None or y > cur[0]:
                top[(x, z)] = (y, st)
        ridge_ys = {top[c][0] for c in top if c[0] == 14}
        edge_ys = {top[c][0] for c in top if c[0] in (10, 18)}
        self.assertTrue(ridge_ys and edge_ys, 'expected ridge and eave membrane')
        self.assertGreater(
            max(ridge_ys), max(edge_ys),
            'ridge must rise above the zone eave (not a flat terrace)',
        )
        slope_tops = [top[c][1] for c in top if c[0] in (11, 12, 16, 17)]
        self.assertTrue(slope_tops, 'expected slope membrane columns')
        self.assertTrue(
            all('brick_stairs' in st for st in slope_tops),
            f'pitched+zones slope must be stairs, got {slope_tops!r}',
        )

        flat = self._build('flat', [45, 50], self.flat_zone_data, material='smooth_stone')
        low = [
            (x, z) for (x, y, z), st in flat.cells.items()
            if y == 45 and st == 'minecraft:smooth_stone' and (x, z) in self.cells
        ]
        high = [
            (x, z) for (x, y, z), st in flat.cells.items()
            if y == 50 and st == 'minecraft:smooth_stone' and (x, z) in self.cells
        ]
        self.assertTrue(low, 'flat zoned roof must keep the low plateau')
        self.assertTrue(high, 'flat zoned roof must keep the high plateau')
        pitched_slabs_on_flat = [
            st for st in flat.cells.values() if 'slab' in st and 'smooth_stone' in st
        ]
        self.assertFalse(
            pitched_slabs_on_flat,
            'flat zoned roofs must stay full-block decks, not stair-slabs',
        )


class PitchedZonesFloatDsmTest(FloatDsmFixture, unittest.TestCase):
    """#300: ``dsm_fine`` (the provider's float option) feeds pitched half-steps only."""

    def test_float_rise_of_one_and_a_half_metres_plans_three_half_steps_and_a_slab(self):
        fine = np.asarray(self.ridge_zone_data['A']['dsm'], dtype=float).copy()
        fine[:, 4:7] = 48.0 + 1.5   # 3-wide ridge 1.5 m above the eave
        data = self._data(fine)
        data['A']['dsm'] = np.where(fine > 49.0, 50.0, 48.0)   # whole-metre grid rounds it
        f = self._prepare(data)
        rises = {h - 2 * f.zone_eave[c] for c, h in f.zone_roof_half.items()}
        self.assertIn(3, rises)
        plan = self._build('pitched', [48], data)
        self.assertTrue(
            any('slab' in st and 'brick' in st for st in plan.cells.values()),
            'a 3-half-step rise must plan a slab course',
        )

    def test_whole_metre_float_input_behaves_exactly_as_without_it(self):
        base = self._prepare(self._data(None))
        same = self._prepare(self._data(np.asarray(self.ridge_zone_data['A']['dsm'], dtype=float)))
        self.assertEqual(base.zone_roof_half, same.zone_roof_half)
        self.assertEqual(base.zone_roof, same.zone_roof)

    def test_dsm_and_ground_grids_are_identical_with_and_without_dsm_fine(self):
        fine = np.asarray(self.ridge_zone_data['A']['dsm'], dtype=float) + 0.5
        a = self._prepare(self._data(None))
        b = self._prepare(self._data(fine))
        self.assertEqual(a.cells, b.cells)
        self.assertEqual(a.zone_eave, b.zone_eave)
        self.assertEqual(a.zone_ground, b.zone_ground)
        self.assertEqual(a.zone_level, b.zone_level)

    def test_nan_cells_in_dsm_fine_do_not_poison_the_roof(self):
        fine = np.asarray(self.ridge_zone_data['A']['dsm'], dtype=float).copy()
        fine[3, 5] = np.nan
        f = self._prepare(self._data(fine))
        self.assertTrue(f.zone_roof_half, 'a no-data cell must not blank the roof')
        self.assertEqual(len(f.zone_roof_half), len(self._prepare(self._data(None)).zone_roof_half))

    def test_low_roof_stays_a_full_deck_through_preparation_and_shell(self):
        fine = np.full_like(self.ridge_zone_data['A']['dsm'], 48.0, dtype=float)
        fine[:, 4:7] = 49.0
        builder = self._builder(self._data(fine))
        with mock.patch.object(FZ, 'metres_aod_to_y', side_effect=lambda m: int(m)), \
                redirect_stdout(io.StringIO()):
            plan = builder.build()
        feature = builder.features[0]
        self.assertEqual(feature.zone_flat_cells, feature.cells)
        for x, z in feature.cells:
            self.assertEqual(plan.cells[(x, feature.zone_roof[(x, z)], z)], 'minecraft:bricks')


class FlatRoofPolicyTest(unittest.TestCase):
    """#314: low roofs flatten; taller roofs need a slope of at least 20%."""

    def ridge(self, profile, length=5):
        med = np.tile(np.asarray(profile, dtype=float), (length, 1))
        return FZ.zone_ridge_half_steps(med, np.zeros(med.shape), np.ones(med.shape, dtype=bool))

    def test_low_rises_flatten_and_exactly_one_and_a_half_can_stay_pitched(self):
        for peak in (0.5, 1.0, 1.499, 1.5):
            with self.subTest(peak=peak):
                out = self.ridge([0, peak, peak, 0])
                if peak < 1.5:
                    self.assertFalse(out.any())
                else:
                    self.assertTrue((out[:, 1:3] == 3).all())

    def test_large_shallow_roof_flattens_even_when_its_total_rise_is_large(self):
        out = self.ridge(np.arange(41) * 0.1)
        self.assertFalse(out.any())  # Four metres of rise, 10% slope.

    def test_lower_tolerance_keeps_a_ridge_that_peaks_under_the_default(self):
        """B35 annex: a real ridge peaking 1.4 m above its eave was flattened by the 1.5 m default."""
        profile = [0, 0.5, 1.0, 1.4, 1.4, 1.0, 0.5, 0]
        med = np.tile(np.asarray(profile, dtype=float), (5, 1))
        zone, readable = np.zeros(med.shape), np.ones(med.shape, dtype=bool)
        self.assertFalse(FZ.zone_ridge_half_steps(med, zone, readable).any())
        out = FZ.zone_ridge_half_steps(med, zone, readable, flat_tol_m=1.25)
        self.assertTrue((out[:, 3:5] == 3).all())
        self.assertTrue((out[:, 2] == 2).all())

    def test_gradient_boundary_is_strict(self):
        self.assertFalse(self.ridge(np.arange(11) * 0.199).any())
        self.assertGreater(int(self.ridge(np.arange(11) * 0.2).max()), 0)

    def test_long_steep_roof_uses_cross_slope_instead_of_ridge_length(self):
        out = self.ridge([0, 0.5, 1, 1.5, 2, 1.5, 1, 0.5, 0], length=101)
        self.assertTrue((out[:, 4] == 4).all())  # 50% slope despite the long ridge.

    def test_rotated_roofs_use_the_combined_gradient(self):
        z, x = np.mgrid[:21, :21]
        for gradient in (0.19, 0.22):
            with self.subTest(gradient=gradient):
                med = (x + z) * gradient / np.sqrt(2)
                out = FZ.zone_ridge_half_steps(med, np.zeros(med.shape), np.ones(med.shape, bool))
                self.assertEqual(bool(out.any()), gradient >= 0.2)

    def test_tall_pitch_does_not_keep_a_low_bump_beyond_a_saddle(self):
        out = self.ridge([0, 1.2, 1.2, 0.5, 1, 2, 3, 3, 2, 0])
        self.assertFalse(out[:, 1:3].any())
        self.assertTrue((out[:, 6:8] == 6).all())
        self.assertTrue((out[:, 4] == 2).all())  # The qualifying roof keeps its toe.

    def test_gentler_upper_pitch_stays_connected_to_its_steep_lower_slope(self):
        out = self.ridge([0, 1.5, 1.6, 1.7, 1.8, 1.9, 2])
        self.assertTrue((out[:, -1] == 4).all())
        self.assertTrue((out[:, 1] == 3).all())

    def test_shallow_roof_does_not_borrow_a_different_roofs_steep_slope(self):
        out = self.ridge([0, 1, 2, 0] + list(np.arange(31) * 0.1))
        self.assertTrue((out[:, 2] == 4).all())
        self.assertFalse(out[:, 4:].any())

    def test_other_zones_and_nodata_cannot_supply_a_qualifying_pitch(self):
        for gap in ('zone', 'nodata'):
            with self.subTest(gap=gap):
                med = np.tile([0, 1, 1, 2, 3, 3], (5, 1)).astype(float)
                zone = np.zeros(med.shape)
                readable = np.ones(med.shape, bool)
                if gap == 'zone':
                    zone[:, 3:] = 3
                else:
                    med[:, 3] = np.nan
                before = readable.copy()
                out = FZ.zone_ridge_half_steps(med, zone, readable)
                self.assertFalse(out[:, :3].any())
                np.testing.assert_array_equal(readable, before)

    def test_flattened_bump_cannot_gain_stairs_slabs_or_caps_beside_retained_pitch(self):
        med = np.tile([0, 1.2, 1.2, 1, 1.5, 2, 3, 3, 2, 0], (5, 1))
        mask = np.ones(med.shape, bool)
        flat = np.empty(med.shape, bool)
        grid = FZ.zone_ridge_half_steps(med, np.zeros(med.shape), mask, flat_out=flat)
        half = {(x, z): 20 + int(grid[z, x]) for z, x in zip(*np.nonzero(mask))}
        flat_cells = {(x, z) for z, x in zip(*np.nonzero(flat))}
        states = RG.pitched_roof_states(half, dict.fromkeys(half, 'brick'), flat_cells=flat_cells)
        self.assertIn((2, 2), flat_cells)
        for c in flat_cells:
            self.assertEqual(states[c], 'bricks')
        self.assertIn('_stairs[', states[(5, 2)])
        from roof_lint import stair_shape_mismatches
        placed = {(x, RG.roof_block_y(half[(x, z)]), z): st for (x, z), st in states.items()}
        self.assertEqual(stair_shape_mismatches(placed), [])

    def test_flat_decks_keep_orthogonal_cross_zone_stairs(self):
        half = {(0, 0): 20, (1, 0): 24, (0, 1): 20}
        levels = {(0, 0): 0, (1, 0): 1, (0, 1): 0}
        flat_cells = {(0, 0), (0, 1)}
        states = RG.pitched_roof_states(
            half, dict.fromkeys(half, 'brick'), levels=levels, flat_cells=flat_cells,
        )
        self.assertEqual(states[(0, 0)], RG._roof_stair('brick', 'east'))
        self.assertEqual(states[(0, 1)], 'bricks')


class SmoothRoofHalfStepsTest(FloatDsmFixture, unittest.TestCase):
    """#302: smoothing the provider's float half-steps (flat decks, lone odd cells, straight ridges)."""

    def _flatten(self, half, zone=None):
        half = np.asarray(half, dtype=int)
        zone = np.full(half.shape, 48.0) if zone is None else np.asarray(zone, dtype=float)
        return FZ.flatten_half_step_decks(half, zone, np.ones(half.shape, dtype=bool))

    @staticmethod
    def _snap(rows):
        """rows of half-heights (one zone) -> rows after the lone-odd repair"""
        h = {(x, z): v for z, row in enumerate(rows) for x, v in enumerate(row)}
        out = FZ.snap_lone_odd_half_heights(h, {c: 48 for c in h})
        return [[out[(x, z)] for x in range(len(rows[0]))] for z in range(len(rows))]

    def test_deck_half_a_metre_above_its_level_stays_flat(self):
        out = self._flatten(np.ones((6, 8), dtype=int))
        self.assertFalse(out.any(), 'a whole zone at +1 half-step is a flat deck, not 1-half-step slabs')

    def test_toe_beside_a_deck_drops_but_the_foot_of_a_slope_stays(self):
        row = [1, 1, 1, 1, 2, 3, 4, 5, 6]
        out = self._flatten(np.tile(row, (5, 1)))
        self.assertEqual(out[2].tolist(), [0, 0, 0, 1, 2, 3, 4, 5, 6])

    def test_whole_metre_input_is_unchanged_by_flattening_and_snapping(self):
        half = np.tile([0, 0, 2, 2, 4, 4, 2, 2, 0, 0], (5, 1))
        self.assertEqual(self._flatten(half).tolist(), half.tolist())
        self.assertEqual(self._snap(half.tolist()), half.tolist())

    def test_flattening_works_in_each_zone_apart(self):
        # a 1 beside a 2 in another zone is still a lone 1 in its own zone: it drops
        half = np.array([[0, 1, 0], [2, 2, 2]])
        zone = np.array([[48.0, 48.0, 48.0], [50.0, 50.0, 50.0]])
        self.assertEqual(self._flatten(half, zone).tolist(), [[0, 0, 0], [2, 2, 2]])

    def test_lone_odd_cell_snaps_to_its_neighbours(self):
        rows = np.full((7, 7), 4)
        rows[3, 3] = 5
        out = self._snap(rows.tolist())
        self.assertEqual(out, np.full((7, 7), 4).tolist())

    def test_lone_odd_cell_ties_go_to_the_lower_neighbour(self):
        rows = np.tile([2, 2, 4, 4, 6, 6], (6, 1))
        rows[3, 2] = 3      # differs from 2 (left), 4 (right), 4 (up), 4 (down)
        out = self._snap(rows.tolist())
        self.assertEqual(out[3][2], 2)

    def test_clean_odd_courses_are_kept(self):
        rows = np.tile([0, 1, 2, 3, 4, 5, 6, 7], (5, 1))
        self.assertEqual(self._snap(rows.tolist()), rows.tolist(), 'a clean slope keeps its odd (slab) steps')

    def test_steep_hip_leaves_no_lone_odd_cell(self):
        # odd cells whose snap would hand the speckle on: the repair must still end with none
        rows = [[0, 2, 4, 6, 8, 10],
                [0, 3, 5, 7, 9, 10],
                [0, 2, 4, 6, 8, 10],
                [0, 3, 5, 7, 9, 10]]
        out = self._snap(rows)
        flat = {(x, z): v for z, row in enumerate(out) for x, v in enumerate(row)}
        self.assertEqual(FZ._lone_odd_half_heights(flat), [])

    def test_snap_leaves_no_cliff_above_two_even_from_raw_heights(self):
        rows = [[0, 1, 4, 7, 4], [0, 2, 5, 5, 4], [0, 1, 3, 3, 3]]
        out = self._snap(rows)
        for z in range(len(out)):
            for x in range(len(out[0])):
                for dx, dz in F.N4:
                    xx, zz = x + dx, z + dz
                    if 0 <= zz < len(out) and 0 <= xx < len(out[0]):
                        self.assertLessEqual(abs(out[z][x] - out[zz][xx]), 2)

    def test_flat_deck_half_a_metre_up_builds_without_slabs_and_a_ridge_keeps_its_slabs(self):
        # deck 0.5 m above its level, no ridge: nothing pitched survives, so no slab
        flat = np.full((7, 11), 48.5)
        data = self._data(flat)
        plan = self._build('pitched', [48], data)
        self.assertFalse(
            any('slab' in st and 'brick' in st for st in plan.cells.values()),
            'a deck 0.5 m above its level must stay flat, not a block of 1-half-step slabs',
        )
        # same deck with a clean float ridge keeps its slabs
        fine = np.asarray(self.ridge_zone_data['A']['dsm'], dtype=float).copy()
        fine[:, 4:7] = 48.0 + 1.5
        plan = self._build('pitched', [48], self._data(fine))
        self.assertTrue(any('slab' in st and 'brick' in st for st in plan.cells.values()))


    def test_noisy_float_ridge_plans_no_ridge_jog_and_no_lone_slab(self):
        import roof_lint
        base = np.full((7, 11), 48.0)
        for xi, h in enumerate([48.5, 48.5, 49.5, 50.5, 52.5, 50.5, 49.5, 48.5, 48.5]):
            base[:, 1 + xi] = h
        for seed in range(6):
            rng = np.random.default_rng(seed)
            fine = base + rng.choice([-0.5, 0.0, 0.0, 0.5], size=base.shape)
            plan = self._build('pitched', [48], self._data(fine))
            self.assertEqual(roof_lint.ridge_jogs(plan.cells), [], f'seed {seed}')
            self.assertEqual(roof_lint.stair_shape_mismatches(plan.cells), [], f'seed {seed}')

    def test_noisy_ridge_half_steps_have_no_lone_odd_cell_but_do_without_the_smoothing(self):
        # the plan lint above passes on this fixture even unsmoothed, so the smoothing is guarded
        # here, on the half-steps: lone odd (slab) cells appear without it and never with it
        base = np.full((7, 11), 48.0)
        for xi, h in enumerate([48.5, 48.5, 49.5, 50.5, 52.5, 50.5, 49.5, 48.5, 48.5]):
            base[:, 1 + xi] = h
        smoothed = raw = 0
        for seed in range(6):
            rng = np.random.default_rng(seed)
            data = self._data(base + rng.choice([-0.5, 0.0, 0.0, 0.5], size=base.shape))
            smoothed += len(FZ._lone_odd_half_heights(self._prepare(data).zone_roof_half))
            with mock.patch.object(FZ, 'flatten_half_step_decks', lambda half, zone, readable: half), \
                    mock.patch.object(FZ, 'snap_lone_odd_half_heights', lambda half, levels, **kw: half):
                raw += len(FZ._lone_odd_half_heights(self._prepare(data).zone_roof_half))
        self.assertEqual(smoothed, 0)
        self.assertGreater(raw, 0)

    def test_deeper_steep_hip_leaves_no_lone_odd_cell(self):
        # #302 item 4: the cliff cap after the drop-one-half-step pass must not leave a speckle
        rows = [[0, 2, 4, 6, 8, 10, 12, 14],
                [0, 3, 5, 7, 9, 11, 13, 14],
                [0, 2, 5, 6, 9, 10, 13, 14],
                [0, 3, 4, 7, 8, 11, 12, 14],
                [0, 2, 4, 6, 8, 10, 12, 14]]
        out = self._snap(rows)
        flat = {(x, z): v for z, row in enumerate(out) for x, v in enumerate(row)}
        self.assertEqual(FZ._lone_odd_half_heights(flat), [])

    def test_random_steep_roofs_never_end_with_a_lone_odd_cell(self):
        import random
        for seed in range(300):
            rnd = random.Random(seed)
            w, d = rnd.randint(3, 9), rnd.randint(3, 7)
            h = {(x, z): max(0, 2 * rnd.randint(0, 6) + rnd.randint(-1, 1) + x * rnd.randint(0, 3))
                 for z in range(d) for x in range(w)}
            lv = {c: 48 for c in h}
            before = RG.regularise_roof_half_heights(h, lv)
            out = FZ.snap_lone_odd_half_heights(before, lv)
            self.assertEqual(FZ._lone_odd_half_heights(out), [], f'seed {seed}')
            self.assertTrue(all(out[c] >= v - v % 2 for c, v in before.items()), f'seed {seed}')
            self.assertEqual(RG.regularise_roof_half_heights(out, lv), out, f'seed {seed}')


class ZoneGuardFixtureTest(unittest.TestCase):
    """Done-when: zoned build + guard keeps door stoop; 0 outside after prune."""

    def setUp(self):
        self.cells = {(x, z) for x in range(10, 16) for z in range(20, 24)}
        depth, width = 6, 8
        dsm = np.full((depth, width), 45.0)
        dsm[:, 4:] = 50.0
        ground = np.full((depth, width), 40)
        self.zone_data = {
            'A': {'dsm': dsm, 'ground_y': ground, 'x0': 9, 'z0': 19},
        }

    def spec(self):
        return {
            'name': 'unit_zones_guard',
            'ways': {'99': 'A'},
            'storeys': {'A': 3},
            'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
            'groups': [{'nos': ['A'], 'front': 'S', 'mode': 'each'}],
            'modifies': [],
            'floor_min': 40,
            'step_max': 4,
            'door_frame': True,
            'keep_edits': False,
            'wall': 'bricks',
            'floor': 'smooth_stone',
            'ledge_stair': False,
            'zones': {
                'A': {'from': 'lidar', 'levels': [45, 50], 'bucket': 2, 'axis_deg': 0},
            },
        }

    def test_prune_keeps_stoop_clears_outside_slabs_zero_guard(self):
        import osm_footprints as ofp

        buf = io.StringIO()
        with mock.patch.object(FZ, 'metres_aod_to_y', side_effect=lambda m: int(m)), \
                mock.patch('cell_registry.view_registry') as vr, \
                redirect_stdout(buf):
            vr.return_value.owned.return_value = {}
            b = F.Build(
                self.spec(), footprints={'A': self.cells}, world=_StoneWorld(),
                zone_data=self.zone_data,
            )
            b.build()
            f = b.features[0]
            wall = next(c for c in sorted(f.cells) if c[1] == max(z for _, z in f.cells))
            b._set_door_leaf(wall, 'S', 'spruce_door', 'left', f.b)
            stoop = (wall[0], wall[1] + 1)
            stoop_key = (stoop[0], f.b, stoop[1])
            self.assertIn(stoop_key, b.p.cells)
            self.assertIn('smooth_stone', b.p.cells[stoop_key])

            far_slab = (200, f.b + 5, 200)
            b.p.cells[far_slab] = 'minecraft:smooth_stone_slab[type=bottom]'
            stray = (201, f.b + 5, 201)
            b.p.cells[stray] = 'minecraft:bricks'

            outside = {
                far_slab: [b.p.cells[far_slab], 5, 'front', None, None],
                stray: [b.p.cells[stray], 5, 'front', None, None],
                stoop_key: [b.p.cells[stoop_key], 5, 'front', None, None],
            }
            with mock.patch.object(F.osm_footprints, 'guard', return_value={'outside': dict(outside), 'blind': []}):
                b._prune_outside_guard_hits()

            self.assertIn(stoop_key, b.p.cells, 'door stoop must survive zone prune')
            self.assertNotIn(far_slab, b.p.cells, 'outside slab must be dropped')
            self.assertIn(stray, b.p.cells, 'non-zoned outside brick must not be wiped')

            del b.p.cells[stray]

            class _Idx:
                def covered(self, x, z):
                    return True

                def near_footprint(self, x, z, margin=2):
                    cols = set(f.cells) | {stoop}
                    return any(
                        abs(x - cx) <= margin and abs(z - cz) <= margin
                        for cx, cz in cols
                    )

                def front_zone(self, x, z):
                    return False

                def nearest_street(self, x, z):
                    return None

            class _GroundWorld:
                def ground(self, x, z, top=120, bottom=0):
                    return 40

            rep = ofp.guard(b.p.cells, world=_GroundWorld(), idx=_Idx())
            self.assertEqual(rep['outside'], {})
            self.assertEqual(rep['blind'], [])
            self.assertIn(stoop_key, b.p.cells)


class PitchedMaterialOwnershipTest(unittest.TestCase):
    """#170: photo-derived roof membership owns materials across lidar zones."""

    def _spec(self):
        return {
            'name': 'unit_pitch_materials', 'ways': {'99': 'A'},
            'storeys': {'A': 1},
            'roofs': {'A': {'kind': 'pitched', 'material': 'brick',
                            'zone_materials': {'45': 'polished_deepslate'}}},
            'groups': [{'nos': ['A'], 'front': 'S', 'mode': 'each'}],
            'wall': 'stone', 'floor': 'smooth_stone',
            'zones': {'A': {'levels': [45, 47, 48], 'from': 'lidar'}},
        }

    def _region(self, level=47, x0=-1, x1=10, z0=-1, z1=4):
        return {'level': level, 'polygon': [[x0, z0], [x1, z0], [x1, z1], [x0, z1]]}

    def _shell(self, heights, levels, regions=None):
        spec = self._spec()
        if regions is not None:
            spec['roofs']['A']['material_regions'] = regions
        builder = F.Build(spec, footprints={'A': set(heights)}, world=_StoneWorld())
        f = builder.features[0]
        f.b = 40
        f.zone_roof_half = heights
        f.zone_roof = {c: RG.roof_block_y(h) for c, h in heights.items()}
        f.zone_ground = {c: 40 for c in heights}
        f.zone_level = levels
        f.zone_eave = levels
        f.roof_material_level = FZ.roof_material_levels(f.roof, levels)
        builder.shell_zoned(0, f)
        return {
            c: builder.p.cells[(c[0], RG.roof_block_y(h), c[1])]
            for c, h in heights.items()
        }, f.roof_material_level

    def test_explicit_roof_membership_sets_stair_full_and_slab_materials(self):
        heights = {(x, z): h for z in range(3)
                   for x, h in enumerate([90, 92, 94, 96, 97, 96, 94, 92, 90])}
        levels = {c: 47 if 2 <= c[0] <= 6 else 45 for c in heights}

        blocks, owners = self._shell(heights, levels, [self._region()])

        self.assertEqual(set(owners.values()), {47})
        self.assertTrue(any('_stairs[' in st for st in blocks.values()))
        self.assertTrue(any(st == 'minecraft:bricks' for st in blocks.values()))
        self.assertTrue(any(st.startswith('minecraft:brick_slab[')
                            for st in blocks.values()))
        for c, state in blocks.items():
            self.assertTrue(state.startswith(('minecraft:brick_stairs[',
                                              'minecraft:bricks', 'minecraft:brick_slab[')),
                            (c, state))
        self.assertEqual(levels[(0, 0)], 45, 'evidence zones are not rewritten')

    def test_region_material_sets_only_its_columns_and_needs_no_spare_plateau(self):
        heights = {(x, z): h for z in range(3)
                   for x, h in enumerate([90, 92, 94, 96, 97, 96, 94, 92, 90])}
        levels = {c: 47 if 2 <= c[0] <= 6 else 45 for c in heights}
        region = dict(self._region(x0=-1, x1=3.9), material='polished_diorite')

        blocks, owners = self._shell(heights, levels, [region])

        inside = {c for c, o in owners.items() if o == 'region:0'}
        self.assertEqual(inside, {c for c in heights if c[0] <= 3})
        self.assertEqual(levels[(0, 0)], 45, 'evidence zones are not rewritten')
        for c in inside:
            self.assertTrue(blocks[c].startswith('minecraft:polished_diorite'), (c, blocks[c]))
        for c in set(heights) - inside:
            self.assertNotIn('polished_diorite', blocks[c])
            self.assertEqual(owners[c], levels[c])

    def test_region_without_material_still_uses_its_plateau_material(self):
        heights = {(x, z): 94 for z in range(2) for x in range(4)}
        levels = {c: 45 for c in heights}
        _blocks, owners = self._shell(heights, levels, [self._region(level=45)])
        self.assertEqual(set(owners.values()), {45})

    def test_odd_h_column_gets_bottom_half_slab_at_roof_block_y_with_wall_below(self):
        heights = {(x, z): h for z in range(3)
                   for x, h in enumerate([90, 92, 94, 96, 97, 96, 94, 92, 90])}
        levels = {c: 47 for c in heights}
        spec = self._spec()
        builder = F.Build(spec, footprints={'A': set(heights)}, world=_StoneWorld())
        f = builder.features[0]
        f.b = 40
        f.zone_roof_half = heights
        f.zone_roof = {c: RG.roof_block_y(h) for c, h in heights.items()}
        f.zone_ground = {c: 40 for c in heights}
        f.zone_level = levels
        f.zone_eave = levels
        f.roof_material_level = FZ.roof_material_levels(f.roof, levels)
        builder.shell_zoned(0, f)
        y = RG.roof_block_y(97)
        self.assertEqual(y, 49)
        self.assertEqual(builder.p.cells[(4, y, 1)], 'minecraft:brick_slab[type=bottom,waterlogged=false]')
        self.assertIn((4, y, 1), builder.p.keep_slab_cells)
        self.assertNotIn(builder.p.cells[(4, y - 1, 1)], ('minecraft:air', None))

    def test_narrow_lower_deck_and_independent_pitch_keep_their_material(self):
        heights = {(x, z): h for z in range(3)
                   for x, h in enumerate([90, 92, 94, 92, 90, 90, 96, 96, 96])}
        # A separate low pitch meets the upper platform: it is not the main roof.
        heights.update({(x, 4): h for x, h in enumerate([90, 92, 94, 96, 96, 96])})
        levels = {c: 47 if (6 <= c[0] or (c[1] < 3 and c[0] == 2)
                            or (c[1] == 4 and c[0] >= 3)) else 45
                  for c in heights}

        blocks, owners = self._shell(
            heights, levels, [self._region(x0=0, x1=5, z0=0, z1=3)],
        )

        self.assertEqual(owners[(5, 1)], 45, 'one-block lower strip stays separate')
        for c, state in blocks.items():
            if (c[1] == 4 and c[0] < 3) or (c[1] < 3 and c[0] == 5):
                self.assertIn('polished_deepslate', state, (c, state))
            elif c[1] == 4:
                self.assertIn('brick', state, (c, state))
            elif c[0] < 5:
                self.assertIn('brick', state, (c, state))

    def test_unmapped_and_legacy_roofs_keep_zone_only_lookup(self):
        heights = {(x, 0): h for x, h in enumerate([90, 92, 94, 96, 96])}
        levels = {c: 45 if c[0] < 3 else 47 for c in heights}

        legacy, legacy_owners = self._shell(heights, levels)
        empty, empty_owners = self._shell(heights, levels, [])

        self.assertEqual(legacy, empty)
        self.assertEqual(legacy_owners, levels)
        self.assertEqual(empty_owners, levels)
        self.assertIn('polished_deepslate', legacy[(2, 0)])
        self.assertIn('brick', legacy[(3, 0)])

    def test_prepared_lidar_slope_uses_explicit_membership_in_shell(self):
        spec = self._spec()
        spec['zones']['A'].update(levels=[45, 47], bucket=1, axis_deg=0)
        spec['roofs']['A']['material_regions'] = [self._region(x0=3, x1=12, z0=0, z1=15)]
        profile = [45, 45, 45, 46, 47, 48, 49, 50, 49, 48, 47, 46, 45, 45, 45]
        dsm = np.tile(profile, (15, 1)).astype(float)
        cells = {(x, z) for x in range(15) for z in range(15)}
        builder = F.Build(
            spec, footprints={'A': cells}, world=_StoneWorld(),
            zone_data={'A': {'x0': 0, 'z0': 0, 'dsm': dsm,
                             'ground_y': np.full((15, 15), 40)}},
        )

        with mock.patch.object(FZ, 'metres_aod_to_y', side_effect=lambda m: int(m)):
            builder.prepare_zones()
        f = builder.features[0]
        builder.shell_zoned(0, f)

        c = (3, 7)
        self.assertEqual(f.zone_level[c], 45)
        self.assertEqual(f.roof_material_level[c], 47)
        self.assertTrue(builder.p.cells[(c[0], f.zone_roof[c], c[1])].startswith(
            'minecraft:brick_stairs[',
        ))
        self.assertEqual(f.roof_material_level[(0, 7)], 45)
        self.assertIn('A material region 0: level 47 columns 135', builder._zone_pad_lines)

    def test_regions_clip_to_roof_and_use_block_centres(self):
        levels = {(0, 0): 45, (1, 0): 47, (2, 0): 45}
        roof = {'material_regions': [self._region(x0=-100, x1=1.5)]}

        owners = FZ.roof_material_levels(roof, levels)

        self.assertEqual(owners, {(0, 0): 47, (1, 0): 47, (2, 0): 45})
        self.assertEqual(set(owners), set(levels))

    def test_rejects_overlapping_empty_and_unknown_level_regions(self):
        levels = {(0, 0): 45, (1, 0): 47}
        cases = [
            ([self._region(), self._region()], 'overlapping'),
            ([self._region(x0=10, x1=12)], 'no roof columns'),
            ([self._region(level=49)], 'unknown plateau'),
            ([{'level': 47, 'polygon': [[0, 0], [1, 0], [2, 0]]}], 'no roof columns'),
        ]
        for regions, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(SystemExit, message):
                FZ.roof_material_levels({'material_regions': regions}, levels)
        with self.assertRaisesRegex(SystemExit, 'no prepared roof columns'):
            FZ.roof_material_levels({'material_regions': [self._region()]}, {})

    def test_declared_plateau_may_own_roof_even_when_no_cells_snapped_to_it(self):
        levels = {(0, 0): 45}

        owners = FZ.roof_material_levels(
            {'material_regions': [self._region()]}, levels, known_levels=[45, 47],
        )

        self.assertEqual(owners, {(0, 0): 47})

    def test_region_validation_rejects_malformed_rows_and_unknown_plateaus(self):
        cases = [
            (None, 'must be a list'),
            ([{}], 'needs'),
            ([dict(self._region(), extra=True)], 'needs'),
            ([dict(self._region(), material='')], 'material'),
            ([dict(self._region(), material=3)], 'material'),
            ([dict(self._region(), level=49)], 'unknown plateau'),
            ([dict(self._region(), level='47')], 'finite plateau'),
            ([dict(self._region(), level=True)], 'finite plateau'),
            ([dict(self._region(), level=float('nan'))], 'finite plateau'),
            ([dict(self._region(), polygon=[[0, 0], [1, 0]])], 'three vertices'),
            ([dict(self._region(), polygon=[[0, 0], [0, 0], [0, 0]])], 'distinct'),
            ([dict(self._region(), polygon=[[0, 0], [1, 0], [2, float('inf')]])], 'finite'),
            ([dict(self._region(), polygon=[[0, 0], [1, 0], [2, True]])], 'finite'),
        ]
        for regions, message in cases:
            with self.subTest(regions=regions):
                spec = self._spec()
                spec['roofs']['A']['material_regions'] = regions
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / 'spec.json'
                    path.write_text(json.dumps(spec))
                    with self.assertRaisesRegex(SystemExit, message):
                        FS.load_spec(path)

    def test_regions_require_pitched_zoned_roofs_and_accept_auto_levels(self):
        for kind, zones in [('flat', {'A': {'levels': [45, 47], 'from': 'lidar'}}),
                            ('pitched', {})]:
            spec = self._spec()
            spec['roofs']['A'].update(kind=kind, material_regions=[self._region()])
            spec['zones'] = zones
            with self.subTest(kind=kind, zones=zones), self.assertRaisesRegex(SystemExit, 'requires'):
                FS._validate_material_regions('spec', spec)
        spec = self._spec()
        spec['zones']['A']['levels'] = 'auto'
        spec['roofs']['A']['material_regions'] = [self._region()]
        FS._validate_material_regions('spec', spec)


class ZoneMaterialsColourTest(unittest.TestCase):
    """#126: per-zone roof material override + dry-run colour suggestion."""

    def setUp(self):
        # Same two-plateau fixture as ZoneBuildTest (x=10..15, z=20..23).
        self.cells = {(x, z) for x in range(10, 16) for z in range(20, 24)}
        self.x0, self.z0 = 9, 19
        depth, width = 6, 8
        dsm = np.full((depth, width), 45.0)
        dsm[:, 4:] = 50.0  # x >= 13 → higher plateau
        ground = np.full((depth, width), 40)
        self.zone_data = {
            'A': {'dsm': dsm, 'ground_y': ground, 'x0': self.x0, 'z0': self.z0},
        }

    def spec(self):
        return {
            'name': 'unit_zone_materials',
            'ways': {'99': 'A'},
            'storeys': {'A': 3},
            'roofs': {
                'A': {
                    'kind': 'flat',
                    'material': 'smooth_stone',
                    'zone_materials': {
                        '45': 'gray_concrete',
                        '50': 'deepslate_tiles',
                    },
                },
            },
            'groups': [{'nos': ['A'], 'front': 'S', 'mode': 'each'}],
            'modifies': [],
            'floor_min': 40,
            'step_max': 4,
            'door_frame': True,
            'keep_edits': False,
            'wall': 'bricks',
            'floor': 'smooth_stone',
            'ledge_stair': False,
            'zones': {
                'A': {'from': 'lidar', 'levels': [45, 50], 'bucket': 2, 'axis_deg': 0},
            },
        }

    def test_per_zone_materials_and_dry_run_colour_suggest(self):
        """Two zones of one building can use different mats; dry-run prints colour + suggest."""
        # Inject 0..1 RGB: low plateau pale grey, high plateau dark blue-grey.
        aerial = {}
        for x, z in self.cells:
            aerial[(x, z)] = (0.62, 0.62, 0.60) if x < 13 else (0.28, 0.28, 0.32)
        buf = io.StringIO()
        with mock.patch.object(FZ, 'metres_aod_to_y', side_effect=lambda m: int(m)), \
                redirect_stdout(buf):
            plan = F.build(
                self.spec(),
                footprints={'A': self.cells},
                world=_StoneWorld(),
                zone_data=self.zone_data,
                aerial_rgb=aerial,
            )
        out = buf.getvalue()
        low = [
            (x, z) for (x, y, z), st in plan.cells.items()
            if y == 45 and st == 'minecraft:gray_concrete' and (x, z) in self.cells
        ]
        high = [
            (x, z) for (x, y, z), st in plan.cells.items()
            if y == 50 and st == 'minecraft:deepslate_tiles' and (x, z) in self.cells
        ]
        self.assertTrue(low, 'zone 45 must use gray_concrete override')
        self.assertTrue(high, 'zone 50 must use deepslate_tiles override')
        # Default material must not cover both plateaus when overrides are set.
        default_roofs = [
            st for (x, y, z), st in plan.cells.items()
            if (x, z) in self.cells and y in (45, 50) and st == 'minecraft:smooth_stone'
        ]
        self.assertFalse(default_roofs, 'zone_materials must override the label default')
        self.assertRegex(out, r'A zone 45:.*suggest \w+')
        self.assertRegex(out, r'A zone 50:.*suggest \w+')
        self.assertIn('rgb=', out)


class SampleColumnsRgbTest(unittest.TestCase):
    """#370: roof-colour sampling reads the current aerial source and fails loudly."""

    def test_samples_columns_the_aerial_source_covers(self):
        import aerial_coverage as aerial
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'tile.png'
            Image.new('RGB', (64, 64), (200, 100, 50)).save(path)
            mosaic = aerial.Mosaic(path, 19, 0, 0)
            pts = [(10, 10), (500, 500)]
            with mock.patch.object(aerial.Mosaic, 'pixels',
                                   lambda self, p: {q: (q[0], q[1]) for q in p}), \
                    mock.patch.object(aerial, 'mosaics', return_value=[mosaic]):
                got = FZ.sample_columns_rgb(pts)
        self.assertEqual(list(got), [(10, 10)])
        self.assertAlmostEqual(got[(10, 10)][0], 200 / 255, places=2)

    def test_missing_aerial_source_raises_instead_of_returning_nothing(self):
        import aerial_coverage as aerial
        with mock.patch.object(aerial, 'mosaics', return_value=[]):
            with self.assertRaises(FZ.AerialSourceMissing):
                FZ.sample_columns_rgb([(1, 1)])

    def test_dry_run_says_when_colour_suggestions_are_skipped(self):
        import aerial_coverage as aerial
        feat = mock.Mock(no='A', cells={(1, 1)}, zone_level={(1, 1): 45})
        with mock.patch.object(aerial, 'mosaics', return_value=[]):
            lines = FZ.roof_zone_colour_lines([feat])
        self.assertEqual(len(lines), 1)
        self.assertIn('skipped', lines[0])
