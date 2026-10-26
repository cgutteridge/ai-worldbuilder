"""World region targeting and apply refuse-on-mismatch (tickets #35, #57)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import site_kit


class TestResolveRegion(unittest.TestCase):
    def test_default_uses_world_config_region(self):
        """Without an explicit arg, region comes from active world config only."""
        self.assertEqual(site_kit.resolve_region().resolve(), Path(site_kit.REGION).resolve())

    def test_region_env_is_ignored(self):
        """AI_WORLDBUILDER_REGION must not override the active world's save."""
        with tempfile.TemporaryDirectory() as d:
            override = Path(d) / 'bakeoff' / 'region'
            override.mkdir(parents=True)
            with mock.patch.dict(os.environ, {'AI_WORLDBUILDER_REGION': str(override)}):
                self.assertEqual(
                    site_kit.resolve_region().resolve(), Path(site_kit.REGION).resolve()
                )
                self.assertNotEqual(
                    site_kit.resolve_region().resolve(), override.resolve()
                )

    def test_explicit_argument_beats_config(self):
        with tempfile.TemporaryDirectory() as d:
            arg_path = Path(d) / 'arg-region'
            arg_path.mkdir()
            self.assertEqual(
                site_kit.resolve_region(arg_path).resolve(), arg_path.resolve()
            )

    def test_world_default_uses_resolve_region(self):
        self.assertEqual(
            site_kit.World().region.resolve(), Path(site_kit.REGION).resolve()
        )


class TestGameworldFromRegion(unittest.TestCase):
    def test_extracts_save_folder_name(self):
        region = Path(
            '/instance/saves/bakeoff/dimensions/minecraft/overworld/region'
        )
        self.assertEqual(site_kit.gameworld_id_from_region(region), 'bakeoff')

    def test_southampton_ai(self):
        region = Path(
            '/instance/saves/SouthamptonAI/dimensions/minecraft/overworld/region'
        )
        self.assertEqual(site_kit.gameworld_id_from_region(region), 'SouthamptonAI')


class TestEnsureRegionMatchesOpenWorld(unittest.TestCase):
    def test_refuses_when_open_world_differs(self):
        region = Path(
            '/instance/saves/SouthamptonAI/dimensions/minecraft/overworld/region'
        )
        with mock.patch.object(
            site_kit, 'mcp_reported_gameworld', return_value='bakeoff'
        ):
            with self.assertRaises(SystemExit) as ctx:
                site_kit.ensure_region_matches_open_world(region)
        msg = str(ctx.exception)
        self.assertIn('bakeoff', msg)
        self.assertIn('SouthamptonAI', msg)
        self.assertIn('AI_WORLDBUILDER_WORLD_ROOT', msg)
        self.assertNotIn('AI_WORLDBUILDER_REGION', msg)

    def test_allows_when_names_match_casefold(self):
        region = Path('/instance/saves/bakeoff/dimensions/minecraft/overworld/region')
        with mock.patch.object(
            site_kit, 'mcp_reported_gameworld', return_value='Bakeoff'
        ):
            site_kit.ensure_region_matches_open_world(region)

    def test_skips_when_mcp_reports_nothing(self):
        region = Path('/instance/saves/bakeoff/dimensions/minecraft/overworld/region')
        with mock.patch.object(site_kit, 'mcp_reported_gameworld', return_value=None):
            site_kit.ensure_region_matches_open_world(region)

    def test_mcp_reported_gameworld_reads_session_lock(self):
        with tempfile.TemporaryDirectory() as d:
            saves = Path(d) / 'saves'
            bakeoff = saves / 'bakeoff'
            other = saves / 'SouthamptonAI'
            region = bakeoff / 'dimensions' / 'minecraft' / 'overworld' / 'region'
            region.mkdir(parents=True)
            other.mkdir(parents=True)
            (bakeoff / 'session.lock').write_bytes(b'x')
            with mock.patch('mcp_probe.mcp_world_reachable', return_value=True):
                self.assertEqual(
                    site_kit.mcp_reported_gameworld(region=region), 'bakeoff'
                )


class TestApplyRegionGate(unittest.TestCase):
    def setUp(self):
        import tempfile
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.tmp = t.name

    def test_live_apply_calls_region_match_gate(self):
        plan = site_kit.Plan('gate-test')
        plan.set(0, 64, 0, 'stone')
        with mock.patch.object(
            site_kit, 'ensure_region_matches_open_world', side_effect=SystemExit('mismatch')
        ) as gate, mock.patch.dict('os.environ', {'AI_WORLDBUILDER_REGISTRY_ROOT': self.tmp}), mock.patch(
            'osm_footprints.guard', return_value={'outside': {}, 'blind': []}
        ), mock.patch.object(site_kit, 'door_frame_issues', return_value=[]), mock.patch(
            'roof_lint.ridge_jogs', return_value=[]
        ), mock.patch.object(site_kit.World, 'block', return_value='minecraft:stone'):
            with self.assertRaises(SystemExit) as ctx:
                site_kit.apply(plan, dry=False)
        self.assertEqual(str(ctx.exception), 'mismatch')
        gate.assert_called_once()

    def test_dry_apply_skips_region_match_gate(self):
        plan = site_kit.Plan('gate-dry')
        plan.set(0, 64, 0, 'stone')
        with mock.patch.object(
            site_kit, 'ensure_region_matches_open_world'
        ) as gate, mock.patch.dict('os.environ', {'AI_WORLDBUILDER_REGISTRY_ROOT': self.tmp}), mock.patch(
            'osm_footprints.guard', return_value={'outside': {}, 'blind': []}
        ), mock.patch.object(site_kit, 'door_frame_issues', return_value=[]), mock.patch(
            'roof_lint.ridge_jogs', return_value=[]
        ), mock.patch.object(site_kit.World, 'block', return_value='minecraft:stone'):
            site_kit.apply(plan, dry=True)
        gate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
