"""Roof geometry shared by the house and feature builders (task 376).

The digests below were recorded on the code before the roof geometry moved out of
``feature_build``; the move must leave every plan byte-identical.
"""

from __future__ import annotations

from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock
import hashlib
import inspect
import io
import json
import unittest

import numpy as np

from block_store import MemoryBlocks
from feature_build_fixtures import (
    _StoneWorld,
    start_lidar_fixture as setUpModule,
    stop_lidar_fixture as tearDownModule,
)
import feature_build as F
import feature_roof_zones as FZ
import house_build as H
import roof_geometry as RG
import roof_material as RM


HOUSE_PITCHES = (None, 0.5, 0.75, 1)
HOUSE_KINDS = ('square', 'L', 'group')
HIP_FOOTPRINTS = {
    'square5': [(x, z) for x in range(5) for z in range(5)],
    'ridge6x9': [(x, z) for x in range(6) for z in range(9)],
    'L10': [(x, z) for x in range(10) for z in range(10) if x < 5 or z < 5],
    'ring_hole': [(x, z) for x in range(9) for z in range(9) if not (3 <= x <= 5 and 3 <= z <= 5)],
}

# Recorded on the pre-change code; see module docstring. The pitched entries (0.75, 1) were
# re-recorded for #414 (eave edge starts a slab or stair high); the None and 0.5 flatish entries are unchanged.
HOUSE_GOLDEN = {
    'square:None': '38acbd50986d4f988349ed6a39ce1f73b7a0e7161fad51b975b82adb3bc4878b',
    'square:0.5': '38acbd50986d4f988349ed6a39ce1f73b7a0e7161fad51b975b82adb3bc4878b',
    'square:0.75': 'e4df67bdcd17c1e8098f5b114845b8af7f90a27718b241dc44a5ee0a42e034d5',
    'square:1': '64762aabf1150483b2e6f841ced09236e310576946ecdb4f2e68916a54a2e0a9',
    'L:None': '049b1ee6ce86d5f6dbad4c119bee30dc3408232ef1bdc6087c818ab7515d644a',
    'L:0.5': '049b1ee6ce86d5f6dbad4c119bee30dc3408232ef1bdc6087c818ab7515d644a',
    'L:0.75': '95ee0749a6515b256a18025835669efe8db1e80081c53608221edbca5bf0f8ee',
    'L:1': 'c32a9eb67c4c5e24ec28e8340cc96516d98373e259edc760b23191360167eeb7',
    'group:None': 'd1feb694bc096d0bc5bb6f6d0ec82b33225c30ae715e7abf261d32d82e88e91b',
    'group:0.5': 'd1feb694bc096d0bc5bb6f6d0ec82b33225c30ae715e7abf261d32d82e88e91b',
    'group:0.75': '4328076590b56048dcbdf616c7e2f7b49e021db827b84e053bf24b6fc6db2fbb',
    'group:1': 'ac7b826c8b292fad3af61a0318a873f4ff33d557344dd38d488f6133106d0f03',
}
HIP_GOLDEN = {
    'square5': (
        '5710c75db7464db2fb3c17d1e7f05133cbe8f896c25d7e7c196c79328a21a4db',
        '3608dac40f0dae2761765a5c39531f58f1d50dbeb73f441cf96a60ef367ae5ec',
    ),
    'ridge6x9': (
        'cbd781b73cfab8a72f68ec6861e819b1ab62574d600649adb5783b6b6f0c495c',
        'fae64383e72074d01e7f15227acdb2db794dba5fe6fb0cb1c66adfef89e9a746',
    ),
    'L10': (
        '390c1749cd69b78f7036b9ff525e33558c1259a57560b71d900290924b6a2fd6',
        'd2932448bdc43ead522d1ef885b8ae59b3223796b30f7f0ff52b16d7bc5c1c4b',
    ),
    'ring_hole': (
        '92fbac612653cf7eea4ea30a5de326ed29cd84d2133f044ae1b9f5ce06658a2e',
        '25f53e2513aa5761008c8ac4bc13199911b74c98e0a483686a3327f25dece1dd',
    ),
}


def _digest(cells: dict) -> str:
    rows = sorted((list(pos), state) for pos, state in cells.items())
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def house_plan(kind: str, pitch, material: str = 'brick'):
    """Plan the roof of a synthetic house footprint (no world, no evidence)."""
    from site_kit import Plan
    if kind == 'square':
        parts = [{(x, z) for x in range(7) for z in range(7)}]
    elif kind == 'L':
        parts = [{(x, z) for x in range(10) for z in range(10) if x < 5 or z < 5}]
    else:
        parts = [{(x, z) for x in range(lo, hi) for z in range(8)} for lo, hi in ((0, 6), (6, 12))]
    labels = [str(i) for i in range(len(parts))]
    group = {'nos': labels, 'front': 'N', 'mode': 'group'}
    b = H.Build.__new__(H.Build)
    b.groups, b.houses = [group], []
    for label, cells in zip(labels, parts):
        house = H.House(label, cells, group)
        house.b = 30
        b.houses.append(house)
    b.owner = {c: i for i, cells in enumerate(parts) for c in cells}
    b.foreign, b.shared = set(), set()
    b.spec = {'roofs': dict.fromkeys(labels, material)}
    if pitch is not None:
        b.spec['roof_pitch'] = dict.fromkeys(labels, pitch)
    b.p = Plan('synthetic_roof')
    b.roof()
    return b.p


def hip_plan(own: set, material: str = 'brick'):
    """Plan a feature hip roof (``Build.roof_pitched``) on a synthetic footprint."""
    stub = mock.Mock()
    stub.p = F.Plan('hip')
    stub.wall = 'bricks'
    stub.owner, stub.foreign, stub.shared = set(own), set(), set()
    stub._roof_warnings = []
    stub.live.block.return_value = 'minecraft:air'
    stub.set = stub.p.set
    stub._is_solid = lambda x, y, z: F.Build._is_solid(stub, x, y, z)
    membrane = {}

    def set_roof(x, y, z, state):
        F.Build._set_roof(stub, x, y, z, state)
        membrane[(x, y, z)] = state

    stub._set_roof = set_roof
    feature = SimpleNamespace(roof={'material': material}, b=10, storeys=2)
    F.Build.roof_pitched(stub, [feature], set(own))
    return stub.p, membrane


def _current_house() -> dict:
    return {f'{kind}:{pitch}': _digest(house_plan(kind, pitch).cells)
            for kind in HOUSE_KINDS for pitch in HOUSE_PITCHES}


def _current_hip() -> dict:
    out = {}
    for name, cells in HIP_FOOTPRINTS.items():
        plan, membrane = hip_plan(set(cells))
        out[name] = [_digest(plan.cells), _digest(membrane)]
    return out


class RoofGoldenTest(unittest.TestCase):
    """Seam: plans stay byte-identical when roof geometry changes home."""

    def test_house_roofs_match_recorded_plans(self):
        self.assertEqual(_current_house(), HOUSE_GOLDEN)

    def test_feature_hip_roofs_match_recorded_plans(self):
        self.assertEqual(_current_hip(), {k: list(v) for k, v in HIP_GOLDEN.items()})


