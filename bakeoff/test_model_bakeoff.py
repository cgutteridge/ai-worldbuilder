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
BAKEOFF_WORLD_FLAG = '--world bakeoff'


SEED_CAPTURE = {
    'cameras': {
        'square_south': {'eye': [6205, 57, -5593], 'look': [6195, 56, -5565]},
        'street_front': {'eye': [6204, 56, -5576], 'look': [6200, 55, -5563]},
        'aerial_top': {'eye': [6185, 160, -5537], 'look': [6185, 40, -5536.99]},
    },
    'aerial_bbox_mc': [6140, -5584, 6230, -5490],
    'FOV': 70,
    'reference_plane_y': 60,
    'aerial_crop_pixels': [451, 156, 1061, 793],
    'aerial_crop_base_width': 1512,
    'aerial_rotation_degrees': 180,
    'real_aerial': {
        'mosaic': 'reference/aerial/susu/mosaic-z18-3x3-centre-130054-87835.png',
        'xyz_origin': [130053, 87834],
        'zoom': 18,
        'resize': [610, 637],
    },
    'portals_note': 'reference/portals/42-A..D.jpg',
}

SEED_SITTING_STEPS = [
    'One apply owner for this sitting (parent recovery → interrupt the model subagent first)',
    'Confirm the world is open (MCP up) — ghost may look sparse/empty around the cell',
    (
        'Reset the 3×3 ghost baseline: '
        f'`python3 bakeoff/model_bakeoff.py --world bakeoff baseline {{id}}` '
        '(undoes prior feature builds, lidar_ghost apply --force, reconcile)'
    ),
    'Claim site lock; build with that tool/model only',
    'Use tool={tool} model={model} only',
    'Read packs/{pack}/BRIEF.md + packs/{pack}/FAIRNESS.md (and repair_brief if kind=repair)',
    'Build from evidence table — not a previous Minecraft build',
    'Log footprint pad: pad_cells / osm_cells (square only to kill 1-block stairs, not a fixed +2 halo)',
    (
        'Apply, then capture fixed cameras: '
        f'`python3 bakeoff/model_bakeoff.py --world bakeoff capture {{id}}`; '
        'release lock'
    ),
    'Interview + score; then usage paste (need-you if counts unknown)',
]

