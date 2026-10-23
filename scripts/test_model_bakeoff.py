"""Unit tests for model bake-off round records and ladder suggestions."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import model_bakeoff as MB

TOOL_ROOT = Path(__file__).resolve().parent.parent
BAKEOFF_WORLD_PREFIX = 'AI_WORLDBUILDER_WORLD_ROOT=$PWD/worlds/bakeoff'


def _seed(root: Path) -> None:
    (root / 'rounds').mkdir(parents=True)
    (root / 'packs' / 'v1').mkdir(parents=True)
    MB.write_json(
        root / 'config.json',
        {
            'pack': 'v1',
            'world': 'bakeoff',
            'cell': [48, -44],
            'cell_note': 'test',
            'ghost_cells': [[48, -44]],
            'active_tiers': ['low', 'mid'],
            'tasks': ['feature_design'],
            'pilot_task': 'feature_design',
            'axes': ['massing', 'keep'],
            'critical_axes': ['massing', 'keep'],
            'max_attempts': 3,
            'grades': ['fail', 'pass', 'great'],
        },
    )
    MB.write_json(
        root / 'catalogue.json',
        {
            'tools': [
                {
                    'id': 'cursor',
                    'name': 'Cursor',
                    'models': [
                        {
                            'id': 'composer',
                            'tier': 'low',
                            'label': 'Composer',
                            'bulk_ok': True,
                        },
                        {
                            'id': 'sonnet',
                            'tier': 'mid',
                            'label': 'Sonnet',
                            'bulk_ok': True,
                        },
                        {
                            'id': 'opus',
                            'tier': 'high',
                            'label': 'Opus',
                            'bulk_ok': False,
                        },
                    ],
                },
                {
                    'id': 'claude',
                    'name': 'Claude.app',
                    'models': [
                        {
                            'id': 'haiku',
                            'tier': 'low',
                            'label': 'Haiku',
                            'bulk_ok': True,
                        },
                    ],
                },
            ],
        },
    )


class TestModelBakeoff(unittest.TestCase):
    def test_open_round_is_self_contained(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            round_ = MB.open_round(
                root,
                tool='cursor',
                model='composer',
                task='feature_design',
            )
            self.assertEqual(round_['id'], 'R-001')
            self.assertEqual(round_['status'], 'ready')
            self.assertEqual(round_['tier'], 'low')
            self.assertEqual(round_['kind'], 'cold')
            self.assertEqual(round_['attempt'], 1)
            self.assertEqual(round_['pack'], 'v1')
            self.assertEqual(round_['cell'], [48, -44])
            self.assertIn('interview', round_)
            self.assertIn('massing', round_['interview']['axes'])
            self.assertIn('usage', round_)
            self.assertIsNone(round_['usage']['total_tokens'])
            checklist = '\n'.join(round_['sitting']['checklist'])
            self.assertIn('lidar_ghost apply', checklist)
            self.assertNotIn('restore-ghost', checklist)
            self.assertIn('FAIRNESS.md', checklist)
            self.assertIn('One apply owner', checklist)
            path = root / 'rounds' / 'R-001.json'
            self.assertTrue(path.is_file())
            disk = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(disk['model']['id'], 'composer')

    def test_record_usage_sums_total(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            round_ = MB.open_round(
                root,
                tool='cursor',
                model='composer',
                task='feature_design',
            )
            updated = MB.record_usage(
                root,
                round_['id'],
                input_tokens=1000,
                output_tokens=250,
                source='cursor-ui',
                notes='paste from usage panel',
            )
            self.assertEqual(updated['usage']['input_tokens'], 1000)
            self.assertEqual(updated['usage']['output_tokens'], 250)
            self.assertEqual(updated['usage']['total_tokens'], 1250)
            self.assertEqual(updated['usage']['source'], 'cursor-ui')
            self.assertIsNotNone(updated['usage']['recorded_at'])

    def test_next_prefers_low_before_mid(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            nxt = MB.suggest_next(root)
            self.assertIsNotNone(nxt)
            self.assertEqual(nxt['tier'], 'low')
            self.assertIn(nxt['model'], ('composer', 'haiku'))

    def test_mid_blocked_until_a_low_is_not_great(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            for tool, model in (('cursor', 'composer'), ('claude', 'haiku')):
                r = MB.open_round(root, tool=tool, model=model, task='feature_design')
                MB.score_round(
                    root,
                    r['id'],
                    overall='great',
                    axes={'massing': 'great', 'keep': 'great'},
                    mech_gate='pass',
                )
            nxt = MB.suggest_next(root)
            self.assertIsNone(nxt)

    def test_mid_unlocks_when_any_low_not_great(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            r = MB.open_round(root, tool='cursor', model='composer', task='feature_design')
            MB.score_round(
                root,
                r['id'],
                overall='pass',
                axes={'massing': 'pass', 'keep': 'pass'},
                mech_gate='pass',
            )
            r2 = MB.open_round(root, tool='claude', model='haiku', task='feature_design')
            MB.score_round(
                root,
                r2['id'],
                overall='great',
                axes={'massing': 'great', 'keep': 'great'},
                mech_gate='pass',
            )
            nxt = MB.suggest_next(root)
            self.assertIsNotNone(nxt)
            self.assertEqual(nxt['tier'], 'mid')
            self.assertEqual(nxt['model'], 'sonnet')

    def test_high_bulk_false_never_suggested(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            r = MB.open_round(root, tool='cursor', model='composer', task='feature_design')
            MB.score_round(
                root,
                r['id'],
                overall='fail',
                axes={'massing': 'fail', 'keep': 'fail'},
                mech_gate='fail',
            )
            r2 = MB.open_round(root, tool='claude', model='haiku', task='feature_design')
            MB.score_round(
                root,
                r2['id'],
                overall='fail',
                axes={'massing': 'fail', 'keep': 'fail'},
                mech_gate='fail',
            )
            # mid open+score fail still should not suggest opus
            r3 = MB.open_round(root, tool='cursor', model='sonnet', task='feature_design')
            MB.score_round(
                root,
                r3['id'],
                overall='fail',
                axes={'massing': 'fail', 'keep': 'fail'},
                mech_gate='fail',
            )
            nxt = MB.suggest_next(root)
            self.assertIsNone(nxt)

    def test_repair_creates_child_round(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            r = MB.open_round(root, tool='cursor', model='composer', task='feature_design')
            MB.score_round(
                root,
                r['id'],
                overall='fail',
                axes={'massing': 'fail', 'keep': 'fail'},
                mech_gate='pass',
                why='ridge too low',
            )
            child = MB.open_repair(root, r['id'], brief='raise ridge one block')
            self.assertEqual(child['attempt'], 2)
            self.assertEqual(child['kind'], 'repair')
            self.assertEqual(child['parent_round'], 'R-001')
            self.assertEqual(child['repair_brief'], 'raise ridge one block')
            self.assertEqual(child['model']['id'], 'composer')

    def test_overall_from_critical_axes(self):
        self.assertEqual(
            MB.overall_from_axes(
                {'massing': 'great', 'keep': 'pass', 'openings': 'fail'},
                ['massing', 'keep'],
            ),
            'pass',
        )
        self.assertEqual(
            MB.overall_from_axes(
                {'massing': 'fail', 'keep': 'great'},
                ['massing', 'keep'],
            ),
            'fail',
        )

    def test_add_model_to_catalogue(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            MB.add_tool(root, tool_id='azure', name='Azure')
            MB.add_model(
                root,
                tool='azure',
                model_id='gpt-mini',
                tier='low',
                label='GPT mini',
                bulk_ok=True,
            )
            cat = MB.load_catalogue(root)
            azure = next(t for t in cat['tools'] if t['id'] == 'azure')
            self.assertEqual(azure['models'][0]['id'], 'gpt-mini')


class TestBakeoffWorldGuard(unittest.TestCase):
    """CLI open/repair must run only against the bake-off world."""

    def test_config_names_bakeoff_world(self):
        cfg = MB.load_config(MB.DEFAULT_BAKEOFFS)
        self.assertEqual(cfg['world'], 'bakeoff')

    def test_cli_open_refuses_wrong_world(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            err = io.StringIO()
            with mock.patch('active_world.gameworld_id', return_value='southampton-build'), \
                    mock.patch('sys.stderr', err):
                rc = MB.main([
                    '--root', str(root),
                    'open', '--tool', 'cursor', '--model', 'composer',
                ])
            self.assertEqual(rc, 2)
            self.assertIn(BAKEOFF_WORLD_PREFIX, err.getvalue())
            self.assertFalse((root / 'rounds' / 'R-001.json').is_file())

    def test_cli_repair_refuses_wrong_world(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            parent = MB.open_round(
                root, tool='cursor', model='composer', task='feature_design',
            )
            MB.score_round(
                root,
                parent['id'],
                overall='fail',
                axes={'massing': 'fail', 'keep': 'fail'},
                mech_gate='pass',
                why='ridge too low',
            )
            err = io.StringIO()
            with mock.patch('active_world.gameworld_id', return_value='southampton-build'), \
                    mock.patch('sys.stderr', err):
                rc = MB.main([
                    '--root', str(root),
                    'repair', parent['id'], '--brief', 'raise ridge one block',
                ])
            self.assertEqual(rc, 2)
            self.assertIn(BAKEOFF_WORLD_PREFIX, err.getvalue())
            self.assertFalse((root / 'rounds' / 'R-002.json').is_file())

    def test_cli_open_succeeds_when_world_matches(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            out = io.StringIO()
            with mock.patch('active_world.gameworld_id', return_value='bakeoff'), \
                    mock.patch('sys.stdout', out):
                rc = MB.main([
                    '--root', str(root),
                    'open', '--tool', 'cursor', '--model', 'composer',
                ])
            self.assertEqual(rc, 0)
            self.assertTrue((root / 'rounds' / 'R-001.json').is_file())
            self.assertIn('R-001', out.getvalue())

    def test_docs_say_prefix_every_command(self):
        skill = (TOOL_ROOT / '.claude' / 'skills' / 'model-bakeoff' / 'SKILL.md').read_text(
            encoding='utf-8',
        )
        protocol = (TOOL_ROOT / 'model-bakeoffs' / 'PROTOCOL.md').read_text(encoding='utf-8')
        for text, name in ((skill, 'SKILL.md'), (protocol, 'PROTOCOL.md')):
            self.assertIn(BAKEOFF_WORLD_PREFIX, text, msg=name)
            self.assertRegex(
                text,
                r'(?i)prefix every',
                msg=f'{name} must say to prefix every bake-off command',
            )
            self.assertNotRegex(
                text,
                r'(?i)export\s+AI_WORLDBUILDER_WORLD_ROOT',
                msg=f'{name} must not teach a one-off export',
            )

    def test_cli_subprocess_status_imports(self):
        """Running the script as ``python3 scripts/model_bakeoff.py`` must find active_world."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            world = root / 'world'
            (world / 'config').mkdir(parents=True)
            (world / 'config' / 'world.json').write_text(
                json.dumps({'gameworld_id': 'bakeoff', 'region_path': str(world)}),
                encoding='utf-8',
            )
            env = os.environ.copy()
            env['AI_WORLDBUILDER_WORLD_ROOT'] = str(world)
            script = TOOL_ROOT / 'scripts' / 'model_bakeoff.py'
            result = subprocess.run(
                [sys.executable, str(script), '--root', str(root), 'status'],
                cwd=str(TOOL_ROOT),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                result.returncode,
                0,
                msg=f'stdout={result.stdout!r} stderr={result.stderr!r}',
            )
            self.assertNotIn('ModuleNotFoundError', result.stderr)


if __name__ == '__main__':
    unittest.main()
