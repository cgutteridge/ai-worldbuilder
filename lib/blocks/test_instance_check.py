#!/usr/bin/env python3
"""Tests for the read-only instance check."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import instance_check as IC


class InstanceCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.inst = Path(self.tmp.name)
        self.save = self.inst / 'saves' / 'W'
        (self.save / 'dimensions' / 'minecraft' / 'overworld' / 'region').mkdir(parents=True)
        (self.save / 'level.dat').write_bytes(b'x')

    def _ok(self, rows):
        return {name: ok for name, ok, _ in rows}

    def test_complete_instance_passes(self):
        (self.inst / 'mods').mkdir()
        for n in ('fabric-api-1.jar', 'minecraft-fabric-mcp-1.jar'):
            (self.inst / 'mods' / n).write_bytes(b'x')
        cfg = self.inst / 'config' / 'minecraft_fabric_mcp'
        cfg.mkdir(parents=True)
        (cfg / 'config.json').write_text(json.dumps({'port': 8765}))
        rows = IC.check_instance(self.save)
        self.assertTrue(all(self._ok(rows).values()), rows)

    def test_missing_pieces_reported(self):
        ok = self._ok(IC.check_instance(self.save))
        self.assertFalse(ok['mod fabric-api'])
        self.assertFalse(ok['MCP config port'])
        self.assertTrue(ok['save folder'])

    def test_mcp_down_only_fails_when_game_is_open(self):
        closed = self._ok(IC.check_instance(self.save, mcp_reachable=False, save_open=False))
        self.assertTrue(closed['MCP server answers'])
        opened = self._ok(IC.check_instance(self.save, mcp_reachable=False, save_open=True))
        self.assertFalse(opened['MCP server answers'])

    def test_writes_nothing(self):
        before = sorted(p for p in self.inst.rglob('*'))
        IC.check_instance(self.save)
        self.assertEqual(before, sorted(p for p in self.inst.rglob('*')))


if __name__ == '__main__':
    unittest.main()
