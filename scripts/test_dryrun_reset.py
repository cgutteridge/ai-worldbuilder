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
        (self.site / 'CURRENT-CELL.json').write_text(json.dumps({'tx': 48, 'tz': -44}))
        self.region = self.world / 'region'
        self.region.mkdir()
        (self.region / 'r.0.0.mca').write_bytes(b'fake')

    def _patch_world(self):
        return mock.patch.multiple(
            dr,
            ROOT=self.world,
            SITE=self.site,
            CURRENT_CELL=self.site / 'CURRENT-CELL.json',
            LOCK=self.site / 'LOCK.json',
            HOLDS=self.site / 'CELL-HOLDS.jsonl',
            VERIFICATION=self.world / 'verification' / 'site',
            load_region_dir=lambda: self.region,
        )

    def test_dry_run_includes_regions_and_preserves_focus(self):
        with self._patch_world():
            rep = dr.run_reset(dry_run=True, archive_site=False, force=False)
        self.assertTrue(rep['dry_run'])
        self.assertIn('r.0.0.mca', rep['regions_to_delete'])
        self.assertEqual(rep['current_cell_kept'], {'tx': 48, 'tz': -44})
        self.assertIn('site/QUEUE.txt', rep['wipe']['replaced'])

    def test_refuses_when_lock_held(self):
        hb = time.time()
        (self.site / 'LOCK.json').write_text(json.dumps({
            'owner': 'agent', 'task': 'Q-1', 'claimed': hb, 'heartbeat': hb,
        }))
        with self._patch_world(), mock.patch.object(dr, 'mcp_world_reachable', return_value=False):
            with self.assertRaises(SystemExit):
                dr.run_reset(dry_run=False, archive_site=False, force=False)

    def test_force_allows_lock_with_warning(self):
        hb = time.time()
        (self.site / 'LOCK.json').write_text(json.dumps({
            'owner': 'agent', 'task': 'Q-1', 'claimed': hb, 'heartbeat': hb,
        }))
        template = _ROOT / 'templates' / 'new-world-site'
        with self._patch_world(), mock.patch.object(dr, 'TEMPLATE', template), \
             mock.patch.object(dr, 'mcp_world_reachable', return_value=False):
            rep = dr.run_reset(dry_run=False, archive_site=False, force=True)
        self.assertIn('warning', rep['warnings'][0])
        self.assertFalse((self.site / 'LOCK.json').exists())


if __name__ == '__main__':
    unittest.main()
