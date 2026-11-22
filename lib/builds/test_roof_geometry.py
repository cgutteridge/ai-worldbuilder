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

# Recorded on the pre-change code; see module docstring.
HOUSE_GOLDEN = {
    'square:None': '38acbd50986d4f988349ed6a39ce1f73b7a0e7161fad51b975b82adb3bc4878b',
    'square:0.5': '38acbd50986d4f988349ed6a39ce1f73b7a0e7161fad51b975b82adb3bc4878b',
    'square:0.75': '5b73fb17262c6cc4c2764cef27bfeafc6a2a8ead645a2b236c7a52a94044fe52',
    'square:1': '97488d5998a2ed09902443c506a27497031335ee9385823499e9b1f6f8c93908',
    'L:None': '049b1ee6ce86d5f6dbad4c119bee30dc3408232ef1bdc6087c818ab7515d644a',
    'L:0.5': '049b1ee6ce86d5f6dbad4c119bee30dc3408232ef1bdc6087c818ab7515d644a',
    'L:0.75': 'af6e3843a65fad8052f9b4193778ebd3118e0e6b3c01f2557514e41220b7df91',
    'L:1': '4254cd0b23bc0a86fd760be273fde9c589069ce75a71ac62bb8f69f9481a7628',
    'group:None': 'd1feb694bc096d0bc5bb6f6d0ec82b33225c30ae715e7abf261d32d82e88e91b',
    'group:0.5': 'd1feb694bc096d0bc5bb6f6d0ec82b33225c30ae715e7abf261d32d82e88e91b',
    'group:0.75': 'f5d862a50b90cfe834c8591f45214f8e93b925b6368fc9a6846c7d401dc1a9e5',
    'group:1': 'fcc76ff44468d239a48e4eb637ededc7040ac2cd94256f50c17625bd9de47d5b',
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
