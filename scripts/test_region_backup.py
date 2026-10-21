"""Tests for sparse / whole-world region snap/restore (ticket #32)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import region_backup as RB


def argparse_ns(**kwargs):
    """Tiny namespace stand-in for CLI args.

    @param kwargs: attribute values
    @returns: object with those attributes
    """
    return type('NS', (), kwargs)()


class TestRegionBackup(unittest.TestCase):
    def test_regions_for_bbox(self):
        cells = RB.regions_for_bbox(6016, -5760, 6399, -5377)
        self.assertIn((11, -12), cells)
        self.assertIn((12, -11), cells)

    def test_restore_refuses_partial_snap(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            region = root / 'region'
            region.mkdir()
            backup_root = root / 'backups' / 'regions'
            snap = backup_root / '2026-01-01-120000-ghost'
            snap.mkdir(parents=True)
            (snap / 'r.0.0.mca').write_bytes(b'x')
            (snap / 'BACKUP.json').write_text(
                json.dumps({
                    'kind': RB.KIND_PARTIAL,
                    'files': ['r.0.0.mca'],
                    'region_path': str(region),
                }) + '\n'
            )
            with mock.patch.object(RB, 'BACKUP_ROOT', backup_root), mock.patch.object(
                RB, 'REGION', region
            ), mock.patch.object(RB, 'ROOT', root), mock.patch(
                'logout_gate.require_logged_out'
            ):
                code = RB.cmd_restore(argparse_ns(
                    snap='2026-01-01-120000-ghost', confirm_logged_out=True, force=False,
                    region=None,
                ))
            self.assertEqual(code, 2)
            self.assertFalse((region / 'r.0.0.mca').exists())

    def test_restore_whole_world_copies_and_verifies(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            region = root / 'region'
            region.mkdir()
            backup_root = root / 'backups' / 'regions'
            snap = backup_root / '2026-01-01-120000-ghost'
            snap.mkdir(parents=True)
            for name in ('r.11.-12.mca', 'r.12.-11.mca'):
                (snap / name).write_bytes(b'snap-' + name.encode())
                (region / name).write_bytes(b'old-' + name.encode())
            (snap / 'registry').mkdir()
            (snap / 'registry' / 'cells.json').write_text('{"48,-44": {}}\n')
            (snap / 'registry' / 'features.json').write_text('{}\n')
            meta = {
                'name': 'ghost',
                'kind': RB.KIND_WHOLE,
                'files': ['r.11.-12.mca', 'r.12.-11.mca'],
                'region_path': str(region),
                'world_root': str(root),
            }
            (snap / 'BACKUP.json').write_text(json.dumps(meta) + '\n')

            with mock.patch.object(RB, 'BACKUP_ROOT', backup_root), mock.patch.object(
                RB, 'REGION', region
            ), mock.patch.object(RB, 'ROOT', root), mock.patch(
                'logout_gate.require_logged_out'
            ) as gate:
                code = RB.cmd_restore(argparse_ns(
                    snap='2026-01-01-120000-ghost', confirm_logged_out=True, force=False,
                    region=None,
                ))
            gate.assert_called_once()
            self.assertEqual(code, 0)
            self.assertEqual((region / 'r.11.-12.mca').read_bytes(), b'snap-r.11.-12.mca')
            self.assertEqual((region / 'r.12.-11.mca').read_bytes(), b'snap-r.12.-11.mca')
            self.assertIn('48,-44', (root / 'registry' / 'cells.json').read_text())

    def test_restore_refuses_when_logout_gate_raises(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            region = root / 'region'
            region.mkdir()
            backup_root = root / 'backups' / 'regions'
            snap = backup_root / '2026-01-01-120000-ghost'
            snap.mkdir(parents=True)
            (snap / 'r.0.0.mca').write_bytes(b'x')
            (snap / 'BACKUP.json').write_text(
                json.dumps({
                    'kind': RB.KIND_WHOLE,
                    'files': ['r.0.0.mca'],
                    'region_path': str(region),
                }) + '\n'
            )
            import logout_gate as LG
            with mock.patch.object(RB, 'BACKUP_ROOT', backup_root), mock.patch.object(
                RB, 'REGION', region
            ), mock.patch.object(RB, 'ROOT', root), mock.patch(
                'logout_gate.require_logged_out',
                side_effect=LG.LogoutGateError('MCP up'),
            ):
                code = RB.cmd_restore(argparse_ns(
                    snap='2026-01-01-120000-ghost', confirm_logged_out=True, force=False,
                    region=None,
                ))
            self.assertEqual(code, 2)
            self.assertFalse((region / 'r.0.0.mca').exists())

    def test_snap_whole_world_copies_registry(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            region = root / 'region'
            region.mkdir()
            (region / 'r.1.2.mca').write_bytes(b'abc')
            (root / 'registry').mkdir()
            (root / 'registry' / 'cells.json').write_text('{}\n')
            (root / 'registry' / 'features.json').write_text('{}\n')
            backup_root = root / 'backups' / 'regions'
            with mock.patch('logout_gate.require_logged_out'):
                dest = RB.snap_whole_world(
                    name='bake',
                    region_dir=region,
                    world_root=root,
                    backup_root=backup_root,
                    confirm_logged_out=True,
                )
            meta = json.loads((dest / 'BACKUP.json').read_text())
            self.assertEqual(meta['kind'], RB.KIND_WHOLE)
            self.assertEqual(meta['files'], ['r.1.2.mca'])
            self.assertTrue((dest / 'registry' / 'cells.json').is_file())

    def test_restore_region_override_beats_snapshot_path(self):
        """``--region`` restores into the bakeoff save, not the snap's recorded path."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            recorded = root / 'recorded'
            target = root / 'target'
            recorded.mkdir()
            target.mkdir()
            backup_root = root / 'backups' / 'regions'
            snap = backup_root / '2026-01-01-120000-ghost'
            snap.mkdir(parents=True)
            (snap / 'r.0.0.mca').write_bytes(b'snap')
            (snap / 'BACKUP.json').write_text(
                json.dumps({
                    'kind': RB.KIND_WHOLE,
                    'files': ['r.0.0.mca'],
                    'region_path': str(recorded),
                    'world_root': str(root),
                }) + '\n'
            )
            with mock.patch.object(RB, 'BACKUP_ROOT', backup_root), mock.patch.object(
                RB, 'ROOT', root
            ), mock.patch('logout_gate.require_logged_out'):
                code = RB.cmd_restore(argparse_ns(
                    snap='2026-01-01-120000-ghost',
                    confirm_logged_out=True,
                    force=False,
                    region=str(target),
                ))
            self.assertEqual(code, 0)
            self.assertEqual((target / 'r.0.0.mca').read_bytes(), b'snap')
            self.assertFalse((recorded / 'r.0.0.mca').exists())

    def test_restore_archives_stale_site_build_records(self):
        """Whole-world restore moves verification/site build records so built_cells() is empty."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            region = root / 'region'
            region.mkdir()
            site = root / 'verification' / 'site'
            site.mkdir(parents=True)
            (site / 'bakeoff_r003_susu-cells.json').write_text('{}\n')
            (site / 'bakeoff_r003_susu-planned.json').write_text('{}\n')
            (site / 'bakeoff_r003_susu-preview.png').write_bytes(b'png')
            (site / 'keep-me-notes.txt').write_text('stay\n')
            backup_root = root / 'backups' / 'regions'
            snap = backup_root / '2026-01-01-120000-ghost'
            snap.mkdir(parents=True)
            (snap / 'r.0.0.mca').write_bytes(b'x')
            (snap / 'BACKUP.json').write_text(
                json.dumps({
                    'kind': RB.KIND_WHOLE,
                    'files': ['r.0.0.mca'],
                    'region_path': str(region),
                    'world_root': str(root),
                }) + '\n'
            )
            with mock.patch.object(RB, 'BACKUP_ROOT', backup_root), mock.patch.object(
                RB, 'ROOT', root
            ), mock.patch('logout_gate.require_logged_out'), mock.patch(
                'region_backup.time.strftime', return_value='2026-10-21'
            ):
                restored = RB.restore_snap(
                    snap,
                    confirm_logged_out=True,
                    skip_logout_gate=True,
                    world_root=root,
                )
            self.assertEqual(restored, ['r.0.0.mca'])
            archive = root / 'verification' / 'archive' / 'stale-after-ghost-restore-2026-10-21'
            self.assertTrue((archive / 'bakeoff_r003_susu-cells.json').is_file())
            self.assertTrue((archive / 'bakeoff_r003_susu-planned.json').is_file())
            self.assertFalse((site / 'bakeoff_r003_susu-cells.json').exists())
            self.assertTrue((site / 'bakeoff_r003_susu-preview.png').is_file())
            self.assertTrue((site / 'keep-me-notes.txt').is_file())


if __name__ == '__main__':
    unittest.main()