SEED_SITTING_STEPS_HYBRID = [
    'One apply owner: builder ({model}); designer ({designer}) writes hand-off only',
    (
        'Reset baseline: '
        f'`python3 bakeoff/model_bakeoff.py --world bakeoff baseline {{id}}`'
    ),
    'Designer ({designer}): write evidence + hand-off; do not apply',
    'Builder ({model}): apply from hand-off only',
    (
        'Capture: '
        f'`python3 bakeoff/model_bakeoff.py --world bakeoff capture {{id}}`'
    ),
    'Interview + score; usage transcript splits designer/builder',
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
            'active_tiers': ['low', 'mid', 'high'],
            'tasks': ['feature_design'],
            'pilot_task': 'feature_design',
            'axes': ['massing', 'keep'],
            'critical_axes': ['massing', 'keep'],
            'max_attempts': 3,
            'grades': ['fail', 'pass', 'great'],
            'sitting_steps': list(SEED_SITTING_STEPS),
            'sitting_steps_hybrid': list(SEED_SITTING_STEPS_HYBRID),
            'capture': dict(SEED_CAPTURE),
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


def _add_hybrid_designer(root: Path) -> None:
    """Register a bulk-ok high designer model for hybrid tests (idempotent)."""
    try:
        MB.add_model(
            root,
            tool='cursor',
            model_id='opus-low',
            tier='high',
            label='Opus low',
            bulk_ok=True,
        )
    except ValueError as exc:
        if 'already on' not in str(exc):
            raise


def _add_claude_effort_models(root: Path) -> None:
    """Register Claude Code catalogue rows with effort suffixes (idempotent)."""
    for model_id, tier, label in (
        ('claude-opus-5-5-low', 'high', 'Claude Opus 5.5 (low effort)'),
        ('claude-opus-5-5-medium', 'high', 'Claude Opus 5.5 (medium effort)'),
        ('claude-sonnet-5-5-medium', 'mid', 'Claude Sonnet 5.5 (medium effort)'),
    ):
        try:
            MB.add_model(
                root,
                tool='claude',
                model_id=model_id,
                tier=tier,
                label=label,
                bulk_ok=True,
            )
        except ValueError as exc:
            if 'already on' not in str(exc):
                raise


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
            self.assertIn('model_bakeoff.py --world bakeoff baseline R-001', checklist)
            self.assertIn('model_bakeoff.py --world bakeoff capture R-001', checklist)
            # baseline replaced restore-ghost; config.json is not in stale_terms scan
            self.assertNotIn('restore-ghost', checklist)
            self.assertIn('FAIRNESS.md', checklist)
            self.assertIn('One apply owner', checklist)
            path = root / 'rounds' / 'R-001.json'
            self.assertTrue(path.is_file())
            disk = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(disk['model']['id'], 'composer')
            card = MB.format_sitting(round_)
            for item in round_['sitting']['checklist']:
                self.assertIn(item, card)

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

    def test_live_config_sitting_steps_are_source_of_truth(self):
        cfg = MB.load_config(MB.DEFAULT_BAKEOFFS)
        steps = cfg.get('sitting_steps')
        self.assertIsInstance(steps, list)
        self.assertGreaterEqual(len(steps), 1)
        joined = '\n'.join(steps)
        self.assertIn('model_bakeoff.py --world bakeoff baseline', joined)
        self.assertIn('model_bakeoff.py --world bakeoff capture', joined)
        self.assertIn('{id}', joined)
        self.assertEqual(joined.count('lidar_ghost apply'), 1)  # only inside the baseline command note
        self.assertNotIn('cell_registry.py reconcile', joined)
        self.assertNotIn('restore-ghost', joined)
        self.assertIn('{tool}', joined)
        self.assertIn('{model}', joined)
        self.assertIn('{pack}', joined)
        cap = cfg.get('capture')
        self.assertIsInstance(cap, dict)
        self.assertIn('square_south', cap.get('cameras') or {})
        self.assertEqual(cap.get('aerial_bbox_mc'), [6140, -5584, 6230, -5490])

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

    def test_sum_transcript_dedupes_message_ids(self):
        fixture = (
            Path(__file__).resolve().parent
            / 'testdata'
            / 'bakeoff_transcript_dup_ids.jsonl'
        )
        summed = MB.sum_transcript_usage(fixture, model_prefix='claude-opus-5-5')
        # msg_dup appears twice; msg_b once; sonnet filtered out
        self.assertEqual(summed['requests'], 2)
        self.assertEqual(summed['input_tokens'], 13)
        self.assertEqual(summed['output_tokens'], 27)
        self.assertEqual(summed['cache_read_tokens'], 150)
        self.assertEqual(summed['cache_write_tokens'], 7)
        self.assertEqual(summed['total_tokens'], 13 + 27 + 150 + 7)
        self.assertEqual(summed['first_timestamp'], '2026-10-24T17:00:10.000Z')
        self.assertEqual(summed['last_timestamp'], '2026-10-24T17:01:00.000Z')

    def test_sum_transcript_includes_subagents(self):
        fixture = (
            Path(__file__).resolve().parent
            / 'testdata'
            / 'bakeoff_transcript_session.jsonl'
        )
        summed = MB.sum_transcript_usage(fixture, model_prefix='claude-opus-5-5')
        self.assertEqual(summed['requests'], 2)
        self.assertEqual(summed['input_tokens'], 5)
        self.assertEqual(summed['output_tokens'], 8)
        self.assertEqual(summed['cache_read_tokens'], 30)
        self.assertEqual(summed['cache_write_tokens'], 4)
        self.assertEqual(summed['total_tokens'], 47)

    def test_record_usage_from_transcript_and_plan_pct_status(self):
        fixture = (
            Path(__file__).resolve().parent
            / 'testdata'
            / 'bakeoff_transcript_dup_ids.jsonl'
        )
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            round_ = MB.open_round(
                root,
                tool='cursor',
                model='composer',
                task='feature_design',
                plan_pct=45,
            )
            self.assertEqual(round_['usage']['plan_pct_start'], 45)
            MB.score_round(
                root,
                round_['id'],
                overall='great',
                axes={'massing': 'great', 'keep': 'great'},
                plan_pct=47,
                wallclock_min=35,
                interruptions='host reboot ~6 min',
            )
            updated = MB.record_usage(
                root,
                round_['id'],
                from_transcript=fixture,
                model_prefix='claude-opus-5-5',
            )
            u = updated['usage']
            self.assertEqual(u['source'], 'api')
            self.assertEqual(u['requests'], 2)
            self.assertEqual(u['total_tokens'], 197)
            self.assertEqual(u['plan_pct_start'], 45)
            self.assertEqual(u['plan_pct_end'], 47)
            self.assertEqual(u['wallclock_min'], 35)
            self.assertEqual(u['interruptions'], 'host reboot ~6 min')
            status = MB.format_status(root)
            self.assertIn('plan=45%→47% (Δ+2)', status)

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

    def test_score_copies_build_script_artefacts(self):
        """Score copies build script + cameras.json into rounds/R-NNN/."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'bakeoffs'
            world = Path(d) / 'world'
            root.mkdir()
            _seed(root)

            builds = world / 'site' / 'builds'
            builds.mkdir(parents=True)
            script = builds / 'r001_susu.py'
            script.write_text(
                '"""R-001 evidence table + build script.\n"""\nPLAN = "r001_susu"\n',
                encoding='utf-8',
            )
            # Sidecar binary / screenshots must not be copied.
            (builds / 'r001-lidar.npz').write_bytes(b'not-a-real-npz')
            (builds / 'skip_me.png').write_bytes(b'fake-png')

            shots = world / 'verification' / 'site' / 'r001-shots'
            shots.mkdir(parents=True)
            cameras = {
                'cameras': {
                    'square_south': {'eye': [1, 2, 3], 'look': [4, 5, 6]},
                }
            }
            (shots / 'cameras.json').write_text(
                json.dumps(cameras, indent=2) + '\n', encoding='utf-8'
            )
            (shots / 'square_south.png').write_bytes(b'fake-shot')

            round_ = MB.open_round(
                root,
                tool='cursor',
                model='composer',
                task='feature_design',
                build_scripts=[script],
            )
            self.assertEqual(round_['build_scripts'], [str(script)])

            scored = MB.score_round(
                root,
                round_['id'],
                overall='pass',
                axes={'massing': 'pass', 'keep': 'pass'},
                world_root=world,
            )

            art_dir = root / 'rounds' / 'R-001'
            self.assertTrue((art_dir / 'r001_susu.py').is_file())
            self.assertTrue((art_dir / 'cameras.json').is_file())
            self.assertFalse((art_dir / 'r001-lidar.npz').exists())
            self.assertFalse((art_dir / 'skip_me.png').exists())
            self.assertFalse((art_dir / 'square_south.png').exists())
            self.assertIn(
                'evidence table',
                (art_dir / 'r001_susu.py').read_text(encoding='utf-8'),
            )

            arts = scored['artefacts']
            self.assertEqual(
                arts['build_scripts'],
                ['bakeoff/rounds/R-001/r001_susu.py'],
            )
            self.assertEqual(
                arts['cameras'],
                'bakeoff/rounds/R-001/cameras.json',
            )
            disk = MB.load_round(root, 'R-001')
            self.assertEqual(disk['artefacts'], arts)

    def test_score_resolves_build_script_from_journal_plan(self):
        """Without --build-script, score uses journal plan → site/builds/<plan>.py."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'bakeoffs'
            world = Path(d) / 'world'
            root.mkdir()
            _seed(root)

            builds = world / 'site' / 'builds'
            builds.mkdir(parents=True)
            (builds / 'r001_susu.py').write_text('# from journal\n', encoding='utf-8')
            (builds / 'r001_susu.json').write_text(
                '{"name": "r001_susu"}\n', encoding='utf-8'
            )
            (builds / 'r001-lidar.npz').write_bytes(b'npz')

            round_ = MB.open_round(
                root, tool='cursor', model='composer', task='feature_design'
            )
            from cell_journal import EntryView

            tagged = EntryView(
                seq=10,
                kind='build',
                plan='r001_susu',
                pass_='feature',
                cell=(48, -44),
                cells=((47, -44), (48, -44), (48, -43)),
                status='landed',
                undone=False,
                n=1,
                owner='test',
                ts='2026-01-01T00:00:00Z',
                meta={'round': 'R-001'},
            )

            scored = MB.score_round(
                root,
                round_['id'],
                overall='pass',
                axes={'massing': 'pass', 'keep': 'pass'},
                world_root=world,
                list_entries_fn=lambda _cells: [tagged],
            )
            art_dir = root / 'rounds' / 'R-001'
            self.assertTrue((art_dir / 'r001_susu.py').is_file())
            self.assertTrue((art_dir / 'r001_susu.json').is_file())
            self.assertFalse((art_dir / 'r001-lidar.npz').exists())
            self.assertEqual(
                scored['artefacts']['build_scripts'],
                [
                    'bakeoff/rounds/R-001/r001_susu.py',
                    'bakeoff/rounds/R-001/r001_susu.json',
                ],
            )


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
            with mock.patch('script_env.activate_world_from_args'), \
                    mock.patch('active_world.gameworld_id', return_value='southampton-build'), \
                    mock.patch('sys.stderr', err):
                rc = MB.main([
                    '--world', 'bakeoff',
                    '--root', str(root),
                    'open', '--tool', 'cursor', '--model', 'composer',
                ])
            self.assertEqual(rc, 2)
            self.assertIn(BAKEOFF_WORLD_FLAG, err.getvalue())
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
            with mock.patch('script_env.activate_world_from_args'), \
                    mock.patch('active_world.gameworld_id', return_value='southampton-build'), \
                    mock.patch('sys.stderr', err):
                rc = MB.main([
                    '--world', 'bakeoff',
                    '--root', str(root),
                    'repair', parent['id'], '--brief', 'raise ridge one block',
                ])
            self.assertEqual(rc, 2)
            self.assertIn(BAKEOFF_WORLD_FLAG, err.getvalue())
            self.assertFalse((root / 'rounds' / 'R-002.json').is_file())

    def test_cli_open_succeeds_when_world_matches(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            out = io.StringIO()
            with mock.patch('script_env.activate_world_from_args'), \
                    mock.patch('active_world.gameworld_id', return_value='bakeoff'), \
                    mock.patch('sys.stdout', out):
                rc = MB.main([
                    '--world', 'bakeoff',
                    '--root', str(root),
                    'open', '--tool', 'cursor', '--model', 'composer',
                ])
            self.assertEqual(rc, 0)
            round_path = root / 'rounds' / 'R-001.json'
            self.assertTrue(round_path.is_file())
            self.assertIn('R-001', out.getvalue())
            # Other tool than config/build-roles.json → designer defaults to builder.
            round_ = json.loads(round_path.read_text(encoding='utf-8'))
            self.assertEqual(round_['round_type'], 'single')
            self.assertEqual(round_['designer']['id'], 'composer')
            self.assertEqual(round_['model']['id'], 'composer')

    def test_docs_say_world_flag_every_command(self):
        skill = (
            TOOL_ROOT / 'bakeoff' / 'skills' / 'model-bakeoff' / 'SKILL.md'
        ).read_text(encoding='utf-8')
        protocol = (TOOL_ROOT / 'bakeoff' / 'PROTOCOL.md').read_text(encoding='utf-8')
        for text, name in ((skill, 'SKILL.md'), (protocol, 'PROTOCOL.md')):
            self.assertIn(BAKEOFF_WORLD_FLAG, text, msg=name)
            self.assertNotRegex(
                text,
                r'(?i)AI_WORLDBUILDER_WORLD_ROOT',
                msg=f'{name} must not mention the retired env var',
            )

    def test_docs_link_sitting_steps_instead_of_copying(self):
        """Operator docs must point at config.json sitting_steps, not duplicate them."""
        paths = [
            TOOL_ROOT / '.claude' / 'skills' / 'model-bakeoff' / 'SKILL.md',
            TOOL_ROOT / 'bakeoff' / 'PROTOCOL.md',
            TOOL_ROOT / 'bakeoff' / 'README.md',
            TOOL_ROOT / 'bakeoff' / 'OVERVIEW.md',
        ]
        for path in paths:
            text = path.read_text(encoding='utf-8')
            name = path.name
            self.assertIn(
                'sitting_steps',
                text,
                msg=f'{name} must link to config sitting_steps',
            )

    def test_cli_subprocess_status_imports(self):
        """Running the script as ``python3 bakeoff/model_bakeoff.py`` must find active_world."""
        import shutil

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            worlds = TOOL_ROOT / 'worlds'
            worlds.mkdir(exist_ok=True)
            world = worlds / '_cli_status_world'
            if world.exists():
                shutil.rmtree(world)
            (world / 'config').mkdir(parents=True)
            (world / 'config' / 'world.json').write_text(
                json.dumps({'gameworld_id': 'bakeoff', 'save_path': str(world)}),
                encoding='utf-8',
            )
            (world / 'config' / 'world-frame.json').write_text(
                json.dumps({'offsets': {'easting': 0, 'northing': 0}}),
                encoding='utf-8',
            )
            script = TOOL_ROOT / 'bakeoff' / 'model_bakeoff.py'
            try:
                result = subprocess.run(
                    [
                        sys.executable, str(script),
                        '--world', '_cli_status_world',
                        '--root', str(root), 'status',
                    ],
                    cwd=str(TOOL_ROOT),
                    env=os.environ.copy(),
                    capture_output=True,
                    text=True,
                    check=False,
                )
            finally:
                shutil.rmtree(world, ignore_errors=True)
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
            with mock.patch('script_env.activate_world_from_args'), \
                    mock.patch('active_world.gameworld_id', return_value='southampton-build'):
                with self.assertRaises(RuntimeError) as ctx:
                    MB.run_baseline(root, 'R-001', plan_only=True)
            self.assertIn(BAKEOFF_WORLD_FLAG, str(ctx.exception))

    def test_baseline_refuses_without_site_owner(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            MB.open_round(root, tool='cursor', model='composer', task='feature_design')
            env = {k: v for k, v in os.environ.items() if k != 'SITE_OWNER'}
            with mock.patch('script_env.activate_world_from_args'), \
                    mock.patch('active_world.gameworld_id', return_value='bakeoff'), \
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

            with mock.patch('script_env.activate_world_from_args'), \
                    mock.patch('active_world.gameworld_id', return_value='bakeoff'), \
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

            with mock.patch('script_env.activate_world_from_args'), \
                    mock.patch('active_world.gameworld_id', return_value='bakeoff'):
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

            with mock.patch('script_env.activate_world_from_args'), \
                    mock.patch('active_world.gameworld_id', return_value='bakeoff'), \
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
            with mock.patch('script_env.activate_world_from_args'), \
                    mock.patch('active_world.gameworld_id', return_value='southampton-build'), \
                    mock.patch('sys.stderr', err):
                rc = MB.main(['--world', 'bakeoff', '--root', str(root), 'baseline', 'R-001', '--plan'])
            self.assertEqual(rc, 2)
            self.assertIn(BAKEOFF_WORLD_FLAG, err.getvalue())


    def test_baseline_archives_prior_round_builds(self):
        """baseline moves r0NN_* out of site/builds into archive/rounds/<id>/."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            world = Path(d) / 'world'
            _seed(root)
            MB.open_round(root, tool='cursor', model='composer', task='feature_design')
            builds = world / 'site' / 'builds'
            builds.mkdir(parents=True)
            (builds / 'r012_susu.py').write_text('# prior\n', encoding='utf-8')
            (builds / 'r011-lidar.npz').write_bytes(b'npz')
            (builds / 'r013_capture.py').write_text('# cap\n', encoding='utf-8')
            (builds / 'notes.txt').write_text('keep me\n', encoding='utf-8')
            (builds / 'R-009_legacy.py').write_text('# not r0NN_\n', encoding='utf-8')

            with mock.patch('script_env.activate_world_from_args'), \
                    mock.patch('active_world.gameworld_id', return_value='bakeoff'), \
                    mock.patch.dict(os.environ, {'SITE_OWNER': 'agent'}), \
                    mock.patch('model_bakeoff._lock_read', return_value=None), \
                    mock.patch('model_bakeoff._lock_fresh', return_value=False), \
                    mock.patch('model_bakeoff._lock_claim'), \
                    mock.patch('model_bakeoff._lock_check', return_value=None):
                report = MB.run_baseline(
                    root,
                    'R-001',
                    world_root=world,
                    list_entries_fn=lambda _cells: [],
                    undo_fn=lambda *_a, **_k: mock.Mock(seq=1),
                    lidar_apply_fn=lambda: None,
                    lidar_status_fn=lambda: {
                        'cells': [
                            {'cell': c, 'ghosts_complete': True}
                            for c in MB.load_config(root)['ghost_cells']
                        ],
                    },
                    reconcile_fn=lambda _c: True,
                )

            self.assertEqual(
                sorted(report['archived']),
                ['r011-lidar.npz', 'r012_susu.py', 'r013_capture.py'],
            )
            self.assertEqual(report['archive_dir'], 'worlds/bakeoff/archive/rounds/R-001')
            dest = world / 'archive' / 'rounds' / 'R-001'
            self.assertTrue((dest / 'r012_susu.py').is_file())
            self.assertTrue((dest / 'r011-lidar.npz').is_file())
            self.assertTrue((dest / 'r013_capture.py').is_file())
            left = sorted(p.name for p in builds.iterdir())
            self.assertEqual(left, ['R-009_legacy.py', 'notes.txt'])
            disk = MB.load_round(root, 'R-001')
            self.assertEqual(
                sorted(disk['baseline']['archived']),
                ['r011-lidar.npz', 'r012_susu.py', 'r013_capture.py'],
            )

    def test_open_warns_on_prior_round_build_leftovers(self):
        """CLI open prints a warning when site/builds still has r0NN_* files."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            world = Path(d) / 'world'
            _seed(root)
            builds = world / 'site' / 'builds'
            builds.mkdir(parents=True)
            (builds / 'r012_susu.py').write_text('# leftover\n', encoding='utf-8')
            err = io.StringIO()
            out = io.StringIO()
            with mock.patch('script_env.activate_world_from_args'), \
                    mock.patch('active_world.gameworld_id', return_value='bakeoff'), \
                    mock.patch('active_world.WORLD_ROOT', world), \
                    mock.patch('sys.stderr', err), \
                    mock.patch('sys.stdout', out):
                rc = MB.main([
                    '--world', 'bakeoff',
                    '--root', str(root),
                    'open', '--tool', 'cursor', '--model', 'composer',
                ])
            self.assertEqual(rc, 0)
            self.assertIn('earlier-round artefacts', err.getvalue())
            self.assertIn('r012_susu.py', err.getvalue())
            self.assertIn('archive/rounds', err.getvalue())


class TestCapture(unittest.TestCase):
    """Photos-map / aerial-crop maths and stubbed ``capture`` runs."""

    def test_scale_aerial_crop_doubles_at_2x_width(self):
        crop = MB.scale_aerial_crop([451, 156, 1061, 793], 3024, base_width=1512)
        self.assertEqual(crop, (902, 312, 2122, 1586))

    def test_scale_aerial_crop_identity_at_base_width(self):
        box = (451, 156, 1061, 793)
        self.assertEqual(MB.scale_aerial_crop(box, 1512), box)

    def test_photos_map_paths(self):
        photos = MB.photos_map_for_shots('worlds/bakeoff/verification/site/r013-shots')
        self.assertEqual(
            photos['square_south'],
            'worlds/bakeoff/verification/site/r013-shots/square_south.png',
        )
        self.assertEqual(
            photos['street_front'],
            'worlds/bakeoff/verification/site/r013-shots/street_front.png',
        )
        self.assertEqual(
            photos['aerial_top'],
            'worlds/bakeoff/verification/site/r013-shots/aerial_top.png',
        )
        self.assertIn('real_aerial.png', photos['real_refs_note'])
        self.assertIn('42-A..D.jpg', photos['real_refs_note'])

    def test_shots_rel_dir_slug(self):
        self.assertEqual(MB.round_shots_slug('R-013'), 'r013')
        self.assertEqual(
            MB.shots_rel_dir('bakeoff', 'R-013'),
            'worlds/bakeoff/verification/site/r013-shots',
        )

    def test_capture_refuses_wrong_world(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            MB.open_round(root, tool='cursor', model='composer', task='feature_design')
            with mock.patch('script_env.activate_world_from_args'), \
                    mock.patch('active_world.gameworld_id', return_value='southampton-build'):
                with self.assertRaises(RuntimeError) as ctx:
                    MB.run_capture(root, 'R-001', capture_fn=lambda *_a, **_k: Path('.'))
            self.assertIn(BAKEOFF_WORLD_FLAG, str(ctx.exception))

    def test_run_capture_stubbed_fills_photos_and_crops(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'bakeoffs'
            world = Path(d) / 'world'
            root.mkdir()
            _seed(root)
            MB.open_round(root, tool='cursor', model='composer', task='feature_design')

            mosaic_path = world / SEED_CAPTURE['real_aerial']['mosaic']
            mosaic_path.parent.mkdir(parents=True)
            # 3×3 tile mosaic at z18 → 768×768; origin matches xyz_origin.
            Image.new('RGB', (768, 768), (40, 120, 40)).save(mosaic_path)

            captured: list[str] = []
            saves: list[int] = []

            def stub_capture(eye, look, dest, *, downscale=1):
                del eye, look, downscale
                name = dest.stem
                captured.append(name)
                # 2× reference width so crop maths are non-trivial.
                size = (3024, 1898) if name == 'aerial_top' else (800, 500)
                Image.new('RGB', size, (10, 20, 30)).save(dest)
                return dest

            def stub_wgs84(x, z):
                # Real bakeoff-frame corners for aerial_bbox_mc (pixels stay in 768²).
                table = {
                    (6140.0, -5584.0): (50.9346157479733, -1.39814603731417),
                    (6140.0, -5490.0): (50.9337704992432, -1.39815695900008),
                    (6230.0, -5584.0): (50.9346091242402, -1.39686531341435),
                    (6230.0, -5490.0): (50.933763875326, -1.39687625828209),
                }
                return table[(float(x), float(z))]

            with mock.patch('script_env.activate_world_from_args'), \
                    mock.patch('active_world.gameworld_id', return_value='bakeoff'):
                report = MB.run_capture(
                    root,
                    'R-001',
                    world_root=world,
                    capture_fn=stub_capture,
                    save_world_fn=lambda: saves.append(1),
                    mc_to_wgs84_fn=stub_wgs84,
                )

            self.assertEqual(sorted(captured), ['aerial_top', 'square_south', 'street_front'])
            self.assertEqual(saves, [1])
            self.assertEqual(report['aerial_crop'], [902, 312, 2122, 1586])
            shots = world / 'verification' / 'site' / 'r001-shots'
            self.assertTrue((shots / 'square_south.png').is_file())
            self.assertTrue((shots / 'street_front.png').is_file())
            self.assertTrue((shots / 'aerial_top.png').is_file())
            self.assertTrue((shots / 'aerial_top-full.png').is_file())
            self.assertTrue((shots / 'real_aerial.png').is_file())
            with Image.open(shots / 'real_aerial.png') as real_im:
                self.assertEqual(real_im.size, (610, 637))
            meta = json.loads((shots / 'cameras.json').read_text(encoding='utf-8'))
            self.assertEqual(meta['actual_aerial_crop_pixels'], [902, 312, 2122, 1586])
            self.assertEqual(meta['aerial_rotation_degrees'], 180)

            disk = MB.load_round(root, 'R-001')
            self.assertEqual(
                disk['photos']['square_south'],
                'worlds/bakeoff/verification/site/r001-shots/square_south.png',
            )
            self.assertEqual(
                disk['photos']['aerial_top'],
                'worlds/bakeoff/verification/site/r001-shots/aerial_top.png',
            )
            self.assertIn(
                'worlds/bakeoff/verification/site/r001-shots/real_aerial.png',
                disk['photos']['real_refs_note'],
            )


    def test_cli_capture_refuses_wrong_world(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            MB.open_round(root, tool='cursor', model='composer', task='feature_design')
            err = io.StringIO()
            with mock.patch('script_env.activate_world_from_args'), \
                    mock.patch('active_world.gameworld_id', return_value='southampton-build'), \
                    mock.patch('sys.stderr', err):
                rc = MB.main(['--world', 'bakeoff', '--root', str(root), 'capture', 'R-001'])
            self.assertEqual(rc, 2)
            self.assertIn(BAKEOFF_WORLD_FLAG, err.getvalue())



class HybridRoundTests(unittest.TestCase):
    """Designer + builder hybrid sittings."""

    def _unlock_mid(self, root: Path) -> None:
        _add_hybrid_designer(root)
        low = MB.open_round(root, tool='cursor', model='composer', task='feature_design')
        MB.score_round(
            root,
            low['id'],
            overall='pass',
            axes={'massing': 'pass', 'keep': 'pass'},
        )

    def test_open_hybrid_round_records_designer_and_checklist(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            self._unlock_mid(root)
            handoff = root / 'design_handoff.py'
            handoff.write_text('PLAN = "r_hybrid"\n', encoding='utf-8')
            round_ = MB.open_round(
                root,
                tool='cursor',
                model='sonnet',
                designer='opus-low',
                task='feature_design',
                handoff=handoff,
                plan_pct=50,
            )
            self.assertEqual(round_['round_type'], 'hybrid')
            self.assertEqual(round_['model']['id'], 'sonnet')
            self.assertEqual(round_['designer']['id'], 'opus-low')
            self.assertEqual(round_['handoff']['path'], str(handoff))
            self.assertEqual(round_['handoff']['kind'], 'script')
            self.assertIn('by_role', round_['usage'])
            checklist = '\n'.join(round_['sitting']['checklist'])
            self.assertIn('opus-low', checklist)
            self.assertIn('sonnet', checklist)
            self.assertIn('hand-off', checklist)
            self.assertNotIn('Use tool=cursor model=sonnet only', checklist)
            card = MB.format_sitting(round_)
            self.assertIn('builder (apply owner)', card)
            self.assertIn('designer:', card)
            self.assertIn(str(handoff), card)

    def test_same_designer_and_builder_is_single_with_both_roles(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            round_ = MB.open_round(
                root,
                tool='cursor',
                model='composer',
                designer='composer',
                task='feature_design',
            )
            self.assertEqual(round_['round_type'], 'single')
            self.assertEqual(round_['designer']['id'], 'composer')
            self.assertEqual(round_['model']['id'], 'composer')
            self.assertFalse(MB.is_hybrid_round(round_))
            self.assertNotIn('handoff', round_)
            self.assertNotIn('by_role', round_['usage'])
            checklist = '\n'.join(round_['sitting']['checklist'])
            self.assertIn('Use tool=cursor model=composer only', checklist)

    def test_hybrid_excluded_from_ladder_and_mid_unlock(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            _add_hybrid_designer(root)
            with self.assertRaises(ValueError):
                MB.open_round(
                    root,
                    tool='cursor',
                    model='sonnet',
                    designer='opus-low',
                    task='feature_design',
                )
            self._unlock_mid(root)
            hybrid = MB.open_round(
                root,
                tool='cursor',
                model='sonnet',
                designer='opus-low',
                task='feature_design',
            )
            MB.score_round(
                root,
                hybrid['id'],
                overall='great',
                axes={'massing': 'great', 'keep': 'great'},
            )
            scored = MB.scored_cold_rounds(
                MB.list_rounds(root), 'feature_design', 'v1'
            )
            self.assertNotIn(
                MB.cold_key('feature_design', 'cursor', 'sonnet', 'v1'),
                scored,
            )
            # Hybrid must not block a later single-model sonnet cold open.
            single = MB.open_round(
                root, tool='cursor', model='sonnet', task='feature_design'
            )
            self.assertEqual(single['round_type'], 'single')
            self.assertNotIn('designer', single)

    def test_transcript_model_prefix_strips_effort_suffix(self):
        self.assertEqual(
            MB.transcript_model_prefix('claude-opus-5-5-low'),
            'claude-opus-5-5',
        )
        self.assertEqual(
            MB.transcript_model_prefix('claude-sonnet-5-5-medium'),
            'claude-sonnet-5-5',
        )
        self.assertEqual(
            MB.transcript_model_prefix('claude-haiku-5-5-high'),
            'claude-haiku-5-5',
        )
        self.assertEqual(
            MB.transcript_model_prefix('claude-haiku-5-5'),
            'claude-haiku-5-5',
        )

    def test_hybrid_rejects_colliding_transcript_prefixes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            _add_claude_effort_models(root)
            with self.assertRaises(ValueError) as ctx:
                MB.open_round(
                    root,
                    tool='claude',
                    model='claude-opus-5-5-medium',
                    designer='claude-opus-5-5-low',
                    task='feature_design',
                )
            msg = str(ctx.exception).lower()
            self.assertIn('transcript', msg)
            self.assertIn('prefix', msg)

    def test_hybrid_usage_split_and_score_handoff_copy(self):
        fixture = (
            Path(__file__).resolve().parent
            / 'testdata'
            / 'bakeoff_transcript_hybrid.jsonl'
        )
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            _add_claude_effort_models(root)
            self._unlock_mid(root)
            handoff = root / 'r_hybrid_susu.py'
            handoff.write_text('PLAN = "r_hybrid_susu"\n', encoding='utf-8')
            round_ = MB.open_round(
                root,
                tool='claude',
                model='claude-sonnet-5-5-medium',
                designer='claude-opus-5-5-low',
                task='feature_design',
                plan_pct=50,
            )
            scored = MB.score_round(
                root,
                round_['id'],
                overall='pass',
                axes={'massing': 'pass', 'keep': 'pass'},
                plan_pct=52,
                handoff=handoff,
            )
            self.assertEqual(scored['handoff']['path'], str(handoff))
            self.assertEqual(
                scored['artefacts']['build_scripts'],
                [f"bakeoff/rounds/{scored['id']}/r_hybrid_susu.py"],
            )
            updated = MB.record_usage(
                root,
                round_['id'],
                from_transcript=fixture,
            )
            u = updated['usage']
            self.assertEqual(u['source'], 'api')
            self.assertEqual(u['by_role']['designer']['total_tokens'], 89)
            self.assertEqual(u['by_role']['designer']['requests'], 2)
            self.assertEqual(
                u['by_role']['designer']['model_prefix'],
                'claude-opus-5-5',
            )
            self.assertEqual(u['by_role']['builder']['total_tokens'], 217)
            self.assertEqual(u['by_role']['builder']['requests'], 2)
            self.assertEqual(
                u['by_role']['builder']['model_prefix'],
                'claude-sonnet-5-5',
            )
            self.assertEqual(u['total_tokens'], 89 + 217)
            self.assertEqual(u['plan_pct_start'], 50)
            self.assertEqual(u['plan_pct_end'], 52)
            status = MB.format_status(root)
            self.assertIn('designer=claude-opus-5-5-low', status)
            self.assertIn('plan=50%→52% (Δ+2)', status)

    def test_hybrid_usage_auto_split_errors_on_zero_requests(self):
        fixture = (
            Path(__file__).resolve().parent
            / 'testdata'
            / 'bakeoff_transcript_hybrid.jsonl'
        )
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed(root)
            _add_claude_effort_models(root)
            # Builder catalogue id whose derived prefix is absent from the fixture.
            MB.add_model(
                root,
                tool='claude',
                model_id='claude-haiku-5-5-high',
                tier='low',
                label='Claude Haiku 5.5 (high effort)',
                bulk_ok=True,
            )
            round_ = MB.open_round(
                root,
                tool='claude',
                model='claude-haiku-5-5-high',
                designer='claude-opus-5-5-low',
                task='feature_design',
                plan_pct=50,
            )
            with self.assertRaises(ValueError) as ctx:
                MB.record_usage(
                    root,
                    round_['id'],
                    from_transcript=fixture,
                )
            msg = str(ctx.exception).lower()
            self.assertIn('0', msg)
            self.assertIn('builder', msg)

    def test_live_config_has_hybrid_sitting_steps(self):
        cfg = MB.load_config(MB.DEFAULT_BAKEOFFS)
        steps = cfg.get('sitting_steps_hybrid')
        self.assertIsInstance(steps, list)
        self.assertGreaterEqual(len(steps), 1)
        joined = '\n'.join(steps)
        self.assertIn('{designer}', joined)
        self.assertIn('{model}', joined)
        self.assertIn('hand-off', joined)
        self.assertIn('model_bakeoff.py --world bakeoff baseline', joined)
        self.assertIn('model_bakeoff.py --world bakeoff capture', joined)

    def test_protocol_documents_hybrid_open_flags(self):
        proto = (MB.DEFAULT_BAKEOFFS / 'PROTOCOL.md').read_text(encoding='utf-8')
        self.assertIn('--designer', proto)
        self.assertIn('by_role', proto)
        self.assertIn('sitting_steps_hybrid', proto)





class SealedAnswersLocationTests(unittest.TestCase):
    def test_default_is_private_and_does_not_read_answers(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(Path, 'read_text', side_effect=AssertionError('sealed read')):
            self.assertEqual(MB.sealed_answers_path(), Path.home() / '.local/share/ai-worldbuilder/bakeoff-sealed/truth.json')

    def test_override_cannot_put_answers_in_checkout(self):
        with mock.patch.dict(os.environ, {'AI_WORLDBUILDER_SEALED_TRUTH': str(MB.ROOT / 'worlds/key.json')}):
            with self.assertRaisesRegex(ValueError, 'outside'):
                MB.sealed_answers_path()

    def test_relative_override_is_rejected(self):
        with mock.patch.dict(os.environ, {'AI_WORLDBUILDER_SEALED_TRUTH': 'private/truth.json'}):
            with self.assertRaisesRegex(ValueError, 'absolute'):
                MB.sealed_answers_path()

    def test_cli_only_prints_location_without_loading_world(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(Path, 'read_text', side_effect=AssertionError('sealed read')), mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(MB.main(['--world', 'not-a-real-world', 'sealed-path']), 0)
            self.assertEqual(output.getvalue().strip(), str(MB.sealed_answers_path()))

    def test_operator_override(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {'AI_WORLDBUILDER_SEALED_TRUTH': td + '/truth.json'}):
            self.assertEqual(MB.sealed_answers_path(), Path(td).resolve() / 'truth.json')


if __name__ == '__main__':
    unittest.main()
