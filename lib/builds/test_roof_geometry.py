"""Roof geometry shared by the house and feature builders (task 376).

The digests below were recorded on the code before the roof geometry moved out of
``feature_build``; the move must leave every plan byte-identical.
"""
from __future__ import annotations

import hashlib
import json
import unittest
from types import SimpleNamespace
from unittest import mock

import feature_build as F
import house_build as H

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
