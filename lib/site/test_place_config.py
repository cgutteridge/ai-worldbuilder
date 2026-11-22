"""Place identity comes from world config, with neutral defaults."""
from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import coverage
import ground_layer
import house_build
import place_config
import rules_context
import script_env
import site_kit

# The live Southampton world keeps these through config. The unit world does not.
_PLACE = {
    'plan_prefix': 'highfield',
    'structure_namespace': 'southampton_refine',
    'base_y': 37,
    'retired_ground_passes': [
        'highfield_q050_opens',
        'highfield_q050_clears',
        'highfield_q051_ur',
        'highfield_q053_paths',
        'highfield_q054_micro',
        'highfield_q061_stragglers',
        'highfield_q063_m45_edge',
    ],
    'reverted_builds': [
        'portswood_x_broadlands_terrace_facade_1',
        'portswood_x_broadlands_terrace_facade_1b',
        'portswood_x_broadlands_terrace_facade_2',
        'portswood_x_broadlands_terrace_facade_2b',
        'portswood_x_broadlands_terrace_facade_2c',
        'broadlands_facade_revert',
    ],
    'ledger_aliases': {'hampshire-hub': ['aerial']},
    'fetch_hosts': ['tiles.maps.southampton.ac.uk', 'maps.southampton.ac.uk'],
    'fetch_probe': {
        'url': 'https://tiles.maps.southampton.ac.uk/aer/18/130063/87836.png',
        'referer': 'https://maps.southampton.ac.uk/',
    },
    'work_context_keywords': {
        'terrace': ['BROADLANDS_RD', ' BROADLANDS '],
        'ground': ['HIGHFIELD_Q'],
    },
}


@contextmanager
def _patched_world(extra: dict):
    """Merge ``extra`` into the bound world's config and restore it afterwards.

    @param extra: fields to set on ``config/world.json``
    """
    path = Path(script_env.ROOT) / 'config' / 'world.json'
    original = path.read_text(encoding='utf-8')
    data = json.loads(original)
    data.update(extra)
    path.write_text(json.dumps(data), encoding='utf-8')
    script_env.clear_world_caches()
    try:
        yield data
    finally:
        path.write_text(original, encoding='utf-8')
        script_env.clear_world_caches()


class TestNeutralDefaults(unittest.TestCase):
    """A world that omits place keys is not given another place's names."""

    def test_plan_names_namespace_and_floor(self):
        """The unit world uses the cell prefix, worldbuilder, and Y 37."""
        self.assertEqual(place_config.plan_prefix(), 'cell')
        self.assertEqual(place_config.plan_name((48, -44), 'ground'), 'cell_48_m44_ground')
        self.assertEqual(place_config.plan_name((48, 3), 'trees'), 'cell_48_3_trees')
        self.assertEqual(place_config.plan_name((48, -44), 'decor'), 'cell_48_m44_decor')
        self.assertEqual(place_config.structure_namespace(), 'worldbuilder')
        self.assertEqual(site_kit.STRUCTURE_NS, 'worldbuilder')
        self.assertEqual(place_config.base_y(), 37)
        self.assertEqual(place_config.retired_ground_passes(), frozenset())
        self.assertEqual(place_config.reverted_builds(), frozenset())
        self.assertNotIn('highfield_q051_ur', ground_layer.ignored_builds((48, -44)))


class TestConfiguredPlace(unittest.TestCase):
    """Southampton-shaped config keeps the names and floor that world already uses."""

    def test_plan_names_namespace_base_y_and_lists(self):
        """Prefix, namespace, floor, retired passes and reverted builds are read back."""
        with _patched_world(_PLACE):
            self.assertEqual(place_config.plan_name((48, -44), 'ground'), 'highfield_48_m44_ground')
            self.assertEqual(place_config.plan_name((48, -44), 'trees'), 'highfield_48_m44_trees')
            self.assertEqual(place_config.plan_name((48, -44), 'decor'), 'highfield_48_m44_decor')
            self.assertEqual(place_config.structure_namespace(), 'southampton_refine')
            self.assertEqual(site_kit.STRUCTURE_NS, 'southampton_refine')
            self.assertEqual(place_config.base_y(), 37)
            self.assertIn('highfield_q051_ur', place_config.retired_ground_passes())
            self.assertIn('highfield_q051_ur', ground_layer.ignored_builds((48, -44)))
            self.assertIn('broadlands_facade_revert', place_config.reverted_builds())
            self.assertTrue(coverage.touch_excluded('portswood_x_broadlands_terrace_facade_1'))
            self.assertEqual(place_config.ledger_aliases()['hampshire-hub'], ('aerial',))
            self.assertIn('tiles.maps.southampton.ac.uk', place_config.extra_fetch_hosts())
            probe = place_config.fetch_probe()
            self.assertEqual(probe['referer'], 'https://maps.southampton.ac.uk/')
            item = {'id': 'Q-1', 'text': 'BROADLANDS_RD houses'}
            ctx, _reason = rules_context.detect_context(item)
            self.assertEqual(ctx, rules_context.WorkContext.TERRACE)

    def test_base_y_override_is_the_stone_fill_floor(self):
        """Builders fill stone from ``base_y``, not a hard-coded 37."""
        stones = []

        def remember(x, y, z, state):
            if state == 'stone' and (x, z) == (0, 0):
                stones.append(y)

        with _patched_world({'base_y': 35}):
            house = SimpleNamespace(
                owner={(0, 0)},
                houses=[SimpleNamespace(b=40, cells=[(0, 0)])],
                foreign=set(),
                shared=set(),
                set=remember,
                live=SimpleNamespace(block=lambda *_a: 'minecraft:air'),
            )
            house_build.Build.clear(house)
            self.assertEqual(stones, list(range(35, 40)))
            stones.clear()
            feature = SimpleNamespace(
                owner={(0, 0): 0},
                features=[SimpleNamespace(cells=[(0, 0)], b=40, zone_roof=None, storeys=1)],
                foreign=set(),
                shared=set(),
                set=remember,
                live=SimpleNamespace(block=lambda *_a: 'minecraft:air'),
                _prefetch_live=lambda _need: None,
            )
            import feature_build
            feature_build.Build.clear(feature)
            self.assertEqual(stones, list(range(35, 40)))


if __name__ == '__main__':
    unittest.main()
