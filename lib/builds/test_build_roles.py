"""Tests for build_roles: load, resolve defaults/overrides, same-model rule."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import build_roles as BR
from unit_test_world import unit_test_cli_checkout


def _write_cfg(path: Path, **fields: str) -> Path:
    data = {
        'tool': fields.get('tool', 'claude-code'),
        'designer': fields.get('designer', 'opus-low'),
        'builder': fields.get('builder', 'sonnet-medium'),
    }
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


class TestLoadBuildRoles(unittest.TestCase):
    def test_live_config_loads(self):
        cfg = BR.load_build_roles()
        self.assertEqual(cfg['tool'], 'claude-code')
        self.assertTrue(cfg['designer'])
        self.assertTrue(cfg['builder'])

    def test_missing_key_raises(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'bad.json'
            path.write_text(json.dumps({'tool': 'x', 'designer': 'a'}), encoding='utf-8')
            with self.assertRaises(ValueError) as ctx:
                BR.load_build_roles(path)
            self.assertIn('builder', str(ctx.exception))


class TestResolveRoles(unittest.TestCase):
    def test_defaults_from_config(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_cfg(Path(d) / 'roles.json')
            roles = BR.resolve_roles(config_path=path)
            self.assertEqual(roles.tool, 'claude-code')
            self.assertEqual(roles.designer, 'opus-low')
            self.assertEqual(roles.builder, 'sonnet-medium')
            self.assertFalse(roles.same_model)
            self.assertTrue(roles.split_sessions)

    def test_overrides_win(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_cfg(Path(d) / 'roles.json')
            roles = BR.resolve_roles(
                designer='haiku',
                builder='haiku',
                tool='cursor',
                config_path=path,
            )
            self.assertEqual(roles.designer, 'haiku')
            self.assertEqual(roles.builder, 'haiku')
            self.assertEqual(roles.tool, 'cursor')
            self.assertTrue(roles.same_model)
            self.assertFalse(roles.split_sessions)

    def test_other_tool_without_designer_defaults_to_builder(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_cfg(Path(d) / 'roles.json')
            roles = BR.resolve_roles(
                builder='composer',
                tool='cursor',
                config_path=path,
            )
            self.assertEqual(roles.designer, 'composer')
            self.assertEqual(roles.builder, 'composer')
            self.assertTrue(roles.same_model)

    def test_matching_tool_fills_designer_from_config(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_cfg(Path(d) / 'roles.json')
            roles = BR.resolve_roles(
                builder='sonnet-high',
                tool='claude-code',
                config_path=path,
            )
            self.assertEqual(roles.designer, 'opus-low')
            self.assertEqual(roles.builder, 'sonnet-high')
            self.assertTrue(roles.split_sessions)

    def test_format_line_and_block(self):
        roles = BR.BuildRoles(
            tool='claude-code',
            designer='opus-low',
            builder='sonnet-medium',
        )
        line = BR.format_roles_line(roles)
        self.assertIn('designer=opus-low', line)
        self.assertIn('builder=sonnet-medium', line)
        self.assertIn('designer then builder', line)
        block = BR.format_roles_block(roles)
        self.assertIn('do not apply', block)
        self.assertIn('feature_build', block)
        same = BR.BuildRoles(tool='cursor', designer='composer', builder='composer')
        self.assertIn('one session', BR.format_roles_line(same))
        self.assertIn('Same model', BR.format_roles_block(same))


class TestBuildRolesCli(unittest.TestCase):
    def test_show_and_resolve_json(self):
        world_name = '_build_roles_cli'
        with unit_test_cli_checkout('scripts/build_roles.py', world_name) as checkout:
            script = checkout / 'scripts' / 'build_roles.py'
            path = _write_cfg(checkout / 'roles.json')
            show = subprocess.run(
                [
                    sys.executable, '-B', str(script),
                    '--world', world_name,
                    '--config', str(path), 'show',
                ],
                cwd=checkout,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(show.returncode, 0, msg=show.stderr)
            self.assertIn('designer=opus-low', show.stdout)

            block = subprocess.run(
                [
                    sys.executable, '-B', str(script),
                    '--world', world_name,
                    '--config', str(path), 'show', '--block',
                ],
                cwd=checkout,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(block.returncode, 0, msg=block.stderr)
            self.assertIn('Designer model: opus-low', block.stdout)
            self.assertIn('do not apply', block.stdout)

            resolved = subprocess.run(
                [
                    sys.executable,
                    '-B',
                    str(script),
                    '--world', world_name,
                    '--config',
                    str(path),
                    'resolve',
                    '--tool',
                    'cursor',
                    '--builder',
                    'composer',
                    '--json',
                ],
                cwd=checkout,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(resolved.returncode, 0, msg=resolved.stderr)
            payload = json.loads(resolved.stdout)
            self.assertEqual(payload['designer'], 'composer')
            self.assertTrue(payload['same_model'])


if __name__ == '__main__':
    unittest.main()
