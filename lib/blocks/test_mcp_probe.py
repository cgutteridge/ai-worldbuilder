"""Tests for mcp_probe (no network: urlopen is mocked)."""
from __future__ import annotations

import unittest
import urllib.error
from unittest import mock

import mcp_probe


class TestMcpProbe(unittest.TestCase):
    def test_answering_bridge_is_reachable(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        with mock.patch('urllib.request.urlopen', return_value=resp):
            self.assertTrue(mcp_probe.mcp_world_reachable())

    def test_http_error_still_means_listening(self):
        err = urllib.error.HTTPError('u', 400, 'bad', {}, None)
        with mock.patch('urllib.request.urlopen', side_effect=err):
            self.assertTrue(mcp_probe.mcp_world_reachable())

    def test_connection_refused_is_unreachable(self):
        with mock.patch('urllib.request.urlopen', side_effect=ConnectionRefusedError()):
            self.assertFalse(mcp_probe.mcp_world_reachable())


if __name__ == '__main__':
    unittest.main()
