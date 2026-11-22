"""Client 429 retry delay after the #389 rate-limit finding."""
from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest import mock

import mcp


class TestRateLimitRetryDelay(unittest.TestCase):
    def test_caps_sixty_second_retry_after(self):
        self.assertEqual(mcp.rate_limit_retry_delay_s('60'), mcp.RATE_LIMIT_RETRY_CAP_S)

    def test_honours_shorter_retry_after(self):
        self.assertEqual(mcp.rate_limit_retry_delay_s('1'), 1.0)

    def test_missing_or_junk_uses_cap(self):
        self.assertEqual(mcp.rate_limit_retry_delay_s(None), mcp.RATE_LIMIT_RETRY_CAP_S)
        self.assertEqual(mcp.rate_limit_retry_delay_s('soon'), mcp.RATE_LIMIT_RETRY_CAP_S)


class TestClientRetries429(unittest.TestCase):
    def test_request_sleeps_capped_delay_then_succeeds(self):
        client = mcp.Client(8765, resume_stateless=True)
        err = urllib.error.HTTPError(
            client.url, 429, 'Too Many Requests', {'Retry-After': '60'}, None,
        )
        ok = mock.MagicMock()
        ok.headers = {}
        ok.read.return_value = json.dumps(
            {'jsonrpc': '2.0', 'id': 1, 'result': {'ok': True}},
        ).encode()
        ok.__enter__.return_value = ok
        ok.__exit__.return_value = False
        sleeps: list[float] = []

        def open_side_effect(*_a, **_k):
            if not hasattr(open_side_effect, 'n'):
                open_side_effect.n = 0
            open_side_effect.n += 1
            if open_side_effect.n == 1:
                raise err
            return ok

        with mock.patch.object(client.opener, 'open', side_effect=open_side_effect), \
                mock.patch('mcp.time.sleep', side_effect=sleeps.append), \
                mock.patch('sys.stderr', new_callable=io.StringIO) as err_out:
            result = client.request('ping')
        self.assertEqual(result, {'ok': True})
        self.assertEqual(sleeps, [mcp.RATE_LIMIT_RETRY_CAP_S])
        self.assertIn('Minecraft rate limit: retrying in 2.0s', err_out.getvalue())


if __name__ == '__main__':
    unittest.main()
