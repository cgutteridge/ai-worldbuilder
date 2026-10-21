#!/usr/bin/env python3
"""Tests for dryrun_reset (no Minecraft)."""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / 'shims', _ROOT / 'scripts'):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

import dryrun_reset as dr


class DryRunOutput(unittest.TestCase):
    """--dry-run lists preserved vs wiped paths."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.world = Path(self.tmp.name)
        self.site = self.world / 'site'
        self.site.mkdir(parents=True)
        (self.site / 'QUEUE.txt').write_text('legacy\n')
        (self.site / 'CURRENT-CELL.json').write_text(json.dumps({'tx': 48, 'tz': -44}))
        (self.site / '.handoff-pending.json').write_text('{}')
        cells = self.site / 'cells'
        cells.mkdir()
        (cells / '48_-44-STAGES.json').write_text('{"stages":{}}')
        reg = self.world / 'registry'
        reg.mkdir()
        (reg / 'cells.json').write_text(json.dumps({'48,-44': {'ground': 'generated'}}))
        (reg / 'features.json').write_text('{}')
        self.region = self.world / 'region'
        self.region.mkdir()
        (self.region / 'r.0.0.mca').write_bytes(b'fake')

    def _patch_world(self):
        return mock.patch.multiple(
            dr,
            ROOT=self.world,
            SITE=self.site,
            REGISTRY=self.world / 'registry',
            LOCK=self.site / 'LOCK.json',
            HOLDS=self.site / 'CELL-HOLDS.jsonl',
            VERIFICATION=self.world / 'verification' / 'site',
            load_region_dir=lambda: self.region,
        )

    def test_dry_run_lists_legacy_wipes_and_empty_registry(self):
        with self._patch_world():
            rep = dr.run_reset(dry_run=True, archive_site=False, force=False)
        self.assertTrue(rep['dry_run'])
        self.assertIn('r.0.0.mca', rep['regions_to_delete'])
        removed = rep['wipe']['removed']
        self.assertIn('site/QUEUE.txt', removed)
        self.assertIn('site/CURRENT-CELL.json', removed)
        self.assertIn('site/.handoff-pending.json', removed)
        self.assertTrue(any('registry' in p for p in rep['wipe']['cleared_dirs'] + removed))

    def test_refuses_without_confirm_logged_out(self):
        with self._patch_world(), mock.patch.object(dr, 'mcp_world_reachable', return_value=False), \
                mock.patch('logout_gate.play_need_you'):
            with self.assertRaises(SystemExit) as ctx:
                dr.run_reset(dry_run=False, archive_site=False, force=False)
        self.assertIn('confirm-logged-out', str(ctx.exception))

    def test_live_reset_wipes_legacy_and_empties_registry(self):
        hb = time.time()
        (self.site / 'LOCK.json').write_text(json.dumps({
            'owner': 'agent', 'task': 'old', 'claimed': hb, 'heartbeat': hb,
        }))
        template = _ROOT / 'templates' / 'new-world-site'
        with self._patch_world(), mock.patch.object(dr, 'TEMPLATE', template), \
             mock.patch('logout_gate.require_logged_out'):
            rep = dr.run_reset(dry_run=False, archive_site=False, force=True)
        self.assertFalse((self.site / 'QUEUE.txt').exists())
        self.assertFalse((self.site / 'CURRENT-CELL.json').exists())
        self.assertFalse((self.site / '.handoff-pending.json').exists())
        self.assertFalse(list((self.site / 'cells').glob('*-STAGES.json')))
        cells = json.loads((self.world / 'registry' / 'cells.json').read_text())
        feats = json.loads((self.world / 'registry' / 'features.json').read_text())
        self.assertEqual(cells, {})
        self.assertEqual(feats, {})
        self.assertFalse((self.site / 'LOCK.json').exists())
        self.assertIn('warning', (rep.get('warnings') or ['warning'])[0])


if __name__ == '__main__':
    unittest.main()
