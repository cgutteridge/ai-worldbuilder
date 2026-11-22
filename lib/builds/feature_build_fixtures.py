"""Shared synthetic feature-build inputs; no test cases or place data."""

from __future__ import annotations

from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
import io
import json
import tempfile

import numpy as np

import feature_build as F
import feature_roof_zones as FZ


def _glazing_plan(rows, plan=None):
    """Compose facade-plan fixture data without a production legacy reader."""
    result = dict(plan or {})
    for label, faces in rows.items():
        body = dict(result.get(label) or {})
        body['faces'] = {**body.get('faces', {}), **{face: {'glazing': values} for face, values in faces.items()}}
        result[label] = body
    return result


class _FixtureLidar:
    """External DTM fixture: bare terrain maps to block Y=49 in the test frame."""
    def missing(self, *args):
        return []

    def sample(self, product, east, north, geocraft=True):
        return np.full(np.asarray(east).shape, 38.0)


class _StoneWorld:
    """World stub: solid stone through y<=49, air above."""

    def block(self, x, y, z):
        return 'minecraft:stone' if y <= 49 else 'minecraft:air'


def _entrance_row(**overrides) -> dict:
    """Minimal placed facade_plan entrance for build tests."""
    import provenance as prov

    row = {
        'id': 'T-A',
        'portal_id': 'T-A',
        'face': 'S',
        'leaves': 1,
        'block': 'iron_door',
        'at': [13, 25],
        'floor_offset': 0,
        'provenance': prov.provenance_record(
            source='register_entry', confidence='high',
        ),
    }
    row.update(overrides)
    return row


def build_spec( storeys=3, kind='flat', material='smooth_stone', floor_y=50):
    return {
        'name': 'unit_feature',
        'ways': {'99': 'A'},
        'storeys': {'A': storeys},
        'roofs': {'A': {'kind': kind, 'material': material}},
        'groups': [{'nos': ['A'], 'front': 'S', 'mode': 'each'}],
        'modifies': [],
        'floor_min': floor_y,
        'step_max': 4,
        'door_frame': True,
        'keep_edits': False,
        'wall': 'bricks',
        'floor': 'smooth_stone',
        'ledge_stair': False,
    }


def valid_spec() -> dict:
    return {
        'name': 'demo',
        'ways': {'1': 'A'},
        'storeys': {'A': 3},
        'roofs': {'A': {'kind': 'flat', 'material': 'smooth_stone'}},
        'groups': [{'nos': ['A'], 'front': 'N'}],
    }


def write_spec(self, spec: dict) -> Path:
    tmp = tempfile.TemporaryDirectory()
    self.addCleanup(tmp.cleanup)
    path = Path(tmp.name) / 's.json'
    path.write_text(json.dumps(spec))
    return path


class PitchedZonesFixture:
    """Synthetic ridge and flat-plateau inputs shared by roof-zone tests."""

    def setUp(self):
        # 9×5 mass at x=10..18, z=20..24; grids cover x0=9..19, z0=19..25
        self.cells = {(x, z) for x in range(10, 19) for z in range(20, 25)}
        self.x0, self.z0 = 9, 19
        depth, width = 7, 11
        ground = np.full((depth, width), 40)
        # DSM ridge along x (eave 48 → peak 52 at centre column x=14)
        dsm = np.full((depth, width), 48.0)
        for xi, h in enumerate([48, 48, 49, 50, 52, 50, 49, 48, 48]):
            dsm[:, 1 + xi] = float(h)  # world x = x0+1+xi → 10..18
        self.ridge_zone_data = {
            'A': {'dsm': dsm, 'ground_y': ground, 'x0': self.x0, 'z0': self.z0},
        }
        # Two flat plateaus (no within-zone ridge) for the flat-kind check
        flat_dsm = np.full((depth, width), 45.0)
        flat_dsm[:, 6:] = 50.0  # x >= 15
        self.flat_zone_data = {
            'A': {'dsm': flat_dsm, 'ground_y': ground, 'x0': self.x0, 'z0': self.z0},
        }

    def _spec(self, kind: str, levels: list, material: str = 'brick'):
        return {
            'name': 'unit_pitched_zones',
            'ways': {'99': 'A'},
            'storeys': {'A': 3},
            'roofs': {'A': {'kind': kind, 'material': material}},
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
                'A': {'from': 'lidar', 'levels': levels, 'bucket': 2, 'axis_deg': 0},
            },
        }

    def _build(self, kind: str, levels: list, zone_data: dict, material: str = 'brick'):
        buf = io.StringIO()
        with mock.patch.object(FZ, 'metres_aod_to_y', side_effect=lambda m: int(m)), \
                redirect_stdout(buf):
            plan = F.build(
                self._spec(kind, levels, material=material),
                footprints={'A': self.cells},
                world=_StoneWorld(),
                zone_data=zone_data,
            )
        return plan


class FloatDsmFixture:
    """Shared ridge-roof fixture for tests that feed ``dsm_fine`` (mixin: no tests of its own)."""

    setUp = PitchedZonesFixture.setUp
    _spec = PitchedZonesFixture._spec
    _build = PitchedZonesFixture._build

    def _data(self, fine):
        d = {k: dict(v) for k, v in self.ridge_zone_data.items()}
        if fine is not None:
            d['A']['dsm_fine'] = fine
        return d

    def _builder(self, zone_data):
        return F.Build(
            self._spec('pitched', [48]), footprints={'A': self.cells},
            world=_StoneWorld(), zone_data=zone_data,
        )

    def _prepare(self, zone_data):
        builder = self._builder(zone_data)
        with mock.patch.object(FZ, 'metres_aod_to_y', side_effect=lambda m: int(m)), \
                redirect_stdout(io.StringIO()):
            builder.prepare_zones()
        return builder.features[0]


_lidar_fixture = mock.patch('lidar_provider.tile_client', return_value=_FixtureLidar())


def start_lidar_fixture():
    """Use synthetic bare terrain for a test module."""
    _lidar_fixture.start()


def stop_lidar_fixture():
    """Release the module's synthetic terrain patch."""
    _lidar_fixture.stop()