class EdgeDistanceFieldTest(unittest.TestCase):
    def test_ring_grows_outside_blocked_columns_and_distance_counts_inward(self):
        import roof_geometry as RG
        own = {(x, z) for x in range(5) for z in range(5)}
        dist = RG.edge_distance_field(own, set())
        self.assertEqual(len(dist), 49)
        self.assertEqual(dist[(-1, -1)], 0)
        self.assertEqual(dist[(2, 2)], 3)
        blocked = {(5, z) for z in range(-1, 6)}
        grown = RG.edge_distance_field(own, blocked)
        self.assertFalse(any(c in grown for c in blocked))
        self.assertEqual(RG.chebyshev_depth({(0, 0)}), {(0, 0): 0})

    def test_house_build_does_not_import_the_builder_module(self):
        import inspect
        self.assertNotIn('from feature_build import', inspect.getsource(H))


def _stair(facing: str, shape: str) -> str:
    return f'minecraft:brick_stairs[facing={facing},half=bottom,shape={shape},waterlogged=false]'


class FinalSkinStairShapeTest(unittest.TestCase):
    """A vent pad removes the stair that made a corner inner; the skin must be straight.

    The neighbourhood is the west roof cell (6189, 61, -5608): a north-facing
    inner_right whose same-Y south neighbour is an east-facing stair. Replacing
    that south stair with a full block (the vent pad) leaves no stair neighbour,
    so the game's rule is straight. A second corner that keeps its south stair
    stays inner_right.
    """

    def _skin(self) -> dict:
        return {
            (0, 5, 0): _stair('north', 'inner_right'),
            (0, 5, 1): _stair('east', 'straight'),
            (0, 5, -1): 'minecraft:bricks',
            (2, 5, 0): _stair('north', 'inner_right'),
            (2, 5, 1): _stair('east', 'straight'),
        }

    def test_removed_back_stair_retargets_inner_corner_to_straight(self):
        import roof_geometry as RG
        from roof_lint import stair_shape_mismatches
        cells = self._skin()
        cells[(0, 5, 1)] = 'minecraft:bricks'
        before = stair_shape_mismatches(cells)
        self.assertEqual(
            [(m['x'], m['planned'], m['expected']) for m in before],
            [(0, 'inner_right', 'straight')],
        )
        aligned = RG.align_stair_shapes(cells)
        self.assertIn('shape=straight', aligned[(0, 5, 0)])
        self.assertIn('facing=north', aligned[(0, 5, 0)])
        self.assertEqual(aligned[(2, 5, 0)], _stair('north', 'inner_right'))
        self.assertEqual(aligned[(0, 5, 1)], 'minecraft:bricks')
        self.assertEqual(stair_shape_mismatches(aligned), [])
        self.assertIn('shape=inner_right', cells[(0, 5, 0)])


def rotated_rect(length, width, degrees, cx=20.0, cz=20.0):
    """Cells whose centres fall inside a ``length`` x ``width`` rectangle rotated ``degrees``."""
    import math
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    cells = set()
    for x in range(int(cx - length), int(cx + length) + 1):
        for z in range(int(cz - length), int(cz + length) + 1):
            dx, dz = x + 0.5 - cx, z + 0.5 - cz
            if abs(dx * c + dz * s) <= length / 2 and abs(-dx * s + dz * c) <= width / 2:
                cells.add((x, z))
    return cells


class RotatedHipFitTest(unittest.TestCase):
    """#415: a house drawn square to an angled road gets straight ridge and hip lines."""

    def test_fit_finds_the_rotation_of_a_rotated_rectangle(self):
        from roof_geometry import fit_rotated_rectangle
        for degrees in (20, 35, 50, 70):
            with self.subTest(degrees=degrees):
                fit = fit_rotated_rectangle(rotated_rect(14, 8, degrees))
                self.assertIsNotNone(fit)
                # Sides repeat every 90 degrees; accept the long axis or its perpendicular.
                self.assertTrue(min(abs(fit[0] - degrees % 90), 90 - abs(fit[0] - degrees % 90)) <= 3, fit)
                self.assertGreaterEqual(fit[5], 0.75)

    def test_square_on_and_irregular_footprints_keep_the_grid_field(self):
        from roof_geometry import fit_rotated_rectangle, rotated_hip_depth
        square = {(x, z) for x in range(9) for z in range(6)}
        ell = {(x, z) for x in range(10) for z in range(10) if x < 5 or z < 5}
        for name, cells in (('axis-aligned', square), ('L', ell), ('empty', set())):
            with self.subTest(shape=name):
                self.assertIsNone(fit_rotated_rectangle(cells))
                self.assertIsNone(rotated_hip_depth(cells, cells))

    def test_depth_has_a_straight_ridge_and_a_zero_eave_ring(self):
        import math
        from roof_geometry import rotated_hip_depth
        from cellmath import N8
        own = rotated_rect(14, 8, 30)
        roof = own | {(x + dx, z + dz) for x, z in own for dx, dz in N8}
        depth = rotated_hip_depth(own, roof)
        self.assertEqual(set(depth), roof)
        self.assertTrue(all(d >= 0 for d in depth.values()))
        self.assertTrue(any(d == 0 for d in depth.values()))
        top = max(depth.values())
        ridge = [c for c, d in depth.items() if d >= top - 0.75]
        # The ridge cells lie along one line through the centre, along the long axis (30 degrees).
        a = math.radians(30)
        across = [-(x + 0.5) * math.sin(a) + (z + 0.5) * math.cos(a) for x, z in ridge]
        self.assertLess(max(across) - min(across), 2.5)
        along = [(x + 0.5) * math.cos(a) + (z + 0.5) * math.sin(a) for x, z in ridge]
        self.assertGreater(max(along) - min(along), 3.0)


