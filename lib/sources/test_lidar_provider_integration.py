"""Load synthetic world providers in fresh processes, away from shim module caches."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[2]

TILES = '''
import numpy as np
crs = 'EPSG:32630'
class LidarTiles:
    crs = crs
    def __init__(self, *, download=False):
        assert download is False
        self.ensured = None
        self.pending = []
    def ensure(self, *box):
        self.ensured = box
        return []
    def tile_status(self, *box):
        from pathlib import Path
        self.status_box = box
        return self.pending, [Path(__file__)]
def mc_box_en(x0, z0, x1, z1):
    return (x0, -z1, x1, -z0)
def mc_heights(client, x0, z0, x1, z1, **kwargs):
    assert not client.pending, 'readiness must not sample with missing tiles'
    shape = (x1-x0+1, z1-z0+1)
    ground = np.full(shape, 4)
    diff = np.full(shape, 3)
    return dict(dtm=ground, dsm=ground+diff, ground_y=ground,
                feature=diff, diff=diff, top_y=ground+diff)
'''


class SyntheticLidar(unittest.TestCase):
    def run_provider(self, provider_id, checks):
        """Install a temporary provider with the real system catalogue still present."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'config'
            config.mkdir()
            (config / 'world.json').write_text(json.dumps({
                'gameworld_id': 'synthetic', 'save_path': str(root / 'save'),
            }))
            (config / 'world-frame.json').write_text(json.dumps({
                'crs': 'EPSG:32630', 'offsets': {'easting': 0, 'northing': 0},
                'y_offset': 0, 'cell': {'size': 2, 'home': [0, 0]},
            }))
            package = root / 'providers' / provider_id
            package.mkdir(parents=True)
            modules = {'world': 'manage/synthetic_world', 'tiles': 'get/synthetic_tiles',
                       'query': 'review/synthetic_query', 'extremes': 'review/synthetic_extremes'}
            code = {'world': "class LidarWorld:\n    source = 'synthetic'\n", 'tiles': TILES}
            for role in ('query', 'extremes'):
                code[role] = f"source = 'synthetic {role}'\ndef main(argv=None):\n    return 23\n"
            for role, rel in modules.items():
                path = package / (rel + '.py')
                path.parent.mkdir(exist_ok=True)
                path.write_text(textwrap.dedent(code[role]))
            (config / 'local-providers.json').write_text(json.dumps({'providers': [{
                'id': provider_id, 'types': ['lidar'],
                'package': f'providers/{provider_id}', 'modules': modules,
            }]}))
            boot = f'''
import sys
from pathlib import Path
sys.path.insert(0, {str(TOOL / 'scripts')!r})
import script_env
from active_world import set_world_root
root = Path({str(root)!r})
set_world_root(root)
script_env.bind_world()
'''
            result = subprocess.run([sys.executable, '-c', boot + textwrap.dedent(checks)],
                                    cwd=TOOL, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-6000:])

    def test_shims_and_registry_use_world_provider_with_system_row_present(self):
        system = json.loads((TOOL / 'config/system-providers.json').read_text())
        system_id = next(row['id'] for row in system['providers'] if 'lidar' in row['types'])
        for provider_id in ('synthetic-lidar', system_id):
            with self.subTest(provider_id=provider_id):
                self.run_provider(provider_id, '''
                    import cell_sources, lidar_provider
                    rows = cell_sources.load_place_sources()['providers']
                    assert any(r['home'] == 'system' and 'lidar' in r['types'] for r in rows)
                    assert lidar_provider.lidar_provider_row()['home'] == 'world'
                    import lidar_world, lidar_tiles, lidar_query, lidar_extremes
                    assert lidar_world.LidarWorld().source == 'synthetic'
                    assert lidar_provider.composite_world().source == 'synthetic'
                    assert lidar_tiles is lidar_provider.load_lidar_module('tiles')
                    for role, module in [('query', lidar_query), ('extremes', lidar_extremes)]:
                        assert module.source == 'synthetic ' + role
                        assert module is lidar_provider.load_lidar_module(role)
                        assert module.main([]) == 23
                    import lidar_ghost, cell_registry
                    assert Path(lidar_ghost.__file__) == Path(script_env.TOOL_ROOT) / 'lib/sources/lidar_ghost.py'
                    reg = cell_registry.open_registry(root=root, build_policy=None)
                    blocks = reg.ghosts((0, 0), 'ground')
                    assert len(blocks) == 16
                    assert blocks[(0, 4, 0)] == 'minecraft:black_concrete'
                    assert not (root / 'registry').exists()
                ''')

    def test_type_shims_contain_no_catalogue_ids(self):
        catalogue = json.loads((TOOL / 'config/system-providers.json').read_text())
        for shim in (TOOL / 'shims').glob('lidar_*.py'):
            for row in catalogue['providers']:
                self.assertNotIn(row['id'], shim.read_text(), str(shim))

    def test_readiness_uses_opaque_provider_tiles_and_hashes_only_reported_inputs(self):
        self.run_provider('synthetic-lidar', '''
            import os
            import lidar_provider
            from readiness_evidence import LocalEvidence, file_identity
            evidence = LocalEvidence(root)
            missing, identities = evidence.lidar_status((0, 0))
            assert missing == []
            client = evidence._lidar_client
            assert client.status_box == (-2, -3, 3, 2)
            tile = Path(lidar_provider.load_lidar_module('tiles').__file__)
            assert identities == [(str(tile), file_identity(tile))]
            stamp = tile.stat()
            tile.write_text(tile.read_text() + '\\n# changed input\\n')
            os.utime(tile, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            evidence.refresh()
            assert evidence.lidar_status((0, 0))[1] != identities
            client = evidence._lidar_client
            client.pending = [{'filename': 'opaque.bin', 'tile': 'DSM arbitrary-id', 'estimated_mb': 2}]
            assert evidence.lidar_status((0, 0))[0] == client.pending
            client.pending = []
            import numpy as np
            tiles = lidar_provider.load_lidar_module('tiles')
            tiles.mc_heights = lambda *a, **k: {'dtm': np.array([np.nan]), 'dsm': np.array([1.])}
            try:
                evidence.lidar_status((0, 0))
            except ValueError as exc:
                assert 'no-data' in str(exc)
            else:
                raise AssertionError('readiness accepted no-data')
        ''')


if __name__ == '__main__':
    unittest.main()
