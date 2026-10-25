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


SEED_SITTING_STEPS = [
    'One apply owner for this sitting (parent recovery → interrupt the model subagent first)',
    'Confirm the world is open (MCP up) — ghost may look sparse/empty around the cell',
    (
        'Reset the 3×3 ghost baseline: '
        f'`{BAKEOFF_WORLD_PREFIX} python3 scripts/model_bakeoff.py baseline {{id}}` '
        '(undoes prior feature builds, lidar_ghost apply --force, reconcile)'
    ),
    'Claim site lock; build with that tool/model only',
    'Use tool={tool} model={model} only',
    'Read packs/{pack}/BRIEF.md + packs/{pack}/FAIRNESS.md (and repair_brief if kind=repair)',
    'Build from evidence table — not a previous Minecraft build',
    'Log footprint pad: pad_cells / osm_cells (square only to kill 1-block stairs, not a fixed +2 halo)',
    'Apply, capture fixed cameras, release lock',
    'Interview + score; then usage paste (need-you if counts unknown)',
]


def _fake_entry(
    *,
    seq: int,
    plan: str,
    pass_: str = 'feature',
    kind: str = 'build',
    cell: tuple[int, int] = (48, -44),
    cells: tuple[tuple[int, int], ...] | None = None,
    status: str = 'landed',
    undone: bool = False,
):
    """Minimal journal EntryView for baseline selection tests."""
    from cell_journal import EntryView

    cell_t = tuple(cell)
    touch = tuple(cells) if cells is not None else (cell_t,)
    return EntryView(
        seq=seq,
        kind=kind,
        plan=plan,
        pass_=pass_,
        cell=cell_t,
        cells=touch,
        status=status,
        undone=undone,
        n=1,
        owner='test',
        ts='2026-01-01T00:00:00Z',
        meta={},
    )


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
            'sitting_steps': list(SEED_SITTING_STEPS),
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
            self.assertIn('model_bakeoff.py baseline R-001', checklist)
            self.assertNotIn('restore-ghost', checklist)
            self.assertIn('FAIRNESS.md', checklist)
            self.assertIn('One apply owner', checklist)
            path = root / 'rounds' / 'R-001.json'
            self.assertTrue(path.is_file())
            disk = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(disk['model']['id'], 'composer')

    def test_open_round_checklist_comes_from_config_sitting_steps(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            custom = [
                'Step A only',
                'Use tool={tool} model={model} pack={pack} cell={cell}',
            ]
            cfg = MB.load_config(root)
            cfg['sitting_steps'] = custom
            MB.write_json(root / 'config.json', cfg)
            round_ = MB.open_round(
                root,
                tool='cursor',
                model='composer',
                task='feature_design',
            )
            self.assertEqual(
                round_['sitting']['checklist'],
                [
                    'Step A only',
                    'Use tool=cursor model=composer pack=v1 cell=(48,-44)',
                ],
            )

    def test_generated_checklist_has_no_stale_quit_or_task_48(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            round_ = MB.open_round(
                root,
                tool='cursor',
                model='composer',
                task='feature_design',
            )
            checklist = '\n'.join(round_['sitting']['checklist'])
            self.assertNotIn('Quit Minecraft', checklist)
            self.assertNotIn('task 48', checklist)
            self.assertIn('model_bakeoff.py baseline', checklist)
            card = MB.format_sitting(round_)
            self.assertNotIn('task 48', card)
            self.assertNotIn('Quit Minecraft', card)
            self.assertIn('model_bakeoff.py baseline', card)
            for item in round_['sitting']['checklist']:
                self.assertIn(item, card)

    def test_live_config_sitting_steps_are_source_of_truth(self):
        cfg = MB.load_config(MB.DEFAULT_BAKEOFFS)
        steps = cfg.get('sitting_steps')
        self.assertIsInstance(steps, list)
        self.assertGreaterEqual(len(steps), 1)
        joined = '\n'.join(steps)
        self.assertNotIn('Quit Minecraft', joined)
        self.assertNotIn('task 48', joined)
        self.assertIn('model_bakeoff.py baseline', joined)
        self.assertIn('{id}', joined)
        self.assertEqual(joined.count('lidar_ghost apply'), 1)  # only inside the baseline command note
        self.assertNotIn('cell_registry.py reconcile', joined)
        self.assertIn('{tool}', joined)
        self.assertIn('{model}', joined)
        self.assertIn('{pack}', joined)

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

    def test_docs_link_sitting_steps_instead_of_copying(self):
        """Operator docs must point at config.json sitting_steps, not duplicate them."""
        paths = [
            TOOL_ROOT / '.claude' / 'skills' / 'model-bakeoff' / 'SKILL.md',
            TOOL_ROOT / 'model-bakeoffs' / 'PROTOCOL.md',
            TOOL_ROOT / 'model-bakeoffs' / 'README.md',
            TOOL_ROOT / 'docs' / 'model-bakeoffs.md',
        ]
        for path in paths:
            text = path.read_text(encoding='utf-8')
            name = path.name
            self.assertIn(
                'sitting_steps',
                text,
                msg=f'{name} must link to config sitting_steps',
            )
            self.assertNotIn('Quit Minecraft', text, msg=name)
            self.assertNotIn('pending task 48', text, msg=name)
            self.assertNotIn('task 48', text, msg=name)

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


class TestBaseline(unittest.TestCase):
    """Undo selection, refusal, and --plan for ``baseline`` (fake journal / lock)."""

    def test_selects_non_undone_feature_builds_in_ghost_cells(self):
        ghost = [(47, -44), (48, -44), (49, -44)]
        entries = [
            _fake_entry(seq=1, plan='old-susu', cells=((48, -44),), undone=True),
            _fake_entry(seq=2, plan='trees-pass', pass_='trees', cells=((48, -44),)),
            _fake_entry(seq=3, plan='ghost-g', pass_='ghost-ground', cells=((47, -44),)),
            _fake_entry(seq=4, plan='r012-susu', cells=((48, -44), (49, -44))),
            _fake_entry(seq=5, plan='outside', cell=(10, 10), cells=((10, 10),)),
            _fake_entry(seq=6, plan='begun-build', cells=((47, -44),), status='begun'),
            _fake_entry(seq=7, plan='r013-susu', cells=((47, -44),)),
            _fake_entry(seq=8, plan='undo:r011', kind='undo', cells=((48, -44),)),
        ]
        targets = MB.select_feature_undo_targets(entries, ghost)
        self.assertEqual(
            [(t['plan'], t['seq']) for t in targets],
            [('r013-susu', 7), ('r012-susu', 4)],
        )

    def test_dedupes_same_plan_keeps_latest_seq(self):
        ghost = [(48, -44)]
        entries = [
            _fake_entry(seq=1, plan='susu', cells=((48, -44),), undone=True),
            _fake_entry(seq=9, plan='susu', cells=((48, -44),)),
            _fake_entry(seq=10, plan='susu', cells=((48, -44),)),
        ]
        targets = MB.select_feature_undo_targets(entries, ghost)
        self.assertEqual([(t['plan'], t['seq']) for t in targets], [('susu', 10)])

    def test_baseline_refuses_wrong_world(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            MB.open_round(root, tool='cursor', model='composer', task='feature_design')
            with mock.patch('active_world.gameworld_id', return_value='southampton-build'):
                with self.assertRaises(RuntimeError) as ctx:
                    MB.run_baseline(root, 'R-001', plan_only=True)
            self.assertIn(BAKEOFF_WORLD_PREFIX, str(ctx.exception))

    def test_baseline_refuses_without_site_owner(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            MB.open_round(root, tool='cursor', model='composer', task='feature_design')
            env = {k: v for k, v in os.environ.items() if k != 'SITE_OWNER'}
            with mock.patch('active_world.gameworld_id', return_value='bakeoff'), \
                    mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaises(RuntimeError) as ctx:
                    MB.run_baseline(
                        root,
                        'R-001',
                        plan_only=False,
                        list_entries_fn=lambda _cells: [],
                        undo_fn=lambda *_a, **_k: None,
                        lidar_apply_fn=lambda: None,
                        lidar_status_fn=lambda: {'cells': []},
                        reconcile_fn=lambda _c: True,
                    )
            self.assertIn('SITE_OWNER', str(ctx.exception))

    def test_baseline_refuses_lock_held_by_other(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            MB.open_round(root, tool='cursor', model='composer', task='feature_design')
            other_lock = {
                'owner': 'other',
                'task': 'busy',
                'heartbeat': 9e12,
                'claimed_at': 'now',
            }

            def boom_claim(*_a, **_k):
                raise AssertionError('must not claim over a fresh foreign lock')

            with mock.patch('active_world.gameworld_id', return_value='bakeoff'), \
                    mock.patch.dict(os.environ, {'SITE_OWNER': 'me'}), \
                    mock.patch('model_bakeoff._lock_read', return_value=other_lock), \
                    mock.patch('model_bakeoff._lock_fresh', return_value=True), \
                    mock.patch('model_bakeoff._lock_claim', side_effect=boom_claim):
                with self.assertRaises(RuntimeError) as ctx:
                    MB.run_baseline(
                        root,
                        'R-001',
                        list_entries_fn=lambda _cells: [],
                        undo_fn=lambda *_a, **_k: None,
                        lidar_apply_fn=lambda: None,
                        lidar_status_fn=lambda: {'cells': []},
                        reconcile_fn=lambda _c: True,
                    )
            self.assertIn('other', str(ctx.exception))

    def test_plan_lists_without_undo_or_record(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            MB.open_round(root, tool='cursor', model='composer', task='feature_design')
            entries = [
                _fake_entry(seq=4, plan='r012-susu', cells=((48, -44),)),
                _fake_entry(seq=7, plan='r013-susu', cells=((48, -44),)),
            ]
            undos: list[str] = []
            applies: list[int] = []

            def capture_undo(plan, *, meta=None):
                undos.append(plan)
                return mock.Mock(seq=99)

            with mock.patch('active_world.gameworld_id', return_value='bakeoff'):
                report = MB.run_baseline(
                    root,
                    'R-001',
                    plan_only=True,
                    list_entries_fn=lambda _cells: entries,
                    undo_fn=capture_undo,
                    lidar_apply_fn=lambda: applies.append(1),
                    lidar_status_fn=lambda: {'cells': []},
                    reconcile_fn=lambda _c: True,
                )
            self.assertEqual(report['undone'], ['r013-susu', 'r012-susu'])
            self.assertEqual(report['plan_only'], True)
            self.assertEqual(undos, [])
            self.assertEqual(applies, [])
            disk = MB.load_round(root, 'R-001')
            self.assertNotIn('baseline', disk)

    def test_baseline_undoes_applies_and_records(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            cfg = MB.load_config(root)
            cfg['ghost_cells'] = [[48, -44], [49, -44]]
            MB.write_json(root / 'config.json', cfg)
            MB.open_round(root, tool='cursor', model='composer', task='feature_design')
            entries = [
                _fake_entry(seq=4, plan='r012-susu', cells=((48, -44),)),
            ]
            undos: list[tuple] = []
            claims: list[tuple] = []

            def undo(plan, *, meta=None):
                undos.append((plan, meta))
                return mock.Mock(seq=42)

            def status():
                return {
                    'cells': [
                        {'cell': [48, -44], 'ghosts_complete': True},
                        {'cell': [49, -44], 'ghosts_complete': True},
                    ],
                }

            with mock.patch('active_world.gameworld_id', return_value='bakeoff'), \
                    mock.patch.dict(os.environ, {'SITE_OWNER': 'agent'}), \
                    mock.patch('model_bakeoff._lock_read', return_value=None), \
                    mock.patch('model_bakeoff._lock_fresh', return_value=False), \
                    mock.patch(
                        'model_bakeoff._lock_claim',
                        side_effect=lambda owner, task: claims.append((owner, task)),
                    ), \
                    mock.patch('model_bakeoff._lock_check', return_value=None):
                report = MB.run_baseline(
                    root,
                    'R-001',
                    list_entries_fn=lambda cells: entries,
                    undo_fn=undo,
                    lidar_apply_fn=lambda: 'applied',
                    lidar_status_fn=status,
                    reconcile_fn=lambda cell: True,
                )
            self.assertEqual(undos, [('r012-susu', {'round': 'R-001'})])
            self.assertEqual(report['undone'], ['r012-susu'])
            self.assertEqual(report['journal_seqs'], [42])
            self.assertTrue(report['clean'])
            self.assertEqual(claims, [('agent', 'model_bakeoff baseline R-001')])
            disk = MB.load_round(root, 'R-001')
            self.assertEqual(disk['baseline']['undone'], ['r012-susu'])
            self.assertEqual(disk['baseline']['journal_seqs'], [42])
            self.assertTrue(disk['baseline']['clean'])

    def test_cli_baseline_plan_refuses_wrong_world(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            MB.open_round(root, tool='cursor', model='composer', task='feature_design')
            err = io.StringIO()
            with mock.patch('active_world.gameworld_id', return_value='southampton-build'), \
                    mock.patch('sys.stderr', err):
                rc = MB.main(['--root', str(root), 'baseline', 'R-001', '--plan'])
            self.assertEqual(rc, 2)
            self.assertIn(BAKEOFF_WORLD_PREFIX, err.getvalue())


if __name__ == '__main__':
    unittest.main()
