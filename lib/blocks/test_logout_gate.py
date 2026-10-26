#!/usr/bin/env python3
"""Tests for the logout gate (ticket #32)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import logout_gate as LG


class LogoutGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.save = self.root / 'save'
        self.save.mkdir()
        self.addCleanup(self.tmp.cleanup)

    def test_refuses_without_confirm_flag(self):
        with mock.patch.object(LG, 'play_need_you') as cue, \
                self.assertRaises(LG.LogoutGateError) as ctx:
            LG.require_logged_out(confirm_logged_out=False, save_path=self.save)
        self.assertIn('--confirm-logged-out', str(ctx.exception))
        cue.assert_called_once()

    def test_refuses_when_session_lock_present_even_with_flag(self):
        (self.save / 'session.lock').write_bytes(b'x')
        with mock.patch.object(LG, 'mcp_world_reachable', return_value=False), \
                self.assertRaises(LG.LogoutGateError) as ctx:
            LG.require_logged_out(confirm_logged_out=True, save_path=self.save)
        self.assertIn('session.lock', str(ctx.exception))

    def test_refuses_when_mcp_up_even_with_flag(self):
        with mock.patch.object(LG, 'mcp_world_reachable', return_value=True), \
                self.assertRaises(LG.LogoutGateError) as ctx:
            LG.require_logged_out(confirm_logged_out=True, save_path=self.save)
        self.assertIn('MCP', str(ctx.exception))

    def test_ok_when_confirmed_and_idle(self):
        with mock.patch.object(LG, 'mcp_world_reachable', return_value=False), \
                mock.patch.object(LG, 'play_need_you') as cue:
            LG.require_logged_out(confirm_logged_out=True, save_path=self.save)
        cue.assert_not_called()


if __name__ == '__main__':
    unittest.main()
