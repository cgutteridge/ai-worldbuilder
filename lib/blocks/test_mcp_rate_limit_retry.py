"""Client 429 retry delay after the #389 rate-limit finding."""
from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest import mock

import mcp


class TestRateLimitRetryDelay(unittest.TestCase):
    def test_first_attempt_starts_at_base(self):
        self.assertEqual(
            mcp.rate_limit_retry_delay_s('60', attempt=0),
            mcp.RATE_LIMIT_RETRY_BASE_S,
        )

    def test_grows_then_spends_remaining_budget(self):
        first = mcp.rate_limit_retry_delay_s('60', attempt=0)
        second = mcp.rate_limit_retry_delay_s('60', attempt=1, waited_s=first)
        third = mcp.rate_limit_retry_delay_s(
            '60', attempt=2, waited_s=first + second,
        )
        self.assertEqual(first, 0.25)
        self.assertEqual(second, 4.0)
        self.assertAlmostEqual(third, 55.75)
        self.assertAlmostEqual(first + second + third, mcp.RATE_LIMIT_RETRY_BUDGET_S)

    def test_honours_shorter_retry_after(self):
        self.assertEqual(mcp.rate_limit_retry_delay_s('1', attempt=1), 1.0)

    def test_missing_or_junk_uses_backoff_not_full_budget(self):
        self.assertEqual(
            mcp.rate_limit_retry_delay_s(None, attempt=0),
            mcp.RATE_LIMIT_RETRY_BASE_S,
        )
        self.assertEqual(
            mcp.rate_limit_retry_delay_s('soon', attempt=0),
            mcp.RATE_LIMIT_RETRY_BASE_S,
        )


class TestClientRetries429(unittest.TestCase):
    def test_request_sleeps_backoff_then_succeeds(self):
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
        self.assertEqual(sleeps, [mcp.RATE_LIMIT_RETRY_BASE_S])
        self.assertIn('Minecraft rate limit: retrying in 0.25s', err_out.getvalue())

    def test_exhausted_retries_raises_after_budgeted_backoff(self):
        client = mcp.Client(8765, resume_stateless=True)
        err = urllib.error.HTTPError(
            client.url, 429, 'Too Many Requests', {'Retry-After': '60'}, None,
        )
        sleeps: list[float] = []

        with mock.patch.object(client.opener, 'open', side_effect=err), \
                mock.patch('mcp.time.sleep', side_effect=sleeps.append), \
                mock.patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                client.request('ping')
        self.assertEqual(raised.exception.code, 429)
        self.assertEqual(len(sleeps), 3)
        self.assertEqual(sleeps[0], 0.25)
        self.assertEqual(sleeps[1], 4.0)
        self.assertAlmostEqual(sleeps[2], 55.75)
        self.assertAlmostEqual(sum(sleeps), mcp.RATE_LIMIT_RETRY_BUDGET_S)


if __name__ == '__main__':
    unittest.main()
