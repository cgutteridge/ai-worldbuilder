"""Tests for sparse region snap/restore."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import region_backup as RB


class TestRegionBackup(unittest.TestCase):
    def test_regions_for_bbox(self):
        cells = RB.regions_for_bbox(6016, -5760, 6399, -5377)
        self.assertIn((11, -12), cells)
        self.assertIn((12, -11), cells)

    def test_restore_copies_and_verifies(self):
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
            meta = {
                'name': 'ghost',
                'files': ['r.11.-12.mca', 'r.12.-11.mca'],
                'region_path': str(region),
            }
            (snap / 'BACKUP.json').write_text(json.dumps(meta) + '\n')

            with mock.patch.object(RB, 'BACKUP_ROOT', backup_root), mock.patch.object(
                RB, 'REGION', region
            ), mock.patch.object(RB, 'mcp_world_reachable', return_value=False):
                code = RB.cmd_restore(
                    argparse_ns(snap='2026-01-01-120000-ghost', force=False)
                )
            self.assertEqual(code, 0)
            self.assertEqual((region / 'r.11.-12.mca').read_bytes(), b'snap-r.11.-12.mca')
            self.assertEqual((region / 'r.12.-11.mca').read_bytes(), b'snap-r.12.-11.mca')

    def test_restore_refuses_when_mcp_up_without_force(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            region = root / 'region'
            region.mkdir()
            backup_root = root / 'backups' / 'regions'
            snap = backup_root / '2026-01-01-120000-ghost'
            snap.mkdir(parents=True)
            (snap / 'r.0.0.mca').write_bytes(b'x')
            (snap / 'BACKUP.json').write_text(
                json.dumps({'files': ['r.0.0.mca'], 'region_path': str(region)}) + '\n'
            )
            with mock.patch.object(RB, 'BACKUP_ROOT', backup_root), mock.patch.object(
                RB, 'REGION', region
            ), mock.patch.object(RB, 'mcp_world_reachable', return_value=True):
                code = RB.cmd_restore(
                    argparse_ns(snap='2026-01-01-120000-ghost', force=False)
                )
            self.assertEqual(code, 2)
            self.assertFalse((region / 'r.0.0.mca').exists())


def argparse_ns(**kwargs):
    """Tiny namespace stand-in for CLI args.

    @param kwargs: attribute values
    @returns: object with those attributes
    """
    return type('NS', (), kwargs)()


if __name__ == '__main__':
    unittest.main()