class WorldNeighbourStairTest(unittest.TestCase):
    """#415: a stair is shaped by every stair beside it in the plan and in the world, whatever the material."""

    @staticmethod
    def stair(facing, shape='straight', mat='brick'):
        return f'minecraft:{mat}_stairs[facing={facing},half=bottom,shape={shape},waterlogged=false]'

    def test_a_stair_in_the_world_shapes_a_planned_stair_of_another_material(self):
        plan = {(5, 60, 5): self.stair('north')}
        world = {(5, 60, 4): self.stair('west', mat='stone')}       # in front, turned left: an outer corner
        aligned = RG.align_stair_shapes(plan, lambda x, y, z: world.get((x, y, z)))
        self.assertIn('shape=outer_left', aligned[5, 60, 5])
        self.assertIn('shape=straight', RG.align_stair_shapes(plan)[5, 60, 5])      # no world: unchanged

    def test_the_plan_wins_over_the_world_and_the_world_is_read_once_per_cell(self):
        plan = {(5, 60, 5): self.stair('north'), (5, 60, 4): 'minecraft:bricks'}
        world = {(5, 60, 4): self.stair('west')}
        reads, warmed = [], []
        aligned = RG.align_stair_shapes(
            plan, lambda x, y, z: reads.append((x, y, z)) or world.get((x, y, z)), warmed.extend)
        self.assertIn('shape=straight', aligned[5, 60, 5])           # the plan's full block hides the world stair
        self.assertNotIn((5, 60, 4), reads)
        self.assertEqual(sorted(reads), sorted(set(reads)))
        self.assertEqual(sorted(warmed), sorted(reads))

    def test_inner_corner_from_a_world_stair_behind(self):
        plan = {(5, 60, 5): self.stair('north')}
        world = {(5, 60, 6): self.stair('west', mat='oak')}            # behind, turned left: an inner corner
        aligned = RG.align_stair_shapes(plan, lambda x, y, z: world.get((x, y, z)))
        self.assertIn('shape=inner_left', aligned[5, 60, 5])

    def test_world_stairs_the_plan_will_retarget_are_reported(self):
        plan = {(5, 60, 4): self.stair('west')}
        world = {(5, 60, 5): self.stair('north')}                      # the plan puts a stair in front of it
        moved = RG.outside_stairs_reshaped(plan, lambda x, y, z: world.get((x, y, z)))
        self.assertEqual([(m['pos'], m['now'], m['then']) for m in moved], [((5, 60, 5), 'straight', 'outer_left')])
        lines = RG.stair_effect_lines(plan, lambda x, y, z: world.get((x, y, z)))
        self.assertIn('1 stair(s) already in the world will change shape', lines[0])
        self.assertEqual(RG.stair_effect_lines({(5, 60, 9): self.stair('west')}, lambda *p: None), [])

    def test_a_neighbouring_world_stair_can_keep_another_straight_and_each_cell_is_read_once(self):
        plan = {(5, 60, 4): self.stair('west')}
        reads = []
        for world, expected in (({(5, 60, 5): self.stair('north')}, [(5, 60, 5)]),
                                # a second world stair beside it, facing the same way, blocks the corner
                                ({(5, 60, 5): self.stair('north'), (6, 60, 5): self.stair('north')}, [])):
            reads.clear()
            moved = RG.outside_stairs_reshaped(
                plan, lambda x, y, z: reads.append((x, y, z)) or world.get((x, y, z)))
            self.assertEqual([m['pos'] for m in moved], expected)
            self.assertEqual(len(reads), len(set(reads)))


class RidgeHalfStepsTest(unittest.TestCase):
    """#299: zone_ridge_half_steps keeps a narrow lidar ridge, ignores lone spikes, clips to the next level."""

    def _grid(self, med):
        med = np.asarray(med, dtype=float)
        readable = np.ones(med.shape, dtype=bool)
        return med, readable

    def test_four_cell_ridge_two_metres_above_eave_gets_four_half_steps(self):
        med = np.full((7, 12), 44.0)
        med[:, 4:8] = 46.0  # 4-cell-wide ridge, 2 m above the eave
        med, readable = self._grid(med)
        zone = np.full(med.shape, 44.0)
        out = FZ.zone_ridge_half_steps(med, zone, readable)
        self.assertTrue((out[:, 4:8] == 4).all())
        self.assertTrue((out[:, :4] == 0).all() and (out[:, 8:] == 0).all())

    def test_float_rise_of_one_and_a_half_metres_gives_three_half_steps(self):
        med = np.full((7, 12), 44.0)
        med[:, 4:8] = 45.5
        med, readable = self._grid(med)
        out = FZ.zone_ridge_half_steps(med, np.full(med.shape, 44.0), readable)
        self.assertTrue((out[:, 4:8] == 3).all())

    def test_nan_cell_is_unreadable_not_a_zero_metre_roof(self):
        med = np.full((7, 12), 44.0)
        med[:, 4:8] = 46.0
        med[3, 5] = np.nan
        med, readable = self._grid(med)
        out = FZ.zone_ridge_half_steps(med, np.full(med.shape, 44.0), readable)
        self.assertEqual(int(out[3, 5]), 0)
        self.assertEqual(int(out[3, 6]), 4)   # neighbours keep their rise
        self.assertTrue(readable.all(), 'the caller mask is left alone')

    def test_flat_zone_with_noise_and_lone_spike_stays_flat(self):
        rng = np.random.default_rng(3)
        med = 44.0 + rng.uniform(-0.3, 0.3, (7, 12))
        med[3, 6] = 45.5  # 1-cell spike, 1.5 m
        med = np.clip(med, 43.7, None)
        med, readable = self._grid(med)
        zone = np.full(med.shape, 44.0)
        out = FZ.zone_ridge_half_steps(med, zone, readable)
        self.assertEqual(int(out.max()), 0)

    def test_lone_spike_in_a_pitched_zone_is_no_ridge(self):
        med = np.full((7, 12), 44.0)
        med[:, 2:6] = 46.0  # real ridge makes the zone pitched
        med[3, 10] = 46.0  # lone chimney away from the ridge
        med, readable = self._grid(med)
        zone = np.full(med.shape, 44.0)
        out = FZ.zone_ridge_half_steps(med, zone, readable)
        self.assertEqual(int(out[3, 10]), 0)
        self.assertEqual(int(out[3, 3]), 4)

    def test_rise_clipped_to_gap_to_next_level(self):
        med = np.full((7, 12), 44.0)
        med[:, 4:8] = 49.0  # 5 m above eave, next level only 3 m up
        med, readable = self._grid(med)
        zone = np.full(med.shape, 44.0)
        out = FZ.zone_ridge_half_steps(med, zone, readable, levels=[44, 47, 50])
        self.assertEqual(int(out.max()), 6)
        # Highest level has no next level: not clipped.
        top = FZ.zone_ridge_half_steps(med, zone, readable, levels=[44])
        self.assertEqual(int(top.max()), 10)

    def test_clip_uses_levels_present_in_zone_by_default(self):
        med = np.full((7, 12), 44.0)
        med[:, :6] = 48.0
        med[:, 6:] = 49.0
        med[:, 0] = 44.0  # Each zone has a real slope up from its own eave.
        med[:, -1] = 47.0
        med, readable = self._grid(med)
        zone = np.full(med.shape, 44.0)
        zone[:, 6:] = 47.0
        out = FZ.zone_ridge_half_steps(med, zone, readable)
        self.assertEqual(int(out[:, :6].max()), 6)  # 4 m rise clipped to the 3 m gap to level 47
        self.assertEqual(int(out[:, 6:].max()), 4)

    def test_call_site_uses_3x3_median_for_ridge_and_5x5_for_zone(self):
        cells = {(x, z) for x in range(10, 22) for z in range(20, 27)}
        x0, z0 = 9, 19
        depth, width = 9, 14
        dsm = np.full((depth, width), 44.0)
        dsm[1:8, 5:9] = 46.0  # x 14..17, 4 wide, 7 deep
        spec = {
            'name': 'unit_ridge_3x3', 'ways': {'99': 'A'}, 'storeys': {'A': 3},
            'roofs': {'A': {'kind': 'pitched', 'material': 'brick'}},
            'groups': [{'nos': ['A'], 'front': 'S', 'mode': 'each'}],
            'modifies': [], 'floor_min': 40, 'step_max': 4, 'door_frame': True,
            'keep_edits': False, 'wall': 'bricks', 'floor': 'smooth_stone', 'ledge_stair': False,
            'zones': {'A': {'from': 'lidar', 'levels': [44, 50], 'bucket': 2, 'axis_deg': 0}},
        }
        seen = {}
        real = FZ.zone_ridge_half_steps

        def spy(med, zone, readable, *a, **k):
            seen['med'], seen['zone'] = med.copy(), zone.copy()
            return real(med, zone, readable, *a, **k)

        builder = F.Build(
            spec, footprints={'A': cells}, world=_StoneWorld(),
            zone_data={'A': {'dsm': dsm, 'ground_y': np.full((depth, width), 40), 'x0': x0, 'z0': z0}},
        )
        with mock.patch.object(FZ, 'metres_aod_to_y', side_effect=lambda m: int(m)), \
                mock.patch.object(FZ, 'zone_ridge_half_steps', side_effect=spy), \
                redirect_stdout(io.StringIO()):
            builder.build()
        np.testing.assert_array_equal(seen['med'], FZ._win(dsm, 3, np.median))
        self.assertFalse(np.array_equal(seen['med'], FZ._win(dsm, 5, np.median)))
        self.assertEqual(float(seen['med'][4, 7]), 46.0)  # peak survives the 3x3 median
        self.assertEqual(float(np.unique(seen['zone'][seen['zone'] > 0]).max()), 44.0)  # zone still on 5x5


class RoofCrestSnapTest(unittest.TestCase):
    """#340: flank speckles must not erode any part of a pitched crest."""

    @staticmethod
    def _noisy_roof(shift, seed=0, transpose=False):
        rng = np.random.default_rng(seed)
        raw, crest = {}, []
        for z in range(24):
            centre = shift(z)
            for offset in range(-8, 9):
                c = (centre + offset, z)
                if transpose:
                    c = c[::-1]
                noise = int(rng.choice([-1, 0, 1])) if offset else 0
                raw[c] = max(0, 12 - 2 * abs(offset) + noise)
                if not offset:
                    crest.append(c)
        levels = {c: 48 for c in raw}
        return RG.regularise_roof_half_heights(raw, levels), levels, crest

    def test_noisy_flanks_keep_each_regularised_crest_column(self):
        for name, shift in [('straight', lambda z: 0),
                            ('drifting', lambda z: z // 4),
                            ('diagonal', lambda z: z)]:
            for transpose in (False, True):
                for seed in range(4):
                    with self.subTest(ridge=name, transpose=transpose, seed=seed):
                        before, levels, crest = self._noisy_roof(shift, seed, transpose)
                        after = FZ.snap_lone_odd_half_heights(before, levels)
                        self.assertTrue(all(before[c] >= 10 for c in crest))
                        losses = [(c, before[c], after[c]) for c in crest
                                  if abs(after[c] - before[c]) > 1]
                        self.assertEqual(losses, [], 'crest column, regularised height, snapped height')
                        self.assertTrue(all(after[c] >= v - v % 2 for c, v in before.items()))
                        self.assertEqual(RG.regularise_roof_half_heights(after, levels), after)
                        self.assertEqual(FZ._lone_odd_half_heights(after), [])
                        self.assertEqual(FZ.snap_lone_odd_half_heights(after, levels), after)
                        self.assertEqual(FZ.snap_lone_odd_half_heights(
                            dict(reversed(list(before.items()))), levels), after)

    def test_lower_crest_is_preserved_beside_a_taller_peak(self):
        before, levels, crest = self._noisy_roof(lambda z: z // 4)
        # Join unequal ridges in one zone: protecting just its tallest point is insufficient.
        taller = {(x + 40, z): v + 8 for (x, z), v in before.items()}
        before.update(taller)
        before.update({(x, 0): min(8, 2 * (x - 8)) for x in range(9, 32)})
        levels = {c: 48 for c in before}
        self.assertEqual(RG.regularise_roof_half_heights(before, levels), before)
        after = FZ.snap_lone_odd_half_heights(before, levels)
        for c in crest + [(x + 40, z) for x, z in crest]:
            self.assertLessEqual(abs(after[c] - before[c]), 1, c)

    def test_snap_and_cliff_cap_do_not_cross_semantic_zone_boundaries(self):
        left = {(x, z): v for z, row in enumerate([[0, 1, 4], [0, 2, 4], [0, 1, 4]])
                for x, v in enumerate(row)}
        right = {(x + 3, z): v + 10 for (x, z), v in left.items()}
        left_levels = {c: 48 for c in left}
        right_levels = {c: 53 for c in right}
        before = {**left, **right}
        levels = {**left_levels, **right_levels}
        expected = {**FZ.snap_lone_odd_half_heights(left, left_levels),
                    **FZ.snap_lone_odd_half_heights(right, right_levels)}
        self.assertEqual(FZ.snap_lone_odd_half_heights(before, levels), expected)
        self.assertEqual(before, {**left, **right}, 'the input must not be mutated')

    def test_zero_passes_keeps_the_input_and_one_pass_respects_the_floor(self):
        before, levels, _ = self._noisy_roof(lambda z: z // 4)
        self.assertEqual(FZ.snap_lone_odd_half_heights(before, levels, passes=0), before)
        after = FZ.snap_lone_odd_half_heights(before, levels, passes=1)
        self.assertTrue(all(after[c] >= v - v % 2 for c, v in before.items()))
        self.assertEqual(RG.regularise_roof_half_heights(after, levels), after)


class BlockFamilyVariantsTest(unittest.TestCase):
    """Seam: family_variants exposes full/slab/stairs without cross-mat swaps."""

    def test_brick_has_all_three(self):
        v = RM.family_variants('brick')
        self.assertEqual(v['full'], 'bricks')
        self.assertEqual(v['slab'], 'brick_slab')
        self.assertEqual(v['stairs'], 'brick_stairs')

    def test_smooth_stone_has_no_stairs(self):
        v = RM.family_variants('smooth_stone')
        self.assertEqual(v['full'], 'smooth_stone')
        self.assertEqual(v['slab'], 'smooth_stone_slab')
        self.assertNotIn('stairs', v)

    def test_deepslate_tile_full_is_plural(self):
        v = RM.family_variants('deepslate_tile')
        self.assertEqual(v['full'], 'deepslate_tiles')
        self.assertEqual(v['stairs'], 'deepslate_tile_stairs')


class PitchedRoofStatesTest(unittest.TestCase):
    """Seam: pitched_roof_states — gable, hip, valley; shape from same-Y neighbours (#149, #169)."""

    def _place_membrane(self, half, states):
        """Write pitched membrane into MemoryBlocks at ``y = roof_block_y(half)``."""
        from block_store import MemoryBlocks
        store = MemoryBlocks(default='air')
        for (x, z), st in states.items():
            store.write((x, RG.roof_block_y(half[(x, z)]), z), st)
        store.flush()
        return store

    def _assert_shapes_match_neighbours(self, half, states):
        """Planned stair shapes must equal the vanilla same-Y neighbour rule."""
        from roof_lint import stair_shape_mismatches
        adjusted = RG.regularise_roof_half_heights(half)
        store = self._place_membrane(adjusted, states)
        positions = [(x, RG.roof_block_y(adjusted[(x, z)]), z) for x, z in states]
        self.assertEqual(stair_shape_mismatches(store.read_many(positions)), [])

    def test_gable_stairs_face_upslope_ridge_bottom_half_slab(self):
        # Ridge along x at z=2; half-heights rise by 2 toward the ridge.
        half = {}
        mats = {}
        for x in range(0, 5):
            for z in range(0, 5):
                half[(x, z)] = 20 + 2 * (2 - abs(z - 2))
                mats[(x, z)] = 'brick'
        warn: list[str] = []
        states = RG.pitched_roof_states(half, mats, warnings=warn)
        self.assertEqual(warn, [])
        # Low-z slope: +z is south, so facing south toward the ridge
        self.assertEqual(
            states[(2, 0)],
            'brick_stairs[facing=south,half=bottom,shape=straight,waterlogged=false]',
        )
        self.assertEqual(
            states[(2, 1)],
            'brick_stairs[facing=south,half=bottom,shape=straight,waterlogged=false]',
        )
        # High-z slope faces north (upslope toward ridge)
        self.assertEqual(
            states[(2, 3)],
            'brick_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]',
        )
        self.assertEqual(states[(2, 2)], 'brick_slab[type=bottom,waterlogged=false]')
        self._assert_shapes_match_neighbours(half, states)

    def test_hip_outer_corner_shape(self):
        # Pyramid: half = 2 * chebyshev dist from outside of a 5×5 (edge=0).
        cells = {(x, z) for x in range(5) for z in range(5)}
        half = {}
        for x, z in cells:
            d = min(x, z, 4 - x, 4 - z)
            half[(x, z)] = 10 + 2 * d
        mats = {c: 'brick' for c in half}
        states = RG.pitched_roof_states(half, mats)
        # Side mid (edge ring): straight stair facing in
        self.assertEqual(
            states[(2, 0)],
            'brick_stairs[facing=south,half=bottom,shape=straight,waterlogged=false]',
        )
        # Footprint corner: diagonal rise only → outer hip stair from edge neighbour
        corner = states[(0, 0)]
        self.assertIn('brick_stairs', corner)
        self.assertIn('shape=outer_', corner)
        self.assertIn('waterlogged=false', corner)
        self.assertEqual(states[(2, 2)], 'brick_slab[type=bottom,waterlogged=false]')
        self._assert_shapes_match_neighbours(half, states)

    def test_valley_inner_corner_shape(self):
        # At (1,1)=12: east and north are +2 uphill; NE diagonal is not higher → valley.
        half = {
            (0, 0): 10, (1, 0): 12, (2, 0): 14,
            (0, 1): 12, (1, 1): 12, (2, 1): 14,
            (0, 2): 14, (1, 2): 14, (2, 2): 12,
        }
        mats = {c: 'brick' for c in half}
        states = RG.pitched_roof_states(half, mats)
        v = states[(1, 1)]
        self.assertIn('brick_stairs', v)
        self.assertIn('shape=inner_', v)
        self.assertIn('half=bottom', v)
        self.assertIn('waterlogged=false', v)
        self._assert_shapes_match_neighbours(half, states)

    def test_valley_inner_even_when_diagonal_is_higher(self):
        # Same valley ups at (1,1), but NE diagonal is taller — old height-diff
        # logic called this outer; same-Y neighbours still form an inner.
        half = {
            (0, 0): 10, (1, 0): 12, (2, 0): 14,
            (0, 1): 12, (1, 1): 12, (2, 1): 14,
            (0, 2): 14, (1, 2): 14, (2, 2): 16,
        }
        mats = {c: 'brick' for c in half}
        states = RG.pitched_roof_states(half, mats)
        self.assertIn('shape=inner_', states[(1, 1)])
        self._assert_shapes_match_neighbours(half, states)

    def test_straight_run_same_height_neighbours(self):
        half = {(x, 0): 10 for x in range(3)}
        half.update({(x, 1): 12 for x in range(3)})
        mats = {c: 'brick' for c in half}
        states = RG.pitched_roof_states(half, mats)
        for x in range(3):
            self.assertEqual(
                states[(x, 0)],
                'brick_stairs[facing=south,half=bottom,shape=straight,waterlogged=false]',
            )
        self._assert_shapes_match_neighbours(half, states)

    def test_three_rises_picks_stable_facing_and_neighbour_shape(self):
        # Local saddle: N/E/S all +2 → ortho pair (N,E), facing north; shape from neighbours.
        half = {
            (1, 0): 14,
            (0, 1): 12, (1, 1): 12, (2, 1): 14,
            (1, 2): 14,
        }
        mats = {c: 'brick' for c in half}
        states = RG.pitched_roof_states(half, mats)
        self.assertIn('brick_stairs', states[(1, 1)])
        self.assertIn('facing=north', states[(1, 1)])
        self._assert_shapes_match_neighbours(half, states)

    def test_slab_beside_stair_does_not_force_corner(self):
        # Odd half-height neighbour is a bottom-half slab, not a stair → shape stays straight.
        half = {(0, 0): 10, (1, 0): 11, (0, 1): 12}
        mats = {c: 'brick' for c in half}
        states = RG.pitched_roof_states(half, mats)
        self.assertIn('brick_stairs', states[(0, 0)])
        self.assertIn('shape=straight', states[(0, 0)])
        self.assertIn('_slab', states[(1, 0)])
        self._assert_shapes_match_neighbours(half, states)

    def test_stair_run_ending_in_low_step_gives_bottom_half_slab(self):
        # #207: odd half-height is a low step; surface half a block below a full block.
        half = {(0, 0): 10, (1, 0): 12, (2, 0): 13}
        mats = {c: 'brick' for c in half}
        states = RG.pitched_roof_states(half, mats)
        self.assertEqual(states[(2, 0)], 'brick_slab[type=bottom,waterlogged=false]')
        self.assertIn('brick_stairs', states[(0, 0)])

    def test_set_roof_marks_bottom_half_slabs_and_survives_apply_rewrite(self):
        from site_kit import Plan, rewrite_bottom_half_slabs
        stub = mock.Mock()
        stub.p = Plan('t')
        F.Build._set_roof(stub, 1, 60, 1, 'brick_slab[type=bottom,waterlogged=false]')
        F.Build._set_roof(stub, 2, 60, 1, 'bricks')
        self.assertEqual(stub.p.keep_slab_cells, {(1, 60, 1)})
        self.assertEqual(rewrite_bottom_half_slabs(stub.p), 0)
        self.assertIn('type=bottom', stub.p.cells[(1, 60, 1)])

    def test_roof_block_y_odd_goes_up_even_unchanged(self):
        self.assertEqual([RG.roof_block_y(h) for h in range(12, 17)], [6, 7, 7, 8, 8])

    def test_odd_h_neighbouring_stairs_have_matching_shapes(self):
        # Stairs at odd h (13, 14 share roof_block_y 7) must satisfy the same-Y shape rule.
        half = {(0, 0): 13, (1, 0): 13, (2, 0): 15, (0, 1): 14, (1, 1): 15, (2, 1): 15,
                (0, 2): 15, (1, 2): 17, (2, 2): 17}
        states = RG.pitched_roof_states(half, {c: 'brick' for c in half})
        self.assertTrue(any('_stairs' in st for st in states.values()))
        self._assert_shapes_match_neighbours(half, states)

    def test_bottom_half_slab_surface_rises_steadily_h12_to_h16(self):
        # #209: surfaces 7, 7.5, 8, 8.5, 9, never a sawtooth.
        half = {(i, 0): 12 + i for i in range(5)}
        states = RG.pitched_roof_states(half, {c: 'brick' for c in half})
        surf = []
        for (x, z), h in sorted(half.items()):
            y = RG.roof_block_y(h)
            surf.append(y + (0.5 if 'type=bottom' in states[(x, z)] else 1))
        self.assertEqual(surf, [7, 7.5, 8, 8.5, 9])

    def _roof_stub(self, below):
        stub = mock.Mock()
        stub.p = F.Plan('t')
        stub.wall = 'bricks'
        if below is not None:
            stub.p.set(1, 59, 1, below)
        stub.live.block.return_value = 'minecraft:air'
        stub._is_solid = lambda x, y, z: F.Build._is_solid(stub, x, y, z)
        return stub

    def test_set_roof_puts_wall_under_slab_over_air(self):
        stub = self._roof_stub(None)
        F.Build._set_roof(stub, 1, 60, 1, 'brick_slab[type=bottom,waterlogged=false]')
        self.assertEqual(stub.p.cells[(1, 59, 1)], 'minecraft:bricks')

    def test_set_roof_keeps_existing_solid_under_slab(self):
        stub = self._roof_stub('stone')
        F.Build._set_roof(stub, 1, 60, 1, 'brick_slab[type=bottom,waterlogged=false]')
        self.assertEqual(stub.p.cells[(1, 59, 1)], 'minecraft:stone')

    def test_two_wide_even_ridge_has_opposed_stairs(self):
        half = {(0, 0): 10, (1, 0): 12, (2, 0): 12, (3, 0): 10}
        mats = {c: 'brick' for c in half}
        states = RG.pitched_roof_states(half, mats)
        self.assertIn('brick_stairs[facing=east,', states[(1, 0)])
        self.assertIn('brick_stairs[facing=west,', states[(2, 0)])
        self._assert_shapes_match_neighbours(half, states)

    def test_valley_low_step_is_bottom_half_slab_never_top(self):
        half = {(0, 0): 12, (1, 0): 11, (2, 0): 12}
        mats = {c: 'brick' for c in half}
        states = RG.pitched_roof_states(half, mats)
        self.assertEqual(states[(1, 0)], 'brick_slab[type=bottom,waterlogged=false]')
        self.assertFalse(any('type=top' in st for st in states.values()))

    def test_slab_family_fallback_still_full_block(self):
        half = {(0, 0): 10, (1, 0): 11}
        warns: list[str] = []
        with mock.patch.object(RG, 'family_variants',
                               return_value={'full': 'stone', 'stairs': 'stone_stairs'}):
            states = RG.pitched_roof_states(half, {c: 'stone' for c in half}, warns)
        self.assertEqual(states[(1, 0)], 'stone')
        self.assertTrue(any('no slab' in w for w in warns))

    def test_full_block_beside_stair_does_not_force_corner(self):
        # Ridge full block upslope of a stair — not a stair neighbour, shape stays straight.
        half = {(0, 0): 10, (1, 0): 12}
        mats = {c: 'brick' for c in half}
        states = RG.pitched_roof_states(half, mats)
        self.assertEqual(
            states[(0, 0)],
            'brick_stairs[facing=east,half=bottom,shape=straight,waterlogged=false]',
        )
        self.assertEqual(states[(1, 0)], 'bricks')
        self._assert_shapes_match_neighbours(half, states)

    def test_full_block_fallback_is_not_a_stair_neighbour(self):
        half = {(0, 0): 10, (1, 0): 10, (0, 1): 10, (1, 1): 12}
        mats = {c: 'brick' for c in half}
        mats[(1, 0)] = 'smooth_stone'  # no vanilla stair family
        states = RG.pitched_roof_states(half, mats)
        self.assertEqual(states[(1, 0)], 'smooth_stone')
        self.assertIn('shape=straight', states[(0, 0)])
        self._assert_shapes_match_neighbours(half, states)

    def test_regularisation_is_stable_and_does_not_mutate_input(self):
        raw = {(0, 0): 10, (1, 0): 18, (2, 0): 20, (3, 0): 22}
        expected = {(0, 0): 10, (1, 0): 12, (2, 0): 14, (3, 0): 16}
        self.assertEqual(RG.regularise_roof_half_heights(raw), expected)
        self.assertEqual(RG.regularise_roof_half_heights(dict(reversed(list(raw.items())))), expected)
        self.assertEqual(RG.regularise_roof_half_heights(expected), expected)
        self.assertEqual(raw[(1, 0)], 18)

    def test_regularise_lowers_cliff_taller_than_one_stair(self):
        raw = {(0, 0): 10, (1, 0): 18}
        got = RG.regularise_roof_half_heights(raw)
        self.assertEqual(got[(0, 0)], 10)
        self.assertEqual(got[(1, 0)], 12)

    def test_missing_stairs_falls_back_to_full_and_warns(self):
        half = {(0, 0): 10, (1, 0): 12}
        mats = {(0, 0): 'smooth_stone', (1, 0): 'smooth_stone'}
        warn: list[str] = []
        states = RG.pitched_roof_states(half, mats, warnings=warn)
        self.assertEqual(states[(0, 0)], 'smooth_stone')
        self.assertTrue(any('no stairs' in w for w in warn))


class RoofTerraceEdgesTest(unittest.TestCase):
    """#309: trim backed terrace edges without changing existing slopes or zone steps."""

    def assert_clean(self, half, states):
        from roof_lint import ridge_jogs, stair_shape_mismatches
        cells = {(x, RG.roof_block_y(half[(x, z)]), z): st for (x, z), st in states.items()}
        self.assertEqual(ridge_jogs(cells), [])
        self.assertEqual(stair_shape_mismatches(cells), [])

    def test_full_and_half_block_drops_in_every_direction(self):
        for facing, (dx, dz) in RG.ROOF_DELTA.items():
            for drop in (1, 2):
                with self.subTest(facing=facing, drop=drop):
                    # A three-column terrace between the lower roof and a higher slope.
                    half = {(dx * i - dz * j, dz * i + dx * j): h
                            for i, h in enumerate((22 - drop, 22, 22, 22, 24))
                            for j in range(3)}
                    before = dict(half)
                    states = RG.pitched_roof_states(half, dict.fromkeys(half, 'brick'))
                    edge = (dx - dz, dz + dx)
                    expected = (RG._roof_stair('brick', facing) if drop == 2 else
                                'brick_slab[type=bottom,waterlogged=false]')
                    self.assertEqual(states[edge], expected)
                    self.assertEqual(states[(2 * dx - dz, 2 * dz + dx)], 'bricks')
                    self.assertEqual(half, before)
                    self.assert_clean(half, states)

    def test_perpendicular_drops_use_slab_and_keep_deck_interior_full(self):
        for north_drop in (1, 2):
            with self.subTest(north_drop=north_drop):
                half = {(x, z): 22 for x in range(3) for z in range(3)}
                half.update({(-1, z): 20 for z in range(3)})
                half.update({(x, -1): 22 - north_drop for x in range(3)})
                states = RG.pitched_roof_states(half, dict.fromkeys(half, 'brick'))
                self.assertEqual(states[(0, 0)], 'brick_slab[type=bottom,waterlogged=false]')
                self.assertEqual(states[(0, 1)], RG._roof_stair('brick', 'east'))
                self.assertEqual(states[(1, 1)], 'bricks')
                self.assert_clean(half, states)

    def test_opposed_drops_and_missing_backing_keep_full_block(self):
        cases = (
            {(-1, 0): 20, (0, 0): 22, (1, 0): 20, (0, 1): 22},
            {(-1, 0): 20, (0, 0): 22, (0, 1): 22},
        )
        for half in cases:
            with self.subTest(half=half):
                states = RG.pitched_roof_states(half, dict.fromkeys(half, 'brick'))
                self.assertEqual(states[(0, 0)], 'bricks')

    def test_half_step_higher_neighbour_keeps_full_block(self):
        half = {(-1, 0): 20, (0, 0): 22, (1, 0): 22, (0, 1): 23}
        states = RG.pitched_roof_states(half, dict.fromkeys(half, 'brick'))
        self.assertEqual(states[(0, 0)], 'bricks')

    def test_lower_or_backing_cell_in_other_zone_does_not_trim_edge(self):
        half = {(0, 0): 20, (1, 0): 22, (2, 0): 22, (3, 0): 24}
        for boundary in ((0, 0), (2, 0)):
            with self.subTest(boundary=boundary):
                levels = dict.fromkeys(half, 0)
                levels[boundary] = 1
                states = RG.pitched_roof_states(half, dict.fromkeys(half, 'brick'), levels=levels)
                self.assertEqual(states[(1, 0)], 'bricks')

    def test_existing_slope_stairs_and_odd_slabs_stay_unchanged(self):
        half = {(x, z): h for x, h in enumerate((20, 22, 23, 24, 26)) for z in range(3)}
        states = RG.pitched_roof_states(half, dict.fromkeys(half, 'brick'))
        self.assertEqual(states[(0, 1)], RG._roof_stair('brick', 'east'))
        self.assertEqual(states[(2, 1)], 'brick_slab[type=bottom,waterlogged=false]')
        self.assertEqual(states[(3, 1)], RG._roof_stair('brick', 'east'))
        self.assert_clean(half, states)

    def test_missing_terrace_variants_fall_back_and_warn(self):
        for drop, missing in ((2, 'stairs'), (1, 'slab')):
            material = 'stone'
            with self.subTest(material=material, drop=drop):
                half = {(x, 0): h for x, h in enumerate((22 - drop, 22, 22, 22, 24))}
                warnings = []
                with mock.patch.object(RG, 'family_variants', return_value={'full': material}):
                    states = RG.pitched_roof_states(half, dict.fromkeys(half, material), warnings)
                self.assertEqual(states[(1, 0)], material)
                self.assertTrue(any(f'no {missing}' in warning for warning in warnings))


class RoofRidgeCapsTest(unittest.TestCase):
    """#304: bounded narrow maxima keep their caps; #309 also trims wider terrace edges."""

    @staticmethod
    def profile(width=1, odd=0, axis='x'):
        heights = [0, 2, 4] + [6] * width + [4, 2, 0]
        return {((i, j) if axis == 'x' else (j, i)): 20 + h + odd
                for i, h in enumerate(heights) for j in range(5)}

    def assert_clean(self, half, states):
        from roof_lint import ridge_jogs, stair_shape_mismatches
        cells = {(x, RG.roof_block_y(half[(x, z)]), z): st for (x, z), st in states.items()}
        self.assertEqual(ridge_jogs(cells), [])
        self.assertEqual(stair_shape_mismatches(cells), [])

    def test_even_and_odd_caps_in_both_axes_and_wider_deck_interior_unchanged(self):
        for width in (1, 2, 3, 4):
            for odd in (0, 1):
                for axis in ('x', 'z'):
                    with self.subTest(width=width, odd=odd, axis=axis):
                        half = self.profile(width, odd, axis)
                        before = dict(half)
                        runs = RG.roof_ridge_runs(half)
                        self.assertEqual(len(runs), 1)
                        self.assertEqual((runs[0].width, runs[0].axis), (width, axis))
                        self.assertEqual(len(runs[0].cells), 5 * width)
                        self.assertEqual(half, before)
                        states = RG.pitched_roof_states(half, dict.fromkeys(half, 'brick'))
                        for c in runs[0].cells:
                            if width == 1 or (width >= 3 and odd):
                                self.assertEqual(states[c], 'brick_slab[type=bottom,waterlogged=false]')
                            elif width == 2:
                                cross = c[0 if axis == 'x' else 1]
                                facing = ('east' if cross == 3 else 'west') if axis == 'x' else (
                                    'south' if cross == 3 else 'north')
                                self.assertEqual(states[c], RG._roof_stair('brick', facing))
                            else:
                                cross = c[0 if axis == 'x' else 1]
                                if cross in (3, width + 2):
                                    facing = ('east' if cross == 3 else 'west') if axis == 'x' else (
                                        'south' if cross == 3 else 'north')
                                    self.assertEqual(states[c], RG._roof_stair('brick', facing))
                                else:
                                    self.assertEqual(states[c], 'bricks')
                        self.assert_clean(half, states)

    def test_run_with_higher_neighbour_at_end_is_not_partially_capped(self):
        half = self.profile()
        half[(3, 5)] = 27  # One half-step up at the end invalidates the entire h=26 run.
        states = RG.pitched_roof_states(half, dict.fromkeys(half, 'brick'))
        self.assertTrue(all(states[(3, z)] == 'bricks' for z in range(5)))
        self.assertFalse(any((3, 0) in run.cells for run in RG.roof_ridge_runs(half)))

    def test_zone_boundary_and_missing_flank_are_not_lower_roof(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                half = self.profile()
                levels = dict.fromkeys(half, 0)
                if missing:
                    del half[(4, 2)]
                    del levels[(4, 2)]
                else:
                    levels.update({(4, z): 1 for z in range(5)})
                runs = RG.roof_ridge_runs(half, levels)
                self.assertIsNone(next(run for run in runs if (3, 2) in run.cells).width)
                states = RG.pitched_roof_states(half, dict.fromkeys(half, 'brick'), levels=levels)
                self.assertTrue(all(states[(3, z)] == 'bricks' for z in range(5)))

    def test_apex_needs_lower_cells_on_both_axes(self):
        x_only = {(-1, 0): 24, (0, 0): 26, (1, 0): 24}
        run = next(r for r in RG.roof_ridge_runs(x_only) if r.cells == {(0, 0)})
        self.assertEqual((run.width, run.axis), (None, None))
        both = {**x_only, (0, -1): 24, (0, 1): 24}
        run = next(r for r in RG.roof_ridge_runs(both) if r.cells == {(0, 0)})
        self.assertEqual(run.width, 1)

    def test_square_l_and_t_plateaus_trim_edges_without_becoming_ridge_caps(self):
        slab = 'brick_slab[type=bottom,waterlogged=false]'
        cases = (
            {(0, 0): slab, (0, 1): slab, (1, 0): slab, (1, 1): slab},
            {(0, 0): 'bricks', (0, 1): 'bricks', (0, 2): slab, (1, 2): 'bricks'},
            {(0, 0): 'bricks', (1, 0): RG._roof_stair('brick', 'south'),
             (2, 0): 'bricks', (1, 1): 'bricks', (1, 2): 'bricks'},
        )
        for expected in cases:
            top = set(expected)
            with self.subTest(top=top):
                half = {(x + dx, z + dz): 24 for x, z in top for dx, dz in F.N8}
                half.update(dict.fromkeys(top, 26))
                states = RG.pitched_roof_states(half, dict.fromkeys(half, 'brick'))
                self.assertEqual({c: states[c] for c in top}, expected)
                self.assertIsNone(next(run for run in RG.roof_ridge_runs(half) if run.cells == top).width)
                self.assert_clean(half, states)

    def test_diagonal_rise_keeps_first_pass_stair(self):
        half = {(0, 0): 26, (-1, 0): 24, (1, 0): 26, (1, 1): 28}
        states = RG.pitched_roof_states(half, dict.fromkeys(half, 'brick'))
        self.assertIn('_stairs[', states[(0, 0)])
        self.assertFalse(any((0, 0) in run.cells for run in RG.roof_ridge_runs(half)))
        self.assert_clean(half, states)

    def test_flat_deck_gets_no_corner_stair_from_a_diagonal_taller_zone(self):
        # #311: a flat deck whose only higher neighbour is a taller zone across
        # a diagonal is beside a wall-backed step, not a hip eave: no stair.
        half = {(x, z): 116 for x in range(4) for z in range(4)}
        for c in ((2, 2), (3, 2), (2, 3), (3, 3)):
            half[c] = 120
        mats = dict.fromkeys(half, 'brick')
        zones = {c: (49 if half[c] == 120 else 47) for c in half}
        states = RG.pitched_roof_states(half, mats, levels=zones)
        self.assertNotIn('_stairs[', states[(1, 1)])
        self.assertEqual(states[(1, 1)], 'bricks')
        # same diagonal inside one zone is a real hip corner and keeps its stair
        same = RG.pitched_roof_states(half, mats, levels=dict.fromkeys(half, 47))
        self.assertIn('_stairs[', same[(1, 1)])

    def test_new_ridge_pair_shapes_include_same_y_slope_neighbours(self):
        # Odd h=25 slope stairs share block Y=13 with the even h=26 ridge.
        half = self.profile(2)
        half.update({(3, -1): 25, (4, -1): 25, (3, -2): 27})
        states = RG.pitched_roof_states(half, dict.fromkeys(half, 'brick'))
        self.assertIn('_stairs[', states[(3, -1)])
        self.assertEqual(RG.roof_block_y(half[(3, -1)]), RG.roof_block_y(half[(3, 0)]))
        self.assertIn('facing=east,', states[(3, 2)])
        self.assertIn('facing=west,', states[(4, 2)])
        self.assert_clean(half, states)

    def test_hip_builder_caps_apex_and_rectangular_two_wide_ridge(self):
        from types import SimpleNamespace
        from roof_lint import ridge_jogs, stair_shape_mismatches
        for sx, sz, width in ((5, 5, 1), (6, 9, 2)):
            with self.subTest(footprint=(sx, sz)):
                own = {(x, z) for x in range(sx) for z in range(sz)}
                stub = mock.Mock()
                stub.p = F.Plan('hip')
                stub.wall = 'bricks'
                stub.owner, stub.foreign, stub.shared = own, set(), set()
                stub._roof_warnings = []
                stub.live.block.return_value = 'minecraft:air'
                stub.set = stub.p.set
                stub._is_solid = lambda x, y, z: F.Build._is_solid(stub, x, y, z)
                membrane = {}

                def set_roof(x, y, z, state):
                    F.Build._set_roof(stub, x, y, z, state)
                    membrane[(x, y, z)] = state

                stub._set_roof = set_roof
                feature = SimpleNamespace(roof={'material': 'brick'}, b=10, storeys=2)
                F.Build.roof_pitched(stub, [feature], own)
                peak = max(p[1] for p in membrane)
                ridge = {p: st for p, st in membrane.items() if p[1] == peak}
                if width == 1:
                    self.assertEqual(len(ridge), 1)
                    (x, y, z), state = next(iter(ridge.items()))
                    self.assertEqual(state, 'brick_slab[type=bottom,waterlogged=false]')
                    self.assertEqual(stub.p.cells[(x, y - 1, z)], 'minecraft:bricks')
                    self.assertIn((x, y, z), stub.p.keep_slab_cells)
                else:
                    self.assertEqual({p[0] for p in ridge}, {2, 3})
                    for (x, y, z), state in ridge.items():
                        self.assertIn(f'facing={"east" if x == 2 else "west"},', state)
                self.assertEqual(ridge_jogs(membrane), [])
                self.assertEqual(stair_shape_mismatches(membrane), [])


class SharedRoofVariantTest(unittest.TestCase):
    def test_pitched_helpers_resolve_shared_block_aliases(self):
        from roof_geometry import _roof_slab, _roof_stair
        self.assertEqual(_roof_slab('minecraft:brick_slab', 'bottom'),
                         'brick_slab[type=bottom,waterlogged=false]')
        self.assertEqual(_roof_stair('brick_stairs', 'north', shape='outer_left'),
                         'brick_stairs[facing=north,half=bottom,shape=outer_left,waterlogged=false]')
